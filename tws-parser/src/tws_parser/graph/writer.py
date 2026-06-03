"""Neo4j writer for the TWS lineage subgraph.

v0.1 entry point — ``GraphWriter(driver).write_schedules(schedules)``. Emits
the legacy 5 node labels (Schedule/Job/Script/Resource/FileWatcher) + 5
edges. Kept so the Postgres-only path + back-compat tests still work.

v0.2 entry point — ``GraphWriter(driver).write_topology(unit, deps)``. Emits
ALL 10 node labels (adds :Workstation, :JobStream, :Calendar, :Prompt,
:EventRule) and ALL edges including the renamed :EXECUTES and
:REQUIRES_RESOURCE plus the new :RUNS_ON / :WAITS_FOR_PROMPT /
:RECOVERS_WITH / :TRIGGERS / :SCHEDULED_BY / :HOSTS_STREAM. The API route
calls this; the Postgres writer still uses the v0.1 ScheduleIR shape.
"""

from __future__ import annotations

import json
from typing import Iterable

from neo4j import Driver, Session

from tws_parser.config import settings
from tws_parser.graph import queries
from tws_parser.models.domain import ParsedComposerUnit, ScheduleIR
from tws_parser.parser.dependencies import ResolvedDependencies, resolve, resolve_full
from tws_parser.utils.ids import file_watcher_id, resource_id, script_id


