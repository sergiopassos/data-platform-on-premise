# Apache Airflow on Kubernetes — On-Premise

## What It Is

Airflow deployed via the official Helm chart (apache-airflow, version 1.15.x) on KIND, using `KubernetesExecutor`. Each task runs in an isolated pod. Cosmos integrates dbt projects as Airflow DAGs automatically.

## KubernetesExecutor Setup

```yaml
# helm/airflow/values.yaml
executor: KubernetesExecutor

env:
  - name: AIRFLOW__KUBERNETES_EXECUTOR__NAMESPACE
    value: "orchestration"

# Worker pod template (defines the template for task pods)
podTemplate: |
  apiVersion: v1
  kind: Pod
  metadata:
    name: placeholder
  spec:
    serviceAccountName: airflow-worker
    containers:
      - name: base
        image: apache/airflow:2.9.3-python3.11
```

RBAC for the worker service account must allow pod creation in `orchestration` namespace.

## PostgreSQL Subchart Image Pinning

The Airflow Helm chart includes Bitnami PostgreSQL as a subchart. **Bitnami removes old Docker Hub tags frequently.** Fixed image tags like `bitnami/postgresql:16.1.0-debian-11-r15` will fail with `ErrImagePull` when Bitnami removes the tag.

```yaml
postgresql:
  enabled: true
  image:
    registry: docker.io
    repository: bitnami/postgresql
    tag: "latest"   # use latest; pin to a date-stamped tag only if reproducibility is critical
```

## Log Persistence Constraint

Airflow's log PVC requests `ReadWriteMany` access mode for scheduler + workers to share logs. `rancher.io/local-path` (KIND default) only supports `ReadWriteOnce`.

```yaml
logs:
  persistence:
    enabled: false   # disable — local-path doesn't support RWX
```

With `enabled: false`, logs are ephemeral (in-pod). They're still visible in the UI while the pod runs. For persistent logs in KIND, use a shared PVC with a NFS provisioner or push logs to MinIO/S3 via `remote_logging`.

## DAGs Persistence

```yaml
dags:
  persistence:
    enabled: true
    size: 2Gi
  gitSync:
    enabled: false   # use PVC instead; gitSync is cleaner for production
```

The `dags` PVC uses `ReadWriteOnce` (one writer) — works with `rancher.io/local-path`. Mount path is `/opt/airflow/dags`.

## Extra Packages

```yaml
extraPipPackages:
  - "astronomer-cosmos==1.5.0"    # dbt-Airflow integration
  - "dbt-core==1.8.0"
  - "dbt-trino==1.8.0"            # Trino adapter for dbt
  - "apache-airflow-providers-cncf-kubernetes==8.4.1"
  - "apache-airflow-providers-trino==5.7.0"
```

## Cosmos (dbt) Integration

Cosmos converts a dbt project into Airflow tasks automatically:

```python
from cosmos import DbtDag, ProjectConfig, ProfileConfig, ExecutionConfig
from cosmos.profiles import TrinoUserPasswordProfileMapping

profile_config = ProfileConfig(
    profile_name="data_platform",
    target_name="prod",
    profile_mapping=TrinoUserPasswordProfileMapping(
        conn_id="trino_default",
        profile_args={"database": "iceberg", "schema": "gold"},
    ),
)

gold_dag = DbtDag(
    dag_id="gold_transformations",
    project_config=ProjectConfig("/opt/airflow/dbt/gold"),
    profile_config=profile_config,
    execution_config=ExecutionConfig(dbt_executable_path="/home/airflow/.local/bin/dbt"),
    schedule_interval="@daily",
)
```

The dbt project must be mounted into the Airflow pod. Use a PVC:

```yaml
extraVolumes:
  - name: dbt-project
    persistentVolumeClaim:
      claimName: airflow-dbt-pvc

extraVolumeMounts:
  - name: dbt-project
    mountPath: /opt/airflow/dbt
```

## Trino Connection

```yaml
env:
  - name: AIRFLOW_CONN_TRINO_DEFAULT
    value: "trino://trino.serving.svc.cluster.local:8080/iceberg"
```

## Fernet Key

```yaml
env:
  - name: AIRFLOW__CORE__FERNET_KEY
    value: "your-base64-fernet-key-here"
```

Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## Stuck Pods After PVC Deletion

If Airflow PVCs are deleted while pods are running (e.g., during `logs.persistence` toggle), pods get stuck in `Pending` or `Init` forever referencing the missing PVC. ArgoCD enters a sync loop.

Fix:
```bash
# Delete all Airflow deployments (ArgoCD will recreate them)
kubectl delete deploy -n orchestration -l release=airflow

# Clear ArgoCD stuck operation
kubectl patch app airflow -n argocd --type json \
  -p '[{"op":"remove","path":"/operation"}]'
```

## Resource Recommendations (KIND)

```yaml
webserver:
  resources:
    requests: { memory: 1Gi, cpu: 500m }
    limits:   { memory: 2Gi }

scheduler:
  resources:
    requests: { memory: 1Gi, cpu: 500m }
    limits:   { memory: 2Gi }
```
