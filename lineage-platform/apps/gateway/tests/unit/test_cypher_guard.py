import pytest

from lineage_gateway.cypher_guard import UnsafeCypherError, assert_read_only


def test_allows_pure_match():
    assert_read_only("MATCH (n:Table) RETURN n LIMIT 10")


def test_allows_match_with_where_and_order_by():
    assert_read_only(
        "MATCH (n)-[:READS_TABLE]->(t:Table) "
        "WHERE n.name CONTAINS 'orders' "
        "RETURN n, t ORDER BY t.name LIMIT 50"
    )


def test_rejects_create():
    with pytest.raises(UnsafeCypherError):
        assert_read_only("CREATE (n:Foo) RETURN n")


@pytest.mark.parametrize(
    "snippet",
    [
        "MERGE (n:Foo) RETURN n",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) SET n.x = 1",
        "MATCH (n) REMOVE n.x",
        "DROP CONSTRAINT foo",
        "LOAD CSV FROM 'x.csv' AS row RETURN row",
        "CALL apoc.refactor.rename.label('A','B') YIELD x RETURN x",
    ],
)
def test_rejects_write_keywords(snippet):
    with pytest.raises(UnsafeCypherError):
        assert_read_only(snippet)


def test_blocked_word_inside_string_literal_is_safe():
    # The keyword is in a string — must not trip the guard.
    assert_read_only("MATCH (n) WHERE n.note = 'CREATE the thing' RETURN n")


def test_blocked_word_inside_line_comment_is_safe():
    assert_read_only(
        "MATCH (n) RETURN n // CREATE would be bad but this is a comment"
    )


def test_blocked_word_inside_block_comment_is_safe():
    assert_read_only(
        "MATCH (n) /* MERGE inside a comment is fine */ RETURN n"
    )


def test_empty_cypher_rejected():
    with pytest.raises(UnsafeCypherError):
        assert_read_only("   ")