class GraphWriter:
    def __init__(self, driver: Driver, database: str | None = None):
        self.driver = driver
        self.database = database or settings.neo4j_database

    def ensure_constraints(self) -> None:
        with self._session() as s:
            for stmt in _CONSTRAINTS:
                s.run(stmt).consume()

    # ------------------------------------------------------------------
    # v0.1 — list[ScheduleIR] entry point
    # ------------------------------------------------------------------

    def write_schedules(
        self, schedules: list[ScheduleIR], overwrite: bool = False
    ) -> dict[str, int]:
        """v0.1 shim — wraps schedules into a minimal ParsedComposerUnit and
        emits via the v0.2 path. Topology fields (workstations / streams /
        calendars / prompts / event_rules) are empty, so no new node labels
        are written — only the existing Schedule/Job/Script/Resource/FileWatcher.
        """
        unit = ParsedComposerUnit(schedules=schedules)
        deps = resolve(schedules)  # v0.1 resolution
        return self.write_topology(unit, deps, overwrite=overwrite)

    # ------------------------------------------------------------------
    # v0.2 — full topology entry point
    # ------------------------------------------------------------------

    def write_topology(
        self,
        unit: ParsedComposerUnit,
        deps: ResolvedDependencies | None = None,
        overwrite: bool = False,
        source_files: dict[str, list[str]] | None = None,
    ) -> dict[str, int]:
        """Write the full TWS topology.

        ``source_files`` (optional) maps node id → list of file paths that
        declared this node. When supplied, every node's ``source_files``
        property is updated with this provenance (deduped). When None, the
        property is set to ``[]`` for new nodes — back-compat with single-
        file callers that don't track file provenance.
        """
        if deps is None:
            deps = resolve_full(unit)
        if source_files is None:
            source_files = {}
        with self._session() as s:
            if overwrite:
                self._delete_overwrites(s, unit)
            self._write_nodes(s, unit, source_files)
            self._write_relationships(s, unit, deps)
        return {"nodes_written": _count_nodes(unit)}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _session(self) -> Session:
        return self.driver.session(database=self.database)

    def _delete_overwrites(self, s: Session, unit: ParsedComposerUnit) -> None:
        # Drop the per-stream subgraphs (v0.2 path) AND the per-schedule
        # subgraphs (v0.1 path) so both topology shapes are cleaned up.
        for stream in unit.job_streams:
            s.execute_write(
                lambda tx, sid=stream.id: tx.run(
                    queries.DELETE_JOB_STREAM_SUBGRAPH, stream_id=sid
                )
            )
        for sched in unit.schedules:
            s.execute_write(
                lambda tx, sid=sched.id: tx.run(
                    queries.DELETE_SCHEDULE_SUBGRAPH, schedule_id=sid
                )
            )

    def _write_nodes(
        self,
        s: Session,
        unit: ParsedComposerUnit,
        source_files: dict[str, list[str]],
    ) -> None:
        def files_for(node_id: str) -> list[str]:
            return source_files.get(node_id, [])

        # Workstations
        _batched(s, queries.MERGE_WORKSTATION, [{
            "id": w.id, "name": w.name, "description": w.description,
            "os": w.os, "node": w.node, "tcp_addr": w.tcp_addr, "type": w.type,
            "domain": w.domain, "autolink": w.autolink,
            "behind_firewall": w.behind_firewall,
            "source_files": files_for(w.id),
        } for w in unit.workstations])

        # Calendars
        _batched(s, queries.MERGE_CALENDAR, [{
            "id": c.id, "name": c.name, "description": c.description,
            "dates": c.dates,
            "source_files": files_for(c.id),
        } for c in unit.calendars])

        # Prompts
        _batched(s, queries.MERGE_PROMPT, [{
            "id": p.id, "name": p.name, "text": p.text,
            "source_files": files_for(p.id),
        } for p in unit.prompts])

        # Event rules
        _batched(s, queries.MERGE_EVENT_RULE, [{
            "id": er.id, "name": er.name, "description": er.description,
            "active": er.active, "rule_type": er.rule_type,
            "event_type": er.event_type, "event_node": er.event_node,
            "event_filename": er.event_filename,
            "action_type": er.action_type,
            "target_stream_qualified": er.target_stream_qualified,
            "source_files": files_for(er.id),
        } for er in unit.event_rules])

        # Job streams (v0.2 topology layer). ``run_cycles`` is JSON-encoded
        # because Neo4j primitives don't support lists of structs; the frontend
        # ``ScheduleSection`` decodes it back into a list. Empty list → null
        # so the UI doesn't show a stray ``[]``.
        _batched(s, queries.MERGE_JOB_STREAM, [{
            "id": js.id,
            "name": js.name,
            "qualified_name": js.qualified_name,
            "workstation": js.workstation,
            "description": js.description,
            "start_time": js.start_time,
            "end_time": js.end_time,
            "deadline": js.deadline,
            "priority": js.priority,
            "limit": js.limit,
            "carry_forward": js.carry_forward,
            "valid_from": js.valid_from,
            "valid_to": js.valid_to,
            "run_cycles": _encode_run_cycles(js.run_cycles),
            "every": js.every,
            "on_until": js.on_until,
            "source_files": files_for(js.id),
        } for js in unit.job_streams])

        # Schedules (v0.1 layer, kept for the Postgres path + back-compat presets)
        _batched(s, queries.MERGE_SCHEDULE, [{
            "id": sc.id, "name": sc.name, "workstation": sc.workstation,
            "scheduler": sc.scheduler, "run_cycle": sc.run_cycle,
            "cron_equivalent": sc.cron_equivalent,
            "valid_from": sc.valid_from, "valid_to": sc.valid_to,
            "start_time": sc.start_time, "end_time": sc.end_time,
            "deadline": sc.deadline, "on_until": sc.on_until,
            "every": sc.every, "limit": sc.limit,
            "run_cycles": _encode_run_cycles(sc.run_cycles),
            "days_of_week": list(sc.days_of_week) if sc.days_of_week else None,
            "priority": sc.priority, "carry_forward": sc.carry_forward,
            "source_files": files_for(sc.id),
        } for sc in unit.schedules])

        # Jobs — across all schedules / streams.
        jobs = [j for sc in unit.schedules for j in sc.jobs]
        _batched(s, queries.MERGE_JOB, [{
            "id": j.id, "name": j.name,
            "qualified_name": j.qualified_name,
            "schedule_id": j.schedule_id,
            "workstation": j.workstation, "stream": j.stream,
            "stream_logon": j.stream_logon, "recovery": j.recovery,
            "description": j.description, "priority": j.priority,
            "order_in_schedule": j.order_in_schedule,
            "source_files": files_for(j.id),
        } for j in jobs])

        # Scripts, Resources, FileWatchers — keyed on cross-parser ids. We
        # don't have explicit IRs in unit.* for scripts + file_watchers, so
        # source_files comes from the parent job's provenance.
        scripts: dict[str, dict] = {}
        for j in jobs:
            if not j.script_path:
                continue
            sid = script_id(j.script_path)
            existing = scripts.get(sid)
            if existing is None:
                scripts[sid] = {
                    "id": sid, "path": j.script_path,
                    "script_type": j.script_type or "unknown",
                    "source_files": list(files_for(j.id)),
                }
            else:
                _extend_unique(existing["source_files"], files_for(j.id))
        _batched(s, queries.MERGE_SCRIPT, list(scripts.values()),
                 parser=queries.PARSER_NAME)

        # Resources may come from RESOURCE declarations (unit.resources) AND
        # from job NEEDS clauses — merge both into a single map by id.
        resources: dict[str, dict] = {}
        for r in unit.resources:
            resources.setdefault(r.id, {
                "id": r.id, "name": r.name, "quantity": r.quantity,
                "source_files": list(files_for(r.id)),
            })
        for j in jobs:
            for name, qty in j.needs:
                rid = resource_id(name)
                existing = resources.get(rid)
                if existing is None:
                    resources[rid] = {
                        "id": rid, "name": name, "quantity": qty,
                        "source_files": list(files_for(j.id)),
                    }
                else:
                    _extend_unique(existing["source_files"], files_for(j.id))
        _batched(s, queries.MERGE_RESOURCE, list(resources.values()))

        file_watchers: dict[str, dict] = {}
        for j in jobs:
            for path in j.opens:
                fid = file_watcher_id(path)
                existing = file_watchers.get(fid)
                if existing is None:
                    file_watchers[fid] = {
                        "id": fid, "path": path, "pattern": None,
                        "source_files": list(files_for(j.id)),
                    }
                else:
                    _extend_unique(existing["source_files"], files_for(j.id))
        _batched(s, queries.MERGE_FILE_WATCHER, list(file_watchers.values()))

    def _write_relationships(
        self, s: Session, unit: ParsedComposerUnit, deps: ResolvedDependencies
    ) -> None:
        # Workstation → JobStream
        hosts = [{
            "workstation_id": _workstation_id_for(stream.workstation, unit),
            "stream_id": stream.id,
        } for stream in unit.job_streams]
        # Filter out streams whose workstation isn't a declared WorkstationIR;
        # we still emit the stream node + jobs, just skip the HOSTS_STREAM
        # edge if the workstation is implicit (not present in this file).
        hosts = [h for h in hosts if h["workstation_id"]]
        _batched(s, queries.HOSTS_STREAM, hosts)

        # Schedule → Job (legacy) AND JobStream → Job (v0.2)
        contains_sched, contains_stream = [], []
        for sc in unit.schedules:
            for j in sc.jobs:
                contains_sched.append({
                    "schedule_id": sc.id, "job_id": j.id,
                    "order": j.order_in_schedule,
                })
        for stream in unit.job_streams:
            for j in stream.jobs:
                contains_stream.append({
                    "stream_id": stream.id, "job_id": j.id,
                    "order": j.order_in_schedule,
                })
        _batched(s, queries.CONTAINS_JOB, contains_sched)
        _batched(s, queries.CONTAINS_JOB_VIA_STREAM, contains_stream)

        # EXECUTES (job → script)
        jobs = [j for sc in unit.schedules for j in sc.jobs]
        executes = [{
            "job_id": j.id, "path": j.script_path, "args": j.script_args or "",
        } for j in jobs if j.script_path]
        _batched(s, queries.EXECUTES, executes)

        # WAITS_FOR_FILE (job → file_watcher)
        waits = []
        for j in jobs:
            for path in j.opens:
                waits.append({
                    "job_id": j.id, "file_watcher_id": file_watcher_id(path),
                })
        _batched(s, queries.WAITS_FOR_FILE, waits)

        # v0.2 edges from the resolved-dependencies output -----------------

        # DEPENDS_ON (job → job) with condition + scope on the edge.
        depends_rows = [{
            "from_id": e.from_job_id,
            "to_id":   e.to_job_id,
            "condition": e.condition or "",
            "scope": e.scope,
        } for e in deps.follows_edges if e.from_job_id != e.to_job_id]
        _batched(s, queries.DEPENDS_ON_JOB, depends_rows)

        # DEPENDS_ON (schedule → schedule)
        sched_id_by_name = {sc.name: sc.id for sc in unit.schedules}
        sched_dep_rows = []
        for sd in deps.schedule_dependencies:
            from_id = sched_id_by_name.get(sd.schedule)
            to_id = sched_id_by_name.get(sd.depends_on_schedule)
            if from_id and to_id and from_id != to_id:
                sched_dep_rows.append({"from_id": from_id, "to_id": to_id})
        _batched(s, queries.DEPENDS_ON_SCHEDULE, sched_dep_rows)

        # REQUIRES_RESOURCE (job → resource) with quantity
        _batched(s, queries.REQUIRES_RESOURCE, [{
            "job_id": e.job_id, "resource_id": e.resource_id, "quantity": e.quantity,
        } for e in deps.requires_resource_edges])

        # RUNS_ON (job → workstation)
        _batched(s, queries.RUNS_ON, [{
            "job_id": e.job_id, "workstation_id": e.workstation_id,
        } for e in deps.runs_on_edges])

        # WAITS_FOR_PROMPT (job → prompt)
        _batched(s, queries.WAITS_FOR_PROMPT, [{
            "job_id": e.job_id, "prompt_id": e.prompt_id,
        } for e in deps.waits_for_prompt_edges])

        # RECOVERS_WITH (job → recovery job)
        _batched(s, queries.RECOVERS_WITH, [{
            "from_id": e.from_job_id, "to_id": e.to_recovery_job_id,
            "recovery_action": e.recovery_action,
        } for e in deps.recovery_edges])

        # TRIGGERS (event rule → job stream)
        _batched(s, queries.TRIGGERS, [{
            "event_rule_id": e.event_rule_id, "job_stream_id": e.job_stream_id,
        } for e in deps.triggers_edges])

        # SCHEDULED_BY (job stream → calendar)
        _batched(s, queries.SCHEDULED_BY, [{
            "stream_id": e.job_stream_id, "calendar_id": e.calendar_id,
        } for e in deps.scheduled_by_edges])


