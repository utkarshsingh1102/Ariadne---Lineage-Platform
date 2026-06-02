# Ariadne — Lineage Platform

A multi-parser data-lineage platform that walks Tableau workbooks, QlikView load
scripts, IBM Tivoli (TWS) schedule dumps, and PySpark / Spark SQL code, and
stitches them into a single Neo4j knowledge graph (plus a Postgres mirror for
SQL-style point queries). A Next.js explorer renders the graph and lets you
trace any column, table, dashboard, or job to its physical source — and back.

The four parsers are independent, each shipped as its own FastAPI service.
Cross-parser stitching is automatic: every parser hashes physical locators
(`db.schema.table`, `s3://bucket/path`, qvd file paths, script paths) into the
same SHA-256 ID, so a Tableau dashboard that reads `PROD.SALES.ORDERS` and a
Spark job that writes `PROD.SALES.ORDERS` land on the **same** node — lineage
threads end-to-end without any glue layer.

---

## What's in this repo

```
.
├── qlikview-parser/      ANTLR4 + sqlglot, v0.2 IR with Dataset/Attribute/
│                         PhysicalSource/KeyConstraint, BINARY-load inheritance,
│                         QVD-header reader, leaf-to-root resolver, secret scrubber
├── tableau-parser/       lxml walk over .twb / .twbx, federated-join + custom-SQL
│                         lineage, calculated-field dependency DAG
├── tws-parser/           ANTLR4 composer-text grammar + lxml XML path,
│                         dual writer (Neo4j + Postgres), run-cycle normalization
├── spark-parser/         Python AST + sqlglot for PySpark / Spark SQL / notebooks,
│                         DataFrame-level lineage with column-level derivation
├── lineage-platform/     The runtime stack
│   ├── apps/
│   │   ├── gateway/      FastAPI — wraps Neo4j + Postgres, hosts Cypher presets,
│   │   │                 proxies /parse to the right parser
│   │   └── frontend/     Next.js 14 + IBM Carbon + Cytoscape graph explorer
│   ├── infra/            Neo4j init Cypher, Postgres init SQL
│   ├── deploy/           Kubernetes / Helm manifests
│   └── docker-compose.yml
├── lineage-contracts/    Shared IR schemas (Neo4j constraints, Postgres DDL)
├── start.sh              Bring the whole stack up
├── stop.sh               Tear everything down
├── restart.sh            Quick container restart (data preserved)
└── refresh.sh            Rebuild parsers + restart host gateway/frontend,
                          NO data loss (Neo4j + Postgres volumes left alone)
```

---

## Architecture

```
                                              ┌─────────────────────┐
   .twb / .twbx ─────► tableau-parser   :8001 │                     │
   composer / XML ──► tws-parser        :8002 │      Neo4j 5.20     │
   .qvs / .qvw ─────► qlikview-parser   :8003 │     (lineagepass)   │
   .py / .sql ──────► spark-parser      :8004 │    HTTP  7475       │
                            │                 │    Bolt  7688       │
                            ▼ MERGE           │                     │
                                              │  Cross-parser join  │
                            ▲ pure-Cypher     │  via SHA-256 ids on │
                            │ reads / writes  │  :Table :Attribute  │
                                              │  :PhysicalSource    │
        ┌─────────────────────────────────┐   │  :Script            │
        │   FastAPI Gateway        :8000  │   └─────────────────────┘
        │  /parse  /graph/* /files  /lineage          ▲
        │  /query/preset/*                            │
        └─────────────────────────────────┘           │
                            ▲                 ┌─────────────────────┐
                            │                 │   Postgres 16  :5432│
                            ▼                 │   TWS mirror tables │
        ┌─────────────────────────────────┐   │   (jobs, schedules, │
        │   Next.js Frontend       :3000  │   │    dependencies)    │
        │   - Files explorer              │   └─────────────────────┘
        │   - Graph (Cytoscape)
        │   - Lineage tracer
        │   - Parse uploads
        └─────────────────────────────────┘
```

---

## Quick start

