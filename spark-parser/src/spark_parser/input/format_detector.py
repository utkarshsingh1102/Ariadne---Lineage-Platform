"""Detect the input file format (plan §6 step 1).

Returns one of:
* ``pyspark``                   — plain ``.py``
* ``sparksql``                  — ``.sql``
* ``notebook_jupyter``          — ``.ipynb``
* ``notebook_databricks``       — ``.py`` with ``# Databricks notebook source`` header
* ``notebook_databricks_archive`` — ``.dbc``
* ``scala``                     — ``.scala`` (out of scope for v0.1)
"""
from __future__ import annotations

from pathlib import Path


class FormatDetectionError(ValueError):
    """Raised when the file extension/content doesn't match any supported format."""


def detect_format(path: str | Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()

    if ext == ".sql":
        return "sparksql"
    if ext == ".ipynb":
        return "notebook_jupyter"
    if ext == ".dbc":
        return "notebook_databricks_archive"
    if ext == ".scala":
        return "scala"
    if ext == ".py":
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:512]
        except OSError:
            head = ""
        if "# Databricks notebook source" in head or "# Databricks-format notebook" in head:
            return "notebook_databricks"
        return "pyspark"

    raise FormatDetectionError(f"Unsupported file extension: {ext or '(none)'} on {p}")
