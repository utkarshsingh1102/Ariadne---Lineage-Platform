"""
Include resolution tests (plan §2.7 / §6 step 2).
Two syntaxes:
  - Legacy:  INCLUDE 'path'
  - Modern:  $(Include=path) / $(Must_Include=path)
Both must be resolved recursively with a depth limit (plan §8: QLIK_MAX_INCLUDE_DEPTH).
"""
import os
import pytest


def test_legacy_include_recorded(parse):
    app = parse("06_variables_and_includes.qvs")
    assert any("common.qvs" in p for p in app.includes), \
        f"Legacy INCLUDE not resolved. Got: {app.includes}"


def test_modern_include_directive_resolved(parse):
    app = parse("08_realistic_dashboard.qvs")
    paths = [os.path.basename(p) for p in app.includes]
    assert "common.qvs" in paths
    assert "connections.qvs" in paths


def test_must_include_resolved(parse):
    app = parse("08_realistic_dashboard.qvs")
    paths = [os.path.basename(p) for p in app.includes]
    # Must_Include for common.qvs in the realistic fixture
    assert "common.qvs" in paths


def test_include_cycle_does_not_hang(parser_no_neo4j, tmp_path):
    """A.qvs INCLUDEs B.qvs, B.qvs INCLUDEs A.qvs — must terminate."""
    a = tmp_path / "A.qvs"
    b = tmp_path / "B.qvs"
    a.write_text("INCLUDE 'B.qvs';\n")
    b.write_text("INCLUDE 'A.qvs';\n")
    # Should return, not hang
    app = parser_no_neo4j.parse_qvs_file(str(a))
    assert app is not None


def test_include_depth_limit_enforced(parser_no_neo4j, tmp_path):
    # Create a chain A → B → C → ... 12 deep; should raise/warn at depth 10
    files = []
    for i in range(12):
        f = tmp_path / f"chain_{i}.qvs"
        files.append(f)
    for i in range(11):
        files[i].write_text(f"INCLUDE 'chain_{i+1}.qvs';\n")
    files[11].write_text("// terminal\n")

    app = parser_no_neo4j.parse_qvs_file(str(files[0]))
    assert any("depth" in (e or "").lower() for e in (app.parse_errors or []))
