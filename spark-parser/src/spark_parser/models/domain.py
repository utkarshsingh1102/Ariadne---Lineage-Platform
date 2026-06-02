"""Domain IR for the Spark parser.

Attribute names mirror the test contract in ``unit/`` and ``integration/`` —
every property a test reads exists here.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConnectionIR:
    """Physical data-source connection — v0.2 §9 cross-parser :Connection node.

    Mirrors the Tableau parser's :Connection shape (``klass``, ``server``,
    ``port``, ``dbname``, ``schema``, ``username``) so a Spark job and a
    Tableau workbook pointing at the same host/db converge on the same
    Neo4j node via deterministic id hashing in ``utils/ids.connection_id``.

    ``options`` carries connector-specific extras the canonical fields can't
    hold (Kafka topic, S3 path prefix, JDBC driver class, sfWarehouse, …).

    Per ``connections.md``:

    * ``resolved`` — False when the connection's identifying value is
      runtime/secret/dynamic and we couldn't pin it statically. The node
      still exists so the I/O site is preserved in the graph.
    * ``has_credentials`` — True when the source code passed a
      user/password/token/key option. The values themselves are never
      stored in the graph.
    * ``source`` — provenance for unresolved connections: ``env``,
      ``secret``, ``runtime``, ``dynamic``, …
    * ``detail`` — free-form provenance string (the symbolic name, the
      env-var key, etc.) so reviewers can see *why* it's unresolved.
    """
    id: str | None = None
    klass: str | None = None         # "jdbc:postgresql" | "kafka" | "snowflake" | "s3" | "unknown:foo" | …
    server: str | None = None
    port: int | None = None
    dbname: str | None = None
    schema: str | None = None
    username: str | None = None
    options: dict[str, str] = field(default_factory=dict)
    resolved: bool = True
    has_credentials: bool = False
    source: str | None = None
    detail: str | None = None
    # First call-site line in the source script where this connection is
    # referenced. Lives per-reference (not on the shared :Connection node)
    # so the writer can persist it on the edge that joins it to the
    # consuming/producing :DataFrame — the source viewer uses that to
    # scroll the panel to the relevant I/O statement.
    line: int | None = None


@dataclass
class TableIR:
    fully_qualified_name: str | None = None
    location: str | None = None
    storage_format: str | None = None
    name: str | None = None
    schema: str | None = None
    database: str | None = None
    # v0.2 §9 — structured connection metadata derived from the read/write
    # options. ``None`` only when the format is unknown and no location is
    # discernible (e.g. a write through a dynamic helper).
    connection: ConnectionIR | None = None
    # Call-site line where this Table is read or written in the script.
    # Persisted by the writer on the relationship (``READS_TABLE.line`` /
    # ``WRITES_TABLE.line``) — :Table itself is MERGEd across scripts so
    # stamping on the node would clobber siblings.
    line: int | None = None


@dataclass
class AttributeIR:
    name: str
    datatype: str | None = None
    is_derived: bool = False
    derivation_formula: str | None = None
    # v0.2 §5 — nested struct path. ``"address.city"`` means the field is a
    # second-level member of a top-level ``address`` struct. None for ordinary
    # top-level columns.
    path: str | None = None
    # v0.2 §5 — record source / target datatype across a single cast(). The
    # current ``datatype`` field always holds the *result* type; this preserves
    # the conversion lineage so consumers can see "string → int".
    type_history: list[tuple[str | None, str | None]] = field(default_factory=list)


@dataclass
class DerivationIR:
    target_column: str
    source_columns: list[str] = field(default_factory=list)
    via: str = "select"           # select | withColumn | rename | join | agg | udf | expr
    formula: str | None = None


@dataclass
class DataFrameEdgeIR:
    """`:DERIVES_FROM_DATAFRAME` — DataFrame → DataFrame transformation."""
    source_var: str | None = None
    # Identity of the source DataFrame at edge-record time. When present,
    # the collapse pass resolves the predecessor by id (unambiguous) instead
    # of by name — necessary for variables that are reassigned multiple
    # times in the same script (``df = transform(df)`` etc.). Defaults to
    # None for edges whose source isn't a concrete DataFrameIR at record
    # time (external functions, conditional placeholders).
    source_id: str | None = None
    via: str = "select"           # select | filter | join | union | groupby | external_function | ...


@dataclass
class TransformStepIR:
    """One operation in a collapsed DataFrame's transformation chain.

    Captures the full per-op context the ``dataframe_collapse_plan.md``
    schema requires: source line, operation name, kind tag, the source-text
    expression where applicable, the touched columns (in → out), and join
    metadata when the op is a join. Stored ordered, one entry per
    intermediate operation between two anchor nodes.
    """
    seq: int = 0
    op: str = ""                       # withColumn | select | filter | join | drop | agg | dropDuplicates | …
    kind: str = ""                     # derive | rename | drop | filter | join | agg | select | cast | meta
    expr: str | None = None
    output_column: str | None = None
    output_columns: list[str] = field(default_factory=list)
    input_columns: list[str] = field(default_factory=list)
    join_other: str | None = None
    join_keys: list[str] = field(default_factory=list)
    join_how: str | None = None
    line: int | None = None


@dataclass
class JoinIR:
    left: str                      # var name (or anon ID) of left DataFrame
    right: str                     # var name of right DataFrame
    join_type: str = "inner"
    join_condition: str | None = None


@dataclass
class WriteEdgeIR:
    target: TableIR
    mode: str = "overwrite"
    via: str = "saveAsTable"       # saveAsTable | insertInto | save
    # Columns the writer was partitioned on (``.partitionBy("a","b")`` or the
    # equivalent kwarg threaded through an inlined wrapper). Empty when the
    # write is unpartitioned.
    partition_columns: list[str] = field(default_factory=list)
    # Delta-specific Z-ORDER columns, e.g. ``ZORDER BY (customer_id, order_id)``.
    z_order_columns: list[str] = field(default_factory=list)
    # Call-site line — persisted on the WRITES_TABLE / WRITES_TO_CONNECTION
    # edge so the source viewer can scroll to the write statement.
    line: int | None = None


@dataclass
class UDFIR:
    name: str
    is_pandas_udf: bool = False
    return_type: str | None = None
    input_types: list[str] = field(default_factory=list)
    # Line in the source script where the UDF was defined / registered.
    # Persisted on the :UDF node — UDFs are script-owned, not shared.
    line: int | None = None


@dataclass
class WarningIR:
    type: str
    detail: str
    line: int | None = None
    subtype: str | None = None


@dataclass
class DataFrameIR:
    var_name: str
    creation_order: int = 0
    is_anonymous: bool = False
    lineage_conditional: bool = False
    lineage_partial: bool = False
    from_sql_block: bool = False
    reads_from: list[TableIR] = field(default_factory=list)
    writes_to: list[TableIR] = field(default_factory=list)
    write_edges: list[WriteEdgeIR] = field(default_factory=list)
    fields: list[AttributeIR] = field(default_factory=list)
    derivations: list[DerivationIR] = field(default_factory=list)
    derives_from_dataframe: list[DataFrameEdgeIR] = field(default_factory=list)
    joins: list[JoinIR] = field(default_factory=list)
    id: str | None = None
    # v0.2 §2 — notebooks: the cell index in which this DataFrame was first
    # bound. None for plain `.py` / `.sql` files (no cell concept).
    cell_index: int | None = None
    # v0.2 §5 — column renames applied to this DataFrame, ordered by
    # application: list of ``(new_name, old_name)``. Survives chain steps so
    # downstream consumers can trace a final-column name back to its origin.
    renames: list[tuple[str, str]] = field(default_factory=list)
    # v0.2 §6 — enterprise runtime hints. None unless an explicit `cache()`,
    # `persist(...)`, `checkpoint(...)`, `repartition(...)`, or `coalesce(...)`
    # was applied. ``partition_columns`` carries the columns passed to
    # `repartition` (empty list for unspecified).
    cached: bool = False
    persist_level: str | None = None
    checkpointed: bool = False
    partition_count: int | None = None
    partition_columns: list[str] = field(default_factory=list)
    broadcast_hint: bool = False
    # ----- dataframe_collapse_plan.md (display layer) ----------------------
    # ``is_anchor`` is False for intermediates (``__anon_N`` results of
    # withColumn/filter/etc. that the visitor produces while walking a
    # chain). A post-pass sets it True for anything the user wrote down:
    # named assignments, temp views, sources, sinks, and forks.
    # ``transform_chain`` is the ordered list of TransformStepIR entries
    # between this anchor and its upstream anchor — populated by the
    # collapse pass after parsing finishes.
    # ``input_anchor_ids`` lists the anchor :DataFrame ids feeding this
    # node (joins produce >1 entry).
    is_anchor: bool = False
    transform_chain: list = field(default_factory=list)
    input_anchor_ids: list[str] = field(default_factory=list)
    produced_by_function: str | None = None
    line_range: tuple[int, int] | None = None


@dataclass
class NotebookCellIR:
    """One notebook cell — v0.2 §2 notebook runtime semantics."""
    index: int
    language: str                  # "python" | "sql" | "scala" | ...
    source: str
    execution_count: int | None = None


@dataclass
class NotebookRunEdgeIR:
    """`%run` or `dbutils.notebook.run("path", ...)` — v0.2 §2.

    The visitor cannot always resolve ``target_path`` to a concrete file on
    disk (env-dependent paths, dynamic strings); when unresolved,
    ``target_script_id`` is None.
    """
    source_script_id: str
    target_path: str                       # raw string from the source
    target_resolved_path: str | None = None
    target_script_id: str | None = None
    kind: str = "magic_run"                # "magic_run" | "dbutils_notebook_run"
    source_cell_index: int | None = None
    line: int | None = None


@dataclass
class SparkScriptIR:
    id: str
    name: str
    file_path: str
    script_type: str = "pyspark"   # pyspark | sparksql | notebook
    dataframes: list[DataFrameIR] = field(default_factory=list)
    udfs: list[UDFIR] = field(default_factory=list)
    warnings: list[WarningIR] = field(default_factory=list)
    # v0.2 §1 — import edges originating from this script. The visitor
    # populates these with `to_script_id=None`; `project_parser` resolves them.
    imports: list["ImportEdgeIR"] = field(default_factory=list)
    # v0.2 §2 — notebook-only: the cells (and their execution order) plus the
    # `%run` / `dbutils.notebook.run` edges discovered while parsing.
    cells: list[NotebookCellIR] = field(default_factory=list)
    notebook_runs: list[NotebookRunEdgeIR] = field(default_factory=list)
    parsed_at: str | None = None


@dataclass
class TaskEdgeIR:
    """One task→task dependency inside an orchestration graph — v0.2 §7."""
    upstream: str
    downstream: str


@dataclass
class OrchestrationJobIR:
    """A scheduler-side job (Airflow DAG / Databricks workflow / shell wrapper).

    v0.2 §7 — bridges the orchestration layer with the Spark lineage graph by
    pointing each task at the script / notebook it executes.
    """
    job_id: str                          # dag_id, workflow name, etc.
    source: str                          # "airflow" | "databricks_workflow" | "spark_submit"
    file_path: str
    schedule: str | None = None
    tasks: list["OrchestrationTaskIR"] = field(default_factory=list)
    edges: list[TaskEdgeIR] = field(default_factory=list)
    warnings: list[WarningIR] = field(default_factory=list)


@dataclass
class OrchestrationTaskIR:
    """One task inside an OrchestrationJobIR — v0.2 §7."""
    task_id: str
    operator: str                        # "SparkSubmitOperator" | "notebook" | …
    target_script: str | None = None     # path/URL of the script the task runs
    parameters: dict[str, str] = field(default_factory=dict)
    line: int | None = None


@dataclass
class SchemaEvolutionIR:
    """One observed schema change between two consecutive Delta commits.

    v0.2 §5 — emitted by ``runtime/delta_log.py``. Bound to a ``:Table`` via
    ``table_fqn`` so downstream consumers can render the change history.
    """
    table_fqn: str | None = None
    version: int | None = None              # Delta commit version (filename N in N.json)
    kind: str = "add_column"                # add_column | drop_column | rename | type_change | nullability_change
    column: str | None = None
    previous_column: str | None = None       # for rename
    from_type: str | None = None
    to_type: str | None = None
    from_nullable: bool | None = None
    to_nullable: bool | None = None
    timestamp_ms: int | None = None


@dataclass
class RuntimeStageIR:
    """One stage extracted from a Spark event log — v0.2 §11."""
    stage_id: int
    parent_ids: list[int] = field(default_factory=list)
    name: str | None = None
    num_tasks: int | None = None
    completed_ms: int | None = None


@dataclass
class RuntimeJobIR:
    """One Spark job — v0.2 §11."""
    job_id: int
    stage_ids: list[int] = field(default_factory=list)
    sql_execution_id: int | None = None
    completed_ms: int | None = None


@dataclass
class SqlExecutionIR:
    """A ``SparkListenerSQLExecutionEnd`` payload — v0.2 §11."""
    execution_id: int
    description: str | None = None
    physical_plan: str | None = None
    analyzed_plan: str | None = None
    optimized_plan: str | None = None
    duration_ms: int | None = None
    completed_ms: int | None = None


@dataclass
class OptimizationDecisionIR:
    """Catalyst optimizer rule applied during a SQL execution — v0.2 §11."""
    execution_id: int
    rule: str                              # "ColumnPruning" | "PushDownPredicate" | …
    detail: str | None = None


@dataclass
class RuntimeIR:
    """Bundle of everything reconstructed from a Spark event log — v0.2 §11."""
    sql_executions: list[SqlExecutionIR] = field(default_factory=list)
    jobs: list[RuntimeJobIR] = field(default_factory=list)
    stages: list[RuntimeStageIR] = field(default_factory=list)
    optimizations: list[OptimizationDecisionIR] = field(default_factory=list)
    warnings: list[WarningIR] = field(default_factory=list)


@dataclass
class RuntimePlanIR:
    """Correlation result — one ``:DataFrame`` ↔ one runtime plan (v0.2 §11)."""
    static_node_id: str                    # DataFrameIR.id
    execution_id: int
    physical_plan: str | None = None
    runtime_dag_signature: str | None = None
    static_dag_signature: str | None = None


@dataclass
class SqlLineageIR:
    """Returned by ``sparksql.lineage.extract_lineage``."""
    target_tables: list[str] = field(default_factory=list)
    source_tables: list[str] = field(default_factory=list)
    derivations: list[DerivationIR] = field(default_factory=list)
    warnings: list[WarningIR] = field(default_factory=list)


# ---------------------------------------------------------------------------
# v0.2 multi-file (project-scoped) IR — §1 cross-file semantic resolution
# ---------------------------------------------------------------------------


@dataclass
class ImportEdgeIR:
    """One import statement: from_script → to_script.

    `kind` is "import" for `import X` and "from" for `from X import Y`.
    `symbol` is the imported name as bound in the importing module.
    `to_script_id` is None when the import is third-party (pyspark, os, etc.).
    """
    from_script_id: str
    symbol: str
    kind: str = "import"               # "import" | "from"
    module: str | None = None          # the module string after `from`
    to_script_id: str | None = None    # None → third-party / unresolved
    to_file_path: str | None = None    # None → third-party / unresolved
    line: int | None = None


@dataclass
class ProjectIR:
    """Multi-file lineage container.

    A `ProjectIR` aggregates the entry script plus every first-party module
    it transitively imports. `import_edges` is the import DAG. Cycles are
    permitted in the source code but never re-parsed (project_parser tracks
    a visited set keyed by absolute path).
    """
    entry_script_id: str
    project_root: str
    modules: list[SparkScriptIR] = field(default_factory=list)
    import_edges: list[ImportEdgeIR] = field(default_factory=list)
    warnings: list[WarningIR] = field(default_factory=list)
