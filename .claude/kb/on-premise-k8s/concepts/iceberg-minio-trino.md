# Iceberg + MinIO + Trino — On-Premise Lakehouse

## Architecture

```
[Spark / dbt / Trino]
         │
         ▼
  [Nessie Catalog]   ← REST catalog server (tracks table metadata)
         │
         ▼
  [Apache Iceberg]   ← open table format (manifests, snapshots)
         │
         ▼
    [MinIO]          ← S3-compatible object storage
```

Nessie provides Git-like branching for the Iceberg catalog. Trino and Spark both talk to Nessie via the Iceberg REST catalog protocol.

## MinIO Configuration

```yaml
# helm/minio/values.yaml
rootUser: minioadmin
rootPassword: minioadmin
buckets:
  - name: lakehouse
    policy: public
    purge: false
persistence:
  enabled: true
  size: 20Gi
```

MinIO endpoint inside cluster: `http://minio.storage.svc.cluster.local:9000`

## Nessie Configuration

Project Nessie acts as the Iceberg REST catalog:

```yaml
# helm/nessie/values.yaml
service:
  type: ClusterIP
  port: 19120
```

Nessie endpoint: `http://nessie.storage.svc.cluster.local:19120/api/v1`

## Trino Iceberg Catalog

```yaml
# Trino catalog config via Helm values
additionalCatalogs:
  iceberg: |
    connector.name=iceberg
    iceberg.catalog.type=rest
    iceberg.rest-catalog.uri=http://nessie.storage.svc.cluster.local:19120/api/v1
    iceberg.rest-catalog.warehouse=s3://lakehouse/
    hive.s3.endpoint=http://minio.storage.svc.cluster.local:9000
    hive.s3.path-style-access=true
    hive.s3.aws-access-key=minioadmin
    hive.s3.aws-secret-key=minioadmin
    hive.s3.ssl.enabled=false
```

## Spark Iceberg Configuration

```python
spark = SparkSession.builder \
    .config("spark.jars.packages",
            "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0,"
            "org.projectnessie.nessie-integrations:nessie-spark-extensions-3.5_2.12:0.80.0") \
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog") \
    .config("spark.sql.catalog.iceberg.catalog-impl",
            "org.apache.iceberg.nessie.NessieCatalog") \
    .config("spark.sql.catalog.iceberg.uri",
            "http://nessie.storage.svc.cluster.local:19120/api/v1") \
    .config("spark.sql.catalog.iceberg.ref", "main") \
    .config("spark.sql.catalog.iceberg.warehouse", "s3://lakehouse/") \
    .config("spark.hadoop.fs.s3a.endpoint",
            "http://minio.storage.svc.cluster.local:9000") \
    .config("spark.hadoop.fs.s3a.access.key", "minioadmin") \
    .config("spark.hadoop.fs.s3a.secret.key", "minioadmin") \
    .config("spark.hadoop.fs.s3a.path.style.access", "true") \
    .config("spark.hadoop.fs.s3a.impl",
            "org.apache.hadoop.fs.s3a.S3AFileSystem") \
    .getOrCreate()
```

## dbt Trino Profile

```yaml
# profiles.yml
data_platform:
  target: dev
  outputs:
    dev:
      type: trino
      method: none
      host: trino.serving.svc.cluster.local
      port: 8080
      database: iceberg
      schema: gold
      threads: 4
```

## Medallion Layer Structure

```
MinIO bucket: lakehouse/
├── bronze/
│   ├── valid/
│   │   └── <source>/<table>/     ← Iceberg table files
│   └── invalid/
│       └── <source>/<table>/
├── silver/
│   └── <domain>/<entity>/
└── gold/
    └── <domain>/<view_name>/     ← dbt models land here
```

## Iceberg Table Operations (Trino)

```sql
-- Create Iceberg table
CREATE TABLE iceberg.silver.orders (
    order_id BIGINT,
    customer_id BIGINT,
    amount DECIMAL(10,2),
    created_at TIMESTAMP(6),
    _ingested_at TIMESTAMP(6)
) WITH (
    format = 'PARQUET',
    partitioning = ARRAY['day(created_at)']
);

-- MERGE for Silver deduplication (CDC)
MERGE INTO iceberg.silver.orders t
USING (SELECT * FROM iceberg.bronze.valid.orders) s
ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET ...
WHEN NOT MATCHED THEN INSERT ...;

-- Expire old snapshots (maintenance)
ALTER TABLE iceberg.silver.orders
EXECUTE expire_snapshots(retention_threshold => '7d');
```

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `Connection refused` to MinIO | Wrong endpoint — S3 default is port 443 | Always set `path-style-access=true` and explicit endpoint |
| `403 Forbidden` from MinIO | Wrong credentials or bucket policy | Check `rootUser`/`rootPassword` match in all configs |
| Nessie `404 Not Found` on table | Nessie branch doesn't exist | Use `main` branch or create the branch explicitly |
| Trino slow on large scans | No partition pruning | Ensure queries filter on partition columns |
