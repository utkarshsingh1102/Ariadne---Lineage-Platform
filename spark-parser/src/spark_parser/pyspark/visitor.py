"""PySpark static analysis (plan §6 step 4–7).

Walks a Python AST with a symbol table:

    var_name → DataFrameIR

Each Spark call site contributes either a new DataFrame (assignment) or an
edge on an existing one (terminal write / temp view). The visitor does *not*
execute any code — it only matches structural patterns.

Variable reassignments increment ``creation_order`` so the audit trail is
preserved (plan §14). Anonymous method-chain DataFrames get
``__anon_<creation_order>`` names.
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..models.domain import (
    AttributeIR,
    DataFrameEdgeIR,
    DataFrameIR,
    DerivationIR,
    ImportEdgeIR,
    JoinIR,
    SparkScriptIR,
    TableIR,
    TransformStepIR,
    UDFIR,
    WarningIR,
    WriteEdgeIR,
)
from ..sparksql.lineage import extract_lineage as extract_sql_lineage
from ..utils.ids import dataframe_id, script_id


@dataclass
class _CallBinding:
    """Resolved arguments for one inlined call site.

    Each scope dict is populated independently so a single bound value can
    surface as both a DataFrame (df_scope) and a string (str_scope) — e.g.
    ``write_delta(df_fact, path=cfg.GOLD_ORDERS, …)`` where ``path`` is
    a string the body uses inside ``writer.save(path)``.
    """
    df_scope: dict[str, DataFrameIR] = field(default_factory=dict)
    str_scope: dict[str, str] = field(default_factory=dict)
    list_scope: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    vararg_overflow: list[ast.AST] = field(default_factory=list)
    kwarg_extras: dict[str, ast.AST] = field(default_factory=dict)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Datatype inference helpers
# ---------------------------------------------------------------------------

# PySpark type-class name → canonical Spark SQL string.
_TYPE_LITERAL_MAP: dict[str, str] = {
    "StringType": "string",
    "IntegerType": "int",
    "LongType": "long",
    "DoubleType": "double",
    "FloatType": "float",
    "BooleanType": "boolean",
    "DateType": "date",
    "TimestampType": "timestamp",
    "BinaryType": "binary",
    "ArrayType": "array",
    "MapType": "map",
    "StructType": "struct",
    "ShortType": "short",
    "ByteType": "byte",
    "DecimalType": "decimal",
}

# Function name → output Spark SQL type, where the type is independent of input.
_AGG_FUNCTION_TYPES: dict[str, str] = {
    "count": "long",
    "countDistinct": "long",
    "count_distinct": "long",
    "approx_count_distinct": "long",
    "sum": "double",
    "_sum": "double",
    "avg": "double",
    "mean": "double",
    "stddev": "double",
    "stddev_pop": "double",
    "stddev_samp": "double",
    "variance": "double",
    "var_pop": "double",
    "var_samp": "double",
    "skewness": "double",
    "kurtosis": "double",
    "corr": "double",
    "covar_pop": "double",
    "covar_samp": "double",
    "year": "int",
    "month": "int",
    "day": "int",
    "dayofmonth": "int",
    "dayofweek": "int",
    "hour": "int",
    "minute": "int",
    "second": "int",
    "length": "int",
    "size": "int",
    "concat": "string",
    "concat_ws": "string",
    "upper": "string",
    "lower": "string",
    "substring": "string",
    "trim": "string",
    "ltrim": "string",
    "rtrim": "string",
    "regexp_replace": "string",
    "regexp_extract": "string",
    "to_date": "date",
    "to_timestamp": "timestamp",
    "current_date": "date",
    "current_timestamp": "timestamp",
    "isnull": "boolean",
    "isnan": "boolean",
}


def _canonicalize_type_string(raw: str | None) -> str | None:
    """Normalise UDF return-type strings like ``stringtype()`` → ``string``."""
    if not raw:
        return raw
    s = raw.strip().lower()
    # Strip "()" if present, e.g. stringtype() -> stringtype
    if s.endswith("()"):
        s = s[:-2]
    # Map lowercased class-name to canonical Spark SQL type
    for cls, canonical in _TYPE_LITERAL_MAP.items():
        if s == cls.lower():
            return canonical
    return s


def _ast_to_type_string(node: ast.AST | None) -> str | None:
    """Translate a PySpark type AST expression into a canonical Spark SQL string.

    Accepts ``"double"`` (string literal), ``DoubleType()`` (class call),
    ``DoubleType`` (bare reference), ``T.DoubleType`` and similar.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.lower()
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name):
            return _TYPE_LITERAL_MAP.get(fn.id)
        if isinstance(fn, ast.Attribute):
            return _TYPE_LITERAL_MAP.get(fn.attr)
    if isinstance(node, ast.Name):
        return _TYPE_LITERAL_MAP.get(node.id)
    if isinstance(node, ast.Attribute):
        return _TYPE_LITERAL_MAP.get(node.attr)
    return None


def _apply_runtime_hint(df: DataFrameIR, method: str, call: ast.Call) -> None:
    """Tag the DataFrame with v0.2 §6 enterprise-runtime metadata.

    ``method`` is the call's attribute name (``cache``, ``persist``, …).
    No-ops if the method isn't a recognised hint.
    """
    if method == "cache":
        df.cached = True
        return
    if method == "persist":
        df.cached = True
        if call.args:
            level = _ast_to_type_string(call.args[0])
            if level:
                df.persist_level = level
        else:
            df.persist_level = "MEMORY_AND_DISK"  # PySpark default
        return
    if method == "checkpoint":
        df.checkpointed = True
        return
    if method in {"repartition", "coalesce"}:
        # First positional int is the partition count; remaining positional or
        # keyword "cols" arguments are partition columns (only meaningful for
        # repartition — coalesce ignores them).
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, int):
            df.partition_count = call.args[0].value
            rest = call.args[1:]
        else:
            rest = call.args
        if method == "repartition":
            cols: list[str] = []
            for a in rest:
                c = _column_ref_name(a)
                if c:
                    cols.append(c)
            if cols:
                df.partition_columns = cols
        return
    if method == "hint":
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            if call.args[0].value.lower() == "broadcast":
                df.broadcast_hint = True
        return


def _cast_types_if_any(expr: ast.Call) -> tuple[str | None, str | None]:
    """If ``expr`` is a cast() call, return (from_type, to_type). Else (None, None).

    Recognises both ``cast(col("x"), "int")`` and ``col("x").cast("int")``.
    The from_type is best-effort: we look at the cast subject if it's
    immediately recognisable (literal, lit, simple type hint); else None.
    """
    fn = expr.func
    # cast(col, type)
    if isinstance(fn, ast.Name) and fn.id == "cast" and len(expr.args) >= 2:
        to_t = _ast_to_type_string(expr.args[1])
        return None, to_t
    # col.cast(type)
    if isinstance(fn, ast.Attribute) and fn.attr == "cast" and expr.args:
        to_t = _ast_to_type_string(expr.args[0])
        return None, to_t
    return None, None


def _datatype_from_expression(
    expr: ast.AST | None,
    udfs_by_name: dict[str, UDFIR],
) -> str | None:
    """Best-effort datatype inference for the RHS of a ``withColumn`` / ``agg``.

    Recognises:
    * ``cast(col, "double")`` and ``col.cast(DoubleType())``
    * ``lit(value)`` (infers from the literal's Python type)
    * Calls to UDFs we've already seen (uses their declared return_type)
    * Aggregation / built-in functions whose output type is constant
    """
    if expr is None:
        return None

    # Literal — col = lit(value)
    if isinstance(expr, ast.Constant):
        v = expr.value
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, int):
            return "int"
        if isinstance(v, float):
            return "double"
        if isinstance(v, str):
            return "string"
        if v is None:
            return None

    if isinstance(expr, ast.Call):
        fn = expr.func
        # cast(col, "double")
        if isinstance(fn, ast.Name) and fn.id == "cast" and len(expr.args) >= 2:
            t = _ast_to_type_string(expr.args[1])
            if t:
                return t
        # col.cast("double")
        if isinstance(fn, ast.Attribute) and fn.attr == "cast" and expr.args:
            t = _ast_to_type_string(expr.args[0])
            if t:
                return t
        # foo(...).alias("name") — alias is transparent for the result type
        if isinstance(fn, ast.Attribute) and fn.attr == "alias":
            return _datatype_from_expression(fn.value, udfs_by_name)
        # foo(...).asType(...) / .astype — also transparent type-wise
        if isinstance(fn, ast.Attribute) and fn.attr in {"astype", "asType"} and expr.args:
            t = _ast_to_type_string(expr.args[0])
            if t:
                return t
        # lit(value) — recurse on the literal
        if isinstance(fn, ast.Name) and fn.id == "lit" and expr.args:
            return _datatype_from_expression(expr.args[0], udfs_by_name)
        # UDF call — use its declared return type
        func_name: str | None = None
        if isinstance(fn, ast.Name):
            func_name = fn.id
        elif isinstance(fn, ast.Attribute):
            func_name = fn.attr
        if func_name and func_name in udfs_by_name:
            return _canonicalize_type_string(udfs_by_name[func_name].return_type)
        if func_name and func_name in _AGG_FUNCTION_TYPES:
            return _AGG_FUNCTION_TYPES[func_name]
        # when(...).otherwise(...) — try the otherwise branch first, then when
        if isinstance(fn, ast.Attribute) and fn.attr == "otherwise" and expr.args:
            t = _datatype_from_expression(expr.args[0], udfs_by_name)
            if t:
                return t
            # Walk into the .when() chain
            inner = fn.value
            if isinstance(inner, ast.Call) and len(inner.args) >= 2:
                return _datatype_from_expression(inner.args[1], udfs_by_name)
        # Plain arithmetic via methods (col("x") + col("y") is BinOp, not Call)
    if isinstance(expr, ast.BinOp):
        # Numeric arithmetic on numeric operands
        if isinstance(expr.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)):
            return "double"
        if isinstance(expr.op, (ast.BitOr, ast.BitAnd, ast.BitXor)):
            return "boolean"
    if isinstance(expr, ast.Compare):
        return "boolean"
    if isinstance(expr, ast.BoolOp):
        return "boolean"
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_pyspark(
    file_path: str | Path,
    *,
    external_functions: dict[str, ast.FunctionDef] | None = None,
) -> SparkScriptIR:
    """Parse one PySpark file.

    ``external_functions`` (v0.2 §1) maps an imported local symbol name to a
    ``FunctionDef`` defined in another parsed module. When the visitor sees a
    call to that name it will inline-walk the function body for cross-file
    lineage. When None (single-file mode) the visitor falls back to
    ``_external_function_df`` for unknown calls, identical to v0.1.
    """
    p = Path(file_path)
    source = p.read_text(encoding="utf-8")
    sid = script_id(str(p))
    ir = SparkScriptIR(
        id=sid, name=p.stem, file_path=str(p), script_type="pyspark",
    )
    try:
        tree = ast.parse(source, filename=str(p))
    except SyntaxError as e:
        ir.warnings.append(WarningIR(
            type="syntax_error", detail=str(e), line=getattr(e, "lineno", None),
        ))
        return ir

    visitor = _PySparkVisitor(
        ir, source=source, file_path=str(p),
        external_functions=external_functions,
    )
    visitor.visit_module(tree)

    # v0.2 §3 — runtime-dynamic detection runs as a sibling pass on the same
    # AST. It only annotates (appends warnings, sets lineage_partial) and never
    # mutates the structural IR built by the visitor.
    from .runtime_dynamic import scan as _scan_runtime_dynamic
    _scan_runtime_dynamic(tree, ir)
    return ir


