"""Inference for ingested addresses (persisted) and ad-hoc points (portfolio uploads)."""

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
from catrix.ml.dataset import NEIGHBOUR_RADIUS_M, static_features
from catrix.ml.explain import shap_for_rows

log = get_logger(__name__)

CAVEATS = [
    "Probability of the location falling inside a mapped CAL FIRE perimeter in the next calendar year, calibrated on validation years.",
    "Not a probability of building damage or insured loss; perimeter mapping is incomplete for small fires.",
    "Features are historical/static; present-day FHSZ zoning is not used by the model.",
]


@lru_cache(maxsize=1)
def load_selected_model() -> tuple[Any, dict[str, Any]] | None:
    settings = get_settings()
    latest = settings.models_dir / "LATEST"
    if not latest.exists():
        return None
    version = latest.read_text(encoding="utf-8").strip()
    metrics_path: Path = settings.models_dir / version / "metrics.json"
    if not metrics_path.exists():
        return None
    doc = json.loads(metrics_path.read_text(encoding="utf-8"))
    if doc.get("status") != "available":
        return None
    sel = next((m for m in doc["models"] if m.get("is_selected")), None)
    if sel is None or not Path(sel["artifact_path"]).exists():
        return None
    tm = joblib.load(sel["artifact_path"])
    # Inference runs on CPU regardless of the training device: request-sized inputs gain
    # nothing from CUDA, and a CUDA booster used from the API's worker threads brought the
    # uvicorn process down with a native fail-fast (0xC0000409) on the second request.
    est = getattr(tm, "estimator", None)
    if est is not None and hasattr(est, "set_params"):
        try:
            if "device" in est.get_params():
                est.set_params(device="cpu")
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("model_device_reset_failed", error=repr(exc))
    return tm, doc


def reset_model_cache() -> None:
    load_selected_model.cache_clear()


def current_year_features(
    session: Session, points: pd.DataFrame, feature_year: int
) -> pd.DataFrame:
    """Build the feature vector for arbitrary points as of the end of ``feature_year`` —
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
    # Ad-hoc points (portfolio uploads, map clicks) carry no precomputed terrain or
    # building-density columns; derive them the same way the training table did.
    from catrix.ml.point_features import static_features_for_points

    points = static_features_for_points(session, points)
    X = current_year_features(session, points, fy)
    for f in feats:
        if f not in X.columns:
            X[f] = np.nan
    p = tm.predict_proba(X[feats])
    shap_rows: list[list[dict[str, Any]]] = [[] for _ in range(len(X))]
    base = None
    if with_shap:
        try:
            shap_rows, base = shap_for_rows(tm, X, feats)
        except Exception as exc:
            log.warning("shap_rows_failed", error=repr(exc))
    return [
        {
            "model_version": f"{doc['version']}:{tm.name}",
            "target_definition": doc["target_definition"],
            "probability": float(p[i]),
            "prediction_year": fy + 1,
            "threshold": float(tm.threshold),
            "predicted_class": bool(p[i] >= tm.threshold),
            "base_value": base,
            "features": {
                f: (None if pd.isna(X[f].iloc[i]) else float(X[f].iloc[i])) for f in feats
            },
            "shap": shap_rows[i],
            "caveats": CAVEATS,
        }
        for i in range(len(X))
    ]


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
