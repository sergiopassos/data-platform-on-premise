# Data Platform — Guia de Utilização

Guia end-to-end para simular e observar o fluxo completo de dados: PostgreSQL → CDC → Kafka → MinIO (Bronze) → Silver → Gold → Trino.

---

## Arquitetura Medallion

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                         DATA PLATFORM — FLUXO CDC COMPLETO                          │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│   ┌──────────────┐   WAL/pgoutput   ┌───────────────┐   cdc.public.*   ┌─────────┐ │
│   │  PostgreSQL  │ ───────────────► │   Debezium    │ ───────────────► │  Kafka  │ │
│   │  (sourcedb)  │                  │ Kafka Connect │                  │ Strimzi │ │
│   │  customers   │                  │  :8083        │                  │  :9092  │ │
│   │  orders      │                  └───────────────┘                  └────┬────┘ │
│   └──────────────┘                                                          │      │
│                                                                              │      │
│   ┌───────────────────────────────────────────────────────────────────┐     │      │
│   │                    MEDALLION LAYERS (Iceberg + Nessie)            │     │      │
│   │                                                                   │     │      │
│   │  BRONZE (Spark Streaming — 10s microbatch)                        │◄────┘      │
│   │  ├── nessie.bronze.valid_{table}    ← registros válidos           │            │
│   │  └── nessie.bronze.invalid_{table} ← registros inválidos         │            │
│   │       s3://warehouse/bronze/                                      │            │
│   │                    │                                              │            │
│   │                    ▼ (Airflow @hourly)                            │            │
│   │  SILVER (Spark Batch — MERGE INTO por PK)                         │            │
│   │  └── nessie.silver.{table}          ← deduplicated, typed        │            │
│   │       s3://warehouse/silver/                                      │            │
│   │                    │                                              │            │
│   │                    ▼ (Airflow @daily via dbt/Cosmos)              │            │
│   │  GOLD (dbt models via Trino)                                      │            │
│   │  ├── nessie.gold.orders_summary     ← agregação por dia/status   │            │
│   │  └── nessie.gold.customers_orders   ← enrichment por cliente     │            │
│   │       s3://warehouse/gold/                                        │            │
│   └───────────────────────────────────────────────────────────────────┘            │
│                                                                                     │
│   QUERY: Trino → iceberg catalog → qualquer camada                                  │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Pré-requisitos

Cluster KIND em execução com todos os serviços no ArgoCD em `Synced/Healthy`.

**Abrir port-forwards** (cada um em um terminal separado):

```bash
# ArgoCD — GitOps dashboard
kubectl port-forward svc/argocd-server -n argocd 8090:443

# Airflow — orquestração
kubectl port-forward svc/airflow-webserver -n orchestration 8081:8080

# MinIO Console — object storage (serviço da console é minio-console, não minio)
kubectl port-forward svc/minio-console -n infra 9001:9001

# Trino — SQL engine
kubectl port-forward svc/trino -n serving 8082:8080

# Chainlit Portal — self-service
kubectl port-forward svc/chainlit -n portal 8000:8000

# Kafka Connect (Debezium) — gestão de conectores
# Nota: {"version":"3.7.0",...} em http://localhost:8083 é a resposta CORRETA da API REST
kubectl port-forward svc/kafka-connect -n streaming 8083:8083

# PostgreSQL fonte — banco de dados de origem CDC
kubectl port-forward svc/postgres -n infra 5432:5432
```

**Acessos:**

| Serviço        | URL                          | Credenciais          |
|----------------|------------------------------|----------------------|
| ArgoCD         | https://localhost:8090       | admin / (ver secret) |
| Airflow        | http://localhost:8081        | admin / admin        |
| MinIO Console  | http://localhost:9001        | minio / minio123     |
| Trino UI       | http://localhost:8082        | — (sem auth)         |
| Chainlit       | http://localhost:8000        | — (sem auth)         |
| Kafka Connect  | http://localhost:8083        | — (REST API)         |

> Para obter a senha do ArgoCD: `kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath="{.data.password}" | base64 -d`

---

## Passo 1 — Seed do PostgreSQL

Popula as tabelas `customers` e `orders` com dados de exemplo:

