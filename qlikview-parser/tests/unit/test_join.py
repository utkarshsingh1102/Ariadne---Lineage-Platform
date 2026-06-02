"""
JOIN extraction tests (plan §2.6).
Must handle both explicit (TargetTable) and implicit-target forms.
"""
import pytest


def test_explicit_target_join_captured(parse):
    app = parse("03_left_join.qvs")
    # INNER JOIN (Orders) LOAD ... RESIDENT Customer
    explicit = [j for j in app.joins if j.target_table == "Orders"]
    assert len(explicit) >= 1
    assert explicit[0].join_type == "INNER"
    assert explicit[0].source_table == "Customer"


def test_implicit_target_join_captured(parse):
    app = parse("03_left_join.qvs")
    # LEFT JOIN LOAD ... RESIDENT Customer  (attaches to Orders — most recent table)
    left_joins = [j for j in app.joins if j.join_type == "LEFT"]
    assert len(left_joins) >= 1
    assert left_joins[0].source_table == "Customer"
    assert left_joins[0].target_table == "Orders"  # resolved from "most recent"


def test_keep_captured_as_join(parse):
    app = parse("08_realistic_dashboard.qvs")
    keeps = [j for j in app.joins if "KEEP" in j.join_type.upper()]
    assert len(keeps) >= 1


def test_mapping_load_recognised(parse):
    app = parse("08_realistic_dashboard.qvs")
    mappings = [l for l in app.loads if l.table_name == "RegionMap"]
    assert len(mappings) == 1
    assert getattr(mappings[0], "is_mapping", False) is True
