"""Stage 1 — pre-processor (plain Python, runs before ANTLR).

Five jobs, in this order:

1. **Encoding detection** — sniff with ``chardet`` when available; fall back to
   a tiered list of common QlikView encodings. Always returns UTF-8 text.
2. **Include resolution** — inline ``$(Include=path)``, ``$(Must_Include=path)``,
   and legacy ``INCLUDE 'path';`` references. Recurses with a cycle guard and
   a configurable depth limit.
3. **Variable harvest** — collect SET/LET so the control-flow pass can
   evaluate static FOR/IF bounds without firing the full macro expansion.
4. **Control-flow unrolling** (Phase 2) — FOR / FOR EACH / IF blocks are
   evaluated and materialised into concrete statements before ANTLR runs.
   See ``control_flow.py`` for the semantics.
5. **Macro expansion** — final ``$(varName)`` substitution pass, including
   the freshly-emitted loop-variable SETs from unrolling.

The output is a fully-inlined, fully-expanded UTF-8 string that ANTLR can lex
without any pre-processor escape hatches.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .control_flow import unroll as _unroll_control_flow
from .models import Diagnostic

# ---------------------------------------------------------------------------
# Regexes used purely at this layer (not for parsing — for text rewrites).
# ---------------------------------------------------------------------------

_RE_MODERN_INCLUDE = re.compile(
    r"\$\(\s*(Must_Include|Include)\s*=\s*([^)]+?)\s*\)\s*;?",
    re.IGNORECASE,
)
_RE_LEGACY_INCLUDE = re.compile(
    r"\bINCLUDE\s+'([^']+)'\s*;?",
    re.IGNORECASE,
)
_RE_SET = re.compile(r"\bSET\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]*);",
                     re.IGNORECASE)
_RE_LET = re.compile(r"\bLET\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]*);",
                     re.IGNORECASE)
_RE_MACRO = re.compile(r"\$\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")

# SUB definition with optional parameter list:
#   SUB Name(p1, p2, ...)  <body>  END SUB
#   SUB Name              <body>  END SUB
_RE_SUB = re.compile(
    r"\bSUB\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?(.*?)\bEND\s+SUB\b",
    re.IGNORECASE | re.DOTALL,
)
_RE_CALL = re.compile(
    r"\bCALL\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s*;?",
    re.IGNORECASE,
)

# Stripping a `lib://...` or Windows-style path prefix when resolving include
# paths against the local filesystem. QlikView's `lib://` URIs map to the
# include root configured by env var, not to a real URL scheme.
_RE_LIB_PREFIX = re.compile(r"^lib://", re.IGNORECASE)


@dataclass
class PreprocessResult:
    text: str                       # fully-inlined, fully-expanded UTF-8 source
    includes: list[str] = field(default_factory=list)
    variables: list[tuple[str, str, str]] = field(default_factory=list)  # (name, value, scope)
    macro_expansions: int = 0
    parse_errors: list[str] = field(default_factory=list)
    subroutines: list[tuple[str, list[str]]] = field(default_factory=list)  # (name, params)
    call_expansions: int = 0
    # v0.2 — structured findings from control-flow unrolling (and beyond).
    # The orchestrator merges these into ``QlikViewApp.diagnostics``.
    diagnostics: list[Diagnostic] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def preprocess(
    path: str | Path,
    *,
    include_root: str | None = None,
    max_depth: int = 10,
) -> PreprocessResult:
    """Read ``path`` and return the inlined / expanded text plus metadata."""
    p = Path(path)
    result = PreprocessResult(text="")
    text = _read_text(p)
    seen = {str(p.resolve())}
    inlined = _resolve_includes(
        text, p.parent, include_root, seen, result, depth=0, max_depth=max_depth,
    )

    # Phase 2 — control-flow unrolling.
    # We need the variable dict BEFORE the full macro pass so that
    # ``FOR i = 1 TO $(vCount)`` can resolve ``vCount`` to its literal.
    # Build a one-shot dict from the current SET/LET text and feed it in;
    # the dedicated _expand_macros() pass later does the full substitution.
    early_vars = _harvest_variables(inlined)
    inlined = _unroll_control_flow(
        inlined, early_vars, result.diagnostics, artifact=str(p),
    )

    # Resolve SUB / CALL before global macro expansion so the inliner can
    # substitute the SUB's local parameters first, then defer to the global
    # SET/LET pass for everything else.
    inlined = _inline_subroutines(inlined, result)
    expanded = _expand_macros(inlined, result)
    result.text = expanded
    return result


def _harvest_variables(text: str) -> dict[str, str]:
    """Return a name → value dict of every SET/LET in ``text``.
    Used to feed the control-flow unroller without firing the full
    macro-expansion pass yet."""
    out: dict[str, str] = {}
    for m in _RE_SET.finditer(text):
        out[m.group(1)] = m.group(2).strip()
    for m in _RE_LET.finditer(text):
        out[m.group(1)] = m.group(2).strip()
    return out


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------

def _read_text(p: Path) -> str:
    """Return the file's contents as UTF-8 text, sniffing the encoding."""
    data = p.read_bytes()
    try:
        import chardet  # type: ignore

        guess = chardet.detect(data) or {}
        enc = guess.get("encoding")
        if enc:
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
    except ImportError:
        pass
    for enc in ("utf-8-sig", "utf-8", "utf-16", "windows-1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Include resolution (recursive, cycle-guarded, depth-limited)
# ---------------------------------------------------------------------------

def _resolve_path(raw: str, base_dir: Path, include_root: str | None) -> Path | None:
    """Convert a QlikView include path into a real filesystem path."""
    raw = raw.strip().strip("'\"")
    raw = _RE_LIB_PREFIX.sub("", raw)
    raw = raw.replace("\\", "/")
    candidate = Path(raw)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    local = (base_dir / raw).resolve()
    if local.exists():
        return local
    if include_root:
        rooted = (Path(include_root) / raw).resolve()
        if rooted.exists():
            return rooted
    return None


def _resolve_includes(
    text: str,
    base_dir: Path,
    include_root: str | None,
    seen: set[str],
    result: PreprocessResult,
    *,
    depth: int,
    max_depth: int,
) -> str:
    if depth > max_depth:
        result.parse_errors.append(
            f"Include depth limit ({max_depth}) exceeded — aborting recursion",
        )
        return text

    def _inline(match: re.Match[str], raw_path: str) -> str:
        resolved = _resolve_path(raw_path, base_dir, include_root)
        if resolved is None:
            result.parse_errors.append(f"Include not found: {raw_path}")
            return ""
        result.includes.append(str(resolved))
        key = str(resolved)
        if key in seen:
            # cycle — break the chain silently
            return ""
        seen.add(key)
        inner = _read_text(resolved)
        return "\n" + _resolve_includes(
            inner, resolved.parent, include_root, seen, result,
            depth=depth + 1, max_depth=max_depth,
        ) + "\n"

    # Modern syntax first ($(Include=...) and $(Must_Include=...))
    out: list[str] = []
    cursor = 0
    for m in _RE_MODERN_INCLUDE.finditer(text):
        out.append(text[cursor : m.start()])
        out.append(_inline(m, m.group(2)))
        cursor = m.end()
    out.append(text[cursor:])
    text = "".join(out)

    # Legacy syntax: INCLUDE 'path';
    out = []
    cursor = 0
    for m in _RE_LEGACY_INCLUDE.finditer(text):
        out.append(text[cursor : m.start()])
        out.append(_inline(m, m.group(1)))
        cursor = m.end()
    out.append(text[cursor:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Macro expansion ($(varName) substitution)
# ---------------------------------------------------------------------------

def _expand_macros(text: str, result: PreprocessResult) -> str:
    """Collect SET/LET vars, then substitute ``$(varName)`` everywhere."""
    variables: dict[str, str] = {}
    for m in _RE_SET.finditer(text):
        name, value = m.group(1), m.group(2).strip()
        variables[name] = value
        result.variables.append((name, value, "set"))
    for m in _RE_LET.finditer(text):
        name, value = m.group(1), m.group(2).strip()
        variables[name] = value
        result.variables.append((name, value, "let"))

    # Expand iteratively to support nested macro references.
    seen_passes = 0
    while seen_passes < 5:
        replaced = 0

        def _sub(match: re.Match[str]) -> str:
            nonlocal replaced
            name = match.group(1)
            if name in variables:
                replaced += 1
                return variables[name]
            return match.group(0)

        new_text = _RE_MACRO.sub(_sub, text)
        if replaced == 0 or new_text == text:
            break
        result.macro_expansions += replaced
        text = new_text
        seen_passes += 1

    return text


# ---------------------------------------------------------------------------
# SUB / CALL inlining
# ---------------------------------------------------------------------------

def _split_call_args(raw: str) -> list[str]:
    """Split CALL args on top-level commas, respecting quotes and parens."""
    args: list[str] = []
    buf: list[str] = []
    depth = 0
    in_str = False
    str_char = ""
    for ch in raw:
        if in_str:
            buf.append(ch)
            if ch == str_char:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            str_char = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        args.append(tail)
    return [a.strip().strip("'\"") for a in args]


def _substitute_params(body: str, params: list[str], args: list[str]) -> str:
    """Replace ``$(param)`` references inside ``body`` with their CALL-site values."""
    out = body
    for pname, aval in zip(params, args):
        out = re.sub(
            r"\$\(\s*" + re.escape(pname) + r"\s*\)",
            aval,
            out,
        )
    return out


def _inline_subroutines(text: str, result: PreprocessResult) -> str:
    """Lift SUB/END SUB blocks out of ``text`` and expand each CALL site inline."""
    subs: dict[str, tuple[list[str], str]] = {}

    def _capture(match: re.Match[str]) -> str:
        name = match.group(1)
        params_raw = match.group(2) or ""
        body = match.group(3)
        params = [p.strip() for p in params_raw.split(",") if p.strip()]
        subs[name] = (params, body)
        result.subroutines.append((name, params))
        return ""  # remove definition from the source

    text = _RE_SUB.sub(_capture, text)

    def _expand(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in subs:
            return match.group(0)
        params, body = subs[name]
        args = _split_call_args(match.group(2) or "")
        result.call_expansions += 1
        return "\n" + _substitute_params(body, params, args) + "\n"

    return _RE_CALL.sub(_expand, text)


# ---------------------------------------------------------------------------
# Convenience accessors used by tests / downstream consumers
# ---------------------------------------------------------------------------

def default_include_root() -> str | None:
    return os.environ.get("QLIK_INCLUDE_ROOT") or None
