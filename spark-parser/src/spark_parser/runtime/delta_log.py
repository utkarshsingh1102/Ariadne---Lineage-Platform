"""Offline Delta Lake transaction-log reader — v0.2 §5.

Reads a Delta table's ``_delta_log/*.json`` files (one JSON-lines file per
commit) and reconstructs the schema-evolution timeline as a list of
``SchemaEvolutionIR`` events.

The reader does **not** require a running Spark cluster. It parses the
``metaData`` action emitted by Delta on every commit; the ``schemaString``
field of that action is a JSON-serialised Spark schema (struct-of-fields).
Diffing consecutive schemas yields:

  - ``add_column``           — column appears in commit N but not N-1
  - ``drop_column``          — column appears in commit N-1 but not N
  - ``type_change``          — same column name, different ``type``
  - ``nullability_change``   — same column + type, different ``nullable``

A column rename appears as a ``drop_column`` + ``add_column`` pair in raw
Delta logs (Delta has no first-class rename action prior to column mapping).
Detection of "looks-like-a-rename" is a v0.3 concern; this module emits the
raw add/drop pair and leaves it to a higher layer to coalesce.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models.domain import SchemaEvolutionIR, WarningIR


_VERSION_RE = re.compile(r"^(\d+)\.json$")


def read_delta_log(
    log_dir: str | Path,
    *,
    table_fqn: str | None = None,
) -> tuple[list[SchemaEvolutionIR], list[WarningIR]]:
    """Read every ``N.json`` under ``log_dir`` and return evolution events.

    The directory is expected to be ``<table>/_delta_log``. Files are
    processed in ascending version order. Returns a tuple of
    ``(evolution_events, warnings)``.
    """
    log_dir = Path(log_dir)
    warnings: list[WarningIR] = []
    if not log_dir.is_dir():
        warnings.append(WarningIR(
            type="delta_log_missing",
            detail=f"Delta log directory not found: {log_dir}",
        ))
        return [], warnings

    commits: list[tuple[int, Path]] = []
    for entry in log_dir.iterdir():
        m = _VERSION_RE.match(entry.name)
        if m:
            commits.append((int(m.group(1)), entry))
    commits.sort(key=lambda x: x[0])
    if not commits:
        warnings.append(WarningIR(
            type="delta_log_empty",
            detail=f"No N.json commit files found in {log_dir}",
        ))
        return [], warnings

    events: list[SchemaEvolutionIR] = []
    previous_schema: dict[str, dict] = {}        # column name → field dict
    for version, path in commits:
        try:
            metadata, ts = _extract_metadata(path)
        except (OSError, json.JSONDecodeError) as e:
            warnings.append(WarningIR(
                type="delta_log_parse_error",
                detail=f"{path.name}: {e}",
            ))
            continue
        if metadata is None:
            # Commit without a metaData action — schema unchanged.
            continue
        try:
            new_schema = _parse_schema_string(metadata.get("schemaString", ""))
        except (json.JSONDecodeError, ValueError) as e:
            warnings.append(WarningIR(
                type="delta_log_schema_error",
                detail=f"{path.name}: {e}",
            ))
            continue
        events.extend(_diff_schemas(
            previous=previous_schema,
            current=new_schema,
            version=version,
            timestamp_ms=ts,
            table_fqn=table_fqn,
        ))
        previous_schema = new_schema

    return events, warnings


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _extract_metadata(path: Path) -> tuple[dict | None, int | None]:
    """Return the ``metaData`` action (or None) plus commitInfo timestamp."""
    text = path.read_text(encoding="utf-8")
    metadata = None
    ts: int | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        action = json.loads(line)
        if "metaData" in action:
            metadata = action["metaData"]
        if "commitInfo" in action:
            ci = action["commitInfo"]
            t = ci.get("timestamp")
            if isinstance(t, int):
                ts = t
    return metadata, ts


def _parse_schema_string(schema_str: str) -> dict[str, dict]:
    """Spark's schemaString is a JSON struct; return {field_name: field_dict}."""
    if not schema_str:
        return {}
    parsed = json.loads(schema_str)
    if parsed.get("type") != "struct":
        raise ValueError(f"Unexpected schema type: {parsed.get('type')!r}")
    out: dict[str, dict] = {}
    for f in parsed.get("fields", []):
        name = f.get("name")
        if name:
            out[name] = f
    return out


def _diff_schemas(
    *,
    previous: dict[str, dict],
    current: dict[str, dict],
    version: int,
    timestamp_ms: int | None,
    table_fqn: str | None,
) -> list[SchemaEvolutionIR]:
    events: list[SchemaEvolutionIR] = []
    prev_names = set(previous)
    curr_names = set(current)

    for added in sorted(curr_names - prev_names):
        field = current[added]
        events.append(SchemaEvolutionIR(
            table_fqn=table_fqn, version=version, timestamp_ms=timestamp_ms,
            kind="add_column", column=added,
            to_type=_type_to_string(field.get("type")),
            to_nullable=field.get("nullable"),
        ))
    for dropped in sorted(prev_names - curr_names):
        field = previous[dropped]
        events.append(SchemaEvolutionIR(
            table_fqn=table_fqn, version=version, timestamp_ms=timestamp_ms,
            kind="drop_column", column=dropped,
            from_type=_type_to_string(field.get("type")),
            from_nullable=field.get("nullable"),
        ))
    for name in sorted(curr_names & prev_names):
        prev_field = previous[name]
        curr_field = current[name]
        prev_type = _type_to_string(prev_field.get("type"))
        curr_type = _type_to_string(curr_field.get("type"))
        if prev_type != curr_type:
            events.append(SchemaEvolutionIR(
                table_fqn=table_fqn, version=version, timestamp_ms=timestamp_ms,
                kind="type_change", column=name,
                from_type=prev_type, to_type=curr_type,
            ))
        elif prev_field.get("nullable") != curr_field.get("nullable"):
            events.append(SchemaEvolutionIR(
                table_fqn=table_fqn, version=version, timestamp_ms=timestamp_ms,
                kind="nullability_change", column=name,
                from_nullable=prev_field.get("nullable"),
                to_nullable=curr_field.get("nullable"),
            ))

    return events


def _type_to_string(t) -> str | None:
    """Render a Spark schema-string ``type`` value back to a canonical string.

    Simple types (``"string"``, ``"integer"``) come through verbatim. Complex
    types are dicts (``{"type": "array", "elementType": "long", ...}``); we
    serialise their dict form deterministically.
    """
    if t is None:
        return None
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        return json.dumps(t, sort_keys=True, separators=(",", ":"))
    return str(t)
