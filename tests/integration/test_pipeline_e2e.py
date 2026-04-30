"""End-to-end integration tests for the full data pipeline.

Prerequisites (must be running in separate terminals before pytest):
  kubectl port-forward svc/postgres        -n infra        5432:5432
  kubectl port-forward svc/kafka-connect   -n streaming    8083:8083
  kubectl port-forward svc/airflow-webserver -n orchestration 8081:8080
  kubectl port-forward svc/trino           -n serving      8082:8080

Run:
  pytest tests/integration/ -v --timeout=300
"""
from __future__ import annotations

import os
import subprocess
import time

import httpx
import pytest

KAFKA_CONNECT_URL = os.getenv("KAFKA_CONNECT_URL", "http://localhost:8083")
AIRFLOW_URL = os.getenv("AIRFLOW_URL", "http://localhost:8081")
AIRFLOW_USER = os.getenv("AIRFLOW_USER", "admin")
AIRFLOW_PASS = os.getenv("AIRFLOW_PASS", "admin")
TRINO_HOST = os.getenv("TRINO_HOST", "localhost")
TRINO_PORT = int(os.getenv("TRINO_PORT", "8082"))

POLL_INTERVAL = 10
MAX_WAIT = 300


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kubectl(*args: str) -> str:
    result = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _psql(sql: str) -> str:
    pod = _kubectl("get", "pod", "-n", "infra", "-l", "app=postgres",
                   "-o", "jsonpath={.items[0].metadata.name}")
    return _kubectl("exec", "-n", "infra", pod, "--", "psql", "-U", "postgres",
                    "-d", "sourcedb", "-t", "-c", sql)


