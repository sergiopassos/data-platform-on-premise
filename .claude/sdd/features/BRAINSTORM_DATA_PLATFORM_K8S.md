# BRAINSTORM: Data Platform On-Premise com Kubernetes

> Exploratory session to clarify intent and approach before requirements capture

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | DATA_PLATFORM_K8S |
| **Date** | 2026-04-21 |
| **Author** | brainstorm-agent |
| **Status** | Ready for Define |
| **Last Updated** | 2026-04-21 (iterate-agent v1.1) |

---

## Initial Idea

**Raw Input:** Construção de uma plataforma de dados on-premise com Kubernetes usando ferramentas opensource, testada localmente com KIND. Arquitetura Medallion (Bronze/Silver/Gold) com ingestão via Kafka, contratos de dados, Iceberg como formato de tabela, MinIO como storage, Airflow para orquestração, Spark Operator para processamento, Trino para consultas SQL e OpenMetadata para governança. Inclui portal self-service via Chainlit onde produtores de dados registram tabelas (PostgreSQL externo) e um AI Agent (Ollama + modelo leve) gera o contrato ODCS automaticamente e ativa o CDC via Debezium/KafkaConnect.

**Context Gathered:**
- Projeto novo, sem código existente — greenfield
- Máquina local: 31GB RAM, 16 CPUs, 353GB disco livre — suficiente para stack completa
- KBs disponíveis relevantes: `data-quality`, `airflow`, `data-modeling`
- KBs ausentes que precisam ser criados antes do /define

**Technical Context Observed (for Define):**

| Aspect | Observation | Implication |
|--------|-------------|-------------|
| Infra | KIND (Kubernetes IN Docker) local | Helm charts + Operators como estratégia de deploy |
| Storage | MinIO (S3-compat) + Apache Iceberg | Trino acessa Iceberg via MinIO catalog |
| Streaming | Kafka via Strimzi Operator | CRDs nativos K8s para Kafka, Topics, Connect |
| Processamento | Spark Operator | CRD SparkApplication para jobs batch/streaming |
| GitOps / Deploy | ArgoCD | Sincroniza Helm charts e manifests do Git para o cluster KIND automaticamente |
| Orquestração | Airflow com KubernetesExecutor + Cosmos | DAGs disparam pods K8s; Cosmos orquestra dbt natively no Airflow |
| Transformações Gold | dbt (Cosmos provider no Airflow) | Views de negócio SQL-first no Gold layer; Cosmos converte models dbt em Tasks Airflow |
| Governança | OpenMetadata | Catálogo, lineage automática, contratos |
| Query Engine | Trino | SQL federado sobre Iceberg/MinIO |
| Contratos | datacontract-cli (ODCS v3.1) | Validação pré-Bronze, separação valid/invalid |
| Self-service UI | Chainlit (Python) | Portal para produtores registrarem tabelas para ingestão |
| AI Agent | Ollama + llama3.2:3b ou qwen2.5:3b | Introspecta schema Postgres → gera contrato ODCS → ativa CDC |
| CDC Source | PostgreSQL (Docker externo ao KIND) | Source system simulado para testes; não pertence à plataforma |
| CDC Engine | Debezium via KafkaConnect (Strimzi) | Captura mudanças do Postgres externo e publica no Kafka |

---

## Discovery Questions & Answers

| # | Question | Answer | Impact |
|---|----------|--------|--------|
| 1 | Qual o comportamento para dados inválidos (bronze/invalid)? | Ainda não definido — decidir no design | Dead-letter pattern, retry e alertas serão opções no /design |
| 2 | Estratégia de deduplicação no Silver? | CDC: dedup pela PK do schema da fonte. Arquivos (csv/xlsx/parquet): definir chave candidata no contrato de dados | Silver espelha CDC com MERGE por PK; arquivos precisam de PK sintética ou declarada no contrato |
| 3 | Escopo do MVP local? | Pipeline completo end-to-end com todos os componentes + Trino | Todos os componentes sobem juntos no KIND desde o dia 1 |
| 4 | Recursos da máquina? | 31GB RAM, 16 CPUs, 353GB disco | Sem restrição — stack completa sem priorização |
| 5 | Qual LLM para o AI Agent de geração de contratos? | Modelo leve local via Ollama (llama3.2:3b ou qwen2.5:3b) — tarefa simples de schema→YAML | Sem API externa, roda como pod no KIND, suficiente para mapeamento estruturado |
| 6 | Onde roda o PostgreSQL (source de teste)? | Docker container externo ao KIND — source system simulado, não pertence à plataforma | Debezium/KafkaConnect no KIND se conecta ao Postgres Docker via rede local |

