# DESIGN — DATA_PLATFORM_K8S

## 1. Metadata

| Field | Value |
|-------|-------|
| Feature ID | `DATA_PLATFORM_K8S` |
| Title | Data Platform On-Premise on Kubernetes (KIND) |
| Status | Ready for Build |
| Author | Design Agent (solution-architect) |
| Owner | `sergio.passos02@gmail.com` |
| Date Created | 2026-04-21 |
| Last Updated | 2026-04-21 |
| Source DEFINE | `.claude/sdd/features/DEFINE_DATA_PLATFORM_K8S.md` |
| Source BRAINSTORM | `.claude/sdd/features/BRAINSTORM_DATA_PLATFORM_K8S.md` |
| KB Domains | `airflow`, `streaming`, `data-modeling`, `dbt`, `lakehouse`, `gitops` |
| Confidence | 0.95 (KB patterns + agents matched for all components) |
| Target Host | Linux, 31GB RAM / 16 CPUs / 353GB disk, KIND multi-node |
| Release Type | MVP, reproducible from Git |

---

## 2. Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                       DATA PLATFORM ON-PREMISE — KIND CLUSTER                             │
│                                                                                            │
│   ┌───────────────── HOST (Docker network) ──────────────────┐                            │
│   │  PostgreSQL (external, Docker)  —  source system         │                            │
│   │      ▲                                                   │                            │
│   └──────┼───────────────────────────────────────────────────┘                            │
│          │ logical replication (pgoutput)                                                  │
│   ─ ─ ─ ─┼─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─     │
│          │                                                                                 │
│   ┌──────┼─────────────────────────── KIND CLUSTER ──────────────────────────────────┐    │
│   │      │                                                                             │   │
│   │  ┌───▼──────── ns: streaming ─────────┐    ┌──────── ns: argocd ──────────┐      │   │
│   │  │ Strimzi Operator                    │    │ ArgoCD                       │      │   │
│   │  │  ├─ Kafka cluster (3 brokers)       │    │  └─ root-app (App of Apps)   │      │   │
│   │  │  ├─ KafkaConnect + Debezium         │◀───│       manages all apps       │      │   │
│   │  │  └─ KafkaTopic CRDs (cdc.public.*)  │    └──────────────────────────────┘      │   │
│   │  └─────────────┬───────────────────────┘                                           │   │
│   │                │ Kafka topics                                                       │   │
│   │                ▼                                                                    │   │
│   │  ┌──────────── ns: processing ──────────┐    ┌──────── ns: infra ──────────┐      │   │
│   │  │ Spark Operator                        │    │ MinIO (S3)                  │      │   │
│   │  │  ├─ SparkApplication: bronze_stream   │───▶│  └─ bucket: warehouse/      │      │   │
│   │  │  │   (Structured Streaming, long)    │    │ Project Nessie (REST)       │      │   │
│   │  │  └─ SparkApplication: silver_batch    │    │  └─ Iceberg catalog        │      │   │
│   │  │      (MERGE INTO, triggered)          │    └──────────────┬──────────────┘      │   │
│   │  └─────────────┬─────────────────────────┘                   │                     │   │
│   │                │                                              │                     │   │
│   │         Bronze │ Iceberg tables         Silver + Gold ◀───────┘                     │   │
│   │                │ (valid / invalid)      (via Trino / Spark)                         │   │
│   │                ▼                                                                    │   │
│   │  ┌──────── ns: orchestration ──────────┐    ┌──────── ns: serving ────────┐      │   │
│   │  │ Airflow (KubernetesExecutor)        │    │ Trino coordinator + workers │      │   │
│   │  │  ├─ silver_processing_dag           │───▶│  └─ catalog: iceberg/nessie │      │   │
│   │  │  ├─ gold_dbt_dag (Cosmos)           │    └──────────────┬──────────────┘      │   │
│   │  │  └─ invalid_monitor_dag             │                   │                     │   │
│   │  └─────────────┬───────────────────────┘                   │                     │   │
│   │                │ dbt models (Gold)                         │                     │   │
│   │                └─────────────────────▶  Trino  ───────────▶│                     │   │
│   │                                                             ▼                     │   │
│   │  ┌──────── ns: governance ─────────────┐    ┌──────── ns: portal ─────────┐      │   │
│   │  │ OpenMetadata                         │    │ Chainlit (portal UI)       │      │   │
│   │  │  ├─ catalog (Kafka, Iceberg, Trino) │    │  └─ AI Agent                │      │   │
│   │  │  ├─ lineage (all layers)             │◀──▶│ Ollama (llama3.2:3b)        │      │   │
│   │  │  └─ governance (tags, owners)        │    │  └─ ODCS v3.1 generator     │      │   │
│   │  └──────────────────────────────────────┘    └──────────────┬──────────────┘      │   │
│   │                                                              │                     │   │
│   │                                                              │ POST connector      │   │
│   │                                                              ▼                     │   │
│   │                                                   KafkaConnect REST (8083)         │   │
│   └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                            │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Legend:**
- Solid arrows = synchronous data flow or dependency
- `ns:` = Kubernetes namespace
- All in-cluster communication uses `<svc>.<ns>.svc.cluster.local`

---

## 3. Components

