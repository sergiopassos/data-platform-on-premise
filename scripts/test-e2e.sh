#!/usr/bin/env bash
# End-to-end data platform setup + verification.
#
# Steps:
#   1.  Bootstrap KIND cluster + ArgoCD sync
#   2.  Port-forwards (stay alive after script exits)
#   3.  Build + load Spark image, apply ConfigMaps
#   4.  Start Bronze streaming Spark job
#   5.  Create + populate customers table in PostgreSQL
#   6.  Upload customers ODCS contract → s3://contracts/
#   7.  Activate Debezium CDC connector for customers
#   8.  Verify Bronze layer (iceberg.bronze.customers_valid)
#   9.  Trigger Silver DAG + wait for SparkApplication
#  10.  Verify Silver layer (iceberg.silver.customers)
#
# Env flags:
#   SKIP_BOOTSTRAP=true    cluster already running
#   SKIP_SPARK_BUILD=true  Spark image already loaded in KIND
#   TEARDOWN=true          kill port-forwards + delete cluster
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SKIP_BOOTSTRAP="${SKIP_BOOTSTRAP:-false}"
SKIP_SPARK_BUILD="${SKIP_SPARK_BUILD:-false}"
TEARDOWN="${TEARDOWN:-false}"

AIRFLOW_URL="${AIRFLOW_URL:-http://localhost:8081}"
AIRFLOW_USER="${AIRFLOW_USER:-admin}"
AIRFLOW_PASS="${AIRFLOW_PASS:-admin}"
KAFKA_CONNECT_URL="${KAFKA_CONNECT_URL:-http://localhost:8083}"
TRINO_URL="${TRINO_URL:-http://localhost:8082}"

# ── Output helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
PASS=0; FAIL=0

_pass()   { echo -e "${GREEN}  [PASS]${RESET} $*"; (( PASS++ )) || true; }
_fail()   { echo -e "${RED}  [FAIL]${RESET} $*" >&2; (( FAIL++ )) || true; }
_info()   { echo -e "${BLUE}  [INFO]${RESET} $*"; }
_header() { echo -e "\n${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n▶ $*${RESET}"; }
log()     { echo "[$(date '+%H:%M:%S')] $*"; }

_summary() {
  echo -e "\n${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${GREEN}  PASS: ${PASS}${RESET}   ${RED}FAIL: ${FAIL}${RESET}"
  echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  if [[ "$FAIL" -gt 0 ]]; then
    echo -e "${RED}${BOLD}  RESULT: FAILED (${FAIL} failure(s))${RESET}\n"
    return 1
  fi
  echo -e "${GREEN}${BOLD}  RESULT: ALL CHECKS PASSED${RESET}\n"
}

# ── Polling (never exits the script — caller decides) ─────────────────────────
# Returns 0 on success, 1 on timeout. Wrap in if/else at call site.
_poll() {
  local label="$1" timeout="${2:-120}" cmd="${*:3}"
  local deadline=$(( $(date +%s) + timeout ))
  echo -ne "  ${BLUE}[WAIT]${RESET} ${label}..."
  while (( $(date +%s) < deadline )); do
    if eval "$cmd" &>/dev/null; then
      echo -e " ${GREEN}OK${RESET}"; return 0
    fi
    echo -n "."; sleep 5
  done
  echo -e " ${RED}TIMEOUT${RESET}"; return 1
}

# ── kubectl helpers ────────────────────────────────────────────────────────────
_get_pod() {
  kubectl get pod -n "$1" -l "$2" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo ""
}

_psql() {
  local pod; pod=$(_get_pod infra "app=postgres")
  [[ -z "$pod" ]] && { _fail "No postgres pod"; return 1; }
  kubectl exec -n infra "$pod" -- psql -U postgres -d sourcedb "$@"
}

_trino_count() {
  # _trino_count "iceberg.bronze.customers_valid"  → prints integer
  kubectl exec -n serving deployment/trino-coordinator -- \
    trino --execute "SELECT COUNT(*) FROM ${1}" \
    --output-format TSV 2>/dev/null | tail -1 | tr -d '[:space:]' || echo "0"
}

