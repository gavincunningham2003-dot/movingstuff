"""Distinguish exposure values, policy terms and duplicate kinds on portfolio rows.

Adds contents value, policy limit and deductible as columns (the deductible previously
lived inside the ``financial`` JSON), a ``duplicate_kind`` classification
(exact | shared_location | repeated_id) with the 5 m ``location_key`` it was derived from,
and per-portfolio counts of shared-location rows and distinct locations.

Revision ID: 0006_portfolio_exposure_fields
Revises: 0005_widen_assessment_text
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_portfolio_exposure_fields"
down_revision = "0005_widen_assessment_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portfolios", sa.Column("n_shared_location", sa.Integer(), server_default="0"))
    op.add_column("portfolios", sa.Column("n_locations", sa.Integer(), server_default="0"))
    op.add_column("portfolio_properties", sa.Column("contents_value", sa.Float(), nullable=True))
    op.add_column("portfolio_properties", sa.Column("policy_limit", sa.Float(), nullable=True))
    op.add_column("portfolio_properties", sa.Column("deductible", sa.Float(), nullable=True))
    op.add_column("portfolio_properties", sa.Column("duplicate_kind", sa.String(24), nullable=True))
    op.add_column("portfolio_properties", sa.Column("location_key", sa.String(64), nullable=True))
    op.create_index(
        "ix_portfolio_properties_location_key", "portfolio_properties", ["location_key"]
    )
    # Backfill: earlier rows stored the deductible in the financial JSON and flagged every
    # location duplicate; those were all "shared_location" or "exact" — keep the honest label.
    op.execute(
        "UPDATE portfolio_properties SET deductible = (financial->>'deductible')::float "
        "WHERE deductible IS NULL AND financial IS NOT NULL "
        "AND financial->>'deductible' IS NOT NULL"
    )
    op.execute(
        "UPDATE portfolio_properties SET duplicate_kind = 'shared_location' "
        "WHERE is_duplicate_of IS NOT NULL AND duplicate_kind IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_portfolio_properties_location_key", table_name="portfolio_properties")
    for col in ("location_key", "duplicate_kind", "deductible", "policy_limit", "contents_value"):
        op.drop_column("portfolio_properties", col)
    op.drop_column("portfolios", "n_locations")
    op.drop_column("portfolios", "n_shared_location")
