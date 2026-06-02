# QlikView Parser — Implementation Review

**Reviewer:** Utkarsh
**Date:** 2026-05-24
**Branch / commit:** `master` @ `bfaa06f`
**Scope:** `qlikview_parser.py`, `BIW1_Main.qvs`, `common_transformations.qvs`, `parsed_qlikview.json`, `requirements.txt`, `readme.md`
**Reference spec:** `qlikview-parser-plan.md` (the implementation plan)

This is a hand-off review for the teammate picking up the work. It captures (a) what the current code actually does, (b) where it diverges from the plan, (c) concrete bugs visible in `parsed_qlikview.json`, and (d) a phased remediation roadmap. Every gap is pinned to a file/line so it can be opened and fixed without re-deriving the analysis.

---

## 1. Executive summary

The current state is a **single-file regex prototype** (~1305 lines in `qlikview_parser.py`). It successfully ingests a `.qvs` file, extracts ODBC connections, some LOAD blocks, synthetic fields, and writes a small graph to Neo4j. End-to-end, the script runs and produces output.

Versus the plan in `qlikview-parser-plan.md`, the implementation is materially incomplete:

- **Parser engine:** plan mandates ANTLR 4 with `.g4` grammar files; code uses ad-hoc regex.
- **Architecture:** plan mandates a multi-module package (`api/`, `parser/`, `visitor/`, `graph/`, `models/`, `utils/`, `generated/`); code is one monolithic file.
- **Neo4j schema:** node labels and relationship types in code do **not** match the plan — this breaks cross-parser lineage with the Tableau/Ab Initio/Teradata/SAS parsers.
- **API, Docker, tests, CI, config-via-env:** none present.
- **Output is wrong** for the only non-trivial fixture (`BIW1_Main.qvs`) — see §4.

Estimated coverage of the plan: **~15–20%**. Estimated effort to reach the plan's "Definition of done": **3–5 engineer-weeks** depending on how aggressively the regex prototype is reused versus rewritten.

---

## 2. What is currently implemented (be fair to the code)

These things **do** work and should be preserved when refactoring:

