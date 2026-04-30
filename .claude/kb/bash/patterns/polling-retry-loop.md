# Polling and Retry Loops with Timeouts

> **Purpose**: Wait for asynchronous resources (pods, connectors, Spark jobs) without blocking forever, using `date +%s` for portable deadline tracking
> **MCP Validated**: 2026-04-24

## When to Use

- Waiting for a Kubernetes pod to appear before calling `kubectl wait`
- Polling a REST endpoint (Kafka Connect, Airflow) until it returns the expected state
- Retrying a flaky command (kubectl exec into a pod that may still be starting) up to N times
- Waiting for a SparkApplication to reach RUNNING or COMPLETED state

## Implementation

```bash
#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; RESET='\033[0m'

# ── _poll: deadline-based polling with dot progress ───────────────────────────
# Usage: _poll "label" TIMEOUT_SECONDS command [args...]
# The command is eval'd — quote carefully.
# Returns 0 on success, 1 on timeout.
_poll() {
  local label="$1"
  local timeout="${2:-120}"
  local cmd="${*:3}"
  local deadline=$(( $(date +%s) + timeout ))

  echo -ne "  ${BLUE}[WAIT]${RESET} ${label}..."
  while (( $(date +%s) < deadline )); do
    if eval "$cmd" &>/dev/null; then
      echo -e " ${GREEN}OK${RESET}"
      return 0
    fi
    echo -n "."
    sleep 5
  done
  echo -e " ${RED}TIMEOUT${RESET}"
  return 1
}

# ── Wait for pod to appear, then use kubectl wait for readiness ───────────────
_wait_pod_ready() {
  local namespace="$1" label="$2" timeout="${3:-300}"

  # Phase 1 — wait for the pod object to exist
  _poll "Pod ${label} appearing in ${namespace}" "$timeout" \
    "kubectl get pod -l '${label}' -n '${namespace}' --no-headers 2>/dev/null | grep -q ."

  # Phase 2 — wait for Ready condition (kubectl wait is exact, no polling needed)
  kubectl wait pod -l "$label" -n "$namespace" \
    --for=condition=Ready --timeout="${timeout}s"
}

# ── Retry N times with exponential-ish backoff ────────────────────────────────
_retry() {
  local attempts="${1:-5}" delay="${2:-10}" label="$3"; shift 3
  local attempt
  for attempt in $(seq 1 "$attempts"); do
    if "$@"; then
      return 0
    fi
    echo "  [RETRY] $label — attempt $attempt/$attempts failed, waiting ${delay}s..." >&2
    sleep "$delay"
  done
  echo "  [FAIL] $label — all $attempts attempts exhausted" >&2
  return 1
}

# ── Concrete usage examples ───────────────────────────────────────────────────

# Wait for MinIO pod to appear before running mc commands
_wait_pod_ready infra "app=minio" 180

# Wait for Kafka Connect connector to reach RUNNING state
KAFKA_CONNECT_URL="${KAFKA_CONNECT_URL:-http://localhost:8083}"
CONN_NAME="debezium-public-customers"
_poll "Connector ${CONN_NAME} RUNNING" 60 \
  "curl -sf '${KAFKA_CONNECT_URL}/connectors/${CONN_NAME}/status' \
   | jq -e '.connector.state == \"RUNNING\"'"

# Wait for Airflow PostgreSQL to be ready (appears asynchronously via ArgoCD)
_poll "Airflow PostgreSQL pod appearing" 300 \
  "kubectl get pod \
     -l 'app.kubernetes.io/name=postgresql,app.kubernetes.io/instance=airflow' \
     -n orchestration --no-headers 2>/dev/null | grep -q ."

kubectl wait pod \
  -l "app.kubernetes.io/name=postgresql,app.kubernetes.io/instance=airflow" \
  -n orchestration --for=condition=Ready --timeout=300s

# Retry Airflow user creation (webserver may restart under memory pressure)
_retry 5 15 "Airflow admin user creation" \
  kubectl exec -n orchestration deploy/airflow-webserver -- \
    airflow users create \
      --username admin --firstname Admin --lastname User \
      --role Admin --email admin@example.com --password admin

# Wait for SparkApplication to complete or fail
_poll "bronze-streaming RUNNING" 180 \
  "kubectl get sparkapplication bronze-streaming -n processing \
   -o jsonpath='{.status.applicationState.state}' 2>/dev/null \
   | grep -q 'RUNNING'"
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `timeout` (poll) | `120` | Seconds before giving up |
| `sleep` interval | `5` | Seconds between probes |
| `attempts` (retry) | `5` | Maximum retry count |
| `delay` (retry) | `10` | Seconds between retries |

## Example Usage

```bash
# One-liner: wait for ArgoCD server to be available
_poll "ArgoCD server ready" 300 \
  "kubectl get deployment argocd-server -n argocd \
   -o jsonpath='{.status.availableReplicas}' 2>/dev/null | grep -qE '^[1-9]'"

# After polling, immediately use kubectl wait for the exact condition
kubectl wait --for=condition=Available deployment/argocd-server \
  -n argocd --timeout=120s
```

## See Also

- [concepts/error-handling.md](../concepts/error-handling.md)
- [patterns/kubectl-scripting.md](kubectl-scripting.md)
- [patterns/curl-jq-rest.md](curl-jq-rest.md)
