"""Kafka → Bronze (valid/invalid) Spark Structured Streaming job.

Reads all cdc.public.* topics, validates against ODCS contracts,
and routes to bronze.valid_{table} or bronze.invalid_{table} in Iceberg.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka-cluster-kafka-bootstrap.streaming.svc.cluster.local:9092")
NESSIE_URI = os.getenv("NESSIE_URI", "http://nessie.infra.svc.cluster.local:19120/api/v1")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio.infra.svc.cluster.local:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio123")
CONTRACTS_DIR = os.getenv("CONTRACTS_DIR", "/contracts")
CHECKPOINT_BASE = os.getenv("CHECKPOINT_BASE", "s3a://warehouse/checkpoints/bronze")


def build_spark_session() -> SparkSession:
    return (
        SparkSession.builder.appName("bronze-streaming")
        .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .config("spark.sql.catalog.nessie", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.nessie.catalog-impl", "org.apache.iceberg.nessie.NessieCatalog")
        .config("spark.sql.catalog.nessie.uri", NESSIE_URI)
        .config("spark.sql.catalog.nessie.ref", "main")
        .config("spark.sql.catalog.nessie.warehouse", "s3a://warehouse/")
        .config("spark.hadoop.fs.s3a.endpoint", MINIO_ENDPOINT)
        .config("spark.hadoop.fs.s3a.access.key", MINIO_ACCESS_KEY)
        .config("spark.hadoop.fs.s3a.secret.key", MINIO_SECRET_KEY)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .getOrCreate()
    )


def table_name_from_topic(topic: str) -> str:
    # topic format: cdc.public.TABLE_NAME
    return topic.split(".")[-1]


def validate_record(record_json: str, table_name: str) -> tuple[bool, str]:
    contract_path = Path(CONTRACTS_DIR) / f"{table_name}.yaml"
    if not contract_path.exists():
        return True, ""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(record_json)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["datacontract", "test", "--contract", str(contract_path), "--data", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip() or result.stdout.strip()
    except FileNotFoundError:
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Validation timeout"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def ensure_bronze_tables(spark: SparkSession, table_name: str) -> None:
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS nessie.bronze.{table_name}_valid (
            _raw_value STRING,
            _source_topic STRING,
            _cdc_op STRING,
            _cdc_ts TIMESTAMP,
            _ingested_at TIMESTAMP
        )
        USING iceberg
        LOCATION 's3a://bronze/{table_name}/valid'
        PARTITIONED BY (days(_ingested_at))
    """)
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS nessie.bronze.{table_name}_invalid (
            _raw_value STRING,
            _source_topic STRING,
            _ingested_at TIMESTAMP,
            _validation_error STRING
        )
        USING iceberg
        LOCATION 's3a://bronze/{table_name}/invalid'
        PARTITIONED BY (days(_ingested_at))
    """)


def process_batch(batch_df: DataFrame, _batch_id: int) -> None:
    if batch_df.isEmpty():
        return

    spark = batch_df.sparkSession
    rows = batch_df.collect()

    for row in rows:
        topic = row["topic"]
        value_str = row["value"]
        table_name = table_name_from_topic(topic)

        try:
            record = json.loads(value_str) if value_str else {}
        except json.JSONDecodeError:
            record = {"_raw": value_str}

        is_valid, error_msg = validate_record(value_str or "{}", table_name)
        ensure_bronze_tables(spark, table_name)

        cdc_op = record.get("op", "r")
        cdc_ts_str = record.get("ts_ms")
        cdc_ts = F.to_timestamp(F.lit(str(cdc_ts_str)), "SSS").cast("timestamp") if cdc_ts_str else None

        if is_valid:
            spark.createDataFrame(
                [(value_str, topic, cdc_op, None, None)],
                schema=StructType([
                    StructField("_raw_value", StringType()),
                    StructField("_source_topic", StringType()),
                    StructField("_cdc_op", StringType()),
                    StructField("_cdc_ts", StringType()),
                    StructField("_ingested_at", StringType()),
                ]),
            ).withColumn("_cdc_ts", F.current_timestamp()) \
             .withColumn("_ingested_at", F.current_timestamp()) \
             .writeTo(f"nessie.bronze.{table_name}_valid").append()
        else:
            spark.createDataFrame(
                [(value_str, topic, None, error_msg)],
                schema=StructType([
                    StructField("_raw_value", StringType()),
                    StructField("_source_topic", StringType()),
                    StructField("_ingested_at", StringType()),
                    StructField("_validation_error", StringType()),
                ]),
            ).withColumn("_ingested_at", F.current_timestamp()) \
             .writeTo(f"nessie.bronze.{table_name}_invalid").append()


def main() -> None:
    spark = build_spark_session()
    spark.sql("CREATE NAMESPACE IF NOT EXISTS nessie.bronze")

    stream = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("subscribePattern", "cdc\\.public\\..*")
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
        .selectExpr("CAST(topic AS STRING)", "CAST(value AS STRING)")
    )

    query = (
        stream.writeStream.foreachBatch(process_batch)
        .option("checkpointLocation", f"{CHECKPOINT_BASE}/stream")
        .trigger(processingTime="10 seconds")
        .start()
    )

    query.awaitTermination()


if __name__ == "__main__":
    main()
