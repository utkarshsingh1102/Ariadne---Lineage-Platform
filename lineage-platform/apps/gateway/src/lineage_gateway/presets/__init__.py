"""Pre-built Cypher queries — one file per preset name.

The ``preset_cypher(name)`` helper reads the file lazily so that adding a
new preset is a pure-content change (drop a ``.cypher`` file in this
directory) and requires no Python code edits.
"""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent


class UnknownPresetError(KeyError):
    """Raised when /graph/query/preset/{name} references an unknown query."""


def preset_cypher(name: str) -> str:
    safe = name.replace("/", "").replace("\\", "")
    path = _DIR / f"{safe}.cypher"
    if not path.is_file():
        raise UnknownPresetError(name)
    return path.read_text(encoding="utf-8")


def list_presets() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.cypher"))
