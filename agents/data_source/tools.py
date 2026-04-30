from __future__ import annotations

import subprocess

import boto3

from agents.config import Config

# ── PostgreSQL ────────────────────────────────────────────────────────────────

def _get_postgres_pod(cfg: Config) -> str:
    result = subprocess.run(
        ["kubectl", "get", "pod", "-n", "infra", "-l", "app=postgres",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("Could not find postgres pod in 'infra' namespace")
    return result.stdout.strip()


def psql_exec(sql: str, cfg: Config) -> str:
    pod = _get_postgres_pod(cfg)
    result = subprocess.run(
        ["kubectl", "exec", "-n", "infra", pod, "--",
         "psql", "-U", cfg.postgres_user, "-d", cfg.postgres_dbname, "-t", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def create_and_seed_table(table_name: str, cfg: Config, seed_rows: int = 5) -> None:
    psql_exec(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            value NUMERIC(10,2) NOT NULL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        cfg,
    )
    for i in range(seed_rows):
        psql_exec(
            f"INSERT INTO {table_name} (name, value) "
            f"VALUES ('item_{i}', {i * 1.5}) ON CONFLICT DO NOTHING",
            cfg,
        )


# ── Contract validation ───────────────────────────────────────────────────────

def _get_portal_pod() -> str:
    result = subprocess.run(
        ["kubectl", "get", "pod", "-n", "serving", "-l", "app=portal",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("Could not find portal pod in 'serving' namespace")
    return result.stdout.strip()


def validate_contract_cli(yaml_content: str) -> tuple[bool, str]:
    """Validate ODCS YAML by piping it to datacontract-cli inside the portal pod."""
    portal_pod = _get_portal_pod()
    result = subprocess.run(
        ["kubectl", "exec", "-i", "-n", "serving", portal_pod, "--",
         "datacontract", "lint", "--file", "/dev/stdin"],
        input=yaml_content,
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr.strip() or result.stdout.strip())


# ── MinIO upload ──────────────────────────────────────────────────────────────

def upload_contract_to_minio(table_name: str, yaml_content: str, cfg: Config) -> str:
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg.minio_endpoint,
        aws_access_key_id=cfg.minio_access_key,
        aws_secret_access_key=cfg.minio_secret_key,
    )
    key = f"{table_name}.yaml"
    s3.put_object(Bucket="contracts", Key=key, Body=yaml_content.encode())
    return f"s3://contracts/{key}"


# ── Kafka consumer ────────────────────────────────────────────────────────────

def consume_one_kafka_message(topic: str, cfg: Config, timeout_seconds: int = 60) -> bool:
    from kafka import KafkaConsumer
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=cfg.kafka_bootstrap,
        auto_offset_reset="earliest",
        consumer_timeout_ms=timeout_seconds * 1000,
        group_id=f"e2e-agent-{topic}",
    )
    try:
        for _ in consumer:
            return True
        return False
    finally:
        consumer.close()


# ── Provider selection ────────────────────────────────────────────────────────

def build_llm_provider():
    """Return GeminiProvider if GEMINI_API_KEY is set, else FallbackProvider."""
    import os

    from portal.agent.providers.base import ProviderError
    if os.getenv("GEMINI_API_KEY"):
        try:
            from portal.agent.providers.gemini import GeminiProvider
            return GeminiProvider()
        except ProviderError:
            pass
    from portal.agent.providers.fallback import FallbackProvider
    return FallbackProvider()