| Component | Purpose | Technology | Namespace | Helm Chart (version) |
|-----------|---------|------------|-----------|----------------------|
| KIND | Local Kubernetes control plane (multi-node) | kind v0.23+ | — | N/A (CLI) |
| ArgoCD | GitOps controller, App of Apps pattern | argo-cd 2.11+ | `argocd` | `argo/argo-cd` 7.x |
| MinIO | S3-compatible object storage for warehouse | MinIO RELEASE.2024 | `infra` | `minio/minio` 5.x |
| Project Nessie | Iceberg REST catalog | Nessie 0.80+ | `infra` | `nessie/nessie` 0.80+ |
| Apache Iceberg | Table format (Bronze/Silver/Gold) | Iceberg 1.5+ | n/a (format) | N/A |
| Strimzi Operator | Kafka + KafkaConnect + Debezium lifecycle | Strimzi 0.40+ | `streaming` | `strimzi/strimzi-kafka-operator` 0.40+ |
| Kafka cluster | Event bus for CDC | Kafka 3.7 (KRaft) | `streaming` | Strimzi `Kafka` CRD |
| KafkaConnect | Debezium workers for Postgres CDC | KafkaConnect 3.7 + Debezium 2.6 | `streaming` | Strimzi `KafkaConnect` CRD |
| Spark Operator | Manages `SparkApplication` CRD lifecycle | spark-operator 2.0.2 | `processing` | `spark-operator/spark-operator` 2.x |
| Spark | Structured Streaming + batch | Spark 3.5 + Iceberg runtime | `processing` | via `SparkApplication` |
| Airflow | Workflow orchestration, KubernetesExecutor | Airflow 2.9+ | `orchestration` | `apache-airflow/airflow` 1.15+ |
| Astronomer Cosmos | dbt-in-Airflow integration | cosmos 1.5+ | `orchestration` | Python dep in Airflow image |
| dbt Core | SQL transformations (Silver → Gold) | dbt-core 1.8 + dbt-trino 1.8 | `orchestration` (runtime pod) | image: `ghcr.io/dbt-labs/dbt-trino:1.8.0` |
| Trino | Federated SQL query engine | Trino 448+ | `serving` | `trinodb/trino` 0.29+ |
| OpenMetadata | Data catalog + lineage + governance | OpenMetadata 1.4+ | `governance` | `open-metadata/openmetadata` 1.4+ |
| Ollama | Local LLM (llama3.2:3b) | Ollama 0.3+ | `portal` | community chart or raw Deployment |
| Chainlit | Self-service conversational portal | Chainlit 1.1+ | `portal` | custom chart (this repo) |
| PostgreSQL (source) | Simulated OLTP source system | Postgres 16 | external (Docker) | N/A |
| datacontract-cli | ODCS v3.1 validation | datacontract-cli 0.10+ | runtime (Spark pod) | Python dep |

---

## 4. Key Decisions (ADRs)

### D-001 — Nessie as Iceberg REST Catalog (vs Hive Metastore)

- **Context:** Need an Iceberg catalog reachable by both Spark and Trino with minimal operational overhead on a single-host KIND cluster.
- **Decision:** Use Project Nessie with the Iceberg REST protocol.
- **Rationale:**
  - No dedicated relational database dependency (HMS would require a Postgres just for the metastore).
  - Git-like branching semantics are a nice-to-have for future experiments.
  - First-class support on Trino (`iceberg.catalog.type=rest`) and Spark (`org.apache.iceberg.nessie.NessieCatalog`).
- **Rejected:** Hive Metastore — heavier, extra Postgres, extra Helm release.
- **Consequence:** Trino catalog configured with `iceberg.catalog.type=rest`, Spark configured with `spark.sql.catalog.nessie.catalog-impl=org.apache.iceberg.nessie.NessieCatalog`.

### D-002 — Spark Structured Streaming for Bronze ingestion

- **Context:** Data must land in Bronze within seconds of CDC events arriving on Kafka, with inline validation against ODCS contracts.
- **Decision:** Long-lived `SparkApplication` CRD running Spark Structured Streaming, reading all `cdc.public.*` topics and routing to `bronze.valid_{table}` or `bronze.invalid_{table}`.
- **Rationale:**
  - One job handles all tables via topic pattern subscription, reducing operator overhead.
  - `datacontract-cli` is invoked inline via a Python UDF/subprocess for validation.
  - Native Kafka source + Iceberg sink integration.
- **Rejected:** Micro-batch Airflow DAG per topic — latency is high (minutes), scheduling overhead per batch is wasteful, and parallelism caps at Airflow worker capacity.
- **Consequence:** A single long-running streaming pod; Airflow only handles Silver and Gold (batch).

### D-003 — Dead-letter strategy for `bronze/invalid`

- **Context:** Events that fail ODCS validation cannot be silently dropped nor auto-retried (schema errors are not transient).
- **Decision:** Persist invalid events into `bronze.invalid_{table}` Iceberg tables with an additional `_validation_error VARCHAR` column. An Airflow DAG monitors counts and raises alerts; reprocessing is triggered manually.
- **Rationale:**
  - Invalid events are typically schema mismatches that require a contract update — human intervention by design.
  - Iceberg table gives queryability and time travel for forensic analysis.
  - OpenMetadata annotations surface the issue to data owners.
- **Rejected:** Automatic retry with exponential backoff — masks contract drift; DLQ-to-log-only — loses queryability.
- **Consequence:** Airflow `invalid_monitor_dag` with `ExternalTaskSensor`/count check; a manual "Reprocess invalid" trigger button runs `silver_processing_dag` with `reprocess_invalid=True`.

### D-004 — Cosmos `DbtDag` for Gold orchestration

- **Context:** Need idiomatic integration between Airflow and dbt for Gold models, with automatic task-per-model discovery.
- **Decision:** Use `astronomer-cosmos` `DbtDag` with `LoadMode.DBT_LS` and `TrinoTokenProfileMapping`. Each dbt node runs as a Kubernetes pod via `KubernetesExecutor`.
- **Rationale:**
  - `DBT_LS` discovers the DAG structure at parse time — no manual wiring.
  - `KubernetesExecutor` per task guarantees isolation and the dbt image is used only when needed.
  - Cosmos supports running tests as Airflow tasks automatically (`--select` + `dbt test`).
- **Rejected:** Monolithic `BashOperator` running `dbt run` — no per-model retries, no lineage in Airflow.
- **Consequence:** `gold_dbt_dag.py` produces the DAG via Cosmos; a `TriggerDagRunOperator` or `ExternalTaskSensor` chains it after `silver_processing_dag`.

### D-005 — ArgoCD App-of-Apps bootstrap

- **Context:** Entire cluster state must be reproducible from Git; many Applications must be managed together.
- **Decision:** A single `root-app` Application points to `gitops/apps/` and manages per-component `Application` manifests.
- **Rationale:**
  - One `kubectl apply` bootstraps the whole platform.
  - `selfHeal=true` and `prune=true` keep the cluster in sync automatically.
  - Clear separation of bootstrap manifests (`gitops/bootstrap/`) vs managed apps (`gitops/apps/`).
