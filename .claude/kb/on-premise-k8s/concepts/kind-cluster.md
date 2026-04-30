# KIND Cluster — On-Premise Local Dev

## What It Is

KIND (Kubernetes IN Docker) runs a full multi-node K8s cluster inside Docker containers. Each node is a Docker container. Used to validate production-grade Kubernetes configurations locally before deploying to bare-metal.

## Cluster Configuration

```yaml
# cluster/kind-config.yaml — 3-node cluster
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry]
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors]
        [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:5001"]
          endpoint = ["http://kind-registry:5000"]
```

## StorageClass Behavior

KIND ships with `rancher.io/local-path` as the default StorageClass. **Critical constraints:**

| Property | Value | Impact |
|----------|-------|--------|
| `volumeBindingMode` | `WaitForFirstConsumer` | PVC stays `Pending` until a pod is scheduled that claims it |
| Access mode | `ReadWriteOnce` only | Airflow logs, shared NFS-style PVCs **will fail** |
| Persistence | Node-local path | Data lost when KIND node container is deleted |

**`WaitForFirstConsumer` race condition:** When a StatefulSet or operator creates multiple PVCs before scheduling pods (e.g., Strimzi `StrimziPodSet` with 3 replicas), PVCs for pods 1 and 2 may never bind if the scheduler can't find a node that satisfies all constraints simultaneously. Fix: use `storage.type: ephemeral` for development Kafka/ZooKeeper.

## Local Registry Integration

```bash
# Start registry container
docker run -d -p 5001:5000 --name kind-registry --restart=always registry:2

# Connect to KIND network so nodes can reach it
docker network connect kind kind-registry

# Get registry IP (needed for containerd trust config)
REGISTRY_IP=$(docker inspect kind-registry \
  --format '{{.NetworkSettings.Networks.kind.IPAddress}}')

# Configure containerd on every KIND node to trust the registry
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

## Locally-Built Images

Images built locally with `docker build` are **not** available to KIND nodes unless explicitly loaded:

```bash
# Load image into KIND (re-run after every cluster recreate)
kind load docker-image my-image:latest --name data-platform

# In Helm values, must use pullPolicy: Never (not Always or IfNotPresent)
image:
  pullPolicy: Never
```

**After cluster recreate:** All loaded images are lost. Must re-run `kind load` for every custom image. Add to bootstrap.sh.

## Cluster Lifecycle Impact on State

| What Gets Lost on Cluster Delete | Solution |
|----------------------------------|----------|
| All PVs / ephemeral Kafka data | Acceptable for dev |
| CRDs (Spark, Strimzi, etc.) | Re-install in bootstrap.sh |
| Custom loaded images | Re-load in bootstrap.sh |
| ArgoCD SSH secrets | Re-create in bootstrap.sh |
| Application secrets | Re-create in bootstrap.sh |

## Resource Constraints

For a 31GB RAM / 16 CPU machine, the full stack fits. Key bottlenecks:
- Kafka broker: 1–2GB RAM minimum
- Spark driver+executor pods: 2–4GB per job run
- Airflow scheduler + webserver: 1–2GB each
- Trino coordinator: 2GB minimum

If memory pressure is an issue: reduce Spark executor memory first, then Airflow workers.

## Anti-Patterns

- **Do not** use `ReadWriteMany` PVCs — local-path doesn't support it (Airflow logs PVC)
- **Do not** use `storage.type: persistent` for Kafka/ZK in KIND — race condition with `WaitForFirstConsumer`
- **Do not** rely on node affinity/topology spread for stateful workloads — KIND nodes have no zone labels by default