def _encode_run_cycles(run_cycles: list) -> str | None:
    if not run_cycles:
        return None
    payload = [
        {
            "name": rc.name,
            "raw_phrase": rc.raw_phrase,
            "calendar_name": rc.calendar_name,
            "rrule": rc.rrule,
            "is_except": rc.is_except,
        }
        for rc in run_cycles
    ]
    return json.dumps(payload, separators=(",", ":"))


def _workstation_id_for(name: str, unit: ParsedComposerUnit) -> str | None:
    """Return the WorkstationIR id matching this name, or None if the
    workstation isn't declared in this file (cross-file references are
    common in production estates)."""
    if not name:
        return None
    for w in unit.workstations:
        if w.name == name:
            return w.id
    return None


# ----- helpers ----------------------------------------------------------------

def _extend_unique(target: list[str], incoming: list[str]) -> None:
    """In-place append items from incoming that aren't already in target.
    Preserves order; used to roll up provenance for derived nodes (scripts,
    resources, file watchers) from multiple parent jobs."""
    for item in incoming:
        if item not in target:
            target.append(item)


def _batched(s: Session, cypher: str, rows: list[dict], **kwargs) -> None:
    if not rows:
        return
    n = settings.batch_size
    for chunk in _chunks(rows, n):
        s.execute_write(lambda tx, c=chunk: tx.run(cypher, rows=c, **kwargs).consume())


