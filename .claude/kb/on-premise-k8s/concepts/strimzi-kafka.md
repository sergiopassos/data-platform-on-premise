# Strimzi Kafka Operator — On-Premise

## What It Is

Strimzi is the CNCF Kubernetes operator for Apache Kafka. It manages Kafka brokers, ZooKeeper/KRaft, topics, users, and connect clusters via Kubernetes CRDs. Version 0.41.0 uses `StrimziPodSet` (not StatefulSet) for broker management.

## Kafka Cluster for Local Dev (KIND)

```yaml
apiVersion: kafka.strimzi.io/v1beta2
kind: Kafka
metadata:
  name: kafka-cluster
  namespace: streaming
spec:
  kafka:
    version: 3.7.0
    replicas: 1                          # 1 replica avoids PVC race condition
    listeners:
      - name: plain
        port: 9092
        type: internal
        tls: false
    config:
      offsets.topic.replication.factor: 1
      transaction.state.log.replication.factor: 1
      transaction.state.log.min.isr: 1
      default.replication.factor: 1
      min.insync.replicas: 1
      inter.broker.protocol.version: "3.7"
    storage:
      type: ephemeral                    # avoids WaitForFirstConsumer race in KIND
    resources:
      requests:
        memory: 1Gi
        cpu: 500m
      limits:
        memory: 2Gi
  zookeeper:
    replicas: 1
    storage:
      type: ephemeral
    resources:
      requests:
        memory: 512Mi
        cpu: 250m
      limits:
        memory: 1Gi
  entityOperator:
    topicOperator: {}
    userOperator: {}
```

## Why `storage.type: ephemeral` in KIND

With `WaitForFirstConsumer` StorageClass and multiple replicas, Strimzi 0.41.0 creates all `StrimziPodSet` PVCs in parallel. The scheduler sees competing PVCs for different nodes, and the `WaitForFirstConsumer` mode means PVCs won't bind until a pod claims them — but pods won't schedule until PVCs bind. This deadlock is intermittent with 3 replicas and consistent in KIND.

Ephemeral storage (hostPath emptyDir) bypasses PVCs entirely. Data is lost on pod restart, which is acceptable for local dev.

## KafkaTopic Naming Rules

`KafkaTopic` resources must have RFC 1123-compliant `metadata.name` — lowercase alphanumeric, hyphens, dots. The Kafka topic name is set separately via `spec.topicName`.

```yaml
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaTopic
metadata:
  name: cdc-public-orders          # RFC 1123: lowercase, dots/hyphens ok, NO underscores in name
  namespace: streaming
  labels:
    strimzi.io/cluster: kafka-cluster
spec:
  topicName: cdc.public.orders     # actual Kafka topic name (can have dots/underscores)
  partitions: 3
  replicas: 1
```

**Common mistake:** Using `TABLE_NAME` or underscores in `metadata.name` — ArgoCD will fail to apply.

## metricsConfig Pitfall

If `metricsConfig` references a ConfigMap that doesn't exist, the Kafka cluster will fail to reconcile with a cryptic error. Either remove `metricsConfig` entirely or create the referenced ConfigMap first.

```yaml
# Don't add this unless the ConfigMap exists:
# metricsConfig:
#   type: jmxPrometheusExporter
#   valueFrom:
#     configMapKeyRef:
#       name: kafka-metrics   # must exist!
#       key: kafka-metrics-config.yml
```

## StrimziPodSet vs StatefulSet

Strimzi 0.41.0 replaced StatefulSets with `StrimziPodSet` for finer lifecycle control. Key difference: `kubectl get pods` shows kafka pods correctly, but `kubectl get statefulset -n streaming` returns nothing. Use `kubectl get strimzipodset -n streaming` to inspect.

## KafkaConnect: Strimzi vs Plain Deployment

Strimzi's `KafkaConnect` CRD has a build feature that creates a custom image with connectors baked in. In KIND:

**Problem:** The build feature needs to push the resulting image to a registry. `quay.io/debezium/connect` is incompatible with Strimzi's expected filesystem layout — the Strimzi operator will crash-loop waiting for a Strimzi-based image.

**Solution for local dev:** Use a plain Kubernetes `Deployment` with `quay.io/debezium/connect:2.6`. This image has Debezium pre-installed and exposes the standard Connect REST API on port 8083.

See pattern: [kafka-connect-debezium](../patterns/kafka-connect-debezium.md)

## Bootstrap Service DNS

From within the cluster, Kafka is accessible at:
```
kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092
```

From outside KIND (port-forward):
```bash
kubectl port-forward svc/kafka-cluster-kafka-bootstrap -n streaming 9092:9092
```

## Operator Restart After CRD Changes

When replacing a `KafkaConnect` CRD resource with a plain `Deployment`, the Strimzi operator may be mid-reconcile loop for the deleted resource (waiting up to 5 minutes). Restart the operator to break the wait:

```bash
kubectl rollout restart deployment/strimzi-cluster-operator -n streaming
```
