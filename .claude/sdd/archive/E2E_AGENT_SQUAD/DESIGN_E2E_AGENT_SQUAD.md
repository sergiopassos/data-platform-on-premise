# DESIGN: E2E Agent Squad — 6-Agent MAS for Data Platform Testing

> Technical specification for implementing the 6-agent autonomous E2E test squad using LangGraph StateGraph on the KIND Kubernetes cluster.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | E2E_AGENT_SQUAD |
| **Date** | 2026-04-25 |
| **Author** | design-agent |
| **DEFINE** | [DEFINE_E2E_AGENT_SQUAD.md](./DEFINE_E2E_AGENT_SQUAD.md) |
| **Status** | ✅ Shipped |

---

## Architecture Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                    E2E AGENT SQUAD — LANGGRAPH STATE MACHINE                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│   CLI: python -m agents.run_e2e --table e2e_test                             │
│                │                                                              │
│                ▼                                                              │
│   ┌────────────────────┐   E2EState (TypedDict)                              │
│   │  [1] Orchestrator  │◄──────────────────────────────────────┐             │
│   │   (router node)    │                                        │             │
│   └─────────┬──────────┘                                        │             │
│             │ route_next_agent()                                 │             │
│    ┌────────▼────────────────────────────────────────┐          │             │
│    │                                                  │          │             │
│    │ "infra"     "data_source"  "spark"   "gold"      │          │             │
│    │    │             │           │         │          │          │             │
│    ▼    ▼             ▼           ▼         ▼    ▼    │          │             │
│  ┌────┐ ┌──────────┐ ┌─────────┐ ┌──────┐ ┌────────┐│          │             │
│  │ 2  │ │    3     │ │    4    │ │  5   │ │   6    ││          │             │
│  │Infra│ │DataSource│ │ Spark   │ │ Gold │ │Reporter││          │             │
│  │    │ │          │ │         │ │      │ │  END   ││          │             │
│  └──┬─┘ └────┬─────┘ └────┬────┘ └──┬───┘ └────────┘│          │             │
│     └────────┴────────────┴─────────┘                 │          │             │
│                    any non-reporter node ──────────────┘──► back to [1]       │
│                    (current_status=ERROR) ─────────────────► [6] Reporter     │
│                                                                               │
├───────────────────────────────────────────────────────────────────────────────┤
│  SHARED LANGFUSE TRACE                                                        │
│  trace_id: "trace_abc890..."                                                  │
│  Observations: infra_check, contract_gen, spark_bronze, spark_silver,        │
│               dag_trigger, trino_query                                        │
│  Scores: infra_health=1.0, contract_valid=1.0, cdc_active=1.0,              │
│          silver_rows=5.0, gold_rows=42.0                                      │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| `agents/state.py` | Shared `E2EState` TypedDict + factory | Python 3.11 `TypedDict` |
| `agents/graph.py` | LangGraph `StateGraph` wiring with conditional edges | `langgraph >= 0.2` |
| `agents/config.py` | All service URLs and credentials from env vars | `dataclasses` + `os.getenv` |
| `agents/observability.py` | Langfuse trace init, `observe()` decorator, score helpers | `langfuse >= 2.0` |
| `agents/run_e2e.py` | CLI entrypoint: `python -m agents.run_e2e` | `argparse` |
| `agents/orchestrator/router.py` | Agent 1: deterministic `next_agent` router node | Pure Python (0 LLM tokens) |
| `agents/infrastructure/` | Agent 2: K8s pod health + MinIO bucket checks | `subprocess` + `boto3` |
| `agents/data_source/` | Agent 3: PostgreSQL + ODCS contract + CDC activation | Reuses `portal/agent/*` |
| `agents/spark_processing/` | Agent 4: SparkApplication CRD lifecycle via kubectl | `subprocess` + `yaml` |
| `agents/gold/` | Agent 5: Airflow DAG trigger + Trino COUNT validation | `httpx` + `subprocess` |
| `agents/reporter/` | Agent 6: Langfuse read + Markdown report generation | `langfuse` SDK |

---

## Key Decisions

### Decision 1: LangGraph StateGraph with Orchestrator as a Re-Entry Node

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-25 |

**Context:** The spec requires a central Orchestrator that evaluates JSON state and delegates to the next agent. LangGraph offers two patterns: (a) each node connects directly to the next via conditional edges, or (b) all agents return to a central orchestrator node that picks the next route.

**Choice:** Pattern (b) — all agent nodes return to the Orchestrator node via `add_edge(agent, "orchestrator")`. The Orchestrator evaluates `current_status` and `next_agent` from state, then routes via a conditional edge function.

**Rationale:** Matches the spec's "Orchestrator evaluates JSON and delegates" semantics exactly. Fail-Fast is a single check in the orchestrator: `if state["current_status"] == "ERROR": return "reporter"`. Adding a new agent only requires adding one edge and one pipeline-order entry in the router — no changes to other agents.

**Alternatives Rejected:**
1. Direct conditional edges between agents — rejects: Fail-Fast logic would be duplicated in every agent's edge function.
2. Pure Python for-loop (no LangGraph) — rejects: loses graph visualization and statically-typed state transitions; harder to add parallel execution later.

