# Pattern: KIND Local Docker Registry

## Problem

KIND nodes can't pull images from Docker Hub during development when working offline, or when building custom images (Spark jobs, portal app). `kind load docker-image` works but is manual and doesn't survive cluster recreate.

## Solution: Persistent Local Registry

Run a Docker registry container on the host, connect it to the KIND network, and configure containerd on all KIND nodes to trust it. Images pushed once to the registry persist across cluster recreates (as long as the registry container runs).

## Setup

```bash
REGISTRY_NAME="kind-registry"
REGISTRY_PORT="5001"

# Start registry (idempotent)
if ! docker ps --filter "name=^/${REGISTRY_NAME}$" --format "{{.Names}}" | grep -q "${REGISTRY_NAME}"; then
  docker run -d -p "${REGISTRY_PORT}:5000" \
    --name "${REGISTRY_NAME}" \
    --restart=always \
    registry:2
fi

# Connect to KIND network
docker network connect kind "${REGISTRY_NAME}" 2>/dev/null || true

# Get registry IP on the KIND network
REGISTRY_IP=$(docker inspect "${REGISTRY_NAME}" \
  --format '{{.NetworkSettings.Networks.kind.IPAddress}}')

# Configure containerd trust on all KIND nodes
for node in $(kind get nodes --name data-platform); do
  docker exec "$node" sh -c "
    mkdir -p /etc/containerd/certs.d/${REGISTRY_IP}:5000
    cat > /etc/containerd/certs.d/${REGISTRY_IP}:5000/hosts.toml << 'EOF'
[host.\"http://${REGISTRY_IP}:5000\"]
  capabilities = [\"pull\", \"resolve\", \"push\"]
  skip_verify = true
EOF
  "
done
```

## Push Images to Registry

```bash
# Tag image for local registry
docker tag my-spark-job:latest ${REGISTRY_IP}:5000/spark-jobs:latest

# Push
docker push ${REGISTRY_IP}:5000/spark-jobs:latest

# Reference in K8s manifests
# image: 172.18.0.5:5000/spark-jobs:latest
# imagePullPolicy: Always   ← Always re-pull from registry
```

## KIND Config Alternative (Static Mirror)

Alternatively, configure the mirror in the KIND cluster config file so newly created nodes automatically trust the registry without re-running the containerd config step:

```yaml
# cluster/kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry]
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors]
        [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:5001"]
          endpoint = ["http://kind-registry:5000"]
nodes:
  - role: control-plane
  - role: worker
  - role: worker
```

This uses the container **name** (`kind-registry`) which resolves via Docker DNS on the KIND network — more stable than the dynamic IP.

## Custom Images That Must Be `kind load`ed

For images that cannot be pushed to a registry (e.g., images built once, or when the registry isn't available), use `kind load`:

```bash
docker build -t data-platform/chainlit-portal:latest portal/
kind load docker-image data-platform/chainlit-portal:latest --name data-platform
```

These images require `imagePullPolicy: Never` in the Helm chart:
```yaml
image:
  repository: data-platform/chainlit-portal
  tag: latest
  pullPolicy: Never    # Never: use only locally loaded images
```

**Important:** `kind load` images are lost when the cluster is recreated. Add the `build + load` to `bootstrap.sh`.

## pullPolicy Decision Table

| Image Source | `pullPolicy` |
|-------------|-------------|
| Local registry (`REGISTRY_IP:5000/...`) | `Always` |
| `kind load`ed image | `Never` |
| Public registry (Docker Hub, quay.io) | `IfNotPresent` |
| Public registry, always latest | `Always` |
