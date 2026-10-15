"""Versioned, atomic assessment refreshes and leased, retryable processing jobs.

* ``assessment_runs`` records every refresh (status, hazards, counts, timing, error).
* ``property_hazard_assessments.run_id`` says which run produced each live row.
* ``assessment_staging`` receives the rows of an in-progress run; a completed run swaps
  them into the live table in one transaction, so readers never see a half-refreshed
  table and a crash leaves the previous assessment intact.
* ``processing_jobs`` gains leases (``lease_expires_at``, ``heartbeat_at``, ``worker_id``)
  and retry bookkeeping (``attempts``, ``max_attempts``, ``last_error``) so a job whose
  runner died can be reclaimed instead of staying "running" forever.

Revision ID: 0007_runs_and_job_leases
Revises: 0006_portfolio_exposure_fields
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_runs_and_job_leases"
down_revision = "0006_portfolio_exposure_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="running", index=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="hazards"),
        sa.Column("hazard_ids", sa.JSON),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("n_addresses", sa.Integer, nullable=False, server_default="0"),
        sa.Column("counts", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("notes", sa.Text),
    )
    op.add_column(
        "property_hazard_assessments",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_property_hazard_assessments_run_id", "property_hazard_assessments", ["run_id"]
    )
    op.create_table(
        "assessment_staging",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("address_id", sa.Integer, nullable=False),
        sa.Column("hazard_id", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("source_version", sa.String(128)),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classification", sa.String(128)),
        sa.Column("value", sa.Float),
        sa.Column("units", sa.String(128)),
        sa.Column("probability_horizon", sa.String(64)),
        sa.Column("coverage_status", sa.String(64), nullable=False),
        sa.Column("confidence", sa.String(16)),
        sa.Column("method", sa.String(64)),
        sa.Column("details", sa.JSON),
        sa.Column("caveats", sa.JSON),
    )
    op.add_column(
        "processing_jobs", sa.Column("attempts", sa.Integer, nullable=False, server_default="0")
    )
    op.add_column(
        "processing_jobs",
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
    )
    op.add_column(
        "processing_jobs",
        sa.Column("lease_seconds", sa.Integer, nullable=False, server_default="600"),
    )
    op.add_column("processing_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("processing_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column("processing_jobs", sa.Column("worker_id", sa.String(64)))
    op.add_column("processing_jobs", sa.Column("last_error", sa.Text))
    # Jobs left "claimed"/"running" by an earlier version have no lease: expire them now so
    # the recovery sweep can retry (or fail) them instead of leaving them stuck.
    op.execute(
        "UPDATE processing_jobs SET lease_expires_at = now() - interval '1 second' "
        "WHERE status IN ('claimed', 'running') AND lease_expires_at IS NULL"
    )


def downgrade() -> None:
    for col in (
        "last_error",
        "worker_id",
        "heartbeat_at",
        "lease_expires_at",
        "lease_seconds",
        "max_attempts",
        "attempts",
    ):
        op.drop_column("processing_jobs", col)
    op.drop_table("assessment_staging")
    op.drop_index("ix_property_hazard_assessments_run_id", table_name="property_hazard_assessments")
    op.drop_column("property_hazard_assessments", "run_id")
    op.drop_table("assessment_runs")
