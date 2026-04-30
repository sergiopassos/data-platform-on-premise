# BRAINSTORM: E2E Agent Squad — 6-Agent MAS for Data Platform Testing

> Exploratory session to clarify intent and approach before requirements capture

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | E2E_AGENT_SQUAD |
| **Date** | 2026-04-25 |
| **Author** | brainstorm-agent |
| **Status** | Ready for Define |

---

## Initial Idea

**Raw Input:** Design a squad of 6 autonomous agents to E2E test the data platform on a KIND 3-node Kubernetes cluster. The squad must run the full Medallion pipeline (Postgres CDC → Kafka → Bronze Iceberg → Silver MERGE → Gold dbt → Trino query) without human intervention, with Langfuse observability and a Fail-Fast pattern that routes to a Reporter agent on any error.

**Context Gathered:**
- Platform runs on KIND cluster with namespaces: `infra`, `streaming`, `processing`, `orchestration`, `serving`
- Existing portal (`portal/app.py`) has `ConnectorActivator`, `ODCSGenerator`, `SchemaInspector` — reusable in agent tools
- Existing Spark jobs: `spark/jobs/bronze_streaming.py` (Kafka→Iceberg) and `spark/jobs/bronze_to_silver.py` (MERGE)
- Existing SparkApplication manifests: `spark/applications/bronze-streaming-app.yaml`, `dags/templates/silver-batch-app.yaml`
- Airflow REST API available at `airflow-webserver.orchestration:8080` (admin/admin)
- Trino at `trino.serving:8080` (port-forwarded as 8082 externally)
- Debezium/KafkaConnect at `kafka-connect.streaming:8083`
- MinIO at `minio.infra:9000` (minio/minio123), buckets: `warehouse`, `bronze`, `contracts`
- Nessie Iceberg catalog at `nessie.infra:19120/api/v1`
- Langfuse deployed in `serving` namespace (or external via LANGFUSE_HOST env var)
- Kafka bootstrap: `kafka-cluster-kafka-bootstrap.streaming:9092`
- PostgreSQL source: `postgres.infra:5432`, db: `sourcedb`, user: `postgres`
- Spark image: `data-platform/spark:3.5.1` (imagePullPolicy: Never — must pre-load into KIND)

**Technical Context Observed (for Define):**

| Aspect | Observation | Implication |
|--------|-------------|-------------|
| Likely Location | `agents/` new top-level package | Separate from `portal/` — this is a test harness, not a portal feature |
| State transport | JSON dict passed as return value between LangGraph nodes | No external broker needed for local KIND |
| Tool reuse | `ConnectorActivator`, `ODCSGenerator`, `SchemaInspector` already in `portal/agent/` | Import directly — do not duplicate |
| Kubernetes access | From outside cluster via `kubeconfig` OR from inside via ServiceAccount | Run from host machine with `kubectl` context pointing at KIND |
| Langfuse SDK | `langfuse` Python package; `Langfuse(host, public_key, secret_key)` | One trace per E2E run; agents add observations/scores to shared trace |

---

## Discovery Questions & Answers

> The input spec was complete enough that these were answered inline by the user's requirements.

| # | Question | Answer | Impact |
|---|----------|--------|--------|
| 1 | Should agents run in the same Python process or in separate containers? | Same process (LangGraph nodes), JSON state passed in-memory | Simpler orchestration; no inter-process messaging needed for local KIND |
| 2 | Is the Orchestrator itself an LLM or a deterministic state machine? | Deterministic router that evaluates `next_agent` and `current_status` fields | Avoids LLM hallucinations in routing; saves tokens; only Data Source & Gold agents need LLM |
| 3 | What is the failure scope — one table or the whole run? | Whole run aborts on first ERROR (Fail-Fast); Reporter generates RCA | Simplifies retry logic; avoids partial state corruption |
| 4 | Does the squad create the test table or assume it exists? | Agent 3 creates and populates the table (simulates full Portal flow) | Idempotency needed: must handle table-already-exists |

---

## Sample Data Inventory

