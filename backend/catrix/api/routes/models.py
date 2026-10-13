from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from catrix.api.deps import DB
from catrix.api.schemas import ModelMetricsOut, ModelsMetricsResponse
from catrix.config import get_settings
from catrix.db.models import ModelVersion

router = APIRouter(prefix="/models", tags=["models"])

LIMITATIONS = [
    "Target = mapped CAL FIRE perimeter exposure in year T+1, not structure damage or insured loss.",
    "Perimeter mapping is incomplete for small fires and for years before ~1990; negatives mean 'no mapped perimeter'.",
    "Building density comes from 2014-2020 footprint imagery and is used for all years (mild anachronism, documented).",
    "No fuel/vegetation or climate anomaly features are included unless the optional LANDFIRE/NLCD/NOAA/USDM connectors have run.",
    "Spatial autocorrelation: rows for nearby addresses are not independent; the spatial-block holdout quantifies the effect.",
    "Calibration is isotonic on validation years; distribution shift (e.g. 2017-2021 extreme seasons) can degrade calibration on later years.",
]


def _latest_metrics_doc() -> dict[str, Any] | None:
    md = get_settings().models_dir
    latest = md / "LATEST"
    if not latest.exists():
        return None
    p: Path = md / latest.read_text(encoding="utf-8").strip() / "metrics.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@router.get("/metrics", response_model=ModelsMetricsResponse)
def model_metrics(db: DB) -> ModelsMetricsResponse:
    doc = _latest_metrics_doc()
    if doc is None:
        return ModelsMetricsResponse(
            models=[],
            selected_model=None,
            feature_list=[],
            limitations=LIMITATIONS,
            status="not_trained",
            status_detail="No training run found under data/models. Run `catrix ml train`.",
        )
    if doc.get("status") != "available":
        return ModelsMetricsResponse(
            models=[],
            selected_model=None,
            feature_list=doc.get("feature_list", []),
            limitations=LIMITATIONS,
            status="insufficient_data",
            status_detail=doc.get("status_detail"),
        )
    trained_at = datetime.fromisoformat(doc["trained_at"])
    models = []
    for m in doc["models"]:
        t = m["test"]
        models.append(
            ModelMetricsOut(
                name=m["name"],
                version=f"{doc['version']}:{m['name']}",
                algorithm=m["algorithm"],
                trained_at=trained_at,
                target_definition=doc["target_definition"],
                train_period=doc["train_period"],
                validation_period=doc["validation_period"],
                test_period=doc["test_period"],
                spatial_holdout=doc.get("spatial_holdout"),
                n_train=doc["n_train"],
                n_test=doc["n_test"],
                prevalence_train=doc["prevalence_train"],
                prevalence_test=doc["prevalence_test"],
                metrics={
                    **t["metrics"],
                    **(
                        {f"spatial_test_{k}": v for k, v in m["spatial_test"]["metrics"].items()}
                        if m.get("spatial_test")
                        else {}
                    ),
                },
                threshold=m["threshold"],
                confusion=t["confusion"],
                roc_curve=t["roc_curve"],
                pr_curve=t["pr_curve"],
                calibration=t["calibration"],
                feature_importance=m.get("feature_importance", []),
                shap_summary=m.get("shap_summary", []),
                geographic_performance=m.get("geographic_performance", []),
                is_selected=bool(m.get("is_selected")),
            )
        )
    _ = db.execute(
        select(ModelVersion.id).limit(1)
    ).first()  # touch DB so a missing DB surfaces as 503 consistently
    return ModelsMetricsResponse(
        models=models,
        selected_model=doc.get("selected_model"),
        feature_list=doc["feature_list"],
        limitations=[*LIMITATIONS, *([doc["status_detail"]] if doc.get("status_detail") else [])],
        status="available",
    )
