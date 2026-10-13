"""Add NSI structures and address-structure matches.

Revision ID: 0002_structures
Revises: 0001_initial
Create Date: 2026-10-13
"""

from __future__ import annotations

import geoalchemy2
import sqlalchemy as sa

from alembic import op

revision = "0002_structures"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "structures",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("hazard_sources.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("bid", sa.String(64)),
        sa.Column("bldgtype", sa.String(4)),
        sa.Column("occtype", sa.String(16)),
        sa.Column("damage_category", sa.String(8)),
        sa.Column("num_story", sa.Integer),
        sa.Column("sqft", sa.Float),
        sa.Column("found_type", sa.String(4)),
        sa.Column("found_ht_ft", sa.Float),
        sa.Column("ground_elev_ft", sa.Float),
        sa.Column("year_built", sa.Integer),
        sa.Column("val_struct_usd", sa.Float),
        sa.Column("val_cont_usd", sa.Float),
        sa.Column("firmzone", sa.String(8)),
        sa.Column("static_bfe_ft", sa.Float),
        sa.Column("county_fips", sa.String(5), index=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index("ix_structures_geom", "structures", ["geom"], postgresql_using="gist")
    op.create_table(
        "address_structures",
        sa.Column("address_id", sa.Integer, sa.ForeignKey("addresses.id"), primary_key=True),
        sa.Column("structure_id", sa.Integer, sa.ForeignKey("structures.id"), index=True),
        sa.Column("match_distance_m", sa.Float),
        sa.Column("uncertainty", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("address_structures")
    op.drop_table("structures")