| Type | Location | Count | Notes |
|------|----------|-------|-------|
| Connector activator | `portal/agent/connector_activator.py` | 1 | Direct reuse for Agent 3 tool |
| ODCS generator + fallback | `portal/agent/odcs_generator.py` | 1 | Direct reuse — fallback is deterministic |
| Schema inspector | `portal/agent/schema_inspector.py` | 1 | Introspects PG info_schema for columns |
| SparkApplication YAML | `spark/applications/bronze-streaming-app.yaml`, `dags/templates/silver-batch-app.yaml` | 2 | Agent 4 applies these manifests |
| E2E integration tests | `tests/integration/test_pipeline_e2e.py` | 1 | Reference for assertion patterns |
| Airflow DAG run API | Airflow REST API at `/api/v1/dags/{dag_id}/dagRuns` | — | Agent 5 uses same endpoint |
| Existing contracts | `contracts/customers.yaml` | 1 | Format reference for Agent 3 fallback |

---

## Approaches Explored

### Approach A: LangGraph State Machine + Anthropic Tool Use ⭐ Recommended

**Description:** Implement the squad as a LangGraph `StateGraph` where each node is a Python function that receives and returns the shared `E2EState` TypedDict. The Orchestrator is a pure router (no LLM, 0 tokens). Agents 2, 4, 5 are tool-calling functions with deterministic logic + optional LLM for diagnostics. Agent 3 uses Claude claude-sonnet-4-6 for ODCS generation with a deterministic fallback. Agent 6 is a pure read + Markdown formatter. Langfuse traces flow through a shared `trace_id` in state.

```text
START
  └─► [1] Orchestrator (router)
          ├─► [2] Infrastructure Agent → health scores to Langfuse
          ├─► [3] Data Source Agent → table + contract + CDC
          ├─► [4] Spark Agent → bronze-streaming + silver-batch
          ├─► [5] Gold Agent → dbt DAG + Trino validation
          └─► [6] Reporter (always final, even on Fail-Fast)
```

**State object:**
```python
class E2EState(TypedDict):
    run_id: str
    langfuse_trace_id: str
    current_status: Literal["RUNNING", "ERROR", "SUCCESS"]
    table_name: str
    data_contract_path: str        # s3://contracts/{table}.yaml
    kafka_topic: str               # cdc.public.{table}
    error_log: str | None
    agent_timings: dict[str, float]
    next_agent: str
    scores: dict[str, float]       # infra_health, contract_valid, silver_rows, etc.
```

**Pros:**
- Orchestrator uses 0 LLM tokens — pure Python routing
- Each agent is independently testable as a function
- LangGraph's built-in error edges map directly to Fail-Fast requirement
- Single Python process — no inter-service latency
- Langfuse SDK wraps each node naturally via decorator or explicit span

**Cons:**
- LangGraph adds a dependency (~15MB); team must learn its state graph API
- Parallel agent execution (infra + contract concurrently) requires `langgraph.pregel` parallel nodes — adds complexity

**Why Recommended:** Matches the spec's State Graph semantics exactly. LangGraph was built for exactly this pattern. The statically typed `E2EState` catches missing fields at definition time, not runtime.

---

### Approach B: Pure Python Orchestrator (no framework)

**Description:** A Python `dataclass` holds the state. A `for agent in pipeline: result = agent.run(state)` loop handles sequencing. Fail-Fast is an early `break`. No framework dependency.

**Pros:**
- Zero new dependencies
- Trivially debuggable — just a loop
- Easier to read for non-LangGraph engineers

**Cons:**
- Fail-Fast routing requires manual `if state.status == "ERROR": break` checks everywhere
- No built-in visualization of the state graph
- Parallel execution (if ever needed) requires manual `asyncio.gather` wiring
- State mutation is implicit — bugs harder to trace

---

### Approach C: Separate Microservices via Kafka

**Description:** Each agent is a separate Python microservice that reads/writes the state JSON from a dedicated Kafka topic. The Orchestrator publishes to `e2e.agent.queue`, agents subscribe to their topic.

**Pros:**
- True distributed isolation — agent crashes don't affect others
- Natural async execution
- State persisted in Kafka log

