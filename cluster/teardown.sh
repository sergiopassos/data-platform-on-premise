#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="data-platform"
REGISTRY_NAME="kind-registry"

log() { echo "[teardown] $*"; }

log "Deleting KIND cluster '${CLUSTER_NAME}'..."
kind delete cluster --name "$CLUSTER_NAME" || {
  log "Cluster '${CLUSTER_NAME}' not found, skipping."
}

log ""
log "Kind cluster deleted. Local registry '${REGISTRY_NAME}' is still running."
log "To also remove the registry:"
log "  docker stop ${REGISTRY_NAME} && docker rm ${REGISTRY_NAME}"
