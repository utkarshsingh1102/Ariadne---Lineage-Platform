"""Phase 2 — pre-processor control-flow unrolling.

QlikView script imperative constructs:

  FOR i = 1 TO 3                                FOR EACH file IN 'a','b','c'
    LOAD * FROM data_$(i).qvd;                    LOAD * FROM $(file);
  NEXT i                                        NEXT file

  IF $(vEnv) = 'PROD' THEN                       DO WHILE ...
    SQL SELECT ... FROM PROD.sales;                ...
  ELSEIF $(vEnv) = 'STAGING' THEN                LOOP
    SQL SELECT ... FROM STG.sales;
  ENDIF

ANTLR can't see these because they're imperative (each iteration changes
what statements exist). We unroll them in pure Python BEFORE the grammar
runs so the parser sees three concrete LOADs instead of one parametric one.

Strategy:

- **Static** bounds (literal INT or pre-collected SET/LET variable that
  evaluates to a literal): unroll deterministically. The loop variable
  ``$(i)`` is substituted as a fresh ``SET`` statement at the head of each
  iteration so the existing macro-expansion pass picks it up.
- **Dynamic** bounds (variable unresolved at preprocess time):
  ``Diagnostic(QV-FOR-DYNAMIC)`` and leave the block intact so downstream
  diagnostics name it; the ANTLR pass falls into ``unknownStmt``.
- **Nesting** is honoured via a stack — a FOR inside an IF inside another
  FOR unrolls cleanly.
- **Loop guard**: any unroll producing more than ``_MAX_ITERATIONS``
  iterations short-circuits with a ``Diagnostic(QV-FOR-EXPLOSION)``. This
  prevents a runaway ``FOR i = 1 TO 1000000`` from blowing up the parser.

DO ... LOOP is recognised but NOT unrolled (Phase 2 scope is static FOR /
FOR EACH / IF only). It survives untouched into ANTLR which treats it as
unknownStmt — same fail-soft behaviour as today.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .models import Diagnostic

# Loop unrolls capped at 1000 iterations to prevent runaway expansion.
_MAX_ITERATIONS = 1000

# Block-start / block-end keyword patterns. Match at the start of a line
# (after optional whitespace) to avoid catching the keywords inside
# string literals — a simple but pragmatic heuristic.
_RE_FOR_LINE = re.compile(
    r"^\s*FOR\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s+TO\s+(.+?)(?:\s+STEP\s+(.+?))?\s*$",
    re.IGNORECASE,
)
_RE_FOREACH_LINE = re.compile(
    r"^\s*FOR\s+EACH\s+([A-Za-z_][A-Za-z0-9_]*)\s+IN\s+(.+)$",
    re.IGNORECASE,
)
_RE_NEXT_LINE = re.compile(r"^\s*NEXT(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\s*;?\s*$", re.IGNORECASE)
_RE_IF_LINE = re.compile(r"^\s*IF\s+(.+?)\s+THEN\s*$", re.IGNORECASE)
_RE_ELSEIF_LINE = re.compile(r"^\s*ELSEIF\s+(.+?)\s+THEN\s*$", re.IGNORECASE)
_RE_ELSE_LINE = re.compile(r"^\s*ELSE\s*$", re.IGNORECASE)
_RE_ENDIF_LINE = re.compile(r"^\s*END\s*IF\s*;?\s*$", re.IGNORECASE)
_RE_DO_LINE = re.compile(r"^\s*DO(?:\s+(?:WHILE|UNTIL)\s+.+)?\s*$", re.IGNORECASE)
_RE_LOOP_LINE = re.compile(r"^\s*LOOP\s*(?:WHILE|UNTIL)?\s*.*$", re.IGNORECASE)

# `$(var)` look-up for dynamic-bound resolution against the collected
# SET/LET dict.
_RE_MACRO = re.compile(r"\$\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")
_RE_INT = re.compile(r"^\s*-?\d+\s*$")
_RE_QSTR = re.compile(r"^\s*'([^']*)'\s*$")


@dataclass
class _Block:
    """One control-flow block awaiting unroll."""
    kind: str       # for | foreach | if | do
    head: str       # the FOR/IF/DO line itself
    children: list  # list of strings OR nested _Block instances
    elif_chain: list = None  # for IF — list of (elif_head, [children])
    else_body: list = None   # for IF — list of children for the ELSE branch


def unroll(
    text: str,
    variables: dict[str, str],
    diagnostics: list[Diagnostic],
    *,
    artifact: str = "",
) -> str:
    """Walk ``text`` line-by-line, build a block tree, then materialise."""
    lines = text.splitlines(keepends=True)
    tree = _parse_blocks(lines, diagnostics, artifact)
    return _render(tree, variables, diagnostics, artifact)


# ---------------------------------------------------------------------------
# Block tree builder
# ---------------------------------------------------------------------------


def _parse_blocks(
    lines: list[str], diagnostics: list[Diagnostic], artifact: str,
) -> list:
    """Convert flat lines into a tree of strings + _Block nodes."""
    i = 0
    return _parse_until(lines, 0, len(lines), None, diagnostics, artifact)[0]


def _parse_until(
    lines: list[str],
    start: int,
    end: int,
    terminator: Callable[[str], bool] | None,
    diagnostics: list[Diagnostic],
    artifact: str,
) -> tuple[list, int]:
    """Parse [start, end) until the terminator predicate matches a line.
    Returns (children, index-of-terminator-or-end)."""
    out: list = []
    i = start
    while i < end:
        line = lines[i]
        if terminator is not None and terminator(line):
            return out, i

        # Block starts
        for_m = _RE_FOR_LINE.match(line) if not _RE_FOREACH_LINE.match(line) else None
        foreach_m = _RE_FOREACH_LINE.match(line)
        if_m = _RE_IF_LINE.match(line)
        do_m = _RE_DO_LINE.match(line)

        if foreach_m:
            children, j = _parse_until(
                lines, i + 1, end, _is_next, diagnostics, artifact,
            )
            if j >= end:
                diagnostics.append(Diagnostic(
                    level="warn", code="QV-FOR-UNCLOSED",
                    message="FOR EACH without matching NEXT — block discarded",
                    artifact=artifact, line=i + 1,
                ))
                out.append(line)
                i += 1
                continue
            out.append(_Block(kind="foreach", head=line, children=children))
            i = j + 1
        elif for_m:
            children, j = _parse_until(
                lines, i + 1, end, _is_next, diagnostics, artifact,
            )
            if j >= end:
                diagnostics.append(Diagnostic(
                    level="warn", code="QV-FOR-UNCLOSED",
                    message="FOR without matching NEXT — block discarded",
                    artifact=artifact, line=i + 1,
                ))
                out.append(line)
                i += 1
                continue
            out.append(_Block(kind="for", head=line, children=children))
            i = j + 1
        elif if_m:
            then_children, j = _parse_until(
                lines, i + 1, end,
                lambda ln: _is_elseif(ln) or _is_else(ln) or _is_endif(ln),
                diagnostics, artifact,
            )
            elif_chain: list = []
            else_body: list = []
            while j < end and _is_elseif(lines[j]):
                elif_head = lines[j]
                ec, j = _parse_until(
                    lines, j + 1, end,
                    lambda ln: _is_elseif(ln) or _is_else(ln) or _is_endif(ln),
                    diagnostics, artifact,
                )
                elif_chain.append((elif_head, ec))
            if j < end and _is_else(lines[j]):
                else_body, j = _parse_until(
                    lines, j + 1, end, _is_endif, diagnostics, artifact,
                )
            if j >= end or not _is_endif(lines[j]):
                diagnostics.append(Diagnostic(
                    level="warn", code="QV-IF-UNCLOSED",
                    message="IF without matching ENDIF — block discarded",
                    artifact=artifact, line=i + 1,
                ))
                out.append(line)
                i += 1
                continue
            out.append(_Block(
                kind="if", head=line, children=then_children,
                elif_chain=elif_chain, else_body=else_body,
            ))
            i = j + 1
        elif do_m:
            # DO ... LOOP — recognised but not unrolled (Phase 2 scope).
            children, j = _parse_until(
                lines, i + 1, end, _is_loop, diagnostics, artifact,
            )
            if j >= end:
                # Unclosed DO — pass through.
                out.append(line)
                i += 1
                continue
            diagnostics.append(Diagnostic(
                level="info", code="QV-DO-LOOP-PASSTHROUGH",
                message=(
                    "DO ... LOOP block detected — Phase 2 leaves it untouched "
                    "(parser will treat its body as a sequence of statements)"
                ),
                artifact=artifact, line=i + 1,
            ))
            # Keep the original lines as-is (DO and LOOP markers + body).
            out.append(line)
            out.extend(_flatten(children))
            out.append(lines[j])
            i = j + 1
        else:
            out.append(line)
            i += 1

    return out, i


def _flatten(nodes: list) -> list[str]:
    """Strings + nested blocks → flat string list (for DO passthrough)."""
    out: list[str] = []
    for n in nodes:
        if isinstance(n, str):
            out.append(n)
        else:
            out.extend(_flatten_block_raw(n))
    return out


def _flatten_block_raw(block: _Block) -> list[str]:
    """For DO passthrough we keep the nested block's RAW text — don't unroll."""
    out = [block.head]
    out.extend(_flatten(block.children))
    if block.kind == "if":
        for elif_head, ec in (block.elif_chain or []):
            out.append(elif_head)
            out.extend(_flatten(ec))
        if block.else_body:
            out.append("    ELSE\n")
            out.extend(_flatten(block.else_body))
        out.append("    ENDIF\n")
    elif block.kind in ("for", "foreach"):
        out.append("NEXT\n")
    return out


def _is_next(line: str) -> bool:
    return bool(_RE_NEXT_LINE.match(line))


def _is_elseif(line: str) -> bool:
    return bool(_RE_ELSEIF_LINE.match(line))


def _is_else(line: str) -> bool:
    return bool(_RE_ELSE_LINE.match(line))


def _is_endif(line: str) -> bool:
    return bool(_RE_ENDIF_LINE.match(line))


def _is_loop(line: str) -> bool:
    return bool(_RE_LOOP_LINE.match(line))


# ---------------------------------------------------------------------------
# Render — evaluate + materialise
# ---------------------------------------------------------------------------


def _render(
    tree: list,
    variables: dict[str, str],
    diagnostics: list[Diagnostic],
    artifact: str,
) -> str:
    """Render a tree of (str | _Block) back into a single text blob."""
    out: list[str] = []
    for node in tree:
        if isinstance(node, str):
            out.append(node)
            continue
        out.append(_render_block(node, variables, diagnostics, artifact))
    return "".join(out)


def _render_block(
    block: _Block,
    variables: dict[str, str],
    diagnostics: list[Diagnostic],
    artifact: str,
) -> str:
    if block.kind == "for":
        return _render_for(block, variables, diagnostics, artifact)
    if block.kind == "foreach":
        return _render_foreach(block, variables, diagnostics, artifact)
    if block.kind == "if":
        return _render_if(block, variables, diagnostics, artifact)
    # DO blocks already passed through; shouldn't reach here.
    return _render(block.children, variables, diagnostics, artifact)


def _resolve_expr(expr: str, variables: dict[str, str]) -> str:
    """Substitute ``$(var)`` references against the variables dict — single pass."""
    return _RE_MACRO.sub(
        lambda m: variables.get(m.group(1), m.group(0)),
        expr,
    ).strip()


def _try_int(expr: str, variables: dict[str, str]) -> int | None:
    resolved = _resolve_expr(expr, variables)
    if _RE_INT.match(resolved):
        try:
            return int(resolved)
        except ValueError:
            return None
    return None


def _render_for(
    block: _Block,
    variables: dict[str, str],
    diagnostics: list[Diagnostic],
    artifact: str,
) -> str:
    m = _RE_FOR_LINE.match(block.head)
    if not m:
        return block.head + _render(block.children, variables, diagnostics, artifact) + "NEXT\n"
    loop_var, start_expr, end_expr, step_expr = m.group(1), m.group(2), m.group(3), m.group(4) or "1"

    start = _try_int(start_expr, variables)
    end = _try_int(end_expr, variables)
    step = _try_int(step_expr, variables) or 1
    if start is None or end is None or step == 0:
        diagnostics.append(Diagnostic(
            level="warn", code="QV-FOR-DYNAMIC",
            message=(
                f"FOR {loop_var} = {start_expr.strip()} TO {end_expr.strip()} "
                "could not be unrolled — bounds dynamic or unresolved. "
                "Block left intact; downstream parser may emit unknownStmt."
            ),
            artifact=artifact, line=None,
        ))
        # Pass-through: preserve the original block verbatim.
        body = _render(block.children, variables, diagnostics, artifact)
        return block.head + body + "NEXT\n"

    # Loop-guard.
    if step > 0:
        count = max(0, (end - start) // step + 1)
    else:
        count = max(0, (start - end) // (-step) + 1)
    if count > _MAX_ITERATIONS:
        diagnostics.append(Diagnostic(
            level="warn", code="QV-FOR-EXPLOSION",
            message=(
                f"FOR loop would unroll to {count} iterations (>"
                f"{_MAX_ITERATIONS}). Block left intact."
            ),
            artifact=artifact, line=None,
        ))
        body = _render(block.children, variables, diagnostics, artifact)
        return block.head + body + "NEXT\n"

    # Unroll: substitute the loop variable INLINE into the body of each
    # iteration. Emitting ``SET loop_var = N`` and deferring to the global
    # macro pass would collapse to the last iteration's value because that
    # pass is single-shot — every $(i) gets the FINAL N. Inline substitution
    # closes the transform here so each iteration's body is concrete.
    body = _render(block.children, variables, diagnostics, artifact)
    pieces: list[str] = []
    i = start
    iters = 0
    macro_pat = re.compile(r"\$\(\s*" + re.escape(loop_var) + r"\s*\)")
    while (step > 0 and i <= end) or (step < 0 and i >= end):
        iter_body = macro_pat.sub(str(i), body)
        pieces.append(iter_body)
        i += step
        iters += 1
    return "".join(pieces)


def _render_foreach(
    block: _Block,
    variables: dict[str, str],
    diagnostics: list[Diagnostic],
    artifact: str,
) -> str:
    m = _RE_FOREACH_LINE.match(block.head)
    if not m:
        return block.head + _render(block.children, variables, diagnostics, artifact) + "NEXT\n"
    loop_var, raw_list = m.group(1), m.group(2)

    items = _parse_foreach_list(raw_list, variables)
    if items is None:
        diagnostics.append(Diagnostic(
            level="warn", code="QV-FOREACH-DYNAMIC",
            message=(
                f"FOR EACH {loop_var} IN {raw_list.strip()} could not be "
                "unrolled — list dynamic or unresolved (e.g. filelist()). "
                "Block left intact."
            ),
            artifact=artifact, line=None,
        ))
        body = _render(block.children, variables, diagnostics, artifact)
        return block.head + body + "NEXT\n"

    if len(items) > _MAX_ITERATIONS:
        diagnostics.append(Diagnostic(
            level="warn", code="QV-FOR-EXPLOSION",
            message=f"FOR EACH expands to {len(items)} > {_MAX_ITERATIONS} iterations; block left intact.",
            artifact=artifact, line=None,
        ))
        body = _render(block.children, variables, diagnostics, artifact)
        return block.head + body + "NEXT\n"

    body = _render(block.children, variables, diagnostics, artifact)
    pieces: list[str] = []
    macro_pat = re.compile(r"\$\(\s*" + re.escape(loop_var) + r"\s*\)")
    for item in items:
        # Strip surrounding quotes so ``$(file)`` substitution doesn't end
        # up as ``''a'' '' '' (double-quoted)`` — the body author already
        # wrote the quotes around ``$(file)`` if they want them.
        unquoted = item
        if (len(unquoted) >= 2
                and unquoted[0] == unquoted[-1]
                and unquoted[0] in ("'", '"')):
            unquoted = unquoted[1:-1]
        pieces.append(macro_pat.sub(unquoted, body))
    return "".join(pieces)


def _parse_foreach_list(raw: str, variables: dict[str, str]) -> list[str] | None:
    """Parse the FOR EACH list expression. Supports literal comma-separated
    items (strings or numbers). Returns None if the list contains
    function calls like ``filelist(...)`` we can't evaluate."""
    expr = _resolve_expr(raw.rstrip("\n").rstrip(), variables)
    expr = expr.rstrip(";").strip()
    if "(" in expr and ")" in expr and not expr.startswith("'"):
        # filelist(), dirlist() etc. — dynamic.
        return None
    items: list[str] = []
    cur: list[str] = []
    in_str = False
    str_ch = ""
    for ch in expr:
        if in_str:
            cur.append(ch)
            if ch == str_ch:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            str_ch = ch
            cur.append(ch)
            continue
        if ch == ",":
            items.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        items.append(tail)
    return items if items else None