**Consequences:**
- Trade-off: every agent execution incurs two LangGraph node invocations (agent → orchestrator → next-agent). Negligible overhead for a sequential test harness.
- Benefit: Fail-Fast is implemented in exactly one place.

---

### Decision 2: Synchronous Tools with asyncio.run() Isolation for Agent 3

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-25 |

**Context:** `ODCSGenerator.generate()` is `async` (uses `await provider.generate_yaml()`). All other agent tools are synchronous (subprocess, boto3, httpx sync). LangGraph nodes are synchronous by default in `langgraph >= 0.2`.

**Choice:** Agent 3 calls `asyncio.run(odcs_generator.generate(...))` inside its synchronous node function. All other agents remain synchronous.

**Rationale:** Avoids converting the entire graph to async for a single async call. `asyncio.run()` creates an isolated event loop; safe because Agent 3 is not running inside an existing async context (LangGraph invocation is synchronous).

**Alternatives Rejected:**
1. Full async graph (`StateGraph` with `async def` nodes) — rejects: LangGraph async requires careful event loop management; overkill for 5 sync + 1 async agents.
2. Sync wrapper for ODCSGenerator — rejects: would require modifying portal code, creating divergence risk.

**Consequences:**
- Agent 3's LLM call blocks the calling thread for its duration (typically < 5s). Acceptable for a sequential test harness.

---

### Decision 3: kubectl via subprocess — No Python Kubernetes Client

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-25 |

**Context:** Agent 2 and Agent 4 need to interact with Kubernetes. Options: (a) `kubernetes` Python client SDK, (b) `kubectl` via `subprocess`.

**Choice:** `subprocess.run(["kubectl", ...], capture_output=True)` with the host kubeconfig.

**Rationale:** The existing integration tests (`tests/integration/test_pipeline_e2e.py`) already use this pattern extensively — it's proven and requires no additional dependencies. The `kubernetes` SDK requires RBAC config and kubeconfig parsing that kubectl handles transparently. Agents run from the host machine where `kubectl` is already configured for `kind-data-platform`.

**Alternatives Rejected:**
1. `kubernetes` Python SDK — rejects: adds dependency, requires explicit kubeconfig loading; no benefit for a local test harness.
2. In-cluster ServiceAccount pod — rejects: requires deploying the agent as a K8s pod; massively over-engineering a dev harness.

**Consequences:**
- All subprocess calls include `timeout=30` (60 for wait loops) to prevent hanging the test run.
- `kubectl` must be on `PATH`; documented in README as prerequisite.

---

### Decision 4: Direct Import from portal/agent — No Code Duplication

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-25 |

**Context:** `portal/agent/` contains `ConnectorActivator`, `ODCSGenerator`, `PostgresSchemaInspector`, and `LLMProvider` implementations that Agent 3 needs.

**Choice:** `from portal.agent.connector_activator import ConnectorActivator` — direct import, no copy-paste.

**Rationale:** These classes are already tested (unit tests in `tests/unit/portal/`). Duplicating them creates a maintenance burden and divergence risk. The `portal/` package is a sibling directory at the repo root; Python path resolution works as long as the repo root is in `sys.path` (ensured by `pyproject.toml` or running from repo root).

**Consequences:**
- `agents/` package depends on `portal/` package. This is acceptable — `agents/` is a test harness that orchestrates the platform, not a production service.
- If `portal/agent/` APIs change, Agent 3 tools must be updated.

---

### Decision 5: Silver SparkApplication Template Rendered in Python, Not Jinja2

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-25 |

**Context:** `dags/templates/silver-batch-app.yaml` uses Airflow Jinja2 syntax (`{{ params.table_name }}`). Agent 4 applies this manifest directly via `kubectl apply`, bypassing Airflow's template rendering.

**Choice:** Agent 4 reads the YAML, performs Python string replacement (`yaml_str.replace("{{ params.table_name }}", table_name).replace("{{ params.date }}", date_str)`), then pipes the rendered YAML to `kubectl apply -f -`.

**Rationale:** The template file is simple (2 variables). Using the Jinja2 library adds a dependency for 2 replacements. Python `str.replace()` is explicit and debuggable. The rendered manifest is logged before apply for auditability.

**Consequences:**
- If the template gains more complex Jinja2 logic (loops, conditionals), this approach breaks. Acceptable for MVP — document as constraint.

---

## File Manifest

