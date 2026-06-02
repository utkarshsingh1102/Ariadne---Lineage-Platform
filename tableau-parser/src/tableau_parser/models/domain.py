"""Intermediate-representation dataclasses produced by the parser modules.

The pipeline returns a `WorkbookIR`; the graph writer consumes it. The IR is
the contract between the two halves — keep it dumb and serializable.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConnectionIR:
    id: str
    klass: str          # class is a Python builtin — `klass` matches the tests
    server: str
    dbname: str
    schema: str = ""
    port: str = ""
    username: str = ""
    # Per-reference source line. Persisted on edges (READS_TABLE.line,
    # CONNECTS_VIA.line) because :Connection is MERGEd cross-script.
    line: int | None = None


@dataclass
class TableIR:
    id: str
    name: str
    schema: str
    database: str
    fully_qualified_name: str
    relation_type: str = "table"  # table / join / custom_sql / stored_proc
    source_type: str = "database"
    # Per-reference source line. Persisted on the READS_TABLE edge because
    # :Table is MERGEd by fully_qualified_name across all scripts.
    line: int | None = None
    # Improvement-v2 §3a — full SQL body for ``custom_sql`` relations
    # (the CDATA payload of ``<relation type='text'>``). Empty for table /
    # join / stored_proc rows. Persisted on the :Table node so downstream
    # tools can re-parse or render it without re-opening the workbook.
    raw_sql: str = ""


@dataclass
class FieldIR:
    id: str
    name: str
    datasource_id: str
    datatype: str = ""
    role: str = ""
    is_calculated: bool = False
    formula: str = ""
    table_fqn: str = ""  # for physical fields when known
    # Source line of the <column> element. For physical fields owned by a
    # single script this lands on the :Attribute node; for fields shared
    # across scripts (rare) the writer keeps the smallest-seen line.
    line: int | None = None
    # Step 8 — sub-field metadata. All optional; only populated when the
    # <column> XML carries the corresponding attribute.
    default_aggregation: str = ""
    ordinal: int | None = None
    precision: int | None = None
    scale: int | None = None
    contains_null: bool | None = None
    # Value aliases (Tableau's `<aliases>` / `<map><bucket>` mapping). Keys
    # are raw values, values are display labels. Persisted as JSON on the
    # :Attribute node since Neo4j doesn't have a native dict type.
    value_aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class CrossDatasourceRefIR:
    """Improvement-v2 §6 — a calc-field reference that resolves into a
    *different* datasource (the ``[ds].[field]`` two-part form, or a
    ``[Parameters].[X]`` reference).

    Built by a workbook-level post-pass after every DatasourceIR is
    populated: it walks each ``DerivesFromIR.refs`` of kind ``cross_source``
    and looks the foreign field up in a global symbol table.

    Persisted as a ``DERIVES_FROM_CROSS_DS`` edge with the formula span so
    the View Source panel can highlight which `[ds].[field]` token in the
    calc body produced the lineage.
    """
    id: str
    target_field_id: str        # the calc field that does the referencing
    source_field_id: str        # the foreign field that satisfies the ref
    source_datasource_name: str # the foreign datasource's name (literal)
    char_start: int
    char_end: int
    formula_snippet: str = ""


@dataclass
class WorksheetBlendIR:
    """Improvement-v2 §9 — one ``<datasource-relationship>`` declaration
    inside a worksheet. Links a primary datasource to a secondary on a
    set of field names. Distinct from an in-database join.
    """
    id: str
    worksheet_id: str
    primary_datasource_name: str
    secondary_datasource_name: str
    on_field_names: list[str] = field(default_factory=list)
    line: int | None = None


@dataclass
class CTEColumnIR:
    """One resolved column lineage edge inside a ``<relation type='text'>``
    custom-SQL block. Emitted only when ``TABLEAU_RESOLVE_CTE_COLUMNS=true``.

    Each row says: "the output column ``output_name`` of the custom-SQL
    relation owned by ``table_fqn`` is derived from the physical column
    ``source_column`` of ``source_table_fqn``, via ``expression``."

    The writer turns each row into a ``DERIVES_FROM_CTE_COLUMN``
    relationship so cross-CTE provenance is queryable. Flag-gated to keep
    default installs lean and to avoid promoting partial lineage as fact
    until the column-resolution path is hardened.
    """
    custom_sql_table_fqn: str
    output_name: str
    source_table_fqn: str
    source_column: str
    expression: str = ""


@dataclass
class FormulaRefIR:
    """One bracketed reference inside a calculation formula.

    The ``char_start``/``char_end`` span lets the source-code panel highlight
    the exact `[token]` inside a `<calculation formula="…">` element. Used by
    the View Source addendum to back-link a graph edge to the precise span
    that produced it.

    ``kind``:
      - ``field``         — plain `[field]` reference to a calc/raw column
      - ``param``         — reference resolves to a workbook parameter
      - ``cross_source``  — two-part `[ds].[field]` reference
      - ``lod_dim``       — bracketed name inside a `{FIXED ...}` dimension list
    """
    source_name: str
    char_start: int
    char_end: int
    kind: str = "field"
    # Optional second name for cross-source refs (the datasource side); empty
    # otherwise. Stored so writers can emit a richer ``DERIVES_FROM_REF`` row.
    datasource_name: str = ""


@dataclass
class DerivesFromIR:
    """Calculation-dependency IR.

    Tests work in *names* (humans read formulas), not IDs. The writer maps
    `(datasource_id, target_field) → Attribute.id` when producing Cypher.
    """
    target_field: str             # name of the calculated field
    source_fields: list[str]      # names of fields it depends on
    datasource_id: str = ""
    formula: str = ""
    # Line of the <calculation> element that holds the formula. Persisted on
    # the DERIVES_FROM edge so the source-code panel can scroll to the calc.
    line: int | None = None
    # Token-level refs harvested from ``formula`` — see FormulaRefIR. Powers
    # the View Source per-token highlight.
    refs: list[FormulaRefIR] = field(default_factory=list)


@dataclass
class HasColumnIR:
    table_id: str
    field_id: str


@dataclass
class ReadsTableIR:
    datasource_id: str
    table_id: str
    relation_type: str = "table"
    line: int | None = None


@dataclass
class FieldUsageIR:
    field_name: str
    shelf: str = "unknown"   # rows / cols / filter / color / size / unknown
    datasource_name: str = ""
    line: int | None = None
    # Aggregation applied at this usage site (e.g. SUM, AVG, COUNTD). Empty
    # when the shelf body uses the field bare (no aggregation wrapper) or
    # when the aggregation can't be inferred. Lifted from the inline pattern
    # ``SUM([Sales])`` inside <rows>/<cols>/<filter>/etc.
    aggregation: str = ""


@dataclass
class WorksheetFilterIR:
    """A filter applied at the worksheet (or datasource) level.

    A datasource-scoped filter has ``worksheet_id == ""``; it's stored on
    DatasourceIR.filters and the writer emits a Datasource→Attribute edge.
    A worksheet-scoped filter is stored on WorksheetIR.filters and emits a
    Worksheet→Attribute edge.
    """
    field_name: str
    datasource_name: str
    worksheet_id: str = ""
    filter_class: str = ""   # categorical | quantitative | relative-date | set | ...
    expression: str = ""
    line: int | None = None


@dataclass
class WorksheetSortIR:
    """A sort applied at the worksheet level."""
    field_name: str
    datasource_name: str
    worksheet_id: str = ""
    direction: str = "ascending"   # ascending | descending
    line: int | None = None


@dataclass
class WorksheetIR:
    id: str
    name: str
    workbook_id: str
    field_usages: list[FieldUsageIR] = field(default_factory=list)
    filters: list[WorksheetFilterIR] = field(default_factory=list)
    sorts: list[WorksheetSortIR] = field(default_factory=list)
    # Improvement-v2 §9 — one ``WorksheetBlendIR`` per
    # ``<datasource-relationship>`` child of the worksheet's ``<view>``.
    blends: list["WorksheetBlendIR"] = field(default_factory=list)
    line: int | None = None
    line_end: int | None = None


@dataclass
class DashboardZoneIR:
    """One <zone> child of a dashboard.

    Worksheet zones are still surfaced via ``DashboardIR.displayed_worksheets``
    for backwards compatibility; non-worksheet zones become DashboardZoneIR
    nodes so the writer can emit ``Dashboard-[HAS_ZONE]->DashboardZone``.
    """
    id: str
    dashboard_id: str
    kind: str = "worksheet"   # worksheet | filter | parameter | text | image | web | container | blank
    name: str = ""
    target_worksheet: str = ""
    target_parameter: str = ""
    line: int | None = None


@dataclass
class DashboardActionIR:
    """One <action> inside a dashboard's <actions> block.

    Captures source → target sheets plus the field(s) the action carries
    (filter actions), the parameter it sets (parameter actions), or the
    URL (URL actions).
    """
    id: str
    dashboard_id: str
    kind: str = "filter"   # filter | highlight | parameter | url | go-to-sheet
    name: str = ""
    source_sheets: list[str] = field(default_factory=list)
    target_sheets: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    parameter_name: str = ""
    url: str = ""
    line: int | None = None


@dataclass
class DashboardIR:
    id: str
    name: str
    workbook_id: str
    displayed_worksheets: list[str] = field(default_factory=list)
    zones: list[DashboardZoneIR] = field(default_factory=list)
    actions: list[DashboardActionIR] = field(default_factory=list)
    line: int | None = None
    line_end: int | None = None


@dataclass
class ParameterIR:
    id: str
    name: str
    workbook_id: str
    datatype: str = ""
    current_value: str = ""
    line: int | None = None
    # Improvement-v2 §4 — opt-in hybrid: the id of the ParameterScopeIR
    # that owns this parameter. Empty when the parameter wasn't grouped
    # under a scope (older fixtures, defensively).
    scope_id: str = ""


@dataclass
class ParameterScopeIR:
    """Improvement-v2 §4 — a synthetic node that represents Tableau's
    ``<datasource name='Parameters'>`` block WITHOUT polluting the
    ``:TableauDatasource`` label (which is reserved for user datasources
    with real fields and connections).

    Emitted as ``:TableauParameterScope``. Owns the workbook's parameters
    via ``HAS_PARAMETER``; itself owned by the workbook via
    ``HAS_PARAMETER_SCOPE``.
    """
    id: str
    name: str              # always "Parameters" for now; left configurable
    workbook_id: str
    line: int | None = None


@dataclass
class GroupIR:
    """A `<group>` — fields whose values are recoded into bucket names.

    Example: `City` grouped into `Region` (East / West / North / South).
    The group derives from its source field; downstream consumers may
    reference the group's name in calc formulas.
    """
    id: str
    name: str
    datasource_id: str
    source_field_names: list[str] = field(default_factory=list)
    line: int | None = None


@dataclass
class SetIR:
    """A `<set>` — a filtered subset of a field's values.

    Sets derive from one source field plus a (possibly complex) condition.
    """
    id: str
    name: str
    datasource_id: str
    source_field_names: list[str] = field(default_factory=list)
    condition_expr: str = ""
    line: int | None = None


@dataclass
class BinIR:
    """A `<column>` produced by binning a numeric measure."""
    id: str
    name: str
    datasource_id: str
    source_field_names: list[str] = field(default_factory=list)
    size: str = ""
    line: int | None = None


@dataclass
class HierarchyIR:
    """A `<drill-path>` — ordered list of fields that drill into each other."""
    id: str
    name: str
    datasource_id: str
    levels: list[str] = field(default_factory=list)
    line: int | None = None


@dataclass
class DatasourceIR:
    id: str
    name: str
    workbook_id: str
    caption: str = ""
    is_federated: bool = False
    has_extract: bool = False
    connections: list[ConnectionIR] = field(default_factory=list)
    tables: list[TableIR] = field(default_factory=list)
    fields: list[FieldIR] = field(default_factory=list)
    derives_from: list[DerivesFromIR] = field(default_factory=list)
    has_columns: list[HasColumnIR] = field(default_factory=list)
    reads_tables: list[ReadsTableIR] = field(default_factory=list)
    # Datasource-wide filters. Same IR shape as worksheet filters with
    # ``worksheet_id == ""`` — the writer emits a Datasource→Attribute edge
    # instead of a Worksheet→Attribute edge.
    filters: list[WorksheetFilterIR] = field(default_factory=list)
    # Step 6 derived-field families.
    groups: list[GroupIR] = field(default_factory=list)
    sets: list[SetIR] = field(default_factory=list)
    bins: list[BinIR] = field(default_factory=list)
    hierarchies: list[HierarchyIR] = field(default_factory=list)
    # Improvement-v2 §3b — populated only when TABLEAU_RESOLVE_CTE_COLUMNS=true.
    # Each row: ``output_column -> (source_table_fqn, source_column)`` through
    # the CTE chain of a custom-SQL relation owned by this datasource.
    cte_columns: list[CTEColumnIR] = field(default_factory=list)
    line: int | None = None
    line_end: int | None = None


@dataclass
class WorkbookIR:
    id: str
    name: str
    file_path: str
    version: str = ""
    parsed_at: str = ""

    datasources: list[DatasourceIR] = field(default_factory=list)
    worksheets: list[WorksheetIR] = field(default_factory=list)
    dashboards: list[DashboardIR] = field(default_factory=list)
    parameters: list[ParameterIR] = field(default_factory=list)
    # Improvement-v2 §4 — one per ``<datasource name='Parameters'>`` block.
    # Owns the parameters list above. Kept separate from ``datasources`` so
    # consumer code that asks "how many real datasources?" gets a clean
    # answer.
    parameter_scopes: list[ParameterScopeIR] = field(default_factory=list)
    # Improvement-v2 §6 — resolved cross-datasource calc-field refs. Built
    # by a workbook-level post-pass after every DatasourceIR is populated.
    cross_ds_refs: list[CrossDatasourceRefIR] = field(default_factory=list)

    # The root <workbook> element spans the whole file; line_end is captured
    # from the tree so a "View Source" click on the workbook node opens the
    # file at the very top.
    line: int | None = None
    line_end: int | None = None

    warnings: list[dict] = field(default_factory=list)

    def stats(self) -> dict[str, int]:
        attributes = sum(len(d.fields) for d in self.datasources)
        calc = sum(1 for d in self.datasources for f in d.fields if f.is_calculated)
        tables = sum(len(d.tables) for d in self.datasources)
        return {
            "datasources": len(self.datasources),
            "tables": tables,
            "attributes": attributes,
            "calculated_fields": calc,
            "worksheets": len(self.worksheets),
            "dashboards": len(self.dashboards),
            "parameters": len(self.parameters),
            "parameter_scopes": len(self.parameter_scopes),
        }
