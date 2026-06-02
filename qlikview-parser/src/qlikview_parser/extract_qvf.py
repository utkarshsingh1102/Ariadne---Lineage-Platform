"""Phase 3 — Qlik Sense ``.qvf`` extractor (v2 plan §1 Stage 1, bucket A).

A QVF is a SQLite database (with optional zlib-compressed blob streams)
that bundles a Qlik Sense app. The load script lives in a single row of
the ``AppObject`` / ``Script`` table; sheets / dimensions / measures /
chart expressions live as JSON blobs in the same table.

For lineage purposes we need two things:

  1. The **load script** — pulled out as plain text and routed through
     the same preprocessor → ANTLR → visitor pipeline used by .qvs files.
  2. The **app objects** (sheets, dimensions, measures, charts) so we
     can emit :UiObject nodes + FEEDS_OBJECT edges from :Attribute
     references inside chart expressions.

This module focuses on (1); :mod:`sense_objects` handles (2).

Safety: SQLite opens are read-only (``mode=ro`` URI) so a corrupted QVF
can't be modified by the parser. Soft-fail by design — a missing /
encrypted / unreadable file yields a structured :class:`Diagnostic`,
never an exception that aborts an estate walk.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Diagnostic


class QvfExtractionError(Exception):
    """Raised only by ``extract_strict`` — the soft-fail ``extract``
    swallows everything and returns a degraded :class:`QvfExtraction`."""


@dataclass
class QvfExtraction:
    """Result of opening a QVF.

    ``script_text``       — the load-script body (UTF-8 text). Empty
                            string when the script row couldn't be located.
    ``app_name``          — best-effort app name from the QVF (falls back
                            on the file stem).
    ``app_objects_raw``   — raw rows from the ``AppObject`` table for
                            consumption by :mod:`sense_objects`.
    ``diagnostics``       — soft-fail findings (``QV-QVF-*`` codes).
    """
    script_text: str = ""
    app_name: str = ""
    app_objects_raw: list[dict] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


# Names of script-bearing tables observed across QlikView Sense versions.
# First table that contains a column matching ``_SCRIPT_COLUMN_HINTS``
# wins. (Real QVFs have differed across Sense releases — a hard-coded
# table name would lock us to one minor version.)
_SCRIPT_TABLE_HINTS: tuple[str, ...] = (
    "Script", "AppScript", "LoadScript", "AppObject", "MainAppObject",
)
_SCRIPT_COLUMN_HINTS: tuple[str, ...] = (
    "script", "scripttext", "body", "content", "definition", "data",
)
_APP_OBJECT_TABLE_HINTS: tuple[str, ...] = (
    "AppObject", "Object", "ChartObject", "SheetObject",
)


def extract(path: Path | str) -> QvfExtraction:
    """Soft-fail QVF extraction. Returns a :class:`QvfExtraction` with
    diagnostics describing any problems — never raises."""
    p = Path(path)
    result = QvfExtraction(app_name=p.stem)
    if not p.exists():
        result.diagnostics.append(Diagnostic(
            level="error", code="QV-QVF-NOT-FOUND",
            message=f"QVF not found: {p!s}",
            artifact=str(p), line=None,
        ))
        return result

    uri = f"file:{p.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, detect_types=0, timeout=2)
    except sqlite3.Error as e:
        result.diagnostics.append(Diagnostic(
            level="error", code="QV-QVF-OPEN",
            message=f"sqlite3 open failed: {e}",
            artifact=str(p), line=None,
        ))
        return result

    try:
        conn.row_factory = sqlite3.Row
        # ---- pull the load script -----------------------------------
        script_text = _find_script_text(conn, result)
        result.script_text = script_text
        # ---- pull raw app objects (chart/sheet/measure rows) -------
        result.app_objects_raw = _read_app_objects(conn, result)
        # ---- best-effort app name -----------------------------------
        result.app_name = _read_app_name(conn) or p.stem
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not result.script_text:
        result.diagnostics.append(Diagnostic(
            level="warn", code="QV-QVF-NO-SCRIPT",
            message="no script-bearing row located in QVF — script-driven lineage will be empty",
            artifact=str(p), line=None,
        ))
    return result


def extract_strict(path: Path | str) -> QvfExtraction:
    """Like :func:`extract` but raises :class:`QvfExtractionError` on
    any error-level diagnostic. Used by tests."""
    result = extract(path)
    fatal = [d for d in result.diagnostics if d.level == "error"]
    if fatal:
        raise QvfExtractionError(fatal[0].message)
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return [row[0] for row in cur.fetchall()]


def _list_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        cur = conn.execute(f"PRAGMA table_info({_safe_ident(table)})")
        return [row[1] for row in cur.fetchall()]
    except sqlite3.Error:
        return []


def _safe_ident(name: str) -> str:
    """Permit only [A-Za-z0-9_] in table names that we template into
    Cypher/SQL. Real QVF tables are all ASCII; anything else is a sign
    of a corrupt/hostile file and we refuse to query it."""
    cleaned = "".join(c for c in name if c.isalnum() or c == "_")
    if not cleaned:
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{cleaned}"'


def _find_script_text(conn: sqlite3.Connection, result: QvfExtraction) -> str:
    tables = _list_tables(conn)
    tbl_set = {t.lower() for t in tables}
    # Walk hint list in preference order.
    for hint in _SCRIPT_TABLE_HINTS:
        match = next((t for t in tables if t.lower() == hint.lower()), None)
        if match is None:
            continue
        cols = _list_columns(conn, match)
        cols_lc = {c.lower(): c for c in cols}
        script_col = next(
            (cols_lc[h] for h in _SCRIPT_COLUMN_HINTS if h in cols_lc),
            None,
        )
        if script_col is None:
            continue
        try:
            cur = conn.execute(
                f"SELECT {_safe_ident(script_col)} FROM {_safe_ident(match)} "
                f"WHERE {_safe_ident(script_col)} IS NOT NULL LIMIT 1"
            )
            row = cur.fetchone()
        except sqlite3.Error as e:
            result.diagnostics.append(Diagnostic(
                level="warn", code="QV-QVF-READ",
                message=f"read {match}.{script_col} failed: {e}",
                artifact="", line=None,
            ))
            continue
        if row is None or row[0] is None:
            continue
        val = row[0]
        # Some QVFs store the script as a JSON blob with a "qScript" field.
        if isinstance(val, bytes):
            try:
                val = val.decode("utf-8", errors="replace")
            except Exception:
                continue
        if isinstance(val, str) and val.lstrip().startswith("{"):
            try:
                parsed = json.loads(val)
                # Common shapes: {"qScript": "..."} or {"script": "..."}
                for k in ("qScript", "script", "scriptText", "body", "Script"):
                    if k in parsed and isinstance(parsed[k], str):
                        return parsed[k]
            except json.JSONDecodeError:
                pass
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _read_app_objects(conn: sqlite3.Connection, result: QvfExtraction) -> list[dict]:
    """Return every row from a recognised app-object table as a dict.
    The downstream :mod:`sense_objects` walker then groups by qType to
    produce :UiObject IR records."""
    tables = _list_tables(conn)
    for hint in _APP_OBJECT_TABLE_HINTS:
        match = next((t for t in tables if t.lower() == hint.lower()), None)
        if match is None:
            continue
        try:
            cur = conn.execute(f"SELECT * FROM {_safe_ident(match)}")
            rows = cur.fetchall()
        except sqlite3.Error as e:
            result.diagnostics.append(Diagnostic(
                level="warn", code="QV-QVF-READ",
                message=f"read {match} failed: {e}",
                artifact="", line=None,
            ))
            continue
        return [dict(r) for r in rows]
    return []


def _read_app_name(conn: sqlite3.Connection) -> str | None:
    """Best-effort app name lookup — some QVFs store it under an 'App'
    or 'AppInfo' table with a 'qTitle' / 'title' column. Returns None if
    no recognised shape matches."""
    for tbl in ("App", "AppInfo", "Header", "MainAppProperties"):
        match_t = next(
            (t for t in _list_tables(conn) if t.lower() == tbl.lower()),
            None,
        )
        if match_t is None:
            continue
        for col in ("qTitle", "title", "name", "appName"):
            cols = {c.lower() for c in _list_columns(conn, match_t)}
            if col.lower() not in cols:
                continue
            try:
                cur = conn.execute(
                    f"SELECT {_safe_ident(col)} FROM {_safe_ident(match_t)} LIMIT 1"
                )
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
            except sqlite3.Error:
                continue
    return None


# ---------------------------------------------------------------------------
# Synthetic QVF writer — used by tests only. Builds a minimal SQLite DB
# with one Script table and one AppObject table so the integration tests
# don't depend on shipping a real Sense app binary.
# ---------------------------------------------------------------------------


def write_synthetic_qvf(
    path: Path | str,
    script_text: str,
    app_objects: list[dict] | None = None,
    app_name: str = "TestApp",
) -> None:
    """Build a tiny SQLite-based QVF stand-in for tests."""
    p = Path(path)
    if p.exists():
        p.unlink()
    conn = sqlite3.connect(str(p))
    try:
        conn.execute("CREATE TABLE Script (id INTEGER PRIMARY KEY, script TEXT)")
        conn.execute(
            "INSERT INTO Script (script) VALUES (?)",
            (script_text,),
        )
        conn.execute(
            "CREATE TABLE AppInfo (id INTEGER PRIMARY KEY, qTitle TEXT)"
        )
        conn.execute("INSERT INTO AppInfo (qTitle) VALUES (?)", (app_name,))
        if app_objects:
            # Build the AppObject table from the union of keys across rows.
            cols = sorted({k for row in app_objects for k in row.keys()})
            cols_sql = ", ".join(f'"{c}" TEXT' for c in cols)
            conn.execute(f"CREATE TABLE AppObject ({cols_sql})")
            for row in app_objects:
                placeholders = ", ".join("?" for _ in cols)
                conn.execute(
                    f'INSERT INTO AppObject ({", ".join(f"{chr(34)}{c}{chr(34)}" for c in cols)}) '
                    f"VALUES ({placeholders})",
                    tuple(row.get(c) for c in cols),
                )
        conn.commit()
    finally:
        conn.close()
