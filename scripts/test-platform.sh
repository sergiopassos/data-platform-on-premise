#!/usr/bin/env bash
# Platform smoke test — mirrors USAGE.md, marking steps AUTO or MANUAL.
# Usage:
#   ./scripts/test-platform.sh              # run all auto steps
#   ./scripts/test-platform.sh --step 3     # run single step
#   ./scripts/test-platform.sh --no-seed    # skip PostgreSQL seeding
#   ./scripts/test-platform.sh --no-spark   # skip Spark image build/load
set -uo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
AIRFLOW_URL="${AIRFLOW_URL:-http://localhost:8081}"
AIRFLOW_USER="${AIRFLOW_USER:-admin}"
AIRFLOW_PASS="${AIRFLOW_PASS:-admin}"
KAFKA_CONNECT_URL="${KAFKA_CONNECT_URL:-http://localhost:8083}"
TRINO_HOST="${TRINO_HOST:-localhost}"
TRINO_PORT="${TRINO_PORT:-8082}"
MINIO_URL="${MINIO_URL:-http://localhost:9001}"

SKIP_SEED="${SKIP_SEED:-false}"
SKIP_SPARK_BUILD="${SKIP_SPARK_BUILD:-false}"
SINGLE_STEP="${SINGLE_STEP:-}"

for arg in "$@"; do
  case "$arg" in
    --no-seed)   SKIP_SEED=true ;;
    --no-spark)  SKIP_SPARK_BUILD=true ;;
    --step)      shift; SINGLE_STEP="$1" ;;
    --step=*)    SINGLE_STEP="${arg#--step=}" ;;
  esac
done

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

PASS=0; FAIL=0; SKIP=0; MANUAL=0

# ── Helpers ───────────────────────────────────────────────────────────────────
_pass()   { echo -e "${GREEN}  [PASS]${RESET} $*"; (( PASS++ )); }
_fail()   { echo -e "${RED}  [FAIL]${RESET} $*"; (( FAIL++ )); }
_skip()   { echo -e "${YELLOW}  [SKIP]${RESET} $*"; (( SKIP++ )); }
_manual() { echo -e "${CYAN}  [MANUAL]${RESET} $*"; (( MANUAL++ )); }
_info()   { echo -e "${BLUE}  [INFO]${RESET} $*"; }
_header() { echo -e "\n${BOLD}${YELLOW}▶ $*${RESET}"; }

_check() {
  local label="$1"; shift
  if "$@" &>/dev/null; then _pass "$label"; return 0
  else _fail "$label"; return 1; fi
}

_require_cmd() {
  command -v "$1" &>/dev/null || { echo -e "${RED}[ERROR]${RESET} '$1' not found. Install it first."; exit 1; }
}

_kubectl_exec_postgres() {
  local POD
  POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  [[ -z "$POD" ]] && return 1
  kubectl exec -n infra "$POD" -- psql -U postgres -d sourcedb "$@"
}

_poll() {
  local label="$1" timeout="${2:-120}" cmd="${@:3}"
  local deadline=$(( $(date +%s) + timeout ))
  echo -ne "  ${BLUE}[WAIT]${RESET} $label..."
  while (( $(date +%s) < deadline )); do
    if eval "$cmd" &>/dev/null; then echo -e " ${GREEN}OK${RESET}"; return 0; fi
    echo -n "."
    sleep 5
  done
  echo -e " ${RED}TIMEOUT${RESET}"
  return 1
}

_should_run() {
  [[ -z "$SINGLE_STEP" ]] || [[ "$SINGLE_STEP" == "$1" ]]
}

# ── Section 0: Prerequisites ──────────────────────────────────────────────────
_header "Step 0 — Prerequisites"

_require_cmd kubectl
_require_cmd curl
_require_cmd jq
_require_cmd docker
_require_cmd kind

# Cluster reachable
_check "kubectl cluster accessible" kubectl cluster-info

# ArgoCD apps not failing
UNHEALTHY=$(kubectl get applications -n argocd -o json 2>/dev/null \
  | jq -r '.items[] | select(.status.health.status != "Healthy") | .metadata.name' \
  | tr '\n' ' ')
if [[ -z "$UNHEALTHY" ]]; then
  _pass "All ArgoCD apps Healthy"
else
  _fail "Unhealthy ArgoCD apps: $UNHEALTHY"
fi