| # | File | Action | Purpose | Dependencies |
|---|------|--------|---------|--------------|
| 1 | `agents/__init__.py` | Create | Package marker | None |
| 2 | `agents/state.py` | Create | `E2EState` TypedDict + `initial_state()` | None |
| 3 | `agents/config.py` | Create | Service URLs/credentials from env vars | None |
| 4 | `agents/observability.py` | Create | Langfuse trace init, `observe()` ctx mgr, score helpers | `langfuse` |
| 5 | `agents/graph.py` | Create | LangGraph `StateGraph` wiring | 2, 6, 7, 8, 9, 10, 11 |
| 6 | `agents/run_e2e.py` | Create | CLI entrypoint — `python -m agents.run_e2e` | 3, 5 |
| 7 | `agents/orchestrator/__init__.py` | Create | Package marker | None |
| 8 | `agents/orchestrator/router.py` | Create | Agent 1: `orchestrator_node()` + `route_next_agent()` | 2 |
| 9 | `agents/infrastructure/__init__.py` | Create | Package marker | None |
| 10 | `agents/infrastructure/tools.py` | Create | `check_namespace_pods`, `check_minio_bucket` | `boto3`, `subprocess` |
| 11 | `agents/infrastructure/agent.py` | Create | Agent 2: `infra_node()` LangGraph node function | 2, 3, 4, 10 |
| 12 | `agents/data_source/__init__.py` | Create | Package marker | None |
| 13 | `agents/data_source/tools.py` | Create | `create_and_seed_table`, `consume_one_kafka_message`, `validate_contract_cli`, `upload_contract_to_minio` | `psycopg`, `kafka-python`, `boto3` |
| 14 | `agents/data_source/agent.py` | Create | Agent 3: `data_source_node()` | 2, 3, 4, 13 + portal imports |
| 15 | `agents/spark_processing/__init__.py` | Create | Package marker | None |
| 16 | `agents/spark_processing/tools.py` | Create | `get_sparkapplication_status`, `apply_sparkapplication`, `wait_for_sparkapplication`, `get_spark_driver_logs`, `check_nessie_table_exists`, `delete_sparkapplication` | `subprocess`, `httpx`, `yaml` |
| 17 | `agents/spark_processing/agent.py` | Create | Agent 4: `spark_node()` | 2, 3, 4, 16 |
| 18 | `agents/gold/__init__.py` | Create | Package marker | None |
| 19 | `agents/gold/tools.py` | Create | `trigger_airflow_dag`, `wait_for_dag_run`, `clear_and_retry_dag`, `query_trino_count`, `check_openmetadata_lineage` | `httpx`, `subprocess` |
| 20 | `agents/gold/agent.py` | Create | Agent 5: `gold_node()` | 2, 3, 4, 19 |
| 21 | `agents/reporter/__init__.py` | Create | Package marker | None |
| 22 | `agents/reporter/tools.py` | Create | `get_langfuse_trace`, `get_langfuse_scores`, `format_slack_report` | `langfuse` |
| 23 | `agents/reporter/agent.py` | Create | Agent 6: `reporter_node()` | 2, 3, 4, 22 |
| 24 | `agents/requirements.txt` | Create | Pin: `langgraph>=0.2`, `langfuse>=2.0`, `boto3`, `httpx`, `kafka-python`, `psycopg[binary]`, `pyyaml` | None |
| 25 | `tests/unit/agents/__init__.py` | Create | Package marker | None |
| 26 | `tests/unit/agents/test_state.py` | Create | Unit tests for `E2EState`, `initial_state()` | 2 |
| 27 | `tests/unit/agents/test_orchestrator.py` | Create | Unit tests for `orchestrator_node()` and `route_next_agent()` | 8 |
| 28 | `tests/unit/agents/test_infrastructure.py` | Create | Unit tests for `infra_node()` with mocked subprocess and boto3 | 10, 11 |
| 29 | `tests/unit/agents/test_data_source.py` | Create | Unit tests for `data_source_node()` with mocked portal imports | 13, 14 |
| 30 | `tests/unit/agents/test_spark_processing.py` | Create | Unit tests for `spark_node()` with mocked kubectl | 16, 17 |
| 31 | `tests/unit/agents/test_gold.py` | Create | Unit tests for `gold_node()` with mocked Airflow API and Trino | 19, 20 |
| 32 | `tests/unit/agents/test_reporter.py` | Create | Unit tests for `reporter_node()` with mocked Langfuse | 22, 23 |

**Total Files:** 32

---

## Agent Assignment Rationale

| Agent | Files | Why |
|-------|-------|-----|
| `python-developer` | 2, 3, 4, 6, 8, 10, 11, 13, 14, 16, 17, 19, 20, 22, 23 | Core Python code: TypedDict, pure functions, subprocess tools, tool wrappers |
| `ai-data-engineer` | 5 | LangGraph StateGraph wiring — graph topology, conditional edges, Fail-Fast routing |
| `on-premise-k8s-specialist` | 10, 16 | kubectl subprocess tools — K8s API patterns, pod status checking, SparkApplication CRDs |
| `test-generator` | 25-32 | pytest unit tests with mocks for subprocess, boto3, httpx, Langfuse SDK |
| `llm-specialist` | 4 | Langfuse observability: trace lifecycle, observation spans, score emission |

---

## Code Patterns

### Pattern 1: E2EState TypedDict

