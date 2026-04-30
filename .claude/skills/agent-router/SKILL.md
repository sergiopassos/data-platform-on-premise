---
name: agent-router
description: Intelligent agent routing -- automatically matches tasks to the best specialist agent based on file patterns, intent keywords, and domain context. Loaded every session to give Claude explicit routing rules for all 58 AgentSpec agents.
---

# Agent Router

Explicit routing rules for matching tasks to the correct specialist agent. Use these tables as the primary dispatch mechanism instead of relying on description-field heuristics.

## A. File-Pattern Routing

Match the file path, extension, or project context to determine which agent handles the task.

| Pattern | Primary Agent | Secondary Agent | Notes |
|---------|--------------|-----------------|-------|
| `models/**/*.sql`, `macros/**/*.sql`, `dbt_project.yml`, `packages.yml` | `dbt-specialist` | `sql-optimizer` | dbt project files -- models, tests, macros, snapshots |
| `*.sql` (non-dbt context) | `sql-optimizer` | `dbt-specialist` | Standalone SQL, query optimization, window functions |
| `*.py` + spark/pyspark imports | `spark-engineer` | `spark-specialist` | PySpark jobs, DataFrame transformations |
| `*.py` + lambda/handler/s3/boto3 context | `lambda-builder` | `aws-lambda-architect` | AWS Lambda handlers, S3-triggered functions |
| `*.py` + airflow/dag imports | `airflow-specialist` | `pipeline-architect` | Airflow DAG files, TaskFlow API |
| `*.py` + flink/kafka imports | `streaming-engineer` | `spark-streaming-architect` | Stream processing jobs |
| `*.py` + pydantic/fastapi/flask | `python-developer` | `code-reviewer` | Python application code |
| `*.py` (general) | `python-developer` | `code-cleaner` | General Python development |
| `dags/`, `airflow/`, `airflow.cfg` | `airflow-specialist` | `pipeline-architect` | Airflow project structure |
| `template.yaml`, `samconfig.toml`, `buildspec.yml` | `aws-lambda-architect` | `aws-deployer` | SAM/CloudFormation templates |
| `databricks.yml`, `bundle.yml`, DLT notebooks | `lakeflow-pipeline-builder` | `lakeflow-architect` | Databricks Asset Bundles, DLT |
| `docker-compose.yml` + supabase | `supabase-specialist` | -- | Supabase local dev setup |
| `*.tf`, `*.tfvars`, `terraform/` | `ci-cd-specialist` | `aws-deployer` | Terraform IaC modules |
| `.github/workflows/`, `azure-pipelines.yml` | `ci-cd-specialist` | `shell-script-specialist` | CI/CD pipeline definitions |
| `*.sh`, `Makefile`, `justfile` | `shell-script-specialist` | `ci-cd-specialist` | Build scripts, automation |
| Fabric notebooks, `*.Notebook/` | `fabric-pipeline-developer` | `fabric-architect` | Microsoft Fabric PySpark notebooks |
| `*.md` (KB domains) | `kb-architect` | `code-documenter` | Knowledge base domain files |
| `tests/`, `test_*.py`, `*_test.py` | `test-generator` | `data-quality-analyst` | pytest files and fixtures |
| `contracts/`, `*contract*.yaml` | `data-contracts-engineer` | `data-quality-analyst` | ODCS data contracts |
| `great_expectations/`, `soda/` | `data-quality-analyst` | `data-contracts-engineer` | Data quality suites |
| BRAINSTORM/DEFINE/DESIGN/BUILD docs | Corresponding workflow agent | `iterate-agent` | SDD phase documents |

## B. Intent-Based Routing

Match user intent from keywords and phrases to the correct agent.

