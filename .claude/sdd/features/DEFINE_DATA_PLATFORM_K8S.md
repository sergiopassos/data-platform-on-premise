# DEFINE: Data Platform On-Premise com Kubernetes

> Plataforma de dados full open-source em Kubernetes local (KIND) com arquitetura Medallion, ingestão via Kafka, contratos ODCS, processamento Spark, transformações Gold via dbt/Cosmos, query SQL via Trino e governança com OpenMetadata — com portal self-service e AI Agent para registro automático de fontes.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | DATA_PLATFORM_K8S |
| **Date** | 2026-04-21 |
| **Author** | define-agent |
| **Status** | Ready for Design |
| **Clarity Score** | 15/15 |
| **Source** | BRAINSTORM_DATA_PLATFORM_K8S.md (v1.2) |

---

## Problem Statement

Times de dados não têm uma plataforma local reproduzível e full open-source que integre ingestão, validação por contratos, processamento e governança em um único ambiente Kubernetes — resultando em soluções fragmentadas, dependência de cloud vendors e ausência de contratos claros entre produtores e consumidores de dados. O objetivo é construir esse ambiente completo, testável localmente via KIND, sem licenças comerciais.

---

## Target Users

| User | Role | Pain Point |
|------|------|------------|
| Engenheiro de dados | Constrói e mantém pipelines | Falta de plataforma reproduzível e testável localmente sem cloud; dificuldade em garantir contratos entre fontes |
| Analista de dados | Consome dados para análise e BI | Dados sem governança, sem catálogo, sem garantia de qualidade — não confia nos dados que consulta |
| Produtor de dados | Registra novas fontes de dados | Processo manual e burocrático para onboarding de novas tabelas; sem geração automática de contratos |

---

## Goals

| Priority | Goal |
|----------|------|
| **MUST** | KIND cluster sobe com todos os componentes da plataforma via ArgoCD + Helm, de forma reproduzível a partir do Git |
| **MUST** | Dados fluem de Kafka → Bronze (valid/invalid) com validação ODCS v3.1 via datacontract-cli |
| **MUST** | CDC do PostgreSQL externo capturado via Debezium/KafkaConnect e publicado no Kafka |
| **MUST** | Silver deduplica por PK do schema fonte (CDC) ou PK declarada no contrato (arquivos) via Spark Operator |
| **MUST** | Gold criado via dbt models com Trino adapter, orquestrado pelo Cosmos provider no Airflow |
| **MUST** | Trino executa queries SQL federadas sobre tabelas Iceberg no MinIO |
| **MUST** | OpenMetadata exibe catálogo e lineage de todas as camadas (Bronze → Silver → Gold) |
| **MUST** | Portal Chainlit permite produtor registrar tabela Postgres; AI Agent (Ollama) gera contrato ODCS e ativa CDC automaticamente |
| **SHOULD** | Airflow com KubernetesExecutor orquestra todos os jobs Spark e dbt runs via Cosmos |
| **SHOULD** | Ingestão de arquivos (CSV, XLSX, Parquet) via Chainlit com PK declarada no contrato ODCS |
| **SHOULD** | Dados inválidos (bronze/invalid) separados e com estratégia de reprocessamento definida |
| **COULD** | Ollama roda como pod dedicado no KIND com modelo llama3.2:3b ou qwen2.5:3b |

---

## Success Criteria

- [ ] ArgoCD sincroniza toda a stack a partir do repositório Git sem intervenção manual após bootstrap
- [ ] Evento CDC inserido no Postgres externo aparece no tópico Kafka correspondente em menos de 30 segundos
- [ ] datacontract-cli roteia corretamente registros válidos para `bronze/valid` e inválidos para `bronze/invalid`
- [ ] Silver contém exatamente uma linha por PK após MERGE com múltiplos eventos CDC
- [ ] `dbt run` via Cosmos/Airflow materializa models Gold no Iceberg/Trino sem erros
- [ ] Query SQL simples no Trino sobre tabela Gold retorna resultado em menos de 30 segundos
- [ ] OpenMetadata exibe lineage completo de fonte → Bronze → Silver → Gold após execução do crawler
- [ ] AI Agent gera contrato ODCS v3.1 válido e ativa connector Debezium a partir do nome de uma tabela Postgres
- [ ] Arquivo CSV com PK declarada no contrato é processado até o Silver sem intervenção manual
- [ ] Testes dbt passam para todos os models Gold (not_null, unique nas PKs)

