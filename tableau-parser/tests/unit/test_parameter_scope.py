"""Step 4 — :TableauParameterScope synthetic node.

Confirms the hybrid design: parameters land under a ParameterScopeIR that
does NOT inflate the user-datasource count.
"""
from __future__ import annotations


def _parse(fixture_path, name):
    from tableau_parser.parser.workbook import parse_workbook
    return parse_workbook(str(fixture_path(name)))


def test_parameter_scope_emitted_when_parameters_block_present(fixture_path):
    wb = _parse(fixture_path, "07_parameters.twb")
    assert len(wb.parameter_scopes) == 1
    scope = wb.parameter_scopes[0]
    assert scope.name == "Parameters"
    assert scope.workbook_id == wb.id
    assert scope.line is not None


def test_user_datasource_count_unchanged_by_scope(fixture_path):
    """Fixture 07 has 1 user datasource (Sales) + the Parameters block.
    The user-datasource count must stay 1; the scope is counted separately."""
    wb = _parse(fixture_path, "07_parameters.twb")
    assert len(wb.datasources) == 1
    assert wb.datasources[0].name == "sales_ds"


def test_parameters_carry_scope_id_when_under_parameters_ds(fixture_path):
    wb = _parse(fixture_path, "07_parameters.twb")
    scope_id = wb.parameter_scopes[0].id
    by_name = {p.name: p for p in wb.parameters}
    # All three Parameter columns from fixture 07 live under Parameters.
    for p in by_name.values():
        assert p.scope_id == scope_id


def test_parameters_embedded_in_user_ds_have_empty_scope_id(fixture_path):
    """Fixture 10 has [Min Sales] inside sales_ds (param-domain-type='range').
    That param's scope_id is empty — it doesn't live under the scope."""
    wb = _parse(fixture_path, "10_resolution_regression.twb")
    by_name = {p.name: p for p in wb.parameters}
    assert by_name["Region Filter"].scope_id  # in Parameters
    assert by_name["Min Sales"].scope_id == ""  # in sales_ds


def test_stats_exposes_parameter_scopes(fixture_path):
    wb = _parse(fixture_path, "07_parameters.twb")
    assert wb.stats()["parameter_scopes"] == 1
    assert wb.stats()["datasources"] == 1
