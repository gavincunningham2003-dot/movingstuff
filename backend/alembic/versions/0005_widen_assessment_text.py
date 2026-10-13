"""Widen short text columns on property_hazard_assessments.

The first live assessment run failed with ``StringDataRightTruncation`` on a
VARCHAR(32) column (hazard units strings are descriptive, e.g. "class (0-10)" and
longer). Classification labels are widened at the same time.

Revision ID: 0005_widen_assessment_text
Revises: 0004_geography_indexes
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_widen_assessment_text"
down_revision = "0004_geography_indexes"
branch_labels = None
depends_on = None

_COLS = {
    "units": (32, 128),
    "method": (32, 64),
    "classification": (64, 128),
    "coverage_status": (32, 64),
}


def upgrade() -> None:
    for col, (old, new) in _COLS.items():
        op.alter_column(
            "property_hazard_assessments",
            col,
            existing_type=sa.String(old),
            type_=sa.String(new),
            existing_nullable=(col != "coverage_status"),
        )


def downgrade() -> None:
    for col, (old, new) in _COLS.items():
        op.alter_column(
            "property_hazard_assessments",
            col,
            existing_type=sa.String(new),
            type_=sa.String(old),
            existing_nullable=(col != "coverage_status"),
        )