- **Rejected:** Flat ArgoCD `ApplicationSet` over a directory generator — slightly more flexible but harder to reason about for a fixed MVP.
- **Consequence:** `cluster/bootstrap.sh` runs `kind create cluster`, `kubectl apply -f gitops/bootstrap/argocd-install.yaml`, then `kubectl apply -f gitops/bootstrap/root-app.yaml`. Everything else is reconciled by ArgoCD.

### D-006 — dbt materialization `table` for Gold (MVP)

- **Context:** Gold volumes are low in the MVP; correctness and simplicity trump performance.
- **Decision:** All Gold dbt models start as `materialized='table'` (full refresh every run).
- **Rationale:**
  - No incremental state to reason about initially.
  - `dbt-trino` writes to Iceberg via Nessie, which supports atomic table replacement.
  - Easy rollback: re-run the model.
- **Consequence:** Moving to `incremental` happens per-domain once volumes justify it (typically when full refresh exceeds the SLA window).

### D-007 — Airflow `KubernetesExecutor`

- **Context:** Airflow task isolation, resource control, no celery/redis operational burden.
- **Decision:** Use `KubernetesExecutor`; each task creates a dedicated pod in `orchestration`.
- **Rationale:**
  - No shared worker fleet; tasks cannot noisy-neighbor.
  - Pod templates are customized per worker type (dbt pod uses `dbt-trino` image, Spark-submit pod uses lightweight `spark-submit` image, sensor tasks use the default Airflow image).
  - ServiceAccount scoped to the `orchestration` namespace with RBAC to create pods.
- **Consequence:** Helm values set `executor: KubernetesExecutor`; `pod_template_file` configured; RBAC rules included in `helm/airflow/values.yaml`.

---

## 5. File Manifest

Total: ~46 files.

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 1 | `cluster/kind-config.yaml` | Create | KIND multi-node cluster definition (1 control-plane, 2 workers, port mappings) | `ci-cd-specialist` | — |
| 2 | `cluster/bootstrap.sh` | Create | Bootstrap script: `kind create` → ArgoCD install → root-app apply | `ci-cd-specialist` | #1, #3, #4 |
| 3 | `gitops/bootstrap/argocd-install.yaml` | Create | ArgoCD Helm install manifest (kustomized) | `ci-cd-specialist` | — |
| 4 | `gitops/bootstrap/root-app.yaml` | Create | Root `Application` CRD implementing App of Apps | `ci-cd-specialist` | #3 |
| 5 | `gitops/apps/minio-app.yaml` | Create | ArgoCD Application for MinIO | `ci-cd-specialist` | #4, #14 |
| 6 | `gitops/apps/nessie-app.yaml` | Create | ArgoCD Application for Nessie | `ci-cd-specialist` | #4, #15 |
| 7 | `gitops/apps/strimzi-operator-app.yaml` | Create | ArgoCD Application for Strimzi Operator | `ci-cd-specialist` | #4, #16 |
| 8 | `gitops/apps/kafka-cluster-app.yaml` | Create | ArgoCD Application for Kafka + KafkaConnect | `streaming-engineer` | #4, #25, #26, #27 |
| 9 | `gitops/apps/spark-operator-app.yaml` | Create | ArgoCD Application for Spark Operator | `ci-cd-specialist` | #4, #17 |
| 10 | `gitops/apps/airflow-app.yaml` | Create | ArgoCD Application for Airflow | `ci-cd-specialist` | #4, #18 |
| 11 | `gitops/apps/trino-app.yaml` | Create | ArgoCD Application for Trino | `ci-cd-specialist` | #4, #19 |
| 12 | `gitops/apps/openmetadata-app.yaml` | Create | ArgoCD Application for OpenMetadata | `ci-cd-specialist` | #4, #20 |
| 13 | `gitops/apps/ollama-app.yaml` | Create | ArgoCD Application for Ollama | `ci-cd-specialist` | #4, #21 |
| 14 | `helm/minio/values.yaml` | Create | MinIO Helm values (access keys, buckets, PVC) | `data-platform-engineer` | — |
| 15 | `helm/nessie/values.yaml` | Create | Nessie Helm values (REST endpoint, backend=in-memory/RocksDB) | `lakehouse-architect` | #14 |
| 16 | `helm/strimzi/values.yaml` | Create | Strimzi Operator Helm values | `streaming-engineer` | — |
| 17 | `helm/spark-operator/values.yaml` | Create | Spark Operator Helm values (webhook, namespaces watched) | `spark-engineer` | — |
| 18 | `helm/airflow/values.yaml` | Create | Airflow Helm values — KubernetesExecutor, Cosmos, Trino/Nessie connections, dbt sidecar image | `airflow-specialist` + `dbt-specialist` | — |
| 19 | `helm/trino/values.yaml` | Create | Trino Helm values — Iceberg catalog via Nessie, worker sizing | `data-platform-engineer` | #15 |
| 20 | `helm/openmetadata/values.yaml` | Create | OpenMetadata Helm values — ingestion connectors (Kafka, Iceberg, Trino) | `data-platform-engineer` | — |
| 21 | `helm/ollama/values.yaml` | Create | Ollama Helm values — model `llama3.2:3b`, persistence | `genai-architect` | — |
| 22 | `helm/chainlit/values.yaml` | Create | Chainlit Helm values — env vars (Ollama URL, KafkaConnect URL, Postgres conn) | `genai-architect` | #21 |
| 23 | `gitops/apps/chainlit-app.yaml` | Create | ArgoCD Application for Chainlit | `ci-cd-specialist` | #4, #22 |
| 24 | `manifests/namespaces.yaml` | Create | All K8s namespaces (argocd, infra, streaming, processing, orchestration, serving, governance, portal) | `ci-cd-specialist` | — |
| 25 | `manifests/kafka/kafka-cluster.yaml` | Create | Strimzi `Kafka` CRD (KRaft mode, 3 brokers) | `streaming-engineer` | #16 |
| 26 | `manifests/kafka/kafka-connect.yaml` | Create | Strimzi `KafkaConnect` CRD with Debezium Postgres plugin | `streaming-engineer` | #25 |
| 27 | `manifests/kafka/topic-template.yaml` | Create | `KafkaTopic` CRD template (used by portal to instantiate) | `streaming-engineer` | #25 |
| 28 | `contracts/.gitkeep` | Create | Placeholder for ODCS contracts generated by AI agent | `data-contracts-engineer` | — |
| 29 | `spark/jobs/bronze_streaming.py` | Create | Kafka → Bronze Structured Streaming job with inline ODCS validation | `spark-streaming-architect` | #46 |
| 30 | `spark/jobs/bronze_to_silver.py` | Create | Bronze → Silver `MERGE INTO` batch job | `spark-engineer` | — |
| 31 | `spark/applications/bronze-streaming-app.yaml` | Create | `SparkApplication` CRD (streaming, long-lived) | `spark-streaming-architect` | #29 |
| 32 | `spark/applications/silver-batch-app.yaml` | Create | `SparkApplication` CRD template (batch, triggered by Airflow) | `spark-engineer` | #30 |
| 33 | `dags/silver_processing_dag.py` | Create | Airflow DAG: per-table `SparkKubernetesOperator` trigger for Silver | `airflow-specialist` | #32 |
| 34 | `dags/gold_dbt_dag.py` | Create | Airflow DAG: Cosmos `DbtDag` for Gold models | `dbt-specialist` | #35, #36, #37 |
| 35 | `dbt/dbt_project.yml` | Create | dbt project definition | `dbt-specialist` | — |
| 36 | `dbt/profiles/profiles.yml` | Create | Trino profile for dbt (used by Cosmos) | `dbt-specialist` | #19 |
| 37 | `dbt/models/sources.yml` | Create | Declares Silver Iceberg tables as dbt sources | `dbt-specialist` | — |
| 38 | `dbt/models/gold/.gitkeep` | Create | Placeholder for Gold models (populated per domain) | `dbt-specialist` | — |
| 39 | `dbt/tests/.gitkeep` | Create | Placeholder for custom dbt tests | `dbt-specialist` | — |
| 40 | `portal/app.py` | Create | Chainlit entry point — orchestrates agent flow | `genai-architect` | #41, #42, #43 |
| 41 | `portal/agent/schema_inspector.py` | Create | Postgres schema introspection (columns, PK, types) | `genai-architect` | — |
| 42 | `portal/agent/odcs_generator.py` | Create | Ollama → ODCS v3.1 YAML generation | `genai-architect` | #41 |
| 43 | `portal/agent/connector_activator.py` | Create | Posts Debezium connector config to KafkaConnect REST | `streaming-engineer` | #26 |
| 44 | `portal/Dockerfile` | Create | Chainlit container image (Python 3.11 + deps) | `genai-architect` | #40, #45 |
| 45 | `portal/requirements.txt` | Create | Python deps — chainlit, psycopg, httpx, pyyaml, ollama | `genai-architect` | — |
| 46 | `validation/validate.py` | Create | `datacontract-cli` wrapper used by Spark bronze job | `data-contracts-engineer` | #28 |
| 47 | `scripts/bootstrap-cluster.sh` | Create | Full bootstrap wrapper (KIND + ArgoCD + seed Postgres) | `ci-cd-specialist` | #2, #48 |
| 48 | `scripts/seed-postgres.sh` | Create | Seeds test data into external Postgres | `ci-cd-specialist` | — |
| 49 | `scripts/test-e2e.sh` | Create | Runs E2E test suite against running cluster | `ci-cd-specialist` | #53 |
| 50 | `tests/unit/portal/test_schema_inspector.py` | Create | Unit tests for schema introspection | `test-generator` | #41 |
| 51 | `tests/unit/portal/test_odcs_generator.py` | Create | Unit tests for ODCS generation (Ollama mocked) | `test-generator` | #42 |
| 52 | `tests/unit/portal/test_connector_activator.py` | Create | Unit tests for connector activation (httpx mocked) | `test-generator` | #43 |
| 53 | `tests/integration/test_pipeline_e2e.py` | Create | E2E: seed Postgres → wait Bronze/Silver/Gold → assert Trino count | `test-generator` | #49 |