---

## Sample Data Inventory

| Type | Location | Count | Notes |
|------|----------|-------|-------|
| Input files | N/A — greenfield | 0 | Serão criados durante o /build |
| CDC examples | N/A | 0 | Simular com Kafka producer de teste |
| Ground truth | N/A | 0 | Contratos ODCS definem o schema esperado |
| Related code | N/A | 0 | Projeto novo |

**Como samples serão usados:**
- Contratos ODCS servirão como ground truth para validação
- Dados sintéticos serão gerados para testes de cada camada
- Scripts de seed para MinIO/Iceberg nos testes de integração

---

## Approaches Explored

### Approach A: Helm Charts + Operators no KIND ⭐ Selecionado

**Description:** Cada componente instalado via Helm com operators K8s nativos. Strimzi para Kafka, Spark Operator, Airflow com KubernetesExecutor, MinIO operator, Trino Helm chart, OpenMetadata Helm chart.

**Pros:**
- Reproduzível via `helm install` — GitOps-ready desde o início
- Mais próximo de produção on-premise real
- Operators gerenciam lifecycle dos componentes (rolling updates, config changes)
- KIND multi-node simula um cluster real

**Cons:**
- Pesado na primeira instalação (muitas imagens Docker)
- Curva de aprendizado com CRDs dos operators
- Debugging mais complexo que Docker Compose

**Why Recommended:** Testa Kubernetes desde o dia 1, elimina divergência entre local e produção, e os Helm charts são o padrão de instalação de todos esses projetos.

---

### Approach B: Docker Compose primeiro, KIND depois

**Description:** Valida a stack em Compose, depois migra para K8s.

**Pros:**
- Mais leve para começar
- Fácil de debugar com logs diretos

**Cons:**
- Dois ambientes para manter (Compose + K8s)
- Migração tem atrito — configurações não são portáveis diretamente
- Não testa integração K8s (Spark Operator CRDs, KubernetesExecutor) desde o início

---

### Approach C: KIND com node pools por domínio

**Description:** KIND com múltiplos worker nodes, cada domínio de dados em namespace dedicado com resource quotas.

**Pros:**
- Isolamento real por domínio
- Simula multi-tenant desde o início

**Cons:**
- Complexidade operacional alta para MVP
- Recursos insuficientes para múltiplos node pools completos localmente

---

## Data Engineering Context

### Source Systems

> PostgreSQL é um source system externo — não pertence à plataforma. Roda como Docker container fora do KIND para simular um sistema de origem real.

| Source | Localização | Type | Volume Estimate | Freshness |
|--------|-------------|------|-----------------|-----------|
| PostgreSQL | Docker externo ao KIND | CDC via Debezium | Tabelas de teste | Real-time streaming |
| APIs externas | Externas | REST/HTTP → Kafka producer | Desconhecido | Near real-time |
| Arquivos | Upload via Chainlit ou S3/MinIO | CSV, Parquet, XLSX | Batch | Agendado via Airflow |

### Data Flow

```text
═══════════════════════════════════════════════════════════════
  SELF-SERVICE INGESTION PORTAL (dentro do KIND)
═══════════════════════════════════════════════════════════════

[Produtor de Dados]
       │
       ▼
[Chainlit UI]  ← Interface conversacional Python
       │  informa tabela Postgres ou faz upload de arquivo
       ▼
[AI Agent - Ollama llama3.2:3b]
       │  1. Introspecta schema da tabela (pg catalog)
       │  2. Gera contrato ODCS v3.1 automaticamente
       │  3. Armazena contrato no repositório
       │  4. Ativa Debezium connector via KafkaConnect REST API
       │
       ▼
[KafkaConnect - Debezium]  ←─── conecta ao Postgres Docker externo
       │  captura CDC (INSERT/UPDATE/DELETE)
       ▼

═══════════════════════════════════════════════════════════════
  PIPELINE MEDALLION (dentro do KIND)
═══════════════════════════════════════════════════════════════

[APIs / CDC / Files]
       │
       ▼
   [Kafka]  ← Strimzi Operator
       │
       ▼
[datacontract-cli]  ← Validação ODCS v3.1
       │
   ┌───┴────────┐
   ▼             ▼
[Bronze/valid]  [Bronze/invalid]  ← Iceberg tables on MinIO
   │
   ▼
[Silver]  ← Spark Operator: MERGE por PK (CDC) ou PK declarada (files)
   │
   ▼
[Gold]  ← dbt (via Cosmos no Airflow): views/models SQL de negócio sobre Silver Iceberg
   │       Spark Operator: agregações pesadas, dedup final quando necessário
   │
   ▼
[Trino]  ← Query engine SQL sobre Iceberg/MinIO (lê tabelas Gold dbt)
   │
   ▼
[Consumidores: BI, APIs, Data Science]

[OpenMetadata]  ← Governança, lineage, catálogo em todas as camadas
[Airflow]       ← Orquestração de DAGs (KubernetesExecutor)
[Ollama]        ← LLM local para AI Agent (pod no KIND)
```

