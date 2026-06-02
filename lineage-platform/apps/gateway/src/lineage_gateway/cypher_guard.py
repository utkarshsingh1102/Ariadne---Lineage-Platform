"""Read-only Cypher validator.

Strips comments, normalizes whitespace, and rejects any statement that
contains a write-keyword. Used by ``/graph/query/cypher`` so that the
power-user endpoint (and, later, the NLP service) can never mutate the
graph.
"""
from __future__ import annotations

import re

_BLOCKED_KEYWORDS = {
    "CREATE", "MERGE", "DELETE", "DETACH",
    "SET", "REMOVE", "DROP",
    # Procedure calls that can write — block the broad CALL keyword.
    # Read-only procedures (e.g. db.schema.visualization) are exposed via
    # dedicated endpoints instead.
    "CALL",
    "LOAD", "FOREACH",
    # USING PERIODIC COMMIT — only valid with LOAD CSV but block defensively.
    "PERIODIC",
}

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"//[^\n]*")
# Strip both single- and double-quoted string literals so blocked keywords
# inside a string ("MATCH (n) WHERE n.note = 'CREATE'") don't trip the guard.
_STRING_LIT_DQ = re.compile(r'"(?:[^"\\]|\\.)*"')
_STRING_LIT_SQ = re.compile(r"'(?:[^'\\]|\\.)*'")


class UnsafeCypherError(ValueError):
    """Raised when a Cypher statement contains a write keyword."""


def assert_read_only(cypher: str) -> None:
    """Raise UnsafeCypherError if cypher contains any write operation.

    The check is purely lexical — we lowercase, strip comments and string
    literals, then look for word-boundary matches of blocked keywords.
    """
    if not cypher or not cypher.strip():
        raise UnsafeCypherError("Empty Cypher")

    stripped = _COMMENT_BLOCK.sub(" ", cypher)
    stripped = _COMMENT_LINE.sub(" ", stripped)
    stripped = _STRING_LIT_DQ.sub('""', stripped)
    stripped = _STRING_LIT_SQ.sub("''", stripped)
    upper = stripped.upper()

    for kw in _BLOCKED_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            raise UnsafeCypherError(
                f"Write keyword '{kw}' is not allowed on this endpoint"
            )
