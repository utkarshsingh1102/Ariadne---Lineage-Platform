"""Walk the ANTLR parse tree and produce IR records.

v0.1 surface: ``visitCompilationUnit`` returns ``list[ScheduleIR]`` so the
~20 existing call-sites (tests + Postgres path) keep working.

v0.2 surface: ``visit_full`` returns ``ParsedComposerUnit`` — schedules +
job streams + workstations + calendars + resources + prompts + event rules.
The Neo4j writer in Phase 5 consumes this.
"""

from __future__ import annotations

from tws_parser.generated.TWSComposerParser import TWSComposerParser
from tws_parser.generated.TWSComposerParserVisitor import TWSComposerParserVisitor
from tws_parser.models.domain import (
    CalendarIR,
    EventRuleIR,
    FollowsRef,
    JobIR,
    JobStreamIR,
    ParsedComposerUnit,
    PromptIR,
    ResourceIR,
    RunCycleRef,
    ScheduleFollows,
    ScheduleIR,
    WorkstationIR,
)
from tws_parser.parser import run_cycle, script_resolver


def _on_until_text(ctx) -> str | None:
    """Return the ONUNTIL action (SUPPR / CANC / CONT) on a deadlineClause
    or untilClause as an uppercase string, or None when no ONUNTIL tail is
    present. The grammar now wraps the action in an ``onUntilAction`` rule.
    """
    action = ctx.onUntilAction() if hasattr(ctx, "onUntilAction") else None
    if action is None:
        return None
    txt = action.getText().upper()
    return "CONT" if txt == "CONTINUE" else txt


def _safe_int(node) -> int | None:
    """Coerce an ANTLR INT terminal to int, tolerating recovery placeholders.

    When the parser recovers from a missing INT, ANTLR inserts a synthetic
    terminal whose text is ``<missing INT>`` — int()-ing that raises
    ValueError and crashes the visitor, masking the collected diagnostic
    that should drive a loud ``status=failed`` response. Returns None
    when the token isn't a real integer; the caller leaves the IR field
    unset and the parse-error list still carries the underlying complaint.
    """
    if node is None:
        return None
    try:
        return int(node.getText())
    except (ValueError, AttributeError):
        return None


