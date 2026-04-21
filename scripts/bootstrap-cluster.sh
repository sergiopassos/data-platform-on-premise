#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
wait_for() { kubectl wait "$@" --timeout=300s; }

log "=== Data Platform Bootstrap ==="

log "Step 1: Creating KIND cluster..."
if kind get clusters | grep -q "data-platform"; then
  log "Cluster 'data-platform' already exists."
else
  mkdir -p /tmp/data-platform/{worker1,worker2}
  kind create cluster --config cluster/kind-config.yaml --name data-platform
fi

kubectl config use-context kind-data-platform

log "Step 2: Adding Helm repos..."
helm repo add argo          https://argoproj.github.io/argo-helm        2>/dev/null || true
helm repo add minio         https://charts.min.io/                      2>/dev/null || true
helm repo add strimzi       https://strimzi.io/charts/                  2>/dev/null || true
helm repo add spark-operator https://kubeflow.github.io/spark-operator  2>/dev/null || true
helm repo add trinodb       https://trinodb.github.io/charts            2>/dev/null || true
helm repo add projectnessie https://charts.projectnessie.org            2>/dev/null || true
helm repo update

log "Step 3: Creating namespaces..."
kubectl apply -f manifests/namespaces.yaml

log "Step 4: Installing ArgoCD..."
helm upgrade --install argocd argo/argo-cd \
  --namespace argocd \
  --create-namespace \
  --version "7.3.11" \
  --set "server.extraArgs[0]=--insecure" \
  --wait --timeout 5m

log "Step 5: Patching ArgoCD config..."
kubectl apply -f gitops/bootstrap/argocd-install.yaml

log "Step 6: Waiting for ArgoCD to be ready..."
wait_for --for=condition=Available deployment/argocd-server -n argocd

log "Step 7: Applying root App of Apps..."
kubectl apply -f gitops/bootstrap/root-app.yaml

log ""
log "=== Bootstrap Complete ==="
log "ArgoCD is reconciling all platform components."
log ""
log "Access points:"
log "  ArgoCD:      kubectl port-forward svc/argocd-server -n argocd 8090:80"
log "  Airflow:     kubectl port-forward svc/airflow-webserver -n orchestration 8080:8080"
log "  Trino:       kubectl port-forward svc/trino -n serving 8081:8080"
log "  Chainlit:    kubectl port-forward svc/chainlit -n portal 8000:8000"
log "  OpenMetadata: kubectl port-forward svc/openmetadata -n governance 8585:8585"
log ""
log "ArgoCD admin password:"
log "  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