def _chunks(rows: list[dict], n: int) -> Iterable[list[dict]]:
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


def _count_nodes(unit: ParsedComposerUnit) -> int:
    jobs = sum(len(s.jobs) for s in unit.schedules)
    scripts = len({j.script_path for s in unit.schedules for j in s.jobs if j.script_path})
    res = len({n for s in unit.schedules for j in s.jobs for n, _ in j.needs}) + len(unit.resources)
    fws = len({p for s in unit.schedules for j in s.jobs for p in j.opens})
    return (
        len(unit.schedules) + len(unit.job_streams) + len(unit.workstations)
        + len(unit.calendars) + len(unit.prompts) + len(unit.event_rules)
        + jobs + scripts + res + fws
    )


_CONSTRAINTS = [
    # v0.1
    "CREATE CONSTRAINT schedule_id IF NOT EXISTS FOR (s:Schedule) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT job_id IF NOT EXISTS FOR (j:Job) REQUIRE j.id IS UNIQUE",
    "CREATE CONSTRAINT script_path IF NOT EXISTS FOR (s:Script) REQUIRE s.path IS UNIQUE",
    "CREATE CONSTRAINT resource_id IF NOT EXISTS FOR (r:Resource) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT file_watcher_id IF NOT EXISTS FOR (f:FileWatcher) REQUIRE f.id IS UNIQUE",
    # v0.2 — new topology nodes
    "CREATE CONSTRAINT workstation_id IF NOT EXISTS FOR (w:Workstation) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT job_stream_id IF NOT EXISTS FOR (js:JobStream) REQUIRE js.id IS UNIQUE",
    "CREATE CONSTRAINT calendar_id IF NOT EXISTS FOR (c:Calendar) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT prompt_id IF NOT EXISTS FOR (p:Prompt) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT event_rule_id IF NOT EXISTS FOR (er:EventRule) REQUIRE er.id IS UNIQUE",
]
