# Shared Labels Contract

For each label written by more than one parser, this document specifies:

- **Owner** — the parser that defines the property set (others must subset, never override differently).
- **Required properties** — every writer must set these.
- **Optional properties** — any writer may set; if absent, downstream consumers must tolerate `null`.

When a parser MERGEs an existing shared node, it must **only set new properties** with `ON CREATE SET` and **never** clobber existing values with `SET`. Use `coalesce()` for fields where the most-informative writer wins.

---

## `:Table`

**Owners:** Ab Initio (assumed external), Teradata, Tableau, QlikView. The first parser to encounter a physical table creates the node; everyone else MERGEs.

| Property | Required | Notes |
|---|---|---|
| `id` | yes | sha256 of canonical string (see node-id-rules.md) |
| `fully_qualified_name` | yes | `<database>.<schema>.<name>`, lowercased. UNIQUE constraint. |
| `name` | yes | Plain table name |
| `schema` | yes | Schema name |
| `database` | yes | Database name |
| `source_type` | no | `database` (default), `file`, `inline`, `extract`. Set by whichever parser knows. |
| `first_seen_by` | no | Parser name that first MERGEd this node. `ON CREATE SET` only. |

**MERGE pattern:**

```cypher
MERGE (t:Table {fully_qualified_name: $fqn})
  ON CREATE SET t.id = $id, t.name = $name, t.schema = $schema,
                t.database = $database, t.first_seen_by = $parser
  ON MATCH SET t.source_type = coalesce(t.source_type, $source_type)
```

---

## `:Attribute`

**Owners:** every parser. Physical columns are owned by whichever schema-providing parser sees them first. Calculated / derived fields are scoped to the parser that defines them (Tableau calc, QlikView LOAD expression).

| Property | Required | Notes |
|---|---|---|
| `id` | yes | sha256 of canonical string |
| `name` | yes | Field/column name, brackets stripped |
| `datatype` | no | Native datatype string |
| `is_calculated` | no | `true` for derived fields, `false`/null for physical columns |
| `formula` | no | Set when `is_calculated=true`. The raw expression. |
| `role` | no | Tableau-specific: `dimension`/`measure` |

---

## `:Connection`

**Owners:** Tableau, QlikView, Ab Initio.

| Property | Required | Notes |
|---|---|---|
| `id` | yes | |
| `class` | yes | Source-system identifier: `teradata`, `oracle`, `snowflake`, `sqlserver`, `mysql`, `hive`, `file`, `excel`, etc. |
| `server` | yes | Hostname or path |
| `dbname` | yes | Database name (may be empty string for file connections) |
| `schema` | no | Default schema (Tableau ships this; QlikView usually does not) |
| `port` | no | |
| `username` | no | Never store passwords |

---

## `:Script`

**Owners:** TWS (writes `:Job CALLS_SCRIPT :Script`), Ab Initio (writes `:Script CONTAINS_COMPONENT :Component`), BTEQ (similar).

| Property | Required | Notes |
|---|---|---|
| `path` | yes | Absolute, lowercased. UNIQUE constraint. |
| `id` | yes | sha256 of canonical string |
| `script_type` | yes | `abinitio` / `bteq` / `shell` / `sql` / `python` / `unknown` |
| `args` | no | Stripped runtime arguments |

TWS is typically the **first** writer (it references scripts before they're parsed). Subsequent parsers (Ab Initio, BTEQ) MERGE on `path` and enrich.

---

## Relationship contract

Cross-parser relationships use these canonical types. Adding a new one requires a contract version bump.

| Type | Direction | Meaning |
|---|---|---|
| `HAS_COLUMN` | `:Table` → `:Attribute` | Physical column on physical table |
| `DERIVES_FROM` | `:Attribute` → `:Attribute` | Derived/calculated dependency |
| `READS_TABLE` | `:TableauDatasource` / `:QlikTable` / `:Script` → `:Table` | Source-system read |
| `WRITES_TABLE` | `:Script` → `:Table` | Source-system write (added when Ab Initio / BTEQ parsers land) |
| `CALLS_SCRIPT` | `:Job` → `:Script` | TWS job execution |
| `USES_CONNECTION` | `:TableauDatasource` / `:QlikScript` → `:Connection` | |
