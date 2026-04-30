# Data Platform On-Premise

A production-grade, open-source data platform running entirely on local Kubernetes (KIND). Implements a full **CDC → Medallion Architecture** pipeline with an AI-powered self-service portal for data contract generation.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      DATA PLATFORM — FULL CDC FLOW                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐  WAL/pgoutput  ┌───────────────┐  cdc.public.*  ┌──────┐ │
│  │  PostgreSQL  │ ─────────────► │   Debezium    │ ─────────────► │Kafka │ │
│  │  (sourcedb)  │                │ Kafka Connect │                │Strimzi│ │
│  │  customers   │                │    :8083      │                └──┬───┘ │
│  │  orders      │                └───────────────┘                  │     │
│  └──────────────┘                                                    │     │
│                                                                      │     │
│  ┌───────────────────────────────────────────────────────────┐       │     │
│  │              MEDALLION LAYERS (Iceberg + Nessie)          │       │     │
│  │                                                           │       │     │
│  │  BRONZE  (Spark Structured Streaming — 10s microbatch)    │◄──────┘     │
│  │  ├── iceberg.bronze.{table}_valid   ← valid records       │             │
│  │  └── iceberg.bronze.{table}_invalid ← contract failures  │             │
│  │                    │                                      │             │
│  │                    ▼  Airflow @hourly                     │             │
│  │  SILVER  (Spark Batch — MERGE INTO by PK)                 │             │
│  │  └── iceberg.silver.{table}  ← deduplicated, typed        │             │
│  │                    │                                      │             │
│  │                    ▼  Airflow @daily (dbt + Cosmos)       │             │
│  │  GOLD    (dbt models via Trino)                           │             │
│  │  ├── iceberg.gold.orders_summary   ← daily aggregation   │             │
│  │  └── iceberg.gold.customers_orders ← customer enrichment │             │
│  └───────────────────────────────────────────────────────────┘             │
│                                                                             │
│  QUERY: Trino → iceberg catalog → any layer                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Orchestration** | Apache Airflow 2 + Astronomer Cosmos |
| **Stream Processing** | Apache Spark Structured Streaming (Spark Operator) |
| **Batch Processing** | Apache Spark (MERGE INTO via Iceberg) |
| **CDC** | Debezium + Kafka Connect (Strimzi) |
| **Messaging** | Apache Kafka (Strimzi Operator) |
| **Table Format** | Apache Iceberg |
| **Catalog** | Project Nessie (Git-like catalog) |
| **Object Storage** | MinIO (S3-compatible) |
| **Query Engine** | Trino |
| **Transformations** | dbt Core |
| **Source DB** | PostgreSQL (WAL logical replication) |
| **AI Portal** | Chainlit + Gemini / Ollama |
| **Data Contracts** | ODCS (Open Data Contract Standard) |
| **GitOps** | ArgoCD |
| **Infrastructure** | Kubernetes (KIND) |

---

## Key Features

- **Full CDC pipeline** — PostgreSQL WAL → Debezium → Kafka → Iceberg with sub-minute latency
- **Medallion Architecture** — Bronze (raw CDC), Silver (deduplicated MERGE), Gold (dbt aggregations)
- **Data Contract enforcement** — ODCS contracts validate every record at Bronze ingestion; invalid records are quarantined to a separate table
- **AI self-service portal** — Chainlit UI generates ODCS data contracts from table schema inspection using Gemini, Ollama, or a deterministic fallback
- **GitOps-driven** — all platform components managed by ArgoCD; cluster state is fully declarative
- **Entirely open-source** — no managed cloud services required; runs on a single developer machine

---

## Getting Started

See [USAGE.md](USAGE.md) for the complete end-to-end walkthrough:

1. Seed PostgreSQL with sample data
2. Activate CDC connectors via the Chainlit portal or REST API
3. Observe Kafka topics in real time
4. Watch data flow through Bronze → Silver → Gold
5. Query any layer via Trino

**Prerequisites:** Docker, KIND, kubectl, ArgoCD CLI. Bootstrap the cluster with `cluster/bootstrap.sh`.

---

## Project Structure

```
.
├── cluster/          # KIND cluster config and bootstrap script
├── spark/
│   ├── applications/ # SparkApplication CRDs (Bronze streaming, Silver batch)
│   └── scripts/      # PySpark jobs (ConfigMaps)
├── dags/             # Airflow DAGs and dbt project (Gold layer)
├── contracts/        # ODCS data contract examples
├── portal/           # Chainlit AI portal (contract generation)
├── docker/spark/     # Custom Spark image with Iceberg/Nessie/Kafka JARs
└── k8s/              # Kubernetes manifests for all services
```

---

## License

MIT