### Data Modeling and Schema

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| schema, star schema, snowflake schema, dimensional model, SCD, grain, fact table, dimension | `schema-designer` | `dbt-specialist` (implementation) |
| medallion, bronze, silver, gold, layer design, data quality progression | `medallion-architect` | `schema-designer` (modeling detail) |
| data model, ERD, entity relationship, normalization | `schema-designer` | `lakehouse-architect` (storage) |

### Pipeline and Orchestration

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| pipeline, DAG, ETL, ELT, orchestration, schedule, dependency | `pipeline-architect` | `airflow-specialist` (Airflow-specific) |
| airflow, DAG, TaskFlow, operator, sensor, XCom, Airflow 3 | `airflow-specialist` | `pipeline-architect` (design decisions) |
| streaming, kafka, flink, CDC, real-time, event-driven | `streaming-engineer` | `spark-streaming-architect` (Spark Streaming) |
| structured streaming, kafka + spark, micro-batch | `spark-streaming-architect` | `streaming-engineer` (Flink/Kafka-native) |

### Data Processing

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| spark, pyspark, dataframe, distributed, partition, shuffle | `spark-engineer` | `spark-specialist` (architecture) |
| spark tuning, memory, AQE, broadcast, partition count | `spark-performance-analyzer` | `spark-troubleshooter` (errors) |
| OOM, data skew, shuffle failure, spark error, stage failed | `spark-troubleshooter` | `spark-performance-analyzer` (tuning) |
| dbt, model, macro, incremental, snapshot, ref, source | `dbt-specialist` | `schema-designer` (modeling) |
| SQL, query, window function, CTE, subquery, explain plan | `sql-optimizer` | `dbt-specialist` (dbt context) |

### Lakehouse and Storage

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| lakehouse, iceberg, delta lake, hudi, catalog, table format | `lakehouse-architect` | `data-platform-engineer` (infra) |
| lakeflow, DLT, declarative pipeline, materialized view, streaming table | `lakeflow-specialist` | `lakeflow-architect` (architecture) |
| DLT troubleshooting, CDC in DLT, SCD Type 2 in DLT | `lakeflow-expert` | `lakeflow-pipeline-builder` (creation) |
| DLT pipeline creation, quality expectations, DLT notebooks | `lakeflow-pipeline-builder` | `lakeflow-architect` (design) |

### Data Quality and Contracts

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| data quality, expectations, validation, freshness, anomaly | `data-quality-analyst` | `data-contracts-engineer` (SLAs) |
| contract, SLA, ODCS, schema governance, ownership | `data-contracts-engineer` | `data-quality-analyst` (enforcement) |
| pytest, test, fixture, mock, coverage, unit test | `test-generator` | `data-quality-analyst` (data tests) |

### Cloud and Infrastructure

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| AWS, Lambda, S3, Glue, Redshift, MWAA, serverless | `aws-data-architect` | `aws-deployer` (deployment) |
| SAM, CloudFormation, IAM policy, Lambda deploy | `aws-lambda-architect` | `lambda-builder` (handler code) |
| Lambda handler, S3 trigger, boto3, event processing | `lambda-builder` | `aws-lambda-architect` (SAM template) |
| deploy, release, CI/CD, terraform, pipeline automation | `ci-cd-specialist` | `aws-deployer` (AWS-specific) |
| GCP, BigQuery, Cloud Run, Pub/Sub, Dataflow, Vertex | `gcp-data-architect` | `ai-data-engineer-gcp` (AI on GCP) |
| Snowflake, Databricks, BigQuery, cost, warehouse, optimize | `data-platform-engineer` | `lakehouse-architect` (storage) |
| supabase, pgvector, RLS, edge function, realtime | `supabase-specialist` | -- |

