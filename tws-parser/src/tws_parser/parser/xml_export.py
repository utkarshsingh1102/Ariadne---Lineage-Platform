"""XML export path: file → list[ScheduleIR] via lxml."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from tws_parser.models.domain import (
    JobIR,
    JobStreamIR,
    ParsedComposerUnit,
    ScheduleFollows,
    ScheduleIR,
)
from tws_parser.parser import run_cycle, script_resolver
from tws_parser.visitor.error_listener import CollectedError


def parse_xml(path: str | Path) -> list[ScheduleIR]:
    """Convenience: returns schedules only.

    The XML path doesn't produce ANTLR-style structured errors today —
    lxml's recover=True silently fixes what it can. For symmetry with the
    composer path use ``parse_xml_with_errors``.
    """
    schedules, _ = parse_xml_with_errors(path)
    return schedules


def parse_xml_with_errors(
    path: str | Path,
) -> tuple[list[ScheduleIR], list[CollectedError]]:
    unit, errors = parse_xml_full_with_errors(path)
    return unit.schedules, errors


def parse_xml_full_with_errors(
    path: str | Path,
) -> tuple[ParsedComposerUnit, list[CollectedError]]:
    """v0.2 — return the full topology IR.

    The TWS XML export format doesn't carry workstation/calendar/prompt/
    event-rule blocks (those live in separate definition files), so the
    unit's non-schedule lists stay empty. JobStreamIRs are synthesised
    from each ScheduleIR for symmetry with the composer path.
    """
    parser = etree.XMLParser(recover=True, huge_tree=True)
    tree = etree.parse(str(path), parser=parser)
    root = tree.getroot()
    if root is None:
        return ParsedComposerUnit(), []

    unit = ParsedComposerUnit()
    for el in root.iter("schedule"):
        s = _parse_schedule(el)
        if s is not None:
            s.source_file = str(path)
            unit.schedules.append(s)
            # Mirror as JobStreamIR — re-uses the jobs list by reference.
            stream = JobStreamIR(workstation=s.workstation, name=s.name)
            stream.jobs = s.jobs
            stream.start_time = s.start_time
            stream.end_time = s.end_time
            stream.priority = s.priority
            stream.carry_forward = s.carry_forward
            stream.valid_from = s.valid_from
            stream.valid_to = s.valid_to
            unit.job_streams.append(stream)
    return unit, []


def _parse_schedule(el: etree._Element) -> ScheduleIR | None:
    name = _text(el, "name")
    if not name:
        return None

    workstation = _text(el, "workstation")
    scheduler = _text(el, "scheduler")
    s = ScheduleIR(workstation=workstation, scheduler=scheduler, name=name)

    s.valid_from = _text(el, "validFrom") or None
    s.valid_to = _text(el, "validTo") or None
    s.start_time = _text(el, "startTime") or None
    s.end_time = _text(el, "endTime") or None
    s.run_cycle = _text(el, "onRunCycle") or None

    cf = _text(el, "carryForward")
    s.carry_forward = cf.lower() in {"true", "yes", "1"}

    priority = _text(el, "priority")
    if priority.isdigit():
        s.priority = int(priority)

    if s.run_cycle:
        rc = run_cycle.normalise(s.run_cycle, start_time=s.start_time)
        s.cron_equivalent = rc.cron_equivalent

    for dep in el.findall("./follows/dependency"):
        sf = _xml_schedule_follows(dep)
        if sf:
            s.schedule_follows.append(sf)

    for i, job_el in enumerate(el.findall("./jobs/job")):
        j = _parse_job(job_el, s.id, order=i)
        if j is not None:
            s.jobs.append(j)
    return s


def _xml_schedule_follows(dep_el: etree._Element) -> ScheduleFollows | None:
    sched = _text(dep_el, "schedule")
    if not sched:
        return None
    wildcard_raw = _text(dep_el, "wildcard").lower()
    wildcard = wildcard_raw in {"true", "yes", "1"}
    ws = _text(dep_el, "workstation")
    return ScheduleFollows(target_workstation=ws, target_schedule=sched, wildcard=wildcard)


def _parse_job(el: etree._Element, schedule_id_str: str, order: int) -> JobIR | None:
    name = _text(el, "name")
    if not name:
        return None
    job = JobIR(schedule_id=schedule_id_str, name=name, order_in_schedule=order)
    job.stream_logon = _text(el, "streamLogon") or None
    job.recovery = (_text(el, "recovery").upper() or None)
    job.description = _text(el, "description") or None

    raw_script = _text(el, "scriptName")
    if raw_script:
        path, args = script_resolver.resolve_script(raw_script)
        if path:
            job.script_path = path
            job.script_args = args
            job.script_type = script_resolver.infer_script_type(path)

    for f_el in el.findall("./follows/dependency"):
        j_name = _text(f_el, "job") or _text(f_el, "name")
        if j_name:
            job.follows.append(j_name)

    for needs_el in el.findall("./needs"):
        # Two shapes:
        #   <needs><resource>NAME</resource><quantity>1</quantity></needs>
        #   <needs><resource name="NAME" quantity="1"/></needs>
        res_attr = needs_el.find("./resource")
        if res_attr is not None and res_attr.get("name"):
            res_name = res_attr.get("name") or ""
            qty_s = res_attr.get("quantity") or "1"
            try:
                qty = int(qty_s)
            except ValueError:
                qty = 1
            if res_name:
                job.needs.append((res_name, qty))
        else:
            res = _text(needs_el, "resource") or _text(needs_el, "name")
            qty_s = _text(needs_el, "quantity") or "1"
            try:
                qty = int(qty_s)
            except ValueError:
                qty = 1
            if res:
                job.needs.append((res, qty))

    for opens_el in el.findall("./opens"):
        file_el = opens_el.find("./file")
        if file_el is not None and file_el.get("path"):
            job.opens.append(file_el.get("path") or "")
            continue
        p = _text(opens_el, "path") or (opens_el.text or "").strip()
        if p:
            job.opens.append(p)

    return job


def _text(parent: etree._Element, tag: str) -> str:
    child = parent.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()