---

## Acceptance Tests

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AT-001 | Bootstrap GitOps | KIND cluster vazio, ArgoCD instalado via bootstrap manual único | ArgoCD Application da plataforma é aplicado ao cluster | Todos os pods dos componentes (Kafka, Airflow, Spark Operator, MinIO, Trino, OpenMetadata, Chainlit, Ollama) atingem status Running/Healthy |
| AT-002 | CDC end-to-end | Postgres externo rodando, tabela registrada via Chainlit, connector Debezium ativo | INSERT é executado no Postgres | Evento aparece no tópico Kafka em < 30s |
| AT-003 | Validação ODCS Bronze | Contrato ODCS registrado para o tópico, dado chegando no Kafka | datacontract-cli valida o payload | Registro válido vai para `bronze/valid`; inválido vai para `bronze/invalid` em tabelas Iceberg separadas |
| AT-004 | Dedup Silver CDC | Bronze/valid com 3 eventos CDC para a mesma PK (INSERT + UPDATE + UPDATE) | Job Spark Operator processa Silver via MERGE INTO | Silver contém exatamente 1 linha para a PK com os valores do último UPDATE |
| AT-005 | Gold via dbt + Cosmos | Tabelas Silver no Iceberg com dados processados | DAG Airflow gerado pelo Cosmos executa `dbt run` | Models Gold materializados no Iceberg, acessíveis via Trino, testes dbt passam |
| AT-006 | Query Trino Gold | Tabelas Gold disponíveis no Iceberg/MinIO | Analista executa `SELECT * FROM gold.tabela LIMIT 100` no Trino | Resultados corretos retornam em < 30s |
| AT-007 | Lineage OpenMetadata | Pipeline completo executado (Bronze → Silver → Gold) | Crawler OpenMetadata é executado | Lineage column-level de Postgres → Kafka → Bronze → Silver → Gold visível no catálogo |
| AT-008 | Portal Chainlit + AI Agent | Produtor acessa o Chainlit, Ollama rodando como pod | Produtor informa nome da tabela Postgres no chat | AI Agent introspecta schema, gera contrato ODCS v3.1 válido, armazena no repositório e ativa o connector Debezium via KafkaConnect REST API |
| AT-009 | Ingestão de arquivo | Produtor faz upload de CSV via Chainlit com PK declarada no contrato ODCS | Airflow detecta arquivo no MinIO e dispara job Spark | Dados do arquivo aparecem no Bronze/valid e, após processamento, no Silver — sem duplicatas pela PK declarada |
| AT-010 | Reproduzibilidade do cluster | Cluster KIND destruído e recriado do zero | ArgoCD Application reaplicado ao novo cluster | Stack completa restaurada ao mesmo estado funcional, idêntica ao estado anterior |

---

## Out of Scope

- **Node pools multi-tenant** — isolamento por domínio via namespaces+quotas, não por node pools separados (complexidade desnecessária para MVP)
- **Schema Registry separado** (Confluent/Apicurio) — datacontract-cli cobre validação de schema inicial
- **Deploy em cloud** — escopo exclusivamente local via KIND; sem AWS, GCP, Azure
- **ML training e ML serving** — escopo futuro; candidatos naturais são Spark MLlib + MLflow dentro da stack existente
- **API de dados para usuários finais** — escopo futuro; FastAPI sobre Trino ou Apache Superset são candidatos
- **dbt Semantic Layer / metrics layer** — models Gold simples por ora, sem camada semântica adicional
- **Alta disponibilidade / multi-replica** — pods single-replica no KIND local; HA é concern de produção

---

## Constraints