**Cons:**
- Massive over-engineering for a local KIND dev test harness
- Requires 6 additional Kafka consumer groups and topics
- Latency between agents is Kafka commit latency (~100ms minimum)
- Debugging requires Kafka consumer inspection
- **Removed — see YAGNI**

---

## Selected Approach

| Attribute | Value |
|-----------|-------|
| **Chosen** | Approach A — LangGraph State Machine |
| **User Confirmation** | 2026-04-25 (specification explicitly describes State Graph JSON routing) |
| **Reasoning** | User's spec maps directly to LangGraph's StateGraph API. Fail-Fast edge, JSON state, and node delegation are first-class LangGraph concepts. |

---

## Key Decisions Made

| # | Decision | Rationale | Alternative Rejected |
|---|----------|-----------|----------------------|
| 1 | Orchestrator is a deterministic Python router, not an LLM | Zero token cost; routing is rule-based (`next_agent` field + `current_status`); LLM adds hallucination risk for critical control flow | LLM orchestrator — rejected: adds cost and unpredictability |
| 2 | Agents 2, 4, 5 use deterministic tool execution; only Agent 3 uses LLM | Infrastructure checks, Spark apply, and Airflow trigger are deterministic; only contract generation benefits from LLM | All agents use LLM — rejected: wasteful and slower |
| 3 | Reuse `ConnectorActivator`, `ODCSGenerator`, `SchemaInspector` from portal | These are already tested and working; DRY | Copy-paste into agents package — rejected: divergence risk |
| 4 | State is a Python TypedDict, not a raw dict | Catches missing fields at definition time; IDE autocomplete works | Raw dict — rejected: runtime KeyError surprises |
| 5 | Langfuse trace created at run start; `trace_id` passed in state | All agents append observations to the same trace; Reporter reads it via Langfuse API | Per-agent separate traces — rejected: no cross-agent correlation |
| 6 | Fail-Fast routes to Reporter immediately on any `ERROR` status | User spec explicitly requires this; prevents cascading failures from corrupting later checks | Continue on error — rejected: could write bad data to Iceberg |
| 7 | Tools execute kubectl via `subprocess` + kubeconfig, not in-cluster ServiceAccount | Agent runs from host machine where `kubectl` is already configured for KIND | In-cluster pod execution — rejected: adds deployment complexity for a test harness |

---

## Agent Specifications (Detailed)

### Agent 1 — Orchestrator (State Manager)

**System Prompt:**
```xml
<role>
You are a deterministic pipeline router for an E2E data platform test. You evaluate a JSON state object and decide which agent to invoke next. You NEVER execute code or call external services. You ONLY call the delegate_agent tool.
</role>
<guardrails>
- If current_status is "ERROR": immediately call delegate_agent with next_agent="Reporter"
- If current_status is "SUCCESS" and all agents have run: call delegate_agent with next_agent="Reporter"
- The pipeline order is fixed: Infrastructure → DataSource → Spark → Gold → Reporter
- You may NOT skip agents or reorder them
- You may NOT modify any field in the state except next_agent
</guardrails>
```

**Tools:**
```python
def delegate_agent(agent_name: str, state: E2EState) -> E2EState:
    """Route state to the named agent. Returns updated state."""
```

**Eval:** Data flowed Postgres → Trino Gold without human intervention; `current_status == "SUCCESS"`

---

### Agent 2 — Infrastructure (K8s & Storage Admin)

**System Prompt:**
```xml
<role>
You are a read-only infrastructure health checker. You verify that all Kubernetes pods and MinIO buckets are ready before the E2E test begins. You emit Langfuse scores for each check. You NEVER modify cluster state.
</role>
<guardrails>
- READ-ONLY: no kubectl apply, delete, scale, or patch
- Check all 5 namespaces: infra, streaming, processing, orchestration, serving
- A namespace passes if ALL non-job pods have status Running or Completed
- MinIO check: warehouse/, bronze/, contracts/ buckets must exist (not just be accessible)
- On any failure: set current_status="ERROR", populate error_log, return immediately
- Emit langfuse score "infra_health" = 1.0 on pass, 0.0 on fail
</guardrails>
```