---

## 6. Agent Assignment Rationale

| Domain | Agent | Why |
|--------|-------|-----|
| Cluster, GitOps, Helm, bootstrap | `ci-cd-specialist` | KIND, ArgoCD, Helm chart composition, shell bootstrap — infrastructure-as-code expertise. |
| Kafka CRDs, Debezium, connector activation | `streaming-engineer` | Strimzi CRD idioms, Debezium Postgres plugin config, KafkaConnect REST client. |
| Spark batch jobs + `SparkApplication` CRDs | `spark-engineer` | `MERGE INTO` Iceberg, Spark Operator CRD lifecycle, executor/driver sizing. |
| Spark Structured Streaming (bronze) | `spark-streaming-architect` | Checkpointing, watermark tuning, Kafka source, foreachBatch routing for valid/invalid split. |
| Airflow DAGs + Airflow Helm | `airflow-specialist` | KubernetesExecutor pod templates, `SparkKubernetesOperator`, RBAC. |
| dbt project + Cosmos DAG | `dbt-specialist` | dbt-trino adapter, Cosmos `DbtDag` + `LoadMode.DBT_LS`, profiles.yml mapping. |
| Nessie + Iceberg schemas | `lakehouse-architect` | Iceberg partitioning/evolution, Nessie catalog REST. |
| Trino + MinIO Helm | `data-platform-engineer` | Trino catalog config, MinIO S3 compatibility, OpenMetadata connectors. |
| Chainlit + Ollama + schema inspector + ODCS generator | `genai-architect` | LLM prompting, Chainlit UX, local model ops, ODCS generation. |
| datacontract-cli + contract storage | `data-contracts-engineer` | ODCS v3.1 schema, validation CLI usage. |
| All tests | `test-generator` | pytest unit patterns, integration against KIND, assertion shape. |

---

## 7. Code Patterns

### Pattern 1 — Cosmos `DbtDag` for Gold

