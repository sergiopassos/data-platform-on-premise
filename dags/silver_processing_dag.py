"""Airflow DAG: Bronze → Silver batch processing.

Discovers registered contracts in /contracts and submits a
SparkKubernetesOperator job per table for the previous day.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.spark_kubernetes import SparkKubernetesOperator
from airflow.providers.cncf.kubernetes.sensors.spark_kubernetes import SparkKubernetesSensor

CONTRACTS_DIR = Path(os.getenv("CONTRACTS_DIR", "/contracts"))
SPARK_APP_TEMPLATE = Path("/opt/airflow/dags/templates/silver-batch-app.yaml")

default_args = {
    "owner": "data-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


def get_registered_tables() -> list[str]:
    if not CONTRACTS_DIR.exists():
        return []
    return [p.stem for p in CONTRACTS_DIR.glob("*.yaml")]


def create_silver_dag(table_name: str) -> DAG:
    dag_id = f"silver_processing_{table_name}"

    with DAG(
        dag_id=dag_id,
        default_args=default_args,
        description=f"Bronze → Silver MERGE for table {table_name}",
        schedule="@hourly",
        start_date=datetime(2026, 1, 1),
        catchup=False,
        tags=["silver", "spark", table_name],
        max_active_runs=1,
    ) as dag:
        processing_date = "{{ ds }}"

        submit = SparkKubernetesOperator(
            task_id=f"submit_silver_{table_name}",
            namespace="processing",
            application_file="spark/applications/silver-batch-app.yaml",
            kubernetes_conn_id="kubernetes_default",
            do_xcom_push=True,
            params={
                "table_name": table_name,
                "date": processing_date,
            },
        )

        monitor = SparkKubernetesSensor(
            task_id=f"monitor_silver_{table_name}",
            namespace="processing",
            application_name=f"{{{{ task_instance.xcom_pull(task_ids='submit_silver_{table_name}') }}}}",
            kubernetes_conn_id="kubernetes_default",
            poke_interval=30,
            timeout=3600,
        )

        submit >> monitor

    return dag


for _table in get_registered_tables():
    globals()[f"silver_processing_{_table}"] = create_silver_dag(_table)

# Fallback DAG for tables not yet discovered at parse time
with DAG(
    dag_id="silver_processing_manual",
    default_args=default_args,
    description="Manual trigger for Silver processing of a specific table",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["silver", "spark", "manual"],
    params={"table_name": "orders", "date": "{{ ds }}"},
) as manual_dag:
    SparkKubernetesOperator(
        task_id="submit_silver_manual",
        namespace="processing",
        application_file="spark/applications/silver-batch-app.yaml",
        kubernetes_conn_id="kubernetes_default",
    )
