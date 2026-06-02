"""Solution-plan acceptance — attribute nodes + HAS_ATTRIBUTE + DERIVES_FROM
emit correctly for INLINE, RESIDENT and JOIN load variants.

Fixture mirrors the user's ``attr_test.qvs`` exactly (no includes,
no BINARY, no variables) so the ONLY thing under test is the visitor's
field-list walk + writer plumbing.
"""
from __future__ import annotations

import pytest


_FIXTURE = """\
Employees:
LOAD * INLINE [
    EmpID, EmpName, DeptID, Salary
    1, Alice, 10, 50000
    2, Bob, 20, 60000
    3, Carol, 10, 55000
];

EmpSummary:
LOAD
    EmpID                       AS EmployeeID,
    Upper(EmpName)              AS EmployeeName,
    DeptID                      AS Department,
    Salary                      AS AnnualSalary,
    Salary / 12                 AS MonthlySalary,
    Salary * 0.2                AS TaxEstimate
RESIDENT Employees;

Departments:
LOAD * INLINE [
    DeptID, DeptName, Location
    10, Engineering, London
    20, Sales, Mumbai
];

LEFT JOIN (EmpSummary)
LOAD
    DeptID          AS Department,
    DeptName,
    Location
RESIDENT Departments;

STORE EmpSummary INTO [lib://out/emp_summary.qvd] (qvd);
"""


@pytest.fixture
def attr_test(parser_no_neo4j, tmp_path):
    p = tmp_path / "attr_test.qvs"
    p.write_text(_FIXTURE)
    return parser_no_neo4j.parse_qvs_file(str(p))


def _attrs_on(app, table: str):
    return [a for a in app.attributes if a.dataset.endswith(f"/table::{table}")]


# ---- §5 oracle: 15 distinct attributes, distributed 4 / 3 / 8 ------------

def test_total_attribute_count_is_fifteen(attr_test):
    assert len(attr_test.attributes) == 15, (
        f"expected 15 total attrs, got {len(attr_test.attributes)}: "
        f"{[(a.dataset.split('::')[-1], a.name) for a in attr_test.attributes]}"
    )


def test_employees_has_four_attributes_from_inline_header(attr_test):
    """INLINE header parsing — body is ``LOAD * INLINE [...]`` and the
    column names come from the FIRST line inside the bracket."""
    names = [a.name for a in _attrs_on(attr_test, "Employees")]
    assert names == ["EmpID", "EmpName", "DeptID", "Salary"], (
        f"INLINE header parse wrong: {names}"
    )


def test_empsummary_has_eight_attributes_after_join(attr_test):
    """6 from the RESIDENT load + 2 injected by the LEFT JOIN body."""
    names = sorted(a.name for a in _attrs_on(attr_test, "EmpSummary"))
    assert names == sorted([
        "EmployeeID", "EmployeeName", "Department", "AnnualSalary",
        "MonthlySalary", "TaxEstimate", "DeptName", "Location",
    ]), f"EmpSummary attrs wrong: {names}"


def test_departments_has_three_attributes(attr_test):
    names = [a.name for a in _attrs_on(attr_test, "Departments")]
    assert names == ["DeptID", "DeptName", "Location"]


# ---- §5 oracle: DERIVES_FROM edges resolve cleanly -----------------------

def _derives(app, dst_table: str, dst_name: str) -> list[tuple[str, str, str | None]]:
    """"What does ``dst_table.dst_name`` derive from?"

    Edge convention: ``dependent -[DERIVES_FROM]-> upstream``, so we
    look up edges whose **src** matches the dependent and return the
    **dst** (the upstream)."""
    from qlikview_parser.ids import sha256_id
    by_id = {sha256_id(a.qname): (a.dataset.split("/table::")[-1], a.name)
             for a in app.attributes}
    out = []
    for e in app.lineage_edges:
        if e.rel != "DERIVES_FROM":
            continue
        src = by_id.get(e.src_id)        # the dependent
        dst = by_id.get(e.dst_id)        # the upstream
        if not src or not dst:
            continue
        if src == (dst_table, dst_name):
            out.append((dst[0], dst[1], e.transform))
    return out


