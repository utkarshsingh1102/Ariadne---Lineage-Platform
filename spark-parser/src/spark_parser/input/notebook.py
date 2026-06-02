"""Notebook cell extraction (plan §2.3 / §6 step 3).

Supports three forms:
* Jupyter ``.ipynb`` — JSON, parsed via ``nbformat``
* Databricks ``.py`` — plain Python with ``# Databricks notebook source``
  header and ``# COMMAND ----------`` cell separators
* Databricks ``.dbc`` — ZIP archive containing a Jupyter notebook
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class NotebookCell:
    language: str          # "python" | "sql" | "scala" | "markdown" | ...
    source: str
    index: int = 0
    # v0.2 §2 — Jupyter's recorded execution count (``In[5]:``). Lets the
    # visitor detect out-of-order execution. None for Databricks `.py` cells
    # and for non-Jupyter sources where the field doesn't exist.
    execution_count: int | None = None


_DBX_HEADER = "# Databricks notebook source"
_DBX_SEPARATOR = re.compile(r"^\s*#\s*COMMAND\s*-+\s*$", re.MULTILINE)
_MAGIC_LANG = re.compile(r"^\s*#\s*MAGIC\s*%(\w+)", re.MULTILINE)


def extract_cells(path: str | Path) -> list[NotebookCell]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".dbc":
        return _extract_from_dbc(p)
    if suffix == ".ipynb":
        return _extract_from_ipynb(p)
    if suffix == ".py":
        return _extract_from_databricks_py(p)
    raise ValueError(f"Unsupported notebook format: {suffix}")


def concatenate_python_cells(path: str | Path) -> str:
    """Return all Python cells joined in order — feeds the PySpark visitor."""
    cells = [c for c in extract_cells(path) if c.language == "python"]
    return "\n\n".join(c.source for c in cells)


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

def _extract_from_ipynb(p: Path) -> list[NotebookCell]:
    data = json.loads(p.read_text(encoding="utf-8"))
    return _cells_from_ipynb_dict(data)


def _cells_from_ipynb_dict(data: dict) -> list[NotebookCell]:
    out: list[NotebookCell] = []
    nb_lang = (
        data.get("metadata", {})
            .get("language_info", {})
            .get("name", "python")
            .lower()
    )
    for i, cell in enumerate(data.get("cells", [])):
        ctype = cell.get("cell_type")
        if ctype != "code":
            continue                       # skip markdown / raw cells entirely
        lang = (
            cell.get("metadata", {}).get("language")
            or nb_lang
            or "python"
        ).lower()
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        exec_count = cell.get("execution_count")
        out.append(NotebookCell(
            language=lang, source=src, index=i,
            execution_count=exec_count if isinstance(exec_count, int) else None,
        ))
    return out


def _extract_from_dbc(p: Path) -> list[NotebookCell]:
    with zipfile.ZipFile(p) as zf:
        for name in zf.namelist():
            if name.endswith(".ipynb") or name.endswith(".json"):
                with zf.open(name) as f:
                    text = io.TextIOWrapper(f, encoding="utf-8").read()
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                return _cells_from_ipynb_dict(data)
    return []


def _extract_from_databricks_py(p: Path) -> list[NotebookCell]:
    text = p.read_text(encoding="utf-8")
    # Strip the source header line (and any blank lines that follow)
    if text.lstrip().startswith(_DBX_HEADER):
        idx = text.find(_DBX_HEADER) + len(_DBX_HEADER)
        text = text[idx:]
    raw_cells = _DBX_SEPARATOR.split(text)
    out: list[NotebookCell] = []
    for i, raw in enumerate(raw_cells):
        body = raw.strip("\n")
        if not body.strip():
            continue
        # Detect cell language via `# MAGIC %sql / %scala / %md` directive
        lang_match = _MAGIC_LANG.search(body[:200])
        if lang_match:
            kind = lang_match.group(1).lower()
            if kind in {"md", "markdown"}:
                # Drop markdown-only cells entirely (mirrors .ipynb behaviour)
                continue
            lang_label = "sql" if kind == "sql" else kind
            cleaned = re.sub(r"^\s*#\s*MAGIC\s?", "", body, flags=re.MULTILINE)
            out.append(NotebookCell(language=lang_label, source=cleaned, index=i))
            continue
        out.append(NotebookCell(language="python", source=body, index=i))
    return out
