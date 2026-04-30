# Pattern: Kafka Connect + Debezium (Plain Deployment)

## Problem

Strimzi's `KafkaConnect` CRD requires either:
1. A Strimzi-compatible base image with connectors baked in (requires push to a registry with credentials)
2. The Strimzi build pipeline that pushes to a registry (needs Docker Hub or equivalent credentials)

In local KIND dev, there are no registry credentials, and `quay.io/debezium/connect` is incompatible with Strimzi's expected filesystem layout. Using Strimzi `KafkaConnect` CRD results in crash loops or build failures.

## Solution: Plain Kubernetes Deployment

Replace the Strimzi `KafkaConnect` CRD with a standard `Deployment` using `quay.io/debezium/connect:2.6`. This image has Debezium connectors pre-installed and exposes the Connect REST API on port 8083 — the same API Strimzi would have created.

## Manifest

```yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kafka-connect
  namespace: streaming
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kafka-connect
  template:
    metadata:
      labels:
        app: kafka-connect
    spec:
      containers:
        - name: kafka-connect
          image: quay.io/debezium/connect:2.6
          ports:
            - containerPort: 8083
          env:
            - name: BOOTSTRAP_SERVERS
              value: kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092
            - name: GROUP_ID
              value: connect-cluster
            - name: CONFIG_STORAGE_TOPIC
              value: connect-cluster-configs
            - name: OFFSET_STORAGE_TOPIC
              value: connect-cluster-offsets
            - name: STATUS_STORAGE_TOPIC
              value: connect-cluster-status
            - name: CONFIG_STORAGE_REPLICATION_FACTOR
              value: "1"
            - name: OFFSET_STORAGE_REPLICATION_FACTOR
              value: "1"
            - name: STATUS_STORAGE_REPLICATION_FACTOR
              value: "1"
            - name: KEY_CONVERTER
              value: org.apache.kafka.connect.json.JsonConverter
            - name: VALUE_CONVERTER
              value: org.apache.kafka.connect.json.JsonConverter
            - name: KEY_CONVERTER_SCHEMAS_ENABLE
              value: "false"
            - name: VALUE_CONVERTER_SCHEMAS_ENABLE
              value: "false"
          resources:
            requests:
              memory: 512Mi
              cpu: 250m
            limits:
              memory: 1Gi
              cpu: 500m
          readinessProbe:
            httpGet:
              path: /connectors
              port: 8083
            initialDelaySeconds: 30
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /connectors
              port: 8083
            initialDelaySeconds: 60
            periodSeconds: 20
---
apiVersion: v1
kind: Service
metadata:
  name: kafka-connect
  namespace: streaming
spec:
  selector:
    app: kafka-connect
  ports:
    - port: 8083
      targetPort: 8083
```

## Debezium PostgreSQL Connector Registration

After `kafka-connect` pod is Running, register the connector via REST:

```bash
curl -X POST \
  http://kafka-connect.streaming.svc.cluster.local:8083/connectors \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "postgres-source-connector",
    "config": {
      "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
      "database.hostname": "postgres.infra.svc.cluster.local",
      "database.port": "5432",
      "database.user": "postgres",
      "database.password": "postgres",
      "database.dbname": "sourcedb",
      "database.server.name": "postgres",
      "table.include.list": "public.orders,public.customers",
      "plugin.name": "pgoutput",
      "publication.autocreate.mode": "filtered",
      "topic.prefix": "cdc"
    }
  }'
```

Topic naming: `cdc.public.orders`, `cdc.public.customers`

## Why Not Strimzi KafkaConnect CRD

| Concern | Strimzi KafkaConnect | Plain Deployment |
|---------|---------------------|-----------------|
| Registry credentials needed | Yes (build push) | No |
| Custom image required | Yes (Strimzi base) | No — quay.io/debezium/connect |
| Connector management | Strimzi `KafkaConnector` CRD | REST API (curl/HTTP) |
| Production-ready | Yes — full lifecycle managed | Acceptable for dev |
| Operator restart risk | Yes — operator reconciliation loop | No — stateless deployment |

## Strimzi Operator Conflict

If you previously had a `KafkaConnect` CRD resource and replaced it with this Deployment, the Strimzi operator may still be in a reconciliation loop for the deleted resource (it waits ~5 minutes for the old `kafka-connect-connect-0` pod). Restart the operator immediately:

```bash
kubectl rollout restart deployment/strimzi-cluster-operator -n streaming
```

## Production Path

For production, build a custom Strimzi+Debezium image:

```dockerfile
FROM quay.io/strimzi/kafka:0.41.0-kafka-3.7.0
USER root:root
RUN mkdir -p /opt/kafka/plugins/debezium-postgres && \
    curl -sL https://repo1.maven.org/maven2/io/debezium/debezium-connector-postgres/2.6.2.Final/debezium-connector-postgres-2.6.2.Final-plugin.tar.gz \
    | tar -xz -C /opt/kafka/plugins/debezium-postgres
USER 1001
```

Then use `KafkaConnect` with `.spec.build.output.image` pointing to your private registry.