```python
# dags/gold_dbt_dag.py
from datetime import datetime

from cosmos import DbtDag, ProfileConfig, ProjectConfig, RenderConfig
from cosmos.constants import LoadMode
from cosmos.profiles import TrinoTokenProfileMapping

gold_dbt_dag = DbtDag(
    dag_id="gold_dbt_dag",
    project_config=ProjectConfig(dbt_project_path="/opt/airflow/dbt"),
    profile_config=ProfileConfig(
        profile_name="data_platform",
        target_name="prod",
        profile_mapping=TrinoTokenProfileMapping(
            conn_id="trino_default",
            profile_args={"schema": "silver"},
        ),
    ),
    render_config=RenderConfig(load_method=LoadMode.DBT_LS),
    operator_args={"image": "ghcr.io/dbt-labs/dbt-trino:1.8.0"},
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["gold", "dbt", "cosmos"],
)
```

### Pattern 2 — Spark Structured Streaming (Bronze)

```python
# spark/jobs/bronze_streaming.py
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp, lit

spark = (
    SparkSession.builder
    .config("spark.sql.catalog.nessie", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.nessie.catalog-impl", "org.apache.iceberg.nessie.NessieCatalog")
    .config("spark.sql.catalog.nessie.uri", "http://nessie.infra.svc.cluster.local:19120/api/v1")
    .config("spark.sql.catalog.nessie.ref", "main")
    .config("spark.sql.catalog.nessie.warehouse", "s3a://warehouse/")
    .config("spark.sql.catalog.nessie.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
    .getOrCreate()
)

df = (
    spark.readStream
    .format("kafka")
    .option("kafka.bootstrap.servers", "kafka.streaming.svc.cluster.local:9092")
    .option("subscribePattern", "cdc\\.public\\..*")
    .option("startingOffsets", "latest")
    .load()
)

# Validation is applied per micro-batch via foreachBatch (see validation/validate.py).
def route_batch(batch_df, batch_id):
    parsed = batch_df.selectExpr("CAST(value AS STRING) AS payload", "topic")
    validated = parsed.withColumn("_ingested_date", current_timestamp())
    # validation.validate.validate_row() decides valid vs invalid rows.
    # valid → nessie.bronze.valid_{table}; invalid → nessie.bronze.invalid_{table} (+ _validation_error).
    ...

(
    df.writeStream
    .foreachBatch(route_batch)
    .option("checkpointLocation", "s3a://warehouse/_checkpoints/bronze_streaming/")
    .trigger(processingTime="30 seconds")
    .start()
    .awaitTermination()
)
```

### Pattern 3 — Silver `MERGE INTO` Iceberg

```python
# spark/jobs/bronze_to_silver.py
spark.sql(f"""
    MERGE INTO nessie.silver.{table_name} t
    USING (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY {pk_columns} ORDER BY _cdc_ts DESC
        ) AS rn
        FROM nessie.bronze.valid_{table_name}
        WHERE _ingested_date = DATE '{processing_date}'
    ) s
      ON {join_condition} AND s.rn = 1
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
""")
```

### Pattern 4 — Chainlit AI Agent entry point

```python
# portal/app.py
import chainlit as cl

from agent.connector_activator import ConnectorActivator
from agent.odcs_generator import ODCSGenerator
from agent.schema_inspector import PostgresSchemaInspector


@cl.on_message
async def handle_message(message: cl.Message):
    table_name = message.content.strip()
    inspector = PostgresSchemaInspector()
    schema = inspector.introspect(table_name)

    generator = ODCSGenerator(ollama_url="http://ollama.portal.svc.cluster.local:11434")
    contract = generator.generate(schema)
    contract.save(f"/contracts/{table_name}.yaml")

    activator = ConnectorActivator(
        kafka_connect_url="http://kafka-connect.streaming.svc.cluster.local:8083"
    )
    activator.activate(table_name, contract)

    await cl.Message(
        content=f"Contrato ODCS gerado e CDC ativado para `{table_name}`."
    ).send()
```

### Pattern 5 — Debezium connector JSON template

```json
{
  "name": "debezium-{table_name}",
  "config": {
    "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
    "database.hostname": "${POSTGRES_HOST}",
    "database.port": "5432",
    "database.user": "${POSTGRES_USER}",
    "database.password": "${POSTGRES_PASSWORD}",
    "database.dbname": "${POSTGRES_DB}",
    "table.include.list": "public.{table_name}",
    "topic.prefix": "cdc",
    "plugin.name": "pgoutput",
    "transforms": "route",
    "transforms.route.type": "org.apache.kafka.connect.transforms.ReplaceField$Value"
  }
}
```

### Pattern 6 — dbt source + Gold model

```yaml
# dbt/models/sources.yml
version: 2
sources:
  - name: silver
    schema: silver
    tables:
      - name: orders
        columns:
          - name: order_id
            tests: [not_null, unique]
```

```sql
-- dbt/models/gold/orders_summary.sql
{{ config(materialized='table') }}

SELECT
    date_trunc('day', created_at) AS order_date,
    status,
    COUNT(*)      AS order_count,
    SUM(amount)   AS total_amount
FROM {{ source('silver', 'orders') }}
GROUP BY 1, 2
```

---

## 8. Data Flow

### 8.1 CDC flow (Postgres → Bronze)

1. **User triggers** a new source registration in the Chainlit portal by typing the source table name (e.g. `orders`).
2. **`schema_inspector.py`** connects to the external Postgres (via service URL) and introspects `information_schema` to return columns, types, primary key.
3. **`odcs_generator.py`** sends the schema to Ollama (`llama3.2:3b`) with an ODCS v3.1 prompt template. The LLM returns YAML that is parsed, validated, then written to `contracts/{table_name}.yaml`.
4. **`connector_activator.py`** posts the Debezium connector config (see Pattern 5) to `http://kafka-connect.streaming.svc.cluster.local:8083/connectors`.
5. **Debezium** begins logical decoding on Postgres (`pgoutput`) and publishes events to `cdc.public.{table_name}` topic.
6. **`bronze_streaming.py`** (Spark Structured Streaming) consumes topic pattern `cdc.public.*`, validates each row against the contract via `validation/validate.py`, and routes:
   - Valid → `nessie.bronze.valid_{table}` (append, partitioned by `_ingested_date`).
   - Invalid → `nessie.bronze.invalid_{table}` (append, with `_validation_error` column).
7. **OpenMetadata** ingestion job picks up new tables on its next scheduled run (lineage: Kafka topic → Iceberg Bronze).

### 8.2 Bronze → Silver flow (batch)