```bash
POSTGRES_HOST=localhost \
POSTGRES_PORT=5432 \
POSTGRES_DB=sourcedb \
POSTGRES_USER=postgres \
POSTGRES_PASSWORD=postgres \
bash scripts/seed-postgres.sh 100
```

**Verificar os dados inseridos:**

```bash
PGPASSWORD=postgres psql -h localhost -p 5432 -U postgres -d sourcedb \
  -c "SELECT COUNT(*) FROM customers; SELECT COUNT(*) FROM orders;"
```

**Explorar as tabelas:**

```bash
PGPASSWORD=postgres psql -h localhost -p 5432 -U postgres -d sourcedb
```

```sql
-- Ver estrutura
\dt

-- Ver dados
SELECT * FROM customers LIMIT 5;
SELECT * FROM orders LIMIT 5;

-- Verificar WAL level (deve ser 'logical')
SHOW wal_level;
```

---

## Passo 2 — Ativar CDC via Portal Chainlit

O portal Chainlit automatiza:
1. Introspecção do schema do PostgreSQL
2. Geração do contrato ODCS via Ollama (llama3.2:3b)
3. Criação do conector Debezium no Kafka Connect
4. Upload do contrato para o MinIO

Acesse **http://localhost:8000** e siga o fluxo:

```
Você: customers
Portal: Inspecionando schema...
Portal: Gerando contrato ODCS...
Portal: Ativando conector Debezium...
Portal: CDC ativo para customers! Tópico: cdc.public.customers
```

Repita para `orders`.

**Alternativa manual via curl** (sem o portal):

```bash
# Criar conector para customers
curl -s -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "debezium-public-customers",
    "config": {
      "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
      "database.hostname": "postgres.infra.svc.cluster.local",
      "database.port": "5432",
      "database.user": "postgres",
      "database.password": "postgres",
      "database.dbname": "sourcedb",
      "database.server.name": "cdc",
      "table.include.list": "public.customers",
      "topic.prefix": "cdc",
      "plugin.name": "pgoutput",
      "publication.autocreate.mode": "filtered",
      "slot.name": "debezium_customers",
      "heartbeat.interval.ms": "10000"
    }
  }' | jq .

# Criar conector para orders
curl -s -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "debezium-public-orders",
    "config": {
      "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
      "database.hostname": "postgres.infra.svc.cluster.local",
      "database.port": "5432",
      "database.user": "postgres",
      "database.password": "postgres",
      "database.dbname": "sourcedb",
      "database.server.name": "cdc",
      "table.include.list": "public.orders",
      "topic.prefix": "cdc",
      "plugin.name": "pgoutput",
      "publication.autocreate.mode": "filtered",
      "slot.name": "debezium_orders",
      "heartbeat.interval.ms": "10000"
    }
  }' | jq .
```

**Verificar conectores ativos:**

```bash
# Listar conectores
curl -s http://localhost:8083/connectors | jq .

# Ver status de um conector
curl -s http://localhost:8083/connectors/debezium-public-customers/status | jq .
```

O status esperado é `"state": "RUNNING"`.

---

## Passo 3 — Observar Mensagens no Kafka

Consuma mensagens do tópico CDC diretamente de um pod Kafka:

```bash
# Acessar o pod do Kafka
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')

# Consumir mensagens do tópico customers (desde o início)
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --from-beginning \
  --max-messages 5

# Consumir mensagens do tópico orders
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.orders \
  --from-beginning \
  --max-messages 5

# Listar todos os tópicos CDC
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-topics.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --list | grep cdc
```

**Exemplo de mensagem CDC (INSERT):**
```json
{
  "before": null,
  "after": {
    "customer_id": 1,
    "email": "customer_1@example.com",
    "name": "Customer 1",
    "created_at": "2026-04-22T10:00:00Z"
  },
  "op": "c",
  "ts_ms": 1745316000000
}
```

Onde `"op"` pode ser: `c` (create/insert), `u` (update), `d` (delete), `r` (read/snapshot).

---

## Passo 4 — Simular Mudanças no PostgreSQL

Faça alterações no PostgreSQL e observe-as chegando no Kafka em tempo real:

