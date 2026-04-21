"""Airflow DAG: Silver → Gold via dbt + Cosmos.

Uses astronomer-cosmos DbtDag to automatically discover and run
all dbt models in the gold/ directory via the Trino adapter.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from airflow.sensors.external_task import ExternalTaskSensor
from cosmos import DbtDag, ProjectConfig, ProfileConfig, RenderConfig, LoadMode
from cosmos.profiles import TrinoTokenProfileMapping

DBT_PROJECT_PATH = Path("/opt/airflow/dbt")
DBT_PROFILES_PATH = DBT_PROJECT_PATH / "profiles"

default_args = {
    "owner": "data-platform",
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": False,
}

gold_dbt_dag = DbtDag(
    dag_id="gold_dbt_dag",
    default_args=default_args,
    description="Silver → Gold dbt models via Cosmos + Trino adapter",
    project_config=ProjectConfig(
        dbt_project_path=DBT_PROJECT_PATH,
    ),
    profile_config=ProfileConfig(
        profile_name="data_platform",
        target_name="prod",
        profile_mapping=TrinoTokenProfileMapping(
            conn_id="trino_default",
            profile_args={
                "database": "iceberg",
                "schema": "silver",
                "host": "trino.serving.svc.cluster.local",
                "port": 8080,
            },
        ),
    ),
    render_config=RenderConfig(
        load_method=LoadMode.DBT_LS,
        select=["path:models/gold"],
    ),
    operator_args={
        "image": "ghcr.io/dbt-labs/dbt-trino:1.8.0",
        "get_logs": True,
    },
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["gold", "dbt", "cosmos"],
    max_active_runs=1,
)