1. **Airflow `silver_processing_dag`** (schedule `@hourly`) enumerates registered contracts and launches a `SparkKubernetesOperator` per table.
2. Each Spark batch job (`bronze_to_silver.py`) executes the `MERGE INTO` in Pattern 3, deduplicating by PK using `_cdc_ts DESC`.
3. Silver tables (`nessie.silver.{table}`) are partitioned by a domain-sensible key (e.g. `DATE(created_at)`), updated via Iceberg MERGE.
4. On success, an `ExternalTaskMarker` signals `gold_dbt_dag` is free to run.
5. **OpenMetadata** lineage updated (Bronze → Silver).

### 8.3 Silver → Gold flow (dbt)

1. **Airflow `gold_dbt_dag`** (Cosmos `DbtDag`) waits on `silver_processing_dag` via `ExternalTaskSensor`.
2. Cosmos discovers dbt nodes via `dbt ls` at DAG parse time and generates one Airflow task per model + tests.
3. Each task runs in a dedicated pod using `ghcr.io/dbt-labs/dbt-trino:1.8.0`, connecting to Trino (`trino.serving.svc.cluster.local:8080`).
4. dbt writes Gold tables back to Iceberg via Trino's Nessie catalog (`iceberg.gold.{model}`).
5. dbt tests (`not_null`, `unique`, custom) run as separate tasks; failures block downstream tasks.
6. **OpenMetadata** lineage updated (Silver → Gold) using dbt manifest ingestion.

### 8.4 Invalid reprocessing flow (manual)

1. Data owner is notified (OpenMetadata annotation + structured log alert) of rising `bronze.invalid_{table}` count.
2. Owner updates the ODCS contract (either directly or by re-running the portal with updated prompt).
3. Owner manually triggers `silver_processing_dag` with `reprocess_invalid=True`.
4. Spark job re-validates `bronze.invalid_{table}` rows against the new contract; rows that now pass are appended to `bronze.valid_{table}` and deleted from `bronze.invalid_{table}`.
5. Normal Silver MERGE proceeds.

---

## 9. Integration Points

| Producer | Consumer | Protocol | Endpoint | Auth |
|----------|----------|----------|----------|------|
| Postgres (external) | KafkaConnect (Debezium) | Postgres logical replication | `host.docker.internal:5432` | user/password env |
| KafkaConnect | Kafka | Kafka protocol | `kafka.streaming.svc.cluster.local:9092` | PLAINTEXT intra-cluster |
| Spark (bronze) | Kafka | Kafka consumer API | `kafka.streaming.svc.cluster.local:9092` | PLAINTEXT |
| Spark | Nessie | REST (Iceberg) | `http://nessie.infra.svc.cluster.local:19120/api/v1` | none (MVP) |
| Spark | MinIO | S3 API | `http://minio.infra.svc.cluster.local:9000` | access/secret key (K8s Secret) |
| Trino | Nessie | REST | same | — |
| Trino | MinIO | S3 API | same | — |
| dbt (Cosmos) | Trino | JDBC/HTTP | `http://trino.serving.svc.cluster.local:8080` | Airflow Connection `trino_default` |
| Airflow | Spark Operator | K8s API | cluster-local | ServiceAccount RBAC |
| Chainlit | Postgres (external) | psycopg | `host.docker.internal:5432` | env secret |
| Chainlit | Ollama | HTTP | `http://ollama.portal.svc.cluster.local:11434` | — |
| Chainlit | KafkaConnect REST | HTTP | `http://kafka-connect.streaming.svc.cluster.local:8083` | — |
| OpenMetadata | Kafka / Trino / Nessie / dbt manifest | various | cluster-local | per-connector config |
| ArgoCD | Git repo | HTTPS | configured repoURL | none (public) or SSH key |

---

## 10. Pipeline Architecture

### 10.1 DAG diagram

```text
 ┌────────────────┐      ┌────────────────────┐      ┌──────────────────────┐
 │  Postgres OLTP │──CDC▶│ KafkaConnect/      │──▶▶▶│ Kafka topics         │
 │  (external)    │      │ Debezium           │      │ cdc.public.*         │
 └────────────────┘      └────────────────────┘      └──────────┬───────────┘
                                                                │ Spark Structured
                                                                │ Streaming
                                                                ▼
                            ┌────────────────────────────────────────────────┐
                            │  bronze.valid_{t}        bronze.invalid_{t}    │
                            │  (Iceberg, _ingested_date partition)           │
                            └────────────────────────────────────────────────┘
                                           │                  │
                   Airflow silver_processing_dag               │ Airflow invalid_monitor_dag
                   (hourly, @SparkKubernetesOperator)          │ (daily count + alert)
                                           ▼
                            ┌───────────────────────────┐
                            │  silver.{t}  (Iceberg,    │
                            │  MERGE INTO by PK)        │
                            └──────────────┬────────────┘
                                           │ Airflow ExternalTaskSensor
                                           ▼
                            ┌───────────────────────────┐
                            │  gold_dbt_dag (Cosmos)    │
                            │  dbt-trino per model      │
                            │  materialized='table'     │
                            └──────────────┬────────────┘
                                           ▼
                            ┌───────────────────────────┐
                            │  iceberg.gold.{model}     │
                            └──────────────┬────────────┘
                                           ▼
                            ┌───────────────────────────┐
                            │  Trino (ad-hoc SQL)       │
                            └───────────────────────────┘
```

### 10.2 Partition strategy

| Table | Partition Key | Granularity | Rationale |
|-------|---------------|-------------|-----------|
| `bronze.valid_{t}` | `_ingested_date` | daily | High write throughput, queries filter by ingest day |
| `bronze.invalid_{t}` | `_ingested_date` | daily | Same as above; low volume but needs partition pruning for count |
| `silver.{t}` (fact-like) | `DATE(created_at)` (or event time) | daily | Most queries filter by business date |
| `silver.{t}` (dim-like) | none | — | Small dimensions (<1M rows), partitioning adds overhead |
| `gold.{t}` (aggregates) | `date_trunc('day', ...)` key | daily | Downstream dashboards slice by day |
| `gold.{t}` (snapshots) | none | — | Full-refresh `materialized='table'` |

