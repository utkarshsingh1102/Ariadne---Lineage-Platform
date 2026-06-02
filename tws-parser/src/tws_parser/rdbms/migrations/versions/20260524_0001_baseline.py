"""Baseline TWS schema — mirrors lineage-contracts/schema/postgres/tws-schema.sql.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-24
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


# The schema lives in the shared contracts repo so cross-parser consumers can
# read it without depending on this package. The migration loads it verbatim.
_SCHEMA_SQL_PATHS = [
    # Local checkout layout (repo root next to the tws-parser repo)
    Path(__file__).resolve().parents[6] / "lineage-contracts" / "schema" / "postgres" / "tws-schema.sql",
    # Fallback — within the package itself, copy bundled at build time
    Path(__file__).resolve().parents[3] / "rdbms" / "tws-schema.sql",
]


def _load_schema() -> str:
    for p in _SCHEMA_SQL_PATHS:
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"tws-schema.sql not found in any of: {[str(p) for p in _SCHEMA_SQL_PATHS]}"
    )


def upgrade() -> None:
    sql = _load_schema()
    # Drop `-- ...` line comments first so they don't end up as the leading
    # content of a chunk and trick the split-on-`;` loop into skipping it.
    cleaned = "\n".join(
        line for line in sql.splitlines()
        if not line.lstrip().startswith("--")
    )
    for stmt in (s.strip() for s in cleaned.split(";")):
        if stmt:
            op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS tws CASCADE")
