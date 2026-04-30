# ArgoCD GitOps — On-Premise Data Platform

## What It Is

ArgoCD is the GitOps controller that keeps the KIND cluster in sync with the Git repository. Git is the single source of truth for all infrastructure: Helm chart versions, values, manifests, and namespaces. ArgoCD continuously reconciles actual cluster state toward desired Git state.

## App of Apps Pattern

One root Application points to a directory of other Application manifests. ArgoCD discovers and manages all platform apps from a single entry point.

```yaml
# gitops/bootstrap/root-app.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root-app
  namespace: argocd
spec:
  project: default
  source:
    repoURL: git@github.com:org/data-platform.git
    targetRevision: HEAD
    path: gitops/apps          # directory of Application YAMLs
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

All per-component apps live in `gitops/apps/*.yaml`. Root app discovers them automatically.

## SSH Repository Authentication

For private GitHub repos, ArgoCD requires an SSH key secret — HTTPS auth is not available without credentials stored in a secret.

```bash
# Create repo secret (idempotent with --dry-run + apply)
kubectl create secret generic repo-data-platform \
  -n argocd \
  --from-literal=type=git \
  --from-literal=url=git@github.com:org/data-platform.git \
  --from-file=sshPrivateKey="$HOME/.ssh/id_ed25519" \
  --dry-run=client -o yaml | kubectl apply -f -

# Label so ArgoCD discovers it
kubectl label secret repo-data-platform -n argocd \
  "argocd.argoproj.io/secret-type=repository" --overwrite
```

**Common error:** `authentication required` or `repository not found` when using HTTPS URL for private repo without credentials. Always use SSH URL with this pattern.

## Multi-Source Application Pattern

The canonical pattern for deploying a public Helm chart with private Git values:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: kafka
  namespace: argocd
spec:
  project: default
  sources:
    # Source 1: public Helm chart registry
    - repoURL: https://strimzi.io/charts/
      chart: strimzi-kafka-operator
      targetRevision: "0.41.0"
      helm:
        skipCrds: false
        valueFiles:
          - $values/helm/kafka/values.yaml   # references source 2 via $values alias

    # Source 2: private Git repo (values only, no chart)
    - repoURL: git@github.com:org/data-platform.git
      targetRevision: HEAD
      ref: values                             # this is the $values alias

  destination:
    server: https://kubernetes.default.svc
    namespace: streaming
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

**Key points:**
- `ref: values` creates the `$values` variable accessible in other sources' `valueFiles`
- Each Helm chart source must have the Git source with `ref: values` in the same Application
- The Git source with `ref: values` does NOT need a `path` — it's just a reference anchor

## ServerSideApply for Large Resources

Some resources (Spark CRDs, large ConfigMaps) exceed the client-side apply annotation limit of 262144 bytes.

```yaml
syncPolicy:
  syncOptions:
    - ServerSideApply=true   # use server-side apply for all resources in this app
    - Replace=true           # fallback: replace instead of patch for immutable fields
```

## Sync Policy Recommendations

| Setting | Value | When to Use |
|---------|-------|-------------|
| `automated.selfHeal` | `true` | Always in dev — recovers from manual `kubectl` changes |
| `automated.prune` | `true` | Safe in dev; caution in prod (deletes removed resources) |
| `CreateNamespace` | `true` | Recommended — namespace creation is idempotent |
| `ServerSideApply` | `true` | Required for Spark operator, large CRDs |

## Troubleshooting

**App stuck in `Syncing`:**
```bash
# Clear stuck operation
kubectl patch app <app-name> -n argocd --type json \
  -p '[{"op":"remove","path":"/operation"}]'
```

**App status `Unknown` or `OutOfSync` after fix:**
```bash
# Force hard refresh (re-evaluate Git state)
kubectl -n argocd annotate app <app-name> \
  argocd.argoproj.io/refresh=hard --overwrite
```

**Old pods still reference deleted PVCs (Airflow pattern):**
Delete old Deployment/StatefulSet manually, then trigger ArgoCD sync. ArgoCD won't delete pods that reference missing PVCs — it gets stuck in a sync loop.

## Bootstrap Order Dependencies

ArgoCD has no built-in dependency ordering between apps. Workarounds:
1. **Sync waves** — add `argocd.argoproj.io/sync-wave: "N"` annotation to apps/resources
2. **Manual order** — for bootstrap, apply root-app last (after Strimzi operator is Ready)
3. **Operator CRDs first** — always pre-install CRDs before the operator app syncs

The Strimzi operator must be Running before any `Kafka`, `KafkaTopic`, or `KafkaConnect` resources are applied — otherwise ArgoCD fails with "no matches for kind".
