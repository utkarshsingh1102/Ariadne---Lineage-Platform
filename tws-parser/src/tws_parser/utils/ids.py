"""Deterministic node-ID derivation. Mirrors lineage-contracts rules."""

from __future__ import annotations

import hashlib


def make_id(*parts: str) -> str:
    canonical = "::".join((p or "").strip().lower() for p in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Topology nodes
# ---------------------------------------------------------------------------

def schedule_id(workstation: str, scheduler: str, name: str) -> str:
    return make_id("schedule", workstation, scheduler, name)


def workstation_id(name: str) -> str:
    return make_id("workstation", name)


def job_stream_id(workstation: str, name: str) -> str:
    return make_id("stream", workstation, name)


def job_id(workstation: str, stream: str, name: str) -> str:
    """v0.2 — qualified-string hash for jobs.

    Two jobs literally named ``VALIDATE`` in different streams MUST hash to
    distinct ids — that's the marquee collision test in the stress fixture
    (``ETL_AGENT_01#INGESTION.VALIDATE`` vs ``DB_AGENT_01#RECONCILE.VALIDATE``).
    The qualified form makes this property explicit instead of relying on
    a nested schedule_id derivation.
    """
    return make_id("job", workstation, stream, name)


def job_id_legacy(schedule_id_str: str, name: str) -> str:
    """v0.1 fallback used by ``JobIR.__post_init__`` when a constructor
    didn't supply workstation/stream. Production code (visitor, writer)
    should not call this — only old test fixtures that construct
    ``JobIR(schedule_id=..., name=...)`` directly. Kept so the test suite
    survives the v0.2 migration without churning every direct construction.
    """
    return make_id("job", schedule_id_str, name)


def calendar_id(name: str) -> str:
    return make_id("calendar", name)


def prompt_id(name: str) -> str:
    return make_id("prompt", name)


def event_rule_id(name: str) -> str:
    return make_id("event_rule", name)


# ---------------------------------------------------------------------------
# Auxiliary IRs (scripts, resources, file watchers) — cross-parser keys
# ---------------------------------------------------------------------------

def _canonical_script_string(path: str) -> str:
    """The exact string that gets hashed into a :Script id.

    Public-ish so cross-parser tests can assert byte-for-byte equality
    with the Ab Initio / BTEQ parsers' canonical strings.
    """
    return f"script::{(path or '').strip()}".lower()


def script_id(path: str) -> str:
    return hashlib.sha256(_canonical_script_string(path).encode("utf-8")).hexdigest()[:16]


def resource_id(name: str) -> str:
    return make_id("resource", name)


def file_watcher_id(path: str) -> str:
    return make_id("file_watcher", path)


def tws_file_id(path: str) -> str:
    """One :TwsFile node per uploaded composer file. Keyed on the resolved
    path so re-uploading the same file is idempotent and merges existing
    schedule containment rather than orphaning the old node."""
    return make_id("tws_file", path)