class TWSIRVisitor(TWSComposerParserVisitor):
    """Returns ``list[ScheduleIR]`` from ``visitCompilationUnit`` (v0.1 shape).

    Use ``visit_full`` to also get workstations, calendars, prompts, event
    rules, job streams, and the structured FollowsRef edges (v0.2).
    """

    # ------------------------------------------------------------------
    # v0.1 surface — back-compat list-of-schedule
    # ------------------------------------------------------------------

    def visitCompilationUnit(self, ctx) -> list[ScheduleIR]:  # noqa: N802
        unit = self._build_unit(ctx)
        return unit.schedules

    # ------------------------------------------------------------------
    # v0.2 surface — full topology
    # ------------------------------------------------------------------

    def visit_full(self, ctx) -> ParsedComposerUnit:
        return self._build_unit(ctx)

    def _build_unit(self, ctx) -> ParsedComposerUnit:
        unit = ParsedComposerUnit()
        for wsd in ctx.workstationDefinition():
            ws = self._build_workstation(wsd)
            if ws is not None:
                unit.workstations.append(ws)
        for cd in ctx.calendarDefinition():
            c = self._build_calendar(cd)
            if c is not None:
                unit.calendars.append(c)
        for rd in ctx.resourceDefinition():
            r = self._build_resource(rd)
            if r is not None:
                unit.resources.append(r)
        for pd in ctx.promptDefinition():
            p = self._build_prompt(pd)
            if p is not None:
                unit.prompts.append(p)
        for erd in ctx.eventRuleDefinition():
            er = self._build_event_rule(erd)
            if er is not None:
                unit.event_rules.append(er)
        for sd in ctx.scheduleDefinition():
            schedule, stream = self._build_schedule_and_stream(sd)
            if schedule is not None:
                unit.schedules.append(schedule)
            if stream is not None:
                unit.job_streams.append(stream)
        return unit

    # ------------------------------------------------------------------
    # Workstation
    # ------------------------------------------------------------------

    def _build_workstation(self, ctx) -> WorkstationIR | None:
        name_text = ctx.parserId().getText()
        if not name_text:
            return None
        ws = WorkstationIR(name=name_text)
        for p in ctx.workstationProperty():
            self._apply_workstation_property(p, ws)
        return ws

    def _apply_workstation_property(self, p, ws: WorkstationIR) -> None:
        if p.DESCRIPTION() is not None and p.STRING() is not None:
            ws.description = _unquote(p.STRING().getText())
            return
        if p.OS() is not None:
            ws.os = _first_child_text_after(p, "OS")
            return
        if p.NODE() is not None and p.hostName() is not None:
            ws.node = p.hostName().getText()
            if p.INT() is not None:
                ws.tcp_addr = _safe_int(p.INT())
            return
        if p.FOR() is not None and p.MAESTRO() is not None and p.workstationMaestroBlock() is not None:
            # Recurse into the nested block — TYPE/AUTOLINK/BEHINDFIREWALL props
            for inner in p.workstationMaestroBlock().workstationProperty():
                self._apply_workstation_property(inner, ws)
            return
        if p.DOMAIN() is not None:
            ws.domain = p.parserId().getText() if p.parserId() else None
            return
        if p.TYPE() is not None:
            ws.type = _first_child_text_after(p, "TYPE")
            return
        if p.AUTOLINK() is not None:
            tail = _first_child_text_after(p, "AUTOLINK")
            ws.autolink = tail.upper() == "ON" if tail else None
            return
        if p.BEHINDFIREWALL() is not None:
            tail = _first_child_text_after(p, "BEHINDFIREWALL")
            ws.behind_firewall = tail.upper() == "ON" if tail else None
            return

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    def _build_calendar(self, ctx) -> CalendarIR | None:
        name = ctx.parserId().getText()
        if not name:
            return None
        c = CalendarIR(name=name)
        if ctx.STRING() is not None:
            c.description = _unquote(ctx.STRING().getText())
        for dl in ctx.dateLiteral():
            c.dates.append(dl.getText())
        return c

    # ------------------------------------------------------------------
    # Resource
    # ------------------------------------------------------------------

    def _build_resource(self, ctx) -> ResourceIR | None:
        qn = ctx.qualifiedName().getText()
        # Resource names can be ``WORKSTATION#NAME`` or bare. Strip prefix
        # for the v0.1 ResourceIR shape (it doesn't model the workstation).
        bare = qn.rsplit("#", 1)[-1]
        if not bare:
            return None
        qty = _safe_int(ctx.INT()) or 0
        return ResourceIR(name=bare, quantity=qty)

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _build_prompt(self, ctx) -> PromptIR | None:
        name = ctx.parserId().getText()
        text = _unquote(ctx.STRING().getText()) if ctx.STRING() else ""
        if not name:
            return None
        return PromptIR(name=name, text=text)

    # ------------------------------------------------------------------
    # Event rule
    # ------------------------------------------------------------------

    def _build_event_rule(self, ctx) -> EventRuleIR | None:
        name = ctx.parserId().getText()
        if not name:
            return None
        er = EventRuleIR(name=name)
        for p in ctx.eventRuleProperty():
            if p.DESCRIPTION() is not None and p.STRING() is not None:
                er.description = _unquote(p.STRING().getText())
            elif p.IS() is not None and p.ACTIVE() is not None:
                er.active = True
            elif p.EVENTRULETYPE() is not None and p.parserId() is not None:
                er.rule_type = p.parserId().getText()
            elif p.EVENT() is not None and p.parserId() is not None:
                er.event_type = p.parserId().getText()
                body = p.eventBody()
                if body is not None:
                    if body.NODE() is not None and body.hostName() is not None:
                        er.event_node = body.hostName().getText()
                    if body.FILENAME() is not None and body.STRING() is not None:
                        er.event_filename = _unquote(body.STRING().getText())
            elif p.ACTION() is not None and p.parserId() is not None:
                er.action_type = p.parserId().getText()
                body = p.actionBody()
                if body is not None and body.JOBSTREAM() is not None and body.qualifiedName() is not None:
                    er.target_stream_qualified = body.qualifiedName().getText()
        return er

    # ------------------------------------------------------------------
    # Schedule + stream
    # ------------------------------------------------------------------

    def _build_schedule_and_stream(self, ctx) -> tuple[ScheduleIR | None, JobStreamIR | None]:
        header = ctx.scheduleHeader()
        workstation, scheduler, name = _split_qualified(header.qualifiedName())
        if not name:
            return None, None

        schedule = ScheduleIR(workstation=workstation, scheduler=scheduler, name=name)
        # v0.2: JobStreamIR is keyed on (workstation, stream-name). When the
        # SCHEDULE header uses the 3-part legacy form, the middle piece is
        # the scheduler — we still treat the third piece as the stream name.
        stream = JobStreamIR(workstation=workstation, name=name)

        run_cycle_phrase: str | None = None
        for prop in ctx.scheduleProperty():
            self._apply_schedule_property(prop, schedule, stream)
            if prop.onClause() is not None:
                run_cycle_phrase = _phrase_text(prop.onClause().runCyclePhrase())

        # Backward-compat v0.1 fields on ScheduleIR.
        schedule.run_cycle = run_cycle_phrase
        if run_cycle_phrase:
            rc = run_cycle.normalise(run_cycle_phrase, start_time=schedule.start_time)
            schedule.cron_equivalent = rc.cron_equivalent

        for i, jd in enumerate(ctx.jobDefinition()):
            job = self._build_job(jd, schedule.id, workstation, name, order=i)
            if job is not None:
                schedule.jobs.append(job)
                stream.jobs.append(job)

        # Mirror cross-cutting fields onto the JobStreamIR.
        stream.start_time = schedule.start_time
        stream.end_time = schedule.end_time
        stream.priority = schedule.priority
        stream.carry_forward = schedule.carry_forward
        stream.valid_from = schedule.valid_from
        stream.valid_to = schedule.valid_to

        return schedule, stream

    def _apply_schedule_property(self, prop, schedule: ScheduleIR, stream: JobStreamIR) -> None:
        if prop.descriptionClause() is not None:
            stream.description = _unquote(prop.descriptionClause().STRING().getText())
            return
        if prop.onClause() is not None:
            on = prop.onClause()
            rc = RunCycleRef(
                name=_phrase_text(on.runCyclePhrase()),
                raw_phrase=_phrase_text(on.runCyclePhrase()),
            )
            if on.VALIDFROM() is not None and on.dateLiteral() is not None:
                schedule.valid_from = on.dateLiteral().getText()
            if on.CALENDAR() is not None and on.parserId() is not None:
                rc.calendar_name = on.parserId().getText()
            if on.STRING() is not None:
                rc.rrule = _unquote(on.STRING().getText())
            stream.run_cycles.append(rc)
            return
        if prop.exceptRunCycleClause() is not None:
            ex = prop.exceptRunCycleClause()
            rc = RunCycleRef(
                name=_phrase_text(ex.runCyclePhrase()),
                raw_phrase=_phrase_text(ex.runCyclePhrase()),
                is_except=True,
            )
            if ex.STRING() is not None:
                rc.rrule = _unquote(ex.STRING().getText())
            stream.run_cycles.append(rc)
            return
        if prop.atClause() is not None:
            schedule.start_time = _normalise_time(prop.atClause().timeLiteral().getText())
            return
        if prop.untilClause() is not None:
            uc = prop.untilClause()
            schedule.end_time = _normalise_time(uc.timeLiteral().getText())
            action = _on_until_text(uc)
            if action is not None:
                stream.on_until = action
            return
        if prop.deadlineClause() is not None:
            dc = prop.deadlineClause()
            stream.deadline = _normalise_time(dc.timeLiteral().getText())
            action = _on_until_text(dc)
            if action is not None:
                stream.on_until = action
            return
        if prop.everyClause() is not None:
            stream.every = _safe_int(prop.everyClause().INT())
            return
        if prop.carryForwardClause() is not None:
            schedule.carry_forward = True
            return
        if prop.priorityClause() is not None:
            schedule.priority = _safe_int(prop.priorityClause().INT())
            return
        if prop.limitClause() is not None:
            stream.limit = _safe_int(prop.limitClause().INT())
            return
        if prop.validFromClause() is not None:
            schedule.valid_from = prop.validFromClause().dateLiteral().getText()
            return
        if prop.validToClause() is not None:
            schedule.valid_to = prop.validToClause().dateLiteral().getText()
            return
        if prop.followsClause() is not None:
            # Schedule-level FOLLOWS — captured in v0.1 ScheduleFollows shape.
            for item in prop.followsClause().followsItem():
                tgt = item.dependencyTarget()
                sf = _schedule_follows_from_text(tgt.getText())
                if sf:
                    schedule.schedule_follows.append(sf)
            return

    # ------------------------------------------------------------------
    # Job
    # ------------------------------------------------------------------

    def _build_job(
        self, ctx, schedule_id_str: str, workstation: str, stream: str, order: int
    ) -> JobIR | None:
        raw = ctx.jobName().getText()
        name = raw.rsplit("#", 1)[-1].split(".")[0]
        if not name:
            return None
        # v0.2: pass workstation + stream so JobIR.id uses the qualified
        # hash. Two ``VALIDATE`` jobs in different streams now hash distinct.
        job = JobIR(
            schedule_id=schedule_id_str,
            name=name,
            workstation=workstation,
            stream=stream,
            order_in_schedule=order,
        )

        for prop in ctx.jobProperty():
            if prop.scriptNameClause() is not None:
                raw_text = _unquote(prop.scriptNameClause().scriptPath().getText())
                path, args = script_resolver.resolve_script(raw_text)
                if path:
                    job.script_path = path
                    job.script_args = args
                    job.script_type = script_resolver.infer_script_type(path)
            elif prop.streamLogonClause() is not None:
                job.stream_logon = prop.streamLogonClause().parserId().getText()
            elif prop.descriptionClause() is not None:
                job.description = _unquote(prop.descriptionClause().STRING().getText())
            elif prop.recoveryClause() is not None:
                rc = prop.recoveryClause()
                job.recovery = rc.recoveryAction().getText().upper()
                # v0.2: optional RECOVERY AFTER <dependencyTarget> — the
                # recovery JOB to invoke on failure.
                if rc.AFTER() is not None and rc.dependencyTarget() is not None:
                    job.recovery_after = rc.dependencyTarget().getText()
            elif prop.followsClause() is not None:
                for item in prop.followsClause().followsItem():
                    tgt = item.dependencyTarget()
                    text = tgt.getText()
                    bare = text.split(".")[0].split("#")[-1]
                    # Back-compat: v0.1 follows is just a list of bare names.
                    job.follows.append(bare)
                    # v0.2: structured FollowsRef with condition + scope.
                    job.follows_refs.append(_build_follows_ref(text, item))
            elif prop.needsClause() is not None:
                qty = _safe_int(prop.needsClause().INT()) or 0
                full = prop.needsClause().qualifiedName().getText()
                res = full.rsplit("#", 1)[-1].split(".")[0]
                job.needs.append((res, qty))
            elif prop.opensClause() is not None:
                tgt = prop.opensClause().opensTarget()
                p = _unquote(tgt.scriptPath().getText())
                if p:
                    job.opens.append(p)
            elif prop.everyClause() is not None:
                job.every = _safe_int(prop.everyClause().INT())
            elif prop.promptDepClause() is not None:
                # v0.3 — PROMPT may name a prompt OR carry an inline STRING.
                pd = prop.promptDepClause()
                if pd.parserId() is not None:
                    job.prompts.append(pd.parserId().getText())
                elif pd.STRING() is not None:
                    job.prompts.append(_unquote(pd.STRING().getText()))
            elif prop.atClause() is not None:
                # Job-level AT — not promoted to a structured field in v0.1;
                # silently accepted so the parse stays clean.
                pass
            elif prop.priorityClause() is not None:
                job.priority = _safe_int(prop.priorityClause().INT())
        return job


