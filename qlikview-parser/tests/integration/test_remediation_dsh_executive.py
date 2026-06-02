"""Remediation plan acceptance tests — exercises Fixes 1/2/3 against
synthetic fixtures that mirror the `_parser_ready/apps/DSH_Executive.qvs`
shape: BINARY upstream with RESIDENT references + attribute-level loads
+ a config-vars include with double-expansion.

Each test is INDEPENDENT of the user's on-disk fixture (we build it
fresh under ``tmp_path``) so the tests run in any environment.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures — mimic the layout of the real _parser_ready/ test estate.
# ---------------------------------------------------------------------------


@pytest.fixture
def estate(tmp_path) -> dict:
    """Build a 3-app + 1-include estate under tmp_path that exercises
    every remediation surface. Returns the dict of paths."""
    apps = tmp_path / "apps"
    includes = tmp_path / "includes"
    apps.mkdir()
    includes.mkdir()

    (includes / "config_vars.qvs").write_text(
        "SET vEnv = 'PROD';\n"
        "SET vQvdRootPROD = '\\\\nas01\\qlik\\PROD\\QVD';\n"
        "SET vQvdRoot = '$(vQvdRootPROD)';\n"
        "SET vExtractQvd = '$(vQvdRoot)\\extract';\n"
        "SET vConnCore = 'LIB://Snowflake_CORE_DWH';\n"
    )

    # Upstream transformer — declares Transactions(SignedAmount, Amount)
    # so the downstream's RESIDENT Transactions has something to inherit.
    (apps / "TRN_DataModel.qvs").write_text(
        "$(Include=..\\includes\\config_vars.qvs);\n"
        "Transactions:\n"
        "LOAD\n"
        "    TxnID,\n"
        "    TxnDate,\n"
        "    Region,\n"
        "    Amount,\n"
        "    Amount * -1   AS SignedAmount\n"
        "RESIDENT _raw;\n"
        "_raw:\n"
        "LOAD * INLINE [TxnID, TxnDate, Region, Amount\n1, 2024-01-01, EMEA, 100];\n"
        "MasterCalendar:\n"
        "LOAD\n"
        "    TxnDate,\n"
        "    Year(TxnDate)    AS Year,\n"
        "    Month(TxnDate)   AS Month\n"
        "RESIDENT Transactions;\n"
    )

    # Downstream dashboard — BINARY into transformer + KPI_Daily that
    # depends on Transactions.SignedAmount + a JOIN onto MasterCalendar.
    (apps / "DSH_Executive.qvs").write_text(
        "BINARY [..\\apps\\TRN_DataModel.qvw];\n"
        "$(Include=..\\includes\\config_vars.qvs);\n"
        "AccessTable:\n"
        "LOAD * INLINE [ACCESS, USERID, REGION\nADMIN, BANK\\svc, *];\n"
        "KPI_Daily:\n"
        "LOAD\n"
        "    TxnDate,\n"
        "    Region,\n"
        "    Count(TxnID)        AS TxnCount,\n"
        "    Sum(SignedAmount)   AS NetFlow,\n"
        "    Sum(Amount)         AS GrossVolume\n"
        "RESIDENT Transactions;\n"
    )

    return {
        "tmp": tmp_path,
        "apps": apps,
        "includes": includes,
        "trn": apps / "TRN_DataModel.qvs",
        "dsh": apps / "DSH_Executive.qvs",
    }


# ---------------------------------------------------------------------------
# Fix 1 — BINARY: bracketed path, .qvw → .qvs fallback, inherited_via,
# lazy resident placeholder when upstream is missing.
# ---------------------------------------------------------------------------


def test_bracketed_binary_path_resolves_to_sibling_qvs(parser_no_neo4j, estate):
    """`BINARY [..\\apps\\TRN_DataModel.qvw]` in DSH should resolve to
    the sibling .qvs via the extension-fallback search."""
    app = parser_no_neo4j.parse_qvs_file(str(estate["dsh"]))
    codes = [d.code for d in app.diagnostics]
    assert "QV-BINARY-FALLBACK" in codes, (
        f"expected fallback resolution, got diagnostics: {codes}"
    )
    assert "QV-BINARY-INHERITED" in codes


def test_inherited_datasets_carry_inherited_via_binary(parser_no_neo4j, estate):
    """Every dataset imported from the BINARY upstream must carry
    ``inherited_via='BINARY'`` and ``inherited_from`` pointing at the
    upstream's file_path."""
    app = parser_no_neo4j.parse_qvs_file(str(estate["dsh"]))
    inherited = [d for d in app.datasets if d.inherited_via == "BINARY"]
    assert inherited, f"no datasets marked inherited_via=BINARY: {[d.name for d in app.datasets]}"
    # Transactions (from the upstream) must be in the host's IR.
    names = {d.name for d in inherited}
    assert "Transactions" in names
    # inherited_from points at the upstream's file_path.
    txn = next(d for d in inherited if d.name == "Transactions")
    assert txn.inherited_from and "TRN_DataModel.qvs" in txn.inherited_from


