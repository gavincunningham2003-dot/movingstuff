"""Widen addresses.dedup_key.

The key embeds the full street name, so long street names overflowed the
original 64-character column on the first live OpenAddresses run.

Revision ID: 0003_widen_dedup_key
Revises: 0002_structures
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_widen_dedup_key"
down_revision = "0002_structures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "addresses",
        "dedup_key",
        existing_type=sa.String(64),
        type_=sa.String(512),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "addresses",
        "dedup_key",
        existing_type=sa.String(512),
        type_=sa.String(64),
        existing_nullable=True,
    )