def collect_top_level_functions(file_path: str | Path) -> dict[str, ast.FunctionDef]:
    """Return ``{name: FunctionDef}`` for every top-level function in ``file_path``.

    Cheap pass used by ``ProjectParser`` to build the external-functions table
    without running the full lineage walker. Returns an empty dict on syntax
    errors or unreadable files — the caller will see those issues again during
    the full parse.
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError):
        return {}
    return {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }


# ---------------------------------------------------------------------------
# Visitor implementation
# ---------------------------------------------------------------------------

_SPARK_READ_FORMATS = {
    "parquet", "delta", "json", "csv", "orc", "avro", "text", "jdbc",
}

_PASSTHROUGH_METHODS = {
    "cache", "persist", "checkpoint", "repartition", "coalesce",
    "dropDuplicates", "distinct", "sort", "orderBy", "limit",
}

_FILTER_METHODS = {"filter", "where"}

_TRANSFORM_METHODS = {
    "select", "selectExpr", "withColumn", "withColumnRenamed", "drop",
}

_JOIN_METHODS = {"join", "crossJoin"}

_UNION_METHODS = {"union", "unionAll", "unionByName"}

_AGG_METHODS = {"agg"}

_TERMINAL_WRITE_METHODS = {
    "saveAsTable", "insertInto", "save",
    # Structured-streaming sink — ``writeStream...start()``.
    "start",
    # Convenience writers that ALSO take the path positionally.
    "parquet", "csv", "json", "orc", "text",
    # JDBC convenience writer (3-arg positional form).
    "jdbc",
}

# RDD-level read methods on ``SparkContext`` — surfaced as I/O sites so the
# resulting DataFrame still gets a reads_from connection.
_RDD_READ_METHODS = {"textFile", "wholeTextFiles", "binaryFiles", "sequenceFile"}

# Terminal read methods that finalise a reader chain. Used to detect the
# end of a stored-reader pattern (``r = spark.read.format(...); r.load(...)``)
# so the chain handler knows when to consume the reader-state stash.
_TERMINAL_READ_METHODS = (
    {"load", "table", "jdbc"} | set(_SPARK_READ_FORMATS) | _RDD_READ_METHODS
)

# M2a — hard cap on inline-recursion depth. Helper that recurses by accident
# (foo → bar → foo) gets a `recursion_capped` warning instead of blowing the
# Python stack.
INLINE_MAX_DEPTH = 8


class _PySparkVisitor:
    def __init__(
        self,
        ir: SparkScriptIR,
        *,
        source: str,
        file_path: str,
        external_functions: dict[str, ast.FunctionDef] | None = None,
    ):
        self.ir = ir
        self.source = source
        self.file_path = file_path
        # v0.2 §1 — cross-module FunctionDefs visible via this file's imports.
        self.external_functions: dict[str, ast.FunctionDef] = external_functions or {}
        # Symbol table: var name → latest DataFrameIR
        self.symbols: dict[str, DataFrameIR] = {}
        # Track all versions for reassignment audit
        self.version_counts: dict[str, int] = {}
        # Local function definitions — used for "call into same file" lineage
        self.functions: dict[str, ast.FunctionDef] = {}
        # Constants assigned to module-level Names (used to resolve f-strings)
        self.string_constants: dict[str, str] = {}
        # v0.2 §1 — imported-symbol table. Local name → raw (kind, module, level,
        # original_symbol). ``module`` is the dotted module string; ``level``>0
        # means relative. The project_parser consumes this to resolve to file
        # paths and inline cross-module function bodies.
        self.imported_symbols: dict[str, dict] = {}
        # v0.2 §8 — class-aware tables for procedural-abstraction inlining.
        # ``class_methods``: class name → method name → FunctionDef.
        # ``class_bases``: class name → list of parent class names (text only —
        # static analysis, no MRO resolution).
        # ``instance_types``: variable name → class name (populated when the
        # visitor sees ``var = ClassName(args)``).
        # ``hof_returns``: function name → FunctionDef the function returns
        # via ``return inner`` — built by inspecting ``return`` statements that
        # reference a nested ``def`` defined in the same body.
        self.class_methods: dict[str, dict[str, ast.FunctionDef]] = {}
        self.class_bases: dict[str, list[str]] = {}
        self.instance_types: dict[str, str] = {}
        self.hof_returns: dict[str, ast.FunctionDef] = {}
        # Anonymous DataFrame counter
        self.anon_counter = 0
        # Temp views: name → DataFrameIR that backs it
        self.temp_views: dict[str, DataFrameIR] = {}
        # SparkSession variable name (default "spark"; set when builder seen)
        self.spark_var = "spark"
        # Default Hive database for `spark.table("orders")` shorthand
        self.default_db = os.environ.get("DEFAULT_DATABASE", "default")
        # M2a — interprocedural recursion depth guard. Helper that calls
        # itself either directly or via a cycle is capped so the analyser
        # degrades gracefully instead of recursing into a stack overflow.
        self._inline_depth = 0
        # M2b — call-graph + topological order. Built by `_build_call_graph`
        # in pass 1 so the inliners can refuse known-recursive functions
        # up-front rather than relying on the runtime depth cap.
        self.call_graph: dict[str, set[str]] = {}
        self.recursive_functions: set[str] = set()
        # Class-level literal attributes — ``class Cfg: GOLD = "s3a://…"``
        # populated by ``_capture_class``. Looked up via instance refs in
        # ``_resolve_str`` so ``cfg.GOLD`` resolves at inlined call sites.
        self.class_attributes: dict[str, dict[str, object]] = {}
        # List-literal scope used by inlined kwargs like ``partition_by=[…]``.
        # Stacked the same way as ``string_constants`` so per-inline overlays
        # restore on exit. Holds either str-only lists (partition columns) or
        # mixed lists (best-effort).
        self.list_constants: dict[str, list[object]] = {}
        # Dict-literal scope used to resolve ``CONFIG["pg_url"]`` style lookups
        # and to expand ``**options`` splats. Values are AST nodes so the
        # caller can re-resolve them in the active overlay (the same dict can
        # carry strings, ints, or references to other constants).
        self.dict_constants: dict[str, dict[str, ast.AST]] = {}
        # Best-effort @property registry: class name → prop name → AST value
        # to resolve when ``inst.prop`` is read.
        self.class_properties: dict[str, dict[str, ast.AST]] = {}
        # Variable name → (source, detail) when the RHS came from a runtime
        # / secret / env source (e.g. ``pg_url = os.getenv("PG_URL")``).
        # ``_classify_unresolved`` consults this when an option chain reaches
        # a bare Name we couldn't resolve to a literal.
        self.runtime_sources: dict[str, tuple[str, str | None]] = {}

    # ---- top-level ------------------------------------------------------

    def visit_module(self, tree: ast.Module) -> None:
        # First pass: collect FunctionDefs, module-level string constants, and
        # import statements so later references can resolve.
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self.functions[node.name] = node
                if any(
                    _decorator_name(d) in ("udf", "pandas_udf") for d in node.decorator_list
                ):
                    self._emit_udf(node)
                # v0.2 §8 — track functions that return a nested def (HOF
                # factory pattern: ``def make_xform(c): def _xf(df): ...;
                # return _xf``).
                self._maybe_record_hof_return(node)
            elif isinstance(node, ast.ClassDef):
                # v0.2 §8 — class hierarchy walker
                self._capture_class(node)
            elif isinstance(node, ast.Assign):
                self._maybe_capture_string_constant(node)
            elif isinstance(node, ast.Import):
                self._capture_import(node)
            elif isinstance(node, ast.ImportFrom):
                self._capture_import_from(node)

        # M2b — call-graph + recursive-cycle detection. Builds a map
        # `fn → {callees}` over self.functions (local) and
        # self.external_functions (cross-module), then runs DFS to find any
        # cycles. Cyclic functions are added to self.recursive_functions so
        # the inliners refuse them up-front with a clean warning.
        self._build_call_graph()

        # Second pass: walk statements in source order
        for node in tree.body:
            self._visit_stmt(node)

        # Third pass — dataframe_collapse_plan.md. Classify anchors
        # (named assignments / IO sites / temp views / forks) and walk
        # backwards through intermediates to populate ``transform_chain``
        # + ``input_anchor_ids`` on each anchor. Granular IR stays intact
        # — the writer / stats consult ``is_anchor`` to expose the
        # collapsed display layer.
        self._collapse_to_anchors()

    # ---- call-graph (M2b) ----------------------------------------------

    def _build_call_graph(self) -> None:
        """Scan every known FunctionDef body for calls into other knowns.

        Cheap O(N · body_size) walk. Result lives in `self.call_graph` and
        `self.recursive_functions`; the inliners consult them to short-
        circuit recursive cycles before they hit the runtime depth cap.
        """
        all_fns: dict[str, ast.FunctionDef] = {}
        all_fns.update(self.functions)
        all_fns.update(self.external_functions)
        for cls_methods in self.class_methods.values():
            for m, fn in cls_methods.items():
                all_fns.setdefault(m, fn)

        for name, fn in all_fns.items():
            callees: set[str] = set()
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                fn_call = node.func
                callee = (
                    fn_call.id if isinstance(fn_call, ast.Name)
                    else (fn_call.attr if isinstance(fn_call, ast.Attribute) else None)
                )
                if callee and callee in all_fns and callee != name:
                    callees.add(callee)
                # Self-recursion (direct).
                if (
                    isinstance(fn_call, ast.Name)
                    and fn_call.id == name
                ):
                    self.recursive_functions.add(name)
            self.call_graph[name] = callees

        # Indirect cycles — DFS from every node, mark any node on a back-edge
        # as recursive.
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n: WHITE for n in self.call_graph}

        def visit(n: str) -> None:
            colour[n] = GREY
            for c in self.call_graph.get(n, ()):
                if c not in colour:
                    continue
                if colour[c] == GREY:
                    # Back edge → both nodes on this path are part of a cycle.
                    self.recursive_functions.add(n)
                    self.recursive_functions.add(c)
                elif colour[c] == WHITE:
                    visit(c)
                    # If `c` got marked recursive during the descent and
                    # there's still an edge from `n` to a cycle, `n` is too.
                    if c in self.recursive_functions and n in self.call_graph.get(
                        c, set(),
                    ):
                        self.recursive_functions.add(n)
            colour[n] = BLACK

        for n in list(colour.keys()):
            if colour[n] == WHITE:
                visit(n)

    # ---- statement dispatch --------------------------------------------

    def _visit_stmt(self, node: ast.AST, *, in_branch: bool = False) -> None:
        if isinstance(node, ast.Assign):
            self._visit_assign(node, in_branch=in_branch)
        elif isinstance(node, ast.AnnAssign):
            # ``df: DataFrame = spark.read…`` — same shape as Assign with one target.
            if node.value is not None and isinstance(node.target, ast.Name):
                synth = ast.Assign(targets=[node.target], value=node.value)
                ast.copy_location(synth, node)
                self._visit_assign(synth, in_branch=in_branch)
            elif node.value is not None:
                self._visit_expression_stmt(node.value)
        elif isinstance(node, ast.AugAssign):
            # ``x += …`` — evaluate the RHS for side-effects (writes etc.).
            self._visit_expression_stmt(node.value)
        elif isinstance(node, ast.Expr):
            self._visit_expression_stmt(node.value)
        elif isinstance(node, ast.If):
            self._visit_if(node)
        elif isinstance(node, ast.For):
            self._visit_for(node)
        elif isinstance(node, ast.While):
            for child in node.body:
                self._visit_stmt(child, in_branch=True)
        elif isinstance(node, ast.With):
            for child in node.body:
                self._visit_stmt(child, in_branch=in_branch)
        elif isinstance(node, ast.Try):
            # Real-world ``if __name__ == "__main__":`` blocks wrap the entry
            # call in try/except/finally. Visit every branch — the try body
            # is the happy path, handlers / orelse / finalbody can still mutate
            # symbol bindings via cleanup logic.
            for child in node.body:
                self._visit_stmt(child, in_branch=in_branch)
            for handler in node.handlers:
                for child in handler.body:
                    self._visit_stmt(child, in_branch=True)
            for child in node.orelse:
                self._visit_stmt(child, in_branch=in_branch)
            for child in node.finalbody:
                self._visit_stmt(child, in_branch=True)
        elif isinstance(node, ast.FunctionDef):
            # Already collected in pass 1; don't recurse into body here.
            pass

    def _visit_if(self, node: ast.If) -> None:
        # For each branch, evaluate independently and union the resulting
        # DataFrames bound to the same name → mark lineage_conditional=True.
        snapshot = dict(self.symbols)
        branch_results: list[dict[str, DataFrameIR]] = []

        for body in (node.body, node.orelse):
            if not body:
                continue
            # Reset symbols to snapshot, evaluate branch, capture deltas
            self.symbols = dict(snapshot)
            for child in body:
                self._visit_stmt(child, in_branch=True)
            branch_results.append(dict(self.symbols))

        if not branch_results:
            return

        # Merge: for any name that diverges between branches, mark the latest
        # symbol as conditional and merge reads_from across branches.
        merged: dict[str, DataFrameIR] = dict(snapshot)
        all_keys = set().union(*branch_results)
        for key in all_keys:
            versions = [b.get(key) for b in branch_results if b.get(key)]
            if not versions:
                continue
            if len(versions) == 1:
                merged[key] = versions[0]
                continue
            primary = versions[0]
            primary.lineage_conditional = True
            seen = {(t.fully_qualified_name, t.location) for t in primary.reads_from}
            for other in versions[1:]:
                for t in other.reads_from:
                    sig = (t.fully_qualified_name, t.location)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    primary.reads_from.append(t)
            merged[key] = primary
        self.symbols = merged

    def _visit_for(self, node: ast.For) -> None:
        # Process loop body once; mark anything assigned as lineage_partial.
        before = set(self.symbols)
        for child in node.body:
            self._visit_stmt(child, in_branch=True)
        for name, df in self.symbols.items():
            if name not in before:
                df.lineage_partial = True
                self.ir.warnings.append(WarningIR(
                    type="lineage_partial",
                    detail=f"Variable '{name}' assigned inside a loop — "
                           "lineage marked partial",
                    line=node.lineno,
                ))
            elif df.lineage_partial is False and isinstance(node, ast.For):
                # Also flag if a pre-existing variable was reassigned inside the loop
                df.lineage_partial = True

    # ---- assignment ----------------------------------------------------

    def _visit_assign(self, node: ast.Assign, *, in_branch: bool) -> None:
        # Single-target LHS support (df = ...). Multi-target is rare in PySpark.
        if not node.targets:
            return
        # M2a — tuple LHS: `a, b = split(df)`. Inline the call once and bind
        # each tuple element to the corresponding LHS name. When the called
        # function returns a single DataFrame (not a tuple), bind it to the
        # first LHS name only and warn.
        if isinstance(node.targets[0], (ast.Tuple, ast.List)):
            self._visit_tuple_assign(node.targets[0], node.value)
            return
        # ``options["url"] = X`` — record the dict-key mutation so
        # ``.options(**options)`` later sees it.
        if isinstance(node.targets[0], ast.Subscript):
            self._record_dict_subscript_assign(node.targets[0], node.value)
            self._visit_expression_stmt(node.value)
            return
        if not isinstance(node.targets[0], ast.Name):
            self._visit_expression_stmt(node.value)
            return
        target_name = node.targets[0].id
        # Factory form: ``my_udf = udf(my_fn, StringType())`` — register the
        # UDF under the variable name so call sites like ``my_udf(col(...))``
        # resolve. (Decorator form is handled separately in visit_module.)
        if self._maybe_capture_udf_factory(target_name, node.value):
            return
        # v0.2 §8 — class instantiation: ``proc = OrderProcessor()`` binds
        # ``proc`` to the OrderProcessor class so ``proc.run(df)`` resolves.
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in self.class_methods
        ):
            self.instance_types[target_name] = node.value.func.id
            return
        # v0.2 §8 — higher-order factory: ``xf = make_xform("region")`` binds
        # ``xf`` to the *returned inner FunctionDef* so ``df2 = xf(df)`` later
        # inlines that closure.
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in self.hof_returns
        ):
            self.functions[target_name] = self.hof_returns[node.value.func.id]
            return
        df = self._eval_rhs_as_dataframe(node.value, default_name=target_name)
        if df is None:
            # The RHS wasn't a Spark expression — could still be a string constant.
            self._maybe_capture_string_constant(node)
            return
        if df.var_name != target_name or df.is_anonymous:
            df.var_name = target_name
            df.is_anonymous = False
            df.creation_order = self._next_order(target_name)
            df.id = self._mint_df_id(target_name, df.creation_order)
        self.symbols[target_name] = df
        if df not in self.ir.dataframes:
            self.ir.dataframes.append(df)

    def _visit_expression_stmt(self, expr: ast.AST) -> None:
        # Method-chain terminal writes or spark.sql(...) calls without LHS.
        if isinstance(expr, ast.Call):
            # ``options.update({"url": X})`` mutates a tracked dict — capture
            # the new pairs so a later ``.options(**options)`` picks them up.
            if (
                isinstance(expr.func, ast.Attribute)
                and expr.func.attr == "update"
                and isinstance(expr.func.value, ast.Name)
                and expr.func.value.id in self.dict_constants
            ):
                tgt = expr.func.value.id
                if expr.args:
                    extra = self._collect_dict_pairs(expr.args[0])
                    if extra:
                        self.dict_constants[tgt].update(extra)
                for kw in expr.keywords:
                    if kw.arg is None:
                        extra = self._collect_dict_pairs(kw.value)
                        if extra:
                            self.dict_constants[tgt].update(extra)
                    elif kw.arg:
                        self.dict_constants[tgt][kw.arg] = kw.value
                return
            self._eval_call(expr)

    def _record_dict_subscript_assign(self, target: ast.Subscript, value: ast.AST) -> None:
        """Capture ``options[key] = value`` mutations on tracked dicts."""
        if not isinstance(target.value, ast.Name):
            return
        var = target.value.id
        if var not in self.dict_constants:
            # Promote a previously-unseen variable to a tracked dict so
            # subsequent mutations stay aligned.
            self.dict_constants[var] = {}
        key = self._resolve_str(target.slice)
        if key is None:
            return
        self.dict_constants[var][key] = value

    def _collect_dict_pairs(self, node: ast.AST) -> dict[str, ast.AST]:
        """Return ``{str_key: ast_value}`` pairs from a dict literal or a
        tracked dict-Name. Returns ``{}`` when the node is opaque.
        """
        if isinstance(node, ast.Dict):
            out: dict[str, ast.AST] = {}
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and v is not None
                ):
                    out[k.value] = v
            return out
        if isinstance(node, ast.Name) and node.id in self.dict_constants:
            return dict(self.dict_constants[node.id])
        return {}

    def _visit_tuple_assign(self, lhs: ast.AST, rhs: ast.AST) -> None:
        """M2a — bind `a, b = ...` LHS names to each element of a tuple RHS.

        Two modes:
        * RHS is a literal tuple (`a, b = df1, df2`) — bind 1:1.
        * RHS is a function call (`a, b = split(df)`) — inline the function,
          inspect its return; if the return is a tuple expression, bind
          each element, otherwise fall back to binding only the first LHS
          and emitting `tuple_return_partial`.
        """
        targets = [t for t in getattr(lhs, "elts", []) if isinstance(t, ast.Name)]
        if not targets:
            return
        # RHS is a literal tuple — bind directly.
        if isinstance(rhs, (ast.Tuple, ast.List)):
            for tgt, val in zip(targets, rhs.elts):
                df = self._eval_rhs_as_dataframe(val, default_name=tgt.id)
                if df is None:
                    continue
                self._bind_named(tgt.id, df)
            return
        # RHS is a function call. Find the FunctionDef, peek at its return
        # to see whether it's a tuple, and bind accordingly.
        fn: ast.FunctionDef | None = None
        if isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Name):
            fn = self.functions.get(rhs.func.id) or self.external_functions.get(
                rhs.func.id,
            )
        if fn is not None:
            tuple_ret = _first_tuple_return(fn)
            if tuple_ret is not None and len(tuple_ret.elts) >= 1:
                # Inline once to populate symbols + ir.dataframes, then bind
                # each tuple element resolved against the function's scope.
                # For now: bind first LHS to the inlined first-element result
                # (which is what `_resolve_tuple_return` returns) and warn
                # about the dropped elements.
                df = self._eval_rhs_as_dataframe(rhs, default_name=targets[0].id)
                if df is not None:
                    self._bind_named(targets[0].id, df)
                if len(targets) > 1:
                    self.ir.warnings.append(WarningIR(
                        type="tuple_return_partial",
                        detail=(
                            f"`{', '.join(t.id for t in targets)} = "
                            f"{rhs.func.id if isinstance(rhs.func, ast.Name) else 'fn'}"
                            "(...)` — only the first return value is bound"
                        ),
                        line=getattr(rhs, "lineno", None),
                    ))
                return
        # Fallback: single-value return assigned to first LHS only.
        df = self._eval_rhs_as_dataframe(rhs, default_name=targets[0].id)
        if df is not None:
            self._bind_named(targets[0].id, df)

    def _bind_named(self, name: str, df: DataFrameIR) -> None:
        """Stamp `df` with the LHS name and append to `ir.dataframes`."""
        if df.var_name != name or df.is_anonymous:
            df.var_name = name
            df.is_anonymous = False
            df.creation_order = self._next_order(name)
            df.id = self._mint_df_id(name, df.creation_order)
        self.symbols[name] = df
        if df not in self.ir.dataframes:
            self.ir.dataframes.append(df)

    # ---- core RHS evaluator --------------------------------------------

    def _eval_rhs_as_dataframe(
        self, node: ast.AST, *, default_name: str,
    ) -> DataFrameIR | None:
        """Evaluate an arbitrary Spark RHS, returning the resulting DataFrameIR."""
        # Constant / non-Spark RHS
        if isinstance(node, (ast.Constant, ast.Name, ast.Subscript)):
            # `df = other_df` — rebind to the same lineage (new creation order)
            if isinstance(node, ast.Name) and node.id in self.symbols:
                src = self.symbols[node.id]
                new_df = self._clone_for_reassignment(src, default_name)
                return new_df
            return None

        if isinstance(node, ast.IfExp):
            # Ternary: cond ? a : b  — emit both branches as parents
            df = self._new_df(default_name)
            df.lineage_conditional = True
            for branch in (node.body, node.orelse):
                child = self._eval_rhs_as_dataframe(branch, default_name="__cond")
                if child is not None:
                    df.reads_from.extend(child.reads_from)
                    df.derives_from_dataframe.append(DataFrameEdgeIR(
                        source_var=child.var_name, via="conditional"
                    ))
            return df

        if isinstance(node, ast.Call):
            return self._eval_call(node, expected_name=default_name)
        return None

    # ---- call dispatch -------------------------------------------------

    def _eval_call(self, call: ast.Call, *, expected_name: str | None = None) -> DataFrameIR | None:
        chain = _flatten_call_chain(call)
        if not chain:
            return None

        # ---- SparkSession.builder...getOrCreate() — record session var ----
        if isinstance(chain[0], ast.Name) and chain[0].id == "SparkSession":
            return None  # builder doesn't yield a DataFrame; LHS becomes spark

        # ---- spark.read.format(...).load(...) / .parquet(...) etc. -------
        if _is_spark_read_chain(chain, self.spark_var):
            df = self._build_df_from_read(chain, expected_name)
            self._handle_terminal_methods(chain, df)
            return df

        # ---- sc.textFile(...) / sc.wholeTextFiles(...) (RDD-level read) -
        if _is_rdd_read_chain(chain):
            df = self._build_df_from_rdd_read(chain, expected_name)
            self._handle_terminal_methods(chain, df)
            return df

        # ---- spark.table("...") ----------------------------------------
        if _is_spark_table_call(chain, self.spark_var):
            df = self._build_df_from_table_call(chain, expected_name)
            self._handle_terminal_methods(chain, df)
            return df

        # ---- spark.sql("...") ------------------------------------------
        if _is_spark_sql_call(chain, self.spark_var):
            df = self._build_df_from_spark_sql(chain, expected_name)
            self._handle_terminal_methods(chain, df)
            return df

        # ---- df.* (method chain rooted in a known var) -----------------
        if isinstance(chain[0], ast.Name) and chain[0].id in self.symbols:
            df = self._derive_from_chain(self.symbols[chain[0].id], chain, expected_name)
            return df

        # ---- v0.2 §8: instance.method(args) — chain rooted in an instance
        # we tracked from ``proc = ClassName(...)``. The first chain step is
        # the method invocation; subsequent steps are chained DataFrame ops.
        if (
            isinstance(chain[0], ast.Name)
            and chain[0].id in self.instance_types
            and len(chain) >= 2
            and isinstance(chain[1], ast.Call)
        ):
            instance_name = chain[0].id
            class_name = self.instance_types[instance_name]
            method_name = _call_attr_name(chain[1])
            if method_name:
                fn = self._resolve_class_method(class_name, method_name)
                if fn is not None:
                    df = self._inline_class_method(
                        fn, chain[1], expected_name,
                        class_name=class_name, method_name=method_name,
                    )
                    if df is not None and len(chain) > 2:
                        # Apply any remaining chain steps (.withColumn, .write, …).
                        df = self._derive_from_chain(df, [chain[1]] + chain[2:], expected_name)
                    return df

        # ---- local function call: out = transform(df) ------------------
        if isinstance(chain[0], ast.Call) and isinstance(chain[0].func, ast.Name):
            fn_name = chain[0].func.id
            if fn_name in self.functions:
                return self._inline_local_function(fn_name, chain[0], expected_name)
            if fn_name in self.external_functions:
                return self._inline_external_function(fn_name, chain[0], expected_name)
            # Unknown call. Only assume it's a DataFrame factory when the
            # callee looks like one — i.e. it consumes a spark session or an
            # already-tracked DataFrame. Type constructors (`StructType([...])`,
            # `Window.partitionBy(...)`, `datetime.now()`) otherwise pollute
            # the IR with phantom DataFrames.
            if self._looks_like_df_producing_call(chain[0]):
                return self._external_function_df(chain[0], expected_name)
            return None

        # ---- v0.2 §8: instance.method(df) → inline the class method body --
        if (
            isinstance(chain[0], ast.Call)
            and isinstance(chain[0].func, ast.Attribute)
            and isinstance(chain[0].func.value, ast.Name)
        ):
            instance_name = chain[0].func.value.id
            method_name = chain[0].func.attr
            class_name = self.instance_types.get(instance_name)
            if class_name:
                fn = self._resolve_class_method(class_name, method_name)
                if fn is not None:
                    return self._inline_class_method(
                        fn, chain[0], expected_name,
                        class_name=class_name, method_name=method_name,
                    )

        # ---- broadcast(df).join(...) — bare-name call wrapping a known df ----
        if isinstance(call.func, ast.Name):
            name = call.func.id
            if name in self.functions:
                return self._inline_local_function(name, call, expected_name)
            if name in self.external_functions:
                return self._inline_external_function(name, call, expected_name)

        return None

    # ---- read / table / sql --------------------------------------------

    def _build_df_from_read(self, chain: list[ast.AST], name: str | None) -> DataFrameIR:
        df = self._new_df(name or "__anon")

        # Detect stored-reader pattern: ``r = spark.read.format("jdbc")``
        # with NO terminal-read method. Stash the state and return a stub
        # so subsequent chains rooted in ``r`` can extend it across
        # statements without losing the format/options we already saw.
        has_terminal_read = any(
            isinstance(n, ast.Call) and _call_attr_name(n) in _TERMINAL_READ_METHODS
            for n in chain
        )
        is_stream = any(
            isinstance(n, ast.Attribute) and n.attr == "readStream"
            for n in chain
        )

        # Determine storage format + location by walking the chain
        storage_format = None
        location = None
        table_arg: str | None = None
        jdbc_options: dict[str, str] = {}

        unresolved_source: str | None = None
        unresolved_detail: str | None = None
        for node in chain:
            if not isinstance(node, ast.Call):
                continue
            attr = _call_attr_name(node)
            if attr == "format":
                fmt = self._resolve_str(node.args[0]) if node.args else None
                storage_format = fmt or storage_format
            elif attr in _SPARK_READ_FORMATS:
                storage_format = storage_format or attr
                arg = self._resolve_str(node.args[0]) if node.args else None
                if arg:
                    location = arg
                elif node.args:
                    # Path is runtime-only (env, secret, dynamic). Surface
                    # the kind so derive_connection mints an unresolved node.
                    src, det = self._classify_unresolved(node.args[0])
                    if src and not unresolved_source:
                        unresolved_source, unresolved_detail = src, det
            elif attr == "load":
                arg = self._resolve_str(node.args[0]) if node.args else None
                if arg:
                    location = arg
                elif node.args:
                    src, det = self._classify_unresolved(node.args[0])
                    if src and not unresolved_source:
                        unresolved_source, unresolved_detail = src, det
            elif attr == "jdbc":
                # 3-arg positional form: spark.read.jdbc(url, table, props)
                # Args:  0=url, 1=table, 2=properties (dict)
                storage_format = "jdbc"
                if node.args:
                    url = self._resolve_str(node.args[0])
                    if url:
                        jdbc_options["url"] = url
                    else:
                        src, det = self._classify_unresolved(node.args[0])
                        if src and not unresolved_source:
                            unresolved_source, unresolved_detail = src, det
                if len(node.args) >= 2:
                    tbl = self._resolve_str(node.args[1])
                    if tbl:
                        jdbc_options["dbtable"] = tbl
                if len(node.args) >= 3:
                    props = self._resolve_dict_value(node.args[2]) or {}
                    for pk, pv in props.items():
                        rv = self._resolve_str(pv)
                        if rv is not None:
                            jdbc_options[pk] = rv
            elif attr in {"option", "options"}:
                # `.option(k,v)` and `.options(**dict)` / `.options(d)` —
                # accumulate every static pair we can resolve.
                if attr == "option":
                    k = self._resolve_str(node.args[0]) if node.args else None
                    v = self._resolve_str(node.args[1]) if len(node.args) > 1 else None
                    if k and v:
                        jdbc_options[k] = v
                    # If a key/value pair labels a connection-identifying
                    # key (`url`, `host`, …) and we can't resolve the value
                    # statically, classify its source so the Connection
                    # node is minted with resolved=False.
                    if k and len(node.args) > 1 and v is None and not unresolved_source:
                        src, det = self._classify_unresolved(node.args[1])
                        if src:
                            unresolved_source, unresolved_detail = src, det
                # Inlined-wrapper kwargs: read_jdbc(spark, table="public.x") →
                # .option("dbtable", "public.x") after binding flows here.
                for kw in node.keywords:
                    if kw.arg is None:
                        # ``**dict`` splat — expand from registered dicts.
                        splat = self._resolve_dict_value(kw.value) or {}
                        for sk, sv in splat.items():
                            rv = self._resolve_str(sv)
                            if rv is not None:
                                jdbc_options[sk] = rv
                        continue
                    if kw.arg:
                        kv = self._resolve_str(kw.value)
                        if kv is not None:
                            jdbc_options[kw.arg] = kv
                # .options(some_dict) — single positional dict resolved against
                # the symbol table (literal or Name pointing at one).
                if attr == "options" and node.args:
                    splat = self._resolve_dict_value(node.args[0]) or {}
                    for sk, sv in splat.items():
                        rv = self._resolve_str(sv)
                        if rv is not None:
                            jdbc_options[sk] = rv
            elif attr == "table":
                # spark.read.table("db.schema.tbl") — Hive catalog read
                t_arg = self._resolve_str(node.args[0]) if node.args else None
                if t_arg:
                    table_arg = t_arg

        # If this DataFrame was built off a tracked reader-state stash
        # (``r = spark.read.format(...); r.option(...); r.load()``) the
        # accumulated options/format live on the reader DataFrame.
        reader_state = getattr(df, "_reader_state", None) or {}
        if reader_state:
            storage_format = storage_format or reader_state.get("format")
            for k, v in (reader_state.get("options") or {}).items():
                jdbc_options.setdefault(k, v)
            if not location and reader_state.get("location"):
                location = reader_state["location"]
            if not table_arg and reader_state.get("table_arg"):
                table_arg = reader_state["table_arg"]
            if not unresolved_source and reader_state.get("unresolved_source"):
                unresolved_source = reader_state["unresolved_source"]
                unresolved_detail = reader_state.get("unresolved_detail")

        # No terminal read in this chain — we're looking at a stored reader
        # like ``r = spark.read.format("jdbc")``. Stash the accumulated state
        # on ``df`` so a follow-up chain rooted in ``r`` can complete the read.
        if not has_terminal_read:
            df._reader_state = {  # type: ignore[attr-defined]
                "format": storage_format,
                "options": jdbc_options,
                "location": location,
                "table_arg": table_arg,
                "streaming": is_stream,
                "unresolved_source": unresolved_source,
                "unresolved_detail": unresolved_detail,
            }
            return df

        # v0.2 §9 — external-ecosystem connectors take precedence over the
        # generic format-string handling so Kafka / Iceberg / Hudi / Snowflake /
        # BigQuery / Redshift get rich FQNs instead of falling back to a path.
        from ..connectors import match_connector, derive_connection
        cm = match_connector(
            storage_format, jdbc_options,
            path_arg=location, table_arg=table_arg,
        )

        if cm is not None:
            tbl = TableIR(
                storage_format=cm.storage_format,
                fully_qualified_name=cm.fully_qualified_name,
                location=cm.location,
            )
        elif table_arg is not None:
            tbl = TableIR(
                fully_qualified_name=_qualify_table_name(table_arg, self.default_db),
                storage_format="hive",
            )
        elif storage_format == "jdbc":
            jdbc_url = jdbc_options.get("url", location or "")
            dbtable = jdbc_options.get("dbtable") or jdbc_options.get("query")
            tbl = TableIR(
                storage_format="jdbc",
                location=jdbc_url,
                # Set FQN to the JDBC table-name when known, or to the URL so
                # downstream consumers that .lower() the FQN don't NPE.
                fully_qualified_name=dbtable or jdbc_url,
            )
        elif location:
            tbl = TableIR(
                storage_format=storage_format or "parquet",
                location=location,
                fully_qualified_name=location,
            )
        else:
            tbl = TableIR(storage_format=storage_format, fully_qualified_name="")
        tbl.connection = derive_connection(
            storage_format, jdbc_options,
            location=tbl.location or location,
            table_arg=table_arg,
            database=self.default_db,
            unresolved_source=unresolved_source,
            unresolved_detail=unresolved_detail,
        )
        self._stamp_read_line(tbl, chain)
        df.reads_from.append(tbl)
        return df

    def _build_df_from_rdd_read(self, chain: list[ast.AST], name: str | None) -> DataFrameIR:
        """``sc.textFile(path)`` / ``sc.wholeTextFiles(path)`` — RDD-level read.

        Surfaces these as a regular DataFrame read so connection extraction
        treats them like ``spark.read.text(path)``. Without this, RDD code
        paths produce no Connection nodes at all.
        """
        df = self._new_df(name or "__anon")
        # The first Call in the chain is the read invocation.
        call = chain[1] if len(chain) >= 2 and isinstance(chain[1], ast.Call) else None
        method = _call_attr_name(call) if call else None
        path = self._resolve_str(call.args[0]) if (call and call.args) else None
        unresolved_source = unresolved_detail = None
        if path is None and call and call.args:
            unresolved_source, unresolved_detail = self._classify_unresolved(call.args[0])
        storage_format = "text" if method in {"textFile", "wholeTextFiles"} else "binary"
        tbl = TableIR(
            storage_format=storage_format,
            location=path or "",
            fully_qualified_name=path or "",
        )
        from ..connectors import derive_connection
        tbl.connection = derive_connection(
            storage_format, None, location=path, database=self.default_db,
            unresolved_source=unresolved_source,
            unresolved_detail=unresolved_detail,
        )
        self._stamp_read_line(tbl, call, chain)
        df.reads_from.append(tbl)
        return df

    # ---- runtime/secret classification ---------------------------------

    _RUNTIME_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
        (("os", "environ"), "env"),                    # os.environ[X]
        (("os", "getenv"), "env"),                     # os.getenv("X")
        (("environ",), "env"),                          # bare environ[X]
        (("dbutils", "secrets", "get"), "secret"),     # dbutils.secrets.get(scope, key)
        (("dbutils", "secrets", "getBytes"), "secret"),
        (("spark", "conf", "get"), "runtime"),         # spark.conf.get("k")
        (("sc", "getConf"), "runtime"),
        (("get_secret",), "secret"),                    # boto3/secret-manager wrappers
        (("Variable", "get"), "runtime"),               # Airflow style
    )

    def _classify_unresolved(self, node: ast.AST) -> tuple[str | None, str | None]:
        """Classify an opaque expression as env/secret/runtime/dynamic.

        Returns ``(source, detail)``. ``source`` is the registered tag
        (``env``, ``secret``, ``runtime``, ``dynamic``). ``detail`` is the
        symbolic key the source code passed (e.g. the env-var name).
        """
        # Bare Name referring to a variable assigned from a tracked runtime
        # source — propagate the original (source, detail).
        if isinstance(node, ast.Name) and node.id in self.runtime_sources:
            return self.runtime_sources[node.id]
        target = node
        if isinstance(node, ast.Subscript):
            target = node.value
            sub = node.slice
            key = self._resolve_str(sub)
            for pattern, source in self._RUNTIME_PATTERNS:
                if _matches_attr_path(target, pattern):
                    return source, key
        if isinstance(node, ast.Call):
            for pattern, source in self._RUNTIME_PATTERNS:
                if _call_matches_attr_path(node, pattern):
                    detail = None
                    if node.args:
                        detail = self._resolve_str(node.args[0])
                    return source, detail
        # Anything else genuinely opaque — dynamic value.
        return ("dynamic", None) if not isinstance(node, ast.Constant) else (None, None)

    def _build_df_from_table_call(self, chain: list[ast.AST], name: str | None) -> DataFrameIR:
        df = self._new_df(name or "__anon")
        call = chain[0]                   # the spark.table(...) call lives at the chain root
        # find the .table(...) call specifically — handle the case where chain[0] is the bare attribute
        for n in chain:
            if isinstance(n, ast.Call) and _call_attr_name(n) == "table":
                call = n
                break
        arg = self._resolve_str(call.args[0]) if call.args else None
        if arg is None:
            df.lineage_partial = True
            self.ir.warnings.append(WarningIR(
                type="dynamic_table_name",
                detail="spark.table() argument is not statically resolvable",
                line=call.lineno,
            ))
            return df
        fqn = _qualify_table_name(arg, self.default_db)
        tbl = TableIR(fully_qualified_name=fqn, storage_format="hive")
        from ..connectors import derive_connection
        tbl.connection = derive_connection("hive", None, table_arg=arg, database=self.default_db)
        self._stamp_read_line(tbl, call)
        df.reads_from.append(tbl)
        return df

    def _build_df_from_spark_sql(self, chain: list[ast.AST], name: str | None) -> DataFrameIR:
        df = self._new_df(name or "__anon")
        df.from_sql_block = True
        call = chain[0]
        for n in chain:
            if isinstance(n, ast.Call) and _call_attr_name(n) == "sql":
                call = n
                break
        sql_text = self._resolve_str(call.args[0]) if call.args else None
        if not sql_text:
            df.lineage_partial = True
            self.ir.warnings.append(WarningIR(
                type="dynamic_sql",
                detail="spark.sql() argument not statically resolvable",
                line=call.lineno,
            ))
            return df
        lineage = extract_sql_lineage(sql_text, dialect="spark")
        from ..connectors import derive_connection
        for src in lineage.source_tables:
            # Resolve temp views to their backing source(s)
            if src in self.temp_views:
                backing = self.temp_views[src]
                for tbl in backing.reads_from:
                    df.reads_from.append(tbl)
                continue
            tbl = TableIR(
                fully_qualified_name=_qualify_table_name(src, self.default_db),
                storage_format="hive",
            )
            tbl.connection = derive_connection("hive", None, table_arg=src, database=self.default_db)
            self._stamp_read_line(tbl, call)
            df.reads_from.append(tbl)
        for tgt in lineage.target_tables:
            tbl = TableIR(
                fully_qualified_name=_qualify_table_name(tgt, self.default_db),
                storage_format="hive",
            )
            tbl.connection = derive_connection("hive", None, table_arg=tgt, database=self.default_db)
            self._stamp_read_line(tbl, call)
            df.writes_to.append(tbl)
            we = WriteEdgeIR(target=tbl, mode="overwrite", via="sparksql")
            we.line = self._first_lineno(call)
            df.write_edges.append(we)
        df.derivations.extend(lineage.derivations)
        return df

    # ---- chained transformations on an existing DataFrame --------------

    def _derive_from_chain(
        self, root: DataFrameIR, chain: list[ast.AST], name: str | None,
    ) -> DataFrameIR:
        current = root
        # Every intermediate transformation is its own DataFrameIR — append
        # them all so tests that flat-collect derivations / joins across
        # ``ir.dataframes`` see the right values.
        intermediates: list[DataFrameIR] = []
        terminal_seen = False
        in_writer_chain = getattr(root, "_writer_state", None) is not None
        in_reader_chain = getattr(root, "_reader_state", None) is not None
        for step in chain[1:]:
            # ``.write`` flattens to a bare Attribute (not a Call). When we hit
            # it, fork a fresh intermediate and stash a writer-state dict so
            # subsequent ``.format/.mode/.option/.partitionBy`` calls can
            # collect their args even though they're in a different statement
            # than the terminal ``.save()``.
            if isinstance(step, ast.Attribute) and step.attr in {"write", "writeStream"}:
                current = self._chain_step(
                    current, step.attr, None, via=step.attr, copy_fields=True,
                )
                current._writer_state = {  # type: ignore[attr-defined]
                    "format": None,
                    "mode": None,
                    "options": {},
                    "partition_cols": [],
                    "streaming": step.attr == "writeStream",
                }
                in_writer_chain = True
                intermediates.append(current)
                continue
            if not isinstance(step, ast.Call):
                continue
            method = _call_attr_name(step)
            if method is None:
                continue
            if in_writer_chain and method in {
                "format", "mode", "option", "options",
                "partitionBy", "outputMode", "queryName", "trigger", "foreachBatch",
            }:
                self._absorb_writer_method(current, method, step)
                continue
            if in_reader_chain:
                # Reader-chain mutators absorb into the stash; terminals
                # finalise the chain into a fully-built read DataFrame.
                if method in {"format", "option", "options"}:
                    self._absorb_reader_method(current, method, step)
                    continue
                if method in _TERMINAL_READ_METHODS:
                    new_df = self._finalize_reader_chain(current, method, step, name)
                    if new_df is not None:
                        current = new_df
                        intermediates.append(current)
                        in_reader_chain = False
                        # Apply any post-load chain steps (.cache(), .withColumn, …).
                        continue
                # Any other method on a reader stash → ignore safely.
                continue

            previous = current
            # Intermediate transformations always emit anonymous DataFrames —
            # only the LAST df is renamed to the LHS by ``_visit_assign``.
            inter_name: str | None = None
            if method in _PASSTHROUGH_METHODS:
                current = self._chain_step(current, method, inter_name, via=method, copy_fields=True)
                # v0.2 §6 — capture enterprise-runtime hints as first-class IR.
                _apply_runtime_hint(current, method, step)
                self._record_chain_step(current, op=method, kind="meta", call=step)
            elif method == "hint":
                current = self._chain_step(
                    current, "hint", inter_name, via="hint", copy_fields=True,
                )
                _apply_runtime_hint(current, "hint", step)
                self._record_chain_step(current, op="hint", kind="meta", call=step)
            elif method in _FILTER_METHODS:
                current = self._chain_step(current, method, inter_name, via="filter", copy_fields=True)
                pred_expr = step.args[0] if step.args else None
                pred_cols, _via, _udf = (
                    _columns_in_expression(pred_expr)
                    if pred_expr is not None
                    else ([], "filter", None)
                )
                self._record_chain_step(
                    current,
                    op=method,
                    kind="filter",
                    call=step,
                    expr=_unparse(pred_expr) if pred_expr is not None else None,
                    input_columns=list(pred_cols),
                )
            elif method == "select":
                current = self._apply_select(current, step, inter_name)
            elif method == "selectExpr":
                current = self._apply_select(current, step, inter_name, expr_mode=True)
            elif method == "withColumn":
                current = self._apply_with_column(current, step, inter_name)
            elif method == "withColumnRenamed":
                current = self._apply_with_column_renamed(current, step, inter_name)
            elif method == "drop":
                current = self._apply_drop(current, step, inter_name)
            elif method in _JOIN_METHODS:
                current = self._apply_join(current, step, inter_name)
            elif method in _UNION_METHODS:
                current = self._apply_union(current, step, inter_name)
            elif method == "groupBy":
                current = self._apply_groupby(current, step, inter_name)
            elif method == "agg":
                current = self._apply_agg(current, step, inter_name)
            elif method == "createOrReplaceTempView":
                self._register_temp_view(current, step)
                continue
            elif method == "transform":
                # v0.2 §8 — ``df.transform(fn)`` calls ``fn(df)``. If ``fn``
                # is a local FunctionDef (including a HOF-factory result we
                # stashed into ``self.functions``), inline-walk it; else fall
                # back to a generic callback edge.
                current = self._apply_transform(current, step, inter_name)
            elif method in {"foreach", "foreachPartition", "mapPartitions"}:
                # v0.2 §8 — callback-driven traversal. These don't return a
                # transformed DataFrame in the canonical sense, but we record
                # the callback edge so reviewers can see what was invoked.
                current = self._chain_step(
                    current, method, inter_name, via="callback", copy_fields=True,
                )
                if step.args and isinstance(step.args[0], ast.Name):
                    fn_name = step.args[0].id
                    if fn_name not in self.functions and fn_name not in self.external_functions:
                        self.ir.warnings.append(WarningIR(
                            type="external_callback",
                            detail=f".{method}({fn_name}) uses an external callable — lineage marked partial",
                            line=step.lineno,
                        ))
                        current.lineage_partial = True
            elif method in _TERMINAL_WRITE_METHODS:
                self._apply_write(current, chain, method, step)
                terminal_seen = True
                break
            else:
                continue

            if current is not previous:
                intermediates.append(current)

        # The LHS-bound DataFrame is the last intermediate — ``_visit_assign``
        # appends it. Earlier ones need to be appended here.
        for inter in intermediates[:-1]:
            if inter not in self.ir.dataframes:
                self.ir.dataframes.append(inter)
        if terminal_seen and name is None and intermediates:
            for inter in intermediates:
                if inter not in self.ir.dataframes:
                    self.ir.dataframes.append(inter)
        return current

    def _chain_step(
        self, src: DataFrameIR, method: str, name: str | None,
        *, via: str, copy_fields: bool,
    ) -> DataFrameIR:
        df = self._new_df(name or "__anon")
        df.reads_from = list(src.reads_from)
        if copy_fields:
            df.fields = list(src.fields)
        df.derives_from_dataframe.append(DataFrameEdgeIR(
            source_var=src.var_name, source_id=src.id, via=via,
        ))
        df.lineage_conditional = src.lineage_conditional
        df.lineage_partial = src.lineage_partial
        # v0.2 §5 — rename history is sticky across chain steps so the final
        # DataFrame carries its full provenance.
        df.renames = list(src.renames)
        # v0.2 §6 — passthrough hints propagate too: a `cache()` followed by
        # `withColumn(...)` keeps the cached flag on the resulting DataFrame.
        df.cached = src.cached
        df.persist_level = src.persist_level
        df.checkpointed = src.checkpointed
        df.partition_count = src.partition_count
        df.partition_columns = list(src.partition_columns)
        df.broadcast_hint = src.broadcast_hint
        return df

    def _apply_transform(
        self, src: DataFrameIR, call: ast.Call, name: str | None,
    ) -> DataFrameIR:
        """``df.transform(fn)`` — v0.2 §8 callback inlining.

        If ``fn`` is a known local function, simulate ``fn(src)`` by inlining
        its body in a scope where its first arg is bound to ``src``. Else fall
        back to a callback edge.
        """
        # Resolve which function reference was passed.
        fn_name: str | None = None
        if call.args and isinstance(call.args[0], ast.Name):
            fn_name = call.args[0].id

        if fn_name and fn_name in self.functions:
            # Build a synthetic call: fn(src) — wrap the src in a Name so the
            # existing inlining helper can resolve it.
            fake_call = ast.Call(
                func=ast.Name(id=fn_name, ctx=ast.Load()),
                args=[ast.Name(id=src.var_name, ctx=ast.Load())],
                keywords=[],
            )
            ast.copy_location(fake_call, call)
            saved = self.symbols
            self.symbols = {**self.symbols, src.var_name: src}
            try:
                result = self._inline_local_function(fn_name, fake_call, name)
            finally:
                self.symbols = saved
            if result is not None:
                result.derives_from_dataframe.append(DataFrameEdgeIR(
                    source_var=fn_name, via="transform",
                ))
                return result

        if fn_name and fn_name in self.external_functions:
            fake_call = ast.Call(
                func=ast.Name(id=fn_name, ctx=ast.Load()),
                args=[ast.Name(id=src.var_name, ctx=ast.Load())],
                keywords=[],
            )
            ast.copy_location(fake_call, call)
            saved = self.symbols
            self.symbols = {**self.symbols, src.var_name: src}
            try:
                result = self._inline_external_function(fn_name, fake_call, name)
            finally:
                self.symbols = saved
            if result is not None:
                result.derives_from_dataframe.append(DataFrameEdgeIR(
                    source_var=fn_name, via="transform",
                ))
                return result

        # External or non-resolvable callback — emit a passthrough + warn.
        df = self._chain_step(src, "transform", name, via="transform", copy_fields=True)
        self.ir.warnings.append(WarningIR(
            type="external_callback",
            detail=f".transform({fn_name or '<expr>'}) callback not resolvable — lineage marked partial",
            line=getattr(call, "lineno", None),
        ))
        df.lineage_partial = True
        return df

    def _apply_select(
        self, src: DataFrameIR, call: ast.Call, name: str | None, *, expr_mode: bool = False,
    ) -> DataFrameIR:
        via_label = "selectExpr" if expr_mode else "select"
        df = self._chain_step(src, "select", name, via=via_label, copy_fields=False)
        seen_names: set[str] = set()
        for arg in call.args:
            # M3b — `select("*")` / star-arg expansion. Mark as
            # all-columns-from-source so downstream consumers know the
            # projection inherits every input field, not just the listed ones.
            if _is_star_arg(arg):
                df.derivations.append(DerivationIR(
                    target_column="*",
                    source_columns=["*"],
                    via=f"{via_label}_star",
                    formula="*",
                ))
                for f in src.fields:
                    if f.name in seen_names:
                        continue
                    seen_names.add(f.name)
                    df.fields.append(AttributeIR(name=f.name))
                continue

            # M3a — selectExpr SQL strings: route through sqlglot so
            # `selectExpr("amount * 1.18 AS taxed")` records target=taxed,
            # sources=[amount]. Plain bare column names still fast-path.
            if expr_mode and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                parsed = _parse_selectexpr(arg.value)
                if parsed is not None:
                    target, sources = parsed
                    if target and target not in seen_names:
                        seen_names.add(target)
                        df.fields.append(AttributeIR(
                            name=target,
                            is_derived=bool(sources and sources != [target]),
                            derivation_formula=arg.value,
                        ))
                        df.derivations.append(DerivationIR(
                            target_column=target,
                            source_columns=sources,
                            via=via_label,
                            formula=arg.value,
                        ))
                    continue
                # Fallthrough: looks like a plain column reference, let
                # _column_ref_name handle it below.

            # M3b — chained .alias() — capture every alias in the chain
            # so reviewers can trace intermediate names.
            chain_aliases: list[str] = []
            if isinstance(arg, ast.Call):
                chain_aliases = _alias_chain(arg)
            col_name = _column_ref_name(arg)
            if col_name:
                # v0.2 §5 — column shadowing: duplicate aliases in the same
                # projection are legal but easy to misread, so emit an *info*
                # so reviewers can spot it without it being flagged as an
                # error.
                if col_name in seen_names:
                    self.ir.warnings.append(WarningIR(
                        type="column_shadowing",
                        subtype="info:select_alias_duplicate",
                        detail=(
                            f"select() projects '{col_name}' more than once"
                        ),
                        line=getattr(call, "lineno", None),
                    ))
                seen_names.add(col_name)
                df.fields.append(AttributeIR(name=col_name))
                if col_name not in {a.name for a in src.fields}:
                    df.derivations.append(DerivationIR(
                        target_column=col_name, source_columns=[col_name], via="select",
                    ))
                # Record every intermediate alias so .alias("x").alias("y")
                # leaves a trace of x.
                for intermediate in chain_aliases[:-1]:
                    if intermediate == col_name or intermediate in seen_names:
                        continue
                    df.derivations.append(DerivationIR(
                        target_column=intermediate,
                        source_columns=[col_name],
                        via="alias_chain",
                    ))
        # One TransformStepIR per select() / selectExpr() call. ``output_columns``
        # is the projected schema (in order); ``*`` markers also carry through.
        self._record_chain_step(
            df,
            op=via_label,
            kind="select",
            call=call,
            output_columns=list(seen_names),
            input_columns=[a.name for a in src.fields],
        )
        return df

    def _apply_with_column(self, src: DataFrameIR, call: ast.Call, name: str | None) -> DataFrameIR:
        df = self._chain_step(src, "withColumn", name, via="withColumn", copy_fields=True)
        if len(call.args) < 2:
            self._record_chain_step(df, op="withColumn", kind="derive", call=call)
            return df
        col_name = _const_arg(call, 0)
        expr = call.args[1]
        if col_name is None:
            self._record_chain_step(df, op="withColumn", kind="derive", call=call)
            return df
        sources, via, udf_name = _columns_in_expression(expr)
        # Detect a single `.cast()` so the step kind reflects type-casts
        # separately from generic derives — matches the plan's schema.
        step_kind = "derive"
        if isinstance(expr, ast.Call) and _cast_types_if_any(expr)[1] is not None:
            step_kind = "cast"
        self._record_chain_step(
            df,
            op="withColumn",
            kind=step_kind,
            call=call,
            expr=_unparse(expr),
            output_column=col_name,
            input_columns=list(sources),
        )
        df.derivations.append(DerivationIR(
            target_column=col_name, source_columns=sources,
            via=via, formula=_unparse(expr),
        ))
        dt = _datatype_from_expression(expr, self._udfs_by_name())
        # v0.2 §5 — column shadowing: a withColumn overwriting an existing
        # field is a real but easy-to-miss source of lineage loss. Drop the
        # severity to info (the in-place overwrite is intentional Spark
        # idiom) and record a self-referential derivation so downstream
        # consumers can see the original column still feeds the new one
        # even when the user-supplied expression doesn't name it.
        existing_idx = next(
            (i for i, a in enumerate(df.fields) if a.name == col_name),
            None,
        )
        if existing_idx is not None:
            self.ir.warnings.append(WarningIR(
                type="column_shadowing",
                subtype="info:withColumn_overwrite",
                detail=(
                    f"withColumn overwrites existing field '{col_name}' "
                    "(in-place rewrite, lineage preserved via self-edge)"
                ),
                line=getattr(call, "lineno", None),
            ))
            if col_name not in sources:
                df.derivations.append(DerivationIR(
                    target_column=col_name,
                    source_columns=[col_name],
                    via="withColumn_shadow",
                    formula=_unparse(expr),
                ))
            df.fields.pop(existing_idx)
        # v0.2 §5 — nested path. ``withColumn("a.b.c", ...)`` writes into a
        # nested struct. Capture the dotted path so writer.py can emit the
        # parent-child edge; the attribute "name" stays as the leaf.
        path = col_name if "." in col_name else None
        leaf = col_name.rsplit(".", 1)[-1] if path else col_name
        attr = AttributeIR(
            name=leaf,
            is_derived=True,
            derivation_formula=_unparse(expr),
            datatype=dt,
            path=path,
        )
        # v0.2 §5 — record the cast type-history when withColumn is a cast.
        if isinstance(expr, ast.Call):
            from_t, to_t = _cast_types_if_any(expr)
            if to_t is not None:
                attr.type_history.append((from_t, to_t))
        df.fields.append(attr)
        return df

    def _udfs_by_name(self) -> dict[str, UDFIR]:
        return {u.name: u for u in self.ir.udfs}

    def _apply_with_column_renamed(
        self, src: DataFrameIR, call: ast.Call, name: str | None,
    ) -> DataFrameIR:
        df = self._chain_step(src, "withColumnRenamed", name,
                              via="withColumnRenamed", copy_fields=True)
        old = _const_arg(call, 0)
        new = _const_arg(call, 1)
        if old and new:
            df.fields = [a for a in df.fields if a.name != old]
            df.fields.append(AttributeIR(name=new))
            df.derivations.append(DerivationIR(
                target_column=new, source_columns=[old], via="rename",
            ))
            # v0.2 §5 — append to the explicit rename log. Multi-step renames
            # (a → b → c) accumulate; consumers walk in reverse to trace the
            # original physical column.
            df.renames.append((new, old))
        self._record_chain_step(
            df,
            op="withColumnRenamed",
            kind="rename",
            call=call,
            input_columns=[old] if old else [],
            output_column=new,
        )
        return df

    def _apply_drop(self, src: DataFrameIR, call: ast.Call, name: str | None) -> DataFrameIR:
        df = self._chain_step(src, "drop", name, via="drop", copy_fields=True)
        drop_names = {_const_arg(call, i) for i in range(len(call.args))}
        df.fields = [a for a in df.fields if a.name not in drop_names]
        dropped = [n for n in drop_names if n]
        self._record_chain_step(
            df,
            op="drop",
            kind="drop",
            call=call,
            input_columns=dropped,
        )
        return df

    def _apply_join(self, src: DataFrameIR, call: ast.Call, name: str | None) -> DataFrameIR:
        df = self._chain_step(src, "join", name, via="join", copy_fields=False)
        # First positional arg is the right-side DataFrame
        right_name = _root_name(call.args[0]) if call.args else None
        # Resolve `broadcast(other)` and similar wrappers
        if call.args and isinstance(call.args[0], ast.Call):
            inner_root = _root_name(call.args[0].args[0]) if call.args[0].args else None
            right_name = inner_root or right_name
        right_df = self.symbols.get(right_name) if right_name else None
        cond_text = _unparse(call.args[1]) if len(call.args) > 1 else None
        join_type = "inner"
        if len(call.args) > 2:
            jt = _const_arg(call, 2)
            if jt:
                join_type = jt
        for kw in call.keywords:
            if kw.arg == "how":
                v = _const_value(kw.value)
                if isinstance(v, str):
                    join_type = v
        # v0.2 §6 — broadcast(other) wrapping on the right side promotes to
        # JoinIR.broadcast_hint plus a flag on the resulting DataFrame.
        join_broadcast_hint = False
        if call.args and isinstance(call.args[0], ast.Call):
            wrapper = call.args[0].func
            if isinstance(wrapper, ast.Name) and wrapper.id == "broadcast":
                join_broadcast_hint = True
        # An explicit `.hint("broadcast")` on either side also counts.
        if src.broadcast_hint or (right_df is not None and right_df.broadcast_hint):
            join_broadcast_hint = True

        join_ir = JoinIR(
            left=src.var_name, right=right_name or "",
            join_type=join_type, join_condition=cond_text,
        )
        # Add the broadcast flag as an attribute on the JoinIR for downstream
        # consumers — kept as setattr to avoid changing the dataclass shape
        # in ways that would force a schema migration on the writer.
        if join_broadcast_hint:
            join_ir.broadcast_hint = True  # type: ignore[attr-defined]
        df.joins.append(join_ir)
        df.broadcast_hint = join_broadcast_hint or df.broadcast_hint
        df.derives_from_dataframe.append(DataFrameEdgeIR(
            source_var=src.var_name, source_id=src.id, via="join",
        ))
        if right_df is not None:
            df.derives_from_dataframe.append(DataFrameEdgeIR(
                source_var=right_df.var_name, source_id=right_df.id, via="join",
            ))
            df.reads_from.extend(right_df.reads_from)
        df.fields = list(src.fields)
        if right_df is not None:
            df.fields.extend(right_df.fields)
        # Extract join keys (may be a string, a list of strings, or an
        # equality expression) for the chain step.
        join_keys: list[str] = []
        if len(call.args) > 1:
            on_arg = call.args[1]
            if isinstance(on_arg, ast.Constant) and isinstance(on_arg.value, str):
                join_keys = [on_arg.value]
            elif isinstance(on_arg, (ast.List, ast.Tuple)):
                join_keys = [
                    e.value for e in on_arg.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
        for kw in call.keywords:
            if kw.arg == "on" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                join_keys = [kw.value.value]
        self._record_chain_step(
            df,
            op="join",
            kind="join",
            call=call,
            expr=cond_text,
            join_other=right_name,
            join_keys=join_keys,
            join_how=join_type,
        )
        return df

    def _apply_union(self, src: DataFrameIR, call: ast.Call, name: str | None) -> DataFrameIR:
        df = self._chain_step(src, "union", name, via="union", copy_fields=True)
        other_name: str | None = None
        if call.args:
            other = self._eval_rhs_as_dataframe(call.args[0], default_name="__union_rhs")
            if other is not None:
                df.derives_from_dataframe.append(DataFrameEdgeIR(
                    source_var=other.var_name, source_id=other.id, via="union",
                ))
                df.reads_from.extend(other.reads_from)
                other_name = other.var_name
        self._record_chain_step(
            df, op="union", kind="join", call=call, join_other=other_name,
        )
        return df

    def _apply_groupby(self, src: DataFrameIR, call: ast.Call, name: str | None) -> DataFrameIR:
        df = self._chain_step(src, "groupBy", name, via="groupby", copy_fields=True)
        # Stash grouping columns so the subsequent .agg(...) call can use them.
        group_cols = [_column_ref_name(a) for a in call.args if _column_ref_name(a)]
        df._group_by_cols = group_cols  # type: ignore[attr-defined]
        self._record_chain_step(
            df,
            op="groupBy",
            kind="agg",
            call=call,
            input_columns=group_cols,
        )
        return df

    def _apply_agg(self, src: DataFrameIR, call: ast.Call, name: str | None) -> DataFrameIR:
        df = self._chain_step(src, "agg", name, via="agg", copy_fields=False)
        group_cols = getattr(src, "_group_by_cols", []) or []
        for g in group_cols:
            df.fields.append(AttributeIR(name=g))
        agg_inputs: list[str] = []
        agg_outputs: list[str] = []
        for arg in call.args:
            alias, sources, formula = _agg_to_alias(arg)
            if alias:
                dt = _datatype_from_expression(arg, self._udfs_by_name())
                df.fields.append(AttributeIR(
                    name=alias,
                    is_derived=True,
                    derivation_formula=formula,
                    datatype=dt,
                ))
                df.derivations.append(DerivationIR(
                    target_column=alias, source_columns=sources, via="agg", formula=formula,
                ))
                agg_outputs.append(alias)
                agg_inputs.extend(sources)
        self._record_chain_step(
            df,
            op="agg",
            kind="agg",
            call=call,
            input_columns=list(dict.fromkeys(group_cols + agg_inputs)),
            output_columns=agg_outputs,
        )
        return df

    def _register_temp_view(self, df: DataFrameIR, call: ast.Call) -> None:
        view_name = _const_arg(call, 0)
        if view_name:
            self.temp_views[view_name] = df

    def _apply_write(
        self, df: DataFrameIR, chain: list[ast.AST], method: str, call: ast.Call,
    ) -> None:
        # Seed config from any writer-state stash the DataFrame collected when
        # ``writer = df.write.format(...).mode(...).partitionBy(...)`` ran in
        # an earlier statement. Without this, ``writer.save(path)`` would lose
        # every writer-chain attribute because they don't appear in this call's
        # chain.
        state = getattr(df, "_writer_state", None) or {}
        write_format = state.get("format")
        mode = state.get("mode") or "overwrite"
        write_options: dict[str, str] = dict(state.get("options") or {})
        partition_cols: list[str] = list(state.get("partition_cols") or [])
        streaming = bool(state.get("streaming"))
        foreach_batch_callback = state.get("foreach_batch_callback")
        for node in chain:
            if not isinstance(node, ast.Call):
                continue
            attr = _call_attr_name(node)
            if attr == "format":
                fmt = self._resolve_str(node.args[0]) if node.args else None
                write_format = fmt or write_format
            elif attr == "mode":
                mode_v = self._resolve_str(node.args[0]) if node.args else None
                if mode_v:
                    mode = mode_v
            elif attr == "option":
                k = self._resolve_str(node.args[0]) if node.args else None
                v = self._resolve_str(node.args[1]) if len(node.args) > 1 else None
                if k and v:
                    write_options[k] = v
                for kw in node.keywords:
                    if kw.arg is None:
                        splat = self._resolve_dict_value(kw.value) or {}
                        for sk, sv in splat.items():
                            rv = self._resolve_str(sv)
                            if rv is not None:
                                write_options[sk] = rv
                    elif kw.arg:
                        kv = self._resolve_str(kw.value)
                        if kv is not None:
                            write_options[kw.arg] = kv
            elif attr == "options" and node.args:
                splat = self._resolve_dict_value(node.args[0]) or {}
                for sk, sv in splat.items():
                    rv = self._resolve_str(sv)
                    if rv is not None:
                        write_options[sk] = rv
            elif attr == "partitionBy":
                partition_cols.extend(self._extract_partition_cols(node))
            elif attr in _TERMINAL_WRITE_METHODS:
                break
        # `start()` and the convenience writers (`.parquet(p)`, `.csv(p)`,
        # `.jdbc(url, tbl, props)`) need their format inferred from the
        # method name when no `.format(...)` was set.
        if not write_format:
            if method in {"parquet", "csv", "json", "orc", "text"}:
                write_format = method
            elif method == "jdbc":
                write_format = "jdbc"
        if method == "start" and not streaming:
            streaming = True
        # 3-arg JDBC writer: df.write.jdbc(url, table, mode=, properties=).
        # Lift url + dbtable into options so derive_connection picks them up.
        if method == "jdbc" and call.args:
            url0 = self._resolve_str(call.args[0])
            if url0:
                write_options["url"] = url0
            if len(call.args) >= 2:
                tbl0 = self._resolve_str(call.args[1])
                if tbl0:
                    write_options["dbtable"] = tbl0
            if len(call.args) >= 3:
                props = self._resolve_dict_value(call.args[2]) or {}
                for pk, pv in props.items():
                    rv = self._resolve_str(pv)
                    if rv is not None:
                        write_options[pk] = rv
            for kw in call.keywords:
                if kw.arg == "properties":
                    props = self._resolve_dict_value(kw.value) or {}
                    for pk, pv in props.items():
                        rv = self._resolve_str(pv)
                        if rv is not None:
                            write_options[pk] = rv
                elif kw.arg == "mode":
                    m = self._resolve_str(kw.value)
                    if m:
                        mode = m
        # foreachBatch — best-effort: walk the callback body for nested
        # writes so the batch sink is still visible in the graph.
        if foreach_batch_callback and foreach_batch_callback in self.functions:
            self._walk_foreach_batch(df, self.functions[foreach_batch_callback])
        # Pull the .save() target via _resolve_str so inlined param bindings
        # like ``writer.save(path)`` where ``path`` was bound to
        # ``cfg.GOLD_ORDERS`` resolve cleanly instead of getting flagged as a
        # dynamic table name.
        target_str = self._resolve_str(call.args[0]) if call.args else None
        unresolved_source = unresolved_detail = None
        if target_str is None and call.args:
            unresolved_source, unresolved_detail = self._classify_unresolved(call.args[0])

        # v0.2 §9 — connector-aware writes. If the format string matches an
        # external connector, emit the canonical FQN even when the write goes
        # through `.save(target)` rather than `.saveAsTable`.
        from ..connectors import match_connector, derive_connection
        cm = match_connector(
            write_format, write_options,
            path_arg=target_str, table_arg=target_str,
        )

        # Connectors that address the target through ``.option(...)`` rather
        # than a positional ``.save("path")`` arg — JDBC (dbtable + url),
        # Cassandra (keyspace + table), MongoDB (uri/collection), Elasticsearch
        # (es.resource). These don't fail when target_str is None; we can
        # derive a Table FQN from the options alone.
        options_addressed_fqn: str | None = None
        options_addressed_location: str | None = None
        wf = (write_format or "").lower()
        if target_str is None and cm is None:
            if wf == "jdbc" or wf in {"redshift", "io.github.spark_redshift_community.spark.redshift", "com.databricks.spark.redshift"}:
                dbtable = write_options.get("dbtable") or write_options.get("query")
                url = write_options.get("url")
                options_addressed_fqn = dbtable or url
                options_addressed_location = url
            elif wf in {"cassandra", "org.apache.spark.sql.cassandra"}:
                keyspace = write_options.get("keyspace") or ""
                tbl_name = write_options.get("table") or ""
                if keyspace or tbl_name:
                    options_addressed_fqn = ".".join(p for p in (keyspace, tbl_name) if p) or None
            elif wf in {"mongo", "mongodb", "com.mongodb.spark.sql.defaultsource"}:
                uri = write_options.get("uri") or write_options.get("spark.mongodb.output.uri")
                coll = write_options.get("collection")
                options_addressed_fqn = coll
                options_addressed_location = uri
            elif wf in {"elasticsearch", "es", "org.elasticsearch.spark.sql"}:
                options_addressed_fqn = write_options.get("es.resource") or write_options.get("resource")

        if target_str is None and cm is None and options_addressed_fqn is None and options_addressed_location is None:
            # No target string AND no option-addressed target. If the call
            # *had* an argument (start(), save(path)) and we know what kind
            # of dynamic value it was, mint an unresolved Connection node so
            # the I/O site is still visible in the graph. We ALSO emit the
            # legacy dynamic_table_name warning so existing consumers don't
            # regress — the two signals are complementary.
            if unresolved_source:
                tbl = TableIR(storage_format=write_format)
                tbl.connection = derive_connection(
                    write_format, write_options,
                    location=None, table_arg=None,
                    database=self.default_db,
                    unresolved_source=unresolved_source,
                    unresolved_detail=unresolved_detail,
                )
                self._stamp_read_line(tbl, call)
                df.writes_to.append(tbl)
                df.write_edges.append(WriteEdgeIR(
                    target=tbl, mode=mode, via=method,
                    partition_columns=list(partition_cols),
                    line=self._first_lineno(call),
                ))
                df.lineage_partial = True
                self.ir.warnings.append(WarningIR(
                    type="dynamic_table_name",
                    subtype=f"unresolved:{unresolved_source}",
                    detail=(
                        f".{method}() target is {unresolved_source}-sourced"
                        + (f" ({unresolved_detail})" if unresolved_detail else "")
                        + " — Connection node minted as resolved=False"
                    ),
                    line=call.lineno,
                ))
                return
            # foreachBatch and method=='start' with no path: the sink was
            # handled inside the callback (via _walk_foreach_batch).
            if method == "start" and foreach_batch_callback:
                return
            df.lineage_partial = True
            self.ir.warnings.append(WarningIR(
                type="dynamic_table_name",
                detail=f"dynamic table name: .{method}() target is not statically resolvable",
                line=call.lineno,
            ))
            return
        if cm is not None:
            tbl = TableIR(
                fully_qualified_name=cm.fully_qualified_name,
                location=cm.location,
                storage_format=cm.storage_format,
            )
        elif options_addressed_fqn is not None or options_addressed_location is not None:
            tbl = TableIR(
                fully_qualified_name=options_addressed_fqn,
                location=options_addressed_location,
                storage_format=write_format,
            )
        elif method in {"saveAsTable", "insertInto"}:
            fqn = _qualify_table_name(target_str, self.default_db)
            tbl = TableIR(fully_qualified_name=fqn, storage_format=write_format or "hive")
        else:  # save("s3://...")
            tbl = TableIR(location=target_str, storage_format=write_format)
        tbl.connection = derive_connection(
            write_format, write_options,
            location=tbl.location or target_str,
            table_arg=target_str if method in {"saveAsTable", "insertInto"} else None,
            database=self.default_db,
            unresolved_source=unresolved_source,
            unresolved_detail=unresolved_detail,
        )
        self._stamp_read_line(tbl, call)
        df.writes_to.append(tbl)
        # Z-order columns can be supplied at the inline call site (e.g.
        # ``write_delta(df, …, z_order="customer_id,product_id")``) — read
        # the active ``z_order`` binding from the overlay if present.
        z_order_cols: list[str] = []
        z_value = self.string_constants.get("z_order")
        if z_value:
            z_order_cols = [c.strip() for c in z_value.split(",") if c.strip()]
        df.write_edges.append(WriteEdgeIR(
            target=tbl,
            mode=mode,
            via="writeStream:" + method if streaming else method,
            partition_columns=list(partition_cols),
            z_order_columns=z_order_cols,
            line=self._first_lineno(call),
        ))

    def _walk_foreach_batch(self, df: DataFrameIR, fn: ast.FunctionDef) -> None:
        """Best-effort: inspect a ``foreachBatch`` callback for sink writes.

        The callback signature is ``(batch_df, batch_id)`` — we bind the
        first parameter to the outer DataFrame and walk the body looking
        for terminal write methods on it. Any sink found is attached back
        to the outer ``df``.
        """
        if not fn.args.args:
            return
        if not self._enter_inline(f"foreachBatch:{fn.name}", getattr(fn, "lineno", None)):
            return
        try:
            scope = dict(self.symbols)
            scope[fn.args.args[0].arg] = df
            saved = self.symbols
            self.symbols = scope
            try:
                for stmt in fn.body:
                    self._visit_stmt(stmt, in_branch=False)
            finally:
                self.symbols = saved
        finally:
            self._exit_inline()

    def _absorb_writer_method(self, df: DataFrameIR, method: str, step: ast.Call) -> None:
        """Stash writer-chain config (``.format``, ``.mode``, ``.option``,
        ``.partitionBy``, plus streaming-only ``.outputMode``, ``.queryName``,
        ``.trigger``, ``.foreachBatch``) on ``df._writer_state`` so a later
        ``.save(path)`` / ``.start()`` in a separate statement can recover
        the full sink description.

        Defensive about being called on a non-writer chain (e.g. when the
        ``.write`` head wasn't recognised): does nothing if the state stash
        is missing.
        """
        state = getattr(df, "_writer_state", None)
        if state is None:
            return
        if method == "format" and step.args:
            s = self._resolve_str(step.args[0])
            if s:
                state["format"] = s
        elif method == "mode" and step.args:
            s = self._resolve_str(step.args[0])
            if s:
                state["mode"] = s
        elif method == "outputMode" and step.args:
            s = self._resolve_str(step.args[0])
            if s:
                state["mode"] = s
                state["streaming"] = True
        elif method == "queryName" and step.args:
            s = self._resolve_str(step.args[0])
            if s:
                state.setdefault("options", {})["queryName"] = s
        elif method == "trigger":
            state["streaming"] = True
        elif method == "option":
            k = self._resolve_str(step.args[0]) if step.args else None
            v = self._resolve_str(step.args[1]) if len(step.args) > 1 else None
            if k and v:
                state["options"][k] = v
            # Streaming options often arrive as kwargs.
            for kw in step.keywords:
                if kw.arg:
                    kv = self._resolve_str(kw.value)
                    if kv is not None:
                        state["options"][kw.arg] = kv
        elif method == "options":
            # `.options(**d)` / `.options(d)` — merge both shapes into stash.
            for kw in step.keywords:
                if kw.arg is None:
                    splat = self._resolve_dict_value(kw.value) or {}
                    for sk, sv in splat.items():
                        rv = self._resolve_str(sv)
                        if rv is not None:
                            state["options"][sk] = rv
                else:
                    kv = self._resolve_str(kw.value)
                    if kv is not None:
                        state["options"][kw.arg] = kv
            if step.args:
                splat = self._resolve_dict_value(step.args[0]) or {}
                for sk, sv in splat.items():
                    rv = self._resolve_str(sv)
                    if rv is not None:
                        state["options"][sk] = rv
        elif method == "partitionBy":
            state["partition_cols"].extend(self._extract_partition_cols(step))
        elif method == "foreachBatch":
            # Best-effort: capture the callback name so _apply_write can
            # inspect the function body for nested write sites.
            if step.args and isinstance(step.args[0], ast.Name):
                state["foreach_batch_callback"] = step.args[0].id
            state["streaming"] = True

    def _absorb_reader_method(self, df: DataFrameIR, method: str, step: ast.Call) -> None:
        """Mirror of ``_absorb_writer_method`` for stored reader chains.

        Accumulates ``.format`` / ``.option`` / ``.options`` (incl. ``**dict``
        splats and a positional dict) into ``df._reader_state`` so the
        eventual ``.load(...)`` call gets the full config.
        """
        state = getattr(df, "_reader_state", None)
        if state is None:
            return
        if method == "format" and step.args:
            s = self._resolve_str(step.args[0])
            if s:
                state["format"] = s
        elif method == "option":
            k = self._resolve_str(step.args[0]) if step.args else None
            v = self._resolve_str(step.args[1]) if len(step.args) > 1 else None
            if k and v:
                state.setdefault("options", {})[k] = v
            for kw in step.keywords:
                if kw.arg:
                    kv = self._resolve_str(kw.value)
                    if kv is not None:
                        state.setdefault("options", {})[kw.arg] = kv
        elif method == "options":
            for kw in step.keywords:
                if kw.arg is None:
                    splat = self._resolve_dict_value(kw.value) or {}
                    for sk, sv in splat.items():
                        rv = self._resolve_str(sv)
                        if rv is not None:
                            state.setdefault("options", {})[sk] = rv
                else:
                    kv = self._resolve_str(kw.value)
                    if kv is not None:
                        state.setdefault("options", {})[kw.arg] = kv
            if step.args:
                splat = self._resolve_dict_value(step.args[0]) or {}
                for sk, sv in splat.items():
                    rv = self._resolve_str(sv)
                    if rv is not None:
                        state.setdefault("options", {})[sk] = rv

    def _finalize_reader_chain(
        self,
        reader_df: DataFrameIR,
        method: str,
        step: ast.Call,
        name: str | None,
    ) -> DataFrameIR | None:
        """Consume the reader-state stash and emit a real read DataFrame.

        Triggered when a stored-reader chain hits a terminal-read method
        (``.load(...)``, ``.parquet(p)``, ``.jdbc(url, tbl, props)``, …).
        Builds the same kind of TableIR + ConnectionIR that an in-line
        ``spark.read.X(...)`` chain would have produced.
        """
        state = dict(getattr(reader_df, "_reader_state", None) or {})
        options: dict[str, str] = dict(state.get("options") or {})
        storage_format = state.get("format")
        location = state.get("location")
        table_arg = state.get("table_arg")
        unresolved_source = state.get("unresolved_source")
        unresolved_detail = state.get("unresolved_detail")

        if method == "load":
            arg = self._resolve_str(step.args[0]) if step.args else None
            if arg:
                location = arg
            elif step.args and not unresolved_source:
                unresolved_source, unresolved_detail = self._classify_unresolved(step.args[0])
        elif method in _SPARK_READ_FORMATS:
            storage_format = storage_format or method
            arg = self._resolve_str(step.args[0]) if step.args else None
            if arg:
                location = arg
            elif step.args and not unresolved_source:
                unresolved_source, unresolved_detail = self._classify_unresolved(step.args[0])
        elif method == "table":
            t_arg = self._resolve_str(step.args[0]) if step.args else None
            if t_arg:
                table_arg = t_arg
        elif method == "jdbc":
            storage_format = "jdbc"
            if step.args:
                url = self._resolve_str(step.args[0])
                if url:
                    options["url"] = url
            if len(step.args) >= 2:
                tbl = self._resolve_str(step.args[1])
                if tbl:
                    options["dbtable"] = tbl
            if len(step.args) >= 3:
                props = self._resolve_dict_value(step.args[2]) or {}
                for pk, pv in props.items():
                    rv = self._resolve_str(pv)
                    if rv is not None:
                        options[pk] = rv
        elif method in _RDD_READ_METHODS:
            storage_format = "text" if method in {"textFile", "wholeTextFiles"} else "binary"
            arg = self._resolve_str(step.args[0]) if step.args else None
            if arg:
                location = arg

        from ..connectors import match_connector, derive_connection
        cm = match_connector(
            storage_format, options, path_arg=location, table_arg=table_arg,
        )
        if cm is not None:
            tbl = TableIR(
                storage_format=cm.storage_format,
                fully_qualified_name=cm.fully_qualified_name,
                location=cm.location,
            )
        elif table_arg is not None:
            tbl = TableIR(
                fully_qualified_name=_qualify_table_name(table_arg, self.default_db),
                storage_format="hive",
            )
        elif storage_format == "jdbc":
            jdbc_url = options.get("url", location or "")
            dbtable = options.get("dbtable") or options.get("query")
            tbl = TableIR(
                storage_format="jdbc",
                location=jdbc_url,
                fully_qualified_name=dbtable or jdbc_url,
            )
        elif location:
            tbl = TableIR(
                storage_format=storage_format or "parquet",
                location=location,
                fully_qualified_name=location,
            )
        else:
            tbl = TableIR(storage_format=storage_format, fully_qualified_name="")
        tbl.connection = derive_connection(
            storage_format, options,
            location=tbl.location or location,
            table_arg=table_arg,
            database=self.default_db,
            unresolved_source=unresolved_source,
            unresolved_detail=unresolved_detail,
        )
        # Mint a fresh DataFrame so the reader-state stash on the old `df`
        # doesn't leak into the loaded result.
        out = self._new_df(name or "__anon")
        self._stamp_read_line(tbl, step)
        out.reads_from.append(tbl)
        return out

    def _extract_partition_cols(self, call: ast.Call) -> list[str]:
        """Pull column names out of a ``.partitionBy(...)`` call.

        Handles three shapes:
        * ``.partitionBy("a", "b")`` — positional literals.
        * ``.partitionBy(*partition_by)`` — Starred Name resolved via
          ``self.list_constants`` so an inlined kwarg flows through.
        * ``.partitionBy(["a", "b"])`` — a literal list as the sole arg.
        """
        cols: list[str] = []
        for arg in call.args:
            if isinstance(arg, ast.Starred):
                resolved = self._resolve_list_value(arg.value)
                if resolved:
                    cols.extend(str(x) for x in resolved if isinstance(x, str))
                continue
            if isinstance(arg, (ast.List, ast.Tuple)):
                resolved_list = self._resolve_list_value(arg)
                if resolved_list:
                    cols.extend(str(x) for x in resolved_list if isinstance(x, str))
                continue
            s = self._resolve_str(arg)
            if s:
                cols.append(s)
        return cols

    # ---- local-function inlining and external-fn fallback --------------

    def _bind_call_args(
        self,
        fn: ast.FunctionDef,
        call: ast.Call,
        *,
        drop_self: bool = False,
    ) -> "_CallBinding":
        """Full Python call-binding semantics.

        Binds caller arguments to function parameters following the same
        rules CPython uses at runtime: positional first, then keywords by
        name, then defaults. Returns a ``_CallBinding`` describing:

        * ``df_scope``: param name → DataFrameIR (when the bound value
          resolved to a tracked DataFrame).
        * ``str_scope``: param name → resolved string literal.
        * ``list_scope``: param name → resolved list-of-strings literal.
        * ``missing``: required params with no caller value AND no default —
          the *only* trigger for ``interproc_args_mismatch``.
        * ``vararg_overflow``: extra positional args that went into ``*args``.
        * ``kwarg_extras``: unrecognised keyword names that went into
          ``**kwargs``.

        ``drop_self`` strips the leading ``self``/``cls`` for bound methods.
        """
        a = fn.args
        params = list(a.args)
        if drop_self and params and params[0].arg in {"self", "cls"}:
            params = params[1:]

        n_required = max(0, len(params) - len(a.defaults))
        defaults_by_idx: dict[int, ast.AST] = {
            n_required + i: d for i, d in enumerate(a.defaults)
        }
        kwonly_defaults: dict[str, ast.AST | None] = {
            kw.arg: dflt
            for kw, dflt in zip(a.kwonlyargs, a.kw_defaults)
        }

        binding = _CallBinding()
        positional_consumed: set[str] = set()
        kw_consumed: set[str] = set()
        valid_kw_names: set[str] = {p.arg for p in params} | {
            kw.arg for kw in a.kwonlyargs
        }

        # 1. Positional args → first N params, then overflow into *args.
        for idx, arg_val in enumerate(call.args):
            if idx < len(params):
                name = params[idx].arg
                self._bind_value(binding, name, arg_val)
                positional_consumed.add(name)
            elif a.vararg is not None:
                binding.vararg_overflow.append(arg_val)
            # else: positional overflow is a real bug; surfaced via "missing"
            # below isn't quite right but Python would TypeError — leave it.

        # 2. Keyword args.
        for kw in call.keywords:
            if kw.arg is None:
                # ``**some_dict`` — best-effort: when the splatted dict is a
                # tracked literal, expand its string entries into bindings.
                splat_items = self._resolve_dict_value(kw.value) or {}
                for key, val_node in splat_items.items():
                    if key in valid_kw_names and key not in kw_consumed:
                        self._bind_value(binding, key, val_node, prebound=True)
                        kw_consumed.add(key)
                    elif a.kwarg is not None:
                        binding.kwarg_extras[key] = val_node
                continue
            if kw.arg in valid_kw_names and kw.arg not in positional_consumed:
                self._bind_value(binding, kw.arg, kw.value)
                kw_consumed.add(kw.arg)
            elif a.kwarg is not None:
                binding.kwarg_extras[kw.arg] = kw.value

        # 3. Defaults + missing-required detection.
        for idx, param in enumerate(params):
            if param.arg in positional_consumed or param.arg in kw_consumed:
                continue
            if idx in defaults_by_idx:
                self._bind_value(binding, param.arg, defaults_by_idx[idx])
                continue
            binding.missing.append(param.arg)

        for kwonly in a.kwonlyargs:
            if kwonly.arg in kw_consumed:
                continue
            dflt = kwonly_defaults.get(kwonly.arg)
            if dflt is not None:
                self._bind_value(binding, kwonly.arg, dflt)
            else:
                binding.missing.append(kwonly.arg)

        return binding

    def _bind_value(
        self,
        binding: "_CallBinding",
        name: str,
        ast_val: ast.AST,
        *,
        prebound: bool = False,
    ) -> None:
        """Populate every applicable slot for one (param_name, ast_value) pair.

        A single AST node may be all of: a DataFrame, a string, and a list.
        We probe each independently so the downstream chain finds whichever
        shape the body needs (``path=…`` reads via str_scope, ``df=…`` via
        df_scope, ``partition_by=…`` via list_scope).
        """
        # DataFrame? — guarded against rebinding when the value was harvested
        # from a **kwargs splat where we don't have a real AST source.
        if not prebound:
            df = self._eval_rhs_as_dataframe(ast_val, default_name=name)
            if df is not None:
                binding.df_scope[name] = df
        s = self._resolve_str(ast_val)
        if s is not None:
            binding.str_scope[name] = s
        items = self._resolve_list_value(ast_val)
        if items is not None and all(isinstance(i, str) for i in items):
            binding.list_scope[name] = [str(i) for i in items]

    def _resolve_dict_value(self, node: ast.AST | None) -> dict[str, ast.AST] | None:
        """Resolve a dict-literal RHS into (str_key → value_node) pairs.

        Used to flatten ``**some_dict`` splats into individual bindings.
        Returns ``None`` when any key isn't a static string literal.

        Beyond literal ``ast.Dict`` we also accept:

        * ``Name`` references into ``self.dict_constants`` — lets
          ``.options(**options)`` resolve when ``options`` was tracked.
        * ``{**a, **b, "k": v}`` merge literals — flatten left-to-right.
        """
        if node is None:
            return None
        if isinstance(node, ast.Name) and node.id in self.dict_constants:
            return dict(self.dict_constants[node.id])
        if not isinstance(node, ast.Dict):
            return None
        out: dict[str, ast.AST] = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                # ``**other`` merge — flatten and copy in.
                nested = self._resolve_dict_value(v)
                if nested is None:
                    return None
                out.update(nested)
                continue
            if isinstance(k, ast.Constant) and isinstance(k.value, str) and v is not None:
                out[k.value] = v
            else:
                return None
        return out

    def _emit_inline_warnings(
        self,
        fn: ast.FunctionDef,
        call: ast.Call,
        fn_label: str,
        binding: "_CallBinding",
    ) -> None:
        """Emit only the warnings the binding *actually* failed to cover.

        Previously every keyword arg or default-bearing param triggered a
        false ``interproc_args_mismatch``. Now we warn solely when a required
        positional param received no caller value AND no default.
        """
        line = getattr(call, "lineno", None)
        if fn.args.vararg is not None and binding.vararg_overflow:
            self.ir.warnings.append(WarningIR(
                type="interproc_vararg",
                detail=(
                    f"{fn_label}({fn.args.vararg.arg}=*) — "
                    f"{len(binding.vararg_overflow)} extra positional args "
                    "spread into *args, individual lineage marked partial"
                ),
                line=line,
            ))
        if fn.args.kwarg is not None and binding.kwarg_extras:
            self.ir.warnings.append(WarningIR(
                type="interproc_kwarg",
                detail=(
                    f"{fn_label}({fn.args.kwarg.arg}=**) — "
                    f"{len(binding.kwarg_extras)} unrecognised kwargs "
                    "spread into **kwargs"
                ),
                line=line,
            ))
        if binding.missing:
            self.ir.warnings.append(WarningIR(
                type="interproc_args_mismatch",
                detail=(
                    f"{fn_label}(...) missing required parameter(s): "
                    f"{', '.join(binding.missing)} — bindings incomplete"
                ),
                line=line,
            ))

    def _inline_overlays(self, binding: "_CallBinding"):
        """Context manager — overlay str/list bindings on visitor state.

        The inlined body sees param names like ``path`` and ``partition_by``
        as fully-resolved values in ``string_constants`` / ``list_constants``
        for the duration of the inline. On exit the prior bindings are
        restored, including the previously-shadowed values (so nested
        inlines that bind ``path`` don't leak out one level up).
        """
        visitor = self

        class _Overlay:
            def __enter__(self_inner) -> None:
                self_inner._str_saved = dict(visitor.string_constants)
                self_inner._list_saved = dict(visitor.list_constants)
                visitor.string_constants.update(binding.str_scope)
                visitor.list_constants.update(binding.list_scope)

            def __exit__(self_inner, *exc) -> None:
                visitor.string_constants = self_inner._str_saved
                visitor.list_constants = self_inner._list_saved
                return None

        return _Overlay()

    def _enter_inline(self, fn_label: str, line: int | None) -> bool:
        """Return True if the caller may proceed; False if depth-capped.

        M2b — also refuses functions the pre-pass marked as recursive (direct
        or via a cycle). Emits a one-time `recursive_function` warning so the
        reader knows lineage stops at the call site.
        """
        # Strip "Class." prefix when consulting the recursive set — the
        # call-graph keyed methods by short name.
        short = fn_label.rsplit(".", 1)[-1]
        if short in self.recursive_functions or fn_label in self.recursive_functions:
            self.ir.warnings.append(WarningIR(
                type="recursive_function",
                detail=(
                    f"{fn_label} is part of a call cycle — not inlined, "
                    "lineage marked partial at the call site"
                ),
                line=line,
            ))
            return False
        if self._inline_depth >= INLINE_MAX_DEPTH:
            self.ir.warnings.append(WarningIR(
                type="recursion_capped",
                detail=(
                    f"interprocedural inline depth hit cap "
                    f"({INLINE_MAX_DEPTH}) at {fn_label} — lineage marked partial"
                ),
                line=line,
            ))
            return False
        self._inline_depth += 1
        return True

    def _exit_inline(self) -> None:
        self._inline_depth -= 1

    def _resolve_tuple_return(
        self, ret_value: ast.AST, expected_name: str | None,
    ) -> DataFrameIR | None:
        """M2a — partial support for `return a, b`.

        We can't bind multi-element returns into the caller's tuple-LHS from
        here (the caller drives binding). Best-effort fallback: return the
        first element so at least the primary DataFrame threads through, and
        warn that the secondary returns are dropped.
        """
        if not isinstance(ret_value, (ast.Tuple, ast.List)):
            return self._eval_rhs_as_dataframe(
                ret_value, default_name=expected_name or "__ret",
            )
        if not ret_value.elts:
            return None
        self.ir.warnings.append(WarningIR(
            type="tuple_return_partial",
            detail=(
                "function returns a tuple/list; only the first element is "
                "threaded into the caller's lineage"
            ),
            line=getattr(ret_value, "lineno", None),
        ))
        return self._eval_rhs_as_dataframe(
            ret_value.elts[0], default_name=expected_name or "__ret",
        )

    def _inline_local_function(
        self, fn_name: str, call: ast.Call, expected_name: str | None,
    ) -> DataFrameIR | None:
        fn = self.functions[fn_name]
        # Build a temp visitor scope bound to function arg names → caller dfs
        if not fn.args.args and fn.args.vararg is None and not fn.args.kwonlyargs:
            return None
        if not self._enter_inline(fn_name, getattr(call, "lineno", None)):
            return None
        binding = self._bind_call_args(fn, call)
        self._emit_inline_warnings(fn, call, fn_name, binding)
        scope: dict[str, DataFrameIR] = dict(self.symbols)
        scope.update(binding.df_scope)
        # Snapshot the IR's dataframe count so we can rewrite the parameter
        # names recorded on intermediates created during this inline back to
        # the caller's argument names. Without this, collapse-pass walk-back
        # hits dead-ends like ``orders``/``enriched_orders`` (the parameter
        # names) instead of reaching the caller's ``df_orders``/``df_enriched``
        # anchors — see _PySparkVisitor._collapse_to_anchors.
        ir_pivot = len(self.ir.dataframes)
        caller_symbols = self.symbols
        with self._inline_overlays(binding):
            saved = self.symbols
            self.symbols = scope
            try:
                result: DataFrameIR | None = None
                for stmt in fn.body:
                    if isinstance(stmt, ast.Return):
                        result = self._resolve_tuple_return(stmt.value, expected_name)
                        break
                    self._visit_stmt(stmt, in_branch=False)
                self._rewrite_param_edges(
                    ir_pivot, fn, call, caller_symbols, result,
                )
                return result
            finally:
                self.symbols = saved
                self._exit_inline()

    def _rewrite_param_edges(
        self,
        ir_pivot: int,
        fn: ast.FunctionDef,
        call: ast.Call,
        caller_symbols: dict[str, DataFrameIR],
        result: DataFrameIR | None,
        *,
        drop_self: bool = False,
    ) -> None:
        """Replace parameter-name ``source_var``s on inline-created edges
        with the caller-visible variable name of the bound argument.

        ``_bind_value`` creates a fresh DataFrame clone whose ``var_name``
        equals the parameter (e.g. ``enriched_orders``), so reading
        ``binding.df_scope[param].var_name`` is a no-op rewrite. The real
        caller-side names live on the ``ast.Name`` nodes in ``call.args`` /
        ``call.keywords``; map those instead.

        Without this fix, the collapse pass's walk-back terminates at a
        dangling parameter name when it tries to find the upstream anchor
        for a reassigned variable, leaving ``input_anchor_ids`` empty.
        """
        params = list(fn.args.args)
        if drop_self and params and params[0].arg in {"self", "cls"}:
            params = params[1:]

        # param_name → (caller_var_name, caller_df_id). Stamping ``source_id``
        # alongside ``source_var`` is what lets the collapse pass land on
        # the exact predecessor when the variable was reassigned multiple
        # times (id is unique, name isn't).
        param_to_caller: dict[str, tuple[str, str | None]] = {}
        # Positional argument bindings.
        for idx, arg_val in enumerate(call.args):
            if idx >= len(params):
                break
            if not isinstance(arg_val, ast.Name):
                continue
            caller_df = caller_symbols.get(arg_val.id)
            if caller_df is None or not caller_df.var_name:
                continue
            param_to_caller[params[idx].arg] = (caller_df.var_name, caller_df.id)
        # Keyword argument bindings (the ones that name an actual param).
        valid_kw = {p.arg for p in params} | {
            kw.arg for kw in fn.args.kwonlyargs
        }
        for kw in call.keywords:
            if kw.arg is None or kw.arg not in valid_kw:
                continue
            if not isinstance(kw.value, ast.Name):
                continue
            caller_df = caller_symbols.get(kw.value.id)
            if caller_df is None or not caller_df.var_name:
                continue
            param_to_caller[kw.arg] = (caller_df.var_name, caller_df.id)

        if not param_to_caller:
            return

        targets: list[DataFrameIR] = list(self.ir.dataframes[ir_pivot:])
        if result is not None and result not in targets:
            targets.append(result)
        for df in targets:
            for edge in df.derives_from_dataframe:
                if edge.source_var and edge.source_var in param_to_caller:
                    new_name, new_id = param_to_caller[edge.source_var]
                    edge.source_var = new_name
                    edge.source_id = new_id

    def _resolve_class_method(
        self, class_name: str, method_name: str,
    ) -> ast.FunctionDef | None:
        """Look up ``ClassName.method`` walking up the MRO chain (v0.2 §8).

        Only same-file bases are followed; ABCs that live in third-party
        packages are out of scope and fall through to ``external_function``.
        """
        seen: set[str] = set()
        stack = [class_name]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            methods = self.class_methods.get(cur, {})
            if method_name in methods:
                return methods[method_name]
            for base in self.class_bases.get(cur, []):
                if base and base not in seen:
                    stack.append(base)
        return None

    def _inline_class_method(
        self,
        fn: ast.FunctionDef,
        call: ast.Call,
        expected_name: str | None,
        *,
        class_name: str,
        method_name: str,
    ) -> DataFrameIR | None:
        """Inline a class method body (v0.2 §8).

        The method's first parameter is ``self`` — bind it to nothing (just
        skip it) and align remaining params with call-site arguments.
        """
        if not fn.args.args:
            return None
        if not self._enter_inline(
            f"{class_name}.{method_name}", getattr(call, "lineno", None),
        ):
            return None
        label = f"{class_name}.{method_name}"
        binding = self._bind_call_args(fn, call, drop_self=True)
        self._emit_inline_warnings(fn, call, label, binding)
        scope: dict[str, DataFrameIR] = dict(self.symbols)
        scope.update(binding.df_scope)
        ir_pivot = len(self.ir.dataframes)
        caller_symbols = self.symbols
        with self._inline_overlays(binding):
            saved = self.symbols
            self.symbols = scope
            try:
                result: DataFrameIR | None = None
                for stmt in fn.body:
                    if isinstance(stmt, ast.Return):
                        result = self._resolve_tuple_return(stmt.value, expected_name)
                        break
                    self._visit_stmt(stmt, in_branch=False)
                if result is not None:
                    result.derives_from_dataframe.append(DataFrameEdgeIR(
                        source_var=label, via="class_method",
                    ))
                self._rewrite_param_edges(
                    ir_pivot, fn, call, caller_symbols, result,
                    drop_self=True,
                )
                return result
            finally:
                self.symbols = saved
                self._exit_inline()

    def _inline_external_function(
        self, fn_name: str, call: ast.Call, expected_name: str | None,
    ) -> DataFrameIR | None:
        """Inline a function imported from another module (v0.2 §1).

        Same shape as ``_inline_local_function`` but the FunctionDef comes from
        the cross-module ``external_functions`` table built by ``ProjectParser``.
        """
        fn = self.external_functions[fn_name]
        if not fn.args.args and fn.args.vararg is None and not fn.args.kwonlyargs:
            return None
        if not self._enter_inline(fn_name, getattr(call, "lineno", None)):
            return None
        binding = self._bind_call_args(fn, call)
        self._emit_inline_warnings(fn, call, fn_name, binding)
        scope: dict[str, DataFrameIR] = dict(self.symbols)
        scope.update(binding.df_scope)
        # Track param-name → original_caller_df so any write_edges the inlined
        # body attaches to the clone are merged back onto the caller's
        # DataFrame. Without this, sink functions like ``write_to_postgres(df)``
        # lose their write lineage entirely because the param clone is never
        # appended to ``ir.dataframes``.
        original_for: dict[str, DataFrameIR] = {}
        for arg_def, arg_val in zip(fn.args.args, call.args):
            if (
                isinstance(arg_val, ast.Name)
                and arg_val.id in self.symbols
                and arg_def.arg in binding.df_scope
            ):
                original_for[arg_def.arg] = self.symbols[arg_val.id]
        ir_pivot = len(self.ir.dataframes)
        caller_symbols = self.symbols
        with self._inline_overlays(binding):
            saved = self.symbols
            self.symbols = scope
            try:
                result: DataFrameIR | None = None
                for stmt in fn.body:
                    if isinstance(stmt, ast.Return):
                        result = self._resolve_tuple_return(stmt.value, expected_name)
                        break
                    self._visit_stmt(stmt, in_branch=False)
                if result is not None:
                    result.derives_from_dataframe.append(DataFrameEdgeIR(
                        source_var=fn_name, via="cross_module_function",
                    ))
                self._rewrite_param_edges(
                    ir_pivot, fn, call, caller_symbols, result,
                )
                # Propagate write_edges + writes_to from any param clone back
                # onto the caller's DataFrame so sink lineage isn't lost.
                for arg_name, original in original_for.items():
                    clone = scope.get(arg_name)
                    if clone is None or clone is original:
                        continue
                    if clone.write_edges:
                        original.write_edges.extend(clone.write_edges)
                    if clone.writes_to:
                        original.writes_to.extend(clone.writes_to)
                return result
            finally:
                self.symbols = saved
                self._exit_inline()

    # Class names that are *definitely not* DataFrame producers. Adding to
    # this set is cheaper than mis-classifying then trying to recover.
    _NON_DF_CONSTRUCTORS: frozenset[str] = frozenset({
        # PySpark schema types
        "StructType", "StructField",
        "StringType", "IntegerType", "LongType", "ShortType", "ByteType",
        "DoubleType", "FloatType", "DecimalType",
        "BooleanType", "DateType", "TimestampType", "BinaryType",
        "ArrayType", "MapType",
        # Common stdlib
        "dict", "list", "tuple", "set", "frozenset", "str", "int", "float",
        "bool", "bytes", "bytearray", "complex",
        "datetime", "date", "time", "timedelta",
        # Typing / dataclass / logging
        "Optional", "Union", "List", "Dict", "Tuple", "Set",
        "namedtuple", "field", "dataclass",
        "Logger", "getLogger",
    })

    def _looks_like_df_producing_call(self, call: ast.Call) -> bool:
        """True if an unknown bare-name call plausibly returns a DataFrame.

        Heuristic: the call must either (a) reference the spark session, or
        (b) take at least one already-tracked DataFrame as a positional or
        keyword argument. Calls to known type/utility constructors are
        rejected outright so they don't pollute `ir.dataframes`.
        """
        if not isinstance(call.func, ast.Name):
            return False
        fn_id = call.func.id
        if fn_id in self._NON_DF_CONSTRUCTORS:
            return False
        # Builtin / dunder names — unlikely to produce DataFrames.
        if fn_id in {"print", "len", "range", "enumerate", "zip", "map", "filter",
                     "open", "isinstance", "issubclass", "hasattr", "getattr",
                     "setattr", "id", "type", "repr", "format"}:
            return False
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            root = _root_name(arg)
            if not root:
                continue
            if self.spark_var and root == self.spark_var:
                return True
            if root in self.symbols:
                return True
        return False

    def _external_function_df(self, call: ast.Call, name: str | None) -> DataFrameIR:
        df = self._new_df(name or "__anon")
        df.derives_from_dataframe.append(DataFrameEdgeIR(via="external_function"))
        # Carry input arguments as parent DataFrames if they reference known vars.
        for arg in call.args:
            root = _root_name(arg)
            if root and root in self.symbols:
                parent = self.symbols[root]
                df.reads_from.extend(parent.reads_from)
        return df

    # ---- dataframe collapse (display layer) -----------------------------

    @staticmethod
    def _first_lineno(*nodes) -> int | None:
        """Return the first AST node's ``lineno`` from a sequence of inputs.
        Inputs may be a single node, a list of nodes, or ``None``. Used to
        stamp source-line provenance on TableIR/ConnectionIR/WriteEdgeIR/UDFIR
        instances so the frontend's source-code panel can scroll the user
        to the call-site that produced them.
        """
        for n in nodes:
            if n is None:
                continue
            if hasattr(n, "lineno"):
                return n.lineno
            if isinstance(n, list):
                for item in n:
                    if hasattr(item, "lineno"):
                        return item.lineno
        return None

    def _stamp_read_line(self, tbl, *nodes) -> None:
        """Stamp ``tbl.line`` (and its connection.line) from the first
        non-None AST node with a ``lineno``. Idempotent — won't overwrite
        a line already set by an upstream constructor."""
        line = self._first_lineno(*nodes)
        if line is None:
            return
        if tbl.line is None:
            tbl.line = line
        if tbl.connection is not None and tbl.connection.line is None:
            tbl.connection.line = line

    def _record_chain_step(
        self,
        df: DataFrameIR,
        *,
        op: str,
        kind: str,
        call: ast.AST | None = None,
        expr: str | None = None,
        output_column: str | None = None,
        output_columns: list[str] | None = None,
        input_columns: list[str] | None = None,
        join_other: str | None = None,
        join_keys: list[str] | None = None,
        join_how: str | None = None,
    ) -> None:
        """Append one ``TransformStepIR`` to the intermediate DataFrame's
        chain. Each chain step the visitor produces records exactly one
        entry so the collapse pass can fold them into the downstream
        anchor in source order.
        """
        if df is None:
            return
        line = getattr(call, "lineno", None)
        step = TransformStepIR(
            seq=len(df.transform_chain),
            op=op,
            kind=kind,
            line=line,
            expr=expr,
            output_column=output_column,
            output_columns=list(output_columns or []),
            input_columns=list(input_columns or []),
            join_other=join_other,
            join_keys=list(join_keys or []),
            join_how=join_how,
        )
        df.transform_chain.append(step)

    # Anchors are the user-visible DataFrames; intermediates collapse into
    # their downstream anchor's transform_chain. See plan §1 for the rules.
    _META_OPS: frozenset[str] = frozenset({
        "cache", "persist", "checkpoint", "dropDuplicates", "distinct",
        "limit", "orderBy", "sort", "repartition", "coalesce", "hint",
    })

    def _collapse_to_anchors(self) -> None:
        """Post-pass: mark anchor DataFrames + fold intermediates into chains.

        Runs after the visitor finishes walking the module. The granular IR
        (``ir.dataframes``) is preserved verbatim — we only flip
        ``is_anchor`` and accumulate ``transform_chain`` / ``input_anchor_ids``
        on the user-visible nodes. The writer + stats then expose only
        anchors to consumers.
        """
        dfs = self.ir.dataframes
        if not dfs:
            return

        # Name-only lookup kept as a fallback for edges whose ``source_id``
        # is missing (external functions, conditional placeholders).
        by_var: dict[str, DataFrameIR] = {}
        for df in dfs:
            by_var.setdefault(df.var_name, df)
        # ``source_id`` resolution — exact identity. Required for variables
        # reassigned multiple times in the same script: ``by_var`` would
        # collapse them all onto the first occurrence.
        by_id: dict[str, DataFrameIR] = {df.id: df for df in dfs if df.id}

        def resolve(edge) -> DataFrameIR | None:
            """Prefer source_id (exact); fall back to source_var (last-known)."""
            if edge.source_id:
                hit = by_id.get(edge.source_id)
                if hit is not None:
                    return hit
            if edge.source_var:
                return by_var.get(edge.source_var)
            return None

        temp_view_dfs = {id(v) for v in self.temp_views.values()}

        # ----- 1. count downstream consumers per source var name --------
        # A DataFrame with >= 2 downstream consumers is a fork and must
        # stay its own anchor so each downstream branch can attach.
        downstream_count: dict[str, int] = {}
        for df in dfs:
            for edge in df.derives_from_dataframe:
                if edge.source_var:
                    downstream_count[edge.source_var] = (
                        downstream_count.get(edge.source_var, 0) + 1
                    )

        # ----- 2. classify each DataFrame ------------------------------
        # ``_chain_step`` propagates ``reads_from`` forward so every
        # intermediate inherits its source's read list. That makes a naive
        # ``bool(df.reads_from)`` check classify EVERY intermediate as an
        # IO source. The real signal for "this DF is the direct result of
        # a ``spark.read.X`` call" is: it has reads AND no upstream
        # DataFrame predecessor.
        for df in dfs:
            is_named = not df.is_anonymous
            is_io_source = (
                bool(df.reads_from) and not df.derives_from_dataframe
            )
            is_temp_view = id(df) in temp_view_dfs
            is_fork = downstream_count.get(df.var_name, 0) >= 2
            df.is_anchor = bool(
                is_named or is_io_source or is_temp_view or is_fork
            )

        # ----- 3. migrate writes from intermediates to upstream anchor --
        # The ``.write.X(...)`` chain creates an anonymous intermediate
        # whose ``write_edges`` / ``writes_to`` carry the sink. Display-wise
        # the user wrote ``enriched.write…`` and expects the write attributed
        # to ``enriched``. Promote every non-anchor's writes onto its
        # upstream anchor BEFORE we walk the chain so the chain doesn't
        # also pick up the writer-state stash methods (``.write``/``.mode``
        # etc.) as transform steps.
        for df in dfs:
            if df.is_anchor:
                continue
            if not (df.write_edges or df.writes_to):
                continue
            upstream = self._first_upstream_anchor(df, by_var, by_id)
            if upstream is None:
                continue
            for edge in df.write_edges:
                upstream.write_edges.append(edge)
            for tbl in df.writes_to:
                upstream.writes_to.append(tbl)
            df.write_edges = []
            df.writes_to = []

        # ----- 4. walk intermediates → anchor chain --------------------
        # For every anchor, walk backwards through ``derives_from_dataframe``
        # until we hit another anchor. Prepend every traversed intermediate's
        # own chain (single step) to the anchor's transform_chain so the
        # final order is upstream-anchor → anchor.
        # ``seen`` prevents revisiting if a fork made an intermediate
        # reachable from two different anchors (shouldn't happen because
        # forks become anchors, but be defensive).
        for anchor in dfs:
            if not anchor.is_anchor:
                continue
            self._walk_back_to_anchor(anchor, by_var, by_id)

        # ----- 4. column count + line range ----------------------------
        for anchor in dfs:
            if not anchor.is_anchor:
                continue
            anchor.column_count = len(anchor.fields)  # type: ignore[attr-defined]
            step_lines = [
                s.line for s in anchor.transform_chain if s.line is not None
            ]
            if step_lines:
                anchor.line_range = (min(step_lines), max(step_lines))

    # Writer-chain method names that the visitor inserts as intermediates
    # but which should NEVER appear in a display transform_chain — they're
    # plumbing for the sink, not user transformations.
    _WRITER_CHAIN_VIAS: frozenset[str] = frozenset({
        "write", "writeStream", "format", "mode", "option", "options",
        "partitionBy", "outputMode", "queryName", "trigger", "foreachBatch",
    })

    def _first_upstream_anchor(
        self,
        df: DataFrameIR,
        by_var: dict[str, DataFrameIR],
        by_id: dict[str, DataFrameIR] | None = None,
    ) -> DataFrameIR | None:
        """Walk ``derives_from_dataframe`` predecessors until an anchor
        appears. Returns ``None`` for a DataFrame with no path to an
        anchor (degenerate case).
        """
        seen: set[int] = {id(df)}
        stack: list[DataFrameIR] = [df]
        while stack:
            cur = stack.pop()
            for edge in cur.derives_from_dataframe:
                src = self._resolve_edge(edge, by_var, by_id)
                if src is None or id(src) in seen:
                    continue
                seen.add(id(src))
                if src.is_anchor:
                    return src
                stack.append(src)
        return None

    def _resolve_edge(
        self,
        edge,
        by_var: dict[str, DataFrameIR],
        by_id: dict[str, DataFrameIR] | None,
    ) -> DataFrameIR | None:
        """Resolve an edge to a concrete DataFrameIR. Prefer ``source_id``
        (exact identity, survives variable reassignment) and fall back to
        ``source_var`` for edges produced before id stamping or by paths
        that don't have a concrete source (external functions, etc.)."""
        if by_id is not None and getattr(edge, "source_id", None):
            hit = by_id.get(edge.source_id)
            if hit is not None:
                return hit
        if edge.source_var:
            return by_var.get(edge.source_var)
        return None

    def _walk_back_to_anchor(
        self,
        anchor: DataFrameIR,
        by_var: dict[str, DataFrameIR],
        by_id: dict[str, DataFrameIR] | None = None,
    ) -> None:
        """Walk ``anchor.derives_from_dataframe`` predecessors, prepending
        intermediates' chain steps until another anchor is hit.

        Multiple predecessors (joins / unions) each contribute their
        upstream anchor to ``input_anchor_ids`` and their own chain
        back-walk.
        """
        seen: set[int] = {id(anchor)}
        # Buffer the chain in reverse so we can ``extendleft`` cleanly.
        # We then prepend the buffered chain in front of anchor's own steps
        # (which were recorded when the anchor itself was the result of a
        # named-variable chain step like ``claims = df.filter(...)``).
        from collections import deque
        chain_prefix: deque[TransformStepIR] = deque()
        input_anchors: list[str] = []

        def visit(edge) -> None:
            src = self._resolve_edge(edge, by_var, by_id)
            if src is None or id(src) in seen:
                return
            seen.add(id(src))
            if src.is_anchor:
                if src.id and src.id not in input_anchors:
                    input_anchors.append(src.id)
                return
            # Intermediate — splice its single chain step into the prefix
            # (intermediates record exactly one step each via
            # ``_record_chain_step``). Then recurse to its own predecessors.
            # Skip writer-chain plumbing (``.write``/``.mode``/etc.) —
            # those landed via ``_chain_step(..., via="write")`` but should
            # not be surfaced as user transformations.
            for s in reversed(src.transform_chain):
                if s.op in self._WRITER_CHAIN_VIAS:
                    continue
                chain_prefix.appendleft(s)
            for upstream_edge in src.derives_from_dataframe:
                visit(upstream_edge)

        for edge in anchor.derives_from_dataframe:
            visit(edge)

        if chain_prefix:
            existing = list(anchor.transform_chain)
            merged = list(chain_prefix) + existing
            # Re-sequence so seq is monotonic across the merged chain.
            for i, step in enumerate(merged):
                step.seq = i
            anchor.transform_chain = merged
        anchor.input_anchor_ids = input_anchors

    # ---- bookkeeping ---------------------------------------------------

    def _new_df(self, name: str) -> DataFrameIR:
        if name == "__anon" or not name:
            self.anon_counter += 1
            name = f"__anon_{self.anon_counter}"
            df = DataFrameIR(var_name=name, is_anonymous=True, creation_order=self._next_order(name))
        else:
            df = DataFrameIR(var_name=name, creation_order=self._next_order(name))
        df.id = self._mint_df_id(name, df.creation_order)
        if df.is_anonymous:
            self.ir.dataframes.append(df)
        return df

    def _next_order(self, name: str) -> int:
        n = self.version_counts.get(name, 0)
        self.version_counts[name] = n + 1
        return n

    def _mint_df_id(self, name: str, order: int | None = None) -> str:
        order = order if order is not None else self.version_counts.get(name, 0) - 1
        return dataframe_id(script_id=self.ir.id, var_name=name, creation_order=order)

    def _clone_for_reassignment(self, src: DataFrameIR, target: str) -> DataFrameIR:
        df = self._new_df(target)
        df.reads_from = list(src.reads_from)
        df.fields = list(src.fields)
        df.derives_from_dataframe.append(DataFrameEdgeIR(
            source_var=src.var_name, source_id=src.id, via="alias",
        ))
        df.lineage_conditional = src.lineage_conditional
        df.lineage_partial = src.lineage_partial
        # v0.2 §5 / §6 — preserve provenance + enterprise hints across alias.
        df.renames = list(src.renames)
        df.cached = src.cached
        df.persist_level = src.persist_level
        df.checkpointed = src.checkpointed
        df.partition_count = src.partition_count
        df.partition_columns = list(src.partition_columns)
        df.broadcast_hint = src.broadcast_hint
        # Preserve writer-chain state across ``writer = df.write.format(...)``
        # → terminal ``.save(path)`` in a separate statement.
        ws = getattr(src, "_writer_state", None)
        if ws is not None:
            df._writer_state = {  # type: ignore[attr-defined]
                "format": ws.get("format"),
                "mode": ws.get("mode"),
                "options": dict(ws.get("options") or {}),
                "partition_cols": list(ws.get("partition_cols") or []),
                "streaming": ws.get("streaming"),
                "foreach_batch_callback": ws.get("foreach_batch_callback"),
            }
        # Reader-chain stash survives ``r = spark.read.format(...)`` →
        # later ``r.option(...).load()`` in a separate statement.
        rs = getattr(src, "_reader_state", None)
        if rs is not None:
            df._reader_state = {  # type: ignore[attr-defined]
                "format": rs.get("format"),
                "options": dict(rs.get("options") or {}),
                "location": rs.get("location"),
                "table_arg": rs.get("table_arg"),
                "streaming": rs.get("streaming"),
                "unresolved_source": rs.get("unresolved_source"),
                "unresolved_detail": rs.get("unresolved_detail"),
            }
        return df

    def _capture_class(self, node: ast.ClassDef) -> None:
        """Record methods + bases for v0.2 §8 procedural-abstraction inlining.

        Also harvest literal class-level attributes (``GOLD = "s3a://…"``,
        ``PARTS = ["a", "b"]``) so call-site refs like ``cfg.GOLD`` resolve
        through ``_resolve_str`` once the instance is bound.

        Each attribute resolves in a scope where sibling class attrs are
        bare-name visible — mirroring Python's own class-body scoping. We
        push accumulated str attrs into ``string_constants`` for the loop
        and restore on exit.
        """
        methods: dict[str, ast.FunctionDef] = {}
        attrs: dict[str, object] = {}
        properties: dict[str, ast.AST] = {}
        saved_str_consts = dict(self.string_constants)
        saved_list_consts = dict(self.list_constants)
        try:
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    methods[child.name] = child
                    # @property best-effort — single ``return <expr>`` body.
                    if any(
                        _decorator_name(d) == "property" for d in child.decorator_list
                    ) and len(child.body) == 1 and isinstance(child.body[0], ast.Return):
                        ret = child.body[0].value
                        if ret is not None:
                            properties[child.name] = ret
                    continue
                if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                    continue
                target = (
                    child.targets[0] if isinstance(child, ast.Assign) and child.targets
                    else (child.target if isinstance(child, ast.AnnAssign) else None)
                )
                value = child.value
                if value is None or not isinstance(target, ast.Name):
                    continue
                resolved = self._resolve_str(value)
                if resolved is not None:
                    attrs[target.id] = resolved
                    self.string_constants[target.id] = resolved
                    continue
                resolved_list = self._resolve_list_value(value)
                if resolved_list is not None:
                    attrs[target.id] = resolved_list
                    if all(isinstance(x, str) for x in resolved_list):
                        self.list_constants[target.id] = list(resolved_list)
                    continue
                v = _const_value(value)
                if v is not None:
                    attrs[target.id] = v
        finally:
            self.string_constants = saved_str_consts
            self.list_constants = saved_list_consts
        self.class_methods[node.name] = methods
        self.class_bases[node.name] = [
            _decorator_name(b) or "" for b in node.bases
        ]
        self.class_attributes[node.name] = attrs
        if properties:
            self.class_properties[node.name] = properties

    def _maybe_record_hof_return(self, node: ast.FunctionDef) -> None:
        """Detect ``def make_xform(c): def _xf(df): ...; return _xf``.

        If the function defines a nested function and returns its name, store
        the inner ``FunctionDef`` keyed under the outer function's name so
        ``_eval_call`` can inline at the call site (``xf = make_xform("c"); df2 = xf(df)``).
        """
        nested: dict[str, ast.FunctionDef] = {}
        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef):
                nested[stmt.name] = stmt
        # Walk top-level ``return`` statements (not those inside nested defs).
        for stmt in node.body:
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                inner = nested.get(stmt.value.id)
                if inner is not None:
                    self.hof_returns[node.name] = inner
                    return

    def _capture_import(self, node: ast.Import) -> None:
        """Record `import X` / `import X as Y` / `import a.b.c` edges (v0.2 §1)."""
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.imported_symbols[local] = {
                "kind": "import",
                "module": alias.name,
                "level": 0,
                "original_symbol": alias.name,
            }
            self.ir.imports.append(ImportEdgeIR(
                from_script_id=self.ir.id,
                symbol=local,
                kind="import",
                module=alias.name,
                line=node.lineno,
            ))

    def _capture_import_from(self, node: ast.ImportFrom) -> None:
        """Record `from X import Y` / `from .X import Y` edges (v0.2 §1)."""
        module = node.module  # may be None for `from . import x`
        level = node.level or 0
        for alias in node.names:
            if alias.name == "*":
                # Star import — record the edge but the symbol is unresolvable.
                self.ir.imports.append(ImportEdgeIR(
                    from_script_id=self.ir.id,
                    symbol="*",
                    kind="from",
                    module=("." * level + (module or "")),
                    line=node.lineno,
                ))
                continue
            local = alias.asname or alias.name
            self.imported_symbols[local] = {
                "kind": "from",
                "module": module,
                "level": level,
                "original_symbol": alias.name,
            }
            self.ir.imports.append(ImportEdgeIR(
                from_script_id=self.ir.id,
                symbol=local,
                kind="from",
                module=("." * level + (module or "")),
                line=node.lineno,
            ))

    def _maybe_capture_string_constant(self, node: ast.Assign) -> None:
        if not node.targets or not isinstance(node.targets[0], ast.Name):
            return
        name = node.targets[0].id
        resolved = self._resolve_str(node.value)
        if resolved is not None:
            self.string_constants[name] = resolved
            return
        # Runtime / secret source captured at module level so subsequent
        # ``.option("url", name)`` calls can attribute the unresolved value
        # back to env / secrets / runtime configs.
        rt = self._classify_unresolved(node.value)
        if rt[0] and rt[0] != "dynamic":
            self.runtime_sources[name] = rt
            return
        resolved_list = self._resolve_list_value(node.value)
        if resolved_list is not None:
            self.list_constants[name] = resolved_list
            return
        # Dict literal at module level — ``CONFIG = {"pg_url": "...", ...}``.
        # Values stay as AST nodes so we can re-resolve them in the active
        # overlay (the same dict may hold references to other module-level
        # constants we haven't seen yet).
        if isinstance(node.value, ast.Dict):
            entries: dict[str, ast.AST] = {}
            for k, v in zip(node.value.keys, node.value.values):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and v is not None
                ):
                    entries[k.value] = v
            if entries:
                self.dict_constants[name] = entries

    # ---- value resolution shared by call-binding and write extraction ----

    def _resolve_str(self, node: ast.AST | None) -> str | None:
        """Resolve a single AST node to a string when possible.

        Recognises ``"literal"``, ``Name`` refs into ``self.string_constants``,
        ``cfg.GOLD`` attribute paths into class-level attributes, and
        f-strings whose pieces are themselves resolvable. Returns ``None``
        when the node is genuinely dynamic at parse time.
        """
        if node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.string_constants:
                return self.string_constants[node.id]
            return None
        if isinstance(node, ast.Attribute):
            # Walk back to the root for ``cfg.GOLD`` / ``PipelineConfig.GOLD`` /
            # nested ``a.b.GOLD`` patterns.
            attr_path: list[str] = [node.attr]
            cur: ast.AST = node.value
            while isinstance(cur, ast.Attribute):
                attr_path.append(cur.attr)
                cur = cur.value
            if not isinstance(cur, ast.Name):
                return None
            attr_path.reverse()
            root = cur.id
            # Direct class reference: ``PipelineConfig.GOLD``.
            cls_name = root if root in self.class_attributes else self.instance_types.get(root)
            if cls_name is None or cls_name not in self.class_attributes:
                return None
            value: object | None = self.class_attributes[cls_name]
            for part in attr_path:
                if not isinstance(value, dict) or part not in value:
                    return None
                value = value[part]
            return value if isinstance(value, str) else None
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for piece in node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append(piece.value)
                elif isinstance(piece, ast.FormattedValue):
                    inner_str = self._resolve_str(piece.value)
                    if inner_str is None:
                        return None
                    parts.append(inner_str)
                else:
                    return None
            return "".join(parts)
        # ``BASE + "/path"`` concatenation, and ``"%s/%s" % (a, b)`` formatting.
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add):
                left = self._resolve_str(node.left)
                right = self._resolve_str(node.right)
                if left is not None and right is not None:
                    return left + right
                return None
            if isinstance(node.op, ast.Mod):
                fmt = self._resolve_str(node.left)
                if fmt is None:
                    return None
                # RHS is either a tuple of values or a single value.
                rhs = node.right
                values: list[ast.AST]
                if isinstance(rhs, ast.Tuple):
                    values = list(rhs.elts)
                else:
                    values = [rhs]
                resolved_vals: list[str] = []
                for v in values:
                    r = self._resolve_str(v)
                    if r is None:
                        # Try plain int/float constants too — they format fine.
                        const = _const_value(v)
                        if const is None:
                            return None
                        r = str(const)
                    resolved_vals.append(r)
                try:
                    return fmt % tuple(resolved_vals)
                except (TypeError, ValueError):
                    return None
        # ``"{}/path".format(bucket)`` / ``"{0}".format(x)``.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "format":
                fmt = self._resolve_str(node.func.value)
                if fmt is None:
                    return None
                args: list[str] = []
                for v in node.args:
                    r = self._resolve_str(v)
                    if r is None:
                        const = _const_value(v)
                        if const is None:
                            return None
                        r = str(const)
                    args.append(r)
                kw_args: dict[str, str] = {}
                for kw in node.keywords:
                    if kw.arg is None:
                        continue
                    r = self._resolve_str(kw.value)
                    if r is None:
                        const = _const_value(kw.value)
                        if const is None:
                            return None
                        r = str(const)
                    kw_args[kw.arg] = r
                try:
                    return fmt.format(*args, **kw_args)
                except (KeyError, IndexError, ValueError):
                    return None
        # ``CONFIG["pg_url"]`` and ``os.environ["X"]`` (the latter is unresolved
        # at parse time; classify catches it before we get here, but if the
        # key lookup hits a tracked dict we resolve it).
        if isinstance(node, ast.Subscript):
            base = node.value
            key_node = node.slice
            key = self._resolve_str(key_node) if key_node is not None else None
            if (
                isinstance(base, ast.Name)
                and base.id in self.dict_constants
                and key is not None
            ):
                entry = self.dict_constants[base.id].get(key)
                if entry is not None:
                    return self._resolve_str(entry)
        # ``inst.prop`` where prop was registered as an @property returning a
        # statically-resolvable expression.
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            root_name = node.value.id
            cls_name = self.instance_types.get(root_name) or (
                root_name if root_name in self.class_properties else None
            )
            if cls_name and node.attr in self.class_properties.get(cls_name, {}):
                return self._resolve_str(self.class_properties[cls_name][node.attr])
        # ``os.getenv("X", "default-literal")`` resolves to the default only
        # when the default is a literal string — same as Python's runtime
        # behaviour when the env var is missing. ``classify_unresolved`` is
        # called separately for the env-var node itself; here we just give
        # the resolver the literal-default fallback when present.
        if isinstance(node, ast.Call):
            for pattern in (("os", "getenv"),):
                if _call_matches_attr_path(node, pattern) and len(node.args) >= 2:
                    return self._resolve_str(node.args[1])
        return None

    def _resolve_list_value(self, node: ast.AST | None) -> list[object] | None:
        """Resolve a list/tuple literal (or known Name → list) to its items.

        Returns a Python list when every element is statically resolvable to
        a string (`["a", "b"]`, `[cfg.X, "y"]`, …) or a primitive constant.
        Returns ``None`` when any element is dynamic so the caller can fall
        back to its old behaviour.
        """
        if node is None:
            return None
        if isinstance(node, (ast.List, ast.Tuple)):
            items: list[object] = []
            for elt in node.elts:
                s = self._resolve_str(elt)
                if s is not None:
                    items.append(s)
                    continue
                v = _const_value(elt)
                if v is not None:
                    items.append(v)
                    continue
                return None
            return items
        if isinstance(node, ast.Name) and node.id in self.list_constants:
            return list(self.list_constants[node.id])
        return None

    def _handle_terminal_methods(self, chain: list[ast.AST], df: DataFrameIR) -> None:
        for step in chain[1:]:
            if isinstance(step, ast.Call):
                method = _call_attr_name(step)
                if method == "createOrReplaceTempView":
                    self._register_temp_view(df, step)
                elif method in _TERMINAL_WRITE_METHODS:
                    self._apply_write(df, chain, method, step)
                    return
                elif method in _PASSTHROUGH_METHODS or method == "hint":
                    # v0.2 §6 — enterprise hints applied directly to a read
                    # (``spark.table("t").cache()``) tag the read DataFrame in
                    # place rather than creating a new node, since the read IR
                    # *is* the named LHS in this pattern.
                    _apply_runtime_hint(df, method, step)

    # ---- UDF emission --------------------------------------------------

    def _maybe_capture_udf_factory(self, var_name: str, value: ast.AST) -> bool:
        """Match ``var = udf(fn, ReturnType())`` / ``pandas_udf(...)`` calls.

        Returns True if a UDF was registered. The variable name becomes the
        UDF's name because that's what later call sites reference, e.g.
        ``hash_email_udf(col("email"))``.
        """
        if not isinstance(value, ast.Call):
            return False
        fn = value.func
        fn_name: str | None = None
        if isinstance(fn, ast.Name):
            fn_name = fn.id
        elif isinstance(fn, ast.Attribute):
            fn_name = fn.attr
        if fn_name not in {"udf", "pandas_udf"}:
            return False
        is_pandas = fn_name == "pandas_udf"
        return_type: str | None = None
        for kw in value.keywords:
            if kw.arg == "returnType":
                return_type = _unparse(kw.value)
        if return_type is None and len(value.args) >= 2:
            return_type = _unparse(value.args[1])
        if return_type:
            return_type = return_type.lower()
        # Best-effort: if the wrapped python function name was captured by
        # _emit_udf already (decorator form), don't double-register.
        if any(u.name == var_name for u in self.ir.udfs):
            return True
        self.ir.udfs.append(UDFIR(
            name=var_name, is_pandas_udf=is_pandas, return_type=return_type,
            line=getattr(value, "lineno", None),
        ))
        return True

    def _emit_udf(self, fn: ast.FunctionDef) -> None:
        is_pandas = False
        return_type: str | None = None
        for dec in fn.decorator_list:
            name = _decorator_name(dec)
            if name == "pandas_udf":
                is_pandas = True
            elif name == "udf":
                is_pandas = False
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "returnType":
                        return_type = _unparse(kw.value)
                for pos in dec.args:
                    rt = _unparse(pos)
                    return_type = return_type or rt
        # Keep the original casing+parens lowercased so e.g. ``StringType()``
        # round-trips as ``stringtype()`` — the test contract accepts either
        # ``string`` or ``stringtype()``.
        if return_type:
            return_type = return_type.lower()
        self.ir.udfs.append(UDFIR(
            name=fn.name, is_pandas_udf=is_pandas, return_type=return_type,
            line=getattr(fn, "lineno", None),
        ))