def test_monthlysalary_derives_from_salary(attr_test):
    edges = _derives(attr_test, "EmpSummary", "MonthlySalary")
    assert ("Employees", "Salary", None) in edges, edges


def test_taxestimate_derives_from_salary(attr_test):
    edges = _derives(attr_test, "EmpSummary", "TaxEstimate")
    assert ("Employees", "Salary", None) in edges, edges


def test_annualsalary_derives_from_salary(attr_test):
    edges = _derives(attr_test, "EmpSummary", "AnnualSalary")
    assert ("Employees", "Salary", None) in edges, edges


def test_employeename_derives_from_empname_via_upper(attr_test):
    edges = _derives(attr_test, "EmpSummary", "EmployeeName")
    assert any(
        s == "Employees" and n == "EmpName" and (t or "").upper().startswith("UPPER")
        for s, n, t in edges
    ), f"missing EmployeeName ← Upper(EmpName): {edges}"


def test_join_brings_deptname_and_location(attr_test):
    """LEFT JOIN should produce JOIN:LEFT-transformed DERIVES_FROM
    edges from Departments → EmpSummary for the non-shared fields."""
    dn = _derives(attr_test, "EmpSummary", "DeptName")
    loc = _derives(attr_test, "EmpSummary", "Location")
    assert any(s == "Departments" and n == "DeptName" for s, n, _ in dn), dn
    assert any(s == "Departments" and n == "Location" for s, n, _ in loc), loc


def test_no_phantom_derives_from_edges(attr_test):
    """Every DERIVES_FROM edge endpoint must resolve to an attribute
    that's actually in the IR — no edges to nodes that don't exist."""
    from qlikview_parser.ids import sha256_id
    valid_ids = {sha256_id(a.qname) for a in attr_test.attributes}
    phantoms = [
        e for e in attr_test.lineage_edges
        if e.rel == "DERIVES_FROM"
        and (e.src_id not in valid_ids or e.dst_id not in valid_ids)
    ]
    assert not phantoms, (
        f"{len(phantoms)} phantom DERIVES_FROM edges: "
        f"{[(e.src_id[:8], e.dst_id[:8], e.transform) for e in phantoms]}"
    )


# ---- §5: HAS_ATTRIBUTE is implicit — every attr carries source_expr ------

def test_every_attribute_carries_source_expr(attr_test):
    """Each Attribute must have its source_expr populated — that's
    what the writer renders as the 'expression' property in the graph."""
    missing = [a for a in attr_test.attributes if not a.source_expr]
    assert not missing, (
        f"attributes with empty source_expr: "
        f"{[(a.dataset.split('::')[-1], a.name) for a in missing]}"
    )


# ---- §7: determinism gate ------------------------------------------------

def test_attribute_ids_are_stable_across_runs(parser_no_neo4j, tmp_path):
    """Two runs over the same fixture must produce identical sets of
    attribute IDs — that's the cross-parser stitching contract."""
    from qlikview_parser.ids import sha256_id

    p = tmp_path / "attr_test.qvs"
    p.write_text(_FIXTURE)
    ids_1 = {sha256_id(a.qname) for a in parser_no_neo4j.parse_qvs_file(str(p)).attributes}

    # Fresh parser instance — no in-process state can leak.
    parser_b = type(parser_no_neo4j)(neo4j_uri="m", neo4j_user="m", neo4j_password="m")
    parser_b.driver = parser_no_neo4j.driver
    ids_2 = {sha256_id(a.qname) for a in parser_b.parse_qvs_file(str(p)).attributes}

    assert ids_1 == ids_2, "attribute IDs drifted between runs"
