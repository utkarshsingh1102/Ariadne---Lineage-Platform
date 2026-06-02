"""Phase 3 — Qlik Sense app-object lineage (v2 plan §1 Stage 1, bucket A).

After :mod:`extract_qvf` pulls the load script out of a .qvf, this
module reads the raw AppObject rows and turns them into:

  - ``:UiObject`` nodes  — one per Sense chart / sheet / dimension /
    measure / bookmark. Carries qType, qTitle, owner-sheet info.
  - ``FEEDS_OBJECT`` edges — from every ``:Attribute`` referenced inside
    a chart expression's dimensions / measures back to the UiObject.

Sense AppObjects are JSON blobs serialised as TEXT in the SQLite table.
The qType column tells us what kind of object it is:

  | qType        | meaning              | fields with field refs  |
  |--------------|----------------------|--------------------------|
  | sheet        | a dashboard sheet    | (children link via qParentId)
  | masterobject | reusable chart       | qHyperCubeDef.qDimensions[*].qDef.qFieldDefs
  | dimension    | master dimension     | qDim.qFieldDefs
  | measure      | master measure       | qMeasure.qDef
  | bookmark     | selection state      | qPatches[*].qPath/qValue

Identity:  ``uiobject::<app_path>/<qId>``. Field references inside an
object's expression are scanned with a conservative regex — any bare
identifier that matches a known :Attribute.name on the same app
produces a FEEDS_OBJECT edge.

Soft-fail: malformed JSON blobs produce diagnostics, never exceptions.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable

from .ids import dataset_qname, sha256_id
from .models import Attribute, Diagnostic, LineageEdge, QlikViewApp


# ---------------------------------------------------------------------------
# UI object IR — frozen value type (mirrors v2 plan §3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UiObject:
    """A Sense sheet / chart / dimension / measure / bookmark.

    Identity: ``uiobject::<app_path>/<qId>``.
    """
    qid: str
    qtype: str
    qtitle: str | None
    app: str
    expression: str | None = None     # joined, scrubbed expression text

    @property
    def qname(self) -> str:
        return f"uiobject::{self.app}/{self.qid}"


@dataclass
class SenseExtraction:
    objects: list[UiObject] = field(default_factory=list)
    edges: list[LineageEdge] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


# Conservative identifier regex — Sense field references are
# ``[Bracketed Field Names]`` or bare ``Identifier`` tokens. We grab both.
_IDENT = re.compile(r"\[([^\]\r\n]+)\]|\b([A-Za-z_][A-Za-z0-9_]*)\b")

# Tokens we always discard from the candidate-field list (Sense
# expression keywords + functions).
_NOISE = {
    "SUM", "COUNT", "AVG", "MIN", "MAX", "ONLY", "FIRSTVALUE", "LASTVALUE",
    "IF", "THEN", "ELSE", "END", "AND", "OR", "NOT", "NULL", "TRUE", "FALSE",
    "AGGR", "TOTAL", "NUM", "DATE", "TEXT", "ROUND", "FLOOR", "CEIL", "LEFT",
    "RIGHT", "MID", "LEN", "UPPER", "LOWER", "TRIM", "REPLACE", "ISNULL",
    "BETWEEN", "IN", "LIKE", "DISTINCT", "DUAL", "PICK", "MATCH",
    "RANGESUM", "DIV", "MOD", "AS", "BY", "ORDER", "DESC", "ASC",
    # Sense-only: set analysis keywords.
    "ALL", "SELECTED", "DOLLAR",
}


def parse_app_objects(
    app: QlikViewApp,
    raw_rows: list[dict],
) -> SenseExtraction:
    """Walk the raw ``AppObject`` rows and emit :UiObject IR records +
    FEEDS_OBJECT edges back to any :Attribute referenced in a chart
    expression. Idempotent — running twice produces the same edges
    because the dedup key is (src_id, dst_id, sig)."""
    result = SenseExtraction()
    seen_edges: set[tuple[str, str, str]] = set()

    # Index existing app attributes by (lower-name) so a free-text
    # field reference inside a chart expression can be resolved.
    attrs_by_name_lc: dict[str, list[Attribute]] = {}
    for a in app.attributes:
        attrs_by_name_lc.setdefault(a.name.lower(), []).append(a)

    for row in raw_rows:
        # Some QVFs store the body in a 'qData' column, others in 'data'
        # or 'definition'. We try each.
        body_blob = (
            row.get("qData") or row.get("data")
            or row.get("definition") or row.get("body")
        )
        qid = row.get("qId") or row.get("id") or row.get("qid") or ""
        qtype = row.get("qType") or row.get("type") or "object"
        qtitle = row.get("qTitle") or row.get("title")
        if not body_blob and not qid:
            continue
        parsed: dict | None = None
        if isinstance(body_blob, bytes):
            try:
                body_blob = body_blob.decode("utf-8", errors="replace")
            except Exception:
                body_blob = ""
        if isinstance(body_blob, str) and body_blob.strip():
            try:
                parsed = json.loads(body_blob)
            except json.JSONDecodeError:
                result.diagnostics.append(Diagnostic(
                    level="warn", code="QV-SENSE-PARSE",
                    message=f"AppObject {qid!s}: JSON parse failed",
                    artifact=app.file_path, line=None,
                ))
                parsed = None
        # Collect every expression text inside the object.
        expressions = list(_walk_expressions(parsed)) if parsed else []
        joined = " ; ".join(expressions) if expressions else None

        ui = UiObject(
            qid=str(qid),
            qtype=str(qtype),
            qtitle=qtitle,
            app=app.file_path,
            expression=joined,
        )
        result.objects.append(ui)

        # Field references → FEEDS_OBJECT edges.
        ui_id = sha256_id(ui.qname)
        for fname in _candidate_fields(expressions):
            attrs = attrs_by_name_lc.get(fname.lower())
            if not attrs:
                continue
            for a in attrs:
                src = sha256_id(a.qname)
                sig = f"FEEDS_OBJECT:{a.name}"
                key = (src, ui_id, sig)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                result.edges.append(LineageEdge(
                    src_id=src,
                    dst_id=ui_id,
                    rel="FEEDS_OBJECT",
                    transform=f"sense:{qtype}",
                    confidence=0.85,
                    evidence=(joined or "")[:120],
                ))
    return result


# ---------------------------------------------------------------------------
# Walkers
# ---------------------------------------------------------------------------


# JSON paths inside a Sense AppObject body that commonly hold an
# expression string. We walk the whole tree depth-first and pull every
# string under one of these keys.
_EXPR_KEYS: frozenset[str] = frozenset({
    "qDef", "qExpression", "qStringExpression", "qNumberExpression",
    "qExpr", "qFieldDefs", "qFieldDef", "qFormula", "qTitleExpression",
})


def _walk_expressions(node) -> Iterable[str]:
    """Yield every string under an :_EXPR_KEYS_-named key in a parsed
    AppObject JSON body. Walks arbitrary nesting."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _EXPR_KEYS and isinstance(v, str) and v.strip():
                yield v
            elif k in _EXPR_KEYS and isinstance(v, list):
                for entry in v:
                    if isinstance(entry, str) and entry.strip():
                        yield entry
                    elif isinstance(entry, dict):
                        # qFieldDefs entries are often {"qDef": "..."}.
                        nested = entry.get("qDef") or entry.get("qExpression")
                        if isinstance(nested, str):
                            yield nested
                        yield from _walk_expressions(entry)
            else:
                yield from _walk_expressions(v)
    elif isinstance(node, list):
        for entry in node:
            yield from _walk_expressions(entry)


def _candidate_fields(expressions: list[str]) -> set[str]:
    """Return identifiers in ``expressions`` that look like field
    references — both ``[Bracketed Form]`` and bare identifiers."""
    out: set[str] = set()
    for expr in expressions:
        # Strip strings + comments so identifiers inside literals don't
        # leak through.
        cleaned = re.sub(r"'[^']*'", "", expr)
        cleaned = re.sub(r"//[^\n]*", "", cleaned)
        for m in _IDENT.finditer(cleaned):
            tok = m.group(1) or m.group(2)
            if not tok:
                continue
            if tok.upper() in _NOISE:
                continue
            if tok.isdigit():
                continue
            out.add(tok)
    return out
