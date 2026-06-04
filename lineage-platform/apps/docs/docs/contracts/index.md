---
title: Contracts
sidebar_label: Contracts
---

# Contracts

The [`lineage-contracts/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/lineage-contracts)
directory is the single source of truth for everything that has to
agree across the four parsers + the gateway.

## What's in there

| File | Contents |
|---|---|
| `schema/neo4j-constraints.cypher` | Applied on Neo4j boot. Uniqueness + indexes across every label. |
| `schema/node-id-rules.md` | Per-label canonical-string definitions. |
| `schema/shared-labels.md` | Ownership rules for `:Table` / `:Connection` / `:Attribute` / `:Script`. |
| `schema/postgres/init.sql` | Postgres `projects` + `project_files` schema. |
| `schema/postgres/tws-schema.sql` | TWS mirror tables. |
| `fixtures-index.md` | Catalogue of the canonical cross-parser fixtures. |
| `README.md` | Versioning + consumption pattern. |

## Versioning

Contracts use semver. Breaking changes require a coordinated bump in
every parser's pin AND a Neo4j migration plan. Today the contract is
v0.1.

## How parsers consume it

Each parser's `utils/ids.py` mirrors the canonical-string rules from
`node-id-rules.md`. Each parser's `graph/queries.py` writes only the
labels it owns, and uses the shared-label rules for any shared writes.
No parser bundles the contract — they each carry their own copy of the
relevant code paths, derived from the same spec.

## See also

- [Architecture · Contracts](/architecture/contracts) — the rules in
  detail with examples.
- [Determinism](/architecture/determinism) — what the contract guarantees.
- [Cross-parser convergence](/parsers/convergence) — what the contract makes possible.
- [Fixtures](/contracts/fixtures) — the canonical cross-parser fixture catalogue.