# ---------------------------------------------------------------------------
# AST helpers (free functions — no parser state)
# ---------------------------------------------------------------------------

def _flatten_call_chain(call: ast.AST) -> list[ast.AST]:
    """Walk a method-chain expression and return nodes in source-text order.

    Examples:
        spark.read.format("parquet").load("p")
        → [Name('spark'), Attribute('read'), Call(format), Call(load)]

        df.select(...).filter(...).saveAsTable("t")
        → [Name('df'), Call(select), Call(filter), Call(saveAsTable)]

    The first element is always the chain's root (a Name or an outer Call
    like ``transform(df)``). Subsequent elements are method calls or bare
    attribute accesses in source order.
    """
    chain: list[ast.AST] = []
    cur: ast.AST | None = call
    while cur is not None:
        chain.append(cur)
        if isinstance(cur, ast.Call):
            func = cur.func
            if isinstance(func, ast.Attribute):
                cur = func.value
                continue
            break
        if isinstance(cur, ast.Attribute):
            cur = cur.value
            continue
        break
    return list(reversed(chain))


def _call_attr_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _const_arg(call: ast.Call, idx: int) -> str | None:
    if len(call.args) <= idx:
        return None
    v = _const_value(call.args[idx])
    return v if isinstance(v, str) else None


def _const_value(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _resolve_str_arg(
    call: ast.Call, idx: int, string_constants: dict[str, str], *, allow_joined: bool = False,
) -> str | None:
    if len(call.args) <= idx:
        return None
    arg = call.args[idx]
    v = _const_value(arg)
    if isinstance(v, str):
        return v
    if isinstance(arg, ast.Name) and arg.id in string_constants:
        return string_constants[arg.id]
    if isinstance(arg, ast.JoinedStr):
        # f-string — resolve constant components, fail otherwise
        parts: list[str] = []
        for v in arg.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                inner = v.value
                if isinstance(inner, ast.Name) and inner.id in string_constants:
                    parts.append(string_constants[inner.id])
                elif isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    parts.append(inner.value)
                else:
                    return None
        return "".join(parts) if parts else None
    return None


def _matches_path(node: ast.AST, parts: tuple[str, ...]) -> bool:
    cur = node
    parts_list = list(parts)
    while isinstance(cur, ast.Attribute) and parts_list:
        if cur.attr != parts_list[-1]:
            return False
        parts_list.pop()
        cur = cur.value
    if parts_list and isinstance(cur, ast.Name):
        return cur.id == parts_list[0] and len(parts_list) == 1
    return False


def _chain_root_is(chain: list[ast.AST], var: str) -> bool:
    return bool(chain) and isinstance(chain[0], ast.Name) and chain[0].id == var


def _is_spark_read_chain(chain: list[ast.AST], spark_var: str) -> bool:
    if not _chain_root_is(chain, spark_var):
        return False
    # Look for `.read` or `.readStream` Attribute. Streaming reads use exactly
    # the same option/format/load shape as batch reads — surfacing them here
    # means everything downstream (path resolution, format dispatch, connection
    # extraction) just works.
    for node in chain[1:4]:
        if isinstance(node, ast.Attribute) and node.attr in {"read", "readStream"}:
            return True
    return False


def _matches_attr_path(node: ast.AST, parts: tuple[str, ...]) -> bool:
    """``True`` when ``node`` is ``parts[0].parts[1]...parts[-1]`` exactly.

    ``parts=("os","environ")`` matches the *expression* ``os.environ`` but
    not ``os`` alone or ``os.environ.copy``. Used by the runtime/secret
    classifier so we can spot ``os.environ[X]``, ``spark.conf.get(...)`` etc.
    structurally rather than by variable name.
    """
    if len(parts) == 1:
        return isinstance(node, ast.Name) and node.id == parts[0]
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr != parts[-1]:
        return False
    return _matches_attr_path(node.value, parts[:-1])


def _call_matches_attr_path(call: ast.Call, parts: tuple[str, ...]) -> bool:
    return _matches_attr_path(call.func, parts)


def _is_rdd_read_chain(chain: list[ast.AST]) -> bool:
    """``sc.textFile(...)`` / ``sc.wholeTextFiles(...)`` etc.

    Matches when the root is a bare Name (``sc`` / ``spark_context`` /
    anything else — we don't fix the var name) AND the first Call in the
    chain is one of the registered RDD read methods. Variable-name
    heuristics would violate the "no hardcoded names" rule.
    """
    if not chain or not isinstance(chain[0], ast.Name):
        return False
    if len(chain) < 2 or not isinstance(chain[1], ast.Call):
        return False
    method = _call_attr_name(chain[1])
    return method in _RDD_READ_METHODS


def _is_spark_table_call(chain: list[ast.AST], spark_var: str) -> bool:
    if not _chain_root_is(chain, spark_var):
        return False
    if len(chain) >= 2 and isinstance(chain[1], ast.Call):
        return _call_attr_name(chain[1]) == "table"
    return False


def _is_spark_sql_call(chain: list[ast.AST], spark_var: str) -> bool:
    if not _chain_root_is(chain, spark_var):
        return False
    if len(chain) >= 2 and isinstance(chain[1], ast.Call):
        return _call_attr_name(chain[1]) == "sql"
    return False


def _attr_path(attr: ast.Attribute) -> list[str]:
    parts: list[str] = []
    cur: ast.AST = attr
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return parts


def _root_name(node: ast.AST) -> str | None:
    cur = node
    while True:
        if isinstance(cur, ast.Name):
            return cur.id
        if isinstance(cur, ast.Attribute):
            cur = cur.value
            continue
        if isinstance(cur, ast.Call):
            cur = cur.func
            continue
        return None


def _first_tuple_return(fn: ast.FunctionDef) -> ast.Tuple | ast.List | None:
    """Return the first top-level `Return` whose value is a tuple/list, or None.

    Used by M2a's tuple-LHS handling to decide whether to expect multi-bind.
    Only the first return is inspected — a function with mixed return shapes
    is treated as tuple-returning if any path returns a tuple.
    """
    for node in fn.body:
        if isinstance(node, ast.Return) and isinstance(
            node.value, (ast.Tuple, ast.List),
        ):
            return node.value
    # Walk nested If/For blocks too — best-effort.
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Return) and isinstance(
            sub.value, (ast.Tuple, ast.List),
        ):
            return sub.value
    return None


