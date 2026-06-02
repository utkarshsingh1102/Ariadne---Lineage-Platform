# QlikView Parser — Test Suite Run Report

**Run date:** 2026-05-24
**Branch / commit:** `master` @ `bfaa06f`
**Environment:** Python 3.11.14, pytest 9.0.3, macOS (darwin)
**Command:** `pytest tests/ -v`
**Runtime:** 0.28 s
**Suite definition:** `tests/` (see `tests/README.md`)

---

## 1. Headline numbers

| Outcome | Count | % of total |
|---|---:|---:|
| ✅ **PASSED** | **33** | 41% |
| ❌ **FAILED** | **0** | 0% |
| ⚠️ **XFAIL** (expected failure — known gap vs plan) | **35** | 43% |
| ⚠️ **XPASS** (unexpected pass — loose assertion or stale marker) | **4** | 5% |
| ⏭️ **SKIPPED** (requires Neo4j) | **9** | 11% |
| **TOTAL collected** | **81** | 100% |

**Headline:** the current code correctly handles ~41% of the plan's contract surface. ~43% is provably broken (each xfail cites the plan/REVIEW.md line that introduces it). 11% can't be evaluated until a Neo4j instance is running. The 4 unexpected passes (XPASS) need a 2-minute investigation each — see §4.

---

## 2. What works today (33 PASSED — protect against regression)

| File | Test | What it proves |
|---|---|---|
| `integration/test_end_to_end.py` | `test_fixture_parses_without_exception[01..10]` × 10 | Every fixture parses to completion without raising — the parser is robust to all 10 input shapes |
| `integration/test_end_to_end.py` | `test_json_export_round_trip` | JSON export shape stable; output written + readable |
| `integration/test_end_to_end.py` | `test_realistic_dashboard_under_20s` | Kitchen-sink fixture parses well under the 20s plan §15 budget |
| `unit/test_connections.py` | `test_odbc_connection_captured`, `test_odbc_data_source_populated`, `test_no_connections_when_script_has_none` | ODBC extraction correct on the simple form |
| `unit/test_expressions.py` | `test_synthetic_field_alias_captured`, `test_upper_alias_captured`, `test_qlikview_function_blacklist_excludes_builtins` | Synthetic fields with `<expr> AS <alias>` recognised; built-in functions filtered |
| `unit/test_field_extraction.py` | `test_aliased_field_uses_alias_name` | Aliased columns expose the alias, not the underlying expression |
| `unit/test_includes.py` | `test_legacy_include_recorded`, `test_include_cycle_does_not_hang` | Legacy `INCLUDE 'path'` resolved; cycle protection works |
| `unit/test_join.py` | `test_explicit_target_join_captured` | Explicit-target form `JOIN (Table) LOAD ... RESIDENT ...` works |
| `unit/test_load_statement.py` | `test_simple_sql_load_table_name`, `test_simple_sql_load_source_type`, `test_multiple_loads_counted`, `test_load_order_is_strictly_increasing` | LOAD-block boundary detection + ordering correct |
| `unit/test_resident.py` | `test_resident_source_type`, `test_resident_source_table_name`, `test_resident_load_fields_include_aliases` | RESIDENT correctly identified and source-table resolved |
| `unit/test_sql_block.py` | `test_sql_query_captured`, `test_physical_table_lifted_from_sql`, `test_sqlglot_blacklist_excludes_keywords`, `test_sqlglot_handles_join` | sqlglot integration extracts physical tables; keyword blacklist works |

**These 33 tests must stay green through the rewrite — they are the regression net.**

---

## 3. What's broken (35 XFAIL — the explicit TODO list)

Grouped by remediation phase from `REVIEW.md §7`.

### Phase 0 — Hotfixes (these alone unblock most of the suite)

