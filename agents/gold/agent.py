from __future__ import annotations

import time

from agents.config import Config
from agents.gold.tools import (
    clear_and_retry_dag,
    query_trino_count,
    trigger_airflow_dag,
    wait_for_dag_run,
)
from agents.observability import emit_score, observe
from agents.state import E2EState

_DAG_ID = "gold_dbt_dag"


def gold_node(state: E2EState) -> E2EState:
    start = time.monotonic()
    cfg = Config.from_env()

    with observe(state["langfuse_trace_id"], name="gold_dbt_trino"):
        try:
            run_id = trigger_airflow_dag(_DAG_ID, conf={}, cfg=cfg)
            dag_state = wait_for_dag_run(_DAG_ID, run_id, cfg, timeout=900)

            if dag_state == "failed":
                retry_run_id = clear_and_retry_dag(_DAG_ID, run_id, cfg)
                dag_state = wait_for_dag_run(_DAG_ID, retry_run_id, cfg, timeout=900)

            if dag_state != "success":
                raise RuntimeError(f"gold_dbt_dag failed after retry (state={dag_state})")

            count = query_trino_count(state["gold_table_fqn"])
            if count == 0:
                raise RuntimeError(
                    f"Gold table {state['gold_table_fqn']} has 0 rows after dbt DAG success"
                )

            emit_score(state["langfuse_trace_id"], "gold_rows", float(count))
            elapsed = time.monotonic() - start
            return {
                **state,
                "last_completed": "gold",
                "next_agent": "orchestrator",
                "agent_timings": {**state["agent_timings"], "gold": elapsed},
                "scores": {**state["scores"], "gold_rows": float(count)},
            }

        except Exception as exc:
            emit_score(state["langfuse_trace_id"], "gold_rows", 0.0, comment=str(exc))
            elapsed = time.monotonic() - start
            return {
                **state,
                "current_status": "ERROR",
                "error_log": f"[Gold] {exc}",
                "last_completed": "gold",
                "next_agent": "orchestrator",
                "agent_timings": {**state["agent_timings"], "gold": elapsed},
                "scores": {**state["scores"], "gold_rows": 0.0},
            }