def _column_ref_name(arg: ast.AST) -> str | None:
    """`"region"` / `col("region")` / `df.region` → "region".

    For a chain of ``.alias("x").alias("y")``, the *outermost* alias is the
    canonical target — that's what the resulting DataFrame's column ends up
    called. Inner aliases are captured separately via ``_alias_chain``.
    """
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Call):
        # Outermost .alias("name") wins — strip it so the inner ref decides
        # the source column.
        if _call_attr_name(arg) == "alias":
            inner = _const_arg(arg, 0)
            if inner:
                return inner
        name = _call_attr_name(arg) or (
            arg.func.id if isinstance(arg.func, ast.Name) else None
        )
        if name == "col" and arg.args:
            v = _const_value(arg.args[0])
            if isinstance(v, str):
                return v
    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
        return arg.attr
    return None


# ---------------------------------------------------------------------------
# M3a/b helpers — wildcard select, chained alias, selectExpr SQL parsing
# ---------------------------------------------------------------------------

def _is_star_arg(arg: ast.AST) -> bool:
    """Match ``df.select("*")`` and ``df.select(*cols)``."""
    if isinstance(arg, ast.Constant) and arg.value == "*":
        return True
    if isinstance(arg, ast.Starred):
        return True
    return False


def _alias_chain(arg: ast.AST) -> list[str]:
    """Return every alias label seen walking outward through ``.alias(...)`` calls.

    For ``col("x").alias("a").alias("b")`` returns ``["a", "b"]`` (innermost
    first). Empty list when the expression isn't a chain.
    """
    out: list[str] = []
    cur = arg
    # Walk outward — outermost is the last .alias call applied.
    outer_chain: list[str] = []
    while isinstance(cur, ast.Call) and _call_attr_name(cur) == "alias":
        label = _const_arg(cur, 0)
        if label:
            outer_chain.append(label)
        cur = cur.func.value if isinstance(cur.func, ast.Attribute) else cur
        # Avoid infinite loop on malformed AST.
        if cur is arg:
            break
    # `outer_chain` is outermost-first; reverse to put innermost first.
    out = list(reversed(outer_chain))
    return out