### 10.3 Incremental strategy

| Model / Job | Strategy | Key | Lookback |
|-------------|----------|-----|----------|
| `bronze_streaming` | streaming append | — (append only) | n/a |
| `bronze_to_silver` | `MERGE INTO` | Contract PK | Current partition + 1-day late arrivals |
| Gold models (MVP) | `materialized='table'` (full refresh) | — | — |
| Gold models (future, per domain) | `materialized='incremental'` with `incremental_strategy='merge'` | model-defined unique key | 3 days |

### 10.4 Schema evolution plan

| Change Type | Handling |
|-------------|----------|
| **New column** (additive) | ODCS contract updated → Debezium picks up from Postgres; Spark bronze writes new column with default NULL; Iceberg auto-adds column on next write (`spark.sql.iceberg.handle-timestamp-without-timezone=true`); Silver MERGE passes through; dbt models updated manually; **no downtime** |
| **Type change** (compatible, e.g. INT → BIGINT) | Iceberg supports promotion in-place; update contract; Spark and Trino handle transparently |
| **Type change** (breaking) | Dual-write transition: create `silver.{t}_v2`, run both MERGEs for 7 days, cut dbt sources to v2, drop v1 |
| **Column removal** | Deprecate in ODCS contract first; remove from Silver/Gold; after 30 days drop the column physically via `ALTER TABLE ... DROP COLUMN` |
| **PK change** | Create `silver.{t}_v2` with new PK; backfill; cut over |
| **Rename** | Treated as add-new + deprecate-old (same path as removal) |

### 10.5 Data quality gates

| Gate | Layer | Tool | Action on fail |
|------|-------|------|----------------|
| Schema validation | Bronze (streaming) | `datacontract-cli` via `validation/validate.py` | Route row to `bronze.invalid_{t}` with `_validation_error` |
| PK uniqueness after MERGE | Silver | Spark post-MERGE assert (`count(*) vs count(distinct pk)`) | Fail Airflow task, no downstream run |
| Row-count sanity | Silver | Spark compare Bronze-valid count vs Silver delta | Warn if deviation > 20% (logged, not fail) |
| `not_null` / `unique` / custom | Gold | dbt tests via Cosmos | Fail the test task → downstream models skip |
| Freshness | Gold | dbt `source freshness` check | Warn after 24h, fail after 48h |
| Invalid backlog | Bronze | `invalid_monitor_dag` (daily) | Alert + OpenMetadata annotation when count > threshold |

---

## 11. Testing Strategy

### 11.1 Unit tests (pytest)

| Module | File | What is tested |
|--------|------|----------------|
| `portal/agent/schema_inspector.py` | `tests/unit/portal/test_schema_inspector.py` | Postgres mock (via `psycopg-mock`); verify column/PK extraction for common types and composite PK |
| `portal/agent/odcs_generator.py` | `tests/unit/portal/test_odcs_generator.py` | Mock Ollama HTTP; assert generated YAML validates against ODCS v3.1 JSON schema; snapshot test for deterministic prompt |
| `portal/agent/connector_activator.py` | `tests/unit/portal/test_connector_activator.py` | Mock httpx; assert correct payload, correct URL, retry on 5xx |
| `validation/validate.py` | `tests/unit/test_validate.py` (impl-driven, add if time allows) | Valid/invalid row discrimination; error string population |

Run locally: `pytest tests/unit -v`. CI target: `pytest tests/unit --cov --cov-fail-under=85`.

### 11.2 Integration tests

Run against a live KIND cluster after `./scripts/bootstrap-cluster.sh`:

- Port-forward Postgres and Chainlit.
- `tests/integration/test_pipeline_e2e.py`:
  1. Call Chainlit endpoint with table `orders`.
  2. Wait for the Debezium connector to appear (poll `/connectors` on KafkaConnect).
  3. Seed 100 rows into external Postgres via `scripts/seed-postgres.sh`.
  4. Poll Trino until `count(*) from iceberg.bronze.valid_orders = 100` (timeout 120s).
  5. Trigger `silver_processing_dag` via Airflow REST; wait for success.
  6. Assert `count(*) from iceberg.silver.orders = 100`.
  7. Trigger `gold_dbt_dag`; wait for success.
  8. Assert `count(*) from iceberg.gold.orders_summary > 0`.

### 11.3 E2E harness

`scripts/test-e2e.sh`:
1. `./scripts/bootstrap-cluster.sh` (idempotent).
2. Wait for all ArgoCD Applications to be `Healthy + Synced`.
3. `pytest tests/integration -v`.
4. Tear down (optional `--keep` flag for debug).

---

## 12. Error Handling

| Failure | Detection | Handling |
|---------|-----------|----------|
| Postgres unreachable from Debezium | Connector task state = FAILED | Strimzi restarts task (retry policy in connector config); alert surfaces in OpenMetadata |
| Kafka broker down | Spark streaming job emits `KafkaException` | Checkpoint recovery on Spark restart; Spark Operator restarts pod on crash |
| Contract validation error | `validation/validate.py` raises per-row | Row redirected to `bronze.invalid_{t}` with error message; never fails the whole batch |
| Spark driver OOM | Pod evicted | Spark Operator restarts; memory limits increased via Helm value if chronic |
| Silver MERGE fails | Airflow task fails | DAG retries 2x with exponential backoff; if still failing, pager-style alert (structured log + OpenMetadata annotation) |
| dbt model failure | Cosmos task fails | Downstream tasks skip; next run retries |
| dbt test failure | Cosmos test task fails | Downstream model tasks do not run; alert |
| Trino query timeout | JDBC error | dbt retries once; operator alerted if persistent |
| KafkaConnect REST 5xx | `connector_activator.py` | Retries 3x with backoff; surfaces error to Chainlit UI |
| Ollama pod crash | Chainlit request timeout | User sees error; portal pod continues; pod restart auto |
| ArgoCD sync failure | ArgoCD UI shows OutOfSync | `selfHeal=true` retries; manual `argocd app sync` as escape hatch |
| Invalid reprocessing double-insert | — | Idempotency guaranteed by MERGE on PK; deleting from invalid table is atomic |

---

## 13. Configuration

Key variables per component. All injected via Helm values / K8s Secrets; no secrets in Git.