# ----- helpers ----------------------------------------------------------------

def _build_follows_ref(text: str, item_ctx) -> FollowsRef:
    """Classify the dependencyTarget text into internal vs external + capture
    the optional IF SUCC/ABEND/RC=N condition.
    """
    # Strip the wildcard suffix for analysis; not relevant to job-level deps.
    bare_text = text.rstrip(".@")
    parts = bare_text.split("#")
    if len(parts) >= 2 and "." in parts[-1]:
        # WS#STREAM.JOB
        ws = parts[0]
        rest = "#".join(parts[1:])  # in case of 3-part legacy
        stream_part, _, job_part = rest.partition(".")
        return FollowsRef(
            target_qualified=text,
            scope="external",
            condition=_extract_condition(item_ctx),
            target_workstation=ws,
            target_stream=stream_part,
            target_job=job_part,
        )
    # Bare job name — internal to the parent stream.
    return FollowsRef(
        target_qualified=text,
        scope="internal",
        condition=_extract_condition(item_ctx),
        target_job=bare_text.split(".")[0].split("#")[-1],
    )


def _extract_condition(item_ctx) -> str | None:
    cond = item_ctx.followsCondition() if hasattr(item_ctx, "followsCondition") else None
    if cond is None:
        return None
    if cond.SUCC() is not None:
        return "SUCC"
    if cond.ABEND() is not None:
        return "ABEND"
    if cond.RC() is not None and cond.INT() is not None:
        return f"RC={cond.INT().getText()}"
    return None