### Key Data Questions Explored

| # | Question | Answer | Impact |
|---|----------|--------|--------|
| 1 | Deduplicação CDC no Silver | MERGE por PK do schema fonte | Iceberg MERGE INTO com Spark |
| 2 | Deduplicação arquivos no Silver | PK declarada no contrato ODCS | datacontract-cli valida e Spark aplica dedup |
| 3 | Dados inválidos no Bronze | A definir no /design | Opções: dead-letter, retry automático, reprocessamento manual |
| 4 | Query engine | Trino sobre Iceberg/MinIO | Catalog Iceberg apontando para MinIO endpoint |

---

## KBs Necessários (a criar antes do /define)

| KB | Conteúdo | Agente Responsável |
|----|----------|-------------------|
| `kafka-k8s` | Strimzi Operator, KafkaConnect, Debezium connectors, KafkaTopic CRDs | `streaming-engineer` |
| `iceberg-minio` | Apache Iceberg sobre MinIO, table management, MERGE INTO, partitioning | `lakehouse-architect` |
| `spark-k8s` | Spark Operator, SparkApplication CRD, PySpark jobs, resource config | `spark-engineer` |
| `trino-iceberg` | Trino Helm chart, Iceberg catalog config, query patterns | `data-platform-engineer` |
| `openmetadata-k8s` | OpenMetadata Helm chart, connectors, lineage, governance | `data-platform-engineer` |
| `kind-cluster` | KIND setup, multi-node config, Helm bootstrap, port-forward, ingress | `ci-cd-specialist` |
| `ollama-k8s` | Ollama Helm chart/pod, modelo llama3.2:3b ou qwen2.5:3b, REST API | `genai-architect` |
| `chainlit-agent` | Chainlit framework, AI Agent com Ollama, pg catalog introspection, ODCS generation | `genai-architect` + `ai-data-engineer` |
| `dbt-cosmos` | dbt Core com Trino adapter, Cosmos provider no Airflow, models Gold, testes, lineage OpenMetadata | `dbt-specialist` + `airflow-specialist` |
| `argocd-k8s` | ArgoCD Helm chart, Application CRDs, App of Apps pattern, sync policies, bootstrap order para KIND | `ci-cd-specialist` |

---

## Agentes Especializados Mapeados

| Fase | Agente | Responsabilidade |
|------|--------|-----------------|
| Infra / KIND setup | `ci-cd-specialist` | KIND cluster, Helm charts, namespaces, ingress, ArgoCD bootstrap |
| Kafka / Streaming | `streaming-engineer` | Strimzi, KafkaConnect, Debezium CDC |
| Iceberg / MinIO | `lakehouse-architect` | Table format, schema evolution, compaction |
| Spark jobs | `spark-engineer` | SparkApplication CRDs, dedup, transformações |
| Spark Streaming | `spark-streaming-architect` | Kafka → Bronze streaming jobs |
| Airflow DAGs | `airflow-specialist` | DAGs com KubernetesExecutor, sensors, Cosmos para dbt |
| dbt Gold layer | `dbt-specialist` | Models SQL para Gold, testes dbt, Cosmos DbtDag |
| Contratos de dados | `data-contracts-engineer` | ODCS v3.1, datacontract-cli, valid/invalid routing |
| Qualidade | `data-quality-analyst` | Soda checks, Great Expectations, testes por camada |
| Schema / Modelagem | `schema-designer` | Iceberg schemas por domínio/entidade |
| Arquitetura geral | `medallion-architect` | Bronze/Silver/Gold layer design |
| Trino / Plataforma | `data-platform-engineer` | Trino catalog, query patterns, performance |
| Self-service portal | `genai-architect` | Chainlit UI, AI Agent, fluxo de registro de tabelas |
| AI Agent (LLM) | `ai-data-engineer` | Ollama integration, schema introspection, ODCS generation |