```python
# agents/state.py
from __future__ import annotations
import uuid
from typing import Literal, TypedDict


class E2EState(TypedDict):
    run_id: str
    langfuse_trace_id: str
    current_status: Literal["RUNNING", "ERROR", "SUCCESS"]
    table_name: str
    data_contract_path: str          # s3://contracts/{table}.yaml
    kafka_topic: str                  # cdc.public.{table_name}
    error_log: str | None
    next_agent: str                   # "infra" | "data_source" | "spark" | "gold" | "reporter"
    agent_timings: dict[str, float]  # wall-clock seconds per agent
    scores: dict[str, float]         # langfuse eval scores
    report_markdown: str | None       # final report from Reporter


def initial_state(table_name: str) -> E2EState:
    run_id = f"e2e-{uuid.uuid4().hex[:8]}"
    return E2EState(
        run_id=run_id,
        langfuse_trace_id="",          # set by run_e2e.py after Langfuse init
        current_status="RUNNING",
        table_name=table_name,
        data_contract_path="",
        kafka_topic=f"cdc.public.{table_name}",
        error_log=None,
        next_agent="infra",
        agent_timings={},
        scores={},
        report_markdown=None,
    )
```

---

### Pattern 2: Orchestrator Router Node

```python
# agents/orchestrator/router.py
from __future__ import annotations
from agents.state import E2EState

_PIPELINE_ORDER = ["infra", "data_source", "spark", "gold", "reporter"]


def orchestrator_node(state: E2EState) -> E2EState:
    """Deterministic router. Sets next_agent; never calls external services."""
    if state["current_status"] == "ERROR":
        return {**state, "next_agent": "reporter"}

    current = state["next_agent"]
    if current == "reporter" or state["current_status"] == "SUCCESS":
        return {**state, "next_agent": "reporter"}

    idx = _PIPELINE_ORDER.index(current)
    if idx + 1 < len(_PIPELINE_ORDER):
        return {**state, "next_agent": _PIPELINE_ORDER[idx + 1]}

    return {**state, "next_agent": "reporter", "current_status": "SUCCESS"}


def route_next_agent(state: E2EState) -> str:
    """LangGraph edge function: returns the name of the next node."""
    return state["next_agent"]
```

---

### Pattern 3: LangGraph StateGraph Wiring

```python
# agents/graph.py
from langgraph.graph import StateGraph, END
from agents.state import E2EState
from agents.orchestrator.router import orchestrator_node, route_next_agent
from agents.infrastructure.agent import infra_node
from agents.data_source.agent import data_source_node
from agents.spark_processing.agent import spark_node
from agents.gold.agent import gold_node
from agents.reporter.agent import reporter_node

_AGENT_NODES = {
    "infra": infra_node,
    "data_source": data_source_node,
    "spark": spark_node,
    "gold": gold_node,
    "reporter": reporter_node,
}


def build_graph() -> StateGraph:
    graph = StateGraph(E2EState)

    graph.add_node("orchestrator", orchestrator_node)
    for name, fn in _AGENT_NODES.items():
        graph.add_node(name, fn)

    graph.set_entry_point("orchestrator")

    graph.add_conditional_edges(
        "orchestrator",
        route_next_agent,
        {name: name for name in _AGENT_NODES},
    )

    for name in ("infra", "data_source", "spark", "gold"):
        graph.add_edge(name, "orchestrator")

    graph.add_edge("reporter", END)

    return graph.compile()
```

---

### Pattern 4: Agent Node with Timing and Error Handling

```python
# Template for any agent node (e.g., agents/infrastructure/agent.py)
import time
from agents.state import E2EState
from agents.config import Config
from agents.observability import observe, emit_score
from agents.infrastructure.tools import check_namespace_pods, check_minio_bucket

_NAMESPACES = ["infra", "streaming", "processing", "orchestration", "serving"]
_BUCKETS = ["warehouse", "bronze", "contracts"]


def infra_node(state: E2EState) -> E2EState:
    start = time.monotonic()
    cfg = Config.from_env()

    with observe(state["langfuse_trace_id"], name="infra_health_check") as span:
        try:
            for ns in _NAMESPACES:
                pods = check_namespace_pods(ns)
                not_ready = [p for p in pods if p["status"] not in ("Running", "Completed", "Succeeded")]
                if not_ready:
                    names = [p["name"] for p in not_ready]
                    raise RuntimeError(f"Namespace {ns}: pods not ready: {names}")

            for bucket in _BUCKETS:
                if not check_minio_bucket(bucket, cfg):
                    raise RuntimeError(f"MinIO bucket missing: {bucket}")

            emit_score(state["langfuse_trace_id"], "infra_health", 1.0)
            elapsed = time.monotonic() - start
            return {
                **state,
                "next_agent": "infra",   # orchestrator advances to next
                "agent_timings": {**state["agent_timings"], "infra": elapsed},
                "scores": {**state["scores"], "infra_health": 1.0},
            }

        except Exception as exc:
            emit_score(state["langfuse_trace_id"], "infra_health", 0.0, comment=str(exc))
            elapsed = time.monotonic() - start
            return {
                **state,
                "current_status": "ERROR",
                "error_log": f"[Infrastructure] {exc}",
                "next_agent": "reporter",
                "agent_timings": {**state["agent_timings"], "infra": elapsed},
                "scores": {**state["scores"], "infra_health": 0.0},
            }
```

---

### Pattern 5: Observability Helpers