def test_resident_placeholder_when_binary_missing(parser_no_neo4j, tmp_path):
    """When the BINARY upstream isn't on disk, RESIDENT references in
    the downstream still get a Dataset (placeholder), not a dangle."""
    app_path = tmp_path / "lonely.qvs"
    app_path.write_text(
        "BINARY [..\\NOT_THERE.qvw];\n"
        "KPI:\n"
        "LOAD a RESIDENT Transactions;\n"
    )
    app = parser_no_neo4j.parse_qvs_file(str(app_path))
    codes = [d.code for d in app.diagnostics]
    assert "QV-BINARY-NOT-FOUND" in codes
    assert "QV-RESIDENT-INHERITED" in codes
    # Placeholder must exist + carry the marker.
    placeholders = [d for d in app.datasets
                    if d.inherited_via == "RESIDENT_PLACEHOLDER"]
    assert any(d.name == "Transactions" for d in placeholders), (
        f"expected Transactions placeholder, got: "
        f"{[(d.name, d.inherited_via) for d in app.datasets]}"
    )


# ---------------------------------------------------------------------------
# Fix 2 — Attributes: LOAD-level Attribute records + DERIVES_FROM edges.
# ---------------------------------------------------------------------------


def test_load_fields_emit_attribute_records(parser_no_neo4j, estate):
    """KPI_Daily's 5 projected columns must each emit an Attribute with
    ordinal + source_expr."""
    app = parser_no_neo4j.parse_qvs_file(str(estate["dsh"]))
    kpi_attrs = sorted(
        (a for a in app.attributes if "KPI_Daily" in a.dataset),
        key=lambda a: a.ordinal or 0,
    )
    names = [a.name for a in kpi_attrs]
    assert {"TxnDate", "Region", "TxnCount", "NetFlow", "GrossVolume"} \
        .issubset(set(names)), f"missing KPI_Daily attributes: {names}"
    # source_expr captures the RHS exactly.
    netflow = next(a for a in kpi_attrs if a.name == "NetFlow")
    assert "Sum(SignedAmount)" in (netflow.source_expr or "")


def test_netflow_derives_from_signedamount(parser_no_neo4j, estate):
    """Field-reference DERIVES_FROM: NetFlow's source_expr references
    SignedAmount which lives on the upstream Transactions table. The
    edge from KPI_Daily.NetFlow → Transactions.SignedAmount must exist."""
    from qlikview_parser.ids import attribute_qname, dataset_qname, sha256_id

    app = parser_no_neo4j.parse_qvs_file(str(estate["dsh"]))
    netflow_q = attribute_qname(
        dataset_qname(app.file_path, "KPI_Daily"), "NetFlow",
    )
    signedamt_q = attribute_qname(
        dataset_qname(app.file_path, "Transactions"), "SignedAmount",
    )
    netflow_id = sha256_id(netflow_q)
    signedamt_id = sha256_id(signedamt_q)
    # Convention: dependent (NetFlow) -[DERIVES_FROM]-> upstream (SignedAmount).
    matches = [
        e for e in app.lineage_edges
        if e.rel == "DERIVES_FROM"
        and e.src_id == netflow_id and e.dst_id == signedamt_id
    ]
    assert matches, (
        "missing NetFlow ─DERIVES_FROM─► SignedAmount edge"
    )


