"""
Comment-handling tests (plan §9.1, REVIEW.md §4.3).
All three comment flavours must be stripped before any regex / lexer pass.
"""
import pytest


def test_line_comment_does_not_affect_parsing(parse):
    app = parse("09_comments_and_edge_cases.qvs")
    customer = next(l for l in app.loads if l.table_name == "Customer")
    assert customer.source_type.value == "SQL"


def test_block_comment_does_not_affect_parsing(parser_no_neo4j, tmp_path):
    script = tmp_path / "block_comment.qvs"
    script.write_text("""
Customer:
LOAD A, B, C;
SQL SELECT A, B, C FROM PROD.X.Y;

/*
   This block comment contains the word RESIDENT and
   even FROM PROD.SECRET.TABLE — neither must be picked up.
*/

Orders:
LOAD A;
SQL SELECT A FROM PROD.X.Z;
""")
    app = parser_no_neo4j.parse_qvs_file(str(script))
    customer = next(l for l in app.loads if l.table_name == "Customer")
    assert customer.source_type.value == "SQL"
    assert "SECRET" not in (customer.source_table or "").upper()


def test_rem_comment_does_not_affect_parsing(parser_no_neo4j, tmp_path):
    script = tmp_path / "rem.qvs"
    script.write_text("""
Customer:
LOAD A;
SQL SELECT A FROM PROD.X.Y;

REM This comment mentions RESIDENT SomeFakeTable on purpose ;

Orders:
LOAD B;
SQL SELECT B FROM PROD.X.Z;
""")
    app = parser_no_neo4j.parse_qvs_file(str(script))
    customer = next(l for l in app.loads if l.table_name == "Customer")
    assert customer.source_type.value == "SQL", \
        f"Customer mis-classified as {customer.source_type.value} (REM bled in)"
    assert customer.source_table != "SomeFakeTable"


def test_double_slash_inside_string_literal_preserved(parse):
    app = parse("09_comments_and_edge_cases.qvs")
    with_url = next(l for l in app.loads if l.table_name == "WithUrl")
    # The full literal 'https://example.com/path' must survive intact in the formula
    synthetics = [f for f in app.fields if f.is_synthetic and f.name == "SourceUrl"]
    assert synthetics, "SourceUrl synthetic alias not captured at all"
    assert "https://example.com/path" in (synthetics[0].formula or ""), \
        f"URL truncated by greedy comment-strip: {synthetics[0].formula!r}"