**Tools:**
```python
def check_namespace_pods(namespace: str) -> list[dict]:
    """kubectl get pods -n {namespace} -o json → list of {name, status, ready}"""

def check_minio_bucket(bucket: str) -> bool:
    """boto3 s3.head_bucket(Bucket=bucket) → True if exists"""

def emit_langfuse_score(trace_id: str, name: str, value: float, comment: str = "") -> None:
    """langfuse.score(trace_id=trace_id, name=name, value=value)"""
```

**Service coordinates:**
- MinIO: `http://localhost:9000` (port-forwarded) or `http://minio.infra.svc.cluster.local:9000`
- Creds: `AWS_ACCESS_KEY_ID=minio`, `AWS_SECRET_ACCESS_KEY=minio123`

**Eval:** All namespaces healthy; buckets present; `scores["infra_health"] == 1.0`

---

### Agent 3 — Data Source & Contracts (Portal Agent)

**System Prompt:**
```xml
<role>
You are a data source setup agent that simulates the Chainlit portal. You create a test table in PostgreSQL, generate an ODCS v0.9.3 contract, validate it, upload to MinIO, and activate the Debezium CDC connector. You are the only agent that uses an LLM (for contract generation).
</role>
<guardrails>
- Table name MUST be parameterized (default: "e2e_test_run_{run_id[:8]}")
- If LLM contract generation fails: MUST call generate_fallback_contract (deterministic)
- Contract MUST contain at least one field with primaryKey: true
- Kafka topic MUST follow pattern: cdc.public.{table_name}
- Upload to MinIO contracts/ bucket BEFORE activating CDC connector
- If Debezium returns 409 (connector exists): treat as success (idempotent)
- Verify ≥ 1 Kafka message in topic before marking complete
</guardrails>
```

**Tools:**
```python
def create_and_seed_table(table_name: str, ddl: str, seed_rows: int = 5) -> bool:
    """psycopg2 → CREATE TABLE IF NOT EXISTS + INSERT seed rows via kubectl exec"""

def inspect_postgres_schema(table_name: str) -> list[ColumnInfo]:
    """Reuses portal/agent/schema_inspector.SchemaInspector"""

def generate_odcs_contract(table_name: str, columns: list[ColumnInfo]) -> dict:
    """LLM call via ODCSGenerator.generate() with active LLMProvider"""

def generate_fallback_contract(table_name: str, columns: list[ColumnInfo]) -> dict:
    """Deterministic: ODCSGenerator._build_fallback_contract() — always succeeds"""

def validate_contract(contract_yaml: str) -> tuple[bool, str]:
    """kubectl exec portal-pod -- datacontract-cli validate --stdin"""

def upload_contract_to_minio(table_name: str, yaml_content: str) -> str:
    """boto3 put_object → returns s3://contracts/{table_name}.yaml"""

def activate_debezium_connector(table_name: str) -> int:
    """Reuses ConnectorActivator.activate() → returns HTTP status code"""

def consume_one_kafka_message(topic: str, timeout_seconds: int = 60) -> bool:
    """kafka-python KafkaConsumer(topic, bootstrap_servers=...) → True if ≥1 message"""
```

**Service coordinates:**
- PostgreSQL: `postgres.infra.svc.cluster.local:5432` (db=sourcedb, user=postgres)
- Debezium: `http://kafka-connect.streaming.svc.cluster.local:8083`
- Kafka: `kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092`

**Eval:** Table seeded; contract in `s3://contracts/{table}.yaml`; Debezium HTTP 201 or 409; ≥1 Kafka message

---

### Agent 4 — Spark Processing (Bronze/Silver Executor)

**System Prompt:**
```xml
<role>
You are a Spark job executor. You apply SparkApplication manifests to Kubernetes and monitor their completion. You NEVER write PySpark code. You only manage manifest files via kubectl.
</role>
<guardrails>
- Apply bronze-streaming ONLY if not already RUNNING (check status first)
- Silver batch: render dags/templates/silver-batch-app.yaml with table_name and today's date
- Always target namespace: processing
- Catalog URI is always: http://nessie.infra.svc.cluster.local:19120/api/v1
- imagePullPolicy: Never — image data-platform/spark:3.5.1 must be pre-loaded in KIND
- Wait timeout: bronze 300s (streaming), silver 600s (batch)
- On FAILED state: capture driver logs, set current_status="ERROR"
- Delete completed silver SparkApplications after verification (cleanup)
</guardrails>
```