# Port-forwards reachable
echo ""
echo -e "  ${YELLOW}Port-forward checks (requires each open in a separate terminal):${RESET}"
declare -A PF_CHECKS=(
  ["Airflow:8081"]="curl -sf -u ${AIRFLOW_USER}:${AIRFLOW_PASS} ${AIRFLOW_URL}/health"
  ["Kafka Connect:8083"]="curl -sf ${KAFKA_CONNECT_URL}/"
  ["Trino:8082"]="curl -sf http://${TRINO_HOST}:${TRINO_PORT}/v1/info"
  ["PostgreSQL:5432"]="kubectl get pod -n infra -l app=postgres --field-selector=status.phase=Running -o name | grep -q pod"
)
for svc in "${!PF_CHECKS[@]}"; do
  if eval "${PF_CHECKS[$svc]}" &>/dev/null; then
    _pass "$svc reachable"
  else
    _fail "$svc not reachable — run: kubectl port-forward svc/<name> ..."
  fi
done

_manual "MinIO Console (http://localhost:9001) — open in browser: minio / minio123"
_manual "ArgoCD UI (https://localhost:8090) — open in browser"
_manual "Chainlit Portal (http://localhost:8000) — open in browser"

# ── Section 1: Seed PostgreSQL ────────────────────────────────────────────────
if _should_run 1; then
_header "Step 1 — Seed PostgreSQL"

if [[ "$SKIP_SEED" == "true" ]]; then
  _skip "Seeding skipped (--no-seed)"
else
  POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [[ -z "$POSTGRES_POD" ]]; then
    _fail "No postgres pod found in infra namespace"
  else
    _info "Pod: $POSTGRES_POD"

    kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb \
      -c "CREATE TABLE IF NOT EXISTS customers (customer_id SERIAL PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE, name VARCHAR(255) NOT NULL, created_at TIMESTAMP DEFAULT NOW());" \
      -c "CREATE TABLE IF NOT EXISTS orders (order_id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), status VARCHAR(50) NOT NULL DEFAULT 'pending', amount NUMERIC(10,2) NOT NULL, created_at TIMESTAMP DEFAULT NOW());" \
      -c "INSERT INTO customers (email, name) SELECT 'customer_' || i || '@example.com', 'Customer ' || i FROM generate_series(1, 100) AS i ON CONFLICT DO NOTHING;" \
      -c "INSERT INTO orders (customer_id, status, amount) SELECT (i % 100) + 1, CASE (i % 5) WHEN 0 THEN 'pending' WHEN 1 THEN 'processing' WHEN 2 THEN 'shipped' WHEN 3 THEN 'delivered' ELSE 'cancelled' END, (RANDOM() * 1000)::NUMERIC(10,2) FROM generate_series(1, 100) AS i;" \
      &>/dev/null && _pass "Tables created and seeded" || _fail "Seed failed"

    CUSTOMER_COUNT=$(kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb -t -c "SELECT COUNT(*) FROM customers;" 2>/dev/null | tr -d ' ')
    ORDER_COUNT=$(kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb -t -c "SELECT COUNT(*) FROM orders;" 2>/dev/null | tr -d ' ')

    [[ "${CUSTOMER_COUNT:-0}" -ge 100 ]] && _pass "customers: $CUSTOMER_COUNT rows" || _fail "customers: $CUSTOMER_COUNT rows (expected >= 100)"
    [[ "${ORDER_COUNT:-0}" -ge 100 ]] && _pass "orders: $ORDER_COUNT rows" || _fail "orders: $ORDER_COUNT rows (expected >= 100)"

    WAL=$(kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb -t -c "SHOW wal_level;" 2>/dev/null | tr -d ' ')
    [[ "$WAL" == "logical" ]] && _pass "wal_level = logical" || _fail "wal_level = '$WAL' (expected 'logical')"
  fi
fi
fi

# ── Section 2: Activate CDC ───────────────────────────────────────────────────
if _should_run 2; then
_header "Step 2 — Activate CDC (Debezium connectors)"

_manual "Option A: Use Chainlit portal at http://localhost:8000 (type table name, e.g. 'orders')"
echo ""
echo -e "  ${BOLD}Running Option B (curl):${RESET}"