| # | Test | REVIEW ref | Effort |
|---|---|---|---|
| 1 | `test_no_field_contains_semicolon` | §4.1 — field list bleeds past `;` | S |
| 2 | `test_no_field_is_a_sql_keyword` | §4.5 — `SQL SELECT` leaks into fields | S |
| 3 | `test_load_field_list_has_no_duplicates` | §4.5 — duplicates from SQL bleed | S |
| 4 | `test_simple_sql_load_field_list` | §4.1 (visible on the simplest fixture) | S |
| 5 | `test_field_list_does_not_leak_semicolon` | §4.1 | S |
| 6 | `test_loads_do_not_bleed_into_each_other` | §4.2 — block-boundary bug | M |
| 7 | `test_line_comment_does_not_affect_parsing` | §4.3 — `//` not stripped | S |
| 8 | `test_block_comment_does_not_affect_parsing` | §4.3 — `/* */` not stripped | S |
| 9 | `test_rem_comment_does_not_affect_parsing` | §4.3 — `REM ... ;` not stripped | S |
| 10 | `test_double_slash_inside_string_literal_preserved` | guard against naive comment stripper | M |
| 11 | `test_resident_keyword_in_comment_is_ignored` | §4.3 — `// Resident` false-match | S |
| 12 | `test_string_literals_not_treated_as_fields` | §4.6 — `'HIGH'`, `'LOW'` as fields | S |
| 13 | `test_implicit_target_join_captured` | §4.4 — JOIN without `(target)` dropped | M |

**Estimated Phase 0 effort:** ~1–2 days. Unblocks 13 of 35 xfails.

### Phase 1 — Schema alignment

| # | Test | REVIEW ref | Effort |
|---|---|---|---|
| 14 | `test_physical_table_uses_fully_qualified_name` | §3.4 — must key on `db.schema.table` | M |
| 15 | `test_attribute_ids_are_deterministic` | §3.4 — SHA-256 IDs not implemented | M |
| 16 | All 8 `test_neo4j_schema.py::*` tests (currently SKIPPED) | §3.4 — labels/edges diverge from plan | L |

**Estimated Phase 1 effort:** ~3–5 days.

### Phase 2 — ANTLR grammar

| # | Test | REVIEW ref | Effort |
|---|---|---|---|
| 17 | `test_star_load_resolves_fields_from_source_table` | Plan §14 — `LOAD *` star-field resolution | L |
| 18 | `test_star_resident_inherits_fields` | Plan §14 | L |
| 19 | `test_preceding_load_chain_keeps_transformed_columns` | Plan §14 — nested LOAD chains | L |
| 20 | `test_teradata_qualify_clause_parses` | Plan §14 — dialect detection for sqlglot | M |

### Phase 3 — Variables / macros / subroutines / includes

| # | Test | REVIEW ref | Effort |
|---|---|---|---|
| 21 | `test_set_variable_captured` | §3.3 | M |
| 22 | `test_let_variable_captured` | §3.3 | M |
| 23 | `test_variable_scope_recorded` | Plan §2.7 | S |
| 24 | `test_macro_expanded_in_sql` | §3.3 — `$(varName)` not substituted | L |
| 25 | `test_macro_substituted_before_sql_parse` | §3.3 | L |
| 26 | `test_subroutine_definition_captured` | §3.3 — `SUB / END SUB` | M |
| 27 | `test_subroutine_params_recorded` | §3.3 | S |
| 28 | `test_call_site_inlines_loads` | §3.3 — `CALL` not inlined | L |
| 29 | `test_modern_include_directive_resolved` | §3.3 — `$(Include=...)` not parsed | M |
| 30 | `test_include_depth_limit_enforced` | Plan §8 — `QLIK_MAX_INCLUDE_DEPTH` | S |
| 31 | `test_lib_connect_to_captured` | §3.3 — `LIB CONNECT TO` ignored | S |
| 32 | `test_connections_from_included_file_propagate` | §3.3 — needs `$(Include=...)` first | depends |
| 33 | `test_oledb_connection_in_realistic_fixture` | Plan §2.1 — `[bracketed]` OLEDB form | S |
| 34 | `test_concatenate_target_recorded` | §3.3 — `CONCATENATE` ignored | M |
| 35 | `test_concatenate_in_realistic_fixture` | §3.3 | depends |
| 36 | `test_keep_captured_as_join` | Plan §2.6 — `KEEP` not handled | M |
| 37 | `test_mapping_load_recognised` | Plan §2.6 — `MAPPING LOAD` not handled | M |
| 38 | `test_realistic_dashboard_stats` | aggregate across the above | rolls up |

---

## 4. XPASS — 4 tests passed unexpectedly (investigate)

These tests **passed** even though they were marked as expected-to-fail. Each one means: either the underlying construct is *accidentally* satisfied by current code (loose assertion), or the xfail marker is stale and can come off. The developer should look at each and decide.

