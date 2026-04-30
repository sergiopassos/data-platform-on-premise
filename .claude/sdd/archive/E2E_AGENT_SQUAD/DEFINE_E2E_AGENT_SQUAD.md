# DEFINE: E2E Agent Squad — 6-Agent MAS for Data Platform Testing

> A 6-agent autonomous squad that executes a full Medallion E2E test on KIND Kubernetes — Postgres CDC → Bronze/Silver Iceberg → Gold dbt → Trino — with Langfuse observability and Fail-Fast routing.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | E2E_AGENT_SQUAD |
| **Date** | 2026-04-25 |
| **Author** | define-agent |
| **Status** | ✅ Shipped |
| **Clarity Score** | 15/15 |
| **Source** | `BRAINSTORM_E2E_AGENT_SQUAD.md` + `agents_spefication.md` |

---

## Problem Statement

Platform engineers have no automated way to verify that the full Medallion data pipeline (Postgres CDC → Kafka → Bronze Iceberg → Silver MERGE → Gold dbt → Trino) works end-to-end after each deployment to the KIND cluster — manual verification is error-prone, slow, and requires deep multi-system knowledge. A 6-agent autonomous squad must run this full test without human intervention, emit Langfuse observability scores, and produce a Markdown RCA report on any failure.

---

## Target Users

| User | Role | Pain Point |
|------|------|------------|
| Platform Engineer | Data infrastructure owner | Must manually verify 7+ services after each deploy; takes 30-45 min; easy to miss a broken layer |
| CI/CD Pipeline | Automated post-deploy gate | No smoke test that covers the full Medallion flow; only unit tests exist today |
| Data Producer | Onboards new tables via Chainlit portal | No way to verify a new table's full pipeline path without running manual Trino queries |

---

## Goals

| Priority | Goal |
|----------|------|
| **MUST** | Agent squad runs `python -m agents.run_e2e` from CLI and completes the full Medallion flow without human input |
| **MUST** | Fail-Fast: any agent error immediately routes to the Reporter agent; no further agents execute |
| **MUST** | Langfuse observability: all agent executions, tool calls, and scores captured under a single shared `trace_id` |
| **MUST** | Reporter generates a Markdown report with RCA, per-agent timing, and pass/fail per eval |
| **SHOULD** | Full E2E run completes in under 20 minutes on KIND 3-node cluster |
| **SHOULD** | All tool calls are idempotent — running the squad twice does not corrupt the platform |
| **COULD** | OpenMetadata lineage confirmation in Agent 5 (optional — skip if pod not Running) |
| **COULD** | Parallel agent execution (infra + data source concurrently) to reduce total run time |

---

## Success Criteria

- [ ] `python -m agents.run_e2e --table e2e_test` exits with code 0 when platform is healthy
- [ ] Bronze Iceberg table `bronze.{table}_valid` visible in Nessie within 5 minutes of CDC activation
- [ ] Silver Iceberg table `silver.{table}` produced via MERGE SparkApplication (COMPLETED state)
- [ ] `gold_dbt_dag` Airflow DAG run completes with state `success`
- [ ] `SELECT COUNT(*) FROM iceberg.gold.{table}` in Trino returns value > 0
- [ ] Langfuse trace contains scores: `infra_health`, `contract_valid`, `cdc_active`, `silver_rows`, `gold_rows`
- [ ] On any agent failure, Reporter generates Markdown report with RCA within 10 seconds of error detection
- [ ] Agent 3 contract generation falls back to deterministic generator if LLM call fails — no run abortion
- [ ] Debezium connector activation is idempotent (HTTP 409 treated as success)
- [ ] Full run completes in ≤ 20 minutes on KIND 3-node cluster

---

## Acceptance Tests

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AT-001 | Happy path — full Medallion run | KIND cluster healthy, all pods Running, Langfuse reachable | `python -m agents.run_e2e --table e2e_test` is executed | All 6 agents complete; `current_status="SUCCESS"`; Gold COUNT > 0; Langfuse trace has 5 scores |
| AT-002 | Fail-Fast on infra failure | One or more pods in `streaming` namespace not Running | Agent 2 executes health checks | Agent 2 sets `current_status="ERROR"`; Orchestrator skips Agents 3-5; Agent 6 generates RCA report |
| AT-003 | LLM contract generation failure | LLM provider unavailable (no API key, Ollama down) | Agent 3 calls `generate_odcs_contract` | Fallback `generate_fallback_contract` is called; run continues without error |
| AT-004 | Debezium connector already exists | Connector was created in a previous run | Agent 3 calls `activate_debezium_connector` | HTTP 409 received; treated as success; `cdc_active` score = 1.0 |
| AT-005 | Spark job failure — bronze-streaming | SparkApplication enters FAILED state | Agent 4 waits for `bronze-streaming-app` | Driver logs captured; `current_status="ERROR"`; Fail-Fast to Reporter with log excerpt |
| AT-006 | dbt DAG retry | `gold_dbt_dag` DAG run state = `failed` on first attempt | Agent 5 polls DAG run status | Agent 5 triggers exactly 1 retry; if retry also fails, `current_status="ERROR"` |
| AT-007 | Gold table empty | dbt DAG succeeds but Gold table has 0 rows | Agent 5 runs `SELECT COUNT(*) FROM iceberg.gold.{table}` | Returns 0; `current_status="ERROR"`; error_log set to "Gold table empty: {table}" |
| AT-008 | Idempotency — second run | Squad was run successfully 1 hour ago; table and connector exist | `python -m agents.run_e2e --table e2e_test` is executed again | Run completes without errors; no duplicate data corruption; Debezium 409 handled gracefully |