# ── Airflow / Kafka Connect helpers ───────────────────────────────────────────
_airflow_healthy() {
  curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" "${AIRFLOW_URL}/health" 2>/dev/null \
    | jq -e '.metadatabase.status == "healthy"' &>/dev/null
}

_connector_state() {
  curl -sf "${KAFKA_CONNECT_URL}/connectors/${1}/status" 2>/dev/null \
    | jq -r '.connector.state' 2>/dev/null || echo "UNKNOWN"
}

# ── Port-forward management ────────────────────────────────────────────────────
PF_PIDS=()

_start_pf() {
  local label="$1" svc="$2" ns="$3" lport="$4" rport="$5"
  fuser -k "${lport}/tcp" &>/dev/null || true
  kubectl port-forward "svc/${svc}" -n "$ns" "${lport}:${rport}" \
    >"/tmp/pf-${label}.log" 2>&1 &
  PF_PIDS+=($!)
}

_stop_pfs() {
  for port in 8081 8082 8083 9001 5432; do
    fuser -k "${port}/tcp" &>/dev/null || true
  done
}

_cleanup() {
  if [[ "$TEARDOWN" == "true" ]]; then
    _stop_pfs
    kind delete cluster --name data-platform 2>/dev/null || true
  fi
}
trap _cleanup EXIT

# ══════════════════════════════════════════════════════════════════════════════
# TEARDOWN mode
# ══════════════════════════════════════════════════════════════════════════════
if [[ "$TEARDOWN" == "true" ]]; then
  _header "TEARDOWN"
  _stop_pfs && _pass "Port-forwards stopped"
  kind delete cluster --name data-platform \
    && _pass "KIND cluster deleted" \
    || _fail "Cluster delete failed (may not exist)"
  _summary; exit 0
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Bootstrap cluster
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 1 — Bootstrap cluster"

if [[ "$SKIP_BOOTSTRAP" == "true" ]]; then
  _info "Skipping bootstrap (SKIP_BOOTSTRAP=true)"
  if kubectl cluster-info &>/dev/null; then
    _pass "Cluster reachable"
  else
    _fail "Cluster not reachable — cannot continue"; _summary; exit 1
  fi
else
  log "Running bootstrap-cluster.sh..."
  if ./scripts/bootstrap-cluster.sh; then
    _pass "Bootstrap complete"
  else
    _fail "Bootstrap failed"; _summary; exit 1
  fi
fi

_header "Step 1b — Wait for ArgoCD apps Healthy"
ARGOCD_APPS=(minio nessie strimzi-operator kafka-cluster spark-operator airflow trino chainlit)
for app in "${ARGOCD_APPS[@]}"; do
  if _poll "ArgoCD '${app}'" 600 \
    "kubectl get application '${app}' -n argocd \
     -o jsonpath='{.status.health.status}' 2>/dev/null | grep -q 'Healthy'"; then
    _pass "ArgoCD '${app}' Healthy"
  else
    _fail "ArgoCD '${app}' not Healthy after 10 min"
  fi
done

# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Port-forwards (stay alive after script exits)
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 2 — Port-forwards"

_start_pf airflow       airflow-webserver  orchestration 8081 8080
_start_pf trino         trino              serving       8082 8080
_start_pf kafka-connect kafka-connect      streaming     8083 8083
_start_pf minio         minio-console      infra         9001 9001
_start_pf postgres      postgres           infra         5432 5432

sleep 3

if _poll "Airflow :8081" 60 \
  "curl -sf -u ${AIRFLOW_USER}:${AIRFLOW_PASS} ${AIRFLOW_URL}/health"; then
  _pass "Airflow reachable → http://localhost:8081  (admin / admin)"
else
  _fail "Airflow not reachable on :8081"
  cat /tmp/pf-airflow.log >&2 || true
fi

if _poll "Kafka Connect :8083" 30 "curl -sf ${KAFKA_CONNECT_URL}/"; then
  _pass "Kafka Connect reachable → http://localhost:8083"