```python
# agents/observability.py
from __future__ import annotations
import contextlib
from langfuse import Langfuse
from agents.config import Config


def get_langfuse() -> Langfuse:
    cfg = Config.from_env()
    return Langfuse(
        host=cfg.langfuse_host,
        public_key=cfg.langfuse_public_key,
        secret_key=cfg.langfuse_secret_key,
    )


def init_trace(run_id: str) -> str:
    """Create a new Langfuse trace for an E2E run. Returns trace_id."""
    lf = get_langfuse()
    trace = lf.trace(name=f"e2e-run-{run_id}", id=run_id)
    return trace.id


@contextlib.contextmanager
def observe(trace_id: str, name: str):
    """Context manager that wraps a block in a Langfuse observation span."""
    lf = get_langfuse()
    span = lf.span(trace_id=trace_id, name=name)
    try:
        yield span
        span.end()
    except Exception as exc:
        span.end(level="ERROR", status_message=str(exc))
        raise


def emit_score(trace_id: str, name: str, value: float, comment: str = "") -> None:
    lf = get_langfuse()
    lf.score(trace_id=trace_id, name=name, value=value, comment=comment)
```

---

### Pattern 6: Config Dataclass

```python
# agents/config.py
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Kubernetes
    kubectl_context: str

    # MinIO / S3
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str

    # PostgreSQL
    postgres_host: str
    postgres_port: int
    postgres_dbname: str
    postgres_user: str
    postgres_password: str

    # Kafka
    kafka_bootstrap: str

    # Debezium / KafkaConnect
    kafka_connect_url: str

    # Nessie
    nessie_url: str

    # Airflow
    airflow_url: str
    airflow_user: str
    airflow_pass: str

    # Trino
    trino_host: str
    trino_port: int

    # Langfuse
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            kubectl_context=os.getenv("KUBECTL_CONTEXT", "kind-data-platform"),
            minio_endpoint=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"),
            minio_access_key=os.getenv("MINIO_ACCESS_KEY", "minio"),
            minio_secret_key=os.getenv("MINIO_SECRET_KEY", "minio123"),
            postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
            postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
            postgres_dbname=os.getenv("POSTGRES_DB", "sourcedb"),
            postgres_user=os.getenv("POSTGRES_USER", "postgres"),
            postgres_password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            kafka_bootstrap=os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"),
            kafka_connect_url=os.getenv("KAFKA_CONNECT_URL", "http://localhost:8083"),
            nessie_url=os.getenv("NESSIE_URL", "http://localhost:19120/api/v1"),
            airflow_url=os.getenv("AIRFLOW_URL", "http://localhost:8081"),
            airflow_user=os.getenv("AIRFLOW_USER", "admin"),
            airflow_pass=os.getenv("AIRFLOW_PASS", "admin"),
            trino_host=os.getenv("TRINO_HOST", "localhost"),
            trino_port=int(os.getenv("TRINO_PORT", "8082")),
            langfuse_host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
        )
```

---

### Pattern 7: Silver SparkApplication Rendering (Agent 4)

```python
# agents/spark_processing/tools.py (excerpt)
import subprocess
import yaml
from pathlib import Path
from datetime import date

_SILVER_TEMPLATE_PATH = Path("dags/templates/silver-batch-app.yaml")


def render_silver_manifest(table_name: str) -> str:
    """Render silver-batch-app.yaml template with Python string replacement."""
    template = _SILVER_TEMPLATE_PATH.read_text()
    today = date.today().isoformat()
    rendered = (
        template
        .replace("{{ params.table_name }}", table_name)
        .replace("{{ params.date }}", today)
    )
    # Give the SparkApplication a unique name to avoid collision with previous runs
    doc = yaml.safe_load(rendered)
    doc["metadata"]["name"] = f"silver-batch-{table_name}"
    return yaml.dump(doc)


def apply_sparkapplication(manifest_yaml: str, namespace: str = "processing") -> str:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-", "-n", namespace],
        input=manifest_yaml, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl apply failed: {result.stderr.strip()}")
    return result.stdout.strip()


def wait_for_sparkapplication(name: str, namespace: str, timeout: int = 600) -> str:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = get_sparkapplication_status(name, namespace)
        if status in ("COMPLETED", "FAILED"):
            return status
        time.sleep(10)
    raise TimeoutError(f"SparkApplication {name} did not complete within {timeout}s")


def get_sparkapplication_status(name: str, namespace: str = "processing") -> str:
    result = subprocess.run(
        ["kubectl", "get", "sparkapplication", name, "-n", namespace,
         "-o", "jsonpath={.status.applicationState.state}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl get sparkapplication failed: {result.stderr.strip()}")
    return result.stdout.strip() or "UNKNOWN"
```

---

### Pattern 8: Airflow DAG Trigger and Poll (Agent 5)

