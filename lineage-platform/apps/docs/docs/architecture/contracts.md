---
title: Contracts
sidebar_label: Contracts
---

# Contracts

`lineage-contracts/` is the single source of truth that lets four
parsers from four different teams produce a coherent knowledge graph.
It's checked into git and consumed by every parser at runtime + every
Neo4j boot via `docker-entrypoint-initdb.d/`.

## Files

| File | Owner | Consumer |
|---|---|---|
| `schema/neo4j-constraints.cypher` | Contracts repo | Applied on Neo4j boot by `neo4j-init` |
| `schema/node-id-rules.md` | Contracts repo | Every parser's `utils/ids.py` mirrors these rules |
| `schema/shared-labels.md` | Contracts repo | Lists ownership rules for `:Table` / `:Connection` / `:Attribute` / `:Script` |
| `schema/postgres/init.sql` | Contracts repo | Postgres bootstrap (projects table) |
| `schema/postgres/tws-schema.sql` | Contracts repo | TWS mirror tables (`tws.schedules`, `tws.jobs`, ...) |
| `fixtures-index.md` | Contracts repo | Catalogue of canonical fixtures |

## Identity rule (one sentence)

> Every node id is `sha256(canonical_string)[:16]`, where
> `canonical_string` is a `::`-separated tuple defined per-label,
> lowercased and trimmed.

For example:

| Label | Canonical string | Example |
|---|---|---|
| `:Workbook` | `workbook::<absolute-path>` | `workbook::/users/.../store.twb` → `a4c0cbb318265102` |
| `:Schedule` | `schedule::<workstation>::<scheduler>::<name>` | `schedule::ws_prod::etl::nightly_load` |
| `:TwsFile` | `tws_file::<absolute-path>` | `tws_file::/data/uploads/.../tws1.txt` |
| `:Table` (shared) | `table::<fully_qualified_name>` | `table::analytics.orders` |
| `:Connection` (shared) | `connection::<class>::<server>::<dbname>` | `connection::snowflake::prod::analytics` |
| `:Script` (shared) | `script::<absolute-path>` | `script::/etl/load_orders.py` |

The full table lives in
[`lineage-contracts/schema/node-id-rules.md`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-contracts/schema/node-id-rules.md).

## Shared-label ownership

These labels are owned by **multiple parsers** — whichever sees the
entity first writes the node; later parsers MERGE onto it:

- **`:Table`** — Tableau, QlikView, Spark, and future Ab Initio / BTEQ
  parsers all write tables they read or expose.
- **`:Connection`** — Tableau, QlikView, Ab Initio.
- **`:Attribute`** — every parser that touches columns.
- **`:Script`** — TWS today, Ab Initio / BTEQ in future.

A unique constraint on each shared label's id (or
`fully_qualified_name` for `:Table`) makes the MERGE deterministic.

## What gates the contract

- **Idempotency tests** in every parser: parse twice, assert
  node-set is byte-identical.
- **Neo4j constraints** applied at boot enforce uniqueness on every
  id field; a parser that drifts from the contract fails at the
  MERGE step instead of producing duplicates.

## See also

- [Determinism](/architecture/determinism) — what guarantees the id stability.
- [Cross-parser convergence](/parsers/convergence) — what this contract makes possible.
- [Storage](/architecture/storage) — what each datastore is responsible for.
