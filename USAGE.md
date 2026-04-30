# Data Platform — Usage Guide

End-to-end guide for simulating and observing the full data flow: PostgreSQL → CDC → Kafka → MinIO (Bronze) → Silver → Gold → Trino.

---

## Prerequisites

KIND cluster running with all services synced and healthy in ArgoCD.

**Open port-forwards** (each in a separate terminal):

```bash
# ArgoCD — GitOps dashboard
kubectl port-forward svc/argocd-server -n argocd 8090:443

# Airflow — orchestration
kubectl port-forward svc/airflow-webserver -n orchestration 8081:8080

# MinIO Console — object storage
kubectl port-forward svc/minio-console -n infra 9001:9001

# Trino — SQL engine
kubectl port-forward svc/trino -n serving 8082:8080

# Chainlit Portal — self-service
kubectl port-forward svc/chainlit -n portal 8000:8000

# Kafka Connect (Debezium) — connector management
kubectl port-forward svc/kafka-connect -n streaming 8083:8083

# PostgreSQL source — CDC source database
kubectl port-forward svc/postgres -n infra 5432:5432
```

**Access:**

| Service | URL | Credentials |
|---|---|---|
| ArgoCD | https://localhost:8090 | admin / (see secret) |
| Airflow | http://localhost:8081 | admin / admin |
| MinIO Console | http://localhost:9001 | minio / minio123 |
| Trino UI | http://localhost:8082 | — (no auth) |
| Chainlit | http://localhost:8000 | — (no auth) |
| Kafka Connect | http://localhost:8083 | — (REST API) |

> ArgoCD password: `kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath="{.data.password}" | base64 -d`

---

## Step 1 — Seed PostgreSQL

Populate `customers` and `orders` with sample data.

> `psql` is not installed locally — use `kubectl exec` to access the pod directly.

```bash
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "CREATE TABLE IF NOT EXISTS customers (customer_id SERIAL PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE, name VARCHAR(255) NOT NULL, created_at TIMESTAMP DEFAULT NOW());" \
  -c "CREATE TABLE IF NOT EXISTS orders (order_id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), status VARCHAR(50) NOT NULL DEFAULT 'pending', amount NUMERIC(10,2) NOT NULL, created_at TIMESTAMP DEFAULT NOW());" \
  -c "INSERT INTO customers (email, name) SELECT 'customer_' || i || '@example.com', 'Customer ' || i FROM generate_series(1, 100) AS i ON CONFLICT DO NOTHING;" \
  -c "INSERT INTO orders (customer_id, status, amount) SELECT (i % 100) + 1, CASE (i % 5) WHEN 0 THEN 'pending' WHEN 1 THEN 'processing' WHEN 2 THEN 'shipped' WHEN 3 THEN 'delivered' ELSE 'cancelled' END, (RANDOM() * 1000)::NUMERIC(10,2) FROM generate_series(1, 100) AS i;"
```

**Verify:**

```bash
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "SELECT 'customers' as table, COUNT(*) FROM customers UNION ALL SELECT 'orders', COUNT(*) FROM orders;"
```

**Interactive exploration:**

```bash
kubectl exec -it -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb
```

```sql
\dt
SELECT * FROM customers LIMIT 5;
SELECT * FROM orders LIMIT 5;
SHOW wal_level;  -- should be 'logical'
```

---

## Step 2 — Activate CDC

The Chainlit portal (`http://localhost:8000`) automates the full flow: inspects the schema, generates the ODCS contract, and activates the Debezium connector in a single interaction.

### Option A — Chainlit Portal (recommended)

**LLM providers:**

| Provider | Requirement | Speed |
|---|---|---|
| `gemini` | `GEMINI_API_KEY` K8s Secret | ~5s |
| `fallback` | None | Instant (deterministic) |
| `ollama` | Ollama pod running in cluster | ~3–8 min (CPU) |

**Using the portal:**

1. Open `http://localhost:8000`
2. Select a provider via the ⚙ icon or use `/llm <name>` in the chat
3. Type the table name (e.g. `orders`)
4. The portal will: inspect schema → generate ODCS contract → upload to MinIO → activate Debezium connector