def _parse_selectexpr(expr_str: str) -> tuple[str | None, list[str]] | None:
    """Parse a ``selectExpr`` argument like ``"amount * 1.18 AS taxed"``.

    Returns ``(target_column, source_columns)`` or ``None`` if the string is
    a plain bare column name (caller's fast path handles those). Returns
    ``("*", ["*"])`` for ``"*"``. On parse failure returns ``None`` and lets
    the bare-name fallback try.
    """
    s = expr_str.strip()
    if not s:
        return None
    if s == "*":
        return ("*", ["*"])
    # A plain column name has no operators / no AS / no parens. Let the
    # downstream bare-name path handle it for symmetry with ``select``.
    if (
        " AS " not in s.upper()
        and not any(c in s for c in "+-*/%(),")
        and " " not in s
    ):
        return None
    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp

        tree = sqlglot.parse_one(f"SELECT {s} FROM __dummy__", read="spark")
    except Exception:
        return None
    if tree is None:
        return None
    # The single projection lives under SELECT.expressions[0].
    projections = list(tree.find_all(sqlglot_exp.Select))
    if not projections:
        return None
    select_node = projections[0]
    exprs = select_node.expressions or []
    if not exprs:
        return None
    proj = exprs[0]
    # Target = alias name if present, otherwise the projection's own name.
    target: str | None = None
    if isinstance(proj, sqlglot_exp.Alias):
        target = proj.alias_or_name
        inner = proj.this
    else:
        target = proj.alias_or_name or None
        inner = proj
    sources: list[str] = []
    seen: set[str] = set()
    for col in (inner.find_all(sqlglot_exp.Column) if inner is not None else []):
        nm = col.name
        if nm and nm not in seen and nm != target:
            seen.add(nm)
            sources.append(nm)
    return (target, sources)


