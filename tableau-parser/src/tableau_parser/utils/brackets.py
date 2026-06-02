"""Tableau wraps every identifier in [ ]. Strip them before storing.

Improvement-v2 §11 — Tableau escapes a literal ``]`` inside a bracketed
identifier by doubling it. So ``[gross_amount_]]raw]`` is one identifier
whose decoded name is ``gross_amount_]raw``, not two adjacent identifiers
``gross_amount_`` and ``raw``.

To match this, every bracket-aware regex below uses the body pattern
``(?:\\]\\]|[^\\]])*`` — accept an escaped ``]]`` or any non-``]`` char —
and every captured identifier is post-processed by ``_unescape`` which
turns the ``]]`` back into a literal ``]``.
"""

from __future__ import annotations

import re

# Identifier body: any sequence of escaped ``]]`` pairs and non-``]`` chars.
_BODY = r"(?:\]\]|[^\]])"

_BRACKETED_TOKEN_RE = re.compile(rf"\[({_BODY}*)\]")
_BRACKET_REF_RE = re.compile(rf"\[({_BODY}+)\]")
# Two-part reference: [datasourceN].[fieldM] (no space allowed between).
_CROSS_SOURCE_RE = re.compile(rf"\[({_BODY}+)\]\.\[({_BODY}+)\]")
# LOD opener: `{FIXED [a],[b] : AGG([c])}`. The first capture is the kind,
# the second is the dimension list (we re-extract bracketed refs from it).
_LOD_RE = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b([^:}]+):", re.IGNORECASE)


def _unescape(s: str) -> str:
    """Reverse Tableau's ``]]`` → ``]`` identifier escape."""
    return s.replace("]]", "]") if "]]" in s else s


def strip_brackets(name: str | None) -> str:
    """Strip every `[bracketed]` segment in `name`, preserving any glue text.

    Examples
    --------
    >>> strip_brackets('[CustomerName]')
    'CustomerName'
    >>> strip_brackets('[Federated.0abc].[Calculation_123]')
    'Federated.0abc.Calculation_123'
    >>> strip_brackets('CustomerName')
    'CustomerName'
    >>> strip_brackets('[]')
    ''
    """
    if name is None or name == "":
        return ""
    s = name.strip()
    # Replace every [...] with its inner text, leaving surrounding glue (e.g. dots) intact.
    if "[" not in s:
        return s
    return _BRACKETED_TOKEN_RE.sub(lambda m: _unescape(m.group(1)), s)


def find_refs(formula: str | None) -> list[str]:
    """Return every `[bracketed]` identifier inside an expression."""
    if not formula:
        return []
    return [_unescape(m.group(1)) for m in _BRACKET_REF_RE.finditer(formula)]


def find_refs_with_spans(formula: str | None) -> list[tuple[str, int, int]]:
    """Return every `[bracketed]` identifier plus its (start, end) char range.

    Char positions are INCLUSIVE of the surrounding brackets — useful so a
    UI highlight covers the visible token, not just the inner name. So in
    ``"[A]+[B]"`` you'd get ``[("A", 0, 3), ("B", 4, 7)]``.

    Returned in source order with duplicates preserved — the caller decides
    how to dedupe (the parser keeps them all so highlight events can fire
    on the SAME calc-field reference at multiple positions).
    """
    if not formula:
        return []
    out: list[tuple[str, int, int]] = []
    for m in _BRACKET_REF_RE.finditer(formula):
        out.append((_unescape(m.group(1)), m.start(), m.end()))
    return out


def find_cross_source_refs(formula: str | None) -> list[tuple[str, str, int, int]]:
    """Return cross-datasource refs `[ds].[field]` with (ds, field, start, end).

    A normal single-bracket ref to ``[A]`` is NOT a cross-source ref — only
    a two-part dotted form counts. Char span covers the whole ``[A].[B]``.
    """
    if not formula:
        return []
    out: list[tuple[str, str, int, int]] = []
    for m in _CROSS_SOURCE_RE.finditer(formula):
        out.append((
            _unescape(m.group(1)),
            _unescape(m.group(2)),
            m.start(), m.end(),
        ))
    return out


def find_lod_dimensions(formula: str | None) -> list[tuple[str, str, int, int]]:
    """Return LOD dimension refs as (kind, field_name, start, end).

    ``kind`` is ``FIXED`` / ``INCLUDE`` / ``EXCLUDE`` (upper-cased). Each
    bracketed ref inside the dimension list (between the kind keyword and
    the ``:`` separator) is returned with its own span so the source-code
    panel can highlight each dimension independently.

    Example::

        find_lod_dimensions("{FIXED [Region],[Segment]: AVG([Sales])}")
        # → [("FIXED", "Region", 7, 15), ("FIXED", "Segment", 16, 25)]
    """
    if not formula:
        return []
    out: list[tuple[str, str, int, int]] = []
    for m in _LOD_RE.finditer(formula):
        kind = m.group(1).upper()
        dim_body = m.group(2)
        dim_body_start = m.start(2)
        # Find each bracketed identifier inside the dimension list and shift
        # the span to absolute positions in the original formula.
        for r in _BRACKET_REF_RE.finditer(dim_body):
            out.append((
                kind, _unescape(r.group(1)),
                dim_body_start + r.start(),
                dim_body_start + r.end(),
            ))
    return out