Prerequisites: Docker Desktop, Python 3.11+, Node 18+. Java 17 is needed only
if you want to regenerate ANTLR parsers locally (the Docker builds handle this
on their own).

### Windows

The stack's lifecycle scripts (`start.sh`, `refresh.sh`, etc.) are bash, so on
Windows everything runs inside WSL2 Ubuntu. A one-shot setup script handles
the Windows-side prerequisites (WSL2 + Ubuntu + Docker Desktop + Git) and
stages a companion script at `~/setup-wsl.sh` for the Linux-side ones
(Python 3.11, Node 18, system tools):

```powershell
# From an elevated PowerShell (Run as Administrator):
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup-windows.ps1
```

Once it finishes, open Ubuntu and run:

```bash
~/setup-wsl.sh
git clone https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform.git
cd Ariadne---Lineage-Platform
./start.sh
```

### macOS / Linux

```bash
git clone https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform.git
cd Ariadne---Lineage-Platform
./start.sh
```

`start.sh` builds the four parser images, brings up Neo4j + Postgres +
parsers via docker-compose, then launches the gateway and frontend on the
host. When it finishes:

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Gateway (FastAPI docs) | http://localhost:8000/docs |
| Neo4j Browser | http://localhost:7475 (`neo4j` / `lineagepass`) |
| Postgres | `localhost:5432` (`lineage` / `lineagepass` / db `lineage`) |
| tableau-parser | http://localhost:8001/health |
| tws-parser | http://localhost:8002/health |
| qlikview-parser | http://localhost:8003/health |
| spark-parser | http://localhost:8004/health |

Stop everything:

```bash
./stop.sh
```

Hard-refresh parser + frontend code without touching Neo4j or Postgres data:

```bash
./refresh.sh
```

---

## Using it

### From the frontend

1. Open http://localhost:3000.
2. **Parse** tab → upload a `.twb`, `.qvs`, `.py`, `.sql`, or TWS dump.
3. **Files** tab → drill into the parsed file, see the graph (Cytoscape),
   click any node for properties, click *Trace from this node* to walk lineage.
4. **Lineage tracer** tab → paste a node id (or a fully-qualified table name),
   pick Upstream or Downstream, hit Trace.

### From the gateway

```bash
# Parse a file by path (the parser picks the right backend by extension)
curl -X POST http://localhost:8000/parse \
  -H 'content-type: application/json' \
  -d '{"file_path":"/data/inputs/sample.qvs"}'

# Read-only Cypher
curl -X POST http://localhost:8000/graph/query/cypher \
  -H 'content-type: application/json' \
  -d '{"cypher":"MATCH (n:Table) RETURN count(n) AS tables"}'

# Lineage preset (upstream walk from a node id)
curl -X POST 'http://localhost:8000/graph/query/preset/lineage-upstream?node_id=<id>'
```

### Sample cross-parser query

End-to-end lineage from a Tableau dashboard to the Spark job that produces
the underlying table:

```cypher
MATCH (dash:TableauDashboard)-[:DISPLAYS_WORKSHEET]->(:TableauWorksheet)
      -[:USES_FIELD]->(:Attribute)<-[:HAS_COLUMN]-(t:Table)
      <-[:WRITES_TABLE]-(:DataFrame)<-[:CONTAINS_DATAFRAME]-(spark:SparkScript)
RETURN dash.name, t.fully_qualified_name, spark.name
```

This works because both parsers hash `t.fully_qualified_name` into the same
`:Table.id`, so `MERGE` collapses them onto one node — no joining table, no
glue code, no ETL step.

---

## Per-parser notes

### tableau-parser

- Inputs: `.twb` (XML) and `.twbx` (ZIP wrapping a `.twb`)
- Resolves federated joins, custom-SQL relations (via sqlglot), and the full
  calculated-field DAG (`[Calc1] = [A] * 2; [Calc2] = [Calc1] + [B]`)
- Schema: `:TableauWorkbook`, `:TableauDatasource`, `:TableauWorksheet`,
  `:TableauDashboard`, `:Parameter`, plus the shared `:Connection`, `:Table`,
  `:Attribute` labels

### qlikview-parser

