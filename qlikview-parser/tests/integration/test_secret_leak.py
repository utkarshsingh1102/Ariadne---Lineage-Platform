"""Phase 1 CI gate — no secret material ever leaks into parser output.

Builds a synthetic .qvs containing every known secret pattern that
``secrets.py`` recognises, parses it, then dumps every property of the
resulting IR through ``looks_like_secret``. Any positive hit fails the
build. This is the standing safety net per v2 plan §0 invariant 6.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, is_dataclass

from qlikview_parser.secrets import looks_like_secret


# A script with every secret shape the scrubber knows about. ANY of these
# surviving to the parser output is a leak.
_LEAKY_SCRIPT = """\
SET vEnv = 'PROD';
OLEDB CONNECT TO [Provider=Snowflake;Server=acme.snowflakecomputing.com;Database=PROD;User=svc;Password=hunter2ABC!;Warehouse=WH_XL];
ODBC CONNECT TO [DSN=Redshift;UID=etl;PWD=Sup3rS3cret!];
SET vAws = 'access_key_id=AKIAIOSFODNN7EXAMPLE';
SET vAzure = 'AccountKey=ZmFrZWFjY291bnRrZXkxMjM0NTY3ODkwYWJjZGVmZ2hpams=';
SET vBearer = 'Authorization: Bearer eyJfakeJWTtoken12345abcdef';

Customers:
SQL SELECT id, name FROM CORE.CUSTOMERS;
"""


def _flatten_for_grep(obj) -> list[str]:
    """Walk the IR / dict recursively and emit every string value so the
    grep is exhaustive — including dataclass attributes, nested lists,
    and dict values."""
    out: list[str] = []
    if obj is None:
        return out
    if isinstance(obj, str):
        out.append(obj)
        return out
    if isinstance(obj, (int, float, bool)):
        return out
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_flatten_for_grep(v))
        return out
    if isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            out.extend(_flatten_for_grep(item))
        return out
    if is_dataclass(obj):
        out.extend(_flatten_for_grep(asdict(obj)))
        return out
    # Fallback — getattr-walk objects that expose properties.
    if hasattr(obj, "__dict__"):
        out.extend(_flatten_for_grep(vars(obj)))
    return out


def test_no_secret_shape_in_parsed_ir(parser_no_neo4j):
    """Build a synthetic .qvs full of secrets, parse it, then assert no
    string in the resulting IR looks like an unredacted secret."""
    with tempfile.NamedTemporaryFile(suffix=".qvs", delete=False, mode="w") as f:
        f.write(_LEAKY_SCRIPT)
        path = f.name

    app = parser_no_neo4j.parse_qvs_file(path)

    leaks: list[tuple[str, str]] = []
    for s in _flatten_for_grep(app):
        if looks_like_secret(s):
            leaks.append((s[:80], "..."))

    assert not leaks, (
        f"SECURITY: {len(leaks)} string(s) in the parser output match a known "
        f"secret pattern after scrubbing. First leak: {leaks[0][0]!r}"
    )


def test_no_secret_shape_in_json_export(parser_no_neo4j):
    """Same gate, but against the JSON serialisation path that the CLI
    uses for ``--emit-json``. JSON serialisation can resurrect secrets
    if to_dict() bypasses scrubbed properties."""
    with tempfile.NamedTemporaryFile(suffix=".qvs", delete=False, mode="w") as f:
        f.write(_LEAKY_SCRIPT)
        path = f.name

    app = parser_no_neo4j.parse_qvs_file(path)
    serialised = json.dumps(app.to_dict())

    assert not looks_like_secret(serialised), (
        "SECURITY: JSON export contains a secret-shaped string after scrubbing"
    )


def test_data_connection_carries_fingerprint_not_secret(parser_no_neo4j):
    """The DataConnection.secret_fingerprint property must be a salted
    hash, NEVER the plaintext password. Catches an accidental swap."""
    with tempfile.NamedTemporaryFile(suffix=".qvs", delete=False, mode="w") as f:
        f.write(_LEAKY_SCRIPT)
        path = f.name

    app = parser_no_neo4j.parse_qvs_file(path)
    for c in app.data_connections:
        if c.secret_fingerprint:
            # Must be a 32-char hex fingerprint, not the plaintext.
            assert len(c.secret_fingerprint) == 32
            assert all(ch in "0123456789abcdef" for ch in c.secret_fingerprint)
            assert "hunter2" not in c.secret_fingerprint
            assert "Sup3r" not in c.secret_fingerprint
