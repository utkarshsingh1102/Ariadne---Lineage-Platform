"""Resolve job- and schedule-level FOLLOWS dependencies + the v0.2 topology edges.

v0.1 surface — ``resolve(schedules: list[ScheduleIR]) -> ResolvedDependencies``
emits the legacy ``job_dependencies`` + ``schedule_dependencies`` shape. The
~5 existing tests + the Postgres writer still use this.

v0.2 surface — ``resolve_full(unit: ParsedComposerUnit) -> ResolvedDependencies``
builds the full topology edge set (follows / recovery / runs-on /
requires-resource / waits-for-prompt / triggers / scheduled-by / opens) with
INTERNAL-vs-EXTERNAL scope honored:

* internal FOLLOWS — bare name; resolved in the SAME job stream only,
  never falling back to other streams (the collision trap).
* external FOLLOWS — ``WORKSTATION#STREAM.JOB``; resolved across the full
  batch. Missing target → ``unresolved_dependency`` warning, never silently
  dropped.

The TRIGGERS edge resolves an EventRule's ``target_stream_qualified``
(``WORKSTATION#STREAM``) to a JobStream id.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tws_parser.models.domain import (
    FollowsRef,
    JobIR,
    JobStreamIR,
    ParsedComposerUnit,
    ScheduleIR,
)
from tws_parser.utils.ids import (
    calendar_id,
    event_rule_id,
    file_watcher_id,
    job_id as job_id_qualified,
    job_id_legacy,
    job_stream_id,
    prompt_id,
    resource_id,
    workstation_id,
)


# ---------------------------------------------------------------------------
# Edge IR dataclasses (v0.2)
# ---------------------------------------------------------------------------


@dataclass
class FollowsEdge:
    from_job_id: str
    to_job_id: str
    condition: str | None = None         # ``SUCC`` | ``ABEND`` | ``RC=0`` | None
    scope: str = "internal"              # ``internal`` | ``external``
    from_qualified: str = ""
    to_qualified: str = ""


@dataclass
class RecoveryEdge:
    from_job_id: str
    to_recovery_job_id: str
    recovery_action: str = ""            # ``STOP`` | ``RERUN`` | ``CONTINUE``
    from_qualified: str = ""
    to_qualified: str = ""


@dataclass
class RunsOnEdge:
    job_id: str
    workstation_id: str


@dataclass
class RequiresResourceEdge:
    job_id: str
    resource_id: str
    quantity: int = 1
    resource_name: str = ""


@dataclass
class WaitsForPromptEdge:
    job_id: str
    prompt_id: str
    prompt_name: str = ""


@dataclass
class TriggersEdge:
    event_rule_id: str
    job_stream_id: str
    target_stream_qualified: str = ""


@dataclass
class ScheduledByEdge:
    job_stream_id: str
    calendar_id: str
    calendar_name: str = ""


@dataclass
class OpensEdge:
    job_id: str
    file_watcher_id: str
    path: str = ""


# ---------------------------------------------------------------------------
# v0.1 dataclasses (kept for the Postgres writer + existing tests)
# ---------------------------------------------------------------------------


@dataclass
class JobDependency:
    job: str
    depends_on: str
    schedule: str = ""


@dataclass
class ScheduleDependency:
    schedule: str
    depends_on_schedule: str
    target_workstation: str = ""
    wildcard: bool = False


@dataclass
class Warning:
    type: str
    detail: str


@dataclass
class ResolvedDependencies:
    # v0.1 fields — Postgres writer + ~5 existing tests still read these.
    job_dependencies: list[JobDependency] = field(default_factory=list)
    schedule_dependencies: list[ScheduleDependency] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)

    # v0.2 edge lists — Neo4j writer in Phase 5 consumes these.
    follows_edges: list[FollowsEdge] = field(default_factory=list)
    recovery_edges: list[RecoveryEdge] = field(default_factory=list)
    runs_on_edges: list[RunsOnEdge] = field(default_factory=list)
    requires_resource_edges: list[RequiresResourceEdge] = field(default_factory=list)
    waits_for_prompt_edges: list[WaitsForPromptEdge] = field(default_factory=list)
    triggers_edges: list[TriggersEdge] = field(default_factory=list)
    scheduled_by_edges: list[ScheduledByEdge] = field(default_factory=list)
    opens_edges: list[OpensEdge] = field(default_factory=list)


# ---------------------------------------------------------------------------
# v0.1 — schedule-only resolution
# ---------------------------------------------------------------------------


def resolve(schedules: list[ScheduleIR]) -> ResolvedDependencies:
    """v0.1 — keeps the Postgres writer + legacy tests working.

    Resolves job-level FOLLOWS (bare names, scoped per-schedule) and
    schedule-level FOLLOWS. The v0.2 topology edges (recovery / runs-on /
    requires-resource / etc.) are NOT populated by this entry point — use
    ``resolve_full`` against a ``ParsedComposerUnit`` for those.
    """
    out = ResolvedDependencies()
    schedule_names = {s.name for s in schedules}

    for s in schedules:
        job_names = {j.name for j in s.jobs}
        for job in s.jobs:
            for dep_name in job.follows:
                if dep_name in job_names:
                    out.job_dependencies.append(
                        JobDependency(job=job.name, depends_on=dep_name, schedule=s.name)
                    )
                else:
                    out.warnings.append(Warning(
                        type="unresolved_dependency",
                        detail=f"Job {s.name}.{job.name} FOLLOWS {dep_name} "
                               "(target job not in batch)",
                    ))

        for sf in s.schedule_follows:
            target = sf.target_schedule
            if target in schedule_names:
                out.schedule_dependencies.append(ScheduleDependency(
                    schedule=s.name,
                    depends_on_schedule=target,
                    target_workstation=sf.target_workstation,
                    wildcard=sf.wildcard,
                ))
            else:
                out.warnings.append(Warning(
                    type="unresolved_dependency",
                    detail=f"Schedule {s.name} FOLLOWS "
                           f"{sf.target_workstation}#{target} (target not in batch)",
                ))
    return out


# ---------------------------------------------------------------------------
# v0.2 — full topology resolution
# ---------------------------------------------------------------------------


def resolve_full(unit: ParsedComposerUnit) -> ResolvedDependencies:
    """v0.2 — build every topology edge type from a ParsedComposerUnit.

    Resolution rules (the standing requirements from the plan):

    * Internal FOLLOWS targets resolve only against jobs in the SAME stream;
      bare names never silently fall back to a same-named job in another
      stream. That's the collision trap (the two VALIDATE jobs).
    * External FOLLOWS (``WS#STREAM.JOB``) resolve only against the exact
      qualified target. Missing → ``unresolved_dependency`` warning (these
      are common in multi-file estates and must NOT be guessed).
    * RECOVERY AFTER follows the same rules.
    * Event-rule TRIGGERS resolves the ``target_stream_qualified``
      (``WS#STREAM``) to a JobStream id.

    Returns a ResolvedDependencies with BOTH the v0.1 fields (so the
    Postgres writer keeps working) AND the v0.2 edge lists populated.
    """
    out = ResolvedDependencies()

    # Build the lookup indexes once.
    jobs_by_qualified: dict[tuple[str, str, str], JobIR] = {}
    jobs_by_stream: dict[tuple[str, str], dict[str, JobIR]] = {}
    streams_by_qualified: dict[tuple[str, str], JobStreamIR] = {}
    schedule_names = {s.name for s in unit.schedules}
    calendar_names = {c.name for c in unit.calendars}
    prompt_names = {p.name for p in unit.prompts}

    for stream in unit.job_streams:
        streams_by_qualified[(stream.workstation, stream.name)] = stream
        in_stream: dict[str, JobIR] = {}
        for job in stream.jobs:
            key = (job.workstation, job.stream, job.name)
            jobs_by_qualified[key] = job
            in_stream[job.name] = job
        jobs_by_stream[(stream.workstation, stream.name)] = in_stream

    # ---- FOLLOWS edges + the v0.1 job_dependencies mirror ---------------
    for stream in unit.job_streams:
        in_stream = jobs_by_stream[(stream.workstation, stream.name)]
        for job in stream.jobs:
            # Emit RUNS_ON for every job — every job runs on its parent
            # stream's workstation. This is the v0.2 explicit edge that
            # earlier versions assumed implicitly.
            out.runs_on_edges.append(RunsOnEdge(
                job_id=job.id,
                workstation_id=workstation_id(job.workstation),
            ))

            # RESOURCE / PROMPT / OPENS edges keyed on the deterministic ids
            # — we don't need a lookup table because the IRs hash by name.
            for res_name, qty in job.needs:
                out.requires_resource_edges.append(RequiresResourceEdge(
                    job_id=job.id,
                    resource_id=resource_id(res_name),
                    quantity=qty,
                    resource_name=res_name,
                ))
                if res_name not in {r.name for r in unit.resources}:
                    out.warnings.append(Warning(
                        type="unresolved_resource",
                        detail=f"Job {job.qualified_name} NEEDS resource "
                               f"{res_name} (not declared in this file)",
                    ))

            for prompt_name in job.prompts:
                out.waits_for_prompt_edges.append(WaitsForPromptEdge(
                    job_id=job.id,
                    prompt_id=prompt_id(prompt_name),
                    prompt_name=prompt_name,
                ))
                if prompt_name in prompt_names:
                    continue
                # v0.3 — inline literal prompts (``PROMPT "Continue? (YES/NO)"``)
                # are anonymously declared at the call site. Synthesize a
                # Prompt so the WAITS_FOR_PROMPT edge resolves and skip the
                # "not declared in this file" warning.
                if _is_inline_prompt(prompt_name):
                    from tws_parser.models.domain import PromptIR
                    unit.prompts.append(PromptIR(
                        name=prompt_name, text=prompt_name,
                    ))
                    prompt_names.add(prompt_name)
                    continue
                out.warnings.append(Warning(
                    type="unresolved_prompt",
                    detail=f"Job {job.qualified_name} PROMPT {prompt_name} "
                           "(not declared in this file)",
                ))

            for path in job.opens:
                out.opens_edges.append(OpensEdge(
                    job_id=job.id,
                    file_watcher_id=file_watcher_id(path),
                    path=path,
                ))

            # FOLLOWS — every FollowsRef on this job. Resolution depends on
            # scope: internal stays in this stream; external is keyed by
            # the full qualified tuple.
            for ref in job.follows_refs:
                target_job = _resolve_follows(
                    ref, stream, in_stream, jobs_by_qualified,
                )
                if target_job is None:
                    out.warnings.append(Warning(
                        type="unresolved_dependency",
                        detail=_unresolved_detail(job, ref),
                    ))
                    continue
                out.follows_edges.append(FollowsEdge(
                    from_job_id=job.id,
                    to_job_id=target_job.id,
                    condition=ref.condition,
                    scope=ref.scope,
                    from_qualified=job.qualified_name,
                    to_qualified=target_job.qualified_name,
                ))
                # Mirror onto the v0.1 job_dependencies list so the Postgres
                # writer keeps emitting the same rows it did pre-v0.2.
                out.job_dependencies.append(JobDependency(
                    job=job.name,
                    depends_on=target_job.name,
                    schedule=stream.name,
                ))

            # ON <job> RC=N / VAL <expr> — conditional branches.
            # Modelled as RecoveryEdge with the condition as recovery_action
            # (the target job runs WHEN the condition holds on the source).
            for target_name, cond in job.on_conditions:
                target = in_stream.get(target_name)
                if target is None:
                    out.warnings.append(Warning(
                        type="unresolved_recovery",
                        detail=f"Job {job.qualified_name} ON {target_name} "
                               f"{cond} (target not in stream "
                               f"{job.workstation}#{job.stream})",
                    ))
                    continue
                out.recovery_edges.append(RecoveryEdge(
                    from_job_id=job.id,
                    to_recovery_job_id=target.id,
                    recovery_action=cond,
                    from_qualified=job.qualified_name,
                    to_qualified=target.qualified_name,
                ))

            # RECOVERY AFTER — resolve the recovery job and emit RecoveryEdge.
            if job.recovery_after:
                recov_ref = _follows_ref_from_text(job.recovery_after)
                target = _resolve_follows(
                    recov_ref, stream, in_stream, jobs_by_qualified,
                )
                if target is None:
                    out.warnings.append(Warning(
                        type="unresolved_recovery",
                        detail=f"Job {job.qualified_name} RECOVERY AFTER "
                               f"{job.recovery_after} (target not in batch)",
                    ))
                else:
                    out.recovery_edges.append(RecoveryEdge(
                        from_job_id=job.id,
                        to_recovery_job_id=target.id,
                        recovery_action=job.recovery or "",
                        from_qualified=job.qualified_name,
                        to_qualified=target.qualified_name,
                    ))

        # SCHEDULED_BY — one edge per RunCycleRef with a calendar reference.
        # Refs come either as the structured ``CALENDAR parserId`` modifier
        # (``rc.calendar_name``) OR as a ``CALENDAR=NAME`` token inside the
        # RRULE string (``rc.rrule``).
        emitted_cal: set[str] = set()
        for rc in stream.run_cycles:
            cal_names: list[str] = []
            if rc.calendar_name:
                cal_names.append(rc.calendar_name)
            if rc.rrule:
                cal_names.extend(_extract_calendars_from_rrule_str(rc.rrule))
            for cal_name in cal_names:
                key = (stream.id, cal_name)
                if key in emitted_cal:
                    continue
                emitted_cal.add(key)
                out.scheduled_by_edges.append(ScheduledByEdge(
                    job_stream_id=stream.id,
                    calendar_id=calendar_id(cal_name),
                    calendar_name=cal_name,
                ))
                if cal_name not in calendar_names:
                    out.warnings.append(Warning(
                        type="unresolved_calendar",
                        detail=f"Stream {stream.qualified_name} ON RUNCYCLE "
                               f"... CALENDAR {cal_name} (calendar not "
                               "declared in this file)",
                    ))

    # ---- Schedule-level FOLLOWS (v0.1 shape) ---------------------------
    for s in unit.schedules:
        for sf in s.schedule_follows:
            target = sf.target_schedule
            if target in schedule_names:
                out.schedule_dependencies.append(ScheduleDependency(
                    schedule=s.name,
                    depends_on_schedule=target,
                    target_workstation=sf.target_workstation,
                    wildcard=sf.wildcard,
                ))
            else:
                out.warnings.append(Warning(
                    type="unresolved_dependency",
                    detail=f"Schedule {s.name} FOLLOWS "
                           f"{sf.target_workstation}#{target} (target not in batch)",
                ))

    # ---- TRIGGERS edges (event rule → job stream) ----------------------
    for er in unit.event_rules:
        if not er.target_stream_qualified:
            continue
        parts = er.target_stream_qualified.split("#")
        if len(parts) < 2:
            # Bare name — try to resolve against any stream with that name
            # but require uniqueness; otherwise surface a warning.
            candidates = [s for s in unit.job_streams if s.name == parts[0]]
            if len(candidates) == 1:
                target_stream = candidates[0]
            else:
                target_stream = None
        else:
            ws, name = parts[0], parts[-1]
            target_stream = streams_by_qualified.get((ws, name))

        if target_stream is None:
            out.warnings.append(Warning(
                type="unresolved_event_target",
                detail=f"EventRule {er.name} TRIGGERS {er.target_stream_qualified} "
                       "(target stream not in batch)",
            ))
            continue
        out.triggers_edges.append(TriggersEdge(
            event_rule_id=er.id,
            job_stream_id=target_stream.id,
            target_stream_qualified=er.target_stream_qualified,
        ))

    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_follows(
    ref: FollowsRef,
    parent_stream: JobStreamIR,
    in_stream: dict[str, JobIR],
    jobs_by_qualified: dict[tuple[str, str, str], JobIR],
) -> JobIR | None:
    if ref.scope == "external":
        # External tuple must match exactly. Never fall back.
        key = (ref.target_workstation, ref.target_stream, ref.target_job)
        return jobs_by_qualified.get(key)
    # Internal — same stream only. Never fall back to other streams.
    return in_stream.get(ref.target_job)


def _follows_ref_from_text(text: str) -> FollowsRef:
    """Build a synthetic FollowsRef from a qualified-name string. Used for
    RECOVERY AFTER targets where the visitor captured the raw text.
    """
    parts = text.split("#")
    if len(parts) >= 2 and "." in parts[-1]:
        ws = parts[0]
        rest = "#".join(parts[1:])
        stream_part, _, job_part = rest.partition(".")
        return FollowsRef(
            target_qualified=text,
            scope="external",
            target_workstation=ws,
            target_stream=stream_part,
            target_job=job_part,
        )
    bare = text.split(".")[0].split("#")[-1]
    return FollowsRef(target_qualified=text, scope="internal", target_job=bare)


def _unresolved_detail(job: JobIR, ref: FollowsRef) -> str:
    if ref.scope == "external":
        return (
            f"Job {job.qualified_name} FOLLOWS {ref.target_qualified} "
            "(external target not in batch — may live in another file)"
        )
    return (
        f"Job {job.qualified_name} FOLLOWS {ref.target_qualified} "
        f"(target not in stream {job.workstation}#{job.stream})"
    )


_CALENDAR_REF_RE = __import__("re").compile(
    r"\bCALENDAR\s*=\s*([A-Za-z_][A-Za-z0-9_]*)", __import__("re").IGNORECASE
)


def _extract_calendars_from_rrule_str(rrule: str) -> list[str]:
    if not rrule:
        return []
    return _CALENDAR_REF_RE.findall(rrule)


def _is_inline_prompt(name: str) -> bool:
    """Heuristic: a TWS prompt identifier is a single word (letters, digits,
    underscores). Anything containing whitespace, punctuation, or characters
    that can't appear in an identifier is an inline literal — typically a
    full English sentence quoted as the PROMPT argument.
    """
    if not name:
        return False
    if any(c.isspace() for c in name):
        return True
    for c in name:
        if c.isalnum() or c == "_":
            continue
        return True
    return False
