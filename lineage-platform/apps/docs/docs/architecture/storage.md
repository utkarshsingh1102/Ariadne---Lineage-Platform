---
title: Storage
sidebar_label: Storage
---

# Storage

Two stores. Each does one job well.

## Neo4j — the graph

| What | Where |
|---|---|
| All parsed nodes (`:Workbook`, `:Schedule`, `:DataFrame`, `:QlikScript`, plus shared `:Table`/`:Connection`/`:Attribute`/`:Script`) | Neo4j 5.20 |
| All lineage edges (`USES_DATASOURCE`, `READS_TABLE`, `CONTAINS_JOB`, `DERIVES_FROM`, …) | Neo4j |
| Uniqueness constraints + indexes | Applied on boot from `lineage-contracts/schema/neo4j-constraints.cypher` via `neo4j-init` |
| APOC procs | Loaded; available for advanced analytics |

Browser: `http://localhost:7475/` (auth `neo4j` / `lineagepass`).
Bolt: `bolt://localhost:7688/`.

## Postgres — operational mirror + project metadata

Postgres holds two distinct schemas:

| Schema | Tables | Purpose |
|---|---|---|
| **`tws.`** | `schedules`, `jobs`, `job_dependencies`, `schedule_dependencies`, `resources`, `job_resources`, `file_watchers`, `job_file_dependencies` | Operational mirror of TWS schedules — supports SQL-style queries by `start_time` window, `script_path` substring, etc. Maintained by `tws-parser`'s `rdbms/writer.py`. |
| **`public.`** | `projects`, `project_files` | Project metadata. A project groups N parsed files into a named collection. Owned by the gateway. |

Schemas applied at first boot via `docker-entrypoint-initdb.d/`:

```yaml
# excerpt from docker-compose.yml
postgres:
  volumes:
    - ./infra/postgres/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    - ../lineage-contracts/schema/postgres/tws-schema.sql:/docker-entrypoint-initdb.d/02-tws-schema.sql:ro
```

Both scripts are idempotent (`IF NOT EXISTS`) so re-running them is
harmless. **They only auto-apply on first boot of an empty data
volume.** To apply to an existing volume, run:

```bash
docker exec -i lineage-postgres psql -U lineage -d lineage \
  < lineage-contracts/schema/postgres/tws-schema.sql
```

## Volumes

| Volume | Mount | Survives `docker compose down` | Survives `docker compose down -v` |
|---|---|---|---|
| `neo4j_data` | `/data` in neo4j | ✓ | ✗ |
| `neo4j_logs` | `/logs` in neo4j | ✓ | ✗ |
| `postgres_data` | `/var/lib/postgresql/data` | ✓ | ✗ |

The `uploads/` bind mount (host `./uploads` → container `/data/uploads`)
is **not** a docker volume — it's a regular directory. Re-creating the
stack with `down -v` does not delete uploaded files.

## See also

- [Contracts](/architecture/contracts) — what the constraints enforce.
- [Deploy → Operations](/deploy/operations) — backup / restore patterns.
