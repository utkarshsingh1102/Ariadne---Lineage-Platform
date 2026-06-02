"""Command-line entry point — `python -m spark_parser <file>` (M6).

Three output formats, selected via ``--format``:

* ``json`` (default) — the same payload the FastAPI ``/parse`` endpoint
  returns, minus the Neo4j-write block. Useful for piping into ``jq``.
* ``openlineage`` — a single OpenLineage 1.0.5 ``RunEvent`` produced by
  ``federation.openlineage_emitter.emit_script_event``.
* ``dot`` — Graphviz source. ``dot -Tsvg`` it to inspect the lineage
  visually without standing up the full frontend.

Stays a thin wrapper over ``parse_input`` so all of the CLI / API
behaviour comes from the same code path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .main import _stats, parse_input
from .models.domain import SparkScriptIR


def _format_json(ir: SparkScriptIR) -> str:
    payload: dict[str, Any] = {
        "script_id": ir.id,
        "script_type": ir.script_type,
        "name": ir.name,
        "file_path": ir.file_path,
        "stats": _stats(ir),
        "dataframes": [
            {
                "var_name": df.var_name,
                "is_anonymous": df.is_anonymous,
                "reads_from": [
                    (t.fully_qualified_name or t.location) for t in df.reads_from
                ],
                "writes_to": [
                    (t.fully_qualified_name or t.location) for t in df.writes_to
                ],
                "fields": [a.name for a in df.fields],
                "derivations": [
                    {
                        "target": d.target_column,
                        "sources": d.source_columns,
                        "via": d.via,
                        "formula": d.formula,
                    }
                    for d in df.derivations
                ],
            }
            for df in ir.dataframes
        ],
        "udfs": [
            {"name": u.name, "return_type": u.return_type, "pandas": u.is_pandas_udf}
            for u in ir.udfs
        ],
        "warnings": [
            {"type": w.type, "detail": w.detail, "line": w.line}
            for w in ir.warnings
        ],
    }
    return json.dumps(payload, indent=2)


def _format_openlineage(ir: SparkScriptIR) -> str:
    from .federation.openlineage_emitter import emit_script_event

    event = emit_script_event(ir, event_type="COMPLETE")
    return json.dumps(event, indent=2)


def _format_dot(ir: SparkScriptIR) -> str:
    """Render the IR as a Graphviz digraph.

    Nodes:
      * blue rectangles — physical Tables (FQN or location)
      * yellow rounded boxes — DataFrames (named ones only; anonymous
        intermediates would explode the graph)
      * grey octagons — UDFs
    Edges:
      * black solid — READS_TABLE / WRITES_TABLE
      * blue dashed — DERIVES_FROM (DataFrame → DataFrame)
      * purple — UDF → DataFrame (where the UDF was used)
    """
    lines: list[str] = ['digraph lineage {', '  rankdir=LR;', '  node [fontname="Helvetica"];']

    def nid(prefix: str, key: str) -> str:
        # Graphviz IDs can't contain ``.`` or ``/`` un-quoted.
        return f'"{prefix}__{key}"'

    seen_tables: set[str] = set()
    for df in ir.dataframes:
        if df.is_anonymous:
            continue
        df_id = nid("df", df.var_name)
        label = df.var_name.replace('"', "'")
        lines.append(
            f'  {df_id} [shape=box, style="rounded,filled", '
            f'fillcolor="#fff8b8", label="{label}"];'
        )
        for tbl in df.reads_from:
            key = tbl.fully_qualified_name or tbl.location
            if not key:
                continue
            if key not in seen_tables:
                seen_tables.add(key)
                lines.append(
                    f'  {nid("tbl", key)} [shape=box, style=filled, '
                    f'fillcolor="#cfe0ff", label="{key}"];'
                )
            lines.append(f'  {nid("tbl", key)} -> {df_id};')
        for tbl in df.writes_to:
            key = tbl.fully_qualified_name or tbl.location
            if not key:
                continue
            if key not in seen_tables:
                seen_tables.add(key)
                lines.append(
                    f'  {nid("tbl", key)} [shape=box, style=filled, '
                    f'fillcolor="#ffd7c2", label="{key}"];'
                )
            lines.append(f'  {df_id} -> {nid("tbl", key)};')

    # DataFrame → DataFrame derivations (named only).
    named = {df.var_name for df in ir.dataframes if not df.is_anonymous}
    for df in ir.dataframes:
        if df.is_anonymous or df.var_name not in named:
            continue
        for edge in df.derives_from_dataframe:
            if edge.source_var and edge.source_var in named:
                lines.append(
                    f'  {nid("df", edge.source_var)} -> {nid("df", df.var_name)} '
                    f'[style=dashed, color="#0f62fe"];'
                )

    for udf in ir.udfs:
        lines.append(
            f'  {nid("udf", udf.name)} [shape=octagon, style=filled, '
            f'fillcolor="#e8daff", label="UDF\\n{udf.name}"];'
        )
        # Connect UDF to any DataFrame whose derivations attribute used it.
        for df in ir.dataframes:
            if df.is_anonymous:
                continue
            if any(d.via == "udf" and udf.name in (d.formula or "") for d in df.derivations):
                lines.append(
                    f'  {nid("udf", udf.name)} -> {nid("df", df.var_name)} '
                    f'[color="#8a3ffc"];'
                )

    lines.append("}")
    return "\n".join(lines)


_FORMATTERS = {
    "json": _format_json,
    "openlineage": _format_openlineage,
    "dot": _format_dot,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spark-parser",
        description="Static lineage extractor for PySpark / Spark SQL / notebooks.",
    )
    parser.add_argument(
        "file",
        type=str,
        help="Path to a .py / .sql / .ipynb file (or a Databricks .py notebook).",
    )
    parser.add_argument(
        "--format",
        choices=sorted(_FORMATTERS.keys()),
        default="json",
        help="Output format (default: json).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Write to this file instead of stdout.",
    )
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    ir = parse_input(str(path))
    rendered = _FORMATTERS[args.format](ir)

    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