**Terminal 1 — Consumidor Kafka em tempo real:**
```bash
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka-kafka -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --from-beginning
```

**Terminal 2 — Fazer alterações no PostgreSQL:**
```bash
PGPASSWORD=postgres psql -h localhost -p 5432 -U postgres -d sourcedb
```

```sql
-- INSERT — observe a mensagem "op": "c" chegando no Kafka
INSERT INTO customers (email, name)
VALUES ('novo@exemplo.com', 'Novo Cliente');

-- UPDATE — observe a mensagem "op": "u" com before/after
UPDATE customers
SET name = 'Cliente Atualizado'
WHERE email = 'novo@exemplo.com';

-- DELETE — observe a mensagem "op": "d" com before: {...}, after: null
DELETE FROM customers
WHERE email = 'novo@exemplo.com';

-- Bulk insert para gerar volume
INSERT INTO orders (customer_id, status, amount)
SELECT
  (random() * 99 + 1)::int,
  CASE (floor(random() * 5))::int
    WHEN 0 THEN 'pending'
    WHEN 1 THEN 'processing'
    WHEN 2 THEN 'shipped'
    WHEN 3 THEN 'delivered'
    ELSE 'cancelled'
  END,
  (random() * 1000)::numeric(10,2)
FROM generate_series(1, 20);
```

Cada operação gera imediatamente uma mensagem no Kafka.

---

## Passo 5 — Bronze Layer: Kafka → MinIO/Iceberg

O job Spark Streaming consome os tópicos `cdc.public.*` e escreve no Iceberg.

**Verificar se o job está rodando:**
```bash
kubectl get sparkapplication -n processing
kubectl get pods -n processing
```

**Se o job não estiver rodando, inicie manualmente:**
```bash
kubectl apply -f spark/applications/bronze-streaming-app.yaml
```

**Acompanhar os logs do job:**
```bash
DRIVER_POD=$(kubectl get pod -n processing -l spark-role=driver -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n processing $DRIVER_POD -f
```

**Verificar dados no MinIO:**

Acesse o MinIO Console em **http://localhost:9001** (minio / minio123):

- Navegue em: `warehouse` → `bronze` → `valid_customers` / `valid_orders`
- Os arquivos Parquet serão visíveis particionados por data de ingestão

**Verificar via Trino:**
```bash
# Conectar ao Trino via CLI
kubectl exec -it deployment/trino-coordinator -n serving -- trino

-- Ou via REST
curl -s -X POST http://localhost:8082/v1/statement \
  -H "X-Trino-User: trino" \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT COUNT(*) FROM iceberg.bronze.valid_customers"}'
```

```sql
-- Via Trino CLI (dentro do pod)
SHOW SCHEMAS FROM iceberg;

-- Ver tabelas Bronze
SHOW TABLES FROM iceberg.bronze;

-- Contar registros no Bronze
SELECT COUNT(*) FROM iceberg.bronze.valid_customers;
SELECT COUNT(*) FROM iceberg.bronze.valid_orders;

-- Ver últimas ingestões
SELECT customer_id, email, _cdc_op, _ingested_at
FROM iceberg.bronze.valid_customers
ORDER BY _ingested_at DESC
LIMIT 10;
```

---

## Passo 6 — Silver Layer: Bronze → Silver (MERGE)

O Airflow executa o job Spark Batch a cada hora para processar Bronze → Silver.

**Acessar o Airflow em http://localhost:8081** (admin / admin):

- DAG: `silver_processing_dag` → executar manualmente (botão ▶)

**Ou disparar via Airflow CLI:**
```bash
# Acionar o DAG manualmente para hoje
TODAY=$(date +%Y-%m-%d)
kubectl exec -n orchestration deployment/airflow-webserver -- \
  airflow dags trigger silver_processing_dag \
  --conf "{\"date\": \"$TODAY\"}"
```

**Acompanhar o job Spark Silver:**
```bash
# Ver SparkApplications criados pelo Airflow
kubectl get sparkapplication -n processing

# Logs do driver
DRIVER=$(kubectl get pod -n processing -l spark-role=driver --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
kubectl logs -n processing $DRIVER -f
```