### Microsoft Fabric

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| fabric, OneLake, workspace, lakehouse (Fabric context) | `fabric-architect` | `fabric-pipeline-developer` (pipelines) |
| fabric pipeline, Data Factory, Dataflow Gen2, notebook | `fabric-pipeline-developer` | `spark-engineer` (PySpark) |
| fabric AI, Copilot, ML model, AI Skills, Azure OpenAI | `fabric-ai-specialist` | `fabric-architect` (architecture) |
| fabric CI/CD, Git integration, deployment pipeline | `fabric-cicd-specialist` | `ci-cd-specialist` (general CI/CD) |
| fabric monitoring, KQL, diagnostic, dashboard | `fabric-logging-specialist` | `fabric-architect` (design) |
| fabric security, RLS, permissions, data masking, encryption | `fabric-security-specialist` | `fabric-architect` (architecture) |

### AI and ML

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| RAG, embedding, vector, feature store, similarity search | `ai-data-engineer` | `qdrant-specialist` (Qdrant-specific) |
| qdrant, collection, vector index, HNSW, payload filter | `qdrant-specialist` | `ai-data-engineer` (general RAG) |
| multi-agent, agentic workflow, orchestration (AI context) | `genai-architect` | `ai-data-engineer` (data layer) |
| prompt, extraction, structured output, few-shot | `ai-prompt-specialist` | `llm-specialist` (advanced) |
| chain-of-thought, tool use, complex prompting, guardrails | `llm-specialist` | `ai-prompt-specialist` (basic) |
| Gemini, Vertex AI, multi-modal, document extraction | `ai-prompt-specialist-gcp` | `gcp-data-architect` (GCP infra) |
| cloud AI pipeline, ML ops, AI/ML on cloud | `ai-data-engineer-cloud` | `ai-data-engineer-gcp` (GCP-specific) |
| GCP serverless, Cloud Functions + BigQuery | `ai-data-engineer-gcp` | `gcp-data-architect` (architecture) |

### Code Quality and Dev Tools

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| review, audit, check code, security review | `code-reviewer` | `code-cleaner` (refactoring) |
| clean, refactor, DRY, remove comments, simplify | `code-cleaner` | `code-reviewer` (review first) |
| document, README, API docs, docstring | `code-documenter` | `kb-architect` (KB domains) |
| explore, understand, onboard, codebase structure | `codebase-explorer` | `the-planner` (architecture) |
| shell, bash, script, automation, deployment script | `shell-script-specialist` | `ci-cd-specialist` (CI/CD) |
| meeting, transcript, decisions, action items | `meeting-analyst` | -- |
| prompt.md, agent matching, SDD-lite | `prompt-crafter` | -- |

### Architecture and Planning

| Intent Keywords | Agent | Escalates To |
|----------------|-------|-------------|
| architecture, design, plan, system design, RFC | `the-planner` | `genai-architect` (AI systems) |
| knowledge base, KB domain, create KB, audit KB | `kb-architect` | `code-documenter` (docs) |
| migrate, legacy, modernize, ETL to ELT | `dbt-specialist` + `spark-engineer` | `pipeline-architect` (orchestration) |

### SDD Workflow

| Intent Keywords | Agent |
|----------------|-------|
| brainstorm, explore idea, what if | `brainstorm-agent` |
| define, requirements, scope, acceptance criteria | `define-agent` |
| design, architecture, file manifest, technical spec | `design-agent` |
| build, implement, create, scaffold | `build-agent` |
| ship, archive, release, lessons learned | `ship-agent` |
| iterate, update, cascade, modify existing | `iterate-agent` |

### Visual and Documentation

| Intent Keywords | Route To |
|----------------|----------|
| slide, diagram, visual, HTML page, architecture diagram | `visual-explainer` skill |
| excalidraw, whiteboard, sketch | `excalidraw-diagram` skill |

## C. Model Routing Strategy

Cost-optimize by matching task complexity to model capability.

### Haiku -- 70% of tasks (fast, cheap)

Use for: file exploration, pattern matching, documentation lookup, simple code generation, search and summarize, codebase navigation, KB index scans, formatting, boilerplate scaffolding.

Typical agents: `codebase-explorer`, `code-documenter`, `kb-architect` (reads), `prompt-crafter`, `meeting-analyst`.

### Sonnet -- 20% of tasks (balanced)

Use for: code review, feature implementation, refactoring, debugging, API development, dbt models, Spark jobs, SQL optimization, pipeline design, test generation, data quality rules, cloud service configuration.

Typical agents: Most T1 and T2 agents -- `dbt-specialist`, `spark-engineer`, `sql-optimizer`, `pipeline-architect`, `code-reviewer`, `test-generator`, `airflow-specialist`, `python-developer`, `data-quality-analyst`, `schema-designer`, `streaming-engineer`, `lakehouse-architect`, all `lakeflow-*` agents, all `fabric-*` pipeline/logging/CI agents.

### Opus -- 10% of tasks (complex, expensive)

Use for: architectural decisions, complex system design, multi-file refactoring, critical production bugs, security reviews, cross-domain orchestration, agentic workflow design.

Typical agents: `the-planner`, `genai-architect`, `design-agent`, `build-agent`, `supabase-specialist`, `fabric-architect`, `fabric-security-specialist`, `spark-specialist`, `llm-specialist`, `qdrant-specialist`.

### Override Rules

- If agent frontmatter declares `model: opus`, always use opus regardless of task simplicity
- If a task touches production data or security (RLS, IAM, encryption), escalate to opus
- If confidence drops below 0.75 on sonnet, retry the same task on opus before asking user

## D. Composition Hints

When to run agents in parallel, serial, or background.

### Parallel (independent work, no shared files)

- `dbt-specialist` + `test-generator` -- model creation and test generation on different files
- `code-reviewer` + `data-quality-analyst` -- reviewing Python code and SQL quality separately
- `schema-designer` + `pipeline-architect` -- data model design and DAG design in parallel
- `codebase-explorer` + `kb-architect` -- exploring codebase and auditing KB simultaneously
- Multiple cloud agents on different services (e.g., `aws-data-architect` + `gcp-data-architect`)

### Serial (output feeds next step)

- `schema-designer` then `dbt-specialist` -- design the model, then implement it
- `pipeline-architect` then `airflow-specialist` -- design the DAG, then build it
- `code-reviewer` then `code-cleaner` -- identify issues, then fix them
- `define-agent` then `design-agent` then `build-agent` -- SDD workflow phases
- `the-planner` then domain specialists -- architecture first, then implementation
- `data-contracts-engineer` then `data-quality-analyst` -- define contract, then enforce it

### Background (non-blocking)

- `codebase-explorer` -- scanning project structure while user works
- `code-documenter` -- generating docs after implementation is done
- `kb-architect` -- auditing KB domains on schedule
- `meeting-analyst` -- processing transcript while discussion continues

## E. Quick Dispatch Reference

When in doubt, use this simplified lookup.

| Domain | Go-To Agent |
|--------|-------------|
| dbt | `dbt-specialist` |
| Spark | `spark-engineer` |
| SQL | `sql-optimizer` |
| Airflow | `airflow-specialist` |
| Streaming | `streaming-engineer` |
| Lakehouse | `lakehouse-architect` |
| Lakeflow/DLT | `lakeflow-pipeline-builder` |
| Data Quality | `data-quality-analyst` |
| Data Contracts | `data-contracts-engineer` |
| Schema Design | `schema-designer` |
| Medallion | `medallion-architect` |
| AWS | `aws-data-architect` |
| GCP | `gcp-data-architect` |
| Fabric | `fabric-architect` |
| Supabase | `supabase-specialist` |
| Python | `python-developer` |
| Testing | `test-generator` |
| Code Review | `code-reviewer` |
| RAG/Vectors | `ai-data-engineer` |
| Prompts/LLM | `llm-specialist` |
| CI/CD | `ci-cd-specialist` |
| Architecture | `the-planner` |
| SDD Workflow | Phase-specific agent |
