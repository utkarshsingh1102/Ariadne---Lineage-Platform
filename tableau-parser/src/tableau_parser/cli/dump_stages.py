"""Tableau dump_stages — see tws_parser.cli.dump_stages for the rationale.

Tableau has no lexer/parse-tree step (lxml DOM walk), so the simulator
tabs that show up for this parser are: input, dom, ir, cypher, graph, meta.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from lxml import etree

from tableau_parser.parser.workbook import parse_workbook


# ---------------------------------------------------------------------------
# DOM — elided lxml tree showing only structural elements + their tag.
# ---------------------------------------------------------------------------

def _dom_summary(file_path: str, max_depth: int = 4) -> dict[str, Any]:
    fp = Path(file_path)
    if fp.suffix.lower() == ".twbx":
        # twbx is a zip wrapping a .twb. Pull the first .twb inside.
        import zipfile
        with zipfile.ZipFile(fp) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".twb"):
                    with zf.open(name) as f:
                        tree = etree.parse(f)
                    break
            else:
                return {"error": "no .twb inside .twbx"}
    else:
        tree = etree.parse(str(fp))

    def tag_of(elem) -> str:
        # Comments, processing instructions etc don't have a normal tag —
        # render a synthetic label so the tree-summary stays well-formed.
        t = getattr(elem, "tag", None)
        if not isinstance(t, str):
            return f"<{type(elem).__name__}>"
        try:
            return etree.QName(t).localname
        except (ValueError, TypeError):
            return str(t)

    def walk(elem, depth: int) -> dict[str, Any]:
        if depth >= max_depth:
            return {"tag": tag_of(elem),
                    "children": [{"_": "…truncated…"}]}
        children: list[dict[str, Any]] = []
        for c in list(elem)[:50]:
            children.append(walk(c, depth + 1))
        return {
            "tag": tag_of(elem),
            "attrs": dict(elem.attrib) if getattr(elem, "attrib", None) else {},
            "children": children,
        }
    return walk(tree.getroot(), 0)


# ---------------------------------------------------------------------------
# IR — WorkbookIR + everything reachable from it.
# ---------------------------------------------------------------------------

def _ir(wb) -> dict[str, Any]:
    return dataclasses.asdict(wb)


# ---------------------------------------------------------------------------
# Cypher dry-run — a representative subset.
# ---------------------------------------------------------------------------

def _cypher_dry_run(wb) -> str:
    chunks = [f"// Workbook -> Cypher sketch ({len(wb.datasources)} datasources)\n"]
    chunks.append("MERGE (w:Workbook {id: $id})")
    chunks.append("  SET w.name = $name, w.file_path = $file_path")
    chunks.append("")
    chunks.append("// One MERGE per datasource:")
    chunks.append("MERGE (ds:Datasource {id: $id})")
    chunks.append("  SET ds.name = $name, ds.kind = $kind")
    chunks.append("MERGE (w)-[:USES_DATASOURCE]->(ds)")
    chunks.append("")
    chunks.append("// One MERGE per worksheet / dashboard etc, see tableau-parser/graph/queries.py")
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Cytoscape graph — workbook + its top-level children.
# ---------------------------------------------------------------------------

def _cytoscape(wb) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    src_sys = "tableau"

    def node(_id, label, cls, **p):
        nodes.append({"data": {
            "id": _id, "label": label, "labels": [cls],
            "source_system": src_sys, "properties": p,
        }})

    def edge(_id, s, t, lbl, **p):
        edges.append({"data": {
            "id": _id, "source": s, "target": t, "label": lbl, "properties": p,
        }})

    node(wb.id, wb.name or "(workbook)", "Workbook", file_path=wb.file_path)
    for ds in wb.datasources:
        node(ds.id, ds.name or "(datasource)", "Datasource",
             is_federated=ds.is_federated, has_extract=ds.has_extract)
        edge(f"{wb.id}-USES_DATASOURCE-{ds.id}", wb.id, ds.id, "USES_DATASOURCE")
        for f in (ds.fields or [])[:25]:
            node(f.id, f.name, "Attribute",
                 role=getattr(f, "role", None),
                 datatype=getattr(f, "datatype", None))
            edge(f"{ds.id}-HAS_ATTRIBUTE-{f.id}", ds.id, f.id, "HAS_ATTRIBUTE")
    for ws in (getattr(wb, "worksheets", None) or []):
        node(ws.id, ws.name, "Worksheet")
        edge(f"{wb.id}-CONTAINS_WORKSHEET-{ws.id}", wb.id, ws.id, "CONTAINS_WORKSHEET")
    for db in (getattr(wb, "dashboards", None) or []):
        node(db.id, db.name, "Dashboard")
        edge(f"{wb.id}-CONTAINS_DASHBOARD-{db.id}", wb.id, db.id, "CONTAINS_DASHBOARD")

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

    wb = parse_workbook(fp)
    (out / "dom.json").write_text(
        json.dumps(_dom_summary(fp), indent=2), encoding="utf-8")
    (out / "ir.json").write_text(
        json.dumps(_ir(wb), indent=2, default=str), encoding="utf-8")
    (out / "cypher.cypher").write_text(_cypher_dry_run(wb), encoding="utf-8")
    (out / "graph.json").write_text(
        json.dumps(_cytoscape(wb), indent=2, default=str), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "parser": "tableau",
        "fixture": Path(fp).name,
        "stats": {
            "datasources": len(wb.datasources),
            "worksheets": len(getattr(wb, "worksheets", None) or []),
            "dashboards": len(getattr(wb, "dashboards", None) or []),
            "attributes": sum(len(ds.fields or []) for ds in wb.datasources),
        },
    }, indent=2), encoding="utf-8")
    print(f"tableau.dump_stages -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