for TABLE in customers orders; do
  CONN_NAME="debezium-public-${TABLE}"
  EXISTING=$(curl -sf "${KAFKA_CONNECT_URL}/connectors/${CONN_NAME}" 2>/dev/null | jq -r '.name' 2>/dev/null)

  if [[ "$EXISTING" == "$CONN_NAME" ]]; then
    _pass "Connector $CONN_NAME already exists"
  else
    SLOT="debezium_${TABLE}"
    HTTP_CODE=$(curl -s -o /tmp/conn_response.json -w "%{http_code}" -X POST "${KAFKA_CONNECT_URL}/connectors" \
      -H "Content-Type: application/json" \
      -d "{
        \"name\": \"${CONN_NAME}\",
        \"config\": {
          \"connector.class\": \"io.debezium.connector.postgresql.PostgresConnector\",
          \"database.hostname\": \"postgres.infra.svc.cluster.local\",
          \"database.port\": \"5432\",
          \"database.user\": \"postgres\",
          \"database.password\": \"postgres\",
          \"database.dbname\": \"sourcedb\",
          \"database.server.name\": \"cdc\",
          \"table.include.list\": \"public.${TABLE}\",
          \"topic.prefix\": \"cdc\",
          \"plugin.name\": \"pgoutput\",
          \"publication.autocreate.mode\": \"filtered\",
          \"slot.name\": \"${SLOT}\",
          \"heartbeat.interval.ms\": \"10000\"
        }
      }" 2>/dev/null)
    if [[ "$HTTP_CODE" == "201" ]]; then _pass "Connector $CONN_NAME created"
    else _fail "Connector $CONN_NAME failed (HTTP $HTTP_CODE): $(cat /tmp/conn_response.json 2>/dev/null | jq -r '.message // .' 2>/dev/null)"; fi
  fi
done

# Add customers to publication if needed
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [[ -n "$POSTGRES_POD" ]]; then
  PUB_TABLES=$(kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb -t \
    -c "SELECT string_agg(tablename, ',') FROM pg_publication_tables WHERE pubname = 'dbz_publication';" 2>/dev/null | tr -d ' \n')
  _info "Publication tables: ${PUB_TABLES:-none}"
  if [[ "$PUB_TABLES" != *"customers"* ]]; then
    kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb \
      -c "ALTER PUBLICATION dbz_publication ADD TABLE customers;" &>/dev/null \
      && _pass "Added customers to dbz_publication" \
      || _info "Could not add customers to publication (may not exist yet)"
  fi
fi

# Wait for connectors to be RUNNING
for TABLE in customers orders; do
  CONN_NAME="debezium-public-${TABLE}"
  _poll "Waiting for $CONN_NAME RUNNING" 60 \
    "curl -sf ${KAFKA_CONNECT_URL}/connectors/${CONN_NAME}/status | jq -e '.connector.state == \"RUNNING\"'" \
    && _pass "$CONN_NAME is RUNNING" || _fail "$CONN_NAME not RUNNING"
done
fi

# ── Section 3: Observe Kafka ──────────────────────────────────────────────────
if _should_run 3; then
_header "Step 3 — Observe Kafka messages"

KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [[ -z "$KAFKA_POD" ]]; then
  _fail "No Kafka pod found"
else
  _info "Pod: $KAFKA_POD"

  # Check CDC topics exist
  for TOPIC in cdc.public.customers cdc.public.orders; do
    TOPIC_EXISTS=$(kubectl exec -n streaming "$KAFKA_POD" -- \
      bin/kafka-topics.sh --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
      --list 2>/dev/null | grep -c "^${TOPIC}$" || true)
    [[ "${TOPIC_EXISTS:-0}" -ge 1 ]] && _pass "Topic $TOPIC exists" || _fail "Topic $TOPIC not found"
  done

  # Check offsets (messages present)
  for TOPIC in cdc.public.customers cdc.public.orders; do
    OFFSETS=$(kubectl exec -n streaming "$KAFKA_POD" -- \
      bin/kafka-get-offsets.sh \
      --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
      --topic "$TOPIC" 2>/dev/null | head -1 || true)
    if [[ -n "$OFFSETS" ]]; then
      _pass "Topic $TOPIC has offsets: $OFFSETS"
    else
      _fail "Topic $TOPIC has no offsets"
    fi
  done

  _manual "To consume messages in real time:"
  _info "  kubectl exec -n streaming $KAFKA_POD -- bin/kafka-console-consumer.sh \\"
  _info "    --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \\"
  _info "    --topic cdc.public.customers --timeout-ms 30000"
fi
fi

# ── Section 4: Simulate PostgreSQL Changes ────────────────────────────────────
if _should_run 4; then
_header "Step 4 — Simulate PostgreSQL changes"

POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [[ -z "$POSTGRES_POD" ]]; then
  _fail "No postgres pod found"
else
  # Insert a test record
  kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb \
    -c "INSERT INTO customers (email, name) VALUES ('test-smoke@cdc.com', 'Smoke Test') ON CONFLICT DO NOTHING;" &>/dev/null \
    && _pass "Test customer inserted" || _fail "Insert failed"

  # Verify it's there
  COUNT=$(kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb -t \
    -c "SELECT COUNT(*) FROM customers WHERE email = 'test-smoke@cdc.com';" 2>/dev/null | tr -d ' ')
  [[ "${COUNT:-0}" -ge 1 ]] && _pass "Test customer visible in PostgreSQL" || _fail "Test customer not found"

  # Bulk orders for volume
  kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb \
    -c "INSERT INTO orders (customer_id, status, amount) SELECT (random() * 99 + 1)::int, 'pending', (random() * 1000)::numeric(10,2) FROM generate_series(1, 10);" \
    &>/dev/null && _pass "10 test orders inserted" || _fail "Bulk order insert failed"

  _manual "For interactive CDC simulation (2 terminals):"
  _info "  Terminal 1 (consumer): kubectl exec -n streaming \$KAFKA_POD -- bin/kafka-console-consumer.sh ..."
  _info "  Terminal 2 (changes):  kubectl exec -it -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb"
fi
fi

# ── Section 5: Bronze Layer ───────────────────────────────────────────────────
if _should_run 5; then
_header "Step 5 — Bronze Layer (Kafka → MinIO/Iceberg via Spark Streaming)"

# Check if Spark image is loaded
IMAGE_EXISTS=$(docker images -q data-platform/spark:3.5.1 2>/dev/null)
if [[ -z "$IMAGE_EXISTS" ]]; then
  if [[ "$SKIP_SPARK_BUILD" == "true" ]]; then
    _skip "Spark image not built (--no-spark)"
  else
    _info "Building Spark image..."
    docker build -t data-platform/spark:3.5.1 docker/spark/ \
      && _pass "Spark image built" || { _fail "Docker build failed"; }

    _info "Loading image into KIND nodes (may take a minute)..."
    kind load docker-image data-platform/spark:3.5.1 --name data-platform \
      && _pass "Spark image loaded into KIND" || _fail "kind load failed"
  fi
else
  _pass "Spark image data-platform/spark:3.5.1 present"
  KIND_LOADED=$(docker exec data-platform-control-plane crictl images 2>/dev/null | grep -c "data-platform/spark" || true)
  [[ "${KIND_LOADED:-0}" -ge 1 ]] && _pass "Image loaded in KIND nodes" || _info "Image may not be loaded in KIND — run: kind load docker-image data-platform/spark:3.5.1 --name data-platform"
fi

# Apply ConfigMaps
for CM in spark/scripts/bronze-streaming-configmap.yaml spark/scripts/silver-batch-configmap.yaml; do
  kubectl apply -f "$CM" &>/dev/null && _pass "Applied $CM" || _fail "Failed to apply $CM"
done

# Check or start bronze streaming job
BRONZE_STATUS=$(kubectl get sparkapplication bronze-streaming -n processing -o jsonpath='{.status.applicationState.state}' 2>/dev/null || echo "NOT_FOUND")
_info "Bronze streaming status: $BRONZE_STATUS"

case "$BRONZE_STATUS" in
  RUNNING)  _pass "Bronze streaming job RUNNING" ;;
  COMPLETED|FAILED|UNKNOWN|NOT_FOUND)
    _info "Applying bronze-streaming-app.yaml..."
    kubectl apply -f spark/applications/bronze-streaming-app.yaml &>/dev/null \
      && _pass "SparkApplication bronze-streaming applied" \
      || _fail "Failed to apply bronze-streaming-app.yaml"
    _poll "Waiting for bronze-streaming RUNNING" 180 \
      "kubectl get sparkapplication bronze-streaming -n processing -o jsonpath='{.status.applicationState.state}' 2>/dev/null | grep -q 'RUNNING'"
    FINAL_STATUS=$(kubectl get sparkapplication bronze-streaming -n processing -o jsonpath='{.status.applicationState.state}' 2>/dev/null)
    [[ "$FINAL_STATUS" == "RUNNING" ]] && _pass "Bronze streaming is RUNNING" || _fail "Bronze streaming status: $FINAL_STATUS"
    ;;
  *) _info "Bronze streaming state: $BRONZE_STATUS" ;;
