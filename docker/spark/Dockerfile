FROM apache/spark:3.5.1-python3

USER root

RUN pip install --no-cache-dir "datacontract-cli==0.10.8"

RUN BASE="https://repo1.maven.org/maven2" && \
    cd /opt/spark/jars && \
    curl -fsSL "$BASE/org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/1.5.2/iceberg-spark-runtime-3.5_2.12-1.5.2.jar" -o iceberg-spark-runtime.jar && \
    curl -fsSL "$BASE/org/projectnessie/nessie-integrations/nessie-spark-extensions-3.5_2.12/0.80.0/nessie-spark-extensions-3.5_2.12-0.80.0.jar" -o nessie-spark-extensions.jar && \
    curl -fsSL "$BASE/org/apache/spark/spark-sql-kafka-0-10_2.12/3.5.1/spark-sql-kafka-0-10_2.12-3.5.1.jar" -o spark-sql-kafka.jar && \
    curl -fsSL "$BASE/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar" -o hadoop-aws.jar && \
    curl -fsSL "$BASE/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar" -o aws-java-sdk-bundle.jar && \
    curl -fsSL "$BASE/org/apache/spark/spark-token-provider-kafka-0-10_2.12/3.5.1/spark-token-provider-kafka-0-10_2.12-3.5.1.jar" -o spark-token-provider-kafka.jar && \
    curl -fsSL "$BASE/org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar" -o kafka-clients.jar && \
    curl -fsSL "$BASE/org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar" -o commons-pool2.jar

USER spark