**MinIO** (`helm/minio/values.yaml`)
- `auth.rootUser`, `auth.rootPassword` (Secret)
- `defaultBuckets: "warehouse"`
- `persistence.size: 100Gi`

**Nessie** (`helm/nessie/values.yaml`)
- `versionStoreType: ROCKSDB` (persistent) or `IN_MEMORY` (dev-only)
- `catalog.serviceConfig.uri: http://minio.infra.svc.cluster.local:9000`

**Strimzi Kafka** (`manifests/kafka/kafka-cluster.yaml`)
- `kafka.replicas: 3`, `kafka.config."default.replication.factor": 3`
- `kafka.storage.size: 20Gi`
- KRaft mode (no ZooKeeper)

**KafkaConnect** (`manifests/kafka/kafka-connect.yaml`)
- `replicas: 1`
- `build.plugins: [{name: debezium-postgres, url: ...}]`
- `config.group.id: connect-cluster`

**Spark** (`helm/spark-operator/values.yaml`)
- `sparkJobNamespaces: [processing]`
- Webhook enabled (mutating for SparkApplication)

**Airflow** (`helm/airflow/values.yaml`)
- `executor: KubernetesExecutor`
- `dags.persistence.enabled: true` (PVC) or `gitSync` pointing to this repo
- Connections: `trino_default`, `spark_default`, `postgres_source`
- Cosmos: dbt_project mounted at `/opt/airflow/dbt` via PVC or initContainer

**Trino** (`helm/trino/values.yaml`)
- `additionalCatalogs.iceberg: |` with `connector.name=iceberg`, `iceberg.catalog.type=rest`, `iceberg.rest-catalog.uri=http://nessie.infra.svc.cluster.local:19120/iceberg`

**OpenMetadata** (`helm/openmetadata/values.yaml`)
- Ingestion connectors for Kafka, Iceberg (via Trino), dbt (manifest.json mount)

**Ollama** (`helm/ollama/values.yaml`)
- `env: OLLAMA_MODELS=/models`, `persistence.size: 20Gi`
- `initContainer: ollama pull llama3.2:3b`

**Chainlit** (`helm/chainlit/values.yaml`)
- `env.OLLAMA_URL: http://ollama.portal.svc.cluster.local:11434`
- `env.KAFKA_CONNECT_URL: http://kafka-connect.streaming.svc.cluster.local:8083`
- `env.POSTGRES_*` from Secret
- PVC mounted at `/contracts`

---

## 14. Security Considerations

| Area | MVP Posture | Future Hardening |
|------|-------------|------------------|
| Network | Intra-cluster plaintext (`svc.cluster.local`) | NetworkPolicies per namespace; mTLS via Linkerd/Istio |
| Kafka | PLAINTEXT, no ACLs | SASL/SCRAM + ACLs via Strimzi `KafkaUser` |
| MinIO | Static root access/secret in Secret | Per-app IAM users; server-side encryption |
| Nessie | Anonymous REST | Bearer tokens; Nessie auth provider |
| Trino | No auth | OAuth2 / LDAP / JWT via Helm values |
| OpenMetadata | Default admin | OIDC provider (Keycloak later) |
| Secrets | K8s Secrets (base64) | External Secrets Operator → Vault/SOPS |
| Postgres source | Plain password env var | SSL-required, rotated credentials |
| Image supply chain | Upstream public images | Sign with cosign; scan with Trivy in CI |
| ArgoCD | Local admin | SSO; RBAC projects per namespace |
| Chainlit | No auth | OIDC; per-user session |
| Ollama | Open HTTP | NetworkPolicy allowing only `portal` pods |

---

## 15. Observability

| Concern | MVP | How |
|---------|-----|-----|
| **Logs** | Structured JSON to stdout for every component | Pod logs via `kubectl logs`; optional Loki later |
| **Metrics** | Exposed by each component (Kafka JMX exporter, Trino `/v1/info`, Spark UI, Airflow StatsD) | Prometheus Operator optional (not part of MVP) |
| **Traces** | Not included in MVP | — |
| **Data lineage** | End-to-end in OpenMetadata | ingestion jobs for Kafka, Iceberg (via Trino), dbt manifest |
| **Data quality** | dbt tests + `invalid_monitor_dag` | Counts and pass/fail surfaced to OpenMetadata |
| **Alerting** | Structured log lines with `severity=alert` | Extract via `kubectl logs` for MVP; Alertmanager future |
| **UI access** | `kubectl port-forward` | ArgoCD UI, Airflow UI, Trino UI, OpenMetadata UI, Chainlit UI all reachable via port-forward |
| **Pipeline state** | Airflow UI (per DAG run) + OpenMetadata lineage | — |

---

## 16. Revision History

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 0.1.0 | 2026-04-21 | Design Agent | Initial DESIGN authored from DEFINE `DATA_PLATFORM_K8S` |

---

## 17. Next Step

**Status:** Ready for Build.

Next phase: invoke the **Build** workflow with this DESIGN and the DEFINE as inputs. The Build phase will:

1. Dispatch per-file work to the agents assigned in the File Manifest (see section 5).
2. Follow the bootstrap order: `manifests/namespaces.yaml` → `gitops/bootstrap/*` → `helm/minio` + `helm/nessie` → `helm/strimzi` + `manifests/kafka/*` → `helm/spark-operator` + `spark/*` → `helm/airflow` + `dags/*` + `dbt/*` → `helm/trino` → `helm/openmetadata` → `helm/ollama` + `portal/*`.
3. Produce unit tests alongside each module (`tests/unit/**`) and the integration harness (`tests/integration/test_pipeline_e2e.py`).
4. Validate by running `./scripts/bootstrap-cluster.sh` and `./scripts/test-e2e.sh` on the target host.

Entry points for humans:
- Bootstrap: `cluster/bootstrap.sh`
- Portal UI: `kubectl port-forward -n portal svc/chainlit 8000:8000`
- Airflow UI: `kubectl port-forward -n orchestration svc/airflow-webserver 8080:8080`
- Trino UI: `kubectl port-forward -n serving svc/trino 8081:8080`
- OpenMetadata UI: `kubectl port-forward -n governance svc/openmetadata 8585:8585`