---

## Out of Scope

- Parallel agent execution (Agents 2 and 3 running concurrently) — deferred to v2
- Slack/Teams webhook integration — Reporter outputs Markdown string; caller handles posting
- Multi-table simultaneous E2E runs — single table per run is sufficient for smoke test
- Kafka consumer group lag monitoring — out of scope for basic E2E pass/fail
- OpenMetadata as a required gate — optional check only; pod may not be running
- Agent self-modification of pipeline code or Kubernetes manifests
- Production cluster testing — KIND local dev only
- Agent retry logic beyond Fail-Fast and the 1 dbt DAG retry — one run = one truth
- dbt test assertion validation (beyond COUNT > 0) — smoke test scope only
- Per-agent separate LLM reasoning — only Agent 3 uses LLM; all others are deterministic

---

## Constraints

| Type | Constraint | Impact |
|------|------------|--------|
| Technical | Spark image `data-platform/spark:3.5.1` must be pre-loaded into KIND nodes (`kind load docker-image`) | Agent 4 will fail at pod scheduling if image not present; imagePullPolicy: Never |
| Technical | Agent 3 requires `GEMINI_API_KEY` OR Ollama running for LLM contract generation | Falls back to deterministic generator if neither — no hard dependency |
| Technical | LangGraph requires Python ≥ 3.11 | Must run in Python 3.11 virtualenv; matches Airflow worker Python version |
| Technical | Kubernetes access via `kubectl` from host machine | kubeconfig must point at KIND cluster context `kind-data-platform`; no in-cluster pod needed |
| Technical | All tool calls that touch K8s use `subprocess` + kubeconfig — not in-cluster ServiceAccount | Agent runs from host; no RBAC ServiceAccount provisioning required |
| Environment | Langfuse credentials must be set: `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Agent 6 (Reporter) cannot fetch trace without these; run still completes but report is degraded |
| Environment | MinIO accessible at `localhost:9000` (port-forwarded) OR `minio.infra.svc.cluster.local:9000` | Agents 2 and 3 need S3 bucket access; boto3 endpoint must be configured |
| Architecture | Orchestrator (Agent 1) is deterministic — NEVER calls an LLM | Routing is rule-based on `next_agent` + `current_status` fields; no LLM tokens consumed for routing |
| Architecture | State is a `TypedDict` (`E2EState`), not a raw dict | Prevents `KeyError` at runtime; IDE autocomplete works; passed by value between LangGraph nodes |

---

## Technical Context

| Aspect | Value | Notes |
|--------|-------|-------|
| **Deployment Location** | `agents/` (new top-level package in repo root) | Separate from `portal/` — this is a test harness, not a portal feature |
| **KB Domains** | `on-premise-k8s`, `lakeflow`, `airflow`, `langfuse` | Consult for K8s tool patterns, Iceberg table ops, Airflow REST API, Langfuse SDK usage |
| **IaC Impact** | None — reads existing cluster resources only | No new Kubernetes manifests, Helm charts, or ArgoCD apps needed |
| **Tool Reuse** | `portal/agent/connector_activator.py`, `portal/agent/odcs_generator.py`, `portal/agent/schema_inspector.py` | Import directly; do not duplicate — these are already tested |
| **Existing Manifests** | `spark/applications/bronze-streaming-app.yaml`, `dags/templates/silver-batch-app.yaml` | Agent 4 applies these; may need to template `table_name` into silver manifest |
| **Framework** | LangGraph `StateGraph` — one node per agent | `StateGraph(E2EState)` with conditional edges for Fail-Fast routing |
| **Observability** | Langfuse Python SDK — one trace per E2E run | `Langfuse.trace()` at start; each agent adds `observation` spans and `score` events |

---

## Data Contract

### Source Inventory

| Source | Type | Volume (per E2E run) | Freshness | Owner |
|--------|------|----------------------|-----------|-------|
| PostgreSQL `sourcedb.e2e_test_{run_id}` | OLTP | 5 seed rows | Real-time via CDC | Agent 3 creates it |
| Kafka `cdc.public.e2e_test_{run_id}` | Stream | ~5 CDC events | < 1s latency | Debezium connector |
| Iceberg Bronze `bronze.e2e_test_{run_id}_valid` | Data lake | ~5 rows | Spark streaming batch | Agent 4 |
| Iceberg Silver `silver.e2e_test_{run_id}` | Data lake | ~5 rows | Spark batch MERGE | Agent 4 |
| Iceberg Gold `gold.{dbt_model}` | Data mart | Aggregated | dbt triggered by DAG | Agent 5 |

### Shared State Schema (E2EState TypedDict)

| Field | Type | Set By | Description |
|-------|------|--------|-------------|
| `run_id` | `str` | Agent 1 | Unique run identifier, e.g. `e2e-20260425-a1b2` |
| `langfuse_trace_id` | `str` | Agent 1 | Langfuse trace ID for this run |
| `current_status` | `Literal["RUNNING","ERROR","SUCCESS"]` | Any agent | Current pipeline status |
| `table_name` | `str` | Agent 3 | Test table name, e.g. `e2e_test_a1b2` |
| `data_contract_path` | `str` | Agent 3 | S3 URI: `s3://contracts/{table}.yaml` |
| `kafka_topic` | `str` | Agent 3 | `cdc.public.{table_name}` |
| `error_log` | `str \| None` | Any agent | Error description on failure |
| `next_agent` | `str` | Agent 1 | Next agent to invoke |
| `agent_timings` | `dict[str, float]` | Each agent | Wall-clock seconds per agent |
| `scores` | `dict[str, float]` | Each agent | Langfuse eval scores |
| `report_markdown` | `str \| None` | Agent 6 | Final Markdown report |

