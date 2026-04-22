"""Chainlit self-service portal for data producers.

Flow:
  1. Producer types a PostgreSQL table name in the chat
  2. AI Agent introspects the schema via PostgreSQL information_schema
  3. Ollama generates an ODCS v3.1 contract
  4. Contract is saved to /contracts/{table}.yaml
  5. Debezium connector is activated via KafkaConnect REST API
"""
import os
from pathlib import Path

import boto3
import chainlit as cl
import yaml

_s3 = boto3.client(
    "s3",
    endpoint_url=os.getenv("MINIO_ENDPOINT", "http://minio.infra.svc.cluster.local:9000"),
    aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minio"),
    aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minio123"),
)
_CONTRACTS_BUCKET = os.getenv("CONTRACTS_BUCKET", "warehouse")
_CONTRACTS_PREFIX = os.getenv("CONTRACTS_PREFIX", "contracts")

from agent.connector_activator import ConnectorActivator, PostgresConfig
from agent.odcs_generator import ODCSGenerator
from agent.schema_inspector import PostgresSchemaInspector

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

_generator = ODCSGenerator(
    ollama_url=os.getenv("OLLAMA_URL", "http://ollama.portal.svc.cluster.local:11434"),
    model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
)

_activator = ConnectorActivator(
    kafka_connect_url=os.environ["KAFKA_CONNECT_URL"],
    postgres=_pg_config,
)


@cl.on_chat_start
async def on_start() -> None:
    await cl.Message(
        content=(
            "Bem-vindo ao portal de ingestão de dados.\n\n"
            "Digite o nome de uma tabela PostgreSQL para iniciar o processo de CDC.\n"
            "Exemplo: `orders`"
        )
    ).send()


@cl.on_message
async def handle_message(message: cl.Message) -> None:
    table_name = message.content.strip().lower()

    if not table_name.replace("_", "").isalnum():
        await cl.Message(content="Nome de tabela inválido. Use apenas letras, números e underscore.").send()
        return

    await cl.Message(content=f"Verificando existência da tabela `{table_name}`...").send()

    if not _inspector.table_exists(table_name):
        await cl.Message(content=f"Tabela `{table_name}` não encontrada no schema `public`.").send()
        return

    await cl.Message(content=f"Inspecionando schema de `{table_name}`...").send()
    columns = _inspector.introspect(table_name)

    col_summary = "\n".join(
        f"  - `{c.name}` ({c.data_type}){' [PK]' if c.is_primary_key else ''}{' NOT NULL' if not c.is_nullable else ''}"
        for c in columns
    )
    await cl.Message(content=f"Schema encontrado ({len(columns)} colunas):\n{col_summary}").send()

    await cl.Message(content="Gerando contrato ODCS v3.1 via Ollama...").send()
    contract = _generator.generate(table_name, columns)

    contract_path = _CONTRACTS_DIR / f"{table_name}.yaml"
    contract_yaml = yaml.dump(contract, default_flow_style=False, allow_unicode=True)
    contract_path.write_text(contract_yaml)

    s3_key = f"{_CONTRACTS_PREFIX}/{table_name}.yaml"
    _s3.put_object(Bucket=_CONTRACTS_BUCKET, Key=s3_key, Body=contract_yaml.encode())

    await cl.Message(
        content=f"Contrato gerado e salvo em `{contract_path}` e `s3://{_CONTRACTS_BUCKET}/{s3_key}`.\n\n```yaml\n{yaml.dump(contract)}\n```"
    ).send()

    await cl.Message(content="Ativando connector Debezium via KafkaConnect...").send()
    result = _activator.activate(table_name)

    status = result.get("status", "created")
    connector_name = result.get("name", f"debezium-public-{table_name}")

    await cl.Message(
        content=(
            f"CDC ativado com sucesso!\n"
            f"- Connector: `{connector_name}`\n"
            f"- Status: `{status}`\n"
            f"- Tópico Kafka: `cdc.public.{table_name}`\n\n"
            f"Os dados serão ingeridos para `bronze.valid_{table_name}` em alguns segundos."
        )
    ).send()
