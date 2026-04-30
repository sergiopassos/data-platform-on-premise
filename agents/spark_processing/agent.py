from __future__ import annotations

import time

from agents.config import Config
from agents.observability import emit_score, observe
from agents.spark_processing.tools import (
    apply_sparkapplication,
    check_nessie_table_exists,
    delete_sparkapplication,
    get_spark_driver_logs,
    get_sparkapplication_status,
    render_silver_manifest,
    wait_for_sparkapplication,
)
from agents.state import E2EState

_BRONZE_APP_NAME = "bronze-streaming"
_NAMESPACE = "processing"


def spark_node(state: E2EState) -> E2EState:
    start = time.monotonic()
    cfg = Config.from_env()
    table_name = state["table_name"]

    with observe(state["langfuse_trace_id"], name="spark_bronze_silver"):
        try:
            # Ensure bronze-streaming is running
            bronze_status = get_sparkapplication_status(_BRONZE_APP_NAME, _NAMESPACE)
            if bronze_status not in ("RUNNING",):
                from pathlib import Path
                manifest = Path("spark/applications/bronze-streaming-app.yaml").read_text()
                apply_sparkapplication(manifest, _NAMESPACE)
                bronze_status = wait_for_sparkapplication(
                    _BRONZE_APP_NAME, _NAMESPACE,
                    expected_states={"RUNNING"},
                    timeout=300,
                )
                if bronze_status != "RUNNING":
                    logs = get_spark_driver_logs(_BRONZE_APP_NAME, _NAMESPACE)
                    raise RuntimeError(
                        f"bronze-streaming reached state {bronze_status}. Logs:\n{logs[:500]}"
                    )

            # Wait for bronze table to appear in Nessie (CDC events processed)
            bronze_table = f"{table_name}_valid"
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if check_nessie_table_exists(cfg.nessie_url, "bronze", bronze_table):
                    break
                time.sleep(15)
            else:
                raise TimeoutError(
                    f"Bronze table 'bronze.{bronze_table}' not found in Nessie within 300s"
                )

            # Apply silver-batch SparkApplication
            silver_app_name = f"silver-batch-{table_name}"
            delete_sparkapplication(silver_app_name, _NAMESPACE)
            silver_manifest = render_silver_manifest(table_name)
            apply_sparkapplication(silver_manifest, _NAMESPACE)

            silver_status = wait_for_sparkapplication(
                silver_app_name, _NAMESPACE,
                expected_states={"COMPLETED", "SUCCEEDED"},
                timeout=600,
            )
            if silver_status == "FAILED":
                logs = get_spark_driver_logs(silver_app_name, _NAMESPACE)
                raise RuntimeError(
                    f"silver-batch-{table_name} FAILED. Logs:\n{logs[:500]}"
                )

            # Verify silver table in Nessie
            if not check_nessie_table_exists(cfg.nessie_url, "silver", table_name):
                raise RuntimeError(f"Silver table 'silver.{table_name}' not found in Nessie after job completion")

            delete_sparkapplication(silver_app_name, _NAMESPACE)

            silver_rows_score = 1.0
            emit_score(state["langfuse_trace_id"], "silver_rows", silver_rows_score)
            elapsed = time.monotonic() - start
            return {
                **state,
                "last_completed": "spark",
                "next_agent": "orchestrator",
                "agent_timings": {**state["agent_timings"], "spark": elapsed},
                "scores": {**state["scores"], "silver_rows": silver_rows_score},
            }

        except Exception as exc:
            emit_score(state["langfuse_trace_id"], "silver_rows", 0.0, comment=str(exc))
            elapsed = time.monotonic() - start
            return {
                **state,
                "current_status": "ERROR",
                "error_log": f"[Spark] {exc}",
                "last_completed": "spark",
                "next_agent": "orchestrator",
                "agent_timings": {**state["agent_timings"], "spark": elapsed},
                "scores": {**state["scores"], "silver_rows": 0.0},
            }
