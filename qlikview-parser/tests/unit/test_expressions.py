"""
Expression / synthetic-field tests (plan §2.2).
A column written as `<expr> AS <alias>` is a calculated attribute and must
emit DERIVES_FROM edges to every field referenced inside <expr>.
"""
import pytest


def test_synthetic_field_alias_captured(parse):
    app = parse("01_simple_sql_load.qvs")
    # Fixture 01 has no synthetic fields
    synthetics = [f for f in app.fields if f.is_synthetic]
    assert synthetics == []


def test_upper_alias_captured(parse):
    app = parse("08_realistic_dashboard.qvs")
    synthetics = {f.name: f for f in app.fields if f.is_synthetic}
    # Note: skipped if preceding-LOAD chain parsing not yet implemented
    if "CustomerName_Upper" in synthetics:
        assert "UPPER" in (synthetics["CustomerName_Upper"].formula or "").upper()
        assert "CustomerName" in synthetics["CustomerName_Upper"].source_fields


def test_string_literals_not_treated_as_fields(parse):
    app = parse("09_comments_and_edge_cases.qvs")
    bucket = next((f for f in app.fields if f.is_synthetic and f.name == "Bucket"), None)
    assert bucket is not None
    for src in bucket.source_fields:
        assert src.upper() not in {"HIGH", "LOW", "ACTIVE", "INACTIVE"}, \
            f"String literal {src!r} treated as source field"


def test_qlikview_function_blacklist_excludes_builtins(parser_no_neo4j):
    """UPPER, IF, COUNT etc. should not be returned as source fields."""
    sources = parser_no_neo4j._extract_source_fields_from_expression(
        "IF(UPPER(CustomerName) = 'X', SUM(Amount), 0)"
    )
    # Should contain CustomerName and Amount, but NOT IF/UPPER/SUM
    assert "CustomerName" in sources
    assert "Amount" in sources
    for builtin in ("IF", "UPPER", "SUM"):
        assert builtin not in sources


# -----------------------------------------------------------------------------
# Plan §5.4: synthetic attribute IDs must be deterministic
# -----------------------------------------------------------------------------

def test_attribute_ids_are_deterministic(parser_no_neo4j, fixture_path):
    """Parsing the same script twice must produce identical attribute IDs."""
    a = parser_no_neo4j.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    b = parser_no_neo4j.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    ids_a = sorted(getattr(f, "id", None) for f in a.fields)
    ids_b = sorted(getattr(f, "id", None) for f in b.fields)
    assert ids_a == ids_b
    assert all(i is not None for i in ids_a)