| Type | Constraint | Impact |
|------|------------|--------|
| Técnica | Ambiente exclusivamente local (KIND) — sem cloud, sem managed services | Todos os componentes devem ter Helm charts funcionando em KIND; sem dependência de serviços externos |
| Recurso | 31GB RAM, 16 CPUs, 353GB disco na máquina local | Stack completa mas pods com resource limits conservadores; sem multi-replica por padrão |
| Licença | 100% open-source — sem licenças comerciais | Trino OSS (não Starburst), Kafka via Strimzi, sem Confluent, sem DBT Cloud |
| Design | Tratamento de dados inválidos (`bronze/invalid`) — estratégia a definir no /design | Design deve propor dead-letter pattern, retry e mecanismo de reprocessamento |
| Infra | PostgreSQL é source system externo ao KIND (Docker container na rede local) | Debezium/KafkaConnect no KIND deve alcançar Postgres via rede Docker; configuração de host/port necessária |

---

## Technical Context

| Aspect | Value | Notes |
|--------|-------|-------|
| **Deployment Location** | KIND (Kubernetes local) — namespaces por camada | `infra/`, `ingestion/`, `processing/`, `serving/`, `governance/`, `portal/` |
| **GitOps** | ArgoCD — App of Apps pattern | Bootstrap manual único; ArgoCD gerencia todos os demais Helm releases |
| **KB Domains** | `kind-cluster`, `argocd-k8s`, `kafka-k8s`, `iceberg-minio`, `spark-k8s`, `trino-iceberg`, `openmetadata-k8s`, `ollama-k8s`, `chainlit-agent`, `dbt-cosmos` | KBs a criar antes do /design — nenhum existe ainda |
| **IaC Impact** | Novos recursos — cluster KIND + todos os Helm charts do zero | Repositório Git será a fonte de verdade (values.yaml por componente) |
| **Formato de tabela** | Apache Iceberg sobre MinIO (S3-compat) | Trino e Spark acessam via Iceberg REST catalog ou Hive metastore |
| **Orquestração** | Airflow com KubernetesExecutor + Cosmos provider | Cosmos converte dbt project em DbtDag automaticamente |
| **Streaming** | Strimzi Operator (Kafka + KafkaConnect + Debezium) | CRDs nativos K8s para gestão de tópicos e connectors |
| **Processamento** | Spark Operator (SparkApplication CRD) | Bronze→Silver: MERGE INTO Iceberg; Silver→Gold delegado ao dbt |
| **Transformação Gold** | dbt Core com Trino adapter | Models SQL-first; testes not_null + unique nas PKs |
| **LLM local** | Ollama (llama3.2:3b ou qwen2.5:3b) como pod KIND | Tarefa de mapeamento estruturado schema→YAML ODCS; sem API externa |

---

## Data Contract

### Source Inventory

| Source | Localização | Type | Volume Estimate | Freshness Target |
|--------|-------------|------|-----------------|-----------------|
| PostgreSQL externo | Docker container fora do KIND | CDC via Debezium/KafkaConnect | Tabelas de teste (< 1M rows) | < 30s até Kafka |
| Arquivos batch | Upload via Chainlit → MinIO | CSV, XLSX, Parquet | Variável — definido por produtor | Batch via Airflow (agendado) |
| APIs externas | HTTP externo | REST → Kafka producer custom | Desconhecido — definido por produtor | Near real-time (produtor customizado) |

### Schema Contract

> Schemas concretos são definidos por contrato ODCS v3.1 gerado pelo AI Agent por tabela. Requisitos gerais:

| Campo | Tipo | Restrição | PII? |
|-------|------|-----------|------|
| `_cdc_op` | VARCHAR | NOT NULL (I/U/D) — apenas CDC | Não |
| `_cdc_ts` | TIMESTAMP | NOT NULL — apenas CDC | Não |
| `_ingested_at` | TIMESTAMP | NOT NULL — todas as camadas | Não |
| `_source_file` | VARCHAR | NOT NULL — apenas arquivos | Não |
| PK declarada no contrato | Variável | NOT NULL, UNIQUE no Silver | Depende do domínio |

