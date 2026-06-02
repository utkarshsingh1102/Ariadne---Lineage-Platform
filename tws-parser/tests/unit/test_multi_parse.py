"""Multi-file parse + commonality analysis (Phase 1 of the multi-file feature).

Covers:
- Merging two distinct files dedups shared entities by id and accumulates
  provenance (which files declared each id).
- Cross-file FOLLOWS that solo-parses leave as ``unresolved_dependency``
  resolves when both files are merged before ``resolve_full``.
- Duplicate job definitions across files with diverging payloads emit a
  ``duplicate_job_definition`` warning rather than silently overwriting.
- A workstation-only file paired with a schedule file leaves no
  shared entities but populates file_specific buckets correctly.
"""

from __future__ import annotations

from tws_parser.parser.dependencies import resolve_full
from tws_parser.parser.merge import compute_commonality, merge_units
from tws_parser.parser.orchestrator import parse_full_with_errors, parse_multi_with_errors


def test_shared_workstation_and_calendar_surface(fixture_path):
    a = str(fixture_path("multi/a_ingestion.txt"))
    b = str(fixture_path("multi/b_reporting.txt"))

    merged, errs, prov, warns = parse_multi_with_errors([a, b])

    assert errs[a] == [] and errs[b] == []
    assert warns == []

    # ETL_AGENT_01 appears in both files; BI_AGENT_01 only in B.
    ws_names = {w.name for w in merged.workstations}
    assert ws_names == {"ETL_AGENT_01", "BI_AGENT_01"}

    # Calendar WORKDAYS shared.
    cal_names = {c.name for c in merged.calendars}
    assert cal_names == {"WORKDAYS"}

    deps = resolve_full(merged)
    report = compute_commonality(merged, prov, deps, [a, b])

    shared_ws = report.shared_entities.get("Workstation", [])
    assert len(shared_ws) == 1
    assert shared_ws[0].name == "ETL_AGENT_01"
    assert set(shared_ws[0].source_files) == {a, b}

    shared_cal = report.shared_entities.get("Calendar", [])
    assert len(shared_cal) == 1
    assert shared_cal[0].name == "WORKDAYS"


def test_cross_file_follows_resolves_when_merged(fixture_path):
    a = str(fixture_path("multi/a_ingestion.txt"))
    b = str(fixture_path("multi/b_reporting.txt"))

    # Solo-parse of B should report the FOLLOWS as unresolved.
    unit_b, _ = parse_full_with_errors(b)
    deps_b_solo = resolve_full(unit_b)
    unresolved = [w for w in deps_b_solo.warnings if w.type == "unresolved_dependency"]
    assert any("LOAD_FACTS" in w.detail for w in unresolved), (
        "Solo-parse of B must flag the cross-file FOLLOWS as unresolved"
    )

    # Multi-parse should resolve it — zero unresolved_dependency warnings.
    merged, _, prov, _ = parse_multi_with_errors([a, b])
    deps = resolve_full(merged)
    assert not [w for w in deps.warnings if w.type == "unresolved_dependency"]

    # And it should appear as exactly one CrossFileFollows in the report.
    report = compute_commonality(merged, prov, deps, [a, b])
    cross = [
        cf for cf in report.cross_file_follows
        if "LOAD_FACTS" in cf.to_job_qualified and "REFRESH_REPORTS" in cf.from_job_qualified
    ]
    assert len(cross) == 1
    assert cross[0].from_file == b
    assert cross[0].to_file == a


def test_file_specific_entities_partition_correctly(fixture_path):
    a = str(fixture_path("multi/a_ingestion.txt"))
    b = str(fixture_path("multi/b_reporting.txt"))

    merged, _, prov, _ = parse_multi_with_errors([a, b])
    deps = resolve_full(merged)
    report = compute_commonality(merged, prov, deps, [a, b])

    # A-only: the DB_CONN_POOL resource and INGESTION_SIGNOFF prompt.
    a_resources = report.file_specific[a].get("Resource", [])
    assert len(a_resources) == 1
    a_prompts = report.file_specific[a].get("Prompt", [])
    assert len(a_prompts) == 1

    # B-only: the BI_AGENT_01 workstation and REPORT_SIGNOFF prompt.
    b_workstations = report.file_specific[b].get("Workstation", [])
    assert len(b_workstations) == 1
    b_prompts = report.file_specific[b].get("Prompt", [])
    assert len(b_prompts) == 1


def test_duplicate_job_definition_emits_warning(fixture_path, tmp_path):
    """Two files declaring the same (workstation, stream, name) with
    different SCRIPTNAMEs must emit a duplicate_job_definition warning
    and keep the first file's payload."""
    fixture_a = tmp_path / "dup_a.txt"
    fixture_b = tmp_path / "dup_b.txt"
    fixture_a.write_text(
        "SCHEDULE WS_X#MY_STREAM\n"
        "  AT 0500\n"
        ":\n"
        "  MYJOB\n"
        "    SCRIPTNAME \"/apps/run_v1.sh\"\n"
        "    STREAMLOGON tws_user\n"
        "END\n"
    )
    fixture_b.write_text(
        "SCHEDULE WS_X#MY_STREAM\n"
        "  AT 0500\n"
        ":\n"
        "  MYJOB\n"
        "    SCRIPTNAME \"/apps/run_v2.sh\"\n"     # divergent script
        "    STREAMLOGON tws_user\n"
        "END\n"
    )

    merged, _, prov, warns = parse_multi_with_errors([str(fixture_a), str(fixture_b)])

    dup_warnings = [w for w in warns if w.type == "duplicate_job_definition"]
    assert len(dup_warnings) == 1
    assert "MYJOB" in dup_warnings[0].detail
    assert "run_v1" in dup_warnings[0].detail and "run_v2" in dup_warnings[0].detail

    # First occurrence wins — the merged job carries file A's script.
    merged_job = next(
        j for js in merged.job_streams for j in js.jobs if j.name == "MYJOB"
    )
    assert merged_job.script_path == "/apps/run_v1.sh"

    # Provenance reflects both files even though only one payload survived.
    assert set(prov[merged_job.id]) == {str(fixture_a), str(fixture_b)}


def test_identical_files_dedup_completely(fixture_path):
    """Parsing the same file twice produces one merged unit identical to
    a single parse, plus provenance showing both files for every id."""
    a = str(fixture_path("multi/a_ingestion.txt"))

    single, _ = parse_full_with_errors(a)
    merged, _, prov, warns = parse_multi_with_errors([a, a])

    assert warns == []
    assert len(merged.workstations) == len(single.workstations)
    assert len(merged.job_streams) == len(single.job_streams)
    assert all(j.name for js in merged.job_streams for j in js.jobs)

    # Every entity should have both file paths in its provenance; since
    # we passed the same path twice, dedup leaves a single entry.
    for files in prov.values():
        assert files == [a]


def test_merge_units_preserves_first_payload():
    """Direct merge_units call: same workstation id but file B carries a
    different description — the first-seen payload wins."""
    from tws_parser.models.domain import ParsedComposerUnit, WorkstationIR

    unit_a = ParsedComposerUnit(
        workstations=[WorkstationIR(name="WS1", description="From A")]
    )
    unit_b = ParsedComposerUnit(
        workstations=[WorkstationIR(name="WS1", description="From B")]
    )
    merged, prov, _ = merge_units({"a.txt": unit_a, "b.txt": unit_b})

    assert len(merged.workstations) == 1
    assert merged.workstations[0].description == "From A"
    assert set(prov[merged.workstations[0].id]) == {"a.txt", "b.txt"}
