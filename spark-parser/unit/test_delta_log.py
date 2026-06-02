"""Unit tests for v0.2 §5 — Delta log schema-evolution reader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark_parser.runtime.delta_log import read_delta_log

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "delta_logs"
    / "orders" / "_delta_log"
)


def test_initial_commit_emits_add_for_every_column():
    events, warnings = read_delta_log(FIXTURE, table_fqn="prod.raw.orders")
    add_v0 = [e for e in events if e.version == 0 and e.kind == "add_column"]
    cols = {e.column for e in add_v0}
    assert cols == {"order_id", "amount"}
    # Each event carries the table_fqn
    assert all(e.table_fqn == "prod.raw.orders" for e in add_v0)


def test_add_column_between_versions():
    events, _ = read_delta_log(FIXTURE, table_fqn="prod.raw.orders")
    adds = [e for e in events if e.kind == "add_column" and e.column == "customer_id"]
    assert adds, "expected an add_column for customer_id"
    assert adds[0].version == 1


def test_type_change_detected():
    events, _ = read_delta_log(FIXTURE, table_fqn="prod.raw.orders")
    tc = [e for e in events if e.kind == "type_change" and e.column == "amount"]
    assert tc, "expected a type_change for amount"
    assert tc[0].from_type == "integer"
    assert tc[0].to_type == "long"
    assert tc[0].version == 2


def test_nullability_change_detected():
    events, _ = read_delta_log(FIXTURE, table_fqn="prod.raw.orders")
    nc = [e for e in events if e.kind == "nullability_change" and e.column == "order_id"]
    assert nc, "expected a nullability_change for order_id"
    assert nc[0].from_nullable is False
    assert nc[0].to_nullable is True


def test_drop_column_detected():
    events, _ = read_delta_log(FIXTURE, table_fqn="prod.raw.orders")
    dropped = [e for e in events if e.kind == "drop_column" and e.column == "amount"]
    assert dropped, "expected a drop_column for amount"
    assert dropped[0].version == 4


def test_missing_directory_warns(tmp_path: Path):
    events, warnings = read_delta_log(tmp_path / "nope")
    assert events == []
    assert any(w.type == "delta_log_missing" for w in warnings)


def test_empty_directory_warns(tmp_path: Path):
    events, warnings = read_delta_log(tmp_path)
    assert events == []
    assert any(w.type == "delta_log_empty" for w in warnings)


def test_timestamp_propagated():
    events, _ = read_delta_log(FIXTURE)
    # Every event in this fixture has a timestamp in the commitInfo.
    assert all(e.timestamp_ms is not None for e in events)
    # Monotonically non-decreasing across commits.
    seen = sorted({(e.version, e.timestamp_ms) for e in events if e.version is not None})
    prev = -1
    for _, ts in seen:
        assert ts >= prev
        prev = ts
