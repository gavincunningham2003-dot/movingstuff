"""Inference for ingested addresses (persisted) and ad-hoc points (portfolio uploads,
map clicks), with a structured account of how complete and how fresh the inputs were.

What a prediction is: the calibrated probability that the location lies inside a mapped
CAL FIRE perimeter in the next calendar year (or within 1 km of one, for the proximity
target). What it is not: a probability of building damage, of an insurance claim, an
expected loss or a premium. The API and the dashboard keep that distinction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.models import ModelVersion, Prediction
from catrix.ingest.store import chunked
from catrix.logging import get_logger
from catrix.ml.artifacts import (
    ArtifactIncompatibleError,
    hard_problems,
    resolve_artifact,
    validate_document,
)
from catrix.ml.dataset import NEIGHBOUR_RADIUS_M, feature_kind, static_features
from catrix.ml.explain import shap_for_rows

log = get_logger(__name__)

CAVEATS = [
    "Probability of the location falling inside a mapped CAL FIRE perimeter in the next calendar year, calibrated on validation years.",
    "Not a probability of building damage or insured loss; perimeter mapping is incomplete for small fires.",
    "Features are historical/static; present-day FHSZ zoning is not used by the model.",
]

EXPLANATION = (
    "This is a screening probability of mapped wildfire-perimeter exposure for the next "
    "calendar year, produced by a model trained on historical perimeters. It is not a "
    "probability of structural damage, an insurance claim, an expected loss or a premium. "
    "Feature completeness and data freshness say how much of the model's input was available "
    "for this location; they are not a statistical confidence interval."
)

FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "terrain": ("elevation_m", "slope_deg", "northness", "eastness"),
    "building_density": ("buildings_within_100m",),
    "climate": (
        "precip_pct_normal",
        "precip_prev_year_pct_normal",
        "precip_3y_pct_normal",
        "tmax_anomaly_c",
    ),
    "drought": ("drought_d2_share", "drought_d3_max"),
    "fuel": ("fbfm40", "fuel_group", "fuel_burnable", "canopy_cover_pct", "burnable_frac_500m"),
    "land_cover": ("lc_developed_intensity", "lc_veg_frac_500m", "lc_developed_frac_500m"),
}


def load_model(version: str | None = None) -> tuple[Any, dict[str, Any]]:
    """Load a training run (the live one by default) after validating its artefact
    document. Raises :class:`ArtifactIncompatibleError` with a readable reason instead of
    returning a model that cannot be trusted."""
    settings = get_settings()
    if version is None:
        latest = settings.models_dir / "LATEST"
        if not latest.exists():
            raise ArtifactIncompatibleError("no live model: data/models/LATEST is missing")
        version = latest.read_text(encoding="utf-8").strip()
    metrics_path: Path = settings.models_dir / version / "metrics.json"
    if not metrics_path.exists():
        raise ArtifactIncompatibleError(f"run {version!r} has no metrics.json")
    doc = json.loads(metrics_path.read_text(encoding="utf-8"))
    doc.setdefault("version", version)
    problems = validate_document(doc)
    for p in problems:
        if p.startswith("warning:"):
            log.warning("artifact_warning", version=version, detail=p)
    hard = hard_problems(problems)
    if hard:
        raise ArtifactIncompatibleError(f"run {version!r} cannot be used: " + "; ".join(hard))
    sel = next(m for m in doc["models"] if m.get("is_selected"))
    path = resolve_artifact(doc, sel)
    if not path.exists():
        raise ArtifactIncompatibleError(f"artifact {path} for run {version!r} is missing")
    tm = joblib.load(path)
    # Inference runs on CPU regardless of the training device (a CUDA booster used from
    # the API's worker threads crashed the process; request-sized inputs gain nothing).
    est = getattr(tm, "estimator", None)
    if est is not None and hasattr(est, "set_params"):
        try:
            if "device" in est.get_params():
                est.set_params(device="cpu")
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("model_device_reset_failed", error=repr(exc))
    return tm, doc


@lru_cache(maxsize=1)
def load_selected_model() -> tuple[Any, dict[str, Any]] | None:
    """The live model, or None (with a logged reason) when none can be loaded."""
    try:
        return load_model(None)
    except ArtifactIncompatibleError as exc:
        log.warning("live_model_unavailable", reason=str(exc))
        return None


def reset_model_cache() -> None:
    load_selected_model.cache_clear()


def current_year_features(
    session: Session, points: pd.DataFrame, feature_year: int
) -> pd.DataFrame:
    """Build the feature vector for arbitrary points as of the end of ``feature_year``:
    the same definitions as the training table (DuckDB SQL mirrored in Python)."""
    rows = list(
        zip(points.index, points["lon"].astype(float), points["lat"].astype(float), strict=True)
    )
    nb: list[dict[str, Any]] = []
    for chunk in chunked(rows, 500):
        values = ", ".join(f"({i}, {lon!r}, {lat!r})" for i, lon, lat in chunk)
        sql = text(
            f"""
            WITH pts(i, lon, lat) AS (VALUES {values}),
            p AS (SELECT i, ST_SetSRID(ST_MakePoint(lon, lat), 4326) AS g FROM pts)
            SELECT p.i AS i, w.year, w.gis_acres,
                   ST_Distance(w.geom::geography, p.g::geography) AS distance_m,
                   ST_Intersects(w.geom, p.g) AS inside
            FROM p JOIN wildfire_events w
              ON w.year IS NOT NULL AND w.year <= :fy AND ST_DWithin(w.geom::geography, p.g::geography, :r)
            """
        )
        nb.extend(
            dict(r)
            for r in session.execute(sql, {"fy": feature_year, "r": NEIGHBOUR_RADIUS_M}).mappings()
        )
    nbdf = pd.DataFrame(nb, columns=["i", "year", "gis_acres", "distance_m", "inside"])
    T = feature_year
    feats = []
    for i in points.index:
        s = nbdf[nbdf["i"] == i]
        last10 = s[s["year"] > T - 10]
        within1 = s[s["distance_m"] <= 1000]
        feats.append(
            {
                "n_perim_1km_10y": int((last10["distance_m"] <= 1000).sum()),
                "n_perim_5km_10y": int((last10["distance_m"] <= 5000).sum()),
                "n_perim_10km_10y": int(len(last10)),
                "n_perim_5km_30y": int(((s["year"] > T - 30) & (s["distance_m"] <= 5000)).sum()),
                "years_since_perim_1km": float(min(T - within1["year"].max(), 60))
                if len(within1)
                else 60.0,
                "ever_inside_perimeter": int(s["inside"].astype(bool).any()),
                "acres_5km_5y": float(
                    s.loc[(s["year"] > T - 5) & (s["distance_m"] <= 5000), "gis_acres"]
                    .fillna(0)
                    .sum()
                ),
                "dist_nearest_perim_m": float(s["distance_m"].min())
                if len(s)
                else float(NEIGHBOUR_RADIUS_M),
            }
        )
    out = pd.DataFrame(feats, index=points.index)
    for c in ("elevation_m", "slope_deg", "northness", "eastness", "buildings_within_100m"):
        out[c] = points[c].values if c in points.columns else np.nan
    from catrix.ml.optional_features import optional_features_for_points

    opt = optional_features_for_points(session, points, feature_year)
    for c in opt.columns:
        out[c] = opt[c].values
    return out


def prediction_quality(
    features: dict[str, Any],
    schema_features: list[str],
    feature_year: int,
    shap: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured data-quality assessment of one prediction's inputs.

    ``feature_completeness`` is the share of the model's features that had a value; it
    describes input availability, not statistical confidence. Freshness reports the
    edition years actually used; warnings name the groups that were missing.
    """
    missing = [f for f in schema_features if features.get(f) is None]
    present = len(schema_features) - len(missing)
    completeness = present / len(schema_features) if schema_features else None
    groups_missing = {
        g: [f for f in names if f in schema_features and features.get(f) is None]
        for g, names in FEATURE_GROUPS.items()
    }
    warnings: list[str] = []
    labels = {
        "terrain": "terrain (elevation/slope/aspect) missing: no DEM tile covered this point",
        "building_density": "building density missing: no footprint count for this point",
        "climate": "climate anomalies missing: no PRISM grid value for this point/year",
        "drought": "drought statistics missing: county not covered by the Drought Monitor table",
        "fuel": "fuel features missing: no LANDFIRE edition at or before the feature year",
        "land_cover": "land cover missing: no NLCD epoch at or before the feature year",
    }
    for g, miss in groups_missing.items():
        if miss and len(miss) == len([f for f in FEATURE_GROUPS[g] if f in schema_features]):
            warnings.append(labels[g])
    lag_lc = features.get("lc_version_lag_years")
    lag_fuel = features.get("fuel_version_lag_years")
    freshness = {
        "feature_year": feature_year,
        "climate_year": feature_year if features.get("precip_pct_normal") is not None else None,
        "land_cover_epoch": (feature_year - int(lag_lc)) if lag_lc is not None else None,
        "fuel_edition_year": (feature_year - int(lag_fuel)) if lag_fuel is not None else None,
        "fire_history_through": feature_year,
    }
    if (
        freshness["land_cover_epoch"] is not None
        and feature_year - freshness["land_cover_epoch"] > 5
    ):
        warnings.append(
            f"land cover is from {freshness['land_cover_epoch']}, {feature_year - freshness['land_cover_epoch']} years before the feature year"
        )
    quality = (
        None
        if completeness is None
        else "complete"
        if completeness >= 0.95
        else "partial"
        if completeness >= 0.7
        else "sparse"
    )
    top = sorted(
        (c for c in (shap or []) if c.get("contribution") is not None),
        key=lambda c: -abs(float(c["contribution"])),
    )[:5]
    return {
        "feature_completeness": completeness,
        "missing_features": missing,
        "data_freshness": freshness,
        "coverage_warnings": warnings,
        "quality": quality,
        "top_factors": [
            {
                "feature": c.get("feature"),
                "contribution": float(c["contribution"]),
                "value": c.get("value"),
            }
            for c in top
        ],
        "explanation": EXPLANATION,
    }