### Freshness SLAs

| Layer | Target | Measurement |
|-------|--------|-------------|
| CDC → Kafka | < 1 second | Kafka message timestamp vs. INSERT timestamp |
| Kafka → Bronze Iceberg | < 3 minutes | Spark streaming micro-batch commit |
| Bronze → Silver | < 5 minutes | SparkApplication COMPLETED state |
| Silver → Gold | < 10 minutes | dbt DAG `success` state |

### Lineage Requirements

- Iceberg table metadata must be registered in Nessie REST catalog (`http://nessie.infra:19120/api/v1`) for both Bronze and Silver layers
- Optional: OpenMetadata lineage graph from source table → Gold model (checked by Agent 5 if pod is Running)

---

## Assumptions

| ID | Assumption | If Wrong, Impact | Validated? |
|----|------------|------------------|------------|
| A-001 | KIND cluster is already running with all pods in Running/Completed state before squad starts | Agent 2 catches this and Fail-Fasts immediately | [x] Agent 2 validates this explicitly |
| A-002 | Spark image `data-platform/spark:3.5.1` is pre-loaded into KIND nodes | Agent 4 SparkApplication pods will fail to pull → FAILED state → Fail-Fast | [ ] Must document in README |
| A-003 | Airflow `gold_dbt_dag` DAG exists and targets the correct Gold dbt model for the test table | Agent 5 will receive 404 or DAG will succeed with no rows → AT-007 catches this | [ ] Verify DAG is parameterized by table_name |
| A-004 | PostgreSQL `postgres` user has CREATE TABLE and INSERT privileges on `sourcedb` | Agent 3 will fail on DDL execution | [x] Confirmed by existing portal usage |
| A-005 | LangGraph `StateGraph` supports conditional edges for Fail-Fast routing | Must verify LangGraph version ≥ 0.2 is installed | [ ] Check `requirements.txt` |
| A-006 | Langfuse instance (in `serving` namespace or external) is reachable during the run | Reporter report will be degraded (state-only, no Langfuse scores) but run still completes | [ ] Graceful degradation needed |

---

## Agent Responsibility Matrix

| Agent | # | LLM? | Read-Only? | Fail-Fast Trigger? | Langfuse Scores |
|-------|---|------|-----------|-------------------|-----------------|
| Orchestrator | 1 | No | N/A (router) | Yes — routes on ERROR | None |
| Infrastructure | 2 | No | Yes | Yes | `infra_health` |
| Data Source & Contracts | 3 | Yes (ODCS gen) | No | Yes | `contract_valid`, `cdc_active` |
| Spark Processing | 4 | No | No (applies CRDs) | Yes | `silver_rows` |
| Gold & Query | 5 | No | No (triggers DAG) | Yes | `gold_rows` |
| Reporter | 6 | No | Yes | Never (final agent) | None (reads scores) |

---

## Clarity Score Breakdown

| Element | Score (0-3) | Notes |
|---------|-------------|-------|
| Problem | 3 | Specific, measurable pain: no automated E2E post-deploy verification |
| Users | 3 | Three personas identified with concrete pain points |
| Goals | 3 | MoSCoW-prioritized; all MUST goals are testable |
| Success | 3 | 10 measurable criteria with concrete numbers (COUNT > 0, ≤ 20 min, HTTP 201/409) |
| Scope | 3 | 10 explicit out-of-scope items; agent responsibility matrix clarifies boundaries |
| **Total** | **15/15** | |

---

## Open Questions

None — ready for Design.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-25 | define-agent | Initial version from BRAINSTORM_E2E_AGENT_SQUAD.md + agents_spefication.md |

---

## Next Step

**Ready for:** `/design .claude/sdd/features/DEFINE_E2E_AGENT_SQUAD.md`
