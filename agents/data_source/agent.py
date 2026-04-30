from __future__ import annotations

import asyncio
import time

import yaml

from agents.config import Config
from agents.data_source.tools import (
    build_llm_provider,
    consume_one_kafka_message,
    create_and_seed_table,
    upload_contract_to_minio,
    validate_contract_cli,
)
from agents.observability import emit_score, observe
from agents.state import E2EState
from portal.agent.connector_activator import ConnectorActivator, PostgresConfig
from portal.agent.odcs_generator import ODCSGenerator
from portal.agent.schema_inspector import PostgresSchemaInspector

_ODCS_GEN = ODCSGenerator()


def data_source_node(state: E2EState) -> E2EState:
    start = time.monotonic()
    cfg = Config.from_env()
    table_name = state["table_name"]

    with observe(state["langfuse_trace_id"], name="data_source_setup"):
        try:
            create_and_seed_table(table_name, cfg)

            inspector = PostgresSchemaInspector(
                host=cfg.postgres_host,
                port=cfg.postgres_port,
                dbname=cfg.postgres_dbname,
                user=cfg.postgres_user,
                password=cfg.postgres_password,
            )
            columns = inspector.introspect(table_name)

            provider = build_llm_provider()
            contract_dict = asyncio.run(_ODCS_GEN.generate(table_name, columns, provider=provider))

            yaml_content = yaml.dump(contract_dict, default_flow_style=False)

            valid, err_msg = validate_contract_cli(yaml_content)
            if not valid:
                fallback = _ODCS_GEN._build_fallback_contract(table_name, columns)
                yaml_content = yaml.dump(fallback, default_flow_style=False)

            contract_path = upload_contract_to_minio(table_name, yaml_content, cfg)

            pg_cfg = PostgresConfig(
                host=cfg.postgres_host,
                port=cfg.postgres_port,
                dbname=cfg.postgres_dbname,
                user=cfg.postgres_user,
                password=cfg.postgres_password,
            )
            activator = ConnectorActivator(
                kafka_connect_url=cfg.kafka_connect_url,
                postgres=pg_cfg,
            )
            result = activator.activate(table_name)
            http_status = result.get("status", "")
            if http_status not in ("already_active",) and "error" in str(result).lower():
                raise RuntimeError(f"Debezium activation failed: {result}")

            kafka_ok = consume_one_kafka_message(state["kafka_topic"], cfg, timeout_seconds=60)
            if not kafka_ok:
                raise RuntimeError(f"No Kafka message received on {state['kafka_topic']} within 60s")

            emit_score(state["langfuse_trace_id"], "contract_valid", 1.0)
            emit_score(state["langfuse_trace_id"], "cdc_active", 1.0)
            elapsed = time.monotonic() - start
            return {
                **state,
                "data_contract_path": contract_path,
                "last_completed": "data_source",
                "next_agent": "orchestrator",
                "agent_timings": {**state["agent_timings"], "data_source": elapsed},
                "scores": {**state["scores"], "contract_valid": 1.0, "cdc_active": 1.0},
            }

        except Exception as exc:
            emit_score(state["langfuse_trace_id"], "contract_valid", 0.0, comment=str(exc))
            elapsed = time.monotonic() - start
            return {
                **state,
                "current_status": "ERROR",
                "error_log": f"[DataSource] {exc}",
                "last_completed": "data_source",
                "next_agent": "orchestrator",
                "agent_timings": {**state["agent_timings"], "data_source": elapsed},
                "scores": {**state["scores"], "contract_valid": 0.0, "cdc_active": 0.0},
            }
