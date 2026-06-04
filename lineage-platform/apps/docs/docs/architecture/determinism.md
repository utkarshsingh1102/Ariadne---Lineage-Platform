---
title: Determinism
sidebar_label: Determinism
---

# Determinism

Why parsing the same file twice produces byte-identical graph writes —
and why that property is **load-bearing** for the whole platform.

## The property

For any input `F` and parser `P`:

```
ids(P(F)) ≡ ids(P(F))      # twice in the same run
ids(P(F)) ≡ ids(P'(F))     # different runs, different days
```

And critically:

```
ids(Tableau(F_twb))  ∩  ids(Spark(F_py))  ⊇  ids(:Table common to both)
```

The third property is what makes cross-parser lineage actually work —
see [Cross-parser convergence](/parsers/convergence).

## How it's guaranteed

1. **Canonical strings, not raw input.** Every id starts from a
   `::`-separated tuple of lowercased, trimmed fields documented in
   `lineage-contracts/schema/node-id-rules.md`. The hash never sees
   whitespace variation, case differences, or trailing slashes.
2. **SHA-256, truncated to 16 hex chars.** Cryptographically
   collision-resistant, short enough to be browsable in Neo4j.
3. **Pure functions.** Every `*_id()` helper is a top-level pure
   function in each parser's `utils/ids.py` — no module-level state,
   no random salts, no time-of-day.
4. **MERGE-only writes.** The writer never `CREATE`s; it always
   `MERGE`s on id. Constraints (`CREATE CONSTRAINT … UNIQUE`) make a
   parser that drifts from the contract fail loudly at write time
   instead of silently producing duplicates.

## How it's tested

Every parser ships an idempotency test that:
1. Parses one fixture twice.
2. Asserts the set of `node_id`s is identical.
3. Asserts the set of `(source_id, edge_label, target_id, props_hash)`
   tuples is identical.

Cross-parser convergence is tested by:
1. Parsing fixture `A.twb` (Tableau) and `B.py` (Spark) that both
   reference `analytics.orders`.
2. Asserting `MATCH (t:Table {fully_qualified_name: 'analytics.orders'}) RETURN count(t)` is 1.

## What's NOT deterministic

- **Timestamps.** Every node gets `ingested_at`/`last_seen_at` from the
  writer; these are NOT part of the id and not compared in
  idempotency tests.
- **Parse warnings list ordering.** Diagnostics are emitted in
  encounter order, which can vary with Python set iteration. Tests
  compare warnings as sorted sets.

## See also

- [Contracts](/architecture/contracts) — the actual canonical-string definitions.
- [Cross-parser convergence](/parsers/convergence) — the payoff.