def predict_for_points(
    session: Session,
    points: pd.DataFrame,
    *,
    feature_year: int | None = None,
    with_shap: bool = False,
) -> list[dict[str, Any] | None]:
    loaded = load_selected_model()
    if loaded is None:
        return [None] * len(points)
    tm, doc = loaded
    fy = feature_year or (datetime.now(UTC).year - 1)
    feats = doc["feature_list"]
    from catrix.ml.point_features import static_features_for_points

    points = static_features_for_points(session, points)
    X = current_year_features(session, points, fy)
    absent = [f for f in feats if f not in X.columns]
    mandatory_absent = [f for f in absent if feature_kind(f) == "mandatory"]
    if mandatory_absent:
        raise ArtifactIncompatibleError(
            f"inference features do not match the saved model schema: missing {mandatory_absent}"
        )
    for f in absent:
        X[f] = np.nan
    # Keep the lag columns in the quality report even when the model excludes them.
    extra = [c for c in ("lc_version_lag_years", "fuel_version_lag_years") if c in X.columns]
    p = tm.predict_proba(X[feats])
    shap_rows: list[list[dict[str, Any]]] = [[] for _ in range(len(X))]
    base = None
    if with_shap:
        try:
            shap_rows, base = shap_for_rows(tm, X, feats)
        except Exception as exc:
            log.warning("shap_rows_failed", error=repr(exc))
    out: list[dict[str, Any] | None] = []
    for i in range(len(X)):
        values = {
            f: (None if pd.isna(X[f].iloc[i]) else float(X[f].iloc[i])) for f in feats + extra
        }
        q = prediction_quality(values, feats, fy, shap_rows[i])
        out.append(
            {
                "model_version": f"{doc['version']}:{tm.name}",
                "target_definition": doc["target_definition"],
                "probability": float(p[i]),
                "prediction_year": fy + 1,
                "threshold": float(tm.threshold),
                "predicted_class": bool(p[i] >= tm.threshold),
                "base_value": base,
                "features": values,
                "shap": shap_rows[i],
                "caveats": CAVEATS,
                **q,
            }
        )
    return out


