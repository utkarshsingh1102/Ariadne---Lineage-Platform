"""IR dataclasses produced by either parser path (ANTLR or XML).

v0.1 — schedules + jobs only.
v0.2 — adds Workstation/JobStream/Calendar/Prompt/EventRule + structured
       FollowsRef for IF SUCC/ABEND/RC conditions + RunCycleRef for the
       new ON RUNCYCLE shapes (calendar reference + RRULE string).

ScheduleIR / JobIR derive their `id` automatically. Tests can construct
``ScheduleIR(workstation, scheduler, name)`` and read `id` back; same for
``JobIR(schedule_id, name)`` (v0.1 shape, falls back to legacy id) or
``JobIR(workstation, stream, name)`` (v0.2 shape, qualified-string hash).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tws_parser.utils.ids import (
    calendar_id,
    event_rule_id,
    file_watcher_id,
    job_id,
    job_id_legacy,
    job_stream_id,
    prompt_id,
    resource_id,
    schedule_id,
    script_id,
    workstation_id,
)


# ============================================================================
# v0.2 — new top-level topology IRs
# ============================================================================


@dataclass
class WorkstationIR:
    """A TWS workstation (a.k.a. CPU / agent / FTA / domain manager).

    Identity: hash("workstation", name). Names are unique in the TWS
    network so we don't need a qualifier.
    """
    name: str
    description: str | None = None
    os: str | None = None              # UNIX / WINDOWS / other
    node: str | None = None            # hostname / FQDN
    tcp_addr: int | None = None
    type: str | None = None            # FTA / MANAGER
    domain: str | None = None
    autolink: bool | None = None
    behind_firewall: bool | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = workstation_id(self.name)


@dataclass
class RunCycleRef:
    """One ``ON RUNCYCLE`` clause on a job stream.

    Composer allows multiple RUNCYCLE clauses + a separate EXCEPT RUNCYCLE.
    Each is captured here with its raw phrase, optional calendar reference,
    and optional RRULE-style string body.
    """
    name: str
    raw_phrase: str = ""
    calendar_name: str | None = None     # for ``ON RUNCYCLE X CALENDAR Y``
    rrule: str | None = None             # for ``"FREQ=DAILY;INTERVAL=1"``
    is_except: bool = False              # for ``EXCEPT RUNCYCLE``


@dataclass
class JobStreamIR:
    """A TWS job stream — the v0.2 graph-facing wrapper around a SCHEDULE block.

    Coexists with ScheduleIR (which retains v0.1 parse-level details for
    Postgres + backward-compat fixtures). The writer prefers JobStreamIR
    for Neo4j topology; ScheduleIR stays for the RDBMS path.

    Identity: hash("stream", workstation, name).
    """
    workstation: str
    name: str
    description: str | None = None
    run_cycles: list[RunCycleRef] = field(default_factory=list)
    start_time: str | None = None
    end_time: str | None = None
    deadline: str | None = None
    priority: int | None = None
    limit: int | None = None
    carry_forward: bool = False
    valid_from: str | None = None
    valid_to: str | None = None
    # v0.3 — schedule-level EVERY N (rerun cadence in minutes) and the
    # ONUNTIL action carried on DEADLINE/UNTIL (one of "SUPPR" | "CANC" |
    # "CONT"). Both surface on the JobStream node so the lineage UI can
    # render them in the Schedule & timing section.
    every: int | None = None
    on_until: str | None = None
    # v0.3 — schedule-level NEEDS (stream-wide resource gates). list of
    # (resource_name, quantity) tuples; the orchestrator promotes these to
    # ResourceIRs and stream→resource edges in the writer.
    stream_needs: list[tuple[str, int]] = field(default_factory=list)
    jobs: list["JobIR"] = field(default_factory=list)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = job_stream_id(self.workstation, self.name)

    @property
    def qualified_name(self) -> str:
        return f"{self.workstation}#{self.name}"


@dataclass
class CalendarIR:
    name: str
    description: str | None = None
    dates: list[str] = field(default_factory=list)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = calendar_id(self.name)


@dataclass
class PromptIR:
    name: str
    text: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = prompt_id(self.name)


@dataclass
class EventRuleIR:
    name: str
    description: str | None = None
    active: bool = True
    rule_type: str | None = None         # ``filter``, etc.
    event_type: str | None = None        # ``FileCreated``, etc.
    event_node: str | None = None
    event_filename: str | None = None
    action_type: str | None = None       # ``SBS``, etc.
    target_stream_qualified: str | None = None   # ``MASTER_DM#DR_FAILOVER``
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = event_rule_id(self.name)


@dataclass
class FollowsRef:
    """One predecessor edge on a job — captures the condition structurally
    so Phase 5's writer can put it on the edge.

    Two predecessors on the same parent with different conditions (RC=0 vs
    RC=4) produce TWO distinct edges; condition is part of the MERGE key.
    """
    target_qualified: str    # ``WS#STREAM.JOB`` or bare ``JOB``
    scope: str               # ``internal`` | ``external``
    condition: str | None = None    # ``SUCC`` | ``ABEND`` | ``RC=0`` | None
    target_workstation: str = ""
    target_stream: str = ""
    target_job: str = ""


# ============================================================================
# v0.1 — kept for backward compatibility (parse-level details + RDBMS path)
# ============================================================================


@dataclass
class ScheduleFollows:
    """A schedule-level FOLLOWS edge. The `.@` wildcard means depend on the
    *whole* schedule rather than each individual job."""
    target_schedule: str
    target_workstation: str = ""
    wildcard: bool = False


@dataclass
class JobIR:
    schedule_id: str
    name: str

    id: str = ""
    script_path: str | None = None
    script_args: str | None = None
    script_type: str | None = None
    stream_logon: str | None = None
    recovery: str | None = None
    description: str | None = None
    priority: int | None = None
    order_in_schedule: int = 0
    follows: list[str] = field(default_factory=list)
    needs: list[tuple[str, int]] = field(default_factory=list)
    opens: list[str] = field(default_factory=list)

    # v0.2 — qualified identity. When workstation + stream are set, ``id``
    # is derived from job_id(workstation, stream, name). When only
    # schedule_id is set (legacy direct-construction tests), it falls back
    # to job_id_legacy(schedule_id, name).
    workstation: str = ""
    stream: str = ""
    follows_refs: list[FollowsRef] = field(default_factory=list)
    recovery_after: str | None = None        # qualified job name for RECOVERY AFTER
    every: int | None = None                 # rerun cadence minutes
    prompts: list[str] = field(default_factory=list)
    # v0.3 — ``ON <jobname> RC=N`` and ``ON <jobname> VAL <expr>`` conditional
    # branches. Each entry is (target_job_name, condition_text). The resolver
    # turns these into RecoveryEdge with recovery_action=condition_text.
    on_conditions: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            if self.workstation and self.stream:
                self.id = job_id(self.workstation, self.stream, self.name)
            else:
                self.id = job_id_legacy(self.schedule_id, self.name)

    @property
    def qualified_name(self) -> str:
        if self.workstation and self.stream:
            return f"{self.workstation}#{self.stream}.{self.name}"
        return self.name


@dataclass
class RunCycle:
    """Normalised run-cycle. `cron_equivalent` is `None` when the parser can't
    derive a clean cron expression (e.g. custom calendars with exclusions)."""
    raw: str = ""
    frequency: str = ""              # daily / weekly / monthly / unknown
    days_of_week: list[str] = field(default_factory=list)
    days_of_month: list[int] = field(default_factory=list)
    cron_equivalent: str | None = None


@dataclass
class ScheduleIR:
    workstation: str
    scheduler: str = ""
    name: str = ""

    id: str = ""
    run_cycle: str | None = None         # raw name e.g. "EVERY_WEEKDAY"
    cron_equivalent: str | None = None   # derived (None if can't infer)
    valid_from: str | None = None        # kept as the raw composer literal
    valid_to: str | None = None
    start_time: str | None = None        # "HH:MM"
    end_time: str | None = None
    priority: int | None = None
    carry_forward: bool = False
    source_file: str = ""
    raw_definition: str = ""
    # v0.3 mirrors of JobStream fields so users viewing the :Schedule node
    # in lineage see complete timing without switching to :JobStream.
    deadline: str | None = None
    on_until: str | None = None
    every: int | None = None
    limit: int | None = None
    run_cycles: list[RunCycleRef] = field(default_factory=list)
    days_of_week: list[str] = field(default_factory=list)
    days_of_month: list[int] = field(default_factory=list)
    frequency: str | None = None

    schedule_follows: list[ScheduleFollows] = field(default_factory=list)
    jobs: list[JobIR] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = schedule_id(self.workstation, self.scheduler, self.name)


# ----- Aux IRs the writers also need -----------------------------------------

@dataclass
class ScriptIR:
    path: str
    script_type: str
    args: str | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = script_id(self.path)


@dataclass
class ResourceIR:
    name: str
    quantity: int | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = resource_id(self.name)


@dataclass
class FileWatcherIR:
    path: str
    pattern: str | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = file_watcher_id(self.path)


# ============================================================================
# v0.2 — full-parse output container
# ============================================================================

@dataclass
class ParsedComposerUnit:
    """Everything the visitor extracted from one composer file.

    ScheduleIR list is the v0.1-compat surface (Postgres writer still
    consumes this, plus ~20 existing tests). The other lists are
    Phase-3-new topology IRs that Phase 5's Neo4j writer will emit.
    """
    schedules: list[ScheduleIR] = field(default_factory=list)
    job_streams: list[JobStreamIR] = field(default_factory=list)
    workstations: list[WorkstationIR] = field(default_factory=list)
    calendars: list[CalendarIR] = field(default_factory=list)
    resources: list[ResourceIR] = field(default_factory=list)
    prompts: list[PromptIR] = field(default_factory=list)
    event_rules: list[EventRuleIR] = field(default_factory=list)
