"""QlikView dump_stages — see tws_parser.cli.dump_stages for the rationale."""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from antlr4 import CommonTokenStream, FileStream

from qlikview_parser.core import QlikViewParser
from qlikview_parser.generated.QlikViewLexer import QlikViewLexer


def _tokens(fp: str) -> list[dict[str, Any]]:
    lexer = QlikViewLexer(FileStream(fp, encoding="utf-8"))
    stream = CommonTokenStream(lexer)
    stream.fill()
    sym = QlikViewLexer.symbolicNames
    rows: list[dict[str, Any]] = []
    for tok in stream.tokens:
        if tok.type == -1:
            continue
        type_name = sym[tok.type] if 0 <= tok.type < len(sym) else f"type_{tok.type}"
        rows.append({"line": tok.line, "column": tok.column,
                     "type": type_name, "text": tok.text})
    return rows


def _ir(app) -> dict[str, Any]:
    def to_d(obj):
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        if isinstance(obj, list):
            return [to_d(x) for x in obj]
        return obj
    return {
        "app_name": getattr(app, "app_name", ""),
        "connections": to_d(list(getattr(app, "connections", []))),
        "variables":   to_d(list(getattr(app, "variables", []))),
        "subroutines": to_d(list(getattr(app, "subroutines", []))),
        "loads":       to_d(list(getattr(app, "loads", []))),
        "joins":       to_d(list(getattr(app, "joins", []))),
        "parse_errors": list(getattr(app, "parse_errors", [])),
    }


def _cypher_dry_run(app) -> str:
    out: list[str] = []
    out.append(f"// QlikView app -> Cypher sketch")
    out.append("MERGE (s:QlikScript {id: $id})")
    out.append("  SET s.name = $name, s.file_path = $file_path")
    out.append("")
    out.append("// One MERGE per LOAD statement:")
    out.append("MERGE (t:QlikTable {id: $id})")
    out.append("  SET t.name = $name, t.source_type = $source_type")
    out.append("MERGE (s)-[:CONTAINS_TABLE]->(t)")
    out.append("")
    out.append("// Plus attribute MERGEs per field; see qlikview-parser/graph/writer.py.")
    return "\n".join(out)


def _cytoscape(app, file_path: str) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    sys_id = "qlikview"

    def n(_id, label, cls, **p):
        nodes.append({"data": {
            "id": _id, "label": label, "labels": [cls],
            "source_system": sys_id, "properties": p,
        }})

    def e(_id, s, t, lbl, **p):
        edges.append({"data": {
            "id": _id, "source": s, "target": t, "label": lbl, "properties": p,
        }})

    app_id = Path(file_path).stem
    n(app_id, app_id, "QlikScript", file_path=file_path)
    for ld in getattr(app, "loads", []):
        tid = f"qt:{ld.table_name}"
        n(tid, ld.table_name, "QlikTable",
          source_type=getattr(ld.source_type, "value", str(ld.source_type)),
          source_table=ld.source_table)
        e(f"{app_id}-CONTAINS_TABLE-{tid}", app_id, tid, "CONTAINS_TABLE")
        for fname in (ld.fields or [])[:25]:
            # LoadStatement.fields is list[str]; the IR carries field names only.
            fid = f"qa:{ld.table_name}.{fname}"
            n(fid, fname, "Attribute")
            e(f"{tid}-HAS_ATTRIBUTE-{fid}", tid, fid, "HAS_ATTRIBUTE")
    for conn in getattr(app, "connections", []):
        # connections may be list[Connection] OR list[str] depending on the
        # IR path that ran — defensive access.
        if isinstance(conn, str):
            cid = f"qc:{conn}"
            n(cid, conn, "Connection")
        else:
            cid = f"qc:{conn.name}"
            n(cid, conn.name, "Connection",
              type=getattr(getattr(conn, "type", None), "value", str(getattr(conn, "type", "")) ))
        e(f"{app_id}-USES_CONNECTION-{cid}", app_id, cid, "USES_CONNECTION")

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

    parser = QlikViewParser()
    try:
        app = parser.parse_qvs_file(fp)
    finally:
        try: parser.close()
        except Exception: pass

    (out / "tokens.json").write_text(
        json.dumps(_tokens(fp), indent=2), encoding="utf-8")
    (out / "ir.json").write_text(
        json.dumps(_ir(app), indent=2, default=str), encoding="utf-8")
    (out / "cypher.cypher").write_text(_cypher_dry_run(app), encoding="utf-8")
    (out / "graph.json").write_text(
        json.dumps(_cytoscape(app, fp), indent=2, default=str), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "parser": "qlikview",
        "fixture": Path(fp).name,
        "stats": {
            "loads": len(getattr(app, "loads", [])),
            "connections": len(getattr(app, "connections", [])),
            "variables": len(getattr(app, "variables", [])),
            "subroutines": len(getattr(app, "subroutines", [])),
            "fields": sum(len(l.fields) for l in getattr(app, "loads", [])),
        },
    }, indent=2), encoding="utf-8")
    print(f"qlikview.dump_stages -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