else
  _fail "Kafka Connect not reachable on :8083"
fi

if _poll "Trino :8082" 60 \
  "curl -sf ${TRINO_URL}/v1/info | jq -e '.starting == false'"; then
  _pass "Trino reachable → http://localhost:8082"
else
  _fail "Trino not reachable on :8082"
fi

_pass "MinIO Console → http://localhost:9001  (minio / minio123)"
_pass "Port-forwards are running in background — they stay alive after this script exits"

# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Spark image + ConfigMaps
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 3 — Spark image + ConfigMaps"

if [[ "$SKIP_SPARK_BUILD" == "true" ]]; then
  _info "Skipping image build (SKIP_SPARK_BUILD=true)"
else
  if [[ -z "$(docker images -q data-platform/spark:3.5.1 2>/dev/null)" ]]; then
    log "Building Spark image..."
    if docker build -t data-platform/spark:3.5.1 docker/spark/; then
      _pass "Spark image built"
    else
      _fail "docker build failed"; _summary; exit 1
    fi
  else
    _pass "Spark image already present locally"
  fi

  log "Loading image into KIND (may take ~1 min)..."
  if kind load docker-image data-platform/spark:3.5.1 --name data-platform; then
    _pass "Image loaded into KIND"
  else
    _fail "kind load failed"
  fi
fi

for cm in spark/scripts/bronze-streaming-configmap.yaml \
          spark/scripts/silver-batch-configmap.yaml; do
  if kubectl apply -f "$cm" &>/dev/null; then
    _pass "Applied $(basename "$cm")"
  else
    _fail "Failed to apply $(basename "$cm")"
  fi
done

# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Start Bronze streaming (must be running before CDC events arrive)
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 4 — Bronze streaming (Kafka → Iceberg)"

BRONZE_STATE=$(kubectl get sparkapplication bronze-streaming -n processing \
  -o jsonpath='{.status.applicationState.state}' 2>/dev/null || echo "NOT_FOUND")
_info "Current state: ${BRONZE_STATE}"

if [[ "$BRONZE_STATE" == "RUNNING" ]]; then
  _pass "Bronze streaming already RUNNING"
else
  if [[ "$BRONZE_STATE" != "NOT_FOUND" ]]; then
    kubectl delete sparkapplication bronze-streaming -n processing &>/dev/null || true
    sleep 2
  fi

  if kubectl apply -f spark/applications/bronze-streaming-app.yaml &>/dev/null; then
    _pass "SparkApplication bronze-streaming applied"
  else
    _fail "Failed to apply bronze-streaming-app.yaml"
  fi

  if _poll "bronze-streaming RUNNING" 240 \
    "kubectl get sparkapplication bronze-streaming -n processing \
     -o jsonpath='{.status.applicationState.state}' 2>/dev/null | grep -q 'RUNNING'"; then
    _pass "Bronze streaming is RUNNING"
  else
    _fail "Bronze streaming did not reach RUNNING after 4 min"
    DRIVER=$(_get_pod processing "spark-role=driver")
    [[ -n "$DRIVER" ]] && kubectl logs -n processing "$DRIVER" --tail=20 >&2 || true
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 5 — Create customers table + populate
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 5 — PostgreSQL: customers table + 100 rows"

