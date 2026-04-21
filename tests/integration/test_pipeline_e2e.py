"""End-to-end integration tests for the full data pipeline.

Prerequisites:
  - KIND cluster running with all components healthy
  - kubectl port-forwards active:
      - Chainlit:      localhost:8000
      - KafkaConnect:  localhost:8083
      - Airflow:       localhost:8080
      - Trino:         localhost:8081
  - External Postgres accessible at POSTGRES_HOST:POSTGRES_PORT
"""
import os
import time

import httpx
import psycopg
import pytest
import trino

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "sourcedb")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")

CHAINLIT_URL = os.getenv("CHAINLIT_URL", "http://localhost:8000")
KAFKA_CONNECT_URL = os.getenv("KAFKA_CONNECT_URL", "http://localhost:8083")
AIRFLOW_URL = os.getenv("AIRFLOW_URL", "http://localhost:8080")
TRINO_HOST = os.getenv("TRINO_HOST", "localhost")
TRINO_PORT = int(os.getenv("TRINO_PORT", "8081"))

TEST_TABLE = "e2e_orders"
POLL_INTERVAL = 10
MAX_WAIT = 180


def _trino_conn():
    return trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user="test",
        catalog="iceberg",
        schema="bronze",
    )


def _poll_until(condition_fn, timeout: int = MAX_WAIT, interval: int = POLL_INTERVAL, msg: str = "") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if condition_fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    pytest.fail(f"Timed out after {timeout}s: {msg}")
    return False


@pytest.fixture(scope="module", autouse=True)
def seed_postgres():
    with psycopg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    ) as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TEST_TABLE} (
                order_id SERIAL PRIMARY KEY,
                amount NUMERIC(10,2) NOT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'pending'
            )
        """)
        conn.execute(f"TRUNCATE TABLE {TEST_TABLE}")
        conn.execute(f"""
            INSERT INTO {TEST_TABLE} (amount, status)
            SELECT (random() * 1000)::NUMERIC(10,2), 'pending'
            FROM generate_series(1, 20)
        """)
        conn.commit()
    yield
    with psycopg.connect(
        host=POSTGRES_HOST, port=POSTGRES_PORT, dbname=POSTGRES_DB,
        user=POSTGRES_USER, password=POSTGRES_PASSWORD,
    ) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
        conn.commit()


class TestChainlitPortal:
    def test_portal_health(self):
        resp = httpx.get(f"{CHAINLIT_URL}/health", timeout=10)
        assert resp.status_code == 200


class TestDebeziumConnector:
    def test_connector_activated_after_portal_registration(self):
        connector_name = f"debezium-public-{TEST_TABLE}"

        def connector_exists():
            resp = httpx.get(f"{KAFKA_CONNECT_URL}/connectors/{connector_name}", timeout=5)
            return resp.status_code == 200

        if not connector_exists():
            httpx.post(
                f"{KAFKA_CONNECT_URL}/connectors",
                json={
                    "name": connector_name,
                    "config": {
                        "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
                        "database.hostname": POSTGRES_HOST,
                        "database.port": str(POSTGRES_PORT),
                        "database.user": POSTGRES_USER,
                        "database.password": POSTGRES_PASSWORD,
                        "database.dbname": POSTGRES_DB,
                        "table.include.list": f"public.{TEST_TABLE}",
                        "topic.prefix": "cdc",
                        "plugin.name": "pgoutput",
                        "slot.name": f"debezium_{TEST_TABLE}",
                    },
                },
                timeout=10,
            )

        _poll_until(connector_exists, timeout=60, msg=f"Connector {connector_name} not found")


class TestBronzeLayer:
    def test_bronze_valid_receives_cdc_events(self):
        def bronze_has_rows():
            with _trino_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM iceberg.bronze.valid_{TEST_TABLE}")
                row = cur.fetchone()
                return row and row[0] >= 20

        _poll_until(bronze_has_rows, timeout=MAX_WAIT, msg=f"bronze.valid_{TEST_TABLE} did not receive 20 rows")

    def test_bronze_valid_count_matches_seed(self):
        with _trino_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM iceberg.bronze.valid_{TEST_TABLE}")
            count = cur.fetchone()[0]
        assert count >= 20


class TestSilverLayer:
    def test_silver_dedup_by_primary_key(self):
        def silver_has_rows():
            with _trino_conn() as conn:
                conn.schema = "silver"
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM iceberg.silver.{TEST_TABLE}")
                row = cur.fetchone()
                return row and row[0] >= 20

        _poll_until(silver_has_rows, timeout=MAX_WAIT, msg=f"silver.{TEST_TABLE} is empty")

    def test_silver_no_duplicates(self):
        with _trino_conn() as conn:
            conn.schema = "silver"
            cur = conn.cursor()
            cur.execute(f"""
                SELECT order_id, COUNT(*) as cnt
                FROM iceberg.silver.{TEST_TABLE}
                GROUP BY order_id
                HAVING COUNT(*) > 1
            """)
            duplicates = cur.fetchall()
        assert len(duplicates) == 0, f"Duplicates found in silver: {duplicates}"


class TestGoldLayer:
    def test_gold_orders_summary_has_rows(self):
        def gold_has_rows():
            with _trino_conn() as conn:
                conn.schema = "gold"
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM iceberg.gold.orders_summary")
                row = cur.fetchone()
                return row and row[0] > 0

        _poll_until(gold_has_rows, timeout=MAX_WAIT, msg="gold.orders_summary is empty")