| Test | Why it likely XPASSes today | Action |
|---|---|---|
| `test_concatenate.py::test_noconcatenate_creates_separate_table` | The `SalesQ3:` LABEL is parsed by the regex; the `NOCONCATENATE` *keyword* is silently ignored. Test only asserts the label is in `app.loads` — too loose. | Tighten: assert `NOCONCATENATE` is recorded as a property on the load |
| `test_includes.py::test_must_include_resolved` | `common.qvs` is also pulled by the legacy `INCLUDE 'common.qvs'` at the bottom of fixture 08. Passes via the wrong code path. | Tighten: remove the legacy include from fixture 08 so the test only passes if `$(Must_Include=...)` actually works |
| `test_load_statement.py::test_preceding_load_chain_keeps_transformed_columns` | `_extract_field_list` happens to capture aliased columns from the outer LOAD; nested preceding-LOAD chains still aren't resolved correctly, but the loose `"CustomerName_Upper" in customer.fields` check passes. | Tighten: also assert the lineage *back* to the inner LOAD's columns (e.g. `Customer.Region` derives from `CountryCode` via ApplyMap) |
| `test_sql_block.py::test_teradata_qualify_clause_parses` | sqlglot in default dialect partially parses the statement and lifts `PROD.SALES.ORDERS` even with `QUALIFY`. `assert source_table is not None` passes by accident. | Tighten: assert the FQN exactly matches `PROD.SALES.ORDERS` (catches partial-parse cases) |

**None of these is a regression** — they're all tests where the assertion is just too forgiving. They're listed so the developer doesn't false-confidence themselves into thinking those features work.

---

## 5. SKIPPED — 9 Neo4j integration tests

All 9 skipped tests are gated by `@pytest.mark.neo4j`. They require:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=<your-password>
pytest tests/integration/ -m neo4j -v
```

Expected behaviour when Neo4j is available: **all 9 currently XFAIL** — they assert the plan's schema (`:QlikScript`, `:Connection`, `:Table` with FQN key, `JOINS_WITH`, `LOADS_FROM_TABLE`, uniqueness constraints, idempotency, cross-parser merge) and the current writer emits a different schema (`:QlikViewApp`, `:Report`, `:SourceTable`, `READS_FROM`, no constraints). They will turn green only after `REVIEW.md §7` Phase 1 lands.

| Test | Asserts |
|---|---|
| `test_qlikscript_node_created` | Label is `:QlikScript`, not `:QlikViewApp` |
| `test_connection_node_created` | `:Connection` nodes are actually written (currently they're extracted but never persisted) |
| `test_physical_table_label_is_Table` | Physical tables use `:Table`, not `:SourceTable` (cross-parser-merge requirement) |
| `test_uses_connection_edge` | `USES_CONNECTION` relationship exists |
| `test_loads_from_table_edge` | `LOADS_FROM_TABLE` exists (currently `READS_FROM`) |
| `test_joins_with_edge` | `JOINS_WITH` edges written (currently extracted but never persisted) |
| `test_uniqueness_constraints_exist` | Plan §5.3 constraints created on first run |
| `test_reparsing_is_idempotent` | Re-parse produces zero diff |
| `test_no_duplicate_physical_table_after_qlik_parse` | Cross-parser merge: pre-seed `:Table {PROD.SALES.CUSTOMER}`, parse `.qvs`, no duplicate |

---

## 6. Raw pytest summary line

```
============= 33 passed, 9 skipped, 35 xfailed, 4 xpassed in 0.28s =============
```

No `failed`, no `error`. Suite is **green** — meaning the existing code holds the contract for the things it claims to support, and every gap is documented as an `xfail` rather than masked.

---

## 7. How to read this as the developer

1. **Open `REVIEW.md` first** — it explains the architecture problems and the phased roadmap.
2. **Open this report second** — it tells you exactly which tests to watch.
3. **Start Phase 0** (1–2 days). When it's done, re-run `pytest tests/` and expect ~13 of the xfails to turn into passes (or, more precisely, the xfail markers can be removed one by one). Aim: 46 passed, 22 xfailed.
4. **Continue down the phases** in `REVIEW.md §7`. The number of xfails is your burn-down chart.
5. **Don't ignore XPASS.** Tighten the 4 assertions in §4 before they hide real gaps.
6. **Spin up Neo4j** for Phase 1+. The 9 skipped tests turn into 9 xfails, then green as schema alignment lands.

---

## 8. Reproduce

```bash
# From the project root
pip install -r requirements.txt
pip install -r tests/requirements-test.txt
pytest tests/ -v

# Or, excluding Neo4j integration:
pytest tests/ -v -m "not neo4j"

# Or, just to see the TODO list:
pytest tests/ --tb=no -q | grep XFAIL
```
