"""
CONCATENATE / NOCONCATENATE tests (plan §2.5).
Should emit CONCATENATES_INTO edges between :QlikTable nodes.
Currently entirely unimplemented — see REVIEW.md §3.3.
"""
import pytest


def test_concatenate_target_recorded(parse):
    app = parse("04_concatenate.qvs")
    # Q2 LOAD should concatenate into SalesQ1
    concats = getattr(app, "concatenations", None) or []
    assert any(c.target_table == "SalesQ1" for c in concats)


def test_noconcatenate_creates_separate_table(parse):
    app = parse("04_concatenate.qvs")
    names = [l.table_name for l in app.loads]
    # SalesQ3 follows a NOCONCATENATE, so it's a distinct table
    assert "SalesQ3" in names


def test_concatenate_in_realistic_fixture(parse):
    app = parse("08_realistic_dashboard.qvs")
    concats = getattr(app, "concatenations", None) or []
    # CONCATENATE (Orders) LOAD ... FROM orders_archive.qvd
    assert any(c.target_table == "Orders" for c in concats)
