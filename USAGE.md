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
│   │  ├── nessie.bronze.{table}_valid    ← registros válidos           │            │
│   │  └── nessie.bronze.{table}_invalid ← registros inválidos         │            │
│   │       s3://bronze/{table}/valid  e  s3://bronze/{table}/invalid   │            │
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

Popula as tabelas `customers` e `orders` com dados de exemplo.

> `psql` não está instalado localmente — use `kubectl exec` para acessar o pod diretamente.

```bash
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "CREATE TABLE IF NOT EXISTS customers (customer_id SERIAL PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE, name VARCHAR(255) NOT NULL, created_at TIMESTAMP DEFAULT NOW());" \
  -c "CREATE TABLE IF NOT EXISTS orders (order_id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), status VARCHAR(50) NOT NULL DEFAULT 'pending', amount NUMERIC(10,2) NOT NULL, created_at TIMESTAMP DEFAULT NOW());" \
  -c "INSERT INTO customers (email, name) SELECT 'customer_' || i || '@example.com', 'Customer ' || i FROM generate_series(1, 100) AS i ON CONFLICT DO NOTHING;" \
  -c "INSERT INTO orders (customer_id, status, amount) SELECT (i % 100) + 1, CASE (i % 5) WHEN 0 THEN 'pending' WHEN 1 THEN 'processing' WHEN 2 THEN 'shipped' WHEN 3 THEN 'delivered' ELSE 'cancelled' END, (RANDOM() * 1000)::NUMERIC(10,2) FROM generate_series(1, 100) AS i;"
```

**Verificar os dados inseridos:**

```bash
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "SELECT 'customers' as tabela, COUNT(*) as total FROM customers UNION ALL SELECT 'orders', COUNT(*) FROM orders;"
```

**Explorar as tabelas interativamente:**

```bash
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb
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

## Passo 2 — Ativar CDC (via curl)

> O portal Chainlit pode ser usado para gerar contratos ODCS via Ollama, mas a ativação do CDC pode ser feita diretamente via curl.

**Criar os conectores Debezium:**

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

**Importante — adicionar `customers` à publication do Debezium** (necessário quando os dois conectores compartilham a mesma publication):

```bash
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "ALTER PUBLICATION dbz_publication ADD TABLE customers;" \
  -c "SELECT pubname, schemaname, tablename FROM pg_publication_tables;"
```

Verificar e restartar o conector após adicionar à publication:

```bash
curl -s -X POST http://localhost:8083/connectors/debezium-public-customers/restart
curl -s http://localhost:8083/connectors/debezium-public-customers/status | jq '.connector.state'
```

---

## Passo 3 — Observar Mensagens no Kafka

```bash
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')

# Verificar quantas mensagens existem no tópico (offset atual)
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-get-offsets.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers

# Consumir mensagens em tempo real (sem --from-beginning — só novas mensagens)
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --timeout-ms 30000

# Ler a partir de um offset específico (ex: offset 100 em diante)
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --partition 0 \
  --offset 100 \
  --max-messages 10 \
  --timeout-ms 8000

# Listar todos os tópicos CDC
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-topics.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --list | grep cdc
```

**Formato da mensagem CDC** — os dados ficam dentro de `.payload`, não na raiz:
```json
{
  "payload": {
    "before": null,
    "after": {
      "customer_id": 1,
      "email": "customer_1@example.com",
      "name": "Cliente Atualizado"
    },
    "op": "u",
    "source": { "table": "customers", "lsn": 26950160 }
  }
}
```

Onde `"op"` pode ser: `c` (insert), `u` (update), `d` (delete), `r` (snapshot inicial).

> **Nota:** `before` é `null` em updates porque a tabela usa `REPLICA IDENTITY DEFAULT`. Para ter o valor anterior, execute: `ALTER TABLE customers REPLICA IDENTITY FULL;`

---

## Passo 4 — Simular Mudanças no PostgreSQL

Faça alterações no PostgreSQL e observe-as chegando no Kafka em tempo real:

**Terminal 1 — Consumidor Kafka em tempo real:**
```bash
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --timeout-ms 60000
```

**Terminal 2 — Fazer alterações no PostgreSQL:**
```bash
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb
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