**Tools:**
```python
def get_sparkapplication_status(name: str, namespace: str = "processing") -> str:
    """kubectl get sparkapplication {name} -n {namespace} -o jsonpath=.status.applicationState.state"""

def apply_sparkapplication(manifest_yaml: str, namespace: str = "processing") -> str:
    """kubectl apply -f - → returns SparkApplication name"""

def wait_for_sparkapplication(name: str, namespace: str, timeout: int) -> str:
    """Polls status until COMPLETED/FAILED/timeout → returns final state"""

def get_spark_driver_logs(name: str, namespace: str, tail: int = 100) -> str:
    """kubectl logs -n {namespace} {name}-driver --tail={tail}"""

def check_nessie_table_exists(catalog: str, schema: str, table: str) -> bool:
    """GET http://nessie.infra:19120/api/v1/trees/main/entries → scan for {schema}.{table}"""

def delete_sparkapplication(name: str, namespace: str = "processing") -> bool:
    """kubectl delete sparkapplication {name} -n {namespace}"""
```

**Eval:** bronze-streaming RUNNING; silver-batch COMPLETED; `bronze.{table}_valid` and `silver.{table}` in Nessie

---

### Agent 5 — Gold & Query (Analytical Orchestration)

**System Prompt:**
```xml
<role>
You are the analytical layer orchestrator. You trigger the dbt Gold DAG via Airflow REST API, wait for completion, then validate data accessibility via Trino. You have exactly 1 retry if the DAG fails.
</role>
<guardrails>
- DAG ID: gold_dbt_dag
- Airflow API: http://airflow-webserver.orchestration.svc.cluster.local:8080/api/v1 (admin/admin)
- Retry limit: exactly 1 retry if DAG run state is "failed"
- After DAG success: run SELECT COUNT(*) FROM iceberg.gold.{table} in Trino
- Trino: trino-coordinator.serving.svc.cluster.local:8080 (or localhost:8082 if port-forwarded)
- If COUNT(*) returns 0: set current_status="ERROR" (data did not flow through)
- OpenMetadata lineage check is OPTIONAL — skip if OpenMetadata pod not Running
</guardrails>
```

**Tools:**
```python
def trigger_airflow_dag(dag_id: str, conf: dict) -> str:
    """POST /api/v1/dags/{dag_id}/dagRuns → returns dag_run_id"""

def wait_for_dag_run(dag_id: str, run_id: str, timeout: int = 900) -> str:
    """Polls GET /api/v1/dags/{dag_id}/dagRuns/{run_id} → returns final state"""

def clear_and_retry_dag(dag_id: str, run_id: str) -> str:
    """POST /api/v1/dags/{dag_id}/clearTaskInstances + new dagRun → returns new run_id"""

def query_trino_count(table_fqn: str) -> int:
    """kubectl exec trino-coordinator -- trino --execute 'SELECT COUNT(*) FROM {table_fqn}' --output-format TSV"""

def check_openmetadata_lineage(table_fqn: str) -> bool:
    """GET http://openmetadata.serving:8585/api/v1/lineage/table/name/{table_fqn} → True if lineage exists"""
```

**Service coordinates:**
- Airflow: `http://airflow-webserver.orchestration.svc.cluster.local:8080`
- Trino: `trino.serving.svc.cluster.local:8080`

**Eval:** `gold_dbt_dag` state=success; `COUNT(*) > 0` in Gold table; `scores["gold_rows"] > 0`

---

### Agent 6 — Reporter (Auditor & Langfuse Integration)

**System Prompt:**
```xml
<role>
You are the final audit agent. You read the completed E2E state and Langfuse trace to produce a Markdown report. You are READ-ONLY — you interact with no data services.
</role>
<guardrails>
- Read langfuse_trace_id from state; fetch all observations and scores via Langfuse API
- If current_status is "ERROR": include Root-Cause Analysis section with agent_timings and error_log
- Format output for Slack/Teams mrkdwn: use bold (*text*) not Markdown headers
- Include: run_id, total duration, per-agent timing, pass/fail per eval, Langfuse link
- Do NOT call any Kubernetes, database, or storage APIs
- Report is returned as a string field in the final state; caller is responsible for posting
</guardrails>
```