### Freshness SLAs

| Layer | Target | Measurement |
|-------|--------|-------------|
| Bronze (CDC) | < 30s após evento no Postgres | Timestamp Kafka vs. `_ingested_at` no Iceberg |
| Bronze (arquivo) | < T+4h após upload no MinIO | Timestamp upload vs. escrita no Iceberg |
| Silver | < 5 min após Bronze | `_ingested_at` Bronze vs. Silver |
| Gold | < 15 min após Silver | Completude do dbt run via Cosmos/Airflow |

### Completeness Metrics

- 100% dos registros do tópico Kafka presentes no Bronze (valid + invalid) sem perda
- Zero registros duplicados no Silver por PK declarada no contrato
- Testes dbt `not_null` e `unique` passando em 100% dos models Gold antes de marcar DAG como sucesso

### Lineage Requirements

- Lineage de coluna (column-level) de Postgres → Kafka → Bronze → Silver → Gold no OpenMetadata
- OpenMetadata crawler deve rodar após cada execução de pipeline para atualizar o grafo de lineage
- Contratos ODCS armazenados no repositório Git e referenciados no OpenMetadata como fonte de verdade do schema

---

## Assumptions

| ID | Assumption | If Wrong, Impact | Validated? |
|----|------------|------------------|------------|
| A-001 | 31GB RAM é suficiente para rodar todos os pods da stack simultaneamente no KIND sem OOM | Seria necessário priorizar componentes e subir em grupos, comprometendo o teste e2e completo | [ ] Validar no /build com resource profiling |
| A-002 | llama3.2:3b ou qwen2.5:3b é suficiente para gerar contratos ODCS v3.1 corretos a partir do schema Postgres | AI Agent produziria contratos incorretos; necessitaria modelo maior ou prompt engineering mais elaborado | [ ] Validar com testes no /build |
| A-003 | Trino OSS com Iceberg REST catalog sobre MinIO é estável e suportado pelas versões Helm disponíveis | Incompatibilidade de versão bloquearia queries no Gold; exigiria Hive metastore separado | [ ] Confirmar versões no /design |
| A-004 | dbt-trino adapter suporta o dialeto SQL e os tipos de dados necessários para os models Gold | Models Gold precisariam de workarounds de tipo ou migração para Spark SQL | [ ] Confirmar compatibilidade no /design |
| A-005 | Debezium KafkaConnect consegue alcançar o Postgres Docker externo pela rede local do KIND | CDC não funcionaria; exigiria configuração de rede adicional (NodePort, host networking) | [ ] Validar configuração de rede no /design |
| A-006 | Cosmos provider converte um dbt project inteiro em DbtDag sem necessidade de DAG customizado por model | Exigiria criação manual de Tasks por model dbt, aumentando manutenção | [ ] Confirmar versão do Cosmos no /design |

---

## Clarity Score Breakdown

| Element | Score (0-3) | Notes |
|---------|-------------|-------|
| Problem | 3 | Específico: quem tem o problema, qual o impacto, por que local e open-source |
| Users | 3 | Três personas com roles e pain points distintos e bem articulados |
| Goals | 3 | MoSCoW com MUST/SHOULD/COULD, cada item testável e não-ambíguo |
| Success | 3 | 10 critérios mensuráveis, com SLAs numéricos (30s, 15min, 100%) |
| Scope | 3 | Out of scope explícito com 7 itens e justificativa para cada |
| **Total** | **15/15** | Pronto para /design sem perguntas abertas |

---

## Open Questions

Nenhuma — o documento está pronto para o /design. As assumptions A-003 a A-006 devem ser validadas durante o /design de cada componente antes de gerar o manifesto de arquitetura final.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-21 | define-agent | Versão inicial extraída do BRAINSTORM_DATA_PLATFORM_K8S.md v1.2 |

---

## Next Step

**Ready for:** `/design .claude/sdd/features/DEFINE_DATA_PLATFORM_K8S.md`
