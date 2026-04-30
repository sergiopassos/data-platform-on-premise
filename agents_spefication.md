### ESPECIFICAÇÃO DOS AGENTES

**1. Agente Orquestrador (State Manager)**
* **Responsabilidade:** Gerenciar a esteira E2E. Avalia o State Graph JSON e delega a tarefa para o próximo agente da fila.
* **Guardrails (Limites):** Não executa código. Apenas chama sub-agentes via tool de delegação. Se o status no JSON for 'ERROR', aciona o 'Fail-Fast': repassa o estado para o Agente Relator e aborta o teste imediatamente.
* **Evals (Sucesso):** O dado fluiu do Postgres até o Trino (Gold) sem intervenção humana.

**2. Agente de Infraestrutura (K8s & Storage Admin)**
* **Responsabilidade:** Health check pré-voo da infraestrutura.
* **Guardrails:** Acesso estrito de 'Read-Only'. Deve checar se os pods dos namespaces `infra`, `streaming`, `processing`, `orchestration` e `serving` estão 'Running'. Deve usar o client S3/MinIO para checar a existência dos buckets `warehouse/`, `bronze/` e `contracts/`. PROIBIDO escalar, deletar ou modificar recursos no K8s.
* **Evals:** Todos os serviços core reportam prontidão. Emite score 1 para `infra_health` no Langfuse.

**3. Agente de Origem e Contratos (Data Source & Portal Agent)**
* **Responsabilidade:** Simular o Portal Chainlit. Ele deve: 1) Criar tabela no PostgreSQL (`infra`), 2) Gerar contrato ODCS v0.9.3 via LLM, 3) Validar com `datacontract-cli` (no ambiente Python 3.11 do portal), 4) Fazer upload do YAML para o MinIO, 5) Ativar o conector CDC no Debezium.
* **Guardrails:** Se a validação da CLI falhar, DEVE obrigatoriamente acionar a tool de 'Fallback Generator' determinístico para gerar um contrato válido. O tópico Kafka gerado deve seguir o padrão `cdc.public.[tabela]`.
* **Evals:** Tabela populada, contrato validado no MinIO, conector Debezium retorna HTTP 201, e pelo menos 1 mensagem lida com sucesso do tópico Kafka.

**4. Agente de Processamento Spark (Bronze/Silver Executor)**
* **Responsabilidade:** Controlar o Apache Spark 3.5.1 via CRDs do Kubernetes.
* **Guardrails:** NÃO escreve código PySpark. Apenas manipula e faz o apply de manifestos `SparkApplication`. Deve executar `bronze_streaming.py` (Kafka -> Iceberg) e depois a pipeline `bronze_to_silver.py` (MERGE Iceberg). O catálogo apontado deve ser sempre o Nessie REST API.
* **Evals:** Pods do Spark completam com sucesso. Tabelas registradas no Nessie e materializadas no bucket `warehouse/` do MinIO.

**5. Agente de Orquestração Analítica (Gold & Query Agent)**
* **Responsabilidade:** Finalizar o fluxo Medallion e validar a acessibilidade.
* **Guardrails:** Dispara a DAG `gold_dbt_dag.py` via API REST do Airflow (que usa Cosmos para dbt). Tem permissão de apenas 1 retry em caso de falha na DAG. Após a DAG concluir, conecta via JDBC no Trino (porta 8082, namespace `serving`) e roda uma query para validar o dado.
* **Evals:** DAG de dbt marca 'Success'. Query `SELECT COUNT(*)` no Trino na tabela Gold retorna valor > 0. O OpenMetadata confirma a linhagem gerada.

**6. Agente Relator (Auditor & Langfuse Integration)**
* **Responsabilidade:** Agente final focado na emissão do laudo técnico do teste.
* **Guardrails:** Acesso 'Read-Only'. Não interage com os serviços de dados. Ele lê o `langfuse_trace_id` do payload, consulta a API do Langfuse para extrair scores, logs de erro e tempo de execução.
* **Evals:** Gera um relatório Markdown detalhado formatado para o Slack/Teams listando gargalos, tempo total e a causa raiz (Root-Cause Analysis) caso algum agente tenha ativado o Fail-Fast.

---

### O PAYLOAD COMPARTILHADO (STATE GRAPH)
O sistema deve transitar um JSON neste formato entre os agentes:
```json
{
  "run_id": "e2e-test-123",
  "langfuse_trace_id": "trace_abc890...",
  "current_status": "RUNNING",
  "data_contract_path": "s3://contracts/table_v1.yaml",
  "kafka_topic": "cdc.public.test_table",
  "error_log": null,
  "next_agent": "Spark Processing"
}