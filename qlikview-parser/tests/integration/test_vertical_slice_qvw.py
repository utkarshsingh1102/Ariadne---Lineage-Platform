"""Phase 1 exit gate — the v2 plan's vertical-slice rule.

A single binary .qvw containing one ``LIB CONNECT TO`` + one
``SQL SELECT`` + one ``STORE INTO`` must flow end-to-end into the v0.2
graph schema:

    :DataPlatform  ← :DataConnection ← :PhysicalSource(db_table)
                                         ↓ SOURCED_FROM
                                         :Dataset →2× :Attribute
                                         :Dataset → :STORED_AS → :PhysicalSource(qvd)

Built using the in-process MS-CFB writer in ``extract_qvw.py`` so we
never have to check a binary fixture into the repo.
"""
from __future__ import annotations

import os
import tempfile

from qlikview_parser.extract_qvw import write_synthetic_qvw


_VERTICAL_SCRIPT = """\
SET vEnv = 'PROD';
LIB CONNECT TO 'snowflake-prod';
Customers:
SQL SELECT id, name FROM CORE.CUSTOMERS;
STORE Customers INTO 'qvd/customers.qvd' (qvd);
"""


def _build_qvw(tmpdir: str) -> str:
    path = os.path.join(tmpdir, "vertical_slice.qvw")
    write_synthetic_qvw(path, _VERTICAL_SCRIPT)
    return path


def test_qvw_flows_through_all_v2_entities(parser_no_neo4j, tmp_path):
    """The full chain from platform → attribute → qvd must be present in
    the parsed IR after ``parse_qvw_file`` on a binary .qvw fixture."""
    qvw = _build_qvw(str(tmp_path))

    app = parser_no_neo4j.parse_qvw_file(qvw)

    # The .qvw path survives — implementation detail of the transient
    # .qvs round-trip must NOT leak into the IR's file_path.
    assert app.file_path == qvw

    # ----- DataPlatform -----------------------------------------------
    platform_kinds = {p.kind for p in app.platforms}
    assert "snowflake" in platform_kinds, (
        f"missing snowflake platform — got: {platform_kinds}"
    )

    # ----- DataConnection ---------------------------------------------
    conn_names = {c.name for c in app.data_connections}
    assert "snowflake-prod" in conn_names, (
        f"missing snowflake-prod connection — got: {conn_names}"
    )

    # ----- PhysicalSource (db_table + qvd) ----------------------------
    db_sources = [s for s in app.physical_sources if s.kind == "db_table"]
    qvd_sources = [s for s in app.physical_sources if s.kind == "qvd"]
    assert any("CUSTOMERS" in s.locator.upper() for s in db_sources), (
        f"no db_table source resolved for CUSTOMERS — got: "
        f"{[s.locator for s in db_sources]}"
    )
    assert any("customers.qvd" in s.locator for s in qvd_sources), (
        f"no qvd source for STORE target — got: "
        f"{[s.locator for s in qvd_sources]}"
    )

    # ----- Dataset + Attributes (the LEAF nodes the v2 plan requires) -
    dataset_names = {d.name for d in app.datasets}
    assert any("CUSTOMERS" in n.upper() for n in dataset_names), (
        f"no Dataset for CUSTOMERS — got: {dataset_names}"
    )

    attr_names = {a.name.lower() for a in app.attributes}
    assert {"id", "name"}.issubset(attr_names), (
        f"missing id/name attributes — got: {attr_names}"
    )

    # ----- STORED_AS edge from Dataset to qvd sink --------------------
    stored_as = [e for e in app.lineage_edges if e.rel == "STORED_AS"]
    assert stored_as, "no STORED_AS edge emitted for STORE INTO"


def test_qvw_extraction_diagnostics_merge_into_app(parser_no_neo4j, tmp_path):
    """OLE-walk diagnostics (e.g. corrupt stream warnings) should land on
    the app's diagnostics list, not be silently dropped."""
    qvw = _build_qvw(str(tmp_path))
    app = parser_no_neo4j.parse_qvw_file(qvw)
    # Clean QVW → no warn/error diagnostics from the OLE layer (info-level
    # secret-scrub diagnostics are fine and expected).
    severe = [d for d in app.diagnostics if d.level in ("warn", "error")
              and d.code.startswith("QV-QVW")]
    assert not severe, f"unexpected QVW-layer diagnostics: {severe}"


def test_qvw_extraction_failure_is_soft(parser_no_neo4j, tmp_path):
    """A garbage non-OLE file must NOT raise — it should produce an app
    with a diagnostic and parse_error entry (fail-soft per §0 invariant 5)."""
    bad = tmp_path / "bogus.qvw"
    bad.write_bytes(b"this is not an OLE compound document at all" * 8)
    app = parser_no_neo4j.parse_qvw_file(str(bad))
    assert app is not None
    assert any(d.code == "QV-QVW-EXTRACT" for d in app.diagnostics)
    assert any("QVW extraction failed" in e for e in app.parse_errors)