---

## Selected Approach

| Attribute | Value |
|-----------|-------|
| **Chosen** | Approach A — Helm Charts + Operators no KIND |
| **User Confirmation** | 2026-04-21 |
| **Reasoning** | Stack completa desde o dia 1, GitOps-ready, mais próximo de produção, máquina local tem recursos suficientes (31GB RAM, 16 CPUs) |

---

## Key Decisions Made

| # | Decision | Rationale | Alternative Rejected |
|---|----------|-----------|----------------------|
| 1 | Helm Charts + Operators no KIND | Reproduzível, próximo de produção, sem divergência local/prod | Docker Compose (dois ambientes, sem teste K8s real) |
| 2 | Apache Iceberg como formato de tabela | Open format, suporte nativo Trino + Spark, schema evolution, MERGE INTO | Delta Lake (menos suporte nativo no Trino OSS) |
| 3 | Trino como query engine | SQL federado, suporte Iceberg nativo, sem vendor lock-in | Presto (fork menos ativo), DuckDB (sem K8s native) |
| 4 | Strimzi para Kafka | Operator K8s nativo, gestão de topics via CRDs, maturidade | Kafka via Docker simples (sem gestão K8s) |
| 5 | Dedup CDC por PK do schema | Espelha comportamento da fonte, natural para CDC | Hash de todas as colunas (frágil, falsos positivos) |
| 6 | PK declarada no contrato para arquivos | Força contratos explícitos, evita dedup sem semântica | Hash de colunas sem PK (ambíguo) |
| 7 | Ollama com modelo leve (3B) para AI Agent | Tarefa de mapeamento estruturado schema→YAML, não requer raciocínio complexo | Claude API (custo/dependência externa), modelo 70B (excessivo) |
| 8 | PostgreSQL como Docker externo ao KIND | Source system não pertence à plataforma — separação de responsabilidades | CloudNativePG dentro do KIND (acoplamento desnecessário) |
| 9 | Chainlit como portal self-service | Framework Python para chat UI com LLMs, integra nativamente com Ollama/LangChain | FastAPI custom (mais trabalho, sem UX conversacional) |
| 10 | dbt + Cosmos para Gold layer | SQL-first para views de negócio; Cosmos converte dbt models em Tasks Airflow nativamente — integração simples sem DAGs manuais | Spark puro para Gold (mais verboso, não SQL-first); dbt standalone sem Cosmos (DAG manual por model) |
| 11 | ArgoCD para GitOps de infra | Deploy declarativo — Git é a fonte de verdade para todos os Helm charts; reproduz o cluster do zero com `kubectl apply`; separa responsabilidade de infra (ArgoCD) de dados (Airflow) | Helm manual (não auditável, não reproduzível); Terraform (state externo, mais complexo para K8s puro) |

---

## Features Removidas (YAGNI)

| Feature Sugerida | Razão Removida | Pode Adicionar Depois? |
|-----------------|----------------|----------------------|
| Node pools por domínio (multi-tenant KIND) | Complexidade alta para MVP, recursos suficientes sem isolamento | Sim — no /design de produção |
| Schema Registry (Confluent/Apicurio) | Pode usar datacontract-cli para validação inicial sem registry | Sim — quando CDC crescer em volume |
| ~~dbt para Gold layer~~ | **Reincorporado (v1.1)** — Cosmos no Airflow simplifica a integração; dbt é SQL-first e natural para views Gold | N/A — adicionado ao escopo |
| Data lineage automático via OpenLineage/Marquez | OpenMetadata já inclui lineage; não precisamos de stack separada | N/A — OpenMetadata cobre |

---

## Incremental Validations

| Section | Presented | User Feedback | Adjusted? |
|---------|-----------|---------------|-----------|
| Análise inicial da arquitetura | ✅ 2026-04-21 | Confirmado | Não |
| Abordagens de deploy (A/B/C) | ✅ 2026-04-21 | Selecionou Approach A | Não |
| Recursos da máquina (htop) | ✅ 2026-04-21 | 31GB RAM / 16 CPUs / 353GB disco | Stack completa sem priorização |

---

## Suggested Requirements for /define