def _columns_in_expression(expr: ast.AST) -> tuple[list[str], str, str | None]:
    """Walk an expression sub-tree, pull out referenced columns.

    Returns ``(source_columns, via, udf_name?)`` where ``via`` is one of
    ``withColumn`` or ``udf`` if the outer call site is a UDF.
    """
    sources: list[str] = []
    seen: set[str] = set()
    udf_name: str | None = None
    via = "withColumn"

    # Detect outer call being a UDF: a Call whose func is a Name that isn't
    # `col`/`lit`/`when`/etc. — i.e. a user-defined function.
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        if expr.func.id not in _BUILTIN_FUNCTIONS:
            udf_name = expr.func.id
            via = "udf"

    for node in ast.walk(expr):
        if isinstance(node, ast.Call):
            name = (
                node.func.id if isinstance(node.func, ast.Name)
                else (_call_attr_name(node) or "")
            )
            if name == "col" and node.args:
                v = _const_value(node.args[0])
                if isinstance(v, str) and v not in seen:
                    seen.add(v)
                    sources.append(v)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            # df.column form — only treat as col ref if the LHS isn't a module
            cand = node.attr
            if cand not in seen and node.value.id not in {"F", "fn", "pyspark"}:
                seen.add(cand)
                sources.append(cand)
    return sources, via, udf_name