```
/llm gemini    → Google Gemini (requires GEMINI_API_KEY)
/llm fallback  → deterministic contract, no LLM
/llm ollama    → local Ollama (slow on CPU)
```

**Configure GEMINI_API_KEY (once per cluster):**

```bash
kubectl create secret generic gemini-api-secret \
  -n portal \
  --from-literal=api-key="<your-key>" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart deployment/chainlit -n portal
```

---

### Option B — Activate CDC via curl (no portal)

**Upload a contract manually to MinIO:**

```bash
MINIO_POD=$(kubectl get pod -n infra -l app=minio -o jsonpath='{.items[0].metadata.name}')
kubectl cp contracts/orders.yaml infra/$MINIO_POD:/tmp/orders.yaml
kubectl exec -n infra $MINIO_POD -- mc alias set local http://localhost:9000 minio minio123 --insecure
kubectl exec -n infra $MINIO_POD -- mc cp /tmp/orders.yaml local/contracts/orders.yaml
```

**Create Debezium connectors:**

```bash
# customers connector
curl -s -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "debezium-public-customers",
    "config": {
      "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
      "database.hostname": "postgres.infra.svc.cluster.local",
      "database.port": "5432",
      "database.user": "postgres",
      "database.password": "postgres",
      "database.dbname": "sourcedb",
      "database.server.name": "cdc",
      "table.include.list": "public.customers",
      "topic.prefix": "cdc",
      "plugin.name": "pgoutput",
      "publication.autocreate.mode": "filtered",
      "slot.name": "debezium_customers",
      "heartbeat.interval.ms": "10000"
    }
  }' | jq .

# orders connector
curl -s -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "debezium-public-orders",
    "config": {
      "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
      "database.hostname": "postgres.infra.svc.cluster.local",
      "database.port": "5432",
      "database.user": "postgres",
      "database.password": "postgres",
      "database.dbname": "sourcedb",
      "database.server.name": "cdc",
      "table.include.list": "public.orders",
      "topic.prefix": "cdc",
      "plugin.name": "pgoutput",
      "publication.autocreate.mode": "filtered",
      "slot.name": "debezium_orders",
      "heartbeat.interval.ms": "10000"
    }
  }' | jq .
```

**Verify connectors:**

```bash
curl -s http://localhost:8083/connectors | jq .
curl -s http://localhost:8083/connectors/debezium-public-customers/status | jq .
```

Expected: `"state": "RUNNING"`.

**Add `customers` to the Debezium publication** (required when both connectors share the same publication):

```bash
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "ALTER PUBLICATION dbz_publication ADD TABLE customers;" \
  -c "SELECT pubname, schemaname, tablename FROM pg_publication_tables;"

curl -s -X POST http://localhost:8083/connectors/debezium-public-customers/restart
curl -s http://localhost:8083/connectors/debezium-public-customers/status | jq '.connector.state'
```

---

## Step 3 — Observe Kafka Messages

```bash
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')

# Check current offset
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-get-offsets.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers

# Consume new messages in real time
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --timeout-ms 30000

# Read from a specific offset
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --partition 0 --offset 100 --max-messages 10 --timeout-ms 8000

# List all CDC topics
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-topics.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --list | grep cdc
```

**CDC message format** — data is inside `.payload`, not at the root:

```json
{
  "payload": {
    "before": null,
    "after": { "customer_id": 1, "email": "customer_1@example.com", "name": "Updated Customer" },
    "op": "u",
    "source": { "table": "customers", "lsn": 26950160 }
  }
}
```

`"op"` values: `c` (insert), `u` (update), `d` (delete), `r` (initial snapshot).

> `before` is `null` on updates because the table uses `REPLICA IDENTITY DEFAULT`. To capture the previous value: `ALTER TABLE customers REPLICA IDENTITY FULL;`

---

## Step 4 — Simulate PostgreSQL Changes

**Terminal 1 — Kafka consumer (real time):**
```bash
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --timeout-ms 60000
```

