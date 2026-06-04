---
title: Operations
sidebar_label: Operations
---

# Deploy → Operations

## Day-to-day commands

```bash
# Bring everything up
cd lineage-platform && docker compose up -d

# Stop everything (preserves data)
docker compose down

# Tail logs for one service
docker compose logs -f gateway

# Rebuild one service after a code change
docker compose up -d --build tws-parser

# Open a shell in a running container
docker exec -it lineage-gateway sh
```

## Re-applying the Postgres TWS schema

The TWS schema only auto-applies on **first boot** of an empty data
volume. For an existing volume:

```bash
docker exec -i lineage-postgres psql -U lineage -d lineage \
  < lineage-contracts/schema/postgres/tws-schema.sql
```

The DDL is idempotent (`IF NOT EXISTS`).

## Wiping the graph (testing)

```bash
docker exec lineage-neo4j cypher-shell -u neo4j -p lineagepass \
  "MATCH (n) WHERE n:Schedule OR n:JobStream OR n:Job OR n:Workstation \
            OR n:Calendar OR n:Prompt OR n:EventRule OR n:Resource \
            OR n:FileWatcher OR n:TwsFile OR (n:Script AND n.source_system='tws') \
   DETACH DELETE n;"
```

## EC2 stop / start (cost savings)

```bash
aws ec2 stop-instances --instance-ids <id>
aws ec2 start-instances --instance-ids <id>
aws ec2 describe-instances --instance-ids <id> \
  --query 'Reservations[].Instances[].State.Name'
```

Stopping the instance preserves the EBS volume and Elastic IP. Cost
drops from ~$33/mo to ~$6/mo while stopped — see [AWS](/deploy/aws).

## Backups

The current setup does **not** automatically back up Neo4j or
Postgres. For production use, schedule:

- `neo4j-admin database backup` against the `neo4j_data` volume.
- `pg_dump` against the `lineage` database.

Both can run as sidecar `oneshot` services under cron — out of scope
for v0.1.

## See also

- [Storage](/architecture/storage) — what's in each datastore.
- [AWS](/deploy/aws) — Terraform deployment.