if _poll "PostgreSQL pod ready" 120 \
  "kubectl get pod -l app=postgres -n infra --no-headers 2>/dev/null | grep -q Running"; then

  if _psql \
    -c "CREATE TABLE IF NOT EXISTS customers (
          customer_id SERIAL PRIMARY KEY,
          email       VARCHAR(255) NOT NULL UNIQUE,
          name        VARCHAR(255) NOT NULL,
          created_at  TIMESTAMP DEFAULT NOW()
        );" &>/dev/null; then
    _pass "customers table created (idempotent)"
  else
    _fail "CREATE TABLE customers failed"
  fi

  if _psql \
    -c "INSERT INTO customers (email, name)
          SELECT 'customer_' || i || '@example.com', 'Customer ' || i
          FROM generate_series(1, 100) AS i
          ON CONFLICT DO NOTHING;" &>/dev/null; then
    _pass "100 customers inserted (ON CONFLICT DO NOTHING)"
  else
    _fail "INSERT customers failed"
  fi

  COUNT=$(_psql -t -c "SELECT COUNT(*) FROM customers;" 2>/dev/null | tr -d ' \n' || echo "0")
  if [[ "${COUNT:-0}" -ge 100 ]]; then
    _pass "customers table has ${COUNT} rows"
  else
    _fail "customers table has ${COUNT} rows (expected >= 100)"
  fi

  WAL=$(_psql -t -c "SHOW wal_level;" 2>/dev/null | tr -d ' \n' || echo "unknown")
  if [[ "$WAL" == "logical" ]]; then
    _pass "wal_level = logical (CDC ready)"
  else
    _fail "wal_level = '${WAL}' — CDC requires logical"
  fi
else
  _fail "PostgreSQL pod not ready after 2 min"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 6 — Upload customers ODCS contract → s3://contracts/
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 6 — Customers ODCS contract → s3://contracts/"

MINIO_POD=$(_get_pod infra "app=minio")
if [[ -z "$MINIO_POD" ]]; then
  _fail "MinIO pod not found"
else
  # Write contract into pod and upload — heredoc piped via stdin
  kubectl exec -n infra "$MINIO_POD" -- sh -c 'cat > /tmp/customers.yaml' <<'YAML'