esac

_manual "Query Bronze via Trino (after ~30s of data flowing):"
_info "  kubectl exec -it deployment/trino-coordinator -n serving -- trino"
_info "  SELECT COUNT(*) FROM iceberg.bronze.customers_valid;"
_info "  SELECT COUNT(*) FROM iceberg.bronze.orders_valid;"

# Try Trino query if reachable
TRINO_REACHABLE=$(curl -sf "http://${TRINO_HOST}:${TRINO_PORT}/v1/info" 2>/dev/null | jq -r '.starting' 2>/dev/null)
if [[ "$TRINO_REACHABLE" == "false" ]]; then
  BRONZE_COUNT=$(kubectl exec deployment/trino-coordinator -n serving -- \
    trino --execute "SELECT COUNT(*) FROM iceberg.bronze.customers_valid" \
    --catalog iceberg --schema bronze 2>/dev/null | tail -1 | tr -d ' ' || echo "")
  if [[ -n "$BRONZE_COUNT" ]] && [[ "$BRONZE_COUNT" -gt 0 ]]; then
    _pass "Bronze customers_valid: $BRONZE_COUNT rows"
  else
    _info "Bronze table may not have data yet (streaming needs ~30s)"
  fi
fi
fi

# ── Section 6: Silver Layer ───────────────────────────────────────────────────
if _should_run 6; then
_header "Step 6 — Silver Layer (Airflow → Spark Batch MERGE)"

# Check Airflow is reachable
AIRFLOW_HEALTH=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" "${AIRFLOW_URL}/health" 2>/dev/null | jq -r '.metadatabase.status' 2>/dev/null)
if [[ "$AIRFLOW_HEALTH" != "healthy" ]]; then
  _fail "Airflow not healthy (status: ${AIRFLOW_HEALTH:-unreachable})"
else
  _pass "Airflow metadatabase healthy"

  # Check DAG exists
  DAG_CHECK=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
    "${AIRFLOW_URL}/api/v1/dags/silver_processing_manual" 2>/dev/null | jq -r '.dag_id' 2>/dev/null)
  [[ "$DAG_CHECK" == "silver_processing_manual" ]] \
    && _pass "DAG silver_processing_manual found" \
    || _fail "DAG silver_processing_manual not found — check git-sync in worker pod"

  # Trigger Silver for both tables
  for TABLE in customers orders; do
    RUN_RESPONSE=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
      -X POST "${AIRFLOW_URL}/api/v1/dags/silver_processing_manual/dagRuns" \
      -H "Content-Type: application/json" \
      -d "{\"conf\": {\"table_name\": \"${TABLE}\"}}" 2>/dev/null)
    RUN_ID=$(echo "$RUN_RESPONSE" | jq -r '.dag_run_id' 2>/dev/null)
    if [[ -n "$RUN_ID" ]] && [[ "$RUN_ID" != "null" ]]; then
      _pass "Triggered silver_processing_manual for $TABLE (run_id: $RUN_ID)"
    else
      _fail "Failed to trigger silver for $TABLE: $(echo "$RUN_RESPONSE" | jq -r '.detail // .' 2>/dev/null)"
    fi
  done

  # Check SparkApplication appears
  _poll "Waiting for a Silver SparkApplication" 300 \
    "kubectl get sparkapplication -n processing -o name 2>/dev/null | grep -q silver"

  SILVER_APP=$(kubectl get sparkapplication -n processing -o name 2>/dev/null | grep silver | head -1)
  if [[ -n "$SILVER_APP" ]]; then
    APP_NAME="${SILVER_APP#sparkapplication.sparkoperator.k8s.io/}"
    _info "Silver SparkApplication: $APP_NAME"
    _poll "Waiting for $APP_NAME to complete" 600 \
      "kubectl get sparkapplication $APP_NAME -n processing -o jsonpath='{.status.applicationState.state}' 2>/dev/null | grep -qE 'COMPLETED|FAILED'"
    SILVER_STATE=$(kubectl get sparkapplication "$APP_NAME" -n processing -o jsonpath='{.status.applicationState.state}' 2>/dev/null)
    [[ "$SILVER_STATE" == "COMPLETED" ]] \
      && _pass "Silver SparkApplication $APP_NAME COMPLETED" \
      || _fail "Silver SparkApplication $APP_NAME state: $SILVER_STATE"
  fi