```python
# agents/gold/tools.py (excerpt)
import time
import httpx
from agents.config import Config


def trigger_airflow_dag(dag_id: str, conf: dict, cfg: Config) -> str:
    resp = httpx.post(
        f"{cfg.airflow_url}/api/v1/dags/{dag_id}/dagRuns",
        json={"conf": conf},
        auth=(cfg.airflow_user, cfg.airflow_pass),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["dag_run_id"]


def wait_for_dag_run(dag_id: str, run_id: str, cfg: Config, timeout: int = 900) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = httpx.get(
            f"{cfg.airflow_url}/api/v1/dags/{dag_id}/dagRuns/{run_id}",
            auth=(cfg.airflow_user, cfg.airflow_pass),
            timeout=30,
        )
        resp.raise_for_status()
        state = resp.json()["state"]
        if state in ("success", "failed"):
            return state
        time.sleep(15)
    raise TimeoutError(f"DAG {dag_id}/{run_id} did not complete within {timeout}s")


def query_trino_count(table_fqn: str) -> int:
    """kubectl exec into trino-coordinator to run COUNT query."""
    result = subprocess.run(
        ["kubectl", "exec", "-n", "serving", "deployment/trino-coordinator", "--",
         "trino", "--execute", f"SELECT COUNT(*) FROM {table_fqn}",
         "--catalog", "iceberg", "--output-format", "TSV"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Trino query failed: {result.stderr.strip()}")
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    return int(lines[0]) if lines else 0
```

---

### Pattern 9: Reporter Markdown Format

```python
# agents/reporter/tools.py (excerpt)
from agents.state import E2EState


def format_slack_report(state: E2EState, scores: list[dict], trace: dict) -> str:
    status_icon = "✅" if state["current_status"] == "SUCCESS" else "❌"
    total_s = sum(state["agent_timings"].values())

    lines = [
        f"{status_icon} *E2E Test Run: {state['run_id']}*",
        f"Status: *{state['current_status']}* | Total: *{total_s:.1f}s*",
        "",
        "*Per-Agent Timing:*",
    ]
    for agent, secs in state["agent_timings"].items():
        lines.append(f"  • {agent}: {secs:.1f}s")

    lines += ["", "*Eval Scores:*"]
    for score in scores:
        icon = "✅" if score["value"] >= 1.0 else "❌"
        lines.append(f"  {icon} {score['name']}: {score['value']}")

    if state["current_status"] == "ERROR":
        lines += [
            "",
            "*Root-Cause Analysis:*",
            f"  Error: {state['error_log'] or 'unknown'}",
            f"  Langfuse Trace: {state['langfuse_trace_id']}",
        ]

    return "\n".join(lines)
```

---

## Data Flow

```text
1. CLI: python -m agents.run_e2e --table e2e_test
   │   → initial_state() creates E2EState with run_id + next_agent="infra"
   │   → Langfuse trace initialized; trace_id written to state
   ▼
2. [Orchestrator Node]
   │   → current_status=RUNNING, next_agent="infra" → routes to infra node
   ▼
3. [Agent 2 — Infrastructure]
   │   → kubectl get pods -n {ns} for 5 namespaces (subprocess)
   │   → boto3 head_bucket for warehouse/, bronze/, contracts/
   │   → emit Langfuse score infra_health=1.0
   │   → returns state with next_agent="infra" (unchanged; orchestrator advances)
   ▼
4. [Orchestrator Node] → routes to data_source
   ▼
5. [Agent 3 — Data Source & Contracts]
   │   → psycopg: CREATE TABLE IF NOT EXISTS e2e_test + INSERT 5 rows
   │   → PostgresSchemaInspector.introspect("e2e_test") → columns
   │   → asyncio.run(ODCSGenerator.generate(..., provider=GeminiProvider/FallbackProvider))
   │   → kubectl exec portal-pod -- datacontract-cli validate --stdin
   │   → boto3 put_object → s3://contracts/e2e_test.yaml
   │   → ConnectorActivator.activate("e2e_test") → HTTP 201 or 409
   │   → KafkaConsumer(cdc.public.e2e_test).poll(timeout=60s) → ≥1 message
   │   → emit scores: contract_valid=1.0, cdc_active=1.0
   │   → state.data_contract_path = "s3://contracts/e2e_test.yaml"
   ▼
6. [Orchestrator Node] → routes to spark
   ▼
7. [Agent 4 — Spark Processing]
   │   → check if bronze-streaming SparkApplication already RUNNING
   │   → if not: kubectl apply -f bronze-streaming-app.yaml
   │   → poll status every 10s until RUNNING (streaming; no completion)
   │   → render silver-batch-app.yaml with table_name="e2e_test", date=today
   │   → kubectl apply -f rendered_silver.yaml
   │   → poll status every 10s until COMPLETED or FAILED (timeout: 600s)
   │   → if FAILED: capture driver logs → set current_status=ERROR
   │   → GET Nessie /api/v1/trees/main/entries → verify bronze.e2e_test_valid, silver.e2e_test
   │   → delete silver SparkApplication after verification
   │   → emit score: silver_rows (from Nessie entry count or >0 check)
   ▼
8. [Orchestrator Node] → routes to gold
   ▼
9. [Agent 5 — Gold & Query]
   │   → POST Airflow /api/v1/dags/gold_dbt_dag/dagRuns with conf={table: "e2e_test"}
   │   → poll dag_run state every 15s until success/failed (timeout: 900s)
   │   → if failed: trigger 1 retry via clearTaskInstances + new dagRun
   │   → kubectl exec trino-coordinator: SELECT COUNT(*) FROM iceberg.gold.e2e_test
   │   → if COUNT=0: current_status=ERROR
   │   → optional: GET OpenMetadata lineage for e2e_test
   │   → emit score: gold_rows=COUNT
   ▼
10. [Orchestrator Node] → current_status=SUCCESS → routes to reporter
    ▼
11. [Agent 6 — Reporter]
    │   → Langfuse.fetch_trace(trace_id) → observations
    │   → Langfuse.fetch_scores(trace_id) → scores list
    │   → format_slack_report() → Markdown string
    │   → state.report_markdown = report
    │   → state.current_status = SUCCESS (or ERROR if Fail-Fast path)
    ▼
12. END — graph.invoke() returns final E2EState
    │   → run_e2e.py prints report_markdown to stdout
    │   → exit(0) if SUCCESS, exit(1) if ERROR
```