_BUILTIN_FUNCTIONS = {
    "col", "lit", "when", "expr", "cast", "F", "broadcast",
    "sum", "count", "avg", "min", "max", "mean", "first", "last",
    "upper", "lower", "trim", "concat", "concat_ws",
    "year", "month", "day", "date", "to_date", "to_timestamp",
    "round", "abs", "floor", "ceil",
    "_sum",  # common alias in fixtures
}


def _agg_to_alias(arg: ast.AST) -> tuple[str | None, list[str], str | None]:
    """``_sum("amount").alias("total_amount")`` → ("total_amount", ["amount"], "_sum(amount)")."""
    if isinstance(arg, ast.Call) and _call_attr_name(arg) == "alias":
        alias = _const_arg(arg, 0)
        inner = arg.func.value if isinstance(arg.func, ast.Attribute) else None
        sources: list[str] = []
        if isinstance(inner, ast.Call):
            for a in inner.args:
                name = _column_ref_name(a) or _const_value(a)
                if isinstance(name, str):
                    sources.append(name)
        return alias, sources, _unparse(arg.func.value) if isinstance(arg.func, ast.Attribute) else None
    return None, [], None


def _qualify_table_name(name: str, default_db: str) -> str:
    parts = name.split(".")
    if len(parts) == 1 and default_db:
        return f"{default_db}.{parts[0]}"
    return name


def _decorator_name(dec: ast.AST) -> str | None:
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Call):
        if isinstance(dec.func, ast.Name):
            return dec.func.id
        if isinstance(dec.func, ast.Attribute):
            return dec.func.attr
    if isinstance(dec, ast.Attribute):
        return dec.attr
    return None


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None