def predict_all_addresses(session: Session, *, feature_year: int | None = None) -> int:
    loaded = load_selected_model()
    if loaded is None:
        log.warning("no_selected_model")
        return 0
    tm, doc = loaded
    mv = session.execute(
        select(ModelVersion).where(ModelVersion.version == f"{doc['version']}:{tm.name}")
    ).scalar_one_or_none()
    if mv is None:
        log.warning("model_version_row_missing", version=doc["version"])
        return 0
    st = static_features(session).set_index("address_id")
    session.execute(delete(Prediction).where(Prediction.model_version_id == mv.id))
    fy = feature_year or (datetime.now(UTC).year - 1)
    n = 0
    for start in range(0, len(st), 1000):
        chunk = st.iloc[start : start + 1000]
        # address_id travels as a column: the index is never treated as an id.
        chunk = chunk.assign(address_id=chunk.index.to_numpy())
        preds = predict_for_points(session, chunk, feature_year=fy, with_shap=True)
        session.bulk_insert_mappings(
            Prediction,
            [
                {
                    "address_id": int(aid),
                    "model_version_id": mv.id,
                    "prediction_year": fy + 1,
                    "probability": pr["probability"],
                    "base_value": pr["base_value"],
                    "features": pr["features"],
                    "shap_values": {"contributions": pr["shap"][:12]},
                }
                for aid, pr in zip(chunk.index, preds, strict=True)
                if pr is not None
            ],
        )
        session.commit()
        n += len(chunk)
        log.info("predictions_batch", done=n, total=len(st))
    return n
