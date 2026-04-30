# curl + jq Patterns for REST APIs

> **Purpose**: Reliable HTTP interactions in Bash — capturing status codes, parsing JSON responses, and driving Airflow/Kafka Connect APIs from scripts
> **MCP Validated**: 2026-04-24

## When to Use

- Creating or checking Kafka Connect connectors via the Connect REST API
- Triggering and monitoring Airflow DAG runs via the Airflow REST API v1
- Health-checking services (Trino `/v1/info`, Airflow `/health`) before proceeding
- Extracting structured data from JSON API responses using `jq`

## Implementation

```bash
#!/usr/bin/env bash
set -euo pipefail

AIRFLOW_URL="${AIRFLOW_URL:-http://localhost:8081}"
AIRFLOW_USER="${AIRFLOW_USER:-admin}"
AIRFLOW_PASS="${AIRFLOW_PASS:-admin}"
KAFKA_CONNECT_URL="${KAFKA_CONNECT_URL:-http://localhost:8083}"
TRINO_URL="${TRINO_URL:-http://localhost:8082}"

# ── Pattern 1: Simple authenticated health check (exit 1 on non-2xx) ─────────
_airflow_healthy() {
  curl -sf -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
    "${AIRFLOW_URL}/health" \
    | jq -e '.metadatabase.status == "healthy"' &>/dev/null
}

# ── Pattern 2: Capture HTTP status AND body separately ───────────────────────
_curl_with_status() {
  local method="${1:-GET}" url="$2" body="${3:-}"
  local out_file
  out_file=$(mktemp)
  local http_code
  http_code=$(curl -s \
    -o "$out_file" \
    -w "%{http_code}" \
    -X "$method" \
    -H "Content-Type: application/json" \
    ${body:+-d "$body"} \
    "$url" 2>/dev/null)
  # Print body to stdout, status to descriptor 3 (caller's choice)
  cat "$out_file"
  rm -f "$out_file"
  return $(( http_code >= 400 ? 1 : 0 ))
}

# ── Pattern 3: Create Kafka Connect connector (idempotent) ────────────────────
_ensure_connector() {
  local table="$1"
  local conn_name="debezium-public-${table}"
  local slot="debezium_${table}"

  # Check if already exists
  local existing
  existing=$(curl -sf "${KAFKA_CONNECT_URL}/connectors/${conn_name}" 2>/dev/null \
    | jq -r '.name' 2>/dev/null || echo "")

  if [[ "$existing" == "$conn_name" ]]; then
    echo "  [PASS] Connector ${conn_name} already exists"
    return 0
  fi

  # Create — capture status code and body separately
  local resp_file
  resp_file=$(mktemp)
  local http_code
  http_code=$(curl -s \
    -o "$resp_file" \
    -w "%{http_code}" \
    -X POST "${KAFKA_CONNECT_URL}/connectors" \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"${conn_name}\",
      \"config\": {
        \"connector.class\": \"io.debezium.connector.postgresql.PostgresConnector\",
        \"database.hostname\": \"postgres.infra.svc.cluster.local\",
        \"database.port\": \"5432\",
        \"database.user\": \"postgres\",
        \"database.password\": \"postgres\",
        \"database.dbname\": \"sourcedb\",
        \"topic.prefix\": \"cdc\",
        \"table.include.list\": \"public.${table}\",
        \"plugin.name\": \"pgoutput\",
        \"slot.name\": \"${slot}\",
        \"heartbeat.interval.ms\": \"10000\"
      }
    }" 2>/dev/null)

  if [[ "$http_code" == "201" ]]; then
    echo "  [PASS] Connector ${conn_name} created"
  else
    local err_msg
    err_msg=$(jq -r '.message // .error_code // .' "$resp_file" 2>/dev/null || cat "$resp_file")
    echo "  [FAIL] Connector ${conn_name} HTTP ${http_code}: ${err_msg}" >&2
    rm -f "$resp_file"
    return 1
  fi
  rm -f "$resp_file"
}

# ── Pattern 4: Trigger Airflow DAG run and get run_id ────────────────────────
_trigger_dag() {
  local dag_id="$1" conf="${2:-{}}"
  local response
  response=$(curl -sf \
    -u "${AIRFLOW_USER}:${AIRFLOW_PASS}" \
    -X POST "${AIRFLOW_URL}/api/v1/dags/${dag_id}/dagRuns" \
    -H "Content-Type: application/json" \
    -d "{\"conf\": ${conf}}" 2>/dev/null)
  local run_id
  run_id=$(echo "$response" | jq -r '.dag_run_id // empty')
  if [[ -z "$run_id" ]]; then
    echo "[FAIL] Could not trigger ${dag_id}: $(echo "$response" | jq -r '.detail // .')" >&2
    return 1
  fi
  echo "$run_id"
}

# ── Pattern 5: Check Trino is ready (not starting) ───────────────────────────
_trino_ready() {
  local starting
  starting=$(curl -sf "${TRINO_URL}/v1/info" 2>/dev/null | jq -r '.starting' 2>/dev/null)
  [[ "$starting" == "false" ]]
}

# ── Usage ─────────────────────────────────────────────────────────────────────
if _airflow_healthy; then
  echo "Airflow is healthy"
  RUN_ID=$(_trigger_dag "silver_processing_manual" '{"table_name":"customers"}')
  echo "Triggered DAG run: $RUN_ID"
fi

_ensure_connector customers
_ensure_connector orders

if _trino_ready; then
  echo "Trino is accepting queries"
fi
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `AIRFLOW_URL` | `http://localhost:8081` | Airflow webserver base URL |
| `AIRFLOW_USER` | `admin` | Basic auth user |
| `AIRFLOW_PASS` | `admin` | Basic auth password |
| `KAFKA_CONNECT_URL` | `http://localhost:8083` | Kafka Connect REST API |
| `TRINO_URL` | `http://localhost:8082` | Trino coordinator |

## Example Usage

```bash
# List all Kafka Connect connectors
curl -sf "${KAFKA_CONNECT_URL}/connectors" | jq '.[]'

# Get last DAG run state
curl -sf -u admin:admin \
  "${AIRFLOW_URL}/api/v1/dags/silver_processing_manual/dagRuns?limit=1&order_by=-start_date" \
  | jq -r '.dag_runs[0].state'

# Check connector task states
curl -sf "${KAFKA_CONNECT_URL}/connectors/debezium-public-customers/status" \
  | jq '{connector: .connector.state, tasks: [.tasks[].state]}'
```

## See Also

- [patterns/polling-retry-loop.md](polling-retry-loop.md)
- [patterns/kubectl-scripting.md](kubectl-scripting.md)
- [concepts/variable-safety.md](../concepts/variable-safety.md)
