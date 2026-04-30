from __future__ import annotations

import uuid
from typing import Literal, TypedDict


class E2EState(TypedDict):
    run_id: str
    langfuse_trace_id: str
    current_status: Literal["RUNNING", "ERROR", "SUCCESS"]
    table_name: str
    gold_table_fqn: str               # e.g. "iceberg.gold.customers_orders"
    data_contract_path: str           # s3://contracts/{table}.yaml
    kafka_topic: str                  # cdc.public.{table_name}
    error_log: str | None
    last_completed: str               # last agent name that finished; "" = none
    next_agent: str                   # node the orchestrator will route to next
    agent_timings: dict[str, float]  # wall-clock seconds per agent
    scores: dict[str, float]         # langfuse eval scores
    report_markdown: str | None


def initial_state(table_name: str, gold_table_fqn: str = "iceberg.gold.customers_orders") -> E2EState:
    run_id = f"e2e-{uuid.uuid4().hex[:8]}"
    return E2EState(
        run_id=run_id,
        langfuse_trace_id="",
        current_status="RUNNING",
        table_name=table_name,
        gold_table_fqn=gold_table_fqn,
        data_contract_path="",
        kafka_topic=f"cdc.public.{table_name}",
        error_log=None,
        last_completed="",
        next_agent="orchestrator",
        agent_timings={},
        scores={},
        report_markdown=None,
    )
