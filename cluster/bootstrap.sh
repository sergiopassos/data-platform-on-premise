#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="data-platform"
ARGOCD_VERSION="7.3.11"
ARGOCD_NS="argocd"

log() { echo "[bootstrap] $*"; }

log "Creating KIND cluster..."
kind create cluster --config cluster/kind-config.yaml --name "$CLUSTER_NAME" || {
  log "Cluster already exists, skipping creation."
}

kubectl config use-context "kind-${CLUSTER_NAME}"

log "Adding Helm repos..."
helm repo add argo https://argoproj.github.io/argo-helm
helm repo add minio https://charts.min.io/
helm repo add strimzi https://strimzi.io/charts/
helm repo add spark-operator https://kubeflow.github.io/spark-operator
helm repo add trinodb https://trinodb.github.io/charts
helm repo add projectnessie https://charts.projectnessie.org
helm repo update

log "Creating namespaces..."
kubectl apply -f manifests/namespaces.yaml

log "Installing ArgoCD..."
helm upgrade --install argocd argo/argo-cd \
  --namespace "$ARGOCD_NS" \
  --create-namespace \
  --version "$ARGOCD_VERSION" \
  --set "server.extraArgs[0]=--insecure" \
  --wait --timeout 5m

kubectl apply -f gitops/bootstrap/argocd-install.yaml

log "Waiting for ArgoCD server..."
kubectl wait --for=condition=Available deployment/argocd-server \
  -n "$ARGOCD_NS" --timeout=120s

log "Applying root app (App of Apps)..."
kubectl apply -f gitops/bootstrap/root-app.yaml

log "Bootstrap complete. ArgoCD will now reconcile all platform components."
log "Monitor sync: kubectl port-forward svc/argocd-server -n argocd 8090:80"
log "ArgoCD UI: http://localhost:8090"
log "Initial password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