def _render_if(
    block: _Block,
    variables: dict[str, str],
    diagnostics: list[Diagnostic],
    artifact: str,
) -> str:
    """Evaluate IF predicates in order; render the first truthy branch.
    If no predicate evaluates statically, leave the entire block intact
    (downstream parser handles the IF/ELSEIF/ENDIF tokens as unknownStmt)."""
    head_m = _RE_IF_LINE.match(block.head)
    if not head_m:
        return _render(block.children, variables, diagnostics, artifact)

    branches: list[tuple[str, list]] = [(head_m.group(1), block.children)]
    for elif_head, ec in (block.elif_chain or []):
        em = _RE_ELSEIF_LINE.match(elif_head)
        if em:
            branches.append((em.group(1), ec))

    for pred, body in branches:
        evaluated = _evaluate_predicate(pred, variables)
        if evaluated is True:
            return _render(body, variables, diagnostics, artifact)
        if evaluated is False:
            continue
        # Unknown — bail and leave the whole IF intact.
        diagnostics.append(Diagnostic(
            level="warn", code="QV-IF-DYNAMIC",
            message=(
                f"IF predicate {pred.strip()!r} could not be evaluated "
                "statically — block left intact."
            ),
            artifact=artifact, line=None,
        ))
        return _passthrough_if(block, variables, diagnostics, artifact)

    # No branch matched — take the ELSE if there is one.
    if block.else_body:
        return _render(block.else_body, variables, diagnostics, artifact)
    return ""


