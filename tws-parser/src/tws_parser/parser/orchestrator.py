"""Top-level: path → IR via the correct format path."""

from __future__ import annotations

from pathlib import Path

from tws_parser.models.domain import ParsedComposerUnit, ScheduleIR
from tws_parser.parser import composer, format_detector, merge as merge_mod, xml_export
from tws_parser.parser.dependencies import Warning
from tws_parser.visitor.error_listener import CollectedError


def parse(path: str | Path) -> list[ScheduleIR]:
    """v0.1 convenience: returns schedules only.

    Callers in the API path should use ``parse_full_with_errors`` so the
    full topology (workstations / calendars / prompts / etc.) and
    collected lexer/parser errors both surface — silently dropping either
    was the bug TWS v0.2 was written to fix.
    """
    schedules, _ = parse_with_errors(path)
    return schedules


def parse_with_errors(
    path: str | Path,
) -> tuple[list[ScheduleIR], list[CollectedError]]:
    unit, errors = parse_full_with_errors(path)
    return unit.schedules, errors


def parse_full_with_errors(
    path: str | Path,
) -> tuple[ParsedComposerUnit, list[CollectedError]]:
    """v0.2 — full topology + collected errors, format-detected."""
    fmt = format_detector.detect_format(path)
    if fmt == "xml":
        return xml_export.parse_xml_full_with_errors(path)
    return composer.parse_composer_full_with_errors(str(path))


def parse_multi_with_errors(
    file_paths: list[str | Path],
) -> tuple[
    ParsedComposerUnit,
    dict[str, list[CollectedError]],
    dict[str, list[str]],
    list[Warning],
]:
    """Parse N TWS composer files and merge them into one unit.

    Returns:
      - merged ``ParsedComposerUnit`` (deduplicated by id across all files),
      - per-file collected parse errors keyed by file path,
      - provenance ``node_id -> [file_path, ...]`` so callers can classify
        each merged entity as shared (≥2 files) or file-specific,
      - merge-time warnings (``duplicate_job_definition`` for jobs whose
        cross-file attributes diverge; first occurrence wins).

    Cross-file FOLLOWS edges that were ``unresolved_dependency`` warnings
    in solo parses now resolve against the merged unit — call
    ``resolve_full(merged)`` to get the union edge set, then feed
    ``compute_commonality`` for the report.
    """
    units_by_file: dict[str, ParsedComposerUnit] = {}
    errors_by_file: dict[str, list[CollectedError]] = {}
    for path in file_paths:
        unit, errs = parse_full_with_errors(path)
        units_by_file[str(path)] = unit
        errors_by_file[str(path)] = errs
    merged, provenance, warnings = merge_mod.merge_units(units_by_file)
    return merged, errors_by_file, provenance, warnings
