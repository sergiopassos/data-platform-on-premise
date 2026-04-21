# Template for a KafkaTopic CRD.
# The AI Agent copies this and substitutes TABLE_NAME before applying.
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaTopic
metadata:
  name: cdc.public.TABLE_NAME
  namespace: streaming
  labels:
    strimzi.io/cluster: kafka-cluster
spec:
  partitions: 3
  replicas: 1
  config:
    retention.ms: 604800000
    segment.bytes: 104857600
    cleanup.policy: delete
