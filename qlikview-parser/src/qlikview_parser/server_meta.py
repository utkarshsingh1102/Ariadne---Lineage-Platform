"""Phase 3 — QlikView Server meta parser (v2 plan §1 Stage 1, bucket A).

QMC (QlikView Management Console) exports tasks + triggers as XML. The
output is one or more ``<Task>`` records nested inside a
``<Tasks>`` root; each Task has zero-or-more child ``<Trigger>``
elements (Scheduled / OnDemand / EDX). Each Task carries an
``AppPath`` field pointing at the .qvw it runs.

For lineage purposes we lift:

  - One :class:`ServerTask` per ``<Task>``.
  - One :class:`Trigger` per ``<Trigger>`` (scheduled / EDX / on-event).
  - Optional ``TRIGGERS`` edges: EDX-style triggers fire other tasks,
    creating cross-app dependency edges.

``.shared`` / ``.meta`` files (XML) carry bookmarks + sheet
expressions but are deferred to a follow-on — Phase 3 ships task /
trigger only, which is the lineage-relevant slice.

Identity:
  task::<task_id> | task::<task_name> when no GUID present
  trigger::<task_qname>/<trigger_id_or_index>

Soft-fail: malformed XML emits diagnostics and returns an empty result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree as _et

from .ids import sha256_id
from .models import Diagnostic, LineageEdge


# ---------------------------------------------------------------------------
# IR records — frozen, qname-keyed.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerTask:
    """A QlikView Server reload task.

    Identity: ``task::<task_id>``.
    """
    task_id: str
    name: str | None
    task_type: str           # reload|external|distribute|edx_listener
    app_path: str | None
    enabled: bool = True

    @property
    def qname(self) -> str:
        return f"task::{self.task_id}"


@dataclass(frozen=True)
class ServerTrigger:
    """A trigger attached to a :class:`ServerTask`.

    Identity: ``trigger::<task_id>/<trigger_id_or_index>``.
    """
    trigger_id: str
    kind: str                # scheduled|edx|on_event|on_demand
    schedule: str | None     # cron / iso-interval / target task ref
    task_id: str             # parent task qname (without prefix)

    @property
    def qname(self) -> str:
        return f"trigger::{self.task_id}/{self.trigger_id}"


@dataclass
class ServerMetaResult:
    tasks: list[ServerTask] = field(default_factory=list)
    triggers: list[ServerTrigger] = field(default_factory=list)
    edges: list[LineageEdge] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_tasks_xml(path: Path | str) -> ServerMetaResult:
    """Parse a QMC tasks XML export. Returns a :class:`ServerMetaResult`
    populated with :class:`ServerTask` + :class:`ServerTrigger` IR
    records and any cross-task EDX TRIGGERS edges."""
    p = Path(path)
    result = ServerMetaResult()
    if not p.exists():
        result.diagnostics.append(Diagnostic(
            level="error", code="QV-SERVER-NOT-FOUND",
            message=f"tasks XML not found: {p!s}",
            artifact=str(p), line=None,
        ))
        return result
    try:
        raw = p.read_bytes()
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            raw = raw.decode("utf-16").encode("utf-8")
        root = _et.fromstring(raw)
    except (_et.XMLSyntaxError, ValueError, UnicodeError) as e:
        result.diagnostics.append(Diagnostic(
            level="error", code="QV-SERVER-PARSE",
            message=f"failed to parse {p.name}: {e}",
            artifact=str(p), line=None,
        ))
        return result

    # Accept either a <Tasks> root or a bare <Task> (single-task export).
    if _local(root.tag) == "task":
        task_nodes = [root]
    else:
        task_nodes = [
            t for t in root.iter()
            if _local(t.tag) == "task"
        ]

    # Index task_id → ServerTask so EDX triggers can produce cross-task
    # TRIGGERS edges later.
    task_by_id: dict[str, ServerTask] = {}
    task_by_name: dict[str, ServerTask] = {}

    for tn in task_nodes:
        task = _parse_task(tn)
        if task is None:
            continue
        result.tasks.append(task)
        task_by_id[task.task_id] = task
        if task.name:
            task_by_name[task.name.lower()] = task

    # Pass 2 — triggers (need full task index for cross-references).
    for tn in task_nodes:
        parent_id = (
            _child_text(tn, "id") or _child_text(tn, "taskid")
            or _child_text(tn, "name") or "_unknown"
        )
        for i, trig_node in enumerate(_find_all_child(tn, "trigger")):
            trig = _parse_trigger(trig_node, parent_id, i)
            if trig is None:
                continue
            result.triggers.append(trig)
            # EDX triggers may reference a target task by name — emit a
            # TRIGGERS edge if we can resolve it.
            if trig.kind == "edx" and trig.schedule:
                target = task_by_name.get(trig.schedule.lower())
                if target is not None:
                    result.edges.append(LineageEdge(
                        src_id=sha256_id(trig.qname),
                        dst_id=sha256_id(target.qname),
                        rel="TRIGGERS",
                        transform="EDX",
                        confidence=0.9,
                        evidence=trig.schedule[:120],
                    ))
    return result


def parse_directory(dir_path: Path | str) -> ServerMetaResult:
    """Walk a directory of *.xml task exports and merge results."""
    d = Path(dir_path)
    merged = ServerMetaResult()
    if not d.exists() or not d.is_dir():
        merged.diagnostics.append(Diagnostic(
            level="warn", code="QV-SERVER-DIR",
            message=f"task directory not found: {d!s}",
            artifact=str(d), line=None,
        ))
        return merged
    for f in sorted(d.iterdir()):
        if f.suffix.lower() != ".xml":
            continue
        sub = parse_tasks_xml(f)
        merged.tasks.extend(sub.tasks)
        merged.triggers.extend(sub.triggers)
        merged.edges.extend(sub.edges)
        merged.diagnostics.extend(sub.diagnostics)
    return merged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Strip the ``{namespace}`` from an lxml tag, lowercased."""
    return tag.split("}")[-1].lower()