**Terminal 2 — Make changes in PostgreSQL:**
```bash
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb
```

```sql
-- INSERT → "op": "c" in Kafka
INSERT INTO customers (email, name) VALUES ('new@example.com', 'New Customer');

-- UPDATE → "op": "u" with before/after
UPDATE customers SET name = 'Updated Customer' WHERE email = 'new@example.com';

-- DELETE → "op": "d" with before: {...}, after: null
DELETE FROM customers WHERE email = 'new@example.com';

-- Bulk insert for volume
INSERT INTO orders (customer_id, status, amount)
SELECT
  (random() * 99 + 1)::int,
  CASE (floor(random() * 5))::int
    WHEN 0 THEN 'pending' WHEN 1 THEN 'processing'
    WHEN 2 THEN 'shipped' WHEN 3 THEN 'delivered' ELSE 'cancelled'
  END,
  (random() * 1000)::numeric(10,2)
FROM generate_series(1, 20);
```

---

## Step 5 — Bronze Layer: Kafka → MinIO/Iceberg

**One-time setup before starting Spark jobs:**

```bash
# Build the custom Spark image (pre-installed JARs)
docker build -t data-platform/spark:3.5.1 docker/spark/

# Load image into KIND nodes (required — imagePullPolicy: Never)
kind load docker-image data-platform/spark:3.5.1 --name data-platform

# Apply ConfigMaps with PySpark scripts
kubectl apply -f spark/scripts/bronze-streaming-configmap.yaml
kubectl apply -f spark/scripts/silver-batch-configmap.yaml
```

**Start the Bronze streaming job:**
```bash
kubectl apply -f spark/applications/bronze-streaming-app.yaml
```

**Monitor:**
```bash
kubectl get sparkapplication -n processing
DRIVER_POD=$(kubectl get pod -n processing -l spark-role=driver -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n processing $DRIVER_POD -f
```

**Query Bronze via Trino:**

> **DBeaver note:** DBeaver fails browsing the schema tree against Trino with Nessie, but direct SQL works. Use the SQL Editor (Ctrl+]) with `Catalog = iceberg` and `Schema = bronze`.

```bash
kubectl exec -it deployment/trino-coordinator -n serving -- trino
```

```sql
SHOW SCHEMAS FROM iceberg;
SHOW TABLES FROM iceberg.bronze;

SELECT COUNT(*) FROM iceberg.bronze.customers_valid;
SELECT COUNT(*) FROM iceberg.bronze.orders_valid;
SELECT COUNT(*) FROM iceberg.bronze.customers_invalid;

-- Inspect raw CDC data
SELECT _source_topic, _cdc_op, _ingested_at,
       json_extract_scalar(_raw_value, '$.after.email') AS email
FROM iceberg.bronze.customers_valid
ORDER BY _ingested_at DESC
LIMIT 10;

SELECT _cdc_op, COUNT(*) AS total
FROM iceberg.bronze.orders_valid
GROUP BY _cdc_op;
```

---

## Step 6 — Silver Layer: Bronze → Silver (MERGE)

Airflow runs the Spark Batch job hourly to process Bronze → Silver.

**Airflow UI at http://localhost:8081** (admin / admin):

- `silver_processing_manual` → trigger manually
- `silver_processing_customers` / `silver_processing_orders` → auto-triggered when ODCS contracts are present in `s3://contracts/`

> **Do not use** `kubectl exec -- airflow dags trigger` — the CLI inside the pod uses SQLite instead of PostgreSQL and will fail with `sqlite3.OperationalError: no such table: dag`. Always use the REST API or UI.

**Trigger via REST API:**
```bash
curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {"table_name": "orders"}}' | jq .

curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {"table_name": "customers"}}' | jq .
```

**Monitor Spark Silver job:**
```bash
kubectl get sparkapplication -n processing
DRIVER=$(kubectl get pod -n processing -l spark-role=driver --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
kubectl logs -n processing $DRIVER -f
```

