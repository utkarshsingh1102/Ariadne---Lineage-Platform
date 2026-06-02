"""Multi-file merge + commonality analysis for TWS composer files.

Every IR entity carries a content-stable hash id (see ``utils/ids.py``),
so dedup across files is a flat ``dict[id -> first_occurrence]`` over the
six top-level lists in ``ParsedComposerUnit``. Provenance — the list of
source files a given id appeared in — is collected as a side-output so
the commonality report can classify entities as shared (≥2 files) or
file-specific (1 file).

The merged unit is fed back into ``resolve_full`` so cross-file FOLLOWS
edges that were ``unresolved_dependency`` warnings per-file now resolve.
``compute_commonality`` walks the resolved edges plus provenance to
build the user-facing report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tws_parser.models.domain import (
    CalendarIR,
    EventRuleIR,
    JobIR,
    JobStreamIR,
    ParsedComposerUnit,
    PromptIR,
    ResourceIR,
    ScheduleIR,
    WorkstationIR,
)
from tws_parser.parser.dependencies import ResolvedDependencies, Warning


# ---------------------------------------------------------------------------
# Commonality report dataclasses (API-facing)
# ---------------------------------------------------------------------------


@dataclass
class SharedEntity:
    """One entity (workstation / calendar / resource / prompt / job_stream /
    job / script / file_watcher / event_rule / schedule) that appears in
    two or more uploaded files."""
    id: str
    name: str
    label: str
    source_files: list[str]            # always len ≥ 2


@dataclass
class CrossFileFollows:
    """A FOLLOWS edge whose predecessor + successor never co-occur in the
    same file. Means: this dependency only resolves when the files are
    parsed together — solo-parsing either file would leave it unresolved."""
    from_file: str                     # first file that contains the predecessor
    from_job_qualified: str
    to_file: str                       # first file that contains the successor
    to_job_qualified: str
    condition: str | None = None


@dataclass
class CommonalityReport:
    shared_entities: dict[str, list[SharedEntity]] = field(default_factory=dict)
    file_specific: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    cross_file_follows: list[CrossFileFollows] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Merge — dedup-by-id over N ParsedComposerUnits
# ---------------------------------------------------------------------------


# Labels keyed on the dataclass type — used both for provenance bucketing
# and for the commonality report.
_LABEL_FOR_TYPE: dict[type, str] = {
    WorkstationIR: "Workstation",
    JobStreamIR: "JobStream",
    CalendarIR: "Calendar",
    PromptIR: "Prompt",
    EventRuleIR: "EventRule",
    ResourceIR: "Resource",
    ScheduleIR: "Schedule",
    JobIR: "Job",
}


def merge_units(
    units_by_file: dict[str, ParsedComposerUnit],
) -> tuple[ParsedComposerUnit, dict[str, list[str]], list[Warning]]:
    """Dedup the six IR lists across N files by ``.id``.

    Returns:
      - merged unit (first occurrence wins for entity payloads),
      - provenance dict ``node_id -> [file_path, ...]`` (order preserved,
        deduped, len ≥ 1),
      - warnings — currently only ``duplicate_job_definition`` for jobs
        with conflicting payloads across files.
    """
    merged = ParsedComposerUnit()
    provenance: dict[str, list[str]] = {}
    warnings: list[Warning] = []
    # The same job lives in BOTH JobStreamIR.jobs and ScheduleIR.jobs, so
    # the merger visits each conflict twice. Track (job_id, file) pairs
    # we've already warned about so the user sees one warning per real
    # divergence, not two.
    warned_dup_pairs: set[tuple[str, str]] = set()

    # Stable iteration order: process files in the order the caller passed
    # them. dict preserves insertion order in Python 3.7+.
    for file_path, unit in units_by_file.items():
        for ws in unit.workstations:
            _dedup_append(merged.workstations, ws, provenance, file_path)
        for c in unit.calendars:
            _dedup_append(merged.calendars, c, provenance, file_path)
        for p in unit.prompts:
            _dedup_append(merged.prompts, p, provenance, file_path)
        for er in unit.event_rules:
            _dedup_append(merged.event_rules, er, provenance, file_path)
        for r in unit.resources:
            _dedup_append(merged.resources, r, provenance, file_path)

        # Job streams need nested-job merging: same stream id across files
        # may carry different jobs, and we need to union those.
        for incoming in unit.job_streams:
            existing = _by_id(merged.job_streams, incoming.id)
            if existing is None:
                merged.job_streams.append(incoming)
                _bump_provenance(provenance, incoming.id, file_path)
                for j in incoming.jobs:
                    _bump_provenance(provenance, j.id, file_path)
            else:
                _bump_provenance(provenance, incoming.id, file_path)
                _merge_jobs(existing, incoming, provenance, file_path,
                            warnings, warned_dup_pairs)

        # Schedules — same nested-job treatment, since the v0.1 list also
        # carries jobs the writer / postgres path reads.
        for incoming in unit.schedules:
            existing = _by_id(merged.schedules, incoming.id)
            if existing is None:
                merged.schedules.append(incoming)
                _bump_provenance(provenance, incoming.id, file_path)
                for j in incoming.jobs:
                    _bump_provenance(provenance, j.id, file_path)
            else:
                _bump_provenance(provenance, incoming.id, file_path)
                _merge_schedule_jobs(
                    existing, incoming, provenance, file_path,
                    warnings, warned_dup_pairs,
                )

    return merged, provenance, warnings


def _dedup_append(lst, item, provenance: dict[str, list[str]], file_path: str) -> None:
    """Append `item` to `lst` if its id isn't already there, then bump
    provenance for this id with this file."""
    if _by_id(lst, item.id) is None:
        lst.append(item)
    _bump_provenance(provenance, item.id, file_path)


def _by_id(lst, target_id: str):
    for x in lst:
        if x.id == target_id:
            return x
    return None


def _bump_provenance(provenance: dict[str, list[str]], node_id: str, file_path: str) -> None:
    files = provenance.setdefault(node_id, [])
    if file_path not in files:
        files.append(file_path)


def _merge_jobs(
    existing_stream: JobStreamIR,
    incoming_stream: JobStreamIR,
    provenance: dict[str, list[str]],
    file_path: str,
    warnings: list[Warning],
    warned_dup_pairs: set[tuple[str, str]],
) -> None:
    """Union the job lists of two same-id streams. First occurrence wins
    for any given job id; conflicting payloads emit a warning so the
    operator can investigate."""
    by_id = {j.id: j for j in existing_stream.jobs}
    for incoming_job in incoming_stream.jobs:
        existing_job = by_id.get(incoming_job.id)
        if existing_job is None:
            existing_stream.jobs.append(incoming_job)
            by_id[incoming_job.id] = incoming_job
        else:
            _maybe_warn_duplicate(
                existing_job, incoming_job, provenance, file_path,
                warnings, warned_dup_pairs,
            )
        _bump_provenance(provenance, incoming_job.id, file_path)


def _merge_schedule_jobs(
    existing_schedule: ScheduleIR,
    incoming_schedule: ScheduleIR,
    provenance: dict[str, list[str]],
    file_path: str,
    warnings: list[Warning],
    warned_dup_pairs: set[tuple[str, str]],
) -> None:
    by_id = {j.id: j for j in existing_schedule.jobs}
    for incoming_job in incoming_schedule.jobs:
        existing_job = by_id.get(incoming_job.id)
        if existing_job is None:
            existing_schedule.jobs.append(incoming_job)
            by_id[incoming_job.id] = incoming_job
        else:
            _maybe_warn_duplicate(
                existing_job, incoming_job, provenance, file_path,
                warnings, warned_dup_pairs,
            )
        _bump_provenance(provenance, incoming_job.id, file_path)


# Job attributes worth checking for cross-file divergence. Comparing the
# full dataclass would flag every cosmetic difference (e.g. order_in_schedule
# or follows_refs ordering) — these are the meaningful ones.
_JOB_SIGNATURE_FIELDS = (
    "script_path", "script_args", "script_type", "stream_logon",
    "recovery", "recovery_after", "priority", "every",
)


def _maybe_warn_duplicate(
    existing: JobIR,
    incoming: JobIR,
    provenance: dict[str, list[str]],
    file_path: str,
    warnings: list[Warning],
    warned_dup_pairs: set[tuple[str, str]],
) -> None:
    diffs = []
    for field_name in _JOB_SIGNATURE_FIELDS:
        a = getattr(existing, field_name, None)
        b = getattr(incoming, field_name, None)
        if a != b:
            diffs.append(f"{field_name}={a!r} vs {b!r}")
    if not diffs:
        return
    pair = (existing.id, file_path)
    if pair in warned_dup_pairs:
        return
    warned_dup_pairs.add(pair)
    prior_files = list(provenance.get(existing.id, []))
    if file_path not in prior_files:
        prior_files.append(file_path)
    warnings.append(Warning(
        type="duplicate_job_definition",
        detail=(
            f"Job {existing.qualified_name} declared in multiple files "
            f"with conflicting attributes: {'; '.join(diffs)}. "
            f"Files: {prior_files}. Keeping the first occurrence."
        ),
    ))


# ---------------------------------------------------------------------------
# Commonality — shared entities + cross-file FOLLOWS
# ---------------------------------------------------------------------------


def compute_commonality(
    merged: ParsedComposerUnit,
    provenance: dict[str, list[str]],
    deps: ResolvedDependencies,
    file_paths: list[str],
) -> CommonalityReport:
    """Walk the merged unit + provenance + resolved edges to build the
    shared-vs-file-specific report.

    ``shared_entities`` lists entities whose provenance has ≥2 files.
    ``file_specific`` lists, per file, the ids of entities only that file
    declared. ``cross_file_follows`` lists FOLLOWS edges whose predecessor
    + successor never co-occur in any single file — they only resolved
    because all files were parsed together.
    """
    report = CommonalityReport()
    for f in file_paths:
        report.file_specific[f] = {}

    # Index every entity in the merged unit so we can look up its label +
    # name from the id alone (for the SharedEntity payload).
    index = _index_merged(merged)

    for node_id, files in provenance.items():
        entry = index.get(node_id)
        if entry is None:
            # Could be a resource/script/filewatcher we created on the fly
            # in the writer but didn't enumerate as a top-level IR.
            continue
        label, name = entry
        if len(files) >= 2:
            shared_bucket = report.shared_entities.setdefault(label, [])
            shared_bucket.append(SharedEntity(
                id=node_id, name=name, label=label, source_files=list(files),
            ))
        elif len(files) == 1:
            only = files[0]
            label_bucket = report.file_specific.setdefault(only, {}).setdefault(label, [])
            label_bucket.append(node_id)

    # Cross-file FOLLOWS: an edge is cross-file iff there's NO single file
    # that contains BOTH the from-job and the to-job. A shared predecessor
    # (in files [a, b]) that points to a successor in file [b] is NOT
    # cross-file because file b contains both.
    for edge in deps.follows_edges:
        from_files = set(provenance.get(edge.from_job_id, []))
        to_files = set(provenance.get(edge.to_job_id, []))
        if not from_files or not to_files:
            continue
        if from_files & to_files:
            continue   # at least one file sees both endpoints — not cross-file
        report.cross_file_follows.append(CrossFileFollows(
            from_file=sorted(from_files)[0],
            from_job_qualified=edge.from_qualified,
            to_file=sorted(to_files)[0],
            to_job_qualified=edge.to_qualified,
            condition=edge.condition,
        ))

    return report


def _index_merged(merged: ParsedComposerUnit) -> dict[str, tuple[str, str]]:
    """Map every IR id in the merged unit to ``(label, display_name)``."""
    idx: dict[str, tuple[str, str]] = {}
    for w in merged.workstations:
        idx[w.id] = ("Workstation", w.name)
    for c in merged.calendars:
        idx[c.id] = ("Calendar", c.name)
    for p in merged.prompts:
        idx[p.id] = ("Prompt", p.name)
    for er in merged.event_rules:
        idx[er.id] = ("EventRule", er.name)
    for r in merged.resources:
        idx[r.id] = ("Resource", r.name)
    for js in merged.job_streams:
        idx[js.id] = ("JobStream", js.qualified_name)
        for j in js.jobs:
            idx[j.id] = ("Job", j.qualified_name)
    for sc in merged.schedules:
        idx[sc.id] = ("Schedule", sc.name)
        for j in sc.jobs:
            idx.setdefault(j.id, ("Job", j.qualified_name))
    return idx
