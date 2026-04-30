# Spark Operator (kubeflow) — On-Premise K8s

## What It Is

The kubeflow Spark Operator (v2.x) manages Apache Spark jobs on Kubernetes via `SparkApplication` and `ScheduledSparkApplication` CRDs. It handles driver/executor pod lifecycle, RBAC, service accounts, and webhooks for pod mutation.

## CRD Size Problem

Spark Operator 2.x CRDs (`SparkApplication`, `ScheduledSparkApplication`) exceed Kubernetes's **262144-byte annotation limit** for client-side apply. The `kubectl.kubernetes.io/last-applied-configuration` annotation stores the full YAML, which is too large for these CRDs.

**Symptom:**
```
metadata.annotations: Too long: must have at most 262144 bytes
```

**Solution:** Always install Spark CRDs via server-side apply, never client-side.

## CRD Installation Pattern

```bash
# Pre-install CRDs via server-side apply (bypasses annotation limit)
helm template spark-crds spark-operator/spark-operator \
  --version 2.1.0 \
  --include-crds \
  | kubectl apply --server-side --force-conflicts -f -
```

**In ArgoCD Application values:**
```yaml
helm:
  skipCrds: true          # don't let ArgoCD try client-side CRD apply
```

**Plus in syncPolicy:**
```yaml
syncPolicy:
  syncOptions:
    - ServerSideApply=true
    - Replace=true
```

## CRD Lifecycle on Cluster Recreate

CRDs are stored in etcd. When the KIND cluster is deleted and recreated, all CRDs are gone. The Spark Operator Helm chart has `skipCrds: true` in ArgoCD (to avoid the size error), so ArgoCD will not reinstall them.

**Must re-run on every cluster recreate:**
```bash
helm template spark-crds spark-operator/spark-operator \
  --version 2.1.0 --include-crds \
  | kubectl apply --server-side --force-conflicts -f -
```

Add this to `bootstrap.sh` as an idempotent step.

## Webhook Port Conflict

Spark Operator 2.x runs a mutating webhook and a metrics server. Default webhook port `8080` conflicts with the metrics server also on `8080`.

**values.yaml fix:**
```yaml
webhook:
  enable: true
  port: 9443     # must NOT be 8080 (conflicts with metrics)
```

**Symptom when wrong:** `webhook` pod `CrashLoopBackOff`, log shows `bind: address already in use`.

## SparkApplication Example

```yaml
apiVersion: sparkoperator.k8s.io/v1beta2
kind: SparkApplication
metadata:
  name: bronze-ingestion
  namespace: processing
spec:
  type: Python
  pythonVersion: "3"
  mode: cluster
  image: 172.18.0.5:5000/spark-jobs:latest
  imagePullPolicy: Always
  mainApplicationFile: local:///app/bronze_ingestion.py
  sparkVersion: "3.5.0"
  driver:
    cores: 1
    memory: 1g
    serviceAccount: spark
  executor:
    cores: 2
    instances: 2
    memory: 2g
  restartPolicy:
    type: OnFailure
    onFailureRetries: 3
```

## RBAC Requirements

```yaml
# Spark jobs need this ServiceAccount in the processing namespace
serviceAccounts:
  spark:
    create: true
    name: spark
rbac:
  create: true
```

The service account needs RBAC to create/delete driver and executor pods.

## Controller Workers

```yaml
controller:
  workers: 10    # concurrent SparkApplication reconciliation loops
```

Increase for high-throughput environments; 10 is safe default.

## Local Image Registry

For custom Spark job images, push to the KIND local registry:

```bash
# Build and push to local KIND registry
docker build -t 172.18.0.5:5000/spark-jobs:latest ./spark-jobs/
docker push 172.18.0.5:5000/spark-jobs:latest

# Reference in SparkApplication:
# image: 172.18.0.5:5000/spark-jobs:latest
# imagePullPolicy: Always  (Always works with registry; Never only for kind-loaded images)
```

## Anti-Patterns

- **Never** use `kubectl apply` (without `--server-side`) for Spark CRDs — exceeds annotation limit
- **Never** set `webhook.port: 8080` — conflicts with metrics server
- **Never** assume CRDs survive cluster recreate — always pre-install in bootstrap
