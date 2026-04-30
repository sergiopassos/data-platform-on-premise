# Pattern: Idempotent Bootstrap Script (KIND)

## Problem

Local KIND clusters are frequently destroyed and recreated (testing, resource cleanup, fresh start). Every recreate loses: CRDs, loaded images, secrets, ArgoCD state. A manual step-by-step process is error-prone and slow.

## Solution: Idempotent bootstrap.sh

One script handles the full cluster bootstrap, safe to re-run at any point. Each step checks whether the work is already done before doing it.

## Idempotency Techniques

| Resource | Idempotency Technique |
|----------|----------------------|
| Docker registry | Check `docker ps` before `docker run` |
| KIND cluster | `kind create || true` |
| Network connect | `docker network connect || true` |
| Helm repos | `helm repo add` is idempotent (overwrites) |
| K8s namespaces | `kubectl apply -f namespaces.yaml` |
| Spark CRDs | `kubectl apply --server-side --force-conflicts` |
| ArgoCD install | `helm upgrade --install` |
| K8s secrets | `--dry-run=client -o yaml \| kubectl apply -f -` |
| ArgoCD label | `kubectl label --overwrite` |
| kind load | Always safe to re-run |
| root-app | `kubectl apply -f` is idempotent |

## Full bootstrap.sh Structure

```bash
#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="data-platform"
ARGOCD_VERSION="7.3.11"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT="5001"
SSH_KEY="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"

log() { echo "[bootstrap] $*"; }

# ── 1. Local registry ──────────────────────────────────────────────────
if ! docker ps --filter "name=^/${REGISTRY_NAME}$" --format "{{.Names}}" | grep -q "${REGISTRY_NAME}"; then
  docker run -d -p "${REGISTRY_PORT}:5000" --name "${REGISTRY_NAME}" --restart=always registry:2
fi
docker network connect kind "${REGISTRY_NAME}" 2>/dev/null || true

# Configure containerd trust (must re-run on each cluster recreate)
REGISTRY_IP=$(docker inspect "${REGISTRY_NAME}" \
  --format '{{.NetworkSettings.Networks.kind.IPAddress}}')
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

# ── 2. KIND cluster ────────────────────────────────────────────────────
kind create cluster --config cluster/kind-config.yaml --name "$CLUSTER_NAME" || true
kubectl config use-context "kind-${CLUSTER_NAME}"

# ── 3. Helm repos ─────────────────────────────────────────────────────
helm repo add argo       https://argoproj.github.io/argo-helm
helm repo add strimzi    https://strimzi.io/charts/
helm repo add spark-operator https://kubeflow.github.io/spark-operator
helm repo add trinodb    https://trinodb.github.io/charts
# ... add all repos ...
helm repo update

# ── 4. Namespaces ─────────────────────────────────────────────────────
kubectl apply -f manifests/namespaces.yaml

# ── 5. Spark CRDs (server-side, bypasses 262KB annotation limit) ──────
helm template spark-crds spark-operator/spark-operator --version 2.1.0 --include-crds \
  | kubectl apply --server-side --force-conflicts -f - 2>/dev/null || true

# ── 6. ArgoCD ─────────────────────────────────────────────────────────
helm upgrade --install argocd argo/argo-cd \
  --namespace argocd --create-namespace \
  --version "$ARGOCD_VERSION" \
  --set "server.extraArgs[0]=--insecure" \
  --wait --timeout 5m

kubectl wait --for=condition=Available deployment/argocd-server \
  -n argocd --timeout=120s

# ── 7. SSH repo secret ────────────────────────────────────────────────
[ ! -f "$SSH_KEY" ] && { log "ERROR: SSH key not found at $SSH_KEY"; exit 1; }
kubectl create secret generic repo-data-platform \
  -n argocd \
  --from-literal=type=git \
  --from-literal=url=git@github.com:org/data-platform.git \
  --from-file=sshPrivateKey="$SSH_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl label secret repo-data-platform -n argocd \
  "argocd.argoproj.io/secret-type=repository" --overwrite

# ── 8. Application secrets (re-create on every cluster boot) ──────────
kubectl create namespace portal 2>/dev/null || true
kubectl create secret generic postgres-source-secret \
  -n portal \
  --from-literal=host="${POSTGRES_HOST:-postgres.infra.svc.cluster.local}" \
  --from-literal=port="${POSTGRES_PORT:-5432}" \
  --from-literal=dbname="${POSTGRES_DB:-sourcedb}" \
  --from-literal=user="${POSTGRES_USER:-postgres}" \
  --from-literal=password="${POSTGRES_PASSWORD:-postgres}" \
  --dry-run=client -o yaml | kubectl apply -f -

# ── 9. Custom images (lost on cluster recreate) ────────────────────────
docker build -t data-platform/chainlit-portal:latest portal/
kind load docker-image data-platform/chainlit-portal:latest --name "$CLUSTER_NAME"

# ── 10. ArgoCD root app ────────────────────────────────────────────────
kubectl apply -f gitops/bootstrap/root-app.yaml

log "Bootstrap complete. ArgoCD reconciling all components."
```

## Key Principles

1. **Check before act**: `docker ps | grep || docker run` not just `docker run`
2. **Use `|| true`** for operations that fail-if-already-done (network connect, cluster create)
3. **Use `--dry-run=client -o yaml | kubectl apply`** for secrets — idempotent and safe
4. **Always re-run image loads** — don't check, just re-run (fast, safe)
5. **Always re-run CRD install** — `--server-side --force-conflicts` handles existing CRDs
6. **`helm upgrade --install`** — installs if not present, upgrades if present

## Environment Variables

Secrets should come from environment, not be hardcoded:

```bash
export POSTGRES_HOST="postgres.infra.svc.cluster.local"
export POSTGRES_USER="postgres"
export POSTGRES_PASSWORD="my-secret"
export SSH_KEY_PATH="$HOME/.ssh/id_ed25519"

./cluster/bootstrap.sh
```
