# Node ID Derivation Rules

> **Critical:** every parser must implement these rules identically. Cross-parser lineage threading depends on bit-for-bit ID equality across writes from different parsers.

All IDs are `sha256(canonical_string)[:16]`, except where noted. The canonical string is constructed by joining components with `::` (double-colon) and applying the per-rule normalization below.

## Normalization

Unless overridden by a specific rule:

- Lowercase the entire canonical string.
- Trim leading/trailing whitespace on each component.
- Use forward slashes in file paths (`os.path.normpath` then replace `\` with `/`).
- Strip any surrounding brackets `[ ]` from Tableau-style identifiers **before** including in the canonical string.

## Shared labels (written by multiple parsers)

| Label | Canonical string | Notes |
|---|---|---|
| `:Table` | `table::<database>.<schema>.<name>` | Use `fully_qualified_name` as the identity column (UNIQUE). The `id` property is the sha256 of the canonical string. Both must be set. |
| `:Attribute` (physical column) | `attribute::<table_fqn>::<column_name>` | `<table_fqn>` is the full `<database>.<schema>.<name>`, already lowercased. |
| `:Attribute` (Tableau calculated field) | `attribute::<tableau_datasource_id>::<field_name>` | |
| `:Attribute` (QlikView in-memory field) | `attribute::<qlik_table_id>::<field_name>` | |
| `:Connection` | `connection::<class>::<server>::<dbname>` | `class` is the source-system identifier (`teradata`, `oracle`, `snowflake`, …). |
| `:Script` | `script::<absolute_lowercased_path>` | TWS jobs CALL_SCRIPT into the same node Ab Initio / BTEQ parsers create when they parse a script. The path is the lineage anchor. |

## Tableau parser

| Label | Canonical string |
|---|---|
| `:TableauWorkbook` | `tableau_workbook::<absolute_file_path>` |
| `:TableauDatasource` | `tableau_datasource::<workbook_id>::<datasource_name>` |
| `:TableauWorksheet` | `worksheet::<workbook_id>::<worksheet_name>` |
| `:TableauDashboard` | `dashboard::<workbook_id>::<dashboard_name>` |
| `:Parameter` | `parameter::<workbook_id>::<parameter_name>` |

## TWS parser

| Label | Canonical string |
|---|---|
| `:Schedule` | `schedule::<workstation>::<scheduler>::<name>` |
| `:Job` | `job::<schedule_id>::<name>` |
| `:Resource` | `resource::<name>` |
| `:FileWatcher` | `file_watcher::<absolute_lowercased_path>` |

## QlikView parser

| Label | Canonical string |
|---|---|
| `:QlikScript` | `qlik_script::<absolute_file_path>` |
| `:QlikTable` | `qlik_table::<script_id>::<table_name>::<load_order>` |
| `:Variable` | `variable::<script_id>::<variable_name>` |
| `:Subroutine` | `subroutine::<script_id>::<sub_name>` |
| `:QlikSheet` | `qlik_sheet::<script_id>::<sheet_name>` |
| `:QlikChart` | `qlik_chart::<sheet_id>::<chart_name>` |

## Reference Python implementation

```python
import hashlib

def make_id(*parts: str) -> str:
    canonical = "::".join(p.strip().lower() for p in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

# Examples
table_id = make_id("table", f"{db}.{schema}.{name}")
script_id = make_id("script", absolute_path)
```

Every parser ships this exact function in its `utils/ids.py` module. Diverging from this recipe will silently break cross-parser lineage.