### Problem Statement (Draft)
Construir uma plataforma de dados on-premise, full open-source, rodando em Kubernetes local (KIND), com ingestão via Kafka, validação por contratos de dados (ODCS), armazenamento em Iceberg/MinIO, processamento por Spark Operator, orquestração via Airflow, query SQL via Trino e governança com OpenMetadata — seguindo arquitetura Medallion (Bronze/Silver/Gold).

### Target Users (Draft)

| User | Pain Point |
|------|------------|
| Engenheiro de dados | Precisa de plataforma reproduzível, testável localmente, sem cloud vendor lock-in |
| Analista de dados | Precisa de SQL sobre dados confiáveis e governados (Trino → Gold) |
| Time de dados | Precisa de contratos claros entre produtores e consumidores |

### Success Criteria (Draft)

- [ ] ArgoCD sobe via bootstrap inicial e sincroniza todos os componentes da plataforma a partir do Git
- [ ] KIND cluster sobe com todos os componentes declarados em Helm charts versionados no Git
- [ ] Dado flui de Kafka → Bronze (valid/invalid) via datacontract-cli
- [ ] CDC deduplica no Silver por PK do schema fonte
- [ ] Arquivos (csv/xlsx/parquet) exigem PK declarada no contrato ODCS
- [ ] Trino executa queries SQL sobre tabelas Iceberg no Gold layer
- [ ] OpenMetadata exibe catálogo e lineage de todas as camadas
- [ ] Airflow orquestra jobs Spark via KubernetesExecutor
- [ ] dbt models geram views/tabelas Gold sobre Silver Iceberg via Trino adapter
- [ ] Cosmos provider converte dbt project em DAG Airflow (DbtDag) sem código manual por model
- [ ] Testes dbt validam qualidade das transformações Gold
- [ ] Chainlit portal permite produtor registrar tabela Postgres e receber confirmação de ingestão ativada
- [ ] AI Agent gera contrato ODCS válido a partir do schema da tabela Postgres
- [ ] Debezium connector é ativado automaticamente após geração do contrato
- [ ] Ollama roda como pod no KIND e responde ao AI Agent via REST API

### Constraints Identified

- Ambiente local (KIND) — sem cloud, sem managed services
- 31GB RAM, 16 CPUs, 353GB disco — stack completa mas sem excesso
- Ferramentas 100% open-source — sem licenças comerciais
- Tratamento de dados inválidos: a definir no /design

### Out of Scope (Confirmado)

- Node pools por domínio (multi-tenant) — complexidade desnecessária para MVP
- Schema Registry separado (Confluent/Apicurio) — datacontract-cli cobre validação inicial
- ~~dbt para transformações Gold~~ — **reincorporado em v1.1** com Cosmos
- Deploy em cloud — escopo local apenas
- ML training / ML serving — escopo futuro; Spark MLlib + MLflow são candidatos naturais dentro da stack
- API de dados para usuários finais — escopo futuro; FastAPI sobre Trino ou Apache Superset (connector Trino nativo)

---

## Session Summary

| Metric | Value |
|--------|-------|
| Questions Asked | 4 |
| Approaches Explored | 3 |
| Features Removed (YAGNI) | 4 |
| Validations Completed | 3 |
| Stack Final | Kafka + Spark + Airflow + Cosmos + dbt + MinIO + Iceberg + Trino + OpenMetadata + Chainlit + Ollama + KIND + ArgoCD |
| Source externo | PostgreSQL Docker (fora do KIND) — source system simulado |

---

## Next Step

**KBs a criar primeiro:** `kind-cluster`, `argocd-k8s`, `kafka-k8s`, `iceberg-minio`, `spark-k8s`, `trino-iceberg`, `openmetadata-k8s`, `ollama-k8s`, `chainlit-agent`, `dbt-cosmos`

**Ready for:** `/define .claude/sdd/features/BRAINSTORM_DATA_PLATFORM_K8S.md`

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-21 | brainstorm-agent | Versão inicial |
| 1.1 | 2026-04-21 | iterate-agent | Adicionado dbt (Trino adapter) para criação de views/models no Gold layer; Cosmos provider no Airflow para orquestração nativa de dbt sem DAGs manuais; KB `dbt-cosmos` adicionado; decisão #10 registrada; critérios de sucesso atualizados; dbt removido de YAGNI |
| 1.2 | 2026-04-21 | iterate-agent | Adicionado ArgoCD como camada GitOps de infra (separa deploy de infra da orquestração de dados do Airflow); KB `argocd-k8s` adicionado; decisão #11 registrada; ML training e Data API adicionados ao Out of Scope como escopo futuro explícito |