**Query Silver via Trino:**
```sql
SHOW TABLES FROM iceberg.silver;

SELECT * FROM iceberg.silver.customers LIMIT 10;
SELECT * FROM iceberg.silver.orders LIMIT 10;
SELECT COUNT(*) FROM iceberg.silver.customers;

SELECT status, COUNT(*) as total
FROM iceberg.silver.orders
GROUP BY status ORDER BY total DESC;
```

Silver layer applies:
- Deduplication by PK (keeps the most recent CDC event)
- `DELETE` on Silver when `_cdc_op = 'D'`
- `UPDATE` when record exists, `INSERT` when it's new

---

## Step 7 — Gold Layer: dbt via Trino

Airflow runs dbt models daily via Astronomer Cosmos.

**Trigger via REST API:**
```bash
curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/gold_dbt_dag/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {}}' | jq .
```

**Query Gold via Trino:**
```sql
SHOW TABLES FROM iceberg.gold;

-- Orders summary by day and status
SELECT * FROM iceberg.gold.orders_summary
ORDER BY order_date DESC, total_amount DESC
LIMIT 20;

-- Customer lifetime value (top 10)
SELECT email, total_orders, lifetime_value, last_order_at
FROM iceberg.gold.customers_orders
ORDER BY lifetime_value DESC
LIMIT 10;
```

---

## Step 8 — End-to-End Flow

```bash
# 1. Insert a new customer
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "INSERT INTO customers (email, name) VALUES ('test@cdc.com', 'CDC Test') RETURNING customer_id;"

# 2. Verify in Kafka (wait a few seconds)
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers --max-messages 1

# 3. Wait ~30s and check Bronze (Spark ingests every 10s)
# Via Trino: SELECT * FROM iceberg.bronze.customers_valid WHERE json_extract_scalar(_raw_value, '$.after.email') = 'test@cdc.com'

# 4. Trigger Silver
curl -s -u admin:admin -X POST http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns \
  -H "Content-Type: application/json" -d '{"conf": {"table_name": "customers"}}' | jq .status

# 5. Trigger Gold
curl -s -u admin:admin -X POST http://localhost:8081/api/v1/dags/gold_dbt_dag/dagRuns \
  -H "Content-Type: application/json" -d '{"conf": {}}' | jq .status
```

**Measure end-to-end latency via Trino:**
```sql
SELECT
  _cdc_op,
  _cdc_ts,
  _ingested_at,
  (_ingested_at - _cdc_ts) AS latency,
  json_extract_scalar(_raw_value, '$.after.email') AS email
FROM iceberg.bronze.customers_valid
WHERE json_extract_scalar(_raw_value, '$.after.email') = 'test@cdc.com'
ORDER BY _ingested_at DESC;
```

---

## Observability

**Service health:**
```bash
kubectl get pods -A | grep -v Running | grep -v Completed
```

**Kafka consumer lag:**
```bash
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --describe --group spark-bronze-streaming
```

**Debezium connectors:**
```bash
curl -s http://localhost:8083/connectors?expand=status | jq '.[].status.connector.state'
curl -s http://localhost:8083/connectors/debezium-public-customers/status | jq .
curl -s -X POST http://localhost:8083/connectors/debezium-public-customers/restart
```

**Spark jobs:**
```bash
kubectl get sparkapplication -n processing
kubectl describe sparkapplication bronze-streaming -n processing
kubectl logs -n processing \
  $(kubectl get pod -n processing -l spark-role=driver -o jsonpath='{.items[0].metadata.name}') \
  --tail=100
```

---

## Troubleshooting

**Debezium: replication slot blocked**
```bash
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "SELECT slot_name, active, restart_lsn FROM pg_replication_slots;"

# Drop slot if needed (restarts CDC from the beginning)
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "SELECT pg_drop_replication_slot('debezium_customers');"
```

**Spark: `ErrImageNeverPull`**

The `data-platform/spark:3.5.1` image must be loaded into KIND before use. `imagePullPolicy: Never` prevents automatic download.

```bash
docker build -t data-platform/spark:3.5.1 docker/spark/
kind load docker-image data-platform/spark:3.5.1 --name data-platform
```

