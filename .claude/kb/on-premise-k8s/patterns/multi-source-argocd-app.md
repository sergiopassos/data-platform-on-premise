# Pattern: Multi-Source ArgoCD Application

## Problem

A Helm chart lives in a public registry (e.g., Strimzi, Spark Operator, Airflow). The values.yaml for that chart is in a private Git repository. ArgoCD needs to install the public chart with the private values.

## Solution: Multi-Source Application with `ref: values`

ArgoCD 2.6+ supports multiple sources per Application. One source provides the Helm chart; another provides the values via a Git reference alias.

## Template

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: <component>
  namespace: argocd
spec:
  project: default
  sources:
    # ── Source 1: Public Helm chart ────────────────────────────────────
    - repoURL: https://<helm-chart-repo-url>
      chart: <chart-name>
      targetRevision: "<chart-version>"
      helm:
        skipCrds: false            # set true for Spark operator
        releaseName: <release>     # optional; defaults to chart name
        valueFiles:
          - $values/helm/<component>/values.yaml   # $values → source 2

    # ── Source 2: Private Git repo (values reference) ──────────────────
    - repoURL: git@github.com:<org>/<repo>.git
      targetRevision: HEAD
      ref: values                  # creates the $values alias

  destination:
    server: https://kubernetes.default.svc
    namespace: <target-namespace>

  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      # Add for large CRDs (Spark):
      # - ServerSideApply=true
      # - Replace=true
```

## Real Examples

### Strimzi Kafka Operator
```yaml
sources:
  - repoURL: https://strimzi.io/charts/
    chart: strimzi-kafka-operator
    targetRevision: "0.41.0"
    helm:
      valueFiles:
        - $values/helm/kafka/values.yaml
  - repoURL: git@github.com:org/data-platform.git
    targetRevision: HEAD
    ref: values
```

### Spark Operator (with skipCrds + server-side apply)
```yaml
sources:
  - repoURL: https://kubeflow.github.io/spark-operator
    chart: spark-operator
    targetRevision: "2.1.0"
    helm:
      skipCrds: true
      valueFiles:
        - $values/helm/spark-operator/values.yaml
  - repoURL: git@github.com:org/data-platform.git
    targetRevision: HEAD
    ref: values
syncPolicy:
  syncOptions:
    - ServerSideApply=true
    - Replace=true
```

### Airflow
```yaml
sources:
  - repoURL: https://airflow.apache.org
    chart: airflow
    targetRevision: "1.15.0"
    helm:
      releaseName: airflow
      valueFiles:
        - $values/helm/airflow/values.yaml
  - repoURL: git@github.com:org/data-platform.git
    targetRevision: HEAD
    ref: values
```

## Rules

1. The source with `ref: values` **must not** have a `path` field — it is a reference anchor, not a chart source.
2. The `$values` prefix in `valueFiles` is only valid inside a source that doesn't define `ref`.
3. Both sources can have different `targetRevision` — pinning the chart version is independent of the Git branch.
4. The SSH Git source requires the ArgoCD repo secret with `argocd.argoproj.io/secret-type=repository`.

## Why Not Single-Source

Single-source pointing to `path: helm/<component>` in Git **fails** because:
- Git path has `values.yaml` but no `Chart.yaml`
- ArgoCD cannot render a Helm chart from values alone
- The public chart is at a different URL not in Git

## Directory Convention

```
helm/
├── airflow/
│   └── values.yaml
├── kafka/
│   └── values.yaml
├── minio/
│   └── values.yaml
├── nessie/
│   └── values.yaml
├── spark-operator/
│   └── values.yaml
└── trino/
    └── values.yaml
```

Each `values.yaml` overrides only the fields that differ from chart defaults.