**Tools:**
```python
def get_langfuse_trace(trace_id: str) -> dict:
    """Langfuse SDK: langfuse.fetch_trace(trace_id) → trace with observations"""

def get_langfuse_scores(trace_id: str) -> list[dict]:
    """Langfuse SDK: langfuse.fetch_scores(trace_id=trace_id) → list of {name, value, comment}"""

def format_slack_report(state: E2EState, trace: dict, scores: list) -> str:
    """Pure Python string formatting — no external calls"""
```

**Eval:** Markdown report generated with all required sections; Langfuse link included

---

## Shared State Graph — Extended

```json
{
  "run_id": "e2e-20260425-a1b2",
  "langfuse_trace_id": "trace_abc890...",
  "current_status": "RUNNING",
  "table_name": "e2e_test_a1b2",
  "data_contract_path": "s3://contracts/e2e_test_a1b2.yaml",
  "kafka_topic": "cdc.public.e2e_test_a1b2",
  "error_log": null,
  "next_agent": "Spark",
  "agent_timings": {
    "Infrastructure": 12.3,
    "DataSource": 45.1,
    "Spark": null
  },
  "scores": {
    "infra_health": 1.0,
    "contract_valid": 1.0,
    "cdc_active": 1.0
  },
  "report_markdown": null
}
```

---

## Data Engineering Context

### Source Systems
| Source | Type | Volume Estimate | Freshness |
|--------|------|-----------------|-----------|
| PostgreSQL `sourcedb` | OLTP | 5 seed rows per E2E run | Real-time via CDC |
| Kafka `cdc.public.*` | Stream | ~5 CDC events per run | <1s latency |
| Iceberg Bronze | Data lake | ~5 rows per table | Streaming (Spark) |
| Iceberg Silver | Data lake | ~5 rows per table | Batch (hourly) |
| Iceberg Gold | Data mart | Aggregated | dbt (triggered) |

### Data Flow Sketch
```text
[PostgreSQL] ──CDC──► [Kafka topic] ──Spark Streaming──► [Bronze Iceberg]
                                                                │
                                                     [Spark Batch MERGE]
                                                                │
                                                       [Silver Iceberg]
                                                                │
                                                         [dbt Gold DAG]
                                                                │
                                                       [Gold Iceberg] ──► [Trino] ──► [Reporter]
```

---

## Features Removed (YAGNI)

| Feature Suggested | Reason Removed | Can Add Later? |
|-------------------|----------------|----------------|
| Parallel agent execution (infra + contract simultaneously) | Adds LangGraph complexity; sequential is sufficient for local KIND | Yes |
| Per-agent LLM for diagnostics | Only Agent 3 needs LLM; others are deterministic | Yes |
| Multi-table E2E run | Single table run is sufficient to prove the pipeline | Yes |
| Slack/Teams webhook posting | Reporter generates the Markdown; caller handles posting | Yes |
| Microservice deployment (Approach C) | Massive over-engineering for a dev harness | No (different feature) |
| dbt test validation (beyond COUNT) | Gold COUNT > 0 is sufficient for E2E smoke test | Yes |
| OpenMetadata required check | OpenMetadata pod may not be running; optional check avoids false failures | Yes — when stable |
| Kafka consumer group lag monitoring | Out of scope for basic E2E pass/fail | Yes |
| Agent retry logic beyond Fail-Fast | Adds state complexity; one run = one truth | Yes |

---

## Incremental Validations

| Section | Presented | User Feedback | Adjusted? |
|---------|-----------|---------------|-----------|
| State Graph JSON structure | Provided in spec | Extended with `table_name`, `agent_timings`, `scores`, `report_markdown` | Yes — enriched |
| Agent responsibilities | Provided in spec | Preserved verbatim; added concrete tool signatures grounded in existing code | Yes — grounded |
| Approach options | Three approaches explored | Approach A confirmed by spec's State Graph framing | No |
| Tool reuse from portal | Observed from codebase | ConnectorActivator, ODCSGenerator, SchemaInspector are direct reuse | Yes — added |