---

## Integration Points

| External System | Integration Type | Auth | Port-Forward Required |
|-----------------|-----------------|------|----------------------|
| PostgreSQL `sourcedb` | psycopg3 direct connection | user/password | Yes (`5432:5432`) |
| MinIO (S3) | boto3 S3 client | access key / secret | Yes (`9000:9000`) |
| Kafka `cdc.public.*` | kafka-python KafkaConsumer | none (PLAINTEXT) | Yes (`9092:9092`) |
| KafkaConnect (Debezium) | httpx REST API | none | Yes (`8083:8083`) |
| Nessie REST API | httpx GET | none | Yes (`19120:19120`) |
| Airflow REST API | httpx + Basic Auth | admin/admin | Yes (`8081:8080`) |
| Trino | kubectl exec (no JDBC driver needed) | none | No (via kubectl) |
| OpenMetadata | httpx REST API | API key | Yes (optional) |
| Langfuse | Langfuse Python SDK | public/secret key | Yes or external |
| Kubernetes API | kubectl subprocess | kubeconfig | No (host kubectl) |

---

## Testing Strategy

| Test Type | Scope | Files | Tools | Coverage Goal |
|-----------|-------|-------|-------|---------------|
| Unit | Each agent node function, tools, router, state factory | `tests/unit/agents/test_*.py` | `pytest`, `unittest.mock` | 90% of agent logic |
| Integration (existing) | Full pipeline on live KIND cluster | `tests/integration/test_pipeline_e2e.py` | `pytest --timeout=300` | Happy path |
| Agent smoke | `build_graph().invoke(initial_state(...))` with live cluster | `tests/integration/test_agent_squad.py` (new) | `pytest --timeout=1200` | Full E2E run |

**Unit test mocking strategy per agent:**

| Agent | Mock Targets |
|-------|-------------|
| Orchestrator | None needed — pure Python routing |
| Infrastructure | `subprocess.run` (kubectl), `boto3.client.head_bucket` |
| Data Source | `psycopg.connect`, `asyncio.run(ODCSGenerator.generate)`, `ConnectorActivator.activate`, `KafkaConsumer` |
| Spark Processing | `subprocess.run` (kubectl), `httpx.get` (Nessie) |
| Gold | `httpx.post/get` (Airflow), `subprocess.run` (Trino kubectl exec) |
| Reporter | `Langfuse.fetch_trace`, `Langfuse.fetch_scores` |

---

## Error Handling

| Error Type | Agent | Handling | Retry? |
|------------|-------|----------|--------|
| Pod not Ready | Agent 2 | Set `current_status=ERROR`, emit `infra_health=0.0` → Fail-Fast | No |
| MinIO bucket missing | Agent 2 | Set `current_status=ERROR` → Fail-Fast | No |
| DDL failure (table exists) | Agent 3 | `CREATE TABLE IF NOT EXISTS` makes it idempotent | N/A |
| LLM contract gen failure | Agent 3 | Call `FallbackProvider` deterministically — do NOT set ERROR | Fallback |
| datacontract-cli validation failure | Agent 3 | Call `generate_fallback_contract` — retry once | Yes (once) |
| Debezium HTTP 409 | Agent 3 | Treat as success (connector already active) | N/A |
| No Kafka message in 60s | Agent 3 | Set `current_status=ERROR` → Fail-Fast | No |
| SparkApplication FAILED | Agent 4 | Capture driver logs, set `current_status=ERROR` → Fail-Fast | No |
| SparkApplication timeout (300/600s) | Agent 4 | `TimeoutError` → set `current_status=ERROR` → Fail-Fast | No |
| dbt DAG failed (1st attempt) | Agent 5 | Trigger 1 retry via `clear_and_retry_dag` | Yes (once) |
| dbt DAG failed (2nd attempt) | Agent 5 | Set `current_status=ERROR` → Fail-Fast | No |
| Trino COUNT = 0 | Agent 5 | Set `current_status=ERROR`, `error_log="Gold table empty"` → Fail-Fast | No |
| Langfuse unreachable | Agent 6 | Log warning; generate report from state only (no scores section) | No |
| kubectl not on PATH | Any | `FileNotFoundError` → `current_status=ERROR` at first kubectl call | No |

---

## Configuration

