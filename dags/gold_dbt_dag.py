"""Airflow DAG: Silver → Gold via dbt + Cosmos (LOCAL execution mode).

Cosmos DbtDag discovers and runs all dbt models under models/gold/ via the
Trino adapter. The dbt project is git-cloned into /opt/airflow/dbt by an
initContainer in each KubernetesExecutor worker pod.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from cosmos import DbtDag, ProjectConfig, ProfileConfig, RenderConfig, LoadMode, ExecutionConfig, ExecutionMode

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
        profiles_yml_filepath=DBT_PROFILES_PATH / "profiles.yml",
    ),
    execution_config=ExecutionConfig(
        execution_mode=ExecutionMode.LOCAL,
        dbt_executable_path="/home/airflow/.local/bin/dbt",
    ),
    render_config=RenderConfig(
        load_method=LoadMode.DBT_LS,
        select=["path:models/gold"],
    ),
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["gold", "dbt", "cosmos"],
    max_active_runs=1,
)