---

## Suggested Requirements for /define

### Problem Statement (Draft)
We need a 6-agent autonomous squad that executes a full E2E test of the Medallion data platform on KIND Kubernetes — from PostgreSQL CDC source through Bronze/Silver Iceberg layers to Gold dbt models queried via Trino — without human intervention, with Langfuse observability and a Fail-Fast pattern that immediately routes to a Reporter on any error.

### Target Users (Draft)
| User | Pain Point |
|------|------------|
| Platform engineer | Manual E2E verification is error-prone and time-consuming after each deployment |
| CI/CD pipeline | No automated post-deploy smoke test that covers the full Medallion flow |
| Data producer | No way to verify a new table's full pipeline path without running test queries manually |

### Success Criteria (Draft)
- [ ] Agent squad runs `python -m agents.run_e2e` from CLI and completes without human input
- [ ] Bronze `customers_valid` rows visible in Trino within 5 minutes of CDC activation
- [ ] Silver `customers` rows produced via MERGE SparkApplication
- [ ] Gold dbt DAG completes with state=success; `SELECT COUNT(*) FROM iceberg.gold.customers_orders` returns > 0
- [ ] Langfuse trace contains scores for `infra_health`, `contract_valid`, `cdc_active`, `silver_rows`, `gold_rows`
- [ ] On any agent failure, Reporter produces a Markdown report with RCA within 10 seconds
- [ ] Full run completes in under 20 minutes on KIND 3-node cluster

### Constraints Identified
- Spark image `data-platform/spark:3.5.1` must have `pyyaml` installed and be pre-loaded into KIND nodes
- Agent 3 requires `GEMINI_API_KEY` env var OR Ollama running — fallback to deterministic if neither
- LangGraph requires Python ≥ 3.11 (matches Airflow worker Python version)
- Kubernetes access via `kubectl` from host — kubeconfig must point at KIND cluster `data-platform`
- Langfuse credentials: `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` env vars
- All tool calls must be idempotent — running the squad twice should not corrupt the platform

### Out of Scope (Confirmed)
- Parallel agent execution
- Slack/Teams webhook integration (Reporter outputs Markdown string only)
- Multi-table simultaneous E2E runs
- Kafka consumer group lag monitoring
- OpenMetadata as a required gate (optional check only)
- Agent self-modification of pipeline code or Kubernetes manifests
- Production cluster testing (KIND only)

---

## Implementation File Plan (Draft for /design)

```text
agents/
  __init__.py
  run_e2e.py              # CLI entrypoint: python -m agents.run_e2e --table customers
  state.py                # E2EState TypedDict + initial_state() factory
  graph.py                # LangGraph StateGraph wiring
  observability.py        # Langfuse trace/score helpers
  orchestrator/
    __init__.py
    router.py             # Agent 1: deterministic next_agent router
  infrastructure/
    __init__.py
    agent.py              # Agent 2: health checks
    tools.py              # check_namespace_pods, check_minio_bucket
  data_source/
    __init__.py
    agent.py              # Agent 3: table + contract + CDC
    tools.py              # wraps portal/agent imports
  spark/
    __init__.py
    agent.py              # Agent 4: SparkApplication lifecycle
    tools.py              # apply, wait, check_nessie
  gold/
    __init__.py
    agent.py              # Agent 5: Airflow DAG + Trino
    tools.py              # trigger_dag, query_trino
  reporter/
    __init__.py
    agent.py              # Agent 6: Langfuse read + Markdown format
    tools.py              # get_langfuse_trace, format_slack_report
```

---

## Session Summary

| Metric | Value |
|--------|-------|
| Questions Asked | 4 (answered from spec) |
| Approaches Explored | 3 |
| Features Removed (YAGNI) | 9 |
| Validations Completed | 4 |
| Agent system prompts drafted | 6 |
| Tool signatures drafted | 28 |

---

## Next Step

**Ready for:** `/define .claude/sdd/features/BRAINSTORM_E2E_AGENT_SQUAD.md`
