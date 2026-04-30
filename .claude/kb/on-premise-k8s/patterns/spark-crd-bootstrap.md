# Pattern: Spark Operator CRD Bootstrap

## Problem

Spark Operator 2.x CRDs (`SparkApplication`, `ScheduledSparkApplication`) exceed the 262144-byte annotation limit imposed by client-side `kubectl apply`. ArgoCD uses client-side apply by default, causing:

```
metadata.annotations: Too long: must have at most 262144 bytes
```

Additionally, CRDs are stored in etcd and are lost when the KIND cluster is recreated. Since ArgoCD has `skipCrds: true`, it won't reinstall them.

## Solution: Pre-Bootstrap via Server-Side Apply

Install CRDs outside of ArgoCD using `helm template --include-crds` piped to `kubectl apply --server-side`. This bypasses the annotation limit entirely.

## Implementation

### bootstrap.sh Step

```bash
log "Pre-installing Spark Operator CRDs via server-side apply..."
helm template spark-crds spark-operator/spark-operator \
  --version 2.1.0 \
  --include-crds \
  | kubectl apply --server-side --force-conflicts -f - 2>/dev/null || true
```

- `--include-crds`: renders only the CRD manifests from the chart
- `--server-side`: server validates and stores the manifest without the `last-applied-configuration` annotation
- `--force-conflicts`: overwrites field managers that conflict (safe for bootstrap)
- `|| true`: don't fail bootstrap if CRDs already exist

### ArgoCD Application (skipCrds)

```yaml
sources:
  - repoURL: https://kubeflow.github.io/spark-operator
    chart: spark-operator
    targetRevision: "2.1.0"
    helm:
      skipCrds: true            # ArgoCD will NOT try to apply CRDs
      valueFiles:
        - $values/helm/spark-operator/values.yaml
  - repoURL: git@github.com:org/data-platform.git
    targetRevision: HEAD
    ref: values
syncPolicy:
  syncOptions:
    - ServerSideApply=true      # use SSA for all remaining resources too
    - Replace=true
```

## Idempotency

The pattern is safe to run multiple times:
- If CRDs don't exist: installs them
- If CRDs exist and are unchanged: no-op (server-side apply detects no diff)
- If CRDs exist with different version: upgrades them (field manager wins on `--force-conflicts`)

## When to Re-Run

| Event | Action Required |
|-------|----------------|
| First cluster creation | Yes — run in bootstrap.sh |
| Cluster recreate (KIND) | Yes — CRDs lost with etcd |
| Spark Operator version upgrade | Yes — CRDs may have changed |
| App restart without cluster delete | No — CRDs persist in etcd |

## Verifying CRDs are Installed

```bash
kubectl get crd | grep spark
# Expected:
# scheduledsparcapplications.sparkoperator.k8s.io   ...
# sparkapplications.sparkoperator.k8s.io            ...
```

## Other Operators with Oversized CRDs

This pattern applies to any operator whose CRDs exceed 262KB. Known cases:
- Spark Operator 2.x (`SparkApplication`)
- Some versions of Prometheus Operator (`PrometheusRule`)
- OpenMetadata (check before applying)

Always check CRD size before choosing between `skipCrds: true` (pre-bootstrap) vs ArgoCD applying CRDs directly:

```bash
helm template <chart> --include-crds | wc -c
# If > 262144 bytes total for any single CRD: use this pattern
```
