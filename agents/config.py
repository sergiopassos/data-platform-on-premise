from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    kubectl_context: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    postgres_host: str
    postgres_port: int
    postgres_dbname: str
    postgres_user: str
    postgres_password: str
    kafka_bootstrap: str
    kafka_connect_url: str
    nessie_url: str
    airflow_url: str
    airflow_user: str
    airflow_pass: str
    trino_host: str
    trino_port: int
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            kubectl_context=os.getenv("KUBECTL_CONTEXT", "kind-data-platform"),
            minio_endpoint=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"),
            minio_access_key=os.getenv("MINIO_ACCESS_KEY", "minio"),
            minio_secret_key=os.getenv("MINIO_SECRET_KEY", "minio123"),
            postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
            postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
            postgres_dbname=os.getenv("POSTGRES_DB", "sourcedb"),
            postgres_user=os.getenv("POSTGRES_USER", "postgres"),
            postgres_password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            kafka_bootstrap=os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"),
            kafka_connect_url=os.getenv("KAFKA_CONNECT_URL", "http://localhost:8083"),
            nessie_url=os.getenv("NESSIE_URL", "http://localhost:19120/api/v1"),
            airflow_url=os.getenv("AIRFLOW_URL", "http://localhost:8081"),
            airflow_user=os.getenv("AIRFLOW_USER", "admin"),
            airflow_pass=os.getenv("AIRFLOW_PASS", "admin"),
            trino_host=os.getenv("TRINO_HOST", "localhost"),
            trino_port=int(os.getenv("TRINO_PORT", "8082")),
            langfuse_host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
        )
