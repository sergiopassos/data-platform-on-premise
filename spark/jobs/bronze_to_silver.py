"""Bronze → Silver MERGE INTO batch job.

Reads bronze.{table}_valid (Debezium CDC envelope in _raw_value), extracts
payload.after using the ODCS contract schema, and merges into silver.{table}.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
    BooleanType,
    DoubleType,
    FloatType,
)

NESSIE_URI = os.getenv("NESSIE_URI", "http://nessie.infra.svc.cluster.local:19120/api/v1")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio.infra.svc.cluster.local:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio123")
CONTRACTS_DIR = os.getenv("CONTRACTS_DIR", "/contracts")

_ODCS_TO_SPARK = {
    "integer": IntegerType(),
    "int": IntegerType(),
    "long": LongType(),
    "string": StringType(),
    "text": StringType(),
    "float": FloatType(),
    "double": DoubleType(),
    "number": DoubleType(),
    "boolean": BooleanType(),
    # Debezium emits timestamps as epoch microseconds (int64)
    "timestamp": LongType(),
    "date": StringType(),
}


def build_spark_session() -> SparkSession:
    return (
        SparkSession.builder.appName("bronze-to-silver")
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


def load_contract(table_name: str) -> tuple[list[str], StructType]:
    """Return (primary_keys, spark_schema) from ODCS contract YAML."""
    contract_path = Path(CONTRACTS_DIR) / f"{table_name}.yaml"
    if not contract_path.exists():
        raise FileNotFoundError(f"Contract not found: {contract_path}")

    with open(contract_path) as f:
        contract = yaml.safe_load(f)

    fields = contract.get("schema", {}).get("fields", [])
    if not fields:
        raise ValueError(f"No fields in contract schema for {table_name}")

    pks = [f["name"] for f in fields if f.get("primaryKey")]
    if not pks:
        raise ValueError(f"No primaryKey fields in contract for {table_name}")

    spark_fields = [
        StructField(f["name"], _ODCS_TO_SPARK.get(f.get("type", "string"), StringType()), True)
        for f in fields
    ]
    return pks, StructType(spark_fields)


def parse_cdc_envelope(df, schema: StructType):
    """Extract payload.after from the Debezium CDC JSON envelope in _raw_value."""
    after_json = F.get_json_object(F.col("_raw_value"), "$.payload.after")
    parsed = F.from_json(after_json, schema)
    business_cols = [F.col(f"after_data.{f.name}").alias(f.name) for f in schema.fields]
    return (
        df.withColumn("after_data", parsed)
        .select(
            *business_cols,
            F.col("_cdc_op"),
            F.col("_cdc_ts"),
            F.col("_ingested_at"),
        )
        # Drop rows where payload.after is null (e.g. DELETE events have no after)
        .filter(F.col(schema.fields[0].name).isNotNull())
    )


def ensure_silver_table(spark: SparkSession, table_name: str, schema: StructType) -> None:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS nessie.silver")
    fields_ddl = ", ".join(
        f"`{f.name}` {f.dataType.simpleString()}" for f in schema.fields
    )
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS nessie.silver.{table_name} ({fields_ddl})
        USING iceberg
        TBLPROPERTIES ('write.merge.mode'='merge-on-read')
    """)


def process(spark: SparkSession, table_name: str, processing_date: str, reprocess_invalid: bool = False) -> None:
    source_table = f"nessie.bronze.{table_name}_{'invalid' if reprocess_invalid else 'valid'}"
    pks, schema = load_contract(table_name)

    raw_df = (
        spark.table(source_table)
        .filter(F.to_date("_ingested_at") == processing_date)
    )

    if raw_df.rdd.isEmpty():
        print(f"No rows in {source_table} for {processing_date}. Skipping.")
        return

    parsed_df = parse_cdc_envelope(raw_df, schema)

    ensure_silver_table(spark, table_name, schema)

    pk_window = ", ".join(pks)
    dedup_df = (
        parsed_df
        .withColumn(
            "_rn",
            F.row_number().over(
                __import__("pyspark.sql.window", fromlist=["Window"])
                .Window.partitionBy(*pks)
                .orderBy(F.col("_cdc_ts").desc())
            ),
        )
        .filter(F.col("_rn") == 1)
        .drop("_rn")
    )

    join_condition = " AND ".join([f"t.`{pk}` = s.`{pk}`" for pk in pks])
    dedup_df.createOrReplaceTempView("source_batch")

    spark.sql(f"""
        MERGE INTO nessie.silver.{table_name} AS t
        USING source_batch AS s
        ON {join_condition}
        WHEN MATCHED AND s._cdc_op = 'd' THEN DELETE
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)

    count = spark.sql(f"SELECT COUNT(*) FROM nessie.silver.{table_name}").collect()[0][0]
    print(f"Silver MERGE complete for {table_name} on {processing_date}. Silver rows: {count}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", required=True)
    parser.add_argument("--date", required=True, help="Processing date YYYY-MM-DD")
    parser.add_argument("--reprocess-invalid", action="store_true", default=False)
    args = parser.parse_args()

    spark = build_spark_session()
    process(spark, args.table, args.date, args.reprocess_invalid)


if __name__ == "__main__":
    main()
