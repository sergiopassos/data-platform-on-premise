"""Chainlit self-service portal for data producers.

Flow:
  1. Producer types a PostgreSQL table name in the chat
  2. AI Agent introspects the schema via PostgreSQL information_schema
  3. Active session provider (Gemini / Ollama / Fallback) generates an
     ODCS v3.1 contract
  4. Contract is saved to /contracts/{table}.yaml and to MinIO
  5. Debezium connector is activated via KafkaConnect REST API

Provider selection:
  - Gear-icon ChatSettings dropdown (persists to cl.user_session)
  - ``/llm <name>`` slash command (equivalent code path)
  - ``DEFAULT_LLM_PROVIDER`` env var pre-populates the session on start
"""
import asyncio
import os
from pathlib import Path

import boto3
import chainlit as cl
import yaml
from agent.commands import is_known_provider, parse_llm_command
from agent.connector_activator import ConnectorActivator, PostgresConfig
from agent.odcs_generator import ODCSGenerator
from agent.providers import KNOWN_PROVIDERS, ProviderError
from agent.schema_inspector import PostgresSchemaInspector
from agent.session import clear_provider, get_provider, set_provider_by_name
from chainlit.input_widget import Select

_s3 = boto3.client(
    "s3",
    endpoint_url=os.getenv("MINIO_ENDPOINT", "http://minio.infra.svc.cluster.local:9000"),
    aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minio"),
    aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minio123"),
)
_CONTRACTS_BUCKET = os.getenv("CONTRACTS_BUCKET", "contracts")
_CONTRACTS_PREFIX = os.getenv("CONTRACTS_PREFIX", "")

_CONTRACTS_DIR = Path(os.getenv("CONTRACTS_DIR", "/contracts"))
_CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

_pg_config = PostgresConfig(
    host=os.environ["POSTGRES_HOST"],
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
)

_inspector = PostgresSchemaInspector(
    host=_pg_config.host,
    port=_pg_config.port,
    dbname=_pg_config.dbname,
    user=_pg_config.user,
    password=_pg_config.password,
)

# Stateless: no provider at construction; the active provider is injected
# per call from the current Chainlit session.
_generator = ODCSGenerator()

_activator = ConnectorActivator(
    kafka_connect_url=os.environ["KAFKA_CONNECT_URL"],
    postgres=_pg_config,
)

_DEFAULT_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "gemini")


def _initial_index() -> int:
    """Return the index of ``_DEFAULT_PROVIDER`` in ``KNOWN_PROVIDERS`` (or 0)."""
    if _DEFAULT_PROVIDER in KNOWN_PROVIDERS:
        return KNOWN_PROVIDERS.index(_DEFAULT_PROVIDER)
    return 0