dataContractSpecification: "0.9.3"
id: urn:datacontract:customers
name: customers
version: 1.0.0
description: Contract for table customers
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
YAML

  MC_UPLOAD=$(kubectl exec -n infra "$MINIO_POD" -- sh -c \
    "mc alias set local http://localhost:9000 minio minio123 --insecure >/dev/null 2>&1
     mc mb --ignore-existing local/contracts >/dev/null 2>&1
     mc cp /tmp/customers.yaml local/contracts/customers.yaml 2>&1" 2>/dev/null || echo "FAILED")

  if echo "$MC_UPLOAD" | grep -q "FAILED"; then
    _fail "MinIO upload failed: $MC_UPLOAD"
  else
    _pass "customers.yaml uploaded to s3://contracts/customers.yaml"
  fi

  # Verify
  LISTED=$(kubectl exec -n infra "$MINIO_POD" -- sh -c \
    "mc alias set local http://localhost:9000 minio minio123 --insecure >/dev/null 2>&1
     mc ls local/contracts/" 2>/dev/null | grep -c "customers.yaml" || echo "0")
  if [[ "${LISTED:-0}" -ge 1 ]]; then
    _pass "Contract verified in s3://contracts/"
  else
    _fail "Contract not visible after upload"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 7 — Activate CDC connector (debezium-public-customers)
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 7 — CDC connector (debezium-public-customers)"

# Ensure publication includes customers (idempotent)
_psql -c "DO \$\$
  BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'dbz_publication') THEN
      ALTER PUBLICATION dbz_publication ADD TABLE customers;
    ELSE
      CREATE PUBLICATION dbz_publication FOR TABLE customers;
    END IF;
  EXCEPTION WHEN duplicate_object THEN NULL;
  END \$\$;" &>/dev/null || true
_info "dbz_publication includes customers"

CONN_NAME="debezium-public-customers"
EXISTING=$(curl -sf "${KAFKA_CONNECT_URL}/connectors/${CONN_NAME}" 2>/dev/null \
  | jq -r '.name' 2>/dev/null || echo "")

if [[ "$EXISTING" == "$CONN_NAME" ]]; then
  _pass "Connector ${CONN_NAME} already exists"
else
  RESP_FILE=$(mktemp)
  HTTP_CODE=$(curl -s -o "$RESP_FILE" -w "%{http_code}" \
    -X POST "${KAFKA_CONNECT_URL}/connectors" \
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
    }' 2>/dev/null)

  if [[ "$HTTP_CODE" == "201" ]]; then
    _pass "Connector ${CONN_NAME} created"
  else
    ERR=$(jq -r '.message // .error_code // .' "$RESP_FILE" 2>/dev/null || cat "$RESP_FILE")
    _fail "Connector creation HTTP ${HTTP_CODE}: ${ERR}"
  fi
  rm -f "$RESP_FILE"
fi

if _poll "Connector RUNNING" 90 \
  "curl -sf '${KAFKA_CONNECT_URL}/connectors/debezium-public-customers/status' \
   | jq -e '.connector.state == \"RUNNING\"'"; then
  _pass "CDC connector is RUNNING — snapshot started"
else
  STATE=$(_connector_state "debezium-public-customers")
  _fail "Connector state: ${STATE} (expected RUNNING)"
  curl -sf "${KAFKA_CONNECT_URL}/connectors/debezium-public-customers/status" 2>/dev/null | jq . >&2 || true
fi

KAFKA_POD=$(_get_pod streaming "strimzi.io/name=kafka-cluster-kafka")
if [[ -n "$KAFKA_POD" ]]; then
  if _poll "Kafka topic cdc.public.customers" 60 \
    "kubectl exec -n streaming '${KAFKA_POD}' -- \
     bin/kafka-topics.sh \
     --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
     --list 2>/dev/null | grep -q 'cdc.public.customers'"; then
    _pass "Kafka topic cdc.public.customers exists"
  else
    _fail "Kafka topic not found — connector may not be working"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 8 — Verify Bronze layer
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 8 — Bronze layer (iceberg.bronze.customers_valid)"

# Debezium snapshot → Kafka → Spark streaming → Iceberg (allow 3 min)
if _poll "iceberg.bronze.customers_valid has rows" 180 \
  "kubectl exec -n serving deployment/trino-coordinator -- \
   trino --execute 'SELECT COUNT(*) FROM iceberg.bronze.customers_valid' \
   --output-format TSV 2>/dev/null | tail -1 | grep -vxE '0'"; then

  CNT=$(_trino_count "iceberg.bronze.customers_valid")
  _pass "Bronze customers_valid: ${CNT} row(s)"

  LATEST=$(kubectl exec -n serving deployment/trino-coordinator -- \
    trino --execute \
    "SELECT _cdc_op, COUNT(*) AS n FROM iceberg.bronze.customers_valid GROUP BY _cdc_op" \
    --output-format TSV 2>/dev/null | awk '{print $1"="$2}' | tr '\n' '  ' || echo "")
  _info "CDC ops: ${LATEST}"
else
  _fail "Bronze customers_valid still empty after 3 min"
  _info "Check bronze-streaming driver logs:"
  DRIVER=$(_get_pod processing "spark-role=driver")
  [[ -n "$DRIVER" ]] && kubectl logs -n processing "$DRIVER" --tail=30 >&2 || true
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 9 — Trigger Silver DAG
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 9 — Silver DAG (silver_processing_manual → customers)"

if ! _poll "Airflow healthy" 120 "_airflow_healthy"; then
  _fail "Airflow not healthy — skipping Silver trigger"
else
  # Unpause DAG if needed
  PAUSED=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
    "${AIRFLOW_URL}/api/v1/dags/silver_processing_manual" 2>/dev/null \
    | jq -r '.is_paused // "true"' 2>/dev/null || echo "true")
  if [[ "$PAUSED" == "true" ]]; then
    curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
      -X PATCH "${AIRFLOW_URL}/api/v1/dags/silver_processing_manual" \
      -H "Content-Type: application/json" \
      -d '{"is_paused": false}' &>/dev/null \
      && _pass "DAG unpaused" || _info "Could not unpause DAG (may already be active)"
  fi

  DAG_ID=$(curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
    "${AIRFLOW_URL}/api/v1/dags/silver_processing_manual" 2>/dev/null \
    | jq -r '.dag_id // empty' || echo "")

  if [[ "$DAG_ID" != "silver_processing_manual" ]]; then
    _fail "DAG silver_processing_manual not found — check git-sync in Airflow worker"
  else
    _pass "DAG silver_processing_manual found"

    RESP=$(curl -sf \
      -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
      -X POST "${AIRFLOW_URL}/api/v1/dags/silver_processing_manual/dagRuns" \
      -H "Content-Type: application/json" \
      -d '{"conf": {"table_name": "customers"}}' 2>/dev/null || echo "{}")
    RUN_ID=$(echo "$RESP" | jq -r '.dag_run_id // empty' 2>/dev/null || echo "")

    if [[ -z "$RUN_ID" ]]; then
      _fail "Could not trigger DAG: $(echo "$RESP" | jq -r '.detail // .title // .' 2>/dev/null)"
    else
      _pass "DAG triggered → run_id: ${RUN_ID}"

      if _poll "Silver SparkApplication created" 300 \
        "kubectl get sparkapplication -n processing -o name 2>/dev/null | grep -q 'silver'"; then

        SILVER_APP=$(kubectl get sparkapplication -n processing -o name 2>/dev/null \
          | grep silver | sort | tail -1)
        APP_NAME="${SILVER_APP#sparkapplication.sparkoperator.k8s.io/}"
        _info "SparkApplication: ${APP_NAME}"

        if _poll "${APP_NAME} COMPLETED/FAILED" 600 \
          "kubectl get sparkapplication '${APP_NAME}' -n processing \
           -o jsonpath='{.status.applicationState.state}' 2>/dev/null \
           | grep -qE 'COMPLETED|FAILED'"; then

          STATE=$(kubectl get sparkapplication "${APP_NAME}" -n processing \
            -o jsonpath='{.status.applicationState.state}' 2>/dev/null || echo "UNKNOWN")
          if [[ "$STATE" == "COMPLETED" ]]; then
            _pass "SparkApplication ${APP_NAME} → COMPLETED"
          else
            _fail "SparkApplication ${APP_NAME} → ${STATE}"
          fi
        else
          _fail "SparkApplication ${APP_NAME} did not finish in 10 min"
        fi
      else
        _fail "No Silver SparkApplication appeared — check Airflow task logs"
      fi
    fi
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 10 — Verify Silver layer
# ══════════════════════════════════════════════════════════════════════════════
_header "Step 10 — Silver layer (iceberg.silver.customers)"

