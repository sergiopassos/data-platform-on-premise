# kubectl Scripting Patterns

> **Purpose**: Reliable pod lookup, exec, wait, rollout, and secret management patterns for data-platform automation scripts
> **MCP Validated**: 2026-04-24

## When to Use

- Bootstrap scripts that must wait for pods before executing work (MinIO bucket creation, DB migrations)
- Smoke-test scripts that verify pod states and execute commands inside pods
- Idempotent secret management with `--dry-run=client -o yaml | kubectl apply -f -`
- Rollout restarts after configuration changes (Airflow restart post-migration)

## Implementation

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── Pod name lookup by label ──────────────────────────────────────────────────
_get_pod() {
  local ns="$1" label="$2"
  kubectl get pod -n "$ns" -l "$label" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
}

# ── Wait for pod to exist, then for Ready condition ───────────────────────────
_require_pod_ready() {
  local ns="$1" label="$2" timeout="${3:-300}"
  until kubectl get pod -l "$label" -n "$ns" --no-headers 2>/dev/null | grep -q .; do
    sleep 5
  done
  kubectl wait pod -l "$label" -n "$ns" \
    --for=condition=Ready --timeout="${timeout}s"
}

# ── Idempotent secret creation ────────────────────────────────────────────────
# The --dry-run=client -o yaml | apply pattern is idempotent:
# it creates the secret if absent, updates if present, no error if identical.
_upsert_secret() {
  kubectl create secret generic "$@" \
    --dry-run=client -o yaml | kubectl apply -f -
}

# ── Execute SQL in the postgres pod ──────────────────────────────────────────
_psql() {
  local pod
  pod=$(_get_pod infra "app=postgres")
  [[ -z "$pod" ]] && { echo "[ERROR] No postgres pod" >&2; return 1; }
  kubectl exec -n infra "$pod" -- psql -U postgres -d sourcedb "$@"
}

# ── Capture jsonpath field safely ─────────────────────────────────────────────
_kget() {
  # Usage: _kget NAMESPACE TYPE NAME JSONPATH
  kubectl get "$2" "$3" -n "$1" -o jsonpath="$4" 2>/dev/null || echo ""
}

# ── Rollout restart + wait ────────────────────────────────────────────────────
_rollout_restart() {
  local ns="$1"; shift
  kubectl rollout restart "$@" -n "$ns"
  kubectl rollout status "$@" -n "$ns" --timeout=600s
}

# ── Concrete usage in bootstrap-cluster.sh style ──────────────────────────────

# Idempotent secrets
_upsert_secret postgres-source-secret \
  -n portal \
  --from-literal=host="${POSTGRES_HOST:-postgres.infra.svc.cluster.local}" \
  --from-literal=port="5432" \
  --from-literal=dbname="sourcedb" \
  --from-literal=user="postgres" \
  --from-literal=password="${POSTGRES_PASSWORD:-postgres}"

_upsert_secret airflow-git-ssh \
  -n orchestration \
  --from-file=gitSshKey="${SSH_KEY:-$HOME/.ssh/id_ed25519}"

# Label a secret for ArgoCD repo discovery
kubectl label secret repo-data-platform -n argocd \
  "argocd.argoproj.io/secret-type=repository" --overwrite

# Wait for MinIO and create buckets via exec
_require_pod_ready infra "app=minio" 180
MINIO_POD=$(_get_pod infra "app=minio")
kubectl exec -n infra "$MINIO_POD" -- sh -c "
  mc alias set local http://localhost:9000 minio minio123 --insecure 2>/dev/null
  mc mb --ignore-existing local/warehouse
  mc mb --ignore-existing local/bronze
  mc mb --ignore-existing local/contracts
"

# Run a one-shot migration job, wait for completion, then delete
kubectl run airflow-db-migrate \
  --image=apache/airflow:2.9.3-python3.11 \
  --restart=Never \
  --env="AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql://postgres:postgres@airflow-postgresql.orchestration:5432/postgres?sslmode=disable" \
  -n orchestration \
  -- airflow db migrate

kubectl wait pod/airflow-db-migrate -n orchestration \
  --for=jsonpath='{.status.phase}'=Succeeded --timeout=300s

MIGRATE_PHASE=$(_kget orchestration pod airflow-db-migrate '{.status.phase}')
if [[ "$MIGRATE_PHASE" != "Succeeded" ]]; then
  echo "[ERROR] Migration failed (phase=$MIGRATE_PHASE)" >&2
  kubectl logs airflow-db-migrate -n orchestration >&2
  exit 1
fi
kubectl delete pod airflow-db-migrate -n orchestration

# Rollout restart Airflow after migration
_rollout_restart orchestration \
  deployment/airflow-scheduler \
  deployment/airflow-webserver

# Check ArgoCD app health across all apps
UNHEALTHY=$(kubectl get applications -n argocd -o json 2>/dev/null \
  | jq -r '.items[] | select(.status.health.status != "Healthy") | .metadata.name' \
  | tr '\n' ' ')
[[ -z "$UNHEALTHY" ]] || echo "[WARN] Unhealthy ArgoCD apps: $UNHEALTHY"
```

## Configuration

| Pattern | Default namespace | Notes |
|---------|------------------|-------|
| Postgres pod | `infra` | Label: `app=postgres` |
| MinIO pod | `infra` | Label: `app=minio` |
| Kafka pod | `streaming` | Label: `strimzi.io/name=kafka-cluster-kafka` |
| Airflow webserver | `orchestration` | Label: `component=webserver,release=airflow` |
| Airflow PostgreSQL | `orchestration` | Label: `app.kubernetes.io/name=postgresql,app.kubernetes.io/instance=airflow` |

## Example Usage

```bash
# Verify Kafka topics after CDC connector creation
KAFKA_POD=$(_get_pod streaming "strimzi.io/name=kafka-cluster-kafka")
kubectl exec -n streaming "$KAFKA_POD" -- \
  bin/kafka-topics.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --list | grep "^cdc\."

# Exec into Trino coordinator for ad-hoc queries
kubectl exec -it deployment/trino-coordinator -n serving -- \
  trino --execute "SELECT COUNT(*) FROM iceberg.bronze.customers_valid"
```

## See Also

- [patterns/polling-retry-loop.md](polling-retry-loop.md)
- [patterns/curl-jq-rest.md](curl-jq-rest.md)
- [concepts/functions-and-return-codes.md](../concepts/functions-and-return-codes.md)