- Inputs: `.qvs` (load scripts) and `.qvw` (binary OLE — extracts the embedded
  script stream)
- Grammar: ANTLR4, covers LOAD / SQL SELECT / RESIDENT / JOIN / INLINE /
  CONCATENATE / BINARY / MAPPING LOAD / QUALIFY / SECTION ACCESS / AUTOGENERATE /
  RENAME, plus SET / LET / SUB / CALL and `$(...)` macro expansion
- v0.2 IR layer: `:DataPlatform` → `:DataConnection` → `:PhysicalSource` →
  `:Dataset` → `:Attribute`, with `:KeyConstraint` nodes from naming
  heuristics + JOIN keys + QVD-header hints
- BINARY-load inheritance: a downstream `.qvw` that does `BINARY 'upstream.qvw'`
  inherits the upstream's entire data model with `DERIVES_FROM` edges
- Secret scrubber + fingerprint (no passwords leak into the graph)

### tws-parser

- Inputs: TWS composer-text dumps and XML exports
- Grammar: ANTLR4 composer DSL — schedule headers, run cycles, FOLLOWS / NEEDS /
  OPENS, RECOVERY
- Writes to **both** Neo4j (`:Schedule`, `:Job`, `:Resource`, `:FileWatcher`)
  and Postgres (the same data flattened for SQL filters like "all jobs running
  in the 05:30–06:30 window that touch table X")
- Excel-export endpoint generates `.xlsx` from any Postgres query result

### spark-parser

- Inputs: PySpark `.py`, Spark SQL `.sql`, Jupyter `.ipynb`, Databricks-source
  `.py` (with `# COMMAND` cell separators), `.dbc` archives
- Strategy: Python `ast` for DataFrame variable tracking, `sqlglot` (Spark
  dialect) for SQL and `F.expr(...)` strings — no Spark cluster needed
- Captures: `spark.read.*` / `spark.table` / `spark.sql` reads,
  `df.write.saveAsTable` / `.save` / `.insertInto` writes, all `.select` /
  `.withColumn` / `.join` / `.groupBy` / `.union` transforms with column-level
  `DERIVES_FROM` edges
- Graceful degradation: dynamic table names, conditional reads, and
  `@udf` calls produce `lineage_partial=true` markers rather than missing edges

---

## Determinism + idempotency

All four parsers obey the same contract:

- Every node id is `sha256(canonical_qname)` — no clocks, no random, no env
  vars participate. Re-parsing the same file produces the same ids.
- Writes use `MERGE`, so re-running is a no-op on counts.
- ID-generation collections are `sorted()` before hashing.
- `PYTHONHASHSEED=0` in CI and Docker.

The qlikview-parser ships CI gates for all three (id stability, idempotent
merge, secret-leak grep).

---

## Development

Each parser is a standalone Python package with its own tests:

```bash
# qlikview-parser
cd qlikview-parser
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make grammar              # regenerate ANTLR lexer/parser from .g4
pytest -q                 # 192 passed, 10 skipped (Neo4j-gated)
```

Same shape for `tableau-parser`, `tws-parser`, `spark-parser`. The
`spark-parser` is pure Python (no Java needed); the other three need Java 17
locally only if you intend to regenerate the ANTLR grammar.

Frontend dev loop:

```bash
cd lineage-platform/apps/frontend
npm install
npm run dev   # http://localhost:3000
```

Gateway dev loop:

```bash
cd lineage-platform/apps/gateway
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn lineage_gateway.main:app --reload --port 8000
```

---

## Tech stack

| Layer | Choice |
|---|---|
| Graph DB | Neo4j 5.20 (community, with APOC) |
| RDBMS mirror | Postgres 16 |
| Parser DSL | ANTLR 4.13 (Python target) |
| SQL parsing | sqlglot (column-level lineage via `sqlglot.lineage`) |
| Gateway | FastAPI (Python 3.11+) |
| Frontend | Next.js 14, TypeScript, IBM Carbon Design System, Cytoscape.js |
| Container | docker-compose for dev, K8s manifests for prod |
| Tests | pytest, testcontainers |

---

## License

Internal — not for redistribution.