@cl.on_chat_start
async def on_start() -> None:
    await cl.ChatSettings(
        [
            Select(
                id="llm_provider",
                label="LLM Provider",
                values=list(KNOWN_PROVIDERS),
                initial_index=_initial_index(),
            ),
        ]
    ).send()

    # Pre-populate the session with the default provider so users do not
    # have to select one on first use (COULD goal from DEFINE).
    active_name = _DEFAULT_PROVIDER
    try:
        set_provider_by_name(_DEFAULT_PROVIDER)
    except (ValueError, ProviderError) as err:
        clear_provider()
        active_name = "(none)"
        await cl.Message(
            content=(
                f"Aviso: nao foi possivel carregar o provider padrao "
                f"`{_DEFAULT_PROVIDER}`: {err}. Use `/llm <nome>` ou o icone de "
                f"engrenagem para escolher um provider."
            )
        ).send()

    await cl.Message(
        content=(
            "Bem-vindo ao portal de ingestao de dados.\n\n"
            f"Provider ativo: `{active_name}`. Use `/llm <nome>` ou o icone de "
            f"engrenagem para trocar. Opcoes: {', '.join(KNOWN_PROVIDERS)}.\n\n"
            "Digite o nome de uma tabela PostgreSQL para iniciar o CDC.\n"
            "Exemplo: `orders`"
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict) -> None:
    name = settings.get("llm_provider")
    if not name:
        return
    try:
        set_provider_by_name(name)
        await cl.Message(content=f"Provider ativo: `{name}`.").send()
    except (ValueError, ProviderError) as err:
        clear_provider()
        await cl.Message(
            content=f"Falha ao carregar `{name}`: {err}. Escolha outro provider."
        ).send()


@cl.on_message
async def handle_message(message: cl.Message) -> None:
    # 1. /llm slash command short-circuit
    cmd = parse_llm_command(message.content)
    if cmd is not None:
        if not is_known_provider(cmd.provider_name):
            await cl.Message(
                content=(
                    f"Provider desconhecido: `{cmd.provider_name}`. "
                    f"Opcoes: {', '.join(KNOWN_PROVIDERS)}."
                )
            ).send()
            return
        try:
            set_provider_by_name(cmd.provider_name)
            await cl.Message(
                content=f"Provider ativo: `{cmd.provider_name}`."
            ).send()
        except (ValueError, ProviderError) as err:
            clear_provider()
            await cl.Message(
                content=(
                    f"Falha ao carregar `{cmd.provider_name}`: {err}. "
                    "Escolha outro provider."
                )
            ).send()
        return

    # 2. No provider? Block and prompt the user to choose.
    provider = get_provider()
    if provider is None:
        await cl.Message(
            content=(
                "Nenhum provider selecionado. Use `/llm <nome>` ou o icone de "
                f"engrenagem para escolher. Opcoes: {', '.join(KNOWN_PROVIDERS)}."
            )
        ).send()
        return

    # 3. Happy path: validate table, introspect, generate, activate.
    table_name = message.content.strip().lower()

    if not table_name.replace("_", "").isalnum():
        await cl.Message(
            content="Nome de tabela invalido. Use apenas letras, numeros e underscore."
        ).send()
        return

    await cl.Message(
        content=f"Verificando existencia da tabela `{table_name}`..."
    ).send()

    if not _inspector.table_exists(table_name):
        await cl.Message(
            content=f"Tabela `{table_name}` nao encontrada no schema `public`."
        ).send()
        return

    await cl.Message(content=f"Inspecionando schema de `{table_name}`...").send()
    columns = _inspector.introspect(table_name)

    col_summary = "\n".join(
        f"  - `{c.name}` ({c.data_type})"
        f"{' [PK]' if c.is_primary_key else ''}"
        f"{' NOT NULL' if not c.is_nullable else ''}"
        for c in columns
    )
    await cl.Message(
        content=f"Schema encontrado ({len(columns)} colunas):\n{col_summary}"
    ).send()

    await cl.Message(
        content=f"Gerando contrato ODCS v3.1 via `{provider.name}`..."
    ).send()

    try:
        contract = await _generator.generate(
            table_name, columns, provider=provider
        )
    except ProviderError as err:
        clear_provider()
        await cl.Message(
            content=(
                f"Falha em `{err.provider_name}` ({type(err).__name__}): {err}. "
                "Selecione outro provider para continuar."
            )
        ).send()
        return

    contract_path = _CONTRACTS_DIR / f"{table_name}.yaml"
    contract_yaml = yaml.dump(contract, default_flow_style=False, allow_unicode=True)
    contract_path.write_text(contract_yaml)

    s3_key = f"{_CONTRACTS_PREFIX}/{table_name}.yaml".lstrip("/")
    _s3.put_object(
        Bucket=_CONTRACTS_BUCKET, Key=s3_key, Body=contract_yaml.encode()
    )

    await cl.Message(
        content=(
            f"Contrato gerado e salvo em `{contract_path}` e "
            f"`s3://{_CONTRACTS_BUCKET}/{s3_key}`.\n\n"
            f"```yaml\n{yaml.dump(contract)}\n```"
        )
    ).send()

    await cl.Message(content="Ativando connector Debezium via KafkaConnect...").send()
    result = await asyncio.to_thread(_activator.activate, table_name)

    status = result.get("status", "created")
    connector_name = result.get("name", f"debezium-public-{table_name}")

    await cl.Message(
        content=(
            f"CDC ativado com sucesso!\n"
            f"- Connector: `{connector_name}`\n"
            f"- Status: `{status}`\n"
            f"- Topico Kafka: `cdc.public.{table_name}`\n\n"
            f"Os dados serao ingeridos para `bronze.valid_{table_name}` em alguns segundos."
        )
    ).send()