| Capability | Location | Notes |
|---|---|---|
| ODBC connection extraction | [qlikview_parser.py:410-418](qlikview_parser.py#L410-L418) | Captures connection name correctly |
| OLEDB connection extraction + `Data Source=` / `Server=` parsing | [qlikview_parser.py:421-440](qlikview_parser.py#L421-L440) | Best-effort but functional |
| Embedded `SQL SELECT` parsing via `sqlglot` | [qlikview_parser.py:277-337](qlikview_parser.py#L277-L337) | With keyword/short-token blacklist — sane idea, keep it |
| Synthetic field extraction (`<expr> AS <alias>`) | [qlikview_parser.py:690-727](qlikview_parser.py#L690-L727) | Formula and source-field extraction works |
| QlikView built-in function blacklist when extracting source fields | [qlikview_parser.py:743-754](qlikview_parser.py#L743-L754) | Reasonable starter set |
| QVD file detection inside LOAD | [qlikview_parser.py:487-491](qlikview_parser.py#L487-L491) | Works for `'C:\...\file.qvd'` |
| Recursive INCLUDE resolution + cycle guard | [qlikview_parser.py:383-398](qlikview_parser.py#L383-L398) | Uses `processed_files` set — correct in principle |
| Neo4j driver lifecycle + connection test | [qlikview_parser.py:252-265](qlikview_parser.py#L252-L265) | Fine |
| JSON export for inspection | [qlikview_parser.py:1210-1221](qlikview_parser.py#L1210-L1221) | Very useful for debugging — keep it |
| Batch discovery via `rglob("*.qvs")` | [qlikview_parser.py:1237-1263](qlikview_parser.py#L1237-L1263) | Wraps the single-file path correctly |

**Do not throw these away** during the rewrite — port them into the new module layout.

---

## 3. Status at a glance (plan vs code)

Legend: ✅ done / ⚠️ partial or broken / ❌ missing

### 3.1 Architecture & tech stack

| Plan requirement | Status | Where / why |
|---|---|---|
| ANTLR 4 grammar (`grammar/QlikViewLexer.g4`, `grammar/QlikViewParser.g4`) | ❌ | Not present. Regex used at [qlikview_parser.py:192-232](qlikview_parser.py#L192-L232) |
| `antlr4-python3-runtime` in dependencies | ❌ | Missing from [requirements.txt](requirements.txt) |
| ANTLR jar vendored under `tools/` | ❌ | No `tools/` directory |
| Multi-module package layout | ❌ | One file: [qlikview_parser.py](qlikview_parser.py) |
| `pyproject.toml` | ❌ | Only `requirements.txt` |
| `Makefile` with `grammar`, `build`, `test`, `run` targets | ❌ | None |
| Multi-stage `Dockerfile` (Java codegen → Python runtime) | ❌ | None |
| `.env.example`, `.gitignore` for `generated/` | ❌ | None |
| `sqlglot` for embedded SQL | ✅ | [qlikview_parser.py:277-337](qlikview_parser.py#L277-L337) |
| `lxml` for XML metadata | ⚠️ | In [requirements.txt:6](requirements.txt#L6) but **unused** |

### 3.2 Parsing pipeline (plan §6)

| Step | Status | Where / why |
|---|---|---|
| 1. Read script as UTF-8, strip BOM | ⚠️ | `utf-8-sig` hardcoded at [qlikview_parser.py:360](qlikview_parser.py#L360); no `chardet`, no UTF-16/Windows-1252 handling |
| 2. Pre-process `$(Include=...)` / `$(Must_Include=...)` recursively, depth ≤10 | ❌ | Only matches plain `INCLUDE 'path'` at [qlikview_parser.py:221-224](qlikview_parser.py#L221-L224). No `$(...)` syntax, no depth limit |
| 3. Lex + parse with ANTLR | ❌ | No ANTLR — regex |
| 4. Walk parse tree with Visitor | ❌ | No tree, no visitor |
| 5a. Pass 1: variables (`SET` / `LET`) | ❌ | Not handled |
| 5b. Pass 2: tables, lineage edges | ⚠️ | Single-pass regex; produces wrong output (see §4) |
| 5c. Macro expansion `$(varName)` before SQL parse | ❌ | Not handled |
| 6. `CollectingErrorListener` → `ScriptIR.warnings`, `STRICT_PARSING` opt-in | ❌ | No structured warnings; only `parse_errors: []` populated from include failures |
| 7. Optional XML metadata → sheets/charts | ❌ | Not implemented |
| 8. Return populated `ScriptIR` | ⚠️ | `QlikViewApp` exists but field names and structure differ from plan |
| 9. Batched MERGE write to Neo4j | ⚠️ | Per-node sessions, not batched; correctness issues (see §5) |

### 3.3 Statement coverage

| Construct | Status | Notes |
|---|---|---|
| `ODBC CONNECT TO` | ✅ | |
| `OLEDB CONNECT TO` | ✅ | |
| `LIB CONNECT TO` (Qlik Sense / managed) | ❌ | Not detected |
| `LOAD` + `SQL SELECT` (preceding LOAD) | ⚠️ | Detects but field list and source-table extraction are buggy (§4.1, §4.2) |
| `RESIDENT` load | ⚠️ | Works in isolation; fires false positives from comments (§4.3) |
| `LOAD ... FROM <file>` (CSV, txt) | ✅ | Basic case works |
| QVD load | ✅ | |
| `LEFT JOIN (target) LOAD ... RESIDENT source` | ⚠️ | Regex requires parenthesized target; misses the JOIN in `BIW1_Main.qvs` (§4.4) |
| `INNER JOIN`, `RIGHT JOIN`, `FULL JOIN` | ⚠️ | Same regex; same limitation |
| `KEEP` (LEFT KEEP / RIGHT KEEP / INNER KEEP) | ❌ | Not handled |
| `CONCATENATE` / `NOCONCATENATE` | ❌ | Not handled |
| `MAPPING LOAD` + `APPLYMAP` | ❌ | Not handled |
| Preceding-LOAD chains (`LOAD a, b LOAD c, d SQL ...`) | ❌ | Not handled |
| `LOAD * RESIDENT Foo` (star-field resolution) | ❌ | Not handled |
| `SET` / `LET` variables → `:Variable` nodes | ❌ | Not handled |
| `$(varName)` macro substitution | ❌ | Not handled |
| `SUB` / `END SUB` / `CALL` (subroutines) | ❌ | Not handled |
| `Drop Table` / `Drop Field` | ❌ | Not handled |
| `BUFFER`, `INLINE`, `AUTOGENERATE` | ❌ | Not handled |
| `INCLUDE 'file.qvs'` | ✅ | Only the non-`$()` syntax |
| `$(Include=...)` / `$(Must_Include=...)` | ❌ | Not handled |
| `// line` and `/* block */` and `REM ... ;` comments stripped before parsing | ❌ | **Causes a real bug — see §4.3** |
| Dynamic SQL (e.g. `SQL SELECT * FROM $(vTable)`) — emit warning + `lineage_partial=true` | ❌ | Not handled |

### 3.4 Neo4j schema (plan §5) — major divergence

**Node labels**

| Plan | Code | Status |
|---|---|---|
| `:QlikScript` | `:QlikViewApp` + `:Report` | ❌ wrong label, also creates two nodes for one script |
| `:Connection` | (none — connections parsed but never written) | ❌ |
| `:QlikTable` (in-memory) | `:Table` | ❌ collides with physical `:Table` |
| `:Table` (physical, shared label, FQN `db.schema.name`) | `:SourceTable` | ❌ wrong label, breaks cross-parser merge |
| `:Attribute` (with `is_calculated`, `formula`) | `:Attribute` (with `is_synthetic`, `formula`) | ⚠️ property name differs (`is_calculated` ≠ `is_synthetic`) |
| `:Variable` | — | ❌ |
| `:Subroutine` | — | ❌ |
| `:QlikSheet` | — | ❌ |
| `:QlikChart` | — | ❌ |

**Relationship types**

| Plan | Code |
|---|---|
| `USES_CONNECTION` | (missing) |
| `CONTAINS_TABLE` (with `load_order`) | (missing — `load_order` lives on `READS` edge instead) |
| `LOADS_FROM_TABLE` (with `via` ∈ {`sql`, `file`, `inline`}) | `READS_FROM` |
| `HAS_FIELD` | `HAS_ATTRIBUTE` |
| `HAS_COLUMN` | (missing — physical columns aren't emitted) |
| `DERIVES_FROM` (attribute → attribute, with `formula`) | `DERIVED_FROM` (close, but spelt differently — naming inconsistency vs Tableau parser) |
| `DERIVES_FROM_TABLE` (with `via` ∈ {`resident`, `join`, `concat`}) | (missing) |
| `JOINS_WITH` (with `join_type`) | (missing — joins captured in JSON, never written to Neo4j) |
| `CONCATENATES_INTO` | (missing) |
| `USES_VARIABLE` | (missing) |
| `CONTAINS_SHEET` / `DISPLAYS_CHART` / `USES_FIELD` | (missing — XML metadata not parsed) |
| (not in plan) `USES_TABLE`, `READS`, `HAS_NOTE` | code-only additions, not in plan |

**Constraints / indexes** — plan §5.3 lists 6 uniqueness constraints and 1 index. Code creates **zero** constraints. Re-runs will duplicate nodes if the merge keys aren't enforced.

**Deterministic node IDs** (plan §5.4) — plan mandates `sha256(canonical_string)[:16]`. Code uses bare `name.lower()` ([qlikview_parser.py:266-274](qlikview_parser.py#L266-L274)). This is the **single biggest blocker** for cross-parser lineage: a Teradata BTEQ parser writing `PROD.SALES.ORDERS` will not merge onto a QlikView-emitted node, because the QlikView node is keyed on `customer_master` and is labelled `:SourceTable` instead of `:Table` with FQN.

### 3.5 API / config / observability

| Plan | Code | Status |
|---|---|---|
| `FastAPI` with `POST /parse`, `POST /parse/batch`, `GET /health`, `GET /version`, `GET /metrics` | None | ❌ |
| Env vars: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `QLIK_INCLUDE_ROOT`, `QLIK_MAX_INCLUDE_DEPTH`, `QLIK_LIB_CONNECTIONS_FILE`, `STRICT_PARSING` | Hardcoded `bolt://localhost:7687` + literal `"password"` at [qlikview_parser.py:1278-1281](qlikview_parser.py#L1278-L1281) | ❌ **security smell** |
| Structured JSON logs | Plain-text logging | ❌ |
| Prometheus metrics (`qlikview_includes_resolved_total`, `qlikview_macro_expansions_total`, `qlikview_sql_parse_failures_total`) | None | ❌ |

### 3.6 Tests, CI, packaging

| Plan | Code |
|---|---|
| `tests/unit/` (grammar, visitor, load, sql, resident, join, variables, includes) | ❌ no tests at all |
| `tests/integration/` (end-to-end + API + Neo4j testcontainer) | ❌ |
| ≥75% line coverage, 100% on visitor + load_statement | ❌ |
| 8 fixture scripts + paired XML | ❌ only `BIW1_Main.qvs` + `common_transformations.qvs` |
| Idempotency assertion (re-run produces zero diff) | ❌ |
| Cross-parser merge assertion (preload `:Table`, parse `.qvs`, no duplicate) | ❌ |
| CI fails on stale generated grammar files | ❌ |
| Docker image with no Java at runtime | ❌ |

### 3.7 Definition-of-done (plan §15)

**0 of 13 items met.**

---

## 4. Bugs visible in the current `parsed_qlikview.json`

These are real defects in the existing code, not just plan-vs-code gaps. They need fixing regardless of the ANTLR rewrite, because the regex prototype currently produces an output the team would not be able to trust.

### 4.1 Field list bleeds past the `;` terminator into the SQL block

**Symptom (in `parsed_qlikview.json`):**

```json
"table_name": "CustomerData",
"fields": [
  "CustomerID", "CustomerName", "CustomerName_Upper", "Region",
  "Country;",          // ← trailing semicolon swallowed
  "SQL SELECT",        // ← SQL keyword pulled in as a "field"
  "CustomerID", "CustomerName", "Region", "Country"
]
```

**Root cause:** `_extract_field_list` at [qlikview_parser.py:542-575](qlikview_parser.py#L542-L575) uses

```python
field_pattern = re.compile(r'^(.*?)(FROM|RESIDENT)', re.IGNORECASE | re.DOTALL)
```

This stops at the first `FROM` / `RESIDENT`, which lands inside the embedded `SQL SELECT ... FROM BIW_DB.Customer_Master`. The `;` that ends the preceding LOAD is never used as a boundary.

**Fix direction:** terminate the field section at the first `;` that is **not inside** parentheses/strings. Until ANTLR is in place, this is a one-line regex change: stop at `;` first, then optionally at `FROM`/`RESIDENT` within the surviving substring.

---

### 4.2 LOAD-block boundary captures cross-block content (semicolon, again)

**Root cause:** `_extract_loads` at [qlikview_parser.py:442-462](qlikview_parser.py#L442-L462) defines block boundaries as `<Label>: LOAD ... <next Label>: LOAD`. The intent is right, but the boundary should be the `;` that ends the LOAD's `SQL` / `RESIDENT` / `FROM` clause, not "until the next label." Anything between the end of one LOAD's `;` and the next `Label:` (comments, JOIN blocks, Drop statements, etc.) is currently glued onto the **previous** LOAD's body — which causes §4.3.

---

### 4.3 `// Resident Load` **comment** gets matched as a `RESIDENT` clause

**Symptom:** `SalesData` in the JSON is reported as

```json
"source_type": "RESIDENT",
"source_table": "Load"
```

But `SalesData` in `BIW1_Main.qvs` is a SQL load. The captured block (per §4.2) extends past the SQL `;` and includes the line:

```
// -------------------------
// Resident Load
// -------------------------
SalesSummary:
```

The `RESIDENT_PATTERN = re.compile(r"\bRESIDENT\s+([a-zA-Z_]\w*)")` then matches `Resident Load` from the comment text and captures `Load` as the source table. `_parse_load_block` checks RESIDENT **before** SQL ([qlikview_parser.py:481-498](qlikview_parser.py#L481-L498)), so it commits the wrong classification.

**Fix direction:** strip `//` line comments, `/* ... */` block comments, and `REM ... ;` statements **before** any regex matching. This is a small standalone pre-processor pass.

---

### 4.4 `LEFT JOIN` in fixture is silently dropped

**Symptom:** `"joins": []` in the JSON, but `BIW1_Main.qvs` lines 60–65 contain a real `LEFT JOIN`.

**Root cause:** the join regex at [qlikview_parser.py:582-585](qlikview_parser.py#L582-L585) requires the form

```
LEFT JOIN (TargetTable) LOAD ... RESIDENT SourceTable
```

But the fixture uses the **implicit-target** form (no parentheses):

```qlikview
LEFT JOIN LOAD
    CustomerID, CustomerName, Region
RESIDENT CustomerData;
```

When the target is omitted, the JOIN attaches to the most recently loaded table (here, `SalesSummary`). The regex doesn't recognise this form, so the JOIN is silently dropped — both in the JSON and in Neo4j.

**Fix direction:** the regex must accept both forms. Track the "current active table" so the implicit form can be resolved. Note that joins are already not being written to Neo4j either (§3.4) — so fixing the regex is necessary but not sufficient.

---

### 4.5 Duplicate field rows + nonsense field names persist into Neo4j

Because §4.1 and §4.2 inflate the field list, `parsed_qlikview.json` contains duplicates and garbage:

- `CustomerData` lists `CustomerID`, `CustomerName`, `Region`, `Country` **twice each** (once from the LOAD, once from the SQL body that leaked in).
- `"Country;"` and `"SalesCategory;"` appear as field names — the trailing `;` was never stripped.
- These are then written as `:Attribute` nodes to Neo4j (`push_to_neo4j` makes no dedup or sanity check at [qlikview_parser.py:1031-1107](qlikview_parser.py#L1031-L1107)).

---

### 4.6 `SalesCategory` synthetic field picks up string literals as "source fields"

```json
{
  "name": "SalesCategory",
  "formula": "IF(SalesAmount > 1000, 'HIGH', 'LOW')",
  "source_fields": ["SalesAmount", "HIGH", "LOW"]
}
```

`'HIGH'` and `'LOW'` are string literals, not fields. `_extract_source_fields_from_expression` at [qlikview_parser.py:729-755](qlikview_parser.py#L729-L755) tokenises every identifier-looking substring without first stripping string literals. The QlikView function blacklist filters out `IF`/`UPPER`/etc. but doesn't handle quoted literals.

**Fix direction:** strip single-quoted and double-quoted literals from the expression before regex tokenisation.

---

### 4.7 `common_transformations.qvs` is parsed as a standalone app

Because `discover_and_parse` calls `rglob("*.qvs")` and the include file is also a `.qvs`, the same file gets parsed twice: once standalone (yielding zero loads because the resident LOAD has no `Label:` prefix), and once inlined via the parent's INCLUDE list. The standalone parse produces an orphan `:QlikViewApp` and `:Report` node named `common_transformations`.

**Fix direction:** maintain a registry of "already inlined as include" paths and skip them at discovery time, or move include files into a sibling directory that `rglob` does not pick up.

---

### 4.8 Hardcoded Neo4j credentials in the entrypoint

[qlikview_parser.py:1278-1281](qlikview_parser.py#L1278-L1281) ships `neo4j` / `"password"` as the literal user/pass. Move to env vars before any commit reaches a shared repo.

---

## 5. Push-to-Neo4j correctness issues

Independent of the schema-name mismatch in §3.4, the write path has structural problems:

| Issue | Where | Why it matters |
|---|---|---|
| One Cypher round-trip **per node and per edge** | [qlikview_parser.py:793-1202](qlikview_parser.py#L793-L1202) | At 90+ attributes per fixture, this is 200+ round-trips. Plan says "batched MERGE." Use `UNWIND $rows AS row MERGE ...` |
| No uniqueness constraints created on first run | (missing) | MERGE without a constraint can race and create duplicates under concurrent loads |
| `:QlikViewApp` and `:Report` are two nodes for the same script | [qlikview_parser.py:810-861](qlikview_parser.py#L810-L861) | Plan has one `:QlikScript` node — collapse |
| `joins` extracted into IR but **never written** to Neo4j | search for `JOINS_WITH` in [qlikview_parser.py](qlikview_parser.py) — not present | Plan §5.2 requires `JOINS_WITH` edges; absent |
| `connections` extracted into IR but **never written** | search for `Connection` MERGE in [qlikview_parser.py](qlikview_parser.py) — not present | Plan §5.1/§5.2 require `:Connection` nodes and `USES_CONNECTION` edges; absent |
| `includes` tracked but no `:QlikScript`→`:QlikScript` edge written | (missing) | Plan implies lineage across includes |
| Synthetic-field source bookkeeping creates `:Attribute` with literal values | see §4.6 | Will write `"HIGH"` and `"LOW"` as attributes to Neo4j |
| Notes are MERGEd on full text content as the key | [qlikview_parser.py:1153-1165](qlikview_parser.py#L1153-L1165) | Will create one `:Note` per slightly-different sentence; not in the plan at all |

---

## 6. Architectural gap (single file → modular package)

The plan's `src/qlikview_parser/` tree has 7 sub-packages and 25+ files. The code has 1 file with 1305 lines and 5 dataclasses. A straightforward decomposition (do this **before** touching ANTLR — it makes the ANTLR work much easier):

| Plan path | Move from current code |
|---|---|
| `models/domain.py` | `QVSConnection`, `QVSLoad`, `QVSJoin`, `QVSField`, `QVSNote`, `QlikViewApp` at [qlikview_parser.py:60-179](qlikview_parser.py#L60-L179) |
| `parser/connections.py` | `_extract_connections`, `_parse_oledb_dsn` at [qlikview_parser.py:405-440](qlikview_parser.py#L405-L440) |
| `parser/load_statement.py` | `_extract_loads`, `_parse_load_block`, `_extract_field_list` at [qlikview_parser.py:442-575](qlikview_parser.py#L442-L575) |
| `parser/sql_block.py` | `extract_sql_tables` at [qlikview_parser.py:277-337](qlikview_parser.py#L277-L337) |
| `parser/join.py` | `_extract_joins` at [qlikview_parser.py:578-612](qlikview_parser.py#L578-L612) |
| `parser/includes.py` | `_extract_includes` at [qlikview_parser.py:757-774](qlikview_parser.py#L757-L774) |
| `parser/expressions.py` | `_extract_synthetic_fields`, `_extract_source_fields_from_expression` at [qlikview_parser.py:690-755](qlikview_parser.py#L690-L755) |
| `parser/script.py` | `parse_qvs_file` orchestrator at [qlikview_parser.py:338-403](qlikview_parser.py#L338-L403) |
| `graph/client.py`, `graph/writer.py`, `graph/queries.py` | `_initialize_driver`, `push_to_neo4j`, `close` at [qlikview_parser.py:252-1208](qlikview_parser.py#L252-L1208) |
| `utils/ids.py` | new — `sha256(...)[:16]` helper per plan §5.4 |
| `utils/logging.py` | replace current `logging.basicConfig` with structured JSON |
| `api/routes.py`, `api/schemas.py`, `main.py` | new — FastAPI surface |
| `grammar/QlikViewLexer.g4`, `grammar/QlikViewParser.g4` | new |
| `tests/unit/*`, `tests/integration/*`, `tests/fixtures/*` | new |

---

## 7. Recommended remediation roadmap

Sequence matters. Each phase delivers value on its own and unblocks the next.

### Phase 0 — Hotfix the prototype (1–2 days)

So the existing JSON output can be trusted while the bigger rewrite is in flight.

- [ ] **Comment stripping pre-processor** (fixes §4.3): one pass to remove `//...`, `/* ... */`, `REM ... ;`. Do this once on `script_content` before any regex.
- [ ] **Field-list terminator** (fixes §4.1, §4.5): stop at the first unquoted `;` outside parens.
- [ ] **LOAD-block boundary** (fixes §4.2): use the LOAD's own terminating `;` as the block end, not "next `Label:`".
- [ ] **JOIN regex accepts implicit-target form** (fixes §4.4): make `(target)` optional, fall back to "current active table".
- [ ] **String-literal stripping in expression source-field extraction** (fixes §4.6): remove `'...'` and `"..."` before tokenising.
- [ ] **Skip include files at discovery** (fixes §4.7): track inlined paths and exclude from `rglob` results.
- [ ] **Move credentials to env vars** (fixes §4.8): `os.environ["NEO4J_URI"]`, etc.
- [ ] **Write `JOINS_WITH` and `:Connection` to Neo4j**: the data is already in the IR — just emit it.

After Phase 0, re-run against `BIW1_Main.qvs` and verify the JSON contains: 5 loads (CustomerData, SalesData SQL-typed, SalesSummary RESIDENT, InventoryData QVD, CSVData FILE) + 1 join + 1 ODBC connection + clean field lists with no `;` or `SQL SELECT` entries.

### Phase 1 — Restructure into modules + align Neo4j schema (3–5 days)

- [ ] Create the `src/qlikview_parser/` tree from §6.
- [ ] Add `pyproject.toml`, `.env.example`, `.gitignore` (exclude `generated/`).
- [ ] Rewrite Neo4j writer to match plan §5 exactly: labels (`:QlikScript`, `:QlikTable`, `:Table`, `:Connection`, `:Attribute`, `:Variable`), relationship types (`USES_CONNECTION`, `CONTAINS_TABLE`, `LOADS_FROM_TABLE`, `HAS_FIELD`, `HAS_COLUMN`, `DERIVES_FROM`, `DERIVES_FROM_TABLE`, `JOINS_WITH`, `CONCATENATES_INTO`, `USES_VARIABLE`), deterministic SHA-256 IDs.
- [ ] Create the constraints from plan §5.3 on first run.
- [ ] Switch writes to `UNWIND` batched MERGE.
- [ ] Add a Neo4j testcontainer integration test that preloads `PROD.SALES.ORDERS` as a `:Table` node and asserts the QlikView parser merges onto it (no duplicate). This is the cross-parser contract test.

### Phase 2 — ANTLR grammar (5–10 days)

- [ ] Vendor `antlr-4.13.1-complete.jar` under `tools/` and add `make grammar` (per plan §4.1).
- [ ] Check `github.com/antlr/grammars-v4` for reusable SQL fragments before writing from scratch.
- [ ] Write `QlikViewLexer.g4` (tokens: keywords, identifiers, strings, comments-as-skip, numbers, operators).
- [ ] Write `QlikViewParser.g4` rules: `script`, `connectStmt`, `loadStmt`, `sqlStmt`, `setStmt`, `letStmt`, `subDef`, `callStmt`, `joinStmt`, `concatStmt`, `dropStmt`, `expression`.
- [ ] Generate `generated/` and gitignore it. CI step verifies regen is clean.
- [ ] Implement `visitor/ir_visitor.py` (subclass `QlikViewVisitor`), one method per rule, returning IR fragments.
- [ ] Implement `visitor/error_listener.py` (`CollectingErrorListener`) — attach errors to `ScriptIR.warnings`, fail-hard only when `STRICT_PARSING=true`.
- [ ] Delete the regex parser once visitor passes parity tests against Phase 0 output.

### Phase 3 — Macros, variables, subroutines, XML (3–5 days)

- [ ] Pre-processor: `$(Include=...)` / `$(Must_Include=...)` with depth limit and per-file lineage attribution.
- [ ] Two-pass walk inside visitor: Pass 1 SET/LET → `:Variable` nodes; Pass 2 expand `$(varName)` in SQL/LOAD before sqlglot parses.
- [ ] SUB/CALL inlining at call site.
- [ ] `lxml`-based XML metadata parser → `:QlikSheet`, `:QlikChart`, `USES_FIELD` (plan §6 step 7).

### Phase 4 — API, Docker, observability, tests (3–5 days)

- [ ] FastAPI app with `/parse`, `/parse/batch`, `/health`, `/version`, `/metrics` per plan §7.
- [ ] Multi-stage Dockerfile (plan §4.1) — Java only in builder stage.
- [ ] Prometheus metrics: `qlikview_includes_resolved_total`, `qlikview_macro_expansions_total`, `qlikview_sql_parse_failures_total`.
- [ ] JSON structured logs.
- [ ] All 8 fixture scripts from plan §9.1, plus one paired `.xml`.
- [ ] ≥75% unit coverage, 100% on `ir_visitor.py` and `load_statement.py` (exclude `generated/`).

### Phase 5 — Polish & DoD sign-off (1–2 days)

- [ ] Walk the plan §15 checklist; tick or document deferral for each item.
- [ ] Performance: 5000-line script + 30 tables + 10 includes parses in under 20s.
- [ ] README rewrite covering install, `make grammar`, local run, sample API calls, schema overview.

---

## 8. Open questions for the team

1. **Cross-parser schema source of truth.** The plan promises label/ID compatibility with the Tableau, Ab Initio, Teradata, and SAS parsers. Before we re-key everything to SHA-256, can the teammate confirm with the Tableau parser owner that `attribute::<table_fqn>::<column_name>` is the canonical form they use? A mismatch here defeats the whole point.
2. **LIB CONNECT TO resolution.** Plan §8 mentions an optional `QLIK_LIB_CONNECTIONS_FILE` (YAML mapping connection names to `{server, db}`). Does an authoritative mapping exist somewhere at Barclays, or do we ship an empty default and document the env var?
3. **VBScript macros.** Plan §14 lists these as out of scope but says to "emit a warning if `set HidePrefix` or other macro-suggestive constructs appear." Is that warning useful to anyone, or noise?
4. **Fixture sourcing.** Plan §10 says ask Sheetal/the project lead for an anonymisable Barclays sample. Whose ask is that?

---

## 9. Hand-off checklist

Before the teammate starts:

- [ ] Read `qlikview-parser-plan.md` end-to-end (it is the spec, this doc is the gap analysis).
- [ ] Skim §2 above to know what to **preserve** during refactor.
- [ ] Start with **Phase 0** — do not jump straight to ANTLR. Fixing the regex bugs first gives you a baseline-correct JSON output to diff against once ANTLR comes online; otherwise you have no way to verify the new parser produces the *right* result.
- [ ] Open a draft PR per phase. Phase 0 should land in 1–2 days and be reviewable on its own.
- [ ] Add me as a reviewer.

---

*End of review.*