def _trino_query(sql: str) -> list[str]:
    result = subprocess.run(
        ["kubectl", "exec", "-n", "serving", "deployment/trino-coordinator", "--",
         "trino", "--execute", sql, "--catalog", "iceberg", "--output-format", "TSV"],
        capture_output=True, text=True, timeout=60,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _poll_until(fn, timeout: int = MAX_WAIT, interval: int = POLL_INTERVAL, msg: str = "") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    pytest.fail(f"Timed out after {timeout}s: {msg}")
    return False


def _airflow(method: str, path: str, **kwargs) -> httpx.Response:
    return httpx.request(
        method,
        f"{AIRFLOW_URL}{path}",
        auth=(AIRFLOW_USER, AIRFLOW_PASS),
        timeout=30,
        **kwargs,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def seed_postgres():
    _psql("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id SERIAL PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    _psql("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            status VARCHAR(50) NOT NULL DEFAULT 'pending',
            amount NUMERIC(10,2) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    _psql("""
        INSERT INTO customers (email, name)
        SELECT 'customer_' || i || '@example.com', 'Customer ' || i
        FROM generate_series(1, 100) AS i ON CONFLICT DO NOTHING
    """)
    _psql("""
        INSERT INTO orders (customer_id, status, amount)
        SELECT (i % 100) + 1,
          CASE (i % 5) WHEN 0 THEN 'pending' WHEN 1 THEN 'processing'
                       WHEN 2 THEN 'shipped'  WHEN 3 THEN 'delivered' ELSE 'cancelled' END,
          (RANDOM() * 1000)::NUMERIC(10,2)
        FROM generate_series(1, 100) AS i
    """)
    yield


# ── Step 1: PostgreSQL ────────────────────────────────────────────────────────

class TestPostgresSetup:
    def test_customers_seeded(self):
        count = int(_psql("SELECT COUNT(*) FROM customers"))
        assert count >= 100, f"customers has only {count} rows"

    def test_orders_seeded(self):
        count = int(_psql("SELECT COUNT(*) FROM orders"))
        assert count >= 100, f"orders has only {count} rows"

    def test_wal_level_logical(self):
        wal = _psql("SHOW wal_level").strip()
        assert wal == "logical", f"wal_level is '{wal}', expected 'logical'"


# ── Step 2: Debezium ──────────────────────────────────────────────────────────

class TestDebeziumConnectors:
    @pytest.mark.parametrize("table", ["customers", "orders"])
    def test_connector_running(self, table: str):
        name = f"debezium-public-{table}"

        # Create if not present
        existing = httpx.get(f"{KAFKA_CONNECT_URL}/connectors/{name}", timeout=10)
        if existing.status_code == 404:
            resp = httpx.post(
                f"{KAFKA_CONNECT_URL}/connectors",
                json={
                    "name": name,
                    "config": {
                        "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
                        "database.hostname": "postgres.infra.svc.cluster.local",
                        "database.port": "5432",
                        "database.user": "postgres",
                        "database.password": "postgres",
                        "database.dbname": "sourcedb",
                        "database.server.name": "cdc",
                        "table.include.list": f"public.{table}",
                        "topic.prefix": "cdc",
                        "plugin.name": "pgoutput",
                        "publication.autocreate.mode": "filtered",
                        "slot.name": f"debezium_{table}",
                        "heartbeat.interval.ms": "10000",
                    },
                },
                timeout=15,
            )
            assert resp.status_code in (200, 201), f"Create connector failed: {resp.text}"

        def is_running():
            r = httpx.get(f"{KAFKA_CONNECT_URL}/connectors/{name}/status", timeout=10)
            return r.status_code == 200 and r.json()["connector"]["state"] == "RUNNING"

        _poll_until(is_running, timeout=60, msg=f"Connector {name} not RUNNING")


# ── Step 3: Kafka Topics ──────────────────────────────────────────────────────

class TestKafkaTopics:
    @pytest.mark.parametrize("topic", ["cdc.public.customers", "cdc.public.orders"])
    def test_topic_exists(self, topic: str):
        kafka_pod = _kubectl(
            "get", "pod", "-n", "streaming",
            "-l", "strimzi.io/name=kafka-cluster-kafka",
            "-o", "jsonpath={.items[0].metadata.name}",
        )
        result = subprocess.run(
            ["kubectl", "exec", "-n", "streaming", kafka_pod, "--",
             "bin/kafka-topics.sh",
             "--bootstrap-server", "kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092",
             "--list"],
            capture_output=True, text=True, timeout=30,
        )
        assert topic in result.stdout, f"Topic {topic} not found in Kafka"


# ── Step 5: Bronze Layer ──────────────────────────────────────────────────────

class TestBronzeLayer:
    @pytest.mark.parametrize("table", ["customers_valid", "orders_valid"])
    def test_bronze_table_has_rows(self, table: str):
        def has_rows():
            rows = _trino_query(f"SELECT COUNT(*) FROM iceberg.bronze.{table}")
            return rows and int(rows[0]) >= 1

        _poll_until(has_rows, timeout=MAX_WAIT, msg=f"bronze.{table} has no rows")

    def test_bronze_customers_cdc_op_present(self):
        rows = _trino_query(
            "SELECT DISTINCT _cdc_op FROM iceberg.bronze.customers_valid LIMIT 10"
        )
        ops = {r.strip() for r in rows}
        assert ops & {"c", "r", "u"}, f"No CDC ops found in bronze.customers_valid: {ops}"


# ── Step 6: Silver Layer ──────────────────────────────────────────────────────

class TestSilverLayer:
    def test_trigger_silver_dag(self):
        resp = _airflow(
            "POST",
            "/api/v1/dags/silver_processing_manual/dagRuns",
            json={"conf": {"table_name": "customers"}},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (200, 409), f"Trigger failed: {resp.text}"

    def test_silver_dag_completes(self):
        def dag_succeeded():
            resp = _airflow(
                "GET",
                "/api/v1/dags/silver_processing_manual/dagRuns?limit=1&order_by=-start_date",
            )
            runs = resp.json().get("dag_runs", [])
            return runs and runs[0]["state"] == "success"

        _poll_until(dag_succeeded, timeout=MAX_WAIT, msg="Silver DAG did not succeed")

    def test_silver_customers_no_duplicates(self):
        rows = _trino_query("""
            SELECT customer_id, COUNT(*) AS cnt
            FROM iceberg.silver.customers
            GROUP BY customer_id
            HAVING COUNT(*) > 1
        """)
        assert len(rows) == 0, f"Duplicate rows in silver.customers: {rows[:5]}"

    def test_silver_customers_has_rows(self):
        rows = _trino_query("SELECT COUNT(*) FROM iceberg.silver.customers")
        assert rows and int(rows[0]) >= 1, "silver.customers is empty"


# ── Step 7: Gold Layer ────────────────────────────────────────────────────────

class TestGoldLayer:
    def test_trigger_gold_dag(self):
        resp = _airflow(
            "POST",
            "/api/v1/dags/gold_dbt_dag/dagRuns",
            json={"conf": {}},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (200, 409), f"Trigger failed: {resp.text}"

    def test_gold_orders_summary_has_rows(self):
        def has_rows():
            rows = _trino_query("SELECT COUNT(*) FROM iceberg.gold.orders_summary")
            return rows and int(rows[0]) >= 1

        _poll_until(has_rows, timeout=MAX_WAIT, msg="gold.orders_summary is empty")

    def test_gold_customers_orders_has_rows(self):
        def has_rows():
            rows = _trino_query("SELECT COUNT(*) FROM iceberg.gold.customers_orders")
            return rows and int(rows[0]) >= 1

        _poll_until(has_rows, timeout=MAX_WAIT, msg="gold.customers_orders is empty")
