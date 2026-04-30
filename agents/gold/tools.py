from __future__ import annotations

import subprocess
import time

import httpx

from agents.config import Config


def trigger_airflow_dag(dag_id: str, conf: dict, cfg: Config) -> str:
    resp = httpx.post(
        f"{cfg.airflow_url}/api/v1/dags/{dag_id}/dagRuns",
        json={"conf": conf},
        auth=(cfg.airflow_user, cfg.airflow_pass),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["dag_run_id"]


def wait_for_dag_run(
    dag_id: str,
    run_id: str,
    cfg: Config,
    timeout: int = 900,
    poll_interval: int = 15,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = httpx.get(
            f"{cfg.airflow_url}/api/v1/dags/{dag_id}/dagRuns/{run_id}",
            auth=(cfg.airflow_user, cfg.airflow_pass),
            timeout=30,
        )
        resp.raise_for_status()
        dag_state = resp.json()["state"]
        if dag_state in ("success", "failed"):
            return dag_state
        time.sleep(poll_interval)
    raise TimeoutError(f"DAG {dag_id}/{run_id} did not complete within {timeout}s")


def clear_and_retry_dag(dag_id: str, run_id: str, cfg: Config) -> str:
    """Clear task instances for a failed run and create a new DAG run. Returns new run_id."""
    httpx.post(
        f"{cfg.airflow_url}/api/v1/dags/{dag_id}/clearTaskInstances",
        json={"dag_run_id": run_id, "dry_run": False},
        auth=(cfg.airflow_user, cfg.airflow_pass),
        timeout=30,
    )
    return trigger_airflow_dag(dag_id, conf={}, cfg=cfg)


def query_trino_count(table_fqn: str) -> int:
    """Run SELECT COUNT(*) via kubectl exec into trino-coordinator."""
    result = subprocess.run(
        ["kubectl", "exec", "-n", "serving", "deployment/trino-coordinator", "--",
         "trino", "--execute", f"SELECT COUNT(*) FROM {table_fqn}",
         "--catalog", "iceberg", "--output-format", "TSV"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Trino query failed: {result.stderr.strip()}")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return int(lines[0]) if lines else 0


def check_openmetadata_lineage(table_fqn: str, openmetadata_url: str) -> bool:
    try:
        resp = httpx.get(
            f"{openmetadata_url}/api/v1/lineage/table/name/{table_fqn}",
            timeout=15,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