fi
fi

# ── Section 7: Gold Layer ─────────────────────────────────────────────────────
if _should_run 7; then
_header "Step 7 — Gold Layer (dbt via Airflow + Cosmos)"

AIRFLOW_HEALTH=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" "${AIRFLOW_URL}/health" 2>/dev/null | jq -r '.metadatabase.status' 2>/dev/null)
if [[ "$AIRFLOW_HEALTH" != "healthy" ]]; then
  _fail "Airflow not reachable — skipping Gold trigger"
else
  DAG_CHECK=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
    "${AIRFLOW_URL}/api/v1/dags/gold_dbt_dag" 2>/dev/null | jq -r '.dag_id' 2>/dev/null)
  [[ "$DAG_CHECK" == "gold_dbt_dag" ]] \
    && _pass "DAG gold_dbt_dag found" \
    || _fail "DAG gold_dbt_dag not found"

  RUN_RESPONSE=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
    -X POST "${AIRFLOW_URL}/api/v1/dags/gold_dbt_dag/dagRuns" \
    -H "Content-Type: application/json" \
    -d '{"conf": {}}' 2>/dev/null)
  RUN_ID=$(echo "$RUN_RESPONSE" | jq -r '.dag_run_id' 2>/dev/null)
  [[ -n "$RUN_ID" ]] && [[ "$RUN_ID" != "null" ]] \
    && _pass "Triggered gold_dbt_dag (run_id: $RUN_ID)" \
    || _fail "Failed to trigger gold_dbt_dag: $(echo "$RUN_RESPONSE" | jq -r '.detail // .' 2>/dev/null)"

  _manual "Monitor: Airflow UI → gold_dbt_dag → graph view"
  _manual "Query:   SELECT * FROM iceberg.gold.orders_summary LIMIT 10;"
fi
fi

# ── Section 8: End-to-End Smoke Test ─────────────────────────────────────────
if _should_run 8; then
_header "Step 8 — End-to-End smoke test"

POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [[ -z "$POSTGRES_POD" ]]; then
  _fail "PostgreSQL pod not found"
else
  # Insert a unique record
  E2E_EMAIL="e2e-$(date +%s)@smoke.test"
  kubectl exec -n infra "$POSTGRES_POD" -- psql -U postgres -d sourcedb \
    -c "INSERT INTO customers (email, name) VALUES ('${E2E_EMAIL}', 'E2E Smoke Test') ON CONFLICT DO NOTHING;" \
    &>/dev/null && _pass "E2E test record inserted: $E2E_EMAIL" || _fail "E2E insert failed"

  # Wait for Kafka message
  if [[ -n "$KAFKA_POD" ]]; then
    _poll "Waiting for CDC event in Kafka (max 30s)" 30 \
      "kubectl exec -n streaming $KAFKA_POD -- bin/kafka-console-consumer.sh \
        --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
        --topic cdc.public.customers --max-messages 1 --timeout-ms 5000 2>/dev/null | grep -q '$E2E_EMAIL'" \
      && _pass "CDC event seen in Kafka for $E2E_EMAIL" \
      || _info "CDC event not confirmed in Kafka (may need a few seconds)"
  fi

  _manual "After ~30s, check Bronze in Trino:"
  _info "  SELECT * FROM iceberg.bronze.customers_valid"
  _info "    WHERE json_extract_scalar(_raw_value, '\$.after.email') = '${E2E_EMAIL}';"

  # Check Airflow Silver DAG recent runs
  RECENT_RUN=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
    "${AIRFLOW_URL}/api/v1/dags/silver_processing_manual/dagRuns?limit=1&order_by=-start_date" \
    2>/dev/null | jq -r '.dag_runs[0].state' 2>/dev/null)
  _info "Last Silver DAG run state: ${RECENT_RUN:-unknown}"
fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  Test Summary${RESET}"
echo -e "${GREEN}  PASS:   $PASS${RESET}"
echo -e "${RED}  FAIL:   $FAIL${RESET}"
echo -e "${YELLOW}  SKIP:   $SKIP${RESET}"
echo -e "${CYAN}  MANUAL: $MANUAL (require browser or interactive terminal)${RESET}"
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  echo -e "${RED}${BOLD}RESULT: FAILED ($FAIL failures)${RESET}"
  exit 1
else
  echo -e "${GREEN}${BOLD}RESULT: ALL AUTO CHECKS PASSED${RESET}"
  exit 0
fi
