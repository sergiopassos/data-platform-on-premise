#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CLUSTER_NAME="data-platform"
ARGOCD_VERSION="7.3.11"
ARGOCD_NS="argocd"
SSH_KEY="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT="5001"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "=== Data Platform Bootstrap ==="

# ── 1. Local registry ─────────────────────────────────────────────────────────
if ! docker ps --filter "name=^/${REGISTRY_NAME}$" --format "{{.Names}}" | grep -q "${REGISTRY_NAME}"; then
  log "Step 1: Starting local Docker registry..."
  docker run -d -p "${REGISTRY_PORT}:5000" --name "${REGISTRY_NAME}" --restart=always registry:2
else
  log "Step 1: Local registry already running."
fi

# ── 2. KIND cluster ───────────────────────────────────────────────────────────
if kind get clusters | grep -q "${CLUSTER_NAME}"; then
  log "Step 2: Cluster '${CLUSTER_NAME}' already exists."
else
  log "Step 2: Creating KIND cluster..."
  kind create cluster --config cluster/kind-config.yaml --name "$CLUSTER_NAME"
fi

docker network connect kind "${REGISTRY_NAME}" 2>/dev/null || true

REGISTRY_IP=$(docker inspect "${REGISTRY_NAME}" --format '{{.NetworkSettings.Networks.kind.IPAddress}}')
for node in $(kind get nodes --name "$CLUSTER_NAME"); do
  docker exec "$node" sh -c "
    mkdir -p /etc/containerd/certs.d/${REGISTRY_IP}:5000
    cat > /etc/containerd/certs.d/${REGISTRY_IP}:5000/hosts.toml << 'EOF'