O job Spark Streaming consome os tópicos `cdc.public.*` e escreve no Iceberg (MinIO).

**Pré-requisitos — bucket e script devem existir no MinIO:**

```bash
# 1. Criar o bucket warehouse (se não existir)
kubectl exec -n infra deployment/minio -- mc alias set local http://localhost:9000 minio minio123
kubectl exec -n infra deployment/minio -- mc mb local/warehouse 2>/dev/null || echo "bucket já existe"

# 2. Verificar se o script está no bucket
kubectl exec -n infra deployment/minio -- mc ls local/warehouse/jars/

# Se não estiver, fazer upload:
CONTENT=$(base64 -w0 spark/jobs/bronze_streaming.py)
kubectl exec -n infra deployment/minio -- sh -c "
  echo '$CONTENT' | base64 -d > /tmp/bronze_streaming.py &&
  mc cp /tmp/bronze_streaming.py local/warehouse/jars/bronze_streaming.py
"
```

> O job baixa os JARs (Iceberg, Nessie, Kafka, Hadoop-AWS) do Maven na primeira execução — pode levar alguns minutos.

**Verificar se o job está rodando:**
```bash
kubectl get sparkapplication -n processing
kubectl get pods -n processing
```

**Iniciar o job:**
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

- Navegue em: bucket `bronze` → `customers` → `valid` / `invalid`
- Bucket separado do `warehouse` — os arquivos Parquet ficam particionados por `_ingested_at_day=YYYY-MM-DD`

**Verificar via Trino CLI (recomendado):**

> **DBeaver — erro "Unable to process:"**: O DBeaver falha na navegação da árvore de schemas/tabelas contra o Trino com catálogo Iceberg REST (Nessie), mas o SQL direto funciona. Use sempre o **SQL Editor** do DBeaver ou a CLI abaixo, **não** tente expandir as tabelas pelo tree de objetos.
>
> No DBeaver: defina `Catalog = iceberg` e `Schema = bronze` na aba de conexão, depois abra um SQL Editor (Ctrl+]) e execute as queries diretamente.

```bash
# Conectar ao Trino via CLI (dentro do pod)
kubectl exec -it deployment/trino-coordinator -n serving -- trino
```

```sql
-- Listar schemas do catálogo iceberg
SHOW SCHEMAS FROM iceberg;

-- Ver tabelas Bronze
SHOW TABLES FROM iceberg.bronze;

-- Contar registros válidos no Bronze
SELECT COUNT(*) FROM iceberg.bronze.customers_valid;
SELECT COUNT(*) FROM iceberg.bronze.orders_valid;

-- Ver registros inválidos (falha de contrato)
SELECT COUNT(*) FROM iceberg.bronze.customers_invalid;
SELECT COUNT(*) FROM iceberg.bronze.orders_invalid;

-- Inspecionar os dados brutos (JSON do CDC)
SELECT _source_topic, _cdc_op, _ingested_at,
       json_extract_scalar(_raw_value, '$.after.email') AS email
FROM iceberg.bronze.customers_valid
ORDER BY _ingested_at DESC
LIMIT 10;

-- Ver distribuição de operações CDC
SELECT _cdc_op, COUNT(*) AS total
FROM iceberg.bronze.orders_valid
GROUP BY _cdc_op;
```

---

## Passo 6 — Silver Layer: Bronze → Silver (MERGE)

O Airflow executa o job Spark Batch a cada hora para processar Bronze → Silver.

**Acessar o Airflow em http://localhost:8081** (admin / admin):

- DAG: `silver_processing_manual` → executar manualmente (botão ▶)
- OU DAGs automáticos: `silver_processing_customers` e `silver_processing_orders` (aparecem apenas quando os contratos ODCS estão em `warehouse/contracts/`)

> **Atenção:** Não use `kubectl exec -- airflow dags trigger`. O CLI dentro do pod usa SQLite em vez de PostgreSQL e falha com `sqlite3.OperationalError: no such table: dag`. Use sempre a REST API ou a UI.

