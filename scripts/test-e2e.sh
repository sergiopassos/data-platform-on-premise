#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

KEEP_CLUSTER="${KEEP_CLUSTER:-false}"

log() { echo "[e2e] $*"; }
fail() { log "FAIL: $*"; exit 1; }

log "=== E2E Test Suite ==="

log "Step 1: Bootstrap cluster (idempotent)..."
./scripts/bootstrap-cluster.sh

log "Step 2: Waiting for ArgoCD sync (all apps Healthy + Synced)..."
for app in minio nessie strimzi-operator kafka-cluster spark-operator airflow trino openmetadata ollama chainlit; do
  log "  Waiting for app: $app"
  timeout 600 bash -c "
    until argocd app get $app --grpc-web 2>/dev/null | grep -q 'Health Status.*Healthy'; do
      sleep 15
    done
  " || log "WARNING: $app may not be fully healthy yet"
done

log "Step 3: Running integration tests..."
python -m pytest tests/integration/ -v --timeout=300 2>&1 | tee /tmp/e2e-results.txt
PYTEST_EXIT=${PIPESTATUS[0]}

if [ "$PYTEST_EXIT" -eq 0 ]; then
  log "=== E2E PASSED ==="
else
  log "=== E2E FAILED (exit $PYTEST_EXIT) ==="
  cat /tmp/e2e-results.txt
fi

if [ "$KEEP_CLUSTER" != "true" ]; then
  log "Tearing down KIND cluster..."
  kind delete cluster --name data-platform || true
fi

exit $PYTEST_EXIT
