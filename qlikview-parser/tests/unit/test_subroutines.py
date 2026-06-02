"""
Subroutine tests (plan §2.8).
SUB ... END SUB definitions should be tracked; CALL sites should be inlined
for lineage purposes.
Currently unimplemented — see REVIEW.md §3.3.
"""
import pytest


def test_subroutine_definition_captured(parse):
    app = parse("07_subroutines.qvs")
    subs = getattr(app, "subroutines", None) or []
    names = {s.name for s in subs}
    assert "LoadDimensionTable" in names
    assert "LogStep" in names


def test_subroutine_params_recorded(parse):
    app = parse("07_subroutines.qvs")
    subs = getattr(app, "subroutines", None) or []
    ldt = next((s for s in subs if s.name == "LoadDimensionTable"), None)
    assert ldt is not None
    assert ldt.params == ["TableName", "FQSourceTable"]


def test_call_site_inlines_loads(parse):
    app = parse("07_subroutines.qvs")
    table_names = {l.table_name for l in app.loads}
    # CALL LoadDimensionTable('Customer', 'PROD.SALES.CUSTOMER') should produce Customer
    assert "Customer" in table_names
    assert "Product" in table_names