# ---------------------------------------------------------------------------
# Fix 3 — Variables: :Variable nodes + RESOLVES_TO edges.
# ---------------------------------------------------------------------------


def test_config_vars_emit_variable_records_with_provenance(parser_no_neo4j, estate):
    """Each SET in config_vars.qvs becomes a Variable with the new v0.3
    fields (app, line, qname, is_connection_ref)."""
    app = parser_no_neo4j.parse_qvs_file(str(estate["dsh"]))
    by_name = {v.name: v for v in app.variables}
    assert "vEnv" in by_name
    assert "vQvdRoot" in by_name
    assert "vConnCore" in by_name
    # Connection-ref detection.
    assert by_name["vConnCore"].is_connection_ref is True
    # qname is app-scoped.
    assert by_name["vEnv"].qname.startswith("var::")
    assert "vEnv" in by_name["vEnv"].qname


def test_double_expansion_emits_var_to_var_resolves_to(parser_no_neo4j, estate):
    """``vQvdRoot = '$(vQvdRootPROD)'`` must produce a RESOLVES_TO edge
    from vQvdRoot → vQvdRootPROD; vExtractQvd → vQvdRoot."""
    from qlikview_parser.ids import sha256_id

    app = parser_no_neo4j.parse_qvs_file(str(estate["dsh"]))
    by_name = {v.name: v for v in app.variables}
    resolves = [e for e in app.lineage_edges if e.rel == "RESOLVES_TO"]
    # vQvdRoot → vQvdRootPROD
    assert any(
        e.src_id == sha256_id(by_name["vQvdRoot"].qname)
        and e.dst_id == sha256_id(by_name["vQvdRootPROD"].qname)
        for e in resolves
    ), "missing vQvdRoot → vQvdRootPROD RESOLVES_TO edge"
    # vExtractQvd → vQvdRoot
    assert any(
        e.src_id == sha256_id(by_name["vExtractQvd"].qname)
        and e.dst_id == sha256_id(by_name["vQvdRoot"].qname)
        for e in resolves
    ), "missing vExtractQvd → vQvdRoot RESOLVES_TO edge"


def test_resolved_value_carries_post_expansion_path(parser_no_neo4j, estate):
    """vExtractQvd's resolved_value must reflect the double-expansion
    chain, not the literal $(vQvdRoot)\\extract."""
    app = parser_no_neo4j.parse_qvs_file(str(estate["dsh"]))
    v = next(v for v in app.variables if v.name == "vExtractQvd")
    # Post-expansion the literal should embed the PROD NAS path.
    assert v.resolved_value is not None
    assert "nas01" in v.resolved_value or "PROD" in v.resolved_value


def test_no_secret_leak_in_variables(parser_no_neo4j, tmp_path):
    """A SET that carries a PWD must be scrubbed in raw_value AND
    resolved_value before reaching the IR."""
    p = tmp_path / "bad.qvs"
    p.write_text(
        "SET vConnString = 'PWD=hunter2hunter2;UID=etl';\n"
        "SET vEcho = '$(vConnString)';\n"
    )
    app = parser_no_neo4j.parse_qvs_file(str(p))
    for v in app.variables:
        for attr in ("raw_value", "resolved_value", "expression"):
            text = getattr(v, attr, None) or ""
            assert "hunter2hunter2" not in text, (
                f"secret leaked through {v.name}.{attr}: {text!r}"
            )
