"""Initial CATRIX schema (PostGIS).

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-13
"""

from __future__ import annotations

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "regions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("region_type", sa.String(32), nullable=False, index=True),
        sa.Column("fips", sa.String(5), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(128), nullable=False, index=True),
        sa.Column("name_lsad", sa.String(160)),
        sa.Column("land_area_m2", sa.Float),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index("ix_regions_geom", "regions", ["geom"], postgresql_using="gist")

    op.create_table(
        "hazard_sources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(255)),
        sa.Column("version", sa.String(128)),
        sa.Column("source_url", sa.Text),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("record_count", sa.Integer),
        sa.Column(
            "ingestion_status", sa.String(32), nullable=False, server_default="never_ingested"
        ),
        sa.Column("error", sa.Text),
        sa.Column("checksum", sa.String(128)),
        sa.Column("extra", sa.JSON),
    )

    op.create_table(
        "addresses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("external_id", sa.String(128), index=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("hazard_sources.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("source_file", sa.String(255)),
        sa.Column("hash", sa.String(64), index=True),
        sa.Column("number", sa.String(32)),
        sa.Column("street", sa.String(255)),
        sa.Column("unit", sa.String(64)),
        sa.Column("city", sa.String(128)),
        sa.Column("district", sa.String(128)),
        sa.Column("region", sa.String(32)),
        sa.Column("postcode", sa.String(16)),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("county_fips", sa.String(5), sa.ForeignKey("regions.fips"), index=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True)),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.String(64), index=True),
        sa.UniqueConstraint("source_id", "hash", name="uq_addresses_source_hash"),
    )
    op.create_index("ix_addresses_geom", "addresses", ["geom"], postgresql_using="gist")

    op.create_table(
        "buildings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("hazard_sources.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("footprint_area_m2", sa.Float, nullable=False),
        sa.Column("release", sa.String(32)),
        sa.Column("capture_dates_range", sa.String(64)),
        sa.Column("county_fips", sa.String(5), index=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index("ix_buildings_geom", "buildings", ["geom"], postgresql_using="gist")

    op.create_table(
        "address_buildings",
        sa.Column("address_id", sa.Integer, sa.ForeignKey("addresses.id"), primary_key=True),
        sa.Column("building_id", sa.Integer, sa.ForeignKey("buildings.id"), index=True),
        sa.Column("match_method", sa.String(32), nullable=False),
        sa.Column("match_distance_m", sa.Float),
        sa.Column("buildings_within_100m", sa.Integer),
        sa.Column("uncertainty", sa.String(32), nullable=False),
    )

    op.create_table(
        "wildfire_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("hazard_sources.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("source_object_id", sa.Integer, nullable=False),
        sa.Column("year", sa.Integer, index=True),
        sa.Column("fire_name", sa.String(255)),
        sa.Column("agency", sa.String(16)),
        sa.Column("unit_id", sa.String(16)),
        sa.Column("inc_num", sa.String(32)),
        sa.Column("irwin_id", sa.String(64)),
        sa.Column("alarm_date", sa.Date),
        sa.Column("cont_date", sa.Date),
        sa.Column("cause", sa.Integer),
        sa.Column("gis_acres", sa.Float),
        sa.Column("objective", sa.Integer),
        sa.Column("complex_name", sa.String(255)),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.UniqueConstraint("source_id", "source_object_id", name="uq_wildfire_source_oid"),
    )
    op.create_index("ix_wildfire_events_geom", "wildfire_events", ["geom"], postgresql_using="gist")

    op.create_table(
        "hazard_geometries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("hazard_sources.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("hazard_id", sa.String(64), nullable=False, index=True),
        sa.Column("source_object_id", sa.String(64)),
        sa.Column("classification", sa.String(64), index=True),
        sa.Column("class_code", sa.Integer),
        sa.Column("attributes", sa.JSON),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_hazard_geometries_geom", "hazard_geometries", ["geom"], postgresql_using="gist"
    )
    op.create_index(
        "ix_hazard_geometries_hazard_class", "hazard_geometries", ["hazard_id", "classification"]
    )

    op.create_table(
        "hazard_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("hazard_sources.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("hazard_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(255)),
        sa.Column("attributes", sa.JSON),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="MULTILINESTRING", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index("ix_hazard_lines_geom", "hazard_lines", ["geom"], postgresql_using="gist")

    op.create_table(
        "earthquake_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("hazard_sources.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("magnitude", sa.Float, nullable=False, index=True),
        sa.Column("depth_km", sa.Float),
        sa.Column("place", sa.String(255)),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_earthquake_events_geom", "earthquake_events", ["geom"], postgresql_using="gist"
    )

    op.create_table(
        "regional_indices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("hazard_sources.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("county_fips", sa.String(5), nullable=False, index=True),
        sa.Column("metric", sa.String(64), nullable=False, index=True),
        sa.Column("value", sa.Float),
        sa.Column("rating", sa.String(64)),
        sa.Column("version", sa.String(64)),
        sa.UniqueConstraint("source_id", "county_fips", "metric", name="uq_regional_metric"),
    )

    op.create_table(
        "environmental_observations",
        sa.Column("address_id", sa.Integer, sa.ForeignKey("addresses.id"), primary_key=True),
        sa.Column("elevation_m", sa.Float),
        sa.Column("slope_deg", sa.Float),
        sa.Column("aspect_deg", sa.Float),
        sa.Column("land_cover_code", sa.Integer),
        sa.Column("land_cover_class", sa.String(64)),
        sa.Column("fuel_model", sa.String(64)),
        sa.Column("distance_to_fault_m", sa.Float),
        sa.Column("distance_to_watercourse_m", sa.Float),
        sa.Column("fires_within_5km_since_1990", sa.Integer),
        sa.Column("years_since_last_fire_within_1km", sa.Float),
        sa.Column("sources", sa.JSON),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "property_hazard_assessments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "address_id", sa.Integer, sa.ForeignKey("addresses.id"), nullable=False, index=True
        ),
        sa.Column("hazard_id", sa.String(64), nullable=False, index=True),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("hazard_sources.id"), nullable=False),
        sa.Column("source_version", sa.String(128)),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classification", sa.String(64)),
        sa.Column("value", sa.Float),
        sa.Column("units", sa.String(32)),
        sa.Column("probability_horizon", sa.String(64)),
        sa.Column("coverage_status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.String(16)),
        sa.Column("method", sa.String(32)),
        sa.Column("details", sa.JSON),
        sa.Column("caveats", sa.JSON),
        sa.UniqueConstraint("address_id", "hazard_id", name="uq_assessment_addr_hazard"),
    )

    op.create_table(
        "ml_features",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "address_id", sa.Integer, sa.ForeignKey("addresses.id"), nullable=False, index=True
        ),
        sa.Column("feature_year", sa.Integer, nullable=False, index=True),
        sa.Column("target_year", sa.Integer, nullable=False, index=True),
        sa.Column("label", sa.Integer),
        sa.Column("label_quality", sa.String(32)),
        sa.Column("features", sa.JSON, nullable=False),
        sa.Column("spatial_block", sa.String(32), index=True),
        sa.Column("dataset_version", sa.String(64), nullable=False, index=True),
        sa.UniqueConstraint(
            "address_id", "feature_year", "dataset_version", name="uq_mlfeat_addr_year"
        ),
    )

    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, index=True),
        sa.Column("version", sa.String(64), nullable=False, unique=True),
        sa.Column("algorithm", sa.String(64), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("target_definition", sa.Text, nullable=False),
        sa.Column("train_period", sa.String(64), nullable=False),
        sa.Column("validation_period", sa.String(64), nullable=False),
        sa.Column("test_period", sa.String(64), nullable=False),
        sa.Column("spatial_holdout", sa.String(128)),
        sa.Column("artifact_path", sa.String(512)),
        sa.Column("feature_list", sa.JSON, nullable=False),
        sa.Column("metrics", sa.JSON, nullable=False),
        sa.Column("is_selected", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "address_id", sa.Integer, sa.ForeignKey("addresses.id"), nullable=False, index=True
        ),
        sa.Column(
            "model_version_id",
            sa.Integer,
            sa.ForeignKey("model_versions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("prediction_year", sa.Integer, nullable=False),
        sa.Column("probability", sa.Float, nullable=False),
        sa.Column("base_value", sa.Float),
        sa.Column("features", sa.JSON),
        sa.Column("shap_values", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("address_id", "model_version_id", name="uq_prediction_addr_model"),
    )

    op.create_table(
        "portfolios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("n_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_valid", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_invalid", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_duplicates", sa.Integer, nullable=False, server_default="0"),
        sa.Column("validation_errors", sa.JSON),
        sa.Column("analysis", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
    )

    op.create_table(
        "portfolio_properties",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "portfolio_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolios.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("row_number", sa.Integer, nullable=False),
        sa.Column("property_id", sa.String(128), nullable=False, index=True),
        sa.Column("policy_id", sa.String(128)),
        sa.Column("address_text", sa.String(512)),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("property_value", sa.Float),
        sa.Column("insured_value", sa.Float),
        sa.Column("county_fips", sa.String(5), index=True),
        sa.Column("is_duplicate_of", sa.Integer),
        sa.Column("hazards", sa.JSON),
        sa.Column("composite_score", sa.Float),
        sa.Column("financial", sa.JSON),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_portfolio_properties_geom", "portfolio_properties", ["geom"], postgresql_using="gist"
    )

    op.create_table(
        "financial_scenario_assumptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scenario_id", sa.String(64), nullable=False, index=True),
        sa.Column("hazard_id", sa.String(64), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("damage_ratio", sa.Float, nullable=False),
        sa.Column("basis", sa.Text, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("progress", sa.Float, nullable=False, server_default="0"),
        sa.Column("params", sa.JSON),
        sa.Column("result", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "data_quality_reports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("stage", sa.String(64), nullable=False, index=True),
        sa.Column("check_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("n_checked", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("detail", sa.JSON),
    )


def downgrade() -> None:
    for table in (
        "data_quality_reports",
        "processing_jobs",
        "financial_scenario_assumptions",
        "portfolio_properties",
        "portfolios",
        "predictions",
        "model_versions",
        "ml_features",
        "property_hazard_assessments",
        "environmental_observations",
        "regional_indices",
        "earthquake_events",
        "hazard_lines",
        "hazard_geometries",
        "wildfire_events",
        "address_buildings",
        "buildings",
        "addresses",
        "hazard_sources",
        "regions",
    ):
        op.drop_table(table)