**Spark: job stuck or errored**
```bash
kubectl delete sparkapplication bronze-streaming -n processing
kubectl apply -f spark/applications/bronze-streaming-app.yaml
```

**Airflow: `sqlite3.OperationalError: no such table: dag`**

Never use `kubectl exec -- airflow dags trigger`. Use the REST API or UI.

```bash
curl -s -u admin:admin http://localhost:8081/api/v1/dags | jq '[.dags[].dag_id]'
curl -s -u admin:admin "http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns?limit=5" \
  | jq '.dag_runs[] | {dag_run_id, state}'
```

**Trino: Nessie connection error**
```bash
kubectl get pod -n infra -l app.kubernetes.io/name=nessie
kubectl logs -n infra deployment/nessie --tail=50
kubectl exec -it deployment/trino-coordinator -n serving -- trino --execute "SHOW CATALOGS;"
```

**Chainlit: Gemini fails with "GEMINI_API_KEY is not set"**
```bash
kubectl get secret gemini-api-secret -n portal
kubectl create secret generic gemini-api-secret \
  -n portal --from-literal=api-key="<your-key>" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deployment/chainlit -n portal
```

**Chainlit: Ollama times out**

The `ollama` provider has a 30-second timeout on CPU. Switch to `fallback` or `gemini` in the chat:
```
/llm fallback
```

---

## Data Contracts (ODCS)

Contracts live in `contracts/` (repository) and are read by Spark jobs from `s3://contracts/` (dedicated MinIO bucket).

**ODCS contract structure:**
```yaml
dataContractSpecification: "0.9.3"
id: urn:datacontract:customers
name: customers
version: 1.0.0
description: Auto-generated contract for table customers
owner: data-platform
domain: source
schema:
  type: table
  fields:
    - name: customer_id
      type: integer
      required: true
      primaryKey: true
    - name: email
      type: string
      required: true
    - name: name
      type: string
      required: true
    - name: created_at
      type: timestamp
quality:
  - type: notNull
    column: customer_id
  - type: notNull
    column: email
  - type: unique
    column: customer_id
```

**Manual contract upload:**
```bash
MINIO_POD=$(kubectl get pod -n infra -l app=minio -o jsonpath='{.items[0].metadata.name}')
kubectl cp contracts/customers.yaml infra/$MINIO_POD:/tmp/customers.yaml
kubectl exec -n infra $MINIO_POD -- mc alias set local http://localhost:9000 minio minio123 --insecure
kubectl exec -n infra $MINIO_POD -- mc cp /tmp/customers.yaml local/contracts/customers.yaml
kubectl exec -n infra $MINIO_POD -- mc ls local/contracts/
```

---

## Quick Reference

| Action | Command |
|---|---|
| Open portal | `kubectl port-forward svc/chainlit -n portal 8000:8000` → `http://localhost:8000` |
| Switch LLM provider | `/llm gemini` or `/llm fallback` or `/llm ollama` (in chat) |
| Set GEMINI_API_KEY | `kubectl create secret generic gemini-api-secret -n portal --from-literal=api-key="..." --dry-run=client -o yaml \| kubectl apply -f -` |
| Portal logs | `kubectl logs -n portal deployment/chainlit --tail=50` |
| Connector status | `curl -s http://localhost:8083/connectors \| jq .` |
| Trigger Silver | `curl -s -u admin:admin -X POST http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns -H "Content-Type: application/json" -d '{"conf":{"table_name":"orders"}}'` |
| Trigger Gold | `curl -s -u admin:admin -X POST http://localhost:8081/api/v1/dags/gold_dbt_dag/dagRuns -H "Content-Type: application/json" -d '{}'` |
| Query Bronze | `SELECT COUNT(*) FROM iceberg.bronze.customers_valid` |
| Query Silver | `SELECT COUNT(*) FROM iceberg.silver.customers` |
| Query Gold | `SELECT * FROM iceberg.gold.orders_summary` |
| Spark jobs | `kubectl get sparkapplication -n processing` |
| Pods with errors | `kubectl get pods -A \| grep -v Running \| grep -v Completed` |
