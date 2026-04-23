#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="data-platform"
ARGOCD_VERSION="7.3.11"
ARGOCD_NS="argocd"
SSH_KEY="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT="5001"

log() { echo "[bootstrap] $*"; }

# ── 1. Local registry ──────────────────────────────────────────────────────────
if ! docker ps --filter "name=^/${REGISTRY_NAME}$" --format "{{.Names}}" | grep -q "${REGISTRY_NAME}"; then
  log "Starting local Docker registry..."
  docker run -d -p "${REGISTRY_PORT}:5000" --name "${REGISTRY_NAME}" --restart=always registry:2
else
  log "Local registry already running."
fi

# ── 2. KIND cluster ───────────────────────────────────────────────────────────
log "Creating KIND cluster..."
kind create cluster --config cluster/kind-config.yaml --name "$CLUSTER_NAME" || {
  log "Cluster already exists, skipping creation."
}

# Connect registry to KIND network (idempotent)
docker network connect kind "${REGISTRY_NAME}" 2>/dev/null || true

# Configure containerd on all nodes to trust the local registry
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
log "Containerd configured on all nodes for local registry at ${REGISTRY_IP}:5000"

kubectl config use-context "kind-${CLUSTER_NAME}"

# ── 3. Helm repos ─────────────────────────────────────────────────────────────
log "Adding Helm repos..."
helm repo add argo https://argoproj.github.io/argo-helm
helm repo add minio https://charts.min.io/
helm repo add strimzi https://strimzi.io/charts/
helm repo add spark-operator https://kubeflow.github.io/spark-operator
helm repo add trinodb https://trinodb.github.io/charts
helm repo add projectnessie https://charts.projectnessie.org
helm repo update

# ── 4. Namespaces ─────────────────────────────────────────────────────────────
log "Creating namespaces..."
kubectl apply -f manifests/namespaces.yaml

# ── 5. Spark CRDs (too large for ArgoCD client-side apply) ────────────────────
log "Pre-installing Spark Operator CRDs via server-side apply..."
helm template spark-crds spark-operator/spark-operator --version 2.1.0 --include-crds \
  | kubectl apply --server-side --force-conflicts -f - 2>/dev/null || true

# ── 6. ArgoCD ─────────────────────────────────────────────────────────────────
log "Installing ArgoCD..."
helm upgrade --install argocd argo/argo-cd \
  --namespace "$ARGOCD_NS" \
  --create-namespace \
  --version "$ARGOCD_VERSION" \
  --set "server.extraArgs[0]=--insecure" \
  --wait --timeout 5m

log "Waiting for ArgoCD server..."
kubectl wait --for=condition=Available deployment/argocd-server \
  -n "$ARGOCD_NS" --timeout=120s

# ── 7. SSH repo secret ────────────────────────────────────────────────────────
if [ ! -f "$SSH_KEY" ]; then
  log "ERROR: SSH private key not found at $SSH_KEY"
  log "Set SSH_KEY_PATH env var or ensure ~/.ssh/id_ed25519 exists."
  exit 1
fi

log "Creating ArgoCD SSH repo secret..."
kubectl create secret generic repo-data-platform \
  -n "$ARGOCD_NS" \
  --from-literal=type=git \
  --from-literal=url=git@github.com:sergiopassos/data-platform-on-premise.git \
  --from-file=sshPrivateKey="$SSH_KEY" \
  --dry-run=client -o yaml \
  | kubectl apply -f -
kubectl label secret repo-data-platform -n "$ARGOCD_NS" \
  "argocd.argoproj.io/secret-type=repository" --overwrite

# ── 8. postgres-source-secret for Chainlit portal ────────────────────────────
log "Creating postgres-source-secret in portal namespace..."
kubectl create namespace portal 2>/dev/null || true
kubectl create secret generic postgres-source-secret \
  -n portal \
  --from-literal=host="${POSTGRES_HOST:-postgres.infra.svc.cluster.local}" \
  --from-literal=port="${POSTGRES_PORT:-5432}" \
  --from-literal=dbname="${POSTGRES_DB:-sourcedb}" \
  --from-literal=user="${POSTGRES_USER:-postgres}" \
  --from-literal=password="${POSTGRES_PASSWORD:-postgres}" \
  --dry-run=client -o yaml \
  | kubectl apply -f -

# ── 9. Airflow secrets + DB migration ────────────────────────────────────────
log "Creating Airflow secrets in orchestration namespace..."
kubectl create secret generic airflow-git-ssh \
  -n orchestration \
  --from-file=gitSshKey="$SSH_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

# Chart mounts this secret even when the key is passed via env:
kubectl create secret generic airflow-fernet-key \
  -n orchestration \
  --from-literal=fernet-key="${AIRFLOW_FERNET_KEY:-data-platform-local-fernet-key-replace-in-prod==}" \
  --dry-run=client -o yaml | kubectl apply -f -

# ── 10. Chainlit portal image (local build) ───────────────────────────────────
log "Building and loading chainlit-portal image into KIND..."
docker build -t data-platform/chainlit-portal:latest portal/
kind load docker-image data-platform/chainlit-portal:latest --name "$CLUSTER_NAME"

# ── 11. Root App ──────────────────────────────────────────────────────────────
log "Applying root app (App of Apps)..."
kubectl apply -f gitops/bootstrap/root-app.yaml

# ── 12. Airflow DB migration ──────────────────────────────────────────────────
# ArgoCD does not execute Helm pre-install hook Jobs, so we run the migration
# manually after waiting for the Airflow PostgreSQL pod to be ready.
log "Waiting for Airflow PostgreSQL to be ready..."
kubectl wait pod \
  -l "app.kubernetes.io/name=postgresql,app.kubernetes.io/instance=airflow" \
  -n orchestration \
  --for=condition=Ready \
  --timeout=300s

log "Running Airflow DB migration..."
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

log "Restarting Airflow components to pick up migrated DB..."
kubectl rollout restart deployment/airflow-scheduler deployment/airflow-webserver -n orchestration
kubectl rollout restart statefulset/airflow-triggerer -n orchestration

# ArgoCD skips the create-user Helm hook job, so create the user manually.
log "Creating Airflow admin user..."
kubectl rollout status deployment/airflow-webserver -n orchestration --timeout=600s
WEBSERVER_POD=$(kubectl get pod -l "component=webserver,release=airflow" \
  -n orchestration -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n orchestration "$WEBSERVER_POD" -- airflow users create \
  --username admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@example.com \
  --password admin

log ""
log "Bootstrap complete. ArgoCD will now reconcile all platform components."
log ""
log "  Monitor:  kubectl -n argocd get apps -w"
log "  UI:       kubectl port-forward svc/argocd-server -n argocd 8090:80"
log "            http://localhost:8090  (admin / see below)"
log "  Password: kubectl -n argocd get secret argocd-initial-admin-secret \\"
log "              -o jsonpath='{.data.password}' | base64 -d"
log ""
log "  Airflow:  kubectl port-forward svc/airflow-webserver -n orchestration 8081:8080"
log "            http://localhost:8081  (admin / admin)"