**Verificar Silver via Trino:**
```sql
SHOW TABLES FROM iceberg.silver;

-- Ver dados Silver (deduplicated, clean)
SELECT * FROM iceberg.silver.customers LIMIT 10;
SELECT * FROM iceberg.silver.orders LIMIT 10;

-- Contar registros
SELECT COUNT(*) FROM iceberg.silver.customers;
SELECT COUNT(*) FROM iceberg.silver.orders;

-- Verificar status distribution
SELECT status, COUNT(*) as total
FROM iceberg.silver.orders
GROUP BY status
ORDER BY total DESC;
```

A camada Silver aplica:
- Deduplicação por PK (mantém o evento CDC mais recente)
- `DELETE` no Silver quando `_cdc_op = 'D'`
- `UPDATE` quando o registro existe
- `INSERT` quando o registro é novo

---

## Passo 7 — Gold Layer: dbt via Trino

O Airflow executa os modelos dbt diariamente usando Astronomer Cosmos.

**Acessar o Airflow em http://localhost:8081**:

- DAG: `gold_dbt_dag` → executar manualmente (botão ▶)

**Ou via CLI:**
```bash
kubectl exec -n orchestration deployment/airflow-webserver -- \
  airflow dags trigger gold_dbt_dag
```

**Verificar modelos Gold via Trino:**
```sql
SHOW TABLES FROM iceberg.gold;

-- Resumo de pedidos por dia e status
SELECT *
FROM iceberg.gold.orders_summary
ORDER BY order_date DESC, total_amount DESC
LIMIT 20;

-- Valor lifetime por cliente (top 10)
SELECT email, total_orders, lifetime_value, last_order_at
FROM iceberg.gold.customers_orders
ORDER BY lifetime_value DESC
LIMIT 10;
```

---

## Passo 8 — Observar o Fluxo Completo

### Simular um ciclo completo de CDC ponta-a-ponta

```bash
# 1. Inserir um novo cliente no PostgreSQL
PGPASSWORD=postgres psql -h localhost -p 5432 -U postgres -d sourcedb \
  -c "INSERT INTO customers (email, name) VALUES ('teste@cdc.com', 'Teste CDC') RETURNING customer_id;"

# 2. Verificar no Kafka (aguardar alguns segundos)
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka-kafka -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --max-messages 1

# 3. Aguardar ~30 segundos e verificar no Bronze (Spark ingere a cada 10s)
# Via Trino:
# SELECT * FROM iceberg.bronze.valid_customers WHERE email = 'teste@cdc.com';

# 4. Disparar Silver processing
kubectl exec -n orchestration deployment/airflow-webserver -- \
  airflow dags trigger silver_processing_dag \
  --conf "{\"date\": \"$(date +%Y-%m-%d)\"}"

# 5. Disparar Gold dbt
kubectl exec -n orchestration deployment/airflow-webserver -- \
  airflow dags trigger gold_dbt_dag
```

### Monitorar latência end-to-end

```sql
-- No Trino: comparar timestamp do evento CDC com a ingestão Bronze
SELECT
  customer_id,
  email,
  _cdc_ts,
  _ingested_at,
  CAST(_ingested_at AS TIMESTAMP) - CAST(_cdc_ts AS TIMESTAMP) AS latency
FROM iceberg.bronze.valid_customers
WHERE email = 'teste@cdc.com';
```

---

## Observabilidade

### Status dos serviços (Kubernetes)

```bash
# Todos os pods por namespace
kubectl get pods -A | grep -v Running | grep -v Completed

# Status dos Kafka topics
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka-kafka -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-topics.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --describe --topic cdc.public.customers

# Consumer groups (lag do Spark)
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --list

# Lag do consumer group do Spark Bronze
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --describe --group spark-bronze-streaming
```

### Debezium (Kafka Connect)

```bash
# Status de todos os conectores
curl -s http://localhost:8083/connectors?expand=status | jq '.[].status.connector.state'

# Detalhes de um conector específico
curl -s http://localhost:8083/connectors/debezium-public-customers/status | jq .

# Reiniciar conector com erro
curl -s -X POST http://localhost:8083/connectors/debezium-public-customers/restart

# Deletar conector
curl -s -X DELETE http://localhost:8083/connectors/debezium-public-customers
```

