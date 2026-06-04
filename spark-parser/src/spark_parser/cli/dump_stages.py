"""Spark dump_stages — Python AST snapshot, IR, Cypher dry-run, Cytoscape graph."""
from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from spark_parser.main import parse_input


def _ast_summary(file_path: str) -> dict[str, Any]:
    """Compact summary: top-level definitions + their child count.

    Showing the full ``ast.dump`` is overwhelming; a tree of top-level
    statements is more useful and still demonstrates what the visitor walks.
    """
    suffix = Path(file_path).suffix.lower()
    if suffix == ".ipynb":
        # Walk the notebook's Python cells. We don't need to be exact —
        # the simulator widget displays this as a summary.
        nb = json.loads(Path(file_path).read_text(encoding="utf-8"))
        src = "\n\n".join(
            "".join(c.get("source", []))
            for c in nb.get("cells", [])
            if c.get("cell_type") == "code"
        )
    elif suffix == ".sql":
        return {"note": "SQL fixtures are parsed via sqlglot, not Python AST"}
    else:
        src = Path(file_path).read_text(encoding="utf-8")

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"error": str(e)}

    def summarize(node) -> dict[str, Any]:
        return {
            "type": type(node).__name__,
            "lineno": getattr(node, "lineno", None),
            "name": getattr(node, "name", None),
        }
    return {"body": [summarize(n) for n in tree.body]}


def _to_d(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, list):
        return [_to_d(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_d(v) for k, v in obj.items()}
    return obj


def _ir(ir) -> dict[str, Any]:
    return _to_d(ir)


def _cypher_dry_run(ir) -> str:
    out = []
    out.append("// SparkScript -> Cypher sketch")
    out.append("MERGE (s:SparkScript {id: $id})")
    out.append("  SET s.name = $name, s.file_path = $file_path,"
               " s.script_type = $script_type")
    out.append("")
    out.append("// One MERGE per DataFrame in the chain:")
    out.append("MERGE (df:DataFrame {id: $df_id})")
    out.append("  SET df.label = $label")
    out.append("MERGE (s)-[:CONTAINS_DATAFRAME]->(df)")
    out.append("")
    out.append("// Plus READS_TABLE / WRITES_TABLE / DERIVES_FROM_DATAFRAME edges —")
    out.append("// see spark-parser/graph/queries.py.")
    return "\n".join(out)


def _cytoscape(ir, file_path: str) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    sysid = "spark"

    def n(_id, label, cls, **p):
        nodes.append({"data": {
            "id": _id, "label": label, "labels": [cls],
            "source_system": sysid, "properties": p,
        }})

    def e(_id, s, t, lbl, **p):
        edges.append({"data": {
            "id": _id, "source": s, "target": t, "label": lbl, "properties": p,
        }})

    sid = getattr(ir, "id", None) or Path(file_path).stem
    n(sid, Path(file_path).name, "SparkScript", file_path=file_path)

    for df in getattr(ir, "dataframes", []) or []:
        df_id = df.id or f"df:{df.var_name}:{df.creation_order}"
        n(df_id, df.var_name or df_id[:8], "DataFrame",
          creation_order=df.creation_order)
        e(f"{sid}-CONTAINS_DATAFRAME-{df_id}", sid, df_id, "CONTAINS_DATAFRAME")
        for r in getattr(df, "reads_from", []) or []:
            fqn = getattr(r, "fully_qualified_name", None) or getattr(r, "name", str(r))
            tid = f"tbl:{fqn}"
            if all(x["data"]["id"] != tid for x in nodes):
                n(tid, fqn, "Table")
            e(f"{df_id}-READS_TABLE-{tid}", df_id, tid, "READS_TABLE")
        for w in getattr(df, "writes_to", []) or []:
            fqn = getattr(w, "fully_qualified_name", None) or getattr(w, "name", str(w))
            tid = f"tbl:{fqn}"
            if all(x["data"]["id"] != tid for x in nodes):
                n(tid, fqn, "Table")
            e(f"{df_id}-WRITES_TABLE-{tid}", df_id, tid, "WRITES_TABLE")
        for d in getattr(df, "derives_from_dataframe", []) or []:
            target = getattr(d, "from_df_id", None) or getattr(d, "to_df_id", None)
            if target:
                e(f"{df_id}-DERIVES_FROM_DATAFRAME-{target}", df_id, target,
                  "DERIVES_FROM_DATAFRAME")

    return {"nodes": nodes, "edges": edges}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fp = args.fixture
    shutil.copyfile(fp, out / f"input{Path(fp).suffix}")

    ir = parse_input(fp)

    (out / "ast.json").write_text(
        json.dumps(_ast_summary(fp), indent=2), encoding="utf-8")
    (out / "ir.json").write_text(
        json.dumps(_ir(ir), indent=2, default=str), encoding="utf-8")
    (out / "cypher.cypher").write_text(_cypher_dry_run(ir), encoding="utf-8")
    (out / "graph.json").write_text(
        json.dumps(_cytoscape(ir, fp), indent=2, default=str), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "parser": "spark",
        "fixture": Path(fp).name,
        "stats": {
            "dataframes": len(getattr(ir, "dataframes", []) or []),
            "reads": sum(len(getattr(d, "reads_from", []) or [])
                         for d in getattr(ir, "dataframes", []) or []),
            "writes": sum(len(getattr(d, "writes_to", []) or [])
                          for d in getattr(ir, "dataframes", []) or []),
        },
    }, indent=2), encoding="utf-8")
    print(f"spark.dump_stages -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