[host.\"http://${REGISTRY_IP}:5000\"]
  capabilities = [\"pull\", \"resolve\", \"push\"]
  skip_verify = true
EOF
  " 2>/dev/null
done
log "  Containerd configured for local registry at ${REGISTRY_IP}:5000"

kubectl config use-context "kind-${CLUSTER_NAME}"

# ── 3. Helm repos ─────────────────────────────────────────────────────────────
log "Step 3: Adding Helm repos..."
helm repo add argo           https://argoproj.github.io/argo-helm       2>/dev/null || true
helm repo add minio          https://charts.min.io/                     2>/dev/null || true
helm repo add strimzi        https://strimzi.io/charts/                 2>/dev/null || true
helm repo add spark-operator https://kubeflow.github.io/spark-operator  2>/dev/null || true
helm repo add trinodb        https://trinodb.github.io/charts           2>/dev/null || true
helm repo add projectnessie  https://charts.projectnessie.org           2>/dev/null || true
helm repo update

# ── 4. Namespaces ─────────────────────────────────────────────────────────────
log "Step 4: Creating namespaces..."
kubectl apply -f manifests/namespaces.yaml

# ── 5. Spark CRDs (too large for ArgoCD client-side apply) ───────────────────
log "Step 5: Pre-installing Spark Operator CRDs via server-side apply..."
helm template spark-crds spark-operator/spark-operator --version 2.1.0 --include-crds \
  | kubectl apply --server-side --force-conflicts -f - 2>/dev/null || true

# ── 6. ArgoCD ─────────────────────────────────────────────────────────────────
log "Step 6: Installing ArgoCD..."
helm upgrade --install argocd argo/argo-cd \
  --namespace "$ARGOCD_NS" \
  --create-namespace \
  --version "$ARGOCD_VERSION" \
  --set "server.extraArgs[0]=--insecure" \
  --wait --timeout 5m

kubectl wait --for=condition=Available deployment/argocd-server \
  -n "$ARGOCD_NS" --timeout=120s

# ── 7. SSH repo secret for ArgoCD ─────────────────────────────────────────────
if [ ! -f "$SSH_KEY" ]; then
  log "ERROR: SSH private key not found at $SSH_KEY"
  log "Set SSH_KEY_PATH env var or ensure ~/.ssh/id_ed25519 exists."
  exit 1
fi

log "Step 7: Creating ArgoCD SSH repo secret..."
kubectl create secret generic repo-data-platform \
  -n "$ARGOCD_NS" \
  --from-literal=type=git \
  --from-literal=url=git@github.com:sergiopassos/data-platform-on-premise.git \
  --from-file=sshPrivateKey="$SSH_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl label secret repo-data-platform -n "$ARGOCD_NS" \
  "argocd.argoproj.io/secret-type=repository" --overwrite

# ── 8. Application secrets ────────────────────────────────────────────────────
log "Step 8: Creating application secrets..."

kubectl create secret generic postgres-source-secret \
  -n portal \
  --from-literal=host="${POSTGRES_HOST:-postgres.infra.svc.cluster.local}" \
  --from-literal=port="${POSTGRES_PORT:-5432}" \
  --from-literal=dbname="${POSTGRES_DB:-sourcedb}" \
  --from-literal=user="${POSTGRES_USER:-postgres}" \
  --from-literal=password="${POSTGRES_PASSWORD:-postgres}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic airflow-git-ssh \
  -n orchestration \
  --from-file=gitSshKey="$SSH_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic airflow-fernet-key \
  -n orchestration \
  --from-literal=fernet-key="${AIRFLOW_FERNET_KEY:-data-platform-local-fernet-key-replace-in-prod==}" \
  --dry-run=client -o yaml | kubectl apply -f -

# ── 9. Chainlit portal image ───────────────────────────────────────────────────
log "Step 9: Building and loading Chainlit portal image..."
docker build -t data-platform/chainlit-portal:latest portal/
kind load docker-image data-platform/chainlit-portal:latest --name "$CLUSTER_NAME"

# ── 10. Root App of Apps ──────────────────────────────────────────────────────
log "Step 10: Applying root App of Apps..."
kubectl apply -f gitops/bootstrap/root-app.yaml

# ── 11. MinIO buckets ─────────────────────────────────────────────────────────
# Nessie health-checks the S3 buckets at startup; create them before Nessie
# probes run or it will fail ReadinessProbe and never become Ready.
log "Step 11: Waiting for MinIO to be ready and creating buckets..."
until kubectl get pod -l "app=minio" -n infra --no-headers 2>/dev/null | grep -q .; do
  sleep 5
done
kubectl wait pod -l "app=minio" -n infra --for=condition=Ready --timeout=180s
MINIO_POD=$(kubectl get pod -n infra -l "app=minio" -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n infra "$MINIO_POD" -- sh -c "
  mc alias set local http://localhost:9000 minio minio123 --insecure 2>/dev/null
  mc mb --ignore-existing local/warehouse
  mc mb --ignore-existing local/bronze
"
log "  Buckets warehouse and bronze created."

# ── 12. Airflow DB migration ──────────────────────────────────────────────────
# ArgoCD does not execute Helm pre-install hook Jobs, so we run it manually.
# First wait for ArgoCD to deploy the Airflow release (PostgreSQL pod appears).
log "Step 12: Waiting for Airflow PostgreSQL pod to appear (ArgoCD sync)..."
until kubectl get pod -l "app.kubernetes.io/name=postgresql,app.kubernetes.io/instance=airflow" \
    -n orchestration --no-headers 2>/dev/null | grep -q .; do
  sleep 5
done
log "  PostgreSQL pod found. Waiting for it to be Ready..."
kubectl wait pod \
  -l "app.kubernetes.io/name=postgresql,app.kubernetes.io/instance=airflow" \
  -n orchestration \
  --for=condition=Ready \
  --timeout=300s

log "  Running airflow db migrate..."
kubectl run airflow-db-migrate \
  --image=apache/airflow:2.9.3-python3.11 \
  --restart=Never \
  --env="AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql://postgres:postgres@airflow-postgresql.orchestration:5432/postgres?sslmode=disable" \
  --env="AIRFLOW__CORE__FERNET_KEY=${AIRFLOW_FERNET_KEY:-data-platform-local-fernet-key-replace-in-prod==}" \
  --env="AIRFLOW__CORE__LOAD_EXAMPLES=false" \
  -n orchestration \
  -- airflow db migrate
kubectl wait pod/airflow-db-migrate -n orchestration \
  --for=jsonpath='{.status.phase}'=Succeeded --timeout=300s
MIGRATE_PHASE=$(kubectl get pod airflow-db-migrate -n orchestration -o jsonpath='{.status.phase}')
if [ "$MIGRATE_PHASE" != "Succeeded" ]; then
  log "ERROR: airflow db migrate failed (phase=$MIGRATE_PHASE). Logs:"
  kubectl logs airflow-db-migrate -n orchestration
  exit 1
fi
kubectl delete pod airflow-db-migrate -n orchestration

# Restart Airflow pods so they pick up the migrated DB immediately
# (they may have already started and failed the check-migrations init before migration completed)
log "  Restarting Airflow components to pick up migrated DB..."
kubectl rollout restart deployment/airflow-scheduler deployment/airflow-webserver -n orchestration
kubectl rollout restart statefulset/airflow-triggerer -n orchestration
kubectl rollout status deployment/airflow-scheduler deployment/airflow-webserver \
  -n orchestration --timeout=600s
kubectl wait pod -l "component=triggerer,release=airflow" \
  -n orchestration --for=condition=Ready --timeout=300s

# ── 13. Airflow admin user ────────────────────────────────────────────────────
# ArgoCD may also run the create-user hook Job. We create the user here as a
# fallback with retry, since the webserver can be briefly killed under memory
# pressure right after rollout completes (exit 137 = SIGKILL).
log "Step 13: Creating Airflow admin user..."
USER_CREATED=false
for attempt in 1 2 3 4 5; do
  WEBSERVER_POD=$(kubectl get pod -l "component=webserver,release=airflow" \
    -n orchestration -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [ -z "$WEBSERVER_POD" ]; then
    log "  Webserver pod not found, retrying ($attempt/5)..."; sleep 10; continue
  fi
  # Check if user already created (e.g. by ArgoCD hook)
  EXISTING=$(kubectl exec -n orchestration "$WEBSERVER_POD" -- \
    airflow users list 2>/dev/null | grep "^1 " || true)
  if [ -n "$EXISTING" ]; then
    log "  Admin user already exists (created by ArgoCD hook)."; USER_CREATED=true; break
  fi
  if kubectl exec -n orchestration "$WEBSERVER_POD" -- airflow users create \
      --username admin --firstname Admin --lastname User \
      --role Admin --email admin@example.com --password admin 2>&1 | \
      grep -qE "created|already exist"; then
    log "  Admin user created."; USER_CREATED=true; break
  fi
  log "  Attempt $attempt/5 failed, retrying in 15s..."; sleep 15
done
if [ "$USER_CREATED" != "true" ]; then
  log "WARNING: Could not create admin user after 5 attempts."
  log "  Run manually: kubectl exec -n orchestration deploy/airflow-webserver -- airflow users create --username admin --password admin --role Admin --firstname Admin --lastname User --email admin@example.com"
fi

log ""
log "=== Bootstrap Complete ==="
log "ArgoCD is reconciling all platform components."
log ""
log "Monitor:  kubectl -n argocd get apps -w"
log ""
log "Access points:"
log "  ArgoCD:       kubectl port-forward svc/argocd-server -n argocd 8090:80"
log "                http://localhost:8090  (admin / see password below)"
log "  Airflow:      kubectl port-forward svc/airflow-webserver -n orchestration 8081:8080"
log "                http://localhost:8081  (admin / admin)"
log "  Trino:        kubectl port-forward svc/trino -n serving 8082:8080"
log "  MinIO:        kubectl port-forward svc/minio -n infra 9001:9001"
log "  Chainlit:     kubectl port-forward svc/chainlit -n portal 8000:8000"
log "  OpenMetadata: kubectl port-forward svc/openmetadata -n governance 8585:8585"
log ""
log "ArgoCD admin password:"
log "  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