### Spark Jobs

```bash
# Ver todos os SparkApplications
kubectl get sparkapplication -n processing

# Ver detalhes de um job
kubectl describe sparkapplication bronze-streaming -n processing

# Logs do driver Bronze Streaming
kubectl logs -n processing \
  $(kubectl get pod -n processing -l spark-role=driver -o jsonpath='{.items[0].metadata.name}') \
  --tail=100

# Ver executores
kubectl get pods -n processing -l spark-role=executor
```

### ArgoCD — Saúde das aplicações

Acesse **https://localhost:8090** para ver o status de sincronização de todas as aplicações.

Via CLI:
```bash
# Status de todas as apps
argocd app list --server localhost:8090 --insecure

# Sync manual de uma app
argocd app sync airflow --server localhost:8090 --insecure
```

---

## Troubleshooting

### Debezium: slot de replicação bloqueado

```bash
# Verificar slots ativos no PostgreSQL
PGPASSWORD=postgres psql -h localhost -p 5432 -U postgres -d sourcedb \
  -c "SELECT slot_name, active, restart_lsn FROM pg_replication_slots;"

# Dropar slot se necessário (reinicia o CDC do início)
PGPASSWORD=postgres psql -h localhost -p 5432 -U postgres -d sourcedb \
  -c "SELECT pg_drop_replication_slot('debezium_customers');"
```

### Spark: job travado ou com erro

```bash
# Ver eventos do SparkApplication
kubectl describe sparkapplication bronze-streaming -n processing

# Forçar restart do job
kubectl delete sparkapplication bronze-streaming -n processing
kubectl apply -f spark/applications/bronze-streaming-app.yaml
```

### Kafka: tópico sem mensagens

```bash
# Verificar offset do tópico
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka-kafka -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers
```

### Trino: erro de conexão com Nessie

```bash
# Verificar Nessie
kubectl get pod -n infra -l app.kubernetes.io/name=nessie
kubectl logs -n infra deployment/nessie --tail=50

# Verificar catalogo no Trino
kubectl exec -it deployment/trino-coordinator -n serving -- trino \
  --execute "SHOW CATALOGS;"
```

### MinIO: buckets não visíveis

```bash
# Verificar MinIO
kubectl get pod -n infra -l app=minio
kubectl logs -n infra deployment/minio --tail=50
```

---

## Contratos de Dados (ODCS)

Os contratos vivem em `contracts/` e são montados nos pods Spark via MinIO.

**Estrutura de um contrato:**
```yaml
# contracts/customers.yaml
apiVersion: v3.1
kind: DataContract
info:
  title: customers
  version: 1.0.0
schema:
  - name: customers
    fields:
      - name: customer_id
        type: integer
        primaryKey: true
        required: true
      - name: email
        type: string
        required: true
        unique: true
      - name: name
        type: string
        required: true
      - name: created_at
        type: timestamp
```

**Upload manual de contrato:**
```bash
# Via mc (MinIO client)
kubectl run mc --image=minio/mc --rm -it --restart=Never -- \
  mc cp /contracts/customers.yaml minio/warehouse/contracts/customers.yaml
```

---

## Referência de Comandos Rápidos

| Ação | Comando |
|------|---------|
| Ver mensagens Kafka | `kubectl exec -n streaming $KAFKA_POD -- bin/kafka-console-consumer.sh --bootstrap-server ... --topic cdc.public.customers --from-beginning` |
| Status conectores | `curl -s http://localhost:8083/connectors \| jq .` |
| Trigger Silver | `airflow dags trigger silver_processing_dag` |
| Trigger Gold | `airflow dags trigger gold_dbt_dag` |
| Query Bronze | `SELECT COUNT(*) FROM iceberg.bronze.valid_customers` |
| Query Silver | `SELECT COUNT(*) FROM iceberg.silver.customers` |
| Query Gold | `SELECT * FROM iceberg.gold.orders_summary` |
| Ver jobs Spark | `kubectl get sparkapplication -n processing` |
| Ver pods com erro | `kubectl get pods -A \| grep -v Running \| grep -v Completed` |