def _first_child_text_after(p, marker: str) -> str:
    """Return the text of the child following the named keyword token.

    e.g. for ``OS UNIX``, _first_child_text_after(p, "OS") returns ``UNIX``.
    """
    children = list(p.getChildren())
    for i, c in enumerate(children):
        if c.getText().upper() == marker.upper() and i + 1 < len(children):
            return children[i + 1].getText()
    return ""


def _split_qualified(ctx) -> tuple[str, str, str]:
    """`WORKSTATION#SCHEDULER#NAME` (with optional dotted suffix) → 3-tuple.

    Handles both 2-part (workstation#name) and 3-part (workstation#scheduler#name)
    legacy forms.
    """
    text = ctx.getText() if ctx is not None else ""
    parts = text.split("#")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2].split(".")[0]
    if len(parts) == 2:
        return parts[0], "", parts[1].split(".")[0]
    return "", "", parts[0].split(".")[0]


def _schedule_follows_from_text(raw: str) -> ScheduleFollows | None:
    """`WS#SCHEDULER#NAME.@` → ScheduleFollows(target_workstation=WS, target_schedule=NAME, wildcard=True)."""
    if not raw:
        return None
    wildcard = raw.endswith(".@")
    if wildcard:
        raw = raw[:-2]
    elif "." in raw:
        raw = raw.split(".")[0]
    parts = raw.split("#")
    if len(parts) >= 3:
        return ScheduleFollows(target_workstation=parts[0], target_schedule=parts[2], wildcard=wildcard)
    if len(parts) == 2:
        return ScheduleFollows(target_workstation=parts[0], target_schedule=parts[1], wildcard=wildcard)
    return ScheduleFollows(target_workstation="", target_schedule=parts[0], wildcard=wildcard)


def _phrase_text(phrase_ctx) -> str:
    if phrase_ctx is None:
        return ""
    return " ".join(child.getText() for child in phrase_ctx.children)


def _normalise_time(raw: str) -> str:
    """`0530` → `"05:30"`, `"05:30"` → `"05:30"`."""
    if raw is None:
        return ""
    s = raw.strip().strip('"')
    if ":" in s:
        return s
    if len(s) == 4 and s.isdigit():
        return f"{s[:2]}:{s[2:]}"
    return s


def _unquote(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('""', '"')
    return s
