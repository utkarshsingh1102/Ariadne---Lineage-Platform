"""Dump the parsing pipeline stage by stage to JSON files.

Used by the Phase 6 docs site's <ParserSimulator/> widget — each stage's
output becomes one tab the reader can flip through.

Usage:
    python -m tws_parser.cli.dump_stages <fixture.txt> --out <dir>

Produces under <dir>:
    input.txt         the source fixture (verbatim copy)
    tokens.json       ANTLR lexer token stream (line, col, type, text)
    tree.json         ANTLR parse tree, serialized as nested dicts
    ir.json           ParsedComposerUnit + resolved deps as JSON
    cypher.cypher     the MERGE statements the writer WOULD emit (dry-run)
    graph.json        Cytoscape-shaped {nodes, edges} for the final graph
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from antlr4 import CommonTokenStream, FileStream

from tws_parser.generated.TWSComposerLexer import TWSComposerLexer
from tws_parser.generated.TWSComposerParser import TWSComposerParser
from tws_parser.graph import queries as q
from tws_parser.parser.dependencies import resolve_full
from tws_parser.parser.orchestrator import parse_full_with_errors
from tws_parser.utils.ids import file_watcher_id, resource_id, script_id, tws_file_id


# ---------------------------------------------------------------------------
# Tokens — re-run the lexer just for the table; cheaper than walking the
# parse tree to extract terminals.
# ---------------------------------------------------------------------------

def _tokens(fp: str) -> list[dict[str, Any]]:
    lexer = TWSComposerLexer(FileStream(fp, encoding="utf-8"))
    stream = CommonTokenStream(lexer)
    stream.fill()
    sym = TWSComposerLexer.symbolicNames
    out: list[dict[str, Any]] = []
    for tok in stream.tokens:
        if tok.type == -1:  # EOF
            continue
        type_name = sym[tok.type] if 0 <= tok.type < len(sym) else f"type_{tok.type}"
        out.append({
            "line": tok.line,
            "column": tok.column,
            "type": type_name,
            "text": tok.text,
        })
    return out


# ---------------------------------------------------------------------------
# Parse tree — ANTLR's tree as nested dicts, capped at a sane depth.
# ---------------------------------------------------------------------------

def _tree(fp: str, max_depth: int = 32) -> dict[str, Any]:
    lexer = TWSComposerLexer(FileStream(fp, encoding="utf-8"))
    stream = CommonTokenStream(lexer)
    parser = TWSComposerParser(stream)
    tree = parser.compilationUnit()
    rule_names = parser.ruleNames

    def walk(node, depth: int) -> dict[str, Any]:
        if depth > max_depth:
            return {"truncated": True}
        if hasattr(node, "ruleIndex"):
            name = rule_names[node.ruleIndex]
            children = []
            for i in range(node.getChildCount()):
                children.append(walk(node.getChild(i), depth + 1))
            return {"rule": name, "children": children}
        # Terminal
        return {"text": node.getText()}

    return walk(tree, 0)


# ---------------------------------------------------------------------------
# IR — full ParsedComposerUnit (already populated by the orchestrator) as
# JSON via dataclasses.asdict.
# ---------------------------------------------------------------------------

def _ir(unit) -> dict[str, Any]:
    return {
        "workstations": [dataclasses.asdict(w) for w in unit.workstations],
        "calendars":    [dataclasses.asdict(c) for c in unit.calendars],
        "resources":    [dataclasses.asdict(r) for r in unit.resources],
        "prompts":      [dataclasses.asdict(p) for p in unit.prompts],
        "event_rules":  [dataclasses.asdict(e) for e in unit.event_rules],
        "schedules":    [dataclasses.asdict(s) for s in unit.schedules],
        "job_streams":  [dataclasses.asdict(s) for s in unit.job_streams],
    }


# ---------------------------------------------------------------------------
# Cypher dry-run — collect the MERGE templates the writer would execute
# without touching Neo4j. We pair each template with one row of data so the
# reader can see what *would* be sent.
# ---------------------------------------------------------------------------

def _cypher_dry_run(unit, deps) -> str:
    chunks: list[str] = []

    def emit(label: str, template: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        chunks.append(f"// {label} — {len(rows)} row(s)")
        chunks.append(f"// example row: {json.dumps(rows[0], default=str)}")
        chunks.append(template.strip())
        chunks.append("")

    emit("MERGE_WORKSTATION", q.MERGE_WORKSTATION,
         [{"id": w.id, "name": w.name} for w in unit.workstations])
    emit("MERGE_CALENDAR", q.MERGE_CALENDAR,
         [{"id": c.id, "name": c.name} for c in unit.calendars])
    emit("MERGE_PROMPT", q.MERGE_PROMPT,
         [{"id": p.id, "name": p.name} for p in unit.prompts])
    emit("MERGE_EVENT_RULE", q.MERGE_EVENT_RULE,
         [{"id": e.id, "name": e.name} for e in unit.event_rules])
    emit("MERGE_JOB_STREAM", q.MERGE_JOB_STREAM,
         [{"id": s.id, "name": s.name} for s in unit.job_streams])
    emit("MERGE_SCHEDULE", q.MERGE_SCHEDULE,
         [{"id": s.id, "name": s.name} for s in unit.schedules])
    jobs = [j for s in unit.schedules for j in s.jobs]
    emit("MERGE_JOB", q.MERGE_JOB,
         [{"id": j.id, "name": j.name} for j in jobs])
    emit("CONTAINS_JOB", q.CONTAINS_JOB,
         [{"schedule_id": s.id, "job_id": j.id, "order": j.order_in_schedule}
          for s in unit.schedules for j in s.jobs])
    if deps.follows_edges:
        emit("DEPENDS_ON_JOB", q.DEPENDS_ON_JOB,
             [{"from_id": e.from_job_id, "to_id": e.to_job_id,
               "condition": e.condition or "", "scope": e.scope}
              for e in deps.follows_edges])
    if deps.recovery_edges:
        emit("RECOVERS_WITH", q.RECOVERS_WITH,
             [{"from_id": e.from_job_id, "to_id": e.to_recovery_job_id,
               "recovery_action": e.recovery_action}
              for e in deps.recovery_edges])

    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Cytoscape graph — same shape the frontend uses.
# ---------------------------------------------------------------------------

def _cytoscape(unit, deps, file_path: str) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def node(_id: str, label: str, cls: str, **props: Any) -> None:
        nodes.append({"data": {
            "id": _id, "label": label, "labels": [cls],
            "source_system": "tws",
            "properties": props,
        }})

    def edge(_id: str, src: str, tgt: str, label: str, **props: Any) -> None:
        edges.append({"data": {
            "id": _id, "source": src, "target": tgt, "label": label,
            "properties": props,
        }})

    # File wrapper.
    fid = tws_file_id(file_path)
    node(fid, Path(file_path).name, "TwsFile", file_path=file_path)
    for s in unit.schedules:
        edge(f"{fid}->{s.id}", fid, s.id, "CONTAINS_SCHEDULE")

    for w in unit.workstations:
        node(w.id, w.name, "Workstation")
    for c in unit.calendars:
        node(c.id, c.name, "Calendar")
    for p in unit.prompts:
        node(p.id, p.name[:40], "Prompt")
    for r in unit.resources:
        node(r.id, r.name, "Resource", quantity=r.quantity)
    for s in unit.schedules:
        node(s.id, s.name, "Schedule",
             workstation=s.workstation, run_cycle=s.run_cycle,
             days_of_week=list(s.days_of_week),
             cron_equivalent=s.cron_equivalent)
        for j in s.jobs:
            node(j.id, j.name, "Job", workstation=j.workstation, stream=j.stream)
            edge(f"{s.id}-CONTAINS_JOB-{j.id}", s.id, j.id, "CONTAINS_JOB",
                 order=j.order_in_schedule)
            if j.script_path:
                sid = script_id(j.script_path)
                node(sid, Path(j.script_path).name, "Script", path=j.script_path)
                edge(f"{j.id}-EXECUTES-{sid}", j.id, sid, "EXECUTES")
            for path in j.opens:
                fwid = file_watcher_id(path)
                node(fwid, Path(path).name, "FileWatcher", path=path)
                edge(f"{j.id}-WAITS_FOR_FILE-{fwid}", j.id, fwid, "WAITS_FOR_FILE")
            for res_name, qty in j.needs:
                rid = resource_id(res_name)
                if all(n["data"]["id"] != rid for n in nodes):
                    node(rid, res_name, "Resource", quantity=qty)
                edge(f"{j.id}-REQUIRES_RESOURCE-{rid}", j.id, rid,
                     "REQUIRES_RESOURCE", quantity=qty)

    for e in deps.follows_edges:
        edges.append({"data": {
            "id": f"{e.from_job_id}-FOLLOWS-{e.to_job_id}-{e.condition or ''}",
            "source": e.from_job_id, "target": e.to_job_id,
            "label": "FOLLOWS",
            "properties": {"condition": e.condition or "", "scope": e.scope},
        }})
    for e in deps.recovery_edges:
        edges.append({"data": {
            "id": f"{e.from_job_id}-RECOVERS_WITH-{e.to_recovery_job_id}",
            "source": e.from_job_id, "target": e.to_recovery_job_id,
            "label": "RECOVERS_WITH",
            "properties": {"recovery_action": e.recovery_action},
        }})

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", help="Path to the composer fixture (.txt / .xml)")
    ap.add_argument("--out", required=True, help="Output directory")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    fp = args.fixture
    suffix = Path(fp).suffix or ".txt"
    shutil.copyfile(fp, out / f"input{suffix}")

    unit, parse_errors = parse_full_with_errors(fp)
    deps = resolve_full(unit)

    # Tokens + tree only meaningful for the composer-DSL path. XML path
    # skips both with a clear marker so the simulator widget hides the tab.
    if suffix.lower() in (".txt", ".sched"):
        (out / "tokens.json").write_text(
            json.dumps(_tokens(fp), indent=2), encoding="utf-8")
        (out / "tree.json").write_text(
            json.dumps(_tree(fp), indent=2), encoding="utf-8")

    (out / "ir.json").write_text(
        json.dumps(_ir(unit), indent=2, default=str), encoding="utf-8")
    (out / "cypher.cypher").write_text(
        _cypher_dry_run(unit, deps), encoding="utf-8")
    (out / "graph.json").write_text(
        json.dumps(_cytoscape(unit, deps, fp), indent=2, default=str),
        encoding="utf-8")
    (out / "meta.json").write_text(
        json.dumps({
            "parser": "tws",
            "fixture": Path(fp).name,
            "parse_errors": [
                {"line": e.line, "column": e.column, "detail": e.msg}
                for e in parse_errors
            ],
            "warnings": [
                {"type": w.type, "detail": w.detail} for w in deps.warnings
            ],
            "stats": {
                "schedules": len(unit.schedules),
                "job_streams": len(unit.job_streams),
                "jobs": sum(len(s.jobs) for s in unit.schedules),
                "follows_edges": len(deps.follows_edges),
                "recovery_edges": len(deps.recovery_edges),
            },
        }, indent=2), encoding="utf-8")

    print(f"tws.dump_stages -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
