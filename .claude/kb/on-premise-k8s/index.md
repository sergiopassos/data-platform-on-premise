# On-Premise K8s Data Platform KB

> Operational knowledge for deploying a full open-source data stack on Kubernetes (KIND locally, bare-metal in production). Captures hard-won lessons from Kafka/Strimzi, MinIO, Trino, Airflow, Spark Operator, ArgoCD, dbt+Cosmos, and Nessie deployments.

## Domain Scope

This KB covers the **operational layer** — deploy, configure, troubleshoot, and maintain. It does not cover pipeline logic (see `streaming`, `medallion`, `dbt`, `airflow` KBs).

## Contents

### Concepts

| Concept | Summary |
|---------|---------|
| [kind-cluster](concepts/kind-cluster.md) | KIND multi-node setup, local registry, StorageClass quirks, bootstrap patterns |
| [argocd-gitops](concepts/argocd-gitops.md) | App of Apps pattern, multi-source apps, SSH repo auth, sync policies |
| [strimzi-kafka](concepts/strimzi-kafka.md) | Strimzi operator, KafkaTopic naming, ephemeral storage, StrimziPodSet |
| [spark-operator](concepts/spark-operator.md) | CRD size limits, server-side apply, webhook port conflicts, CRD lifecycle |
| [airflow-k8s](concepts/airflow-k8s.md) | KubernetesExecutor, Bitnami postgresql image pinning, RWX PVC constraints |
| [iceberg-minio-trino](concepts/iceberg-minio-trino.md) | Iceberg catalog on MinIO via Nessie, Trino connector config |

### Patterns

| Pattern | Summary |
|---------|---------|
| [multi-source-argocd-app](patterns/multi-source-argocd-app.md) | Public Helm chart + private Git values in a single ArgoCD Application |
| [kind-local-registry](patterns/kind-local-registry.md) | Local Docker registry trusted by KIND nodes, image loading |
| [spark-crd-bootstrap](patterns/spark-crd-bootstrap.md) | Pre-install oversized CRDs via server-side apply on every cluster recreate |
| [kafka-connect-debezium](patterns/kafka-connect-debezium.md) | Plain Deployment with quay.io/debezium/connect instead of Strimzi KafkaConnect CRD |
| [bootstrap-idempotency](patterns/bootstrap-idempotency.md) | Idempotent bootstrap.sh pattern for local dev clusters |
| [dbt-cosmos-airflow](patterns/dbt-cosmos-airflow.md) | dbt Gold layer via Cosmos DbtDag in Airflow with Trino adapter |

### Specs

| Spec | Summary |
|------|---------|
| [platform-components](specs/platform-components.yaml) | Component registry with namespaces, versions, Helm chart sources |

## Quick Start

1. Check `quick-reference.md` for common commands and gotchas
2. Read `concepts/kind-cluster.md` before anything else — KIND quirks cascade everywhere
3. Use `patterns/multi-source-argocd-app.md` as the template for every Helm-based app

## Key Insight

> On local KIND, three things always bite you: **StorageClass binding mode**, **CRD size limits**, and **image pull policy for locally-loaded images**. Everything else is standard Kubernetes.
