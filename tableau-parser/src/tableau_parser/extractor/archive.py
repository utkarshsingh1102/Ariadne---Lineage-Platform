"""Extract the inner .twb from a .twbx archive and detect data extracts."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path


class TwbxFormatError(ValueError):
    """Raised when a .twbx is missing required structure (e.g. no .twb inside)."""


def extract_twbx(twbx_path: str | Path, dest_dir: str | Path) -> Path:
    """Unpack `twbx_path` into `dest_dir` and return the inner `.twb` path."""
    twbx_path = Path(twbx_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not zipfile.is_zipfile(twbx_path):
        raise TwbxFormatError(f"Not a zip archive: {twbx_path}")

    with zipfile.ZipFile(twbx_path) as zf:
        zf.extractall(dest_dir)

    twb_path: Path | None = None
    for root, _, files in os.walk(dest_dir):
        for f in files:
            if f.lower().endswith(".twb"):
                candidate = Path(root) / f
                if twb_path is None:
                    twb_path = candidate

    if twb_path is None:
        raise TwbxFormatError(f"No .twb file found inside {twbx_path}")
    return twb_path


def detect_extracts(extract_dir: str | Path) -> list[Path]:
    """Return every `.hyper` / `.tde` file inside `extract_dir` (recursive)."""
    extract_dir = Path(extract_dir)
    out: list[Path] = []
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.lower().endswith((".hyper", ".tde")):
                out.append(Path(root) / f)
    return out


def resolve_input(path: str | Path, dest_dir: str | Path) -> Path:
    """Normalize any input path to a .twb path.

    - `.twb` → returned unchanged
    - `.twbx` → unpacked into `dest_dir`, inner .twb returned
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".twb":
        return p
    if suffix == ".twbx":
        return extract_twbx(p, dest_dir)
    raise TwbxFormatError(f"Unsupported file extension: {p}")