**Disparar via REST API:**
```bash
# Trigger da tabela orders
curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {"table_name": "orders"}}' | jq .

# Trigger da tabela customers
curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {"table_name": "customers"}}' | jq .
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

**Ou via REST API:**
```bash
curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/gold_dbt_dag/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {}}' | jq .
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
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "INSERT INTO customers (email, name) VALUES ('teste@cdc.com', 'Teste CDC') RETURNING customer_id;"

# 2. Verificar no Kafka (aguardar alguns segundos)
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers \
  --max-messages 1

# 3. Aguardar ~30 segundos e verificar no Bronze (Spark ingere a cada 10s)
# Via Trino:
# SELECT * FROM iceberg.bronze.valid_customers WHERE email = 'teste@cdc.com';

# 4. Disparar Silver processing (orders e customers)
curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {"table_name": "orders"}}' | jq .status
curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {"table_name": "customers"}}' | jq .status

# 5. Disparar Gold dbt
curl -s -u admin:admin -X POST \
  http://localhost:8081/api/v1/dags/gold_dbt_dag/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"conf": {}}' | jq .status
```

### Monitorar latência end-to-end

```sql
-- No Trino: localizar o registro pelo JSON bruto e ver latência de ingestão
SELECT
  _cdc_op,
  _cdc_ts,
  _ingested_at,
  (_ingested_at - _cdc_ts) AS latency,
  json_extract_scalar(_raw_value, '$.after.email') AS email
FROM iceberg.bronze.customers_valid
WHERE json_extract_scalar(_raw_value, '$.after.email') = 'teste@cdc.com'
ORDER BY _ingested_at DESC;
```

---

## Observabilidade

### Status dos serviços (Kubernetes)

```bash
# Todos os pods por namespace
kubectl get pods -A | grep -v Running | grep -v Completed

# Status dos Kafka topics
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')
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
POSTGRES_POD=$(kubectl get pod -n infra -l app=postgres -o jsonpath='{.items[0].metadata.name}')

# Verificar slots ativos no PostgreSQL
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
  -c "SELECT slot_name, active, restart_lsn FROM pg_replication_slots;"

# Dropar slot se necessário (reinicia o CDC do início)
kubectl exec -n infra $POSTGRES_POD -- psql -U postgres -d sourcedb \
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
KAFKA_POD=$(kubectl get pod -n streaming -l strimzi.io/name=kafka-cluster-kafka -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n streaming $KAFKA_POD -- \
  bin/kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092 \
  --topic cdc.public.customers
```

### Airflow: `sqlite3.OperationalError: no such table: dag`

O CLI do Airflow quando executado via `kubectl exec` usa SQLite em vez do PostgreSQL configurado. Nunca use `kubectl exec -- airflow dags trigger`.

```bash
# Verificar DAGs disponíveis via REST API
curl -s -u admin:admin http://localhost:8081/api/v1/dags | jq '[.dags[].dag_id]'

# Verificar status de um DAG run
curl -s -u admin:admin \
  "http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns?limit=5" | jq '.dag_runs[] | {dag_run_id, state}'
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
| Trigger Silver | `curl -s -u admin:admin -X POST http://localhost:8081/api/v1/dags/silver_processing_manual/dagRuns -H "Content-Type: application/json" -d '{"conf":{"table_name":"orders"}}'` |
| Trigger Gold | `curl -s -u admin:admin -X POST http://localhost:8081/api/v1/dags/gold_dbt_dag/dagRuns -H "Content-Type: application/json" -d '{}'` |
| Query Bronze | `SELECT COUNT(*) FROM iceberg.bronze.customers_valid` |
| Query Silver | `SELECT COUNT(*) FROM iceberg.silver.customers` |
| Query Gold | `SELECT * FROM iceberg.gold.orders_summary` |
| Ver jobs Spark | `kubectl get sparkapplication -n processing` |
| Ver pods com erro | `kubectl get pods -A \| grep -v Running \| grep -v Completed` |
