# Ariadne — Data Lineage Platform
### A Complete Walkthrough (in simple English)

> **What is this document?**
> This is a guided tour of the whole project. It explains what the platform does, how it is
> built, what each of the four parsers does, what test cases we have written, and where the
> hard parts and limits are. It is written in plain English so anyone — technical or not —
> can follow along.

---

## Table of Contents

1. [The Big Picture — What Problem Are We Solving?](#1-the-big-picture)
2. [How It All Fits Together (Architecture)](#2-architecture)
3. [The Secret Sauce — How Different Tools Get Stitched Together](#3-the-secret-sauce)
4. [The Four Parsers](#4-the-four-parsers)
   - [4.1 Tableau Parser](#41-tableau-parser)
   - [4.2 QlikView Parser](#42-qlikview-parser)
   - [4.3 TWS Parser](#43-tws-parser)
   - [4.4 Spark Parser](#44-spark-parser)
5. [The Platform Glue (Gateway, Frontend, Databases)](#5-the-platform-glue)
6. [Testing — What We Check and How](#6-testing)
7. [Limitations & Difficulties (Honest Summary)](#7-limitations--difficulties)
8. [Live Demo Script — Step by Step](#8-live-demo-script)
9. [Quick Reference Card](#9-quick-reference-card)

---

## 1. The Big Picture

### What problem does this solve?

In a big company, data does not live in one place. It flows through many tools:

- **Tableau** builds dashboards that people look at.
- **QlikView** loads and reshapes data for reports.
- **TWS (IBM Tivoli Workload Scheduler)** is the "alarm clock" that runs jobs at set times.
- **Spark / PySpark** does the heavy data crunching.

The problem: **nobody can easily answer simple questions**, like:

- "If this database table breaks, which dashboards stop working?"
- "Where did the number on this dashboard actually come from?"
- "Which scheduled jobs touch this table, and when do they run?"

Today, answering these means asking five different teams and reading thousands of lines of code by hand.

### What Ariadne does

Ariadne reads the files from all four tools and builds **one giant map** (a "knowledge graph")
that connects everything end to end. You can then click any dashboard, table, column, or job
and **trace it backward to its source or forward to everything it affects**.

Think of it like Google Maps for your company's data — every road (data flow) connected,
no matter which "city" (tool) it passes through.

### The name

The project is called **Ariadne** — from the Greek myth where Ariadne's thread helped
guide someone out of a maze. Here, the "thread" is data lineage.

---

## 2. Architecture

### The simple version

```
   Your Files                Parsers              Storage             What You See
  -----------               ---------            ---------           --------------

  .twb / .twbx  ───────►  Tableau  :8001  ─┐
  TWS dumps     ───────►  TWS      :8002  ─┤
  .qvs / .qvw   ───────►  QlikView :8003  ─┼──►  Neo4j (graph)  ──►  Gateway  ──►  Website
  .py / .sql    ───────►  Spark    :8004  ─┘     Postgres (SQL)      :8000        :3000
```

### The parts, explained simply

| Part | What it is | Plain-English job |
|------|-----------|-------------------|
| **4 Parsers** | Small independent web services | Each reads ONE kind of file and writes what it learns into the shared map |
| **Neo4j** | A graph database | Stores the map — nodes (things) and edges (connections between them) |
| **Postgres** | A regular SQL database | A second copy of the TWS data, good for "spreadsheet-style" questions |
| **Gateway** | The front door (FastAPI) | One single doorway — it routes file uploads to the right parser and answers questions about the map |
| **Frontend** | A website (Next.js) | The pretty screen where you upload files, search, and see the graph drawn out |

### Why four separate parsers instead of one big one?

Because each source tool is completely different. A Tableau file is XML. A Spark file is Python
code. A TWS dump is its own little language. Keeping them separate means:

- One parser breaking does not break the others.
- Each team can work on its own parser.
- Each parser can be tested on its own.

The clever part is that even though they are separate, **their output automatically merges**
into one connected map. That trick is explained next.

---

## 3. The Secret Sauce

### How do four separate parsers end up building ONE connected map?

This is the single most important idea in the whole project, so let's go slow.

Imagine a table called `PROD.SALES.ORDERS`.

- The **Tableau** parser sees a dashboard *reading* from `PROD.SALES.ORDERS`.
- The **Spark** parser sees a job *writing* to `PROD.SALES.ORDERS`.

These two parsers never talk to each other. So how do they know it's the *same* table?

**The answer: they both turn the table's full name into the exact same fingerprint.**

Every parser runs the table name through the same recipe:

1. Take the full name: `PROD.SALES.ORDERS`
2. Make it lowercase and tidy: `prod.sales.orders`
3. Run it through a math function called **SHA-256** to get a fixed fingerprint (an ID).

Because both parsers use the **same recipe**, they get the **same fingerprint**. When they both
save to Neo4j using a command called `MERGE` ("create this, but only if it doesn't already exist"),
the two pieces land on the **exact same node**.

```
  Tableau parser  ──writes──►  node "prod.sales.orders"  ◄──writes──  Spark parser
                                        (same node!)
```

The result: a Tableau dashboard and a Spark job are now connected through a shared table —
**with zero glue code, zero manual mapping, and no extra database**. The lineage thread runs
all the way through.

### Three promises every parser keeps

1. **Same input = same output, every time.** No randomness, no clocks. Re-reading the same file
   gives identical IDs. (We even fix `PYTHONHASHSEED=0` so Python's hashing is predictable.)
2. **Re-running is safe.** Because of `MERGE`, parsing the same file twice does not create duplicates.
3. **No secrets leak.** Passwords inside connection strings are scrubbed out before anything is saved.

---

## 4. The Four Parsers

Each parser section below follows the same shape:
**What it reads → What it produces → How it works → A worked example → Tests → Limits.**

A quick reminder of how to read the "things and connections" tables: a **node** is a thing
(a dashboard, a table, a column); an **edge** is a labelled arrow between two things
(`USES_FIELD`, `DERIVES_FROM`). Everything a parser learns is expressed as nodes and edges.

---

### 4.1 Tableau Parser

#### What it reads
- `.twb` files — a Tableau workbook, which is really just a big **XML text file**.
- `.twbx` files — a **zip bundle** that contains a `.twb` plus its data extracts and images.

Tableau changed its file format over the years, so the same idea can appear written in several
different ways. The parser is built to tolerate that (more on this under "the hard part" below).

#### What a Tableau file actually contains
A workbook is a nested structure, roughly:

- **Datasources** — each one points at a database (a *connection*), reads one or more *tables*,
  and defines *fields* (columns). Fields can be plain physical columns, or **calculated fields**
  (formulas built from other fields).
- **Worksheets** — a single chart/view. Each worksheet uses certain fields from certain datasources,
  placed on "shelves" (rows, columns, filters, colour, tooltip…).
- **Dashboards** — a layout that *displays* several worksheets together.

#### What it produces (the map pieces)

| Node (a "thing") | What it represents |
|---|---|
| `:TableauWorkbook` | The file itself |
| `:TableauDatasource` | One data source inside the workbook |
| `:TableauWorksheet` | One chart/view |
| `:TableauDashboard` | A dashboard that arranges worksheets |
| `:Parameter` | A Tableau parameter (a user-controlled value) |
| `:Connection` *(shared)* | The database/server it connects to |
| `:Table` *(shared)* | A physical table — **this is the node that merges with other tools** |
| `:Attribute` *(shared)* | A column or field (physical or calculated) |

| Edge (a "connection") | Meaning in plain English |
|---|---|
| `CONTAINS_DATASOURCE` / `CONTAINS_WORKSHEET` / `CONTAINS_DASHBOARD` | The workbook holds these |
| `CONNECTS_VIA` | A datasource connects through a connection |
| `READS_TABLE` | A datasource reads a physical table |
| `HAS_COLUMN` / `HAS_FIELD` | A table/datasource owns this column/field |
| `DERIVES_FROM` | A calculated field is built from other fields |
| `USES_FIELD` | A worksheet uses this field (and on which shelf) |
| `DISPLAYS_WORKSHEET` | A dashboard shows this worksheet |

#### How it works (step by step)
1. If it's a `.twbx` zip, unzip it to get the `.twb` inside.
2. Read the XML into a tree.
3. **Clean up the tag names.** Newer Tableau versions wrap some tags in long prefixes like
   `_.fcp.ObjectModelEncapsulateLegacy.false...relation`. The parser strips these down to the plain
   name (`relation`) so the rest of the code stays simple. This one step is what lets a single parser
   handle many Tableau versions.
4. **Walk each datasource:** read its connection (server, database, schema), find the tables it
   reads, and list its columns.
5. **Untangle relations.** A datasource can read a table four different ways, and the parser handles
   each: a plain `table`, a `join` (a tree of tables joined together — it walks the whole tree), a
   `text` relation (hand-written **custom SQL** — handed to the `sqlglot` library to find the real
   tables, including multi-CTE queries), or a `stored-proc`.
6. **Resolve calculated fields.** This is the cleverest part — see the worked example below. It
   follows formula chains, handles LOD expressions and table calcs, and detects loops so it never
   gets stuck.
7. **Walk worksheets and dashboards** to record which fields appear where, and which dashboards show
   which worksheets.
8. **Run a "coverage" check** — a safety net that lists any XML elements the parser walked past but
   didn't understand, so we never silently miss something new.
9. Hand the finished map to the writer, which saves it into Neo4j in efficient batches.

#### Worked example — calculated fields (real fixture)
Here is a real snippet from one of the test workbooks:

```xml
<column name='[Amount]' datatype='real' role='measure'/>
<column name='[AmountWithTax]'>
  <calculation class='tableau' formula='[Amount] * 1.18'/>
</column>
<column name='[Profit]'>
  <calculation class='tableau' formula='[AmountWithTax] - [Amount]'/>
</column>
<column name='[TotalPerCustomer]'>
  <calculation class='tableau' formula='{FIXED [CustomerID] : SUM([Amount])}'/>
</column>
```

The parser reads these formulas, finds the `[bracketed]` field names inside each one, and builds
a dependency chain:

```
  Amount ──DERIVES_FROM──◄ AmountWithTax ──DERIVES_FROM──◄ Profit
  Amount ──DERIVES_FROM──◄ TotalPerCustomer        (also pulls out CustomerID from the LOD braces)
```

So if someone later asks *"what would break if we change `Amount`?"*, the answer includes
`AmountWithTax`, `Profit`, and `TotalPerCustomer` — automatically.

The four formula styles it understands:
- **Simple:** `[Amount] * 1.18`
- **Nested / chained:** `[AmountWithTax] - [Amount]` (depends on another calc)
- **LOD (Level of Detail):** `{FIXED [CustomerID] : SUM([Amount])}` — it pulls out *both* the
  grouping field and the measured field
- **CASE / IF:** `IF [Amount] > 1000 THEN 'High' ... END` — pulls every field mentioned

#### Other capabilities worth mentioning
- **Federated joins** — joins across two different databases.
- **Data blending** — when Tableau mixes two datasources on a shared field.
- **Groups, sets, bins, hierarchies** — treated as real lineage objects, not ignored.
- **Cross-datasource references** — a formula that points at `[Parameters].[X]` or a field in another
  datasource is resolved after all datasources are read.

#### API surface
A small FastAPI service: `POST /parse` (parse a file), `GET /health`, `GET /version`.

#### Tests
- **About 173 tests total** (~125 small unit tests + ~48 bigger integration tests).
- **12 hand-built sample workbooks**, from a one-table smoke test up to a "kitchen sink" file with
  federated joins, LOD calcs, custom SQL with CTEs, and Unicode field names.
- A dedicated **"lineage stress" test** that pins down 11 previously-fixed bugs (the messy tag names,
  stored procs, field-name collisions, escaped brackets, blending, hierarchies…) so they can never
  quietly come back.
- Target: **80%+ of the code covered**, and **100%** on the two trickiest files (the calculation
  resolver and the relation walker).

#### Limits & difficulties
- **Published datasources** (data shared centrally on a Tableau Server) are skipped for now.
- **Extract files** (`.hyper` / `.tde`) are only flagged as "present" — we don't read the schema
  inside them.
- **Custom SQL**: we capture which *tables* a query uses, but not yet the column-by-column flow
  *inside* the query.
- Very large workbooks (50MB+) are not speed-optimised yet.
- **The hard part:** Tableau's XML is inconsistent across versions — the same concept is written
  several different ways, and only *some* tags get the long prefixes. The tag-cleaning step is
  essential, and the parser must use precise paths (e.g. "datasources directly under the workbook")
  rather than broad searches, or it accidentally picks up worksheet-level references.

---

### 4.2 QlikView Parser

#### What it reads
- `.qvs` files — QlikView **load scripts** (plain text that looks a bit like SQL mixed with macros).
- `.qvw` files — **binary** QlikView documents. The script is buried inside a binary container; the
  parser digs it back out.
- `.qvf` files — Qlik Sense, a close cousin with similar syntax.
- Optionally a paired `.xml` export that adds sheet/chart details.

#### Why this is the hardest language to parse
QlikView's load script is a real little programming language. It has variables, macros that expand
inline, included sub-files, subroutines you can call, and many different ways to build a table.
Because of this, this parser uses **ANTLR4** — a professional tool where you describe the language
in a formal "grammar" and it generates a robust parser. (Regular expressions were tried first and
proved too fragile; ANTLR replaced them.)

#### What it produces

| Node | What it represents |
|---|---|
| `:QlikScript` | The load script (the file) |
| `:QlikTable` | An **in-memory** table that the script builds |
| `:Table` *(shared)* | A **physical** database table it reads from |
| `:Connection` *(shared)* | An ODBC / OLEDB / LIB connection |
| `:Attribute` *(shared)* | A field (column) |
| `:Variable` | A `SET` / `LET` variable |
| `:Subroutine` | A reusable `SUB ... END SUB` block |
| `:QlikSheet` / `:QlikChart` | Dashboard sheets and charts (from the optional XML) |

Key edges: `LOADS_FROM_TABLE` (in-memory table built from a physical one), `DERIVES_FROM_TABLE`
(one in-memory table built from another, e.g. a RESIDENT load), `JOINS_WITH`, `CONCATENATES_INTO`,
`HAS_FIELD`, `USES_VARIABLE`, and `DISPLAYS_CHART`.

#### Worked example — a RESIDENT load (real fixture)
```qlik
ODBC CONNECT TO 'TERADATA_PROD';

Orders:
LOAD OrderID, CustomerID, OrderDate, Amount;
SQL SELECT OrderID, CustomerID, OrderDate, Amount
FROM PROD.SALES.ORDERS;

OrdersByCustomer:
LOAD CustomerID,
     SUM(Amount)    AS TotalAmount,
     COUNT(OrderID) AS OrderCount
RESIDENT Orders
GROUP BY CustomerID;
```

What the parser produces from this:

```
  Connection 'TERADATA_PROD'
        │ USES_CONNECTION
  QlikTable "Orders" ──LOADS_FROM_TABLE──► Table PROD.SALES.ORDERS   (a real DB table — mergeable!)
        │ DERIVES_FROM_TABLE (via='resident')
  QlikTable "OrdersByCustomer"   with fields TotalAmount, OrderCount
```

Notice two things: (1) `PROD.SALES.ORDERS` is a **shared `:Table`** — if a Spark or Tableau file
also touches it, they all land on one node. (2) The second table is correctly recorded as *derived
from* the first, in memory, not from the database.

#### How it works (step by step)
1. **Pre-process the script.** Detect the text encoding (UTF-16, UTF-8, or Windows-1252), strip
   comments and junk, pull in any `$(Include=...)` sub-files (up to 10 levels deep, with a loop
   guard so two files including each other can't spin forever), and expand `$(variable)` macros.
2. **Parse with ANTLR** into a structured tree.
3. **First pass:** collect every `SET` / `LET` variable into a lookup table.
4. **Second pass:** walk the statements in order — connections, LOADs, joins, concatenations, fields.
5. **Parse the embedded SQL** inside `SQL SELECT ...` blocks with `sqlglot` to find the real
   database tables and columns.
6. **Handle `LOAD *`** (load every column) by deferring it until all tables are known, then filling
   in the actual column list.
7. Optionally read the `.xml` to connect charts and sheets back to the fields they show.

#### Standout capabilities
- Understands a **huge slice of the QlikView language**: LOAD, SQL SELECT, RESIDENT, JOIN, INLINE,
  CONCATENATE, BINARY, MAPPING LOAD, QUALIFY, SECTION ACCESS, AUTOGENERATE, RENAME, plus SET / LET /
  SUB / CALL and `$(...)` macro expansion.
- **BINARY-load inheritance:** if document B starts with `BINARY 'A.qvw'`, it inherits A's *entire*
  data model, linked back with `DERIVES_FROM` edges. This is how QlikView estates chain documents
  together, and the parser follows the chain.
- **QVD-header reading:** QlikView's own `.qvd` data files carry a header describing their columns;
  the parser can read it to enrich lineage.
- **Secret scrubber:** any password inside a connection string is removed (and replaced with a
  harmless fingerprint) *before* anything is written — so credentials never reach the graph.

#### Tests
- **About 193 tests** (~139 unit + ~54 integration).
- **10 sample scripts** covering simple SQL loads, RESIDENT loads, joins, CONCATENATE,
  file loads, variables/includes, subroutines, a realistic dashboard (with paired XML), comment
  edge cases, and QVD loads.
- Three **CI gates** run on every change: **ID stability** (same file → identical IDs),
  **idempotent merge** (re-running changes nothing), and a **secret-leak grep** (fails the build if
  any password string appears in the output).

#### Limits & difficulties
- **Dynamic SQL** built from variables (e.g. `SQL SELECT * FROM $(vTableName)`) can't be fully
  resolved — it's flagged `lineage_partial=true` with a warning rather than guessing the table.
- **VBScript macros** inside `.qvw` files are out of scope.
- **The hard part:** detecting the right **text encoding** for old scripts, and reliably extracting
  the script out of the **binary `.qvw`** container, whose internal layout changes between QlikView
  versions (the parser sniffs for known signatures rather than relying on fixed positions).
- Repo note: there are two similarly named folders (`qlikview-parser` and an older, misspelled
  `qlickview-parser`). The correctly-spelled one is the live parser.

---

### 4.3 TWS Parser

> TWS = IBM Tivoli Workload Scheduler — the enterprise "alarm clock" that runs batch jobs at set
> times and in a set order. It's the layer that actually *triggers* the Spark, Ab Initio, and BTEQ
> jobs. Capturing it answers the operational question *"what runs when, and in what order?"*

#### What it reads
- **Composer-text dumps** (`.txt`) — the native TWS scheduling language (`SCHEDULE ... END`).
- **XML exports** (`.xml`) — the exact same information, just in XML form.

The parser sniffs the file content and automatically picks the right reader. Both readers converge
on the **same internal shape**, so everything downstream is identical regardless of input format.

#### A peek at the language
```tws
SCHEDULE WORKSTATION_A#MASTER#DAILY_SALES_LOAD
  ON RUNCYCLE EVERY_WEEKDAY VALIDFROM 01/01/2025
  AT 0530 UNTIL 0900
  PRIORITY 50
:
  EXTRACT_ORDERS
    SCRIPTNAME "/apps/abinitio/run.sh extract_orders.mp"
    RECOVERY STOP

  TRANSFORM_ORDERS
    SCRIPTNAME "/apps/abinitio/run.sh transform_orders.mp"
    FOLLOWS EXTRACT_ORDERS

  LOAD_ORDERS_TO_DW
    SCRIPTNAME "/apps/bteq/load_orders.bteq"
    FOLLOWS TRANSFORM_ORDERS
END
```

Read in plain English: *"A schedule called DAILY_SALES_LOAD runs every weekday at 05:30. It has
three jobs. EXTRACT runs first; TRANSFORM runs after EXTRACT; LOAD runs after TRANSFORM."*

#### What it produces — and this one is special: it writes to TWO databases

Into **Neo4j (the graph):**

| Node | Represents |
|---|---|
| `:Schedule` | A schedule (the whole `SCHEDULE ... END` block) |
| `:Job` | A job inside a schedule |
| `:Script` *(shared)* | The script a job runs — **the cross-tool connector** |
| `:Resource` | A named resource a job needs (e.g. a licence or a slot — `NEEDS`) |
| `:FileWatcher` | A file a job waits for (`OPENS`) |

Edges: `CONTAINS_JOB` (with an order number), `CALLS_SCRIPT`, `DEPENDS_ON` (FOLLOWS — job→job and
schedule→schedule), `NEEDS_RESOURCE`, and `WAITS_FOR_FILE`.

Into **Postgres (a normal SQL database):** the same data flattened into tables — `schedules`,
`jobs`, `job_dependencies`, `schedule_dependencies`, `resources`, `job_resources`, `file_watchers` —
plus ready-made views like `v_runtime_window`.

**Why both?** Some questions are natural as a graph (*"trace this job's whole dependency chain"*),
and some are natural as SQL (*"**list every job that runs between 05:30 and 06:30 and touches table
X**"*). The graph is great for *connections*; SQL is great for *filtering and time windows*. There's
even an Excel-export endpoint built on the SQL side for ops teams.

#### Worked example — the result of the snippet above
```
  Schedule DAILY_SALES_LOAD  (runs Mon–Fri 05:30, cron: 30 5 * * 1-5)
     │ CONTAINS_JOB (order 0,1,2)
     ▼
  EXTRACT_ORDERS ──CALLS_SCRIPT──► Script /apps/abinitio/run.sh
     ▲ DEPENDS_ON (follows)
  TRANSFORM_ORDERS ──CALLS_SCRIPT──► Script /apps/abinitio/run.sh   (same script node, merged)
     ▲ DEPENDS_ON (follows)
  LOAD_ORDERS_TO_DW ──CALLS_SCRIPT──► Script /apps/bteq/load_orders.bteq
```

#### How it works (step by step)
1. **Detect the format** (composer-text vs XML).
2. **Parse it** — ANTLR for composer-text, lxml for XML — into the shared internal shape. Parse
   errors are *collected* (with line and column) rather than crashing the whole run.
3. **Normalise run cycles.** Turn human rules like `EVERY_WEEKDAY` into a standard cron expression
   (`0 5 * * 1-5`) so they become searchable and comparable.
4. **Figure out scripts.** A line like `/apps/abinitio/run.sh extract_orders.mp` is split into the
   script path, its type (Ab Initio / BTEQ / shell), and its arguments.
5. **Resolve dependencies:** `FOLLOWS` (run after another job/schedule), `NEEDS` (needs a resource),
   `OPENS` (waits for a file).
6. **Write to Neo4j, then write to Postgres.**

#### The cross-tool link (this is the whole point of including TWS)
The `:Script` node is the magic connector. TWS records *"this job runs `/apps/abinitio/run.sh`"*,
while the Ab Initio / BTEQ parsers describe what that script actually *does* to the data. They merge
on the script path — so you can join **"when it runs" (TWS)** to **"what it produces" (the data
jobs)** and finally to **"what reads the result" (Tableau/QlikView)**. That's a full operational +
data lineage thread.

#### API surface
`POST /parse` (single file), `POST /parse/batch` (many files, with cross-file dependency resolution),
`POST /export/excel` (export a SQL view to `.xlsx`), plus `GET /health` and `GET /version`. The
response carries a tri-state status — `ok`, `partial`, or `failed` — and a list of any warnings.

#### Tests
- **About 152 tests** (~95 unit + ~27 integration, across 22 files).
- **8+ sample files** (composer-text + matching XML) covering single jobs, FOLLOWS chains,
  schedule-to-schedule dependencies, resources/files, complex run cycles, a big realistic dump, and
  even a deliberately malformed file to test error handling.
- Integration tests verify **both** the Neo4j writes **and** the Postgres writes, confirm that
  re-parsing doesn't duplicate rows, and check the cross-parser script merge.

#### Limits & difficulties
- **Unresolved dependencies are normal, not errors.** If Job B follows Job C but C is defined in a
  different file, it stays unresolved until you parse both together via `/parse/batch`. The parser
  *warns* rather than failing — real estates are split across many files.
- **Unusual run cycles** (e.g. "every weekday except holidays" using a custom calendar) can't always
  be turned into a clean cron expression — they're marked partial with a warning.
- **The two writes (Neo4j + Postgres) are not one transaction.** Neo4j is written first; if Postgres
  then fails, the two stores could briefly disagree.
- **Definitions, not history.** We read what *should* run; live run logs and actual run history are
  out of scope.
- The most common developer trap: editing the grammar (`.g4`) file but forgetting to regenerate the
  parser code (`make grammar`).

---

### 4.4 Spark Parser

> This is the parser that captures the *heavy data crunching* — the code that actually reads tables,
> transforms them, and writes new ones. It produces the deepest lineage of all four parsers,
> including **column-by-column** detail.

#### What it reads
- **PySpark** code (`.py`) — Python that uses Spark's DataFrame API.
- **Spark SQL** (`.sql`) — pure SQL files.
- **Jupyter notebooks** (`.ipynb`).
- **Databricks notebooks** (`.py` with `# COMMAND` cell separators) and **`.dbc`** archives.
- **Scala** is politely refused (marked "unsupported") — out of scope.

#### What it produces

| Node | Represents |
|---|---|
| `:SparkScript` | The code file |
| `:DataFrame` | An intermediate dataset the code builds (each variable / step) |
| `:Table` *(shared)* | A physical source or target table — **mergeable across tools** |
| `:Attribute` *(shared)* | A column |
| `:UDF` | A user-defined function |
| `:Connection` *(shared)* | A JDBC / Kafka / Snowflake / S3 connection |

Edges: `READS_TABLE`, `WRITES_TABLE`, `HAS_COLUMN`, `HAS_FIELD`, `JOINS_WITH`, `USES_UDF`,
`DERIVES_FROM_DATAFRAME` (this dataset built from that one), and — the standout —
**`DERIVES_FROM` at the column level**, which records that `total = price * quantity`: *which input
columns produced which output column, and the formula used.*

#### Worked example — a simple read/write (real fixture)
```python
orders = spark.read.format("parquet").load("s3://raw/orders/")
orders.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders")
```

Produces:
```
  SparkScript ──CONTAINS_DATAFRAME──► DataFrame "orders"
       DataFrame "orders" ──READS_TABLE──►  Table s3://raw/orders/   (via='spark.read')
       DataFrame "orders" ──WRITES_TABLE──► Table prod.mart.orders   (mode='overwrite')
```

That `prod.mart.orders` node is shared — if a Tableau dashboard later reads it, the two threads join
automatically.

#### Worked example — SQL embedded inside PySpark (real fixture)
```python
raw = spark.read.format("parquet").load("s3://raw/orders/")
raw.createOrReplaceTempView("raw_orders")

enriched = spark.sql("""
    SELECT o.order_id, o.customer_id, o.amount, c.region
    FROM raw_orders o
    INNER JOIN prod.dim.customers c ON o.customer_id = c.id
""")
```

The parser pulls the SQL string out, hands it to `sqlglot`, resolves `raw_orders` back through the
temp view to the real S3 path, picks up the join to `prod.dim.customers`, and records the four output
columns with their sources. No Spark cluster runs — it's all read from the code.

#### How it works (step by step)
This parser is **pure Python** — no Java, no separate grammar tool needed.

1. **Detect the format;** for notebooks, pull out the code cells and stitch the Python ones together.
2. **Read PySpark as code, not text.** It uses Python's built-in `ast` module to understand the
   program's structure, and keeps a "symbol table" tracking every DataFrame variable as it changes.
3. **Spot the important calls:**
   - reads: `spark.read...`, `spark.table(...)`, `spark.sql(...)`, `createDataFrame(...)`
   - writes: `.saveAsTable(...)`, `.save(...)`, `.insertInto(...)`
   - transforms: `.select`, `.withColumn`, `.join`, `.groupBy().agg()`, `.union`, `.filter`, …
4. **For any embedded SQL** (including `F.expr("...")` strings), hand it to `sqlglot` to extract
   tables and columns.
5. **Track the tricky stuff:** variable reassignment (`df = df.filter(...)` becomes a new step),
   if/else branches (both arms recorded), loops, method chains with no intermediate variable
   (given synthetic names like `__anon_1`), temp views, and local function calls (inlined).
6. **Write the finished map to Neo4j**, cleaning up stale steps from previous runs of the same file.

#### Graceful degradation (a key design choice)
When the parser truly can't be sure — for example a table name built at runtime from a command-line
argument, a config file, or a Databricks widget — it does **not** guess and it does **not** crash.
It records everything it *does* know and adds a `lineage_partial=true` marker **with the line
number**, so a human knows exactly where to look. The same applies to malformed SQL, `eval`/`exec`,
and reflection tricks.

#### Tests
- **Very heavily tested:** **238** total across the suites (parser internals + a 128-test
  frontend-contract suite), all passing (a handful skipped only because they need a live Neo4j).
- **Dozens of sample files** covering reads/writes, every join type, window functions, CTEs,
  `MERGE INTO`, notebooks, and deliberately broken/edge cases (dynamic SQL, malformed SQL,
  unsupported Scala).
- **Determinism tests:** parse the same file three times and confirm byte-for-byte identical IDs.
- **Golden tests:** output compared *exactly* against a hand-checked "correct answer" file.
- **Real-world run:** tested against **97 real PySpark files** from a public GitHub repo —
  **96 passed (99%)** in 3.7 seconds, producing 202 DataFrames, 456 columns, and 43 SQL blocks.

#### Limits & difficulties
- **Scala Spark** is not parsed at all (PySpark + Spark SQL only).
- **UDF internals** aren't opened up — we record that a function is *used* and on which columns, but
  we don't read inside the function body to see what it does to those values.
- **Streaming** (`readStream` / `writeStream`) is captured like batch, but streaming-specific ideas
  (watermarks, triggers, checkpoints) aren't modelled.
- **Dynamic table names** from arguments / config / widgets become partial lineage.
- **Following code across files** is limited to one hop.

---

## 5. The Platform Glue

These pieces aren't parsers, but they make the whole thing usable.

### The Gateway (the front door) — port 8000

One single web service (FastAPI) that everything goes through. It:

- **Routes uploads** to the correct parser based on the file type (`.twb` → Tableau, `.py` → Spark, etc.).
- **Answers questions about the map** — list nodes, get a node's neighbors, run lineage traces.
- **Runs canned queries ("presets")** so the website doesn't have to write complicated graph
  queries itself. There are 6 presets, including `lineage-upstream` (trace back to sources) and
  `lineage-downstream` (trace forward to everything affected).
- **Protects the database.** It only allows *read* queries. Anything that tries to change or
  delete data (CREATE, MERGE, DELETE, etc.) is blocked.

The gateway has its own **42 tests** that use fake databases, so they run fast without needing
real Neo4j or Postgres.

### The Frontend (the website) — port 3000

Built with **Next.js 14** and **IBM Carbon** (IBM's design system, dark theme). Graphs are drawn
with **Cytoscape.js**. The main screens:

| Screen | What you do there |
|--------|-------------------|
| **Dashboard** | See whether everything is healthy and how big the map is |
| **Explorer** | Search for any node and expand its neighbors visually |
| **Lineage Tracer** | Pick a node, choose upstream or downstream, and trace the chain |
| **TWS Operations** | Search schedules/jobs by time window (the SQL-style view) |
| **Parse / Upload** | Drop a file in and watch it get parsed |
| **Files** | Browse everything that's already been parsed |

Nodes are **color-coded by source**: Tableau = blue, QlikView = green, TWS = magenta,
Spark = yellow, shared things (tables/columns) = purple. So in one glance you can see a thread
go from a blue dashboard, through a purple table, into yellow Spark code.

### The two databases

- **Neo4j 5.20** (graph): the main map. Has uniqueness rules so the same table/script can't be
  duplicated, and indexes so searches are fast.
- **Postgres 16** (SQL): a mirror of the TWS data for spreadsheet-style questions.

### Running it

- `./start.sh` — builds and starts everything (~90 seconds when nothing changed).
- `./refresh.sh` — reloads your code changes **without wiping the data**.
- `./stop.sh` — shuts down (your data survives).
- There's also a one-EC2 **AWS deployment** via Terraform (~$30/month always-on, ~$12 if stopped nights).

---

## 6. Testing

### How much do we test? (the numbers)

| Parser | Roughly how many tests | Sample files |
|--------|------------------------|--------------|
| Tableau | ~173 | 12 workbooks |
| QlikView | ~193 | 10 scripts |
| TWS | ~152 | 8 dumps (text + XML) |
| Spark | ~238 | 30+ scripts/notebooks |
| Gateway | ~42 | fake databases |

That's roughly **800 automated tests** across the project.

### What kinds of tests are there?

1. **Unit tests** — check one small piece in isolation (e.g. "does it correctly read a LEFT JOIN?").
2. **Integration tests** — run a whole sample file end to end and check the resulting map.
3. **Determinism tests** — parse the same file several times and confirm the IDs are identical.
4. **Idempotency tests** — parse twice and confirm no duplicate nodes appear.
5. **Cross-parser merge tests** — confirm that two parsers writing the same table land on one node.
6. **Security tests** — confirm no passwords leak into the saved data.
7. **Golden tests** (Spark) — compare output exactly against a hand-verified "correct" answer.
8. **Real-world tests** (Spark) — run against 97 real-world files from the internet.

### Why these tests matter

The three big promises (same input → same output, safe re-runs, no secret leaks) are exactly
what make the cross-tool merging trick work. If IDs weren't perfectly stable, the same table
from two parsers would land on two different nodes and the lineage thread would break. So the
determinism and idempotency tests aren't optional extras — they protect the core idea.

---

## 7. Limitations & Difficulties

A frank, plain-English summary. Good presentations admit what doesn't work yet.

### Limits that apply to the whole platform

- **No login/security layer.** It assumes a trusted network. Fine for a demo or internal VPN,
  not yet ready to expose to the open internet.
- **The two databases (Neo4j + Postgres) aren't kept in one transaction.** In rare failures they
  could briefly disagree.
- **We read definitions, not live history.** We see what *should* happen (the code and schedules),
  not what actually ran last night.

### Per-parser difficulties at a glance

| Parser | The hardest part | Biggest current gap |
|--------|------------------|---------------------|
| **Tableau** | Tableau's messy, version-dependent XML | Column-level flow inside custom SQL; extract files only flagged |
| **QlikView** | Old text encodings + binary `.qvw` extraction | Dynamic SQL from variables; VBScript macros out of scope |
| **TWS** | Cross-file dependencies and odd run cycles | Two-database writes not transactional; no runtime history |
| **Spark** | Dynamic/runtime-built table names | No Scala; UDF internals not opened; streaming semantics not modeled |

### The shared theme: "graceful degradation"

When any parser hits something it genuinely can't be sure about (a dynamic name, an unusual
calendar, a macro), it follows the same rule:

> **Don't guess, don't crash. Record what you know, and clearly flag the gap with a warning.**

This is a feature, not a bug. A clearly-marked partial answer with a line number is far more
useful than a confident wrong answer or a hard crash.

---

## 8. Live Demo Script

Here's a clean order to walk through in a live demo.

1. **Start the stack**
   ```bash
   ./start.sh
   ```
   Then open the website at `http://localhost:3000`.

2. **Show health.** On the Dashboard screen, point out that the gateway, Neo4j, Postgres, and
   all four parsers are green.

3. **Upload one file per tool.** Go to the **Parse** tab and upload a `.twb`, a `.qvs`, a `.py`,
   and a TWS dump. Each one is routed to the right parser automatically.

4. **Explore.** Go to the **Files** tab, open one of the parsed files, and show the colored graph.
   Click a node to see its properties.

5. **Trace lineage — the money shot.** Go to **Lineage Tracer**, pick a Tableau dashboard, and
   trace **upstream**. Show how the thread runs:
   blue dashboard → worksheet → datasource → **purple shared table** → **yellow Spark code** that
   produced it. *This is the cross-tool magic working live.*

6. **Show the SQL side.** Go to **TWS Operations** and run a time-window query like
   "everything that runs between 05:30 and 06:30." Optionally export to Excel.

7. **Prove determinism (optional, for technical audiences).** Re-upload the same file and show
   that node counts don't change — `MERGE` makes re-runs a no-op.

### The one cross-tool query to show off

```cypher
MATCH (dash:TableauDashboard)-[:DISPLAYS_WORKSHEET]->(:TableauWorksheet)
      -[:USES_FIELD]->(:Attribute)<-[:HAS_COLUMN]-(t:Table)
      <-[:WRITES_TABLE]-(:DataFrame)<-[:CONTAINS_DATAFRAME]-(spark:SparkScript)
RETURN dash.name, t.fully_qualified_name, spark.name
```

In English: *"Find every Tableau dashboard, follow it down to the physical table it depends on,
then find the Spark job that writes that table."* This works only because both parsers hashed the
table name into the same ID — so the two halves meet on one node.

---

## 9. Quick Reference Card

### Ports & logins (local dev)

```
Website (Frontend)      http://localhost:3000
Gateway API docs        http://localhost:8000/docs
Neo4j Browser           http://localhost:7475   (neo4j / lineagepass)
Postgres                localhost:5432          (lineage / lineagepass)

Parsers (health checks):
  Tableau               http://localhost:8001/health
  TWS                   http://localhost:8002/health
  QlikView              http://localhost:8003/health
  Spark                 http://localhost:8004/health
```

### File type → parser

| File extension | Goes to |
|----------------|---------|
| `.twb`, `.twbx` | Tableau |
| `.txt`, `.xml` (TWS dumps) | TWS |
| `.qvs`, `.qvw`, `.qvf` | QlikView |
| `.py`, `.sql`, `.ipynb`, `.dbc` | Spark |

### One-line summary of each parser

- **Tableau** — reads dashboard files, maps dashboards → worksheets → datasources → tables/columns.
- **QlikView** — reads load scripts, maps the full LOAD/JOIN/SQL data model, even across BINARY inheritance.
- **TWS** — reads the job scheduler, maps schedules → jobs → scripts, writes to both a graph and SQL.
- **Spark** — reads PySpark/SQL code statically, maps reads → transforms → writes with column-level detail.

### The one idea to remember

> **Every parser turns physical names into the same SHA-256 fingerprint.** That's why four
> independent tools quietly build one connected map — no glue, no manual mapping, no ETL.

---

*End of walkthrough. Built for the Ariadne Lineage Platform.*
