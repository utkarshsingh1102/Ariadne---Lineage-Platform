#!/usr/bin/env bash
# Daily Spark batch entry point.
set -e

spark-submit --master yarn --deploy-mode cluster \
    --conf spark.executor.memory=4g \
    --py-files lib/utils.py,lib/connectors.py \
    /jobs/ingest_orders.py prod 2024-01-01

# Second job — runs after ingest completes.
spark-submit \
    --conf spark.sql.shuffle.partitions=400 \
    /jobs/transform_orders.py prod orders_enriched
