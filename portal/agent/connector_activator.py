"""KafkaConnect REST API client for activating Debezium connectors."""
import time
from dataclasses import dataclass

import httpx


@dataclass
class PostgresConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str


class ConnectorActivator:
    def __init__(self, kafka_connect_url: str, postgres: PostgresConfig) -> None:
        self.base_url = kafka_connect_url.rstrip("/")
        self.postgres = postgres

    def activate(self, table_name: str, schema: str = "public") -> dict:
        connector_name = f"debezium-{schema}-{table_name}"

        if self._connector_exists(connector_name):
            return {"name": connector_name, "status": "already_active"}

        config = self._build_config(connector_name, table_name, schema)
        return self._create_connector(config)

    def _connector_exists(self, connector_name: str) -> bool:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{self.base_url}/connectors/{connector_name}")
            return resp.status_code == 200

    def _build_config(self, connector_name: str, table_name: str, schema: str) -> dict:
        return {
            "name": connector_name,
            "config": {
                "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
                "database.hostname": self.postgres.host,
                "database.port": str(self.postgres.port),
                "database.user": self.postgres.user,
                "database.password": self.postgres.password,
                "database.dbname": self.postgres.dbname,
                "database.server.name": "cdc",
                "table.include.list": f"{schema}.{table_name}",
                "topic.prefix": "cdc",
                "plugin.name": "pgoutput",
                "publication.autocreate.mode": "filtered",
                "slot.name": f"debezium_{table_name}",
                "heartbeat.interval.ms": "10000",
                "transforms": "route",
                "transforms.route.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
            },
        }

    def _create_connector(self, config: dict, max_retries: int = 3) -> dict:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(f"{self.base_url}/connectors", json=config)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise
                last_error = e
                time.sleep(2 ** attempt)
            except httpx.RequestError as e:
                last_error = e
                time.sleep(2 ** attempt)

        raise RuntimeError(f"Failed to create connector after {max_retries} attempts: {last_error}")

    def list_connectors(self) -> list[str]:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{self.base_url}/connectors")
            resp.raise_for_status()
            return resp.json()
