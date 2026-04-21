FROM quay.io/strimzi/kafka:0.41.0-kafka-3.7.0
USER root:root
RUN mkdir -p /opt/kafka/plugins/debezium-postgres \
    && curl -sL https://repo1.maven.org/maven2/io/debezium/debezium-connector-postgresql/2.6.2.Final/debezium-connector-postgresql-2.6.2.Final-plugin.tar.gz \
       | tar xz -C /opt/kafka/plugins/debezium-postgres
USER 1001
