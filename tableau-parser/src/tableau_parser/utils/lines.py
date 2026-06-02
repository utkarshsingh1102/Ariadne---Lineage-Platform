"""Source-line stamping for IR objects.

Mirrors the spark-parser pattern in
``spark-parser/src/spark_parser/pyspark/visitor.py:3107`` — same helper
shape, same idempotent semantics, just adapted from AST ``lineno`` to
lxml ``sourceline`` so the View Source panel can scroll the user to the
exact element that produced each :Tableau* node.

Usage::

    from tableau_parser.utils.lines import first_sourceline, stamp_line

    ir = TableIR(...)
    stamp_line(ir, table_el)             # set ir.line from the element

    # Or, when the helper is composed in a constructor::
    ir = TableIR(..., line=first_sourceline(table_el))
"""

from __future__ import annotations

from typing import Any, Iterable


def first_sourceline(*nodes: Any) -> int | None:
    """Return the first lxml ``sourceline`` from a sequence of inputs.

    Inputs may be a single element, a list of elements, or ``None``. Used
    to derive a line at constructor time when an IR object is built from
    one or more XML elements.
    """
    for n in nodes:
        if n is None:
            continue
        sl = getattr(n, "sourceline", None)
        if isinstance(sl, int) and sl > 0:
            return sl
        if isinstance(n, Iterable) and not isinstance(n, (str, bytes)):
            for item in n:
                sl = getattr(item, "sourceline", None)
                if isinstance(sl, int) and sl > 0:
                    return sl
    return None


def stamp_line(ir: Any, *nodes: Any) -> None:
    """Idempotently set ``ir.line`` from the first input that has one.

    Won't overwrite an already-set line — a caller that knows a more
    precise origin (e.g. the inner ``<calculation>`` line for a calc
    field) can stamp first and a later coarser caller will be a no-op.
    """
    if getattr(ir, "line", None) is not None:
        return
    line = first_sourceline(*nodes)
    if line is None:
        return
    try:
        ir.line = line
    except (AttributeError, TypeError):
        # Frozen dataclass or other non-mutable IR — caller used the wrong
        # IR shape; surface as a silent skip rather than crash the parse.
        pass