if _poll "iceberg.silver.customers has rows" 120 \
  "kubectl exec -n serving deployment/trino-coordinator -- \
   trino --execute 'SELECT COUNT(*) FROM iceberg.silver.customers' \
   --output-format TSV 2>/dev/null | tail -1 | grep -vxE '0'"; then

  CNT=$(_trino_count "iceberg.silver.customers")
  _pass "Silver customers: ${CNT} row(s)"

  DUP=$(kubectl exec -n serving deployment/trino-coordinator -- \
    trino --execute \
    "SELECT COUNT(*) FROM (
       SELECT customer_id FROM iceberg.silver.customers
       GROUP BY customer_id HAVING COUNT(*) > 1
     )" \
    --output-format TSV 2>/dev/null | tail -1 | tr -d '[:space:]' || echo "0")
  if [[ "${DUP:-0}" == "0" ]]; then
    _pass "Silver customers: no duplicates (dedup OK)"
  else
    _fail "Silver customers: ${DUP} duplicate customer_id(s)"
  fi
else
  _fail "iceberg.silver.customers still empty"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Done
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}  Services are running:${RESET}"
echo -e "    ${YELLOW}Airflow${RESET}       http://localhost:8081  (admin / admin)"
echo -e "    ${YELLOW}MinIO${RESET}         http://localhost:9001  (minio / minio123)"
echo -e "    ${YELLOW}Trino${RESET}         http://localhost:8082"
echo -e "    ${YELLOW}Kafka Connect${RESET} http://localhost:8083"
echo ""
echo -e "  Port-forwards stay alive after script exits."
echo -e "  To tear down: ${YELLOW}TEARDOWN=true ./scripts/test-e2e.sh${RESET}"

_summary
