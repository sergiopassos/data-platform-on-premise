# Pattern: dbt Gold Layer via Cosmos in Airflow

## What It Is

Cosmos (astronomer-cosmos) is an Airflow provider that converts a dbt project into Airflow tasks automatically. Each dbt model becomes a task; dependencies between models become task dependencies. No manual DAG code per model — one `DbtDag` declaration covers the entire Gold layer.

## Why Cosmos over Manual Airflow + dbt

| Approach | DAG Code | Observability | Dependency Tracking |
|----------|----------|---------------|---------------------|
| Manual `BashOperator` running `dbt run` | 5 lines | Opaque (one task) | None |
| Manual `KubernetesPodOperator` per model | N lines per model | Better | Manual |
| Cosmos `DbtDag` | ~15 lines total | Per-model task in UI | Automatic from dbt DAG |

## Stack

- **Airflow**: `apache/airflow:2.9.3-python3.11` with `KubernetesExecutor`
- **Cosmos**: `astronomer-cosmos==1.5.0`
- **dbt**: `dbt-core==1.8.0` + `dbt-trino==1.8.0`
- **Query engine**: Trino over Iceberg/MinIO

## Airflow Packages

```yaml
# helm/airflow/values.yaml
extraPipPackages:
  - "astronomer-cosmos==1.5.0"
  - "dbt-core==1.8.0"
  - "dbt-trino==1.8.0"
  - "apache-airflow-providers-trino==5.7.0"
```

## dbt Project Structure

```
dbt/gold/
├── dbt_project.yml
├── profiles.yml           # or via env var / Airflow connection
├── models/
│   ├── orders_daily.sql
│   ├── customer_ltv.sql
│   └── revenue_summary.sql
└── tests/
    └── orders_daily.yml
```

## dbt_project.yml (Trino + Iceberg)

```yaml
name: data_platform_gold
version: "1.0.0"
profile: data_platform

models:
  data_platform_gold:
    +materialized: view    # views are cheapest on Trino/Iceberg
    +schema: gold
```

## profiles.yml (Trino Adapter)

```yaml
data_platform:
  target: dev
  outputs:
    dev:
      type: trino
      method: none
      host: trino.serving.svc.cluster.local
      port: 8080
      database: iceberg
      schema: gold
      threads: 4
      http_scheme: http
```

## Cosmos DAG

```python
# dags/gold_transformations.py
from cosmos import DbtDag, ProjectConfig, ProfileConfig, ExecutionConfig
from cosmos.profiles import TrinoUserPasswordProfileMapping
from datetime import datetime

profile_config = ProfileConfig(
    profile_name="data_platform",
    target_name="dev",
    profile_mapping=TrinoUserPasswordProfileMapping(
        conn_id="trino_default",
        profile_args={
            "database": "iceberg",
            "schema": "gold",
        },
    ),
)

gold_dag = DbtDag(
    dag_id="gold_transformations",
    project_config=ProjectConfig("/opt/airflow/dbt/gold"),
    profile_config=profile_config,
    execution_config=ExecutionConfig(
        dbt_executable_path="/home/airflow/.local/bin/dbt",
    ),
    schedule_interval="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["gold", "dbt"],
)
```

## Mounting dbt Project into Airflow

The dbt project must be accessible at `/opt/airflow/dbt` in both the scheduler pod (DAG parsing) and worker pods (task execution).

```yaml
# helm/airflow/values.yaml
extraVolumes:
  - name: dbt-project
    persistentVolumeClaim:
      claimName: airflow-dbt-pvc

extraVolumeMounts:
  - name: dbt-project
    mountPath: /opt/airflow/dbt
```

The PVC `airflow-dbt-pvc` must contain the dbt project files. In development, use a Job or initContainer to sync from Git, or mount directly from the DAGs PVC.

## Trino Airflow Connection

```yaml
env:
  - name: AIRFLOW_CONN_TRINO_DEFAULT
    value: "trino://trino.serving.svc.cluster.local:8080/iceberg"
```

Or via Airflow UI: Admin → Connections → `trino_default`

## dbt Model for Silver → Gold

```sql
-- models/orders_daily.sql
{{ config(materialized='view') }}

SELECT
    DATE_TRUNC('day', created_at) AS order_date,
    COUNT(*)                       AS total_orders,
    SUM(amount)                    AS total_revenue,
    AVG(amount)                    AS avg_order_value
FROM {{ source('silver', 'orders') }}
WHERE created_at >= CURRENT_DATE - INTERVAL '90' DAY
GROUP BY 1
```

```yaml
# models/sources.yml
sources:
  - name: silver
    database: iceberg
    schema: silver
    tables:
      - name: orders
```

## dbt Tests

```yaml
# models/schema.yml
models:
  - name: orders_daily
    columns:
      - name: order_date
        tests:
          - not_null
          - unique
      - name: total_revenue
        tests:
          - not_null
```

Cosmos runs `dbt test` tasks after each model task automatically when `select_models` includes tests.

## Airflow Task Graph

With Cosmos, the Airflow UI shows:
```
orders_daily_run → orders_daily_test
customer_ltv_run → customer_ltv_test
revenue_summary_run → revenue_summary_test
```

Dependencies follow the dbt model DAG — if `revenue_summary` depends on `orders_daily`, Airflow enforces that order automatically.
