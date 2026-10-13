"""Functional geography indexes for proximity lookups.

The fire-history and earthquake lookups filter with ``ST_DWithin(geom::geography, ...)``.
The cast hides the plain geometry GiST index, so PostgreSQL falls back to a sequential
scan over every perimeter for every point (observed: one 500-point batch > 5 minutes on
18k perimeters). An expression index on ``(geom::geography)`` lets those predicates use
the index.

Revision ID: 0004_geography_indexes
Revises: 0003_widen_dedup_key
Create Date: 2026-10-13
"""

from __future__ import annotations

from alembic import op

revision = "0004_geography_indexes"
down_revision = "0003_widen_dedup_key"
branch_labels = None
depends_on = None

_TABLES = ("wildfire_events", "earthquake_events", "hazard_lines", "structures")


def upgrade() -> None:
    for t in _TABLES:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{t}_geog ON {t} USING gist ((geom::geography))")


def downgrade() -> None:
    for t in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{t}_geog")