def _child_text(node, tag: str) -> str | None:
    for child in node:
        if _local(child.tag) == tag.lower() and child.text:
            return child.text.strip()
    # fall back to attribute on the node itself
    for k, v in node.attrib.items():
        if _local(k) == tag.lower() and v:
            return v.strip()
    return None


def _find_all_child(node, tag: str):
    """Return immediate child elements (one level deep) whose local tag
    matches. We deliberately do NOT recurse — a <Trigger> nested inside
    a sub-task means something different."""
    out = []
    for child in node:
        if _local(child.tag) == tag.lower():
            out.append(child)
        # Some exports nest <Triggers><Trigger>…</></> — walk one level.
        if _local(child.tag) == f"{tag.lower()}s":
            for gc in child:
                if _local(gc.tag) == tag.lower():
                    out.append(gc)
    return out


def _parse_task(node) -> ServerTask | None:
    name = _child_text(node, "name")
    task_id = (
        _child_text(node, "id")
        or _child_text(node, "taskid")
        or (name and f"named::{name}")
        or None
    )
    if task_id is None:
        return None
    task_type = (
        _child_text(node, "type")
        or _child_text(node, "tasktype")
        or "reload"
    ).lower()
    app_path = (
        _child_text(node, "apppath")
        or _child_text(node, "document")
        or _child_text(node, "documentpath")
    )
    enabled_raw = _child_text(node, "enabled")
    enabled = (
        enabled_raw is None
        or enabled_raw.strip().lower() not in {"false", "0", "no"}
    )
    return ServerTask(
        task_id=task_id, name=name, task_type=task_type,
        app_path=app_path, enabled=enabled,
    )


def _parse_trigger(node, parent_task_id: str, index: int) -> ServerTrigger | None:
    trigger_id = (
        _child_text(node, "id")
        or _child_text(node, "triggerid")
        or f"t{index}"
    )
    raw_kind = (
        _child_text(node, "type")
        or _child_text(node, "kind")
        or _child_text(node, "triggertype")
        or "scheduled"
    ).lower()
    kind = _normalise_kind(raw_kind)
    schedule = (
        _child_text(node, "schedule")
        or _child_text(node, "expression")
        or _child_text(node, "target")
        or _child_text(node, "targettask")
    )
    return ServerTrigger(
        trigger_id=str(trigger_id),
        kind=kind,
        schedule=schedule,
        task_id=parent_task_id,
    )


def _normalise_kind(raw: str) -> str:
    s = raw.lower()
    if "edx" in s:
        return "edx"
    if "demand" in s or "manual" in s:
        return "on_demand"
    if "event" in s:
        return "on_event"
    return "scheduled"