def _passthrough_if(
    block: _Block,
    variables: dict[str, str],
    diagnostics: list[Diagnostic],
    artifact: str,
) -> str:
    out = [block.head]
    out.append(_render(block.children, variables, diagnostics, artifact))
    for elif_head, ec in (block.elif_chain or []):
        out.append(elif_head)
        out.append(_render(ec, variables, diagnostics, artifact))
    if block.else_body:
        out.append("ELSE\n")
        out.append(_render(block.else_body, variables, diagnostics, artifact))
    out.append("ENDIF\n")
    return "".join(out)


def _evaluate_predicate(pred: str, variables: dict[str, str]) -> bool | None:
    """Best-effort static evaluation of an IF predicate.

    Returns True/False if we can decide statically, None if the predicate
    references unresolved variables / functions we can't evaluate.

    Supports the simplest cases: ``$(vEnv) = 'PROD'`` style equality and
    ``$(vFlag) = 1`` numeric equality. Anything else returns None.
    """
    resolved = _resolve_expr(pred, variables)
    # If after resolution there's still an unexpanded $(var), bail.
    if _RE_MACRO.search(resolved):
        return None
    # Equality between two quoted strings.
    m = re.match(r"\s*'([^']*)'\s*=\s*'([^']*)'\s*", resolved)
    if m:
        return m.group(1) == m.group(2)
    # Equality between two integers.
    m = re.match(r"\s*(-?\d+)\s*=\s*(-?\d+)\s*", resolved)
    if m:
        return int(m.group(1)) == int(m.group(2))
    # Boolean literal.
    if resolved.strip().lower() in ("1", "true", "-1"):
        return True
    if resolved.strip().lower() in ("0", "false"):
        return False
    return None
