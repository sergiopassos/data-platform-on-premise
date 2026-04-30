# On-Premise K8s — Quick Reference

## Bootstrap Sequence (KIND)

```bash
# 1. Start local registry
docker run -d -p 5001:5000 --name kind-registry --restart=always registry:2
docker network connect kind kind-registry

# 2. Create cluster
kind create cluster --config cluster/kind-config.yaml --name data-platform

# 3. Pre-install Spark CRDs (server-side, bypasses 262KB annotation limit)
helm template spark-crds spark-operator/spark-operator --version 2.1.0 --include-crds \
  | kubectl apply --server-side --force-conflicts -f -

# 4. Bootstrap ArgoCD + secrets + root app
./cluster/bootstrap.sh
```

## ArgoCD

```bash
# Port-forward UI
kubectl port-forward svc/argocd-server -n argocd 8090:80

# Get admin password
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d

# Force refresh an app
kubectl -n argocd annotate app <app-name> argocd.argoproj.io/refresh=hard --overwrite

# Clear stuck operation
kubectl patch app <app-name> -n argocd --type json \
  -p '[{"op":"remove","path":"/operation"}]'

# Watch all apps
kubectl -n argocd get apps -w
```

## Kafka / Strimzi

```bash
# Check Kafka cluster status
kubectl get kafka -n streaming

# Check topics
kubectl get kafkatopic -n streaming

# Kafka Connect REST API (inside cluster)
curl http://kafka-connect.streaming.svc.cluster.local:8083/connectors

# Register Debezium connector
curl -X POST http://kafka-connect.streaming.svc.cluster.local:8083/connectors \
  -H 'Content-Type: application/json' \
  -d @connector-config.json

# Produce test message
kubectl exec -it kafka-cluster-kafka-0 -n streaming -- \
  bin/kafka-console-producer.sh \
  --bootstrap-server localhost:9092 \
  --topic my.topic

# Consume messages
kubectl exec -it kafka-cluster-kafka-0 -n streaming -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic my.topic --from-beginning
```

## Spark Operator

```bash
# Submit a SparkApplication
kubectl apply -f spark-job.yaml -n processing

# Check SparkApplication status
kubectl get sparkapplication -n processing

# Logs of driver pod
kubectl logs <driver-pod> -n processing

# Delete and resubmit
kubectl delete sparkapplication my-job -n processing
kubectl apply -f spark-job.yaml -n processing
```

## MinIO

```bash
# Port-forward MinIO console
kubectl port-forward svc/minio -n storage 9001:9001

# MinIO CLI inside cluster
kubectl exec -it deploy/minio -n storage -- mc alias set local http://localhost:9000 minioadmin minioadmin
kubectl exec -it deploy/minio -n storage -- mc ls local/
```

## Trino

```bash
# Port-forward Trino
kubectl port-forward svc/trino -n serving 8080:8080

# Trino CLI
kubectl exec -it deploy/trino-coordinator -n serving -- trino --catalog iceberg
```

## Airflow

```bash
# Port-forward webserver
kubectl port-forward svc/airflow-webserver -n orchestration 8081:8080

# Check DAG status
kubectl exec -it deploy/airflow-scheduler -n orchestration -- airflow dags list

# Trigger DAG
kubectl exec -it deploy/airflow-scheduler -n orchestration -- \
  airflow dags trigger my_dag
```

## Common Gotchas

| Symptom | Cause | Fix |
|---------|-------|-----|
| PVC stuck `Pending` | `WaitForFirstConsumer` binding mode needs pod scheduled first | Use `ephemeral` storage for Kafka/ZK in dev; or ensure pod anti-affinity doesn't block scheduling |
| `ErrImageNeverPull` | Custom image not loaded into KIND after cluster recreate | `kind load docker-image <image> --name data-platform` |
| ArgoCD `authentication required` | Private repo accessed via HTTPS without credentials | Use SSH URL + `argocd.argoproj.io/secret-type=repository` secret |
| Spark operator `CrashLoopBackOff` | Webhook port 8080 conflicts with metrics server | Set `webhook.port: 9443` in values.yaml |
| Spark CRD `annotations too large` | CRD exceeds 262144-byte annotation limit | Use `skipCrds: true` + pre-install via `helm template \| kubectl apply --server-side` |
| Airflow postgres `image not found` | Bitnami removed old Docker Hub tags | Override `postgresql.image.tag: "latest"` |
| Airflow logs PVC `ReadWriteMany` | `rancher.io/local-path` only supports `ReadWriteOnce` | Set `logs.persistence.enabled: false` |
| Strimzi stuck reconciling old object | Operator mid-reconcile on deleted CRD resource | Restart Strimzi operator pod |
| `KafkaTopic` name invalid | `RFC 1123` — no uppercase, dots are ok but underscores not in metadata.name | Use lowercase + hyphens in metadata.name |
| Chainlit `pydantic` error | Old chainlit version incompatible with pydantic v2 | Pin `chainlit>=2.0.4` |
| Ollama init `kill %1` fails | `sh` has no job control; `%1` is bash-only | Use `SERVE_PID=$!` and `kill $SERVE_PID` |

## Namespace Map

| Namespace | Components |
|-----------|-----------|
| `argocd` | ArgoCD server, repo-server, application-controller |
| `streaming` | Kafka cluster (Strimzi), KafkaConnect (Debezium) |
| `storage` | MinIO, Nessie |
| `processing` | Spark Operator, SparkApplication jobs |
| `serving` | Trino |
| `orchestration` | Airflow (scheduler, webserver, triggerer, workers) |
| `governance` | OpenMetadata |
| `portal` | Chainlit, Ollama |
| `infra` | PostgreSQL source (external simulation) |