| Config Key (env var) | Default | Agent | Description |
|----------------------|---------|-------|-------------|
| `KUBECTL_CONTEXT` | `kind-data-platform` | 2, 4 | kubectl context name |
| `MINIO_ENDPOINT` | `http://localhost:9000` | 2, 3 | MinIO S3 endpoint |
| `MINIO_ACCESS_KEY` | `minio` | 2, 3 | MinIO access key |
| `MINIO_SECRET_KEY` | `minio123` | 2, 3 | MinIO secret key |
| `POSTGRES_HOST` | `localhost` | 3 | PostgreSQL host (port-forwarded) |
| `POSTGRES_PORT` | `5432` | 3 | PostgreSQL port |
| `POSTGRES_DB` | `sourcedb` | 3 | Database name |
| `POSTGRES_USER` | `postgres` | 3 | Database user |
| `POSTGRES_PASSWORD` | `postgres` | 3 | Database password |
| `KAFKA_BOOTSTRAP` | `localhost:9092` | 3 | Kafka bootstrap servers |
| `KAFKA_CONNECT_URL` | `http://localhost:8083` | 3 | KafkaConnect REST URL |
| `NESSIE_URL` | `http://localhost:19120/api/v1` | 4 | Nessie REST catalog URL |
| `AIRFLOW_URL` | `http://localhost:8081` | 5 | Airflow webserver URL |
| `AIRFLOW_USER` | `admin` | 5 | Airflow Basic Auth user |
| `AIRFLOW_PASS` | `admin` | 5 | Airflow Basic Auth password |
| `TRINO_HOST` | `localhost` | 5 | Trino coordinator host |
| `TRINO_PORT` | `8082` | 5 | Trino coordinator port |
| `LANGFUSE_HOST` | `http://localhost:3000` | 4 (observe), 6 | Langfuse server URL |
| `LANGFUSE_PUBLIC_KEY` | `` | all | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | `` | all | Langfuse secret key |
| `GEMINI_API_KEY` | `` | 3 | Gemini API key (optional; falls back to Ollama then FallbackProvider) |

---

## Security Considerations

- Credentials (MinIO, PostgreSQL, Airflow) use environment variables — never hardcoded. `Config.from_env()` reads at runtime.
- `subprocess.run()` calls pass arguments as a list — no shell interpolation, no command injection risk.
- The `agents/` package is a **test harness** — it must never be deployed into production. README must state: "Local KIND only."
- Langfuse keys are optional — if missing, observability degrades gracefully but the run still completes.
- `kubeconfig` for KIND is a local dev credential with cluster-admin on a throwaway cluster. No production RBAC impact.
- Agent 2's guardrail (Read-Only) is enforced by tool design — `tools.py` only contains `kubectl get/describe` verbs; `kubectl apply/delete/scale` are only in Agent 4's tools, scoped to SparkApplication CRDs in `processing` namespace.

---

## Observability

| Aspect | Implementation |
|--------|----------------|
| Langfuse Trace | One trace per `run_e2e` invocation; `trace_id` = `run_id` for correlation |
| Observations | Each agent wraps its work in `observe(trace_id, name=...)` context manager |
| Scores | `emit_score()` called after each eval: `infra_health`, `contract_valid`, `cdc_active`, `silver_rows`, `gold_rows` |
| Agent Timings | `time.monotonic()` delta stored in `state["agent_timings"]` per agent |
| Structured Logging | `print(f"[{agent_name}] ...")` to stdout (simple; adequate for local KIND) |
| Fail-Fast Audit | `error_log` field in state carries the exception message; Reporter surfaces it in RCA |

---

## Pipeline Architecture

### DAG Diagram (Medallion Layers)

```text
[PostgreSQL sourcedb]
        │ CDC (Debezium pgoutput)
        ▼
[Kafka: cdc.public.e2e_test]
        │ Spark Structured Streaming
        ▼
[Bronze Iceberg: nessie.bronze.e2e_test_valid]  ← SparkApplication: bronze-streaming
        │ Spark Batch MERGE
        ▼
[Silver Iceberg: nessie.silver.e2e_test]  ← SparkApplication: silver-batch-e2e_test
        │ dbt (Cosmos, Trino adapter)
        ▼
[Gold Iceberg: nessie.gold.*]  ← Airflow DAG: gold_dbt_dag
        │ SELECT COUNT(*)
        ▼
[Trino: iceberg.gold.*]  ← Agent 5 validates
```

### Data Quality Gates

| Gate | Implemented By | Threshold | Fail-Fast? |
|------|---------------|-----------|-----------|
| All pods Running | Agent 2 | 100% pods in Ready state | Yes |
| Buckets exist | Agent 2 | 3/3 buckets present | Yes |
| Contract ODCS valid | Agent 3 + datacontract-cli | CLI exit code 0 | No (fallback) |
| CDC active | Agent 3 | ≥1 Kafka message in 60s | Yes |
| Silver rows exist | Agent 4 | Nessie entry present | Yes |
| Gold rows > 0 | Agent 5 | COUNT(*) > 0 | Yes |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-25 | design-agent | Initial version from DEFINE_E2E_AGENT_SQUAD.md |

---

## Next Step

**Ready for:** `/build .claude/sdd/features/DESIGN_E2E_AGENT_SQUAD.md`
