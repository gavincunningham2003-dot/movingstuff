"""Portable, self-describing model artefacts.

A training run writes ``data/models/<version>/`` containing one ``<model>.joblib`` per
candidate and a ``metrics.json`` that names them by **file name only**. Everything needed to
check that an artefact can be used is stored next to it: the feature schema, the
preprocessing version, the training configuration, the random seed, the dependency
versions, the dataset version and a reproducible experiment id. The directory can be
moved between Windows, Linux and Docker; paths are resolved against the configured
models directory at load time, and an incompatible artefact raises instead of silently
predicting.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from catrix.config import get_settings

# Bump when the feature definitions or their preprocessing change incompatibly.
PREPROCESSING_VERSION = "features-v2"

DEPENDENCIES = ("numpy", "pandas", "scikit-learn", "xgboost", "shap", "joblib")


class ArtifactIncompatibleError(RuntimeError):
    """The stored model cannot be used with this code or environment."""


def dependency_versions() -> dict[str, str]:
    out = {"python": platform.python_version()}
    for name in DEPENDENCIES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = "missing"
    try:
        out["catrix"] = metadata.version("catrix")
    except metadata.PackageNotFoundError:
        out["catrix"] = "dev"
    return out


def experiment_id(config: dict[str, Any]) -> str:
    """Deterministic id of a training configuration (dataset, target, features, seed,
    CV and calibration settings): identical inputs give identical ids."""
    payload = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    kind: str  # mandatory | optional | experimental
    dtype: str
    coverage: dict[str, float]  # split -> share of non-null rows


def models_dir() -> Path:
    return get_settings().models_dir


def resolve_artifact(doc: dict[str, Any], model_doc: dict[str, Any]) -> Path:
    """Path of a model's joblib file. New documents carry ``artifact_file`` (relative);
    older ones carry an absolute ``artifact_path`` that is honoured only if it exists,
    otherwise its file name is looked up under the run directory."""
    run_dir = models_dir() / str(doc["version"])
    if model_doc.get("artifact_file"):
        return run_dir / str(model_doc["artifact_file"])
    legacy = model_doc.get("artifact_path")
    if legacy:
        p = Path(str(legacy))
        if p.exists():
            return p
        return run_dir / p.name
    raise ArtifactIncompatibleError(f"model {model_doc.get('name')} has no artifact reference")


def _major(v: str) -> str:
    return v.split(".")[0] if v and v != "missing" else v


def validate_document(doc: dict[str, Any]) -> list[str]:
    """Return human-readable incompatibilities (empty when the run can be used).

    Hard failures: wrong preprocessing version, missing feature schema or selected
    model, a different scikit-learn or XGBoost *major* version (pickles are not stable
    across majors). Minor-version differences are returned as warnings prefixed
    ``warning:`` and do not block loading.
    """
    problems: list[str] = []
    if doc.get("status") != "available":
        problems.append(f"run status is {doc.get('status')!r}, not 'available'")
    pv = doc.get("preprocessing_version")
    if pv != PREPROCESSING_VERSION:
        problems.append(
            f"preprocessing version {pv!r} does not match this code ({PREPROCESSING_VERSION!r})"
        )
    if not doc.get("feature_schema") and not doc.get("feature_list"):
        problems.append("no feature schema recorded")
    if not any(m.get("is_selected") for m in doc.get("models", [])):
        problems.append("no selected model in the run")
    stored = doc.get("dependency_versions") or {}
    current = dependency_versions()
    for dep in ("scikit-learn", "xgboost"):
        s, c = stored.get(dep), current.get(dep)
        if s and c and s != "missing" and c != "missing":
            if _major(s) != _major(c):
                problems.append(f"{dep} major version differs: artefact {s}, environment {c}")
            elif s != c:
                problems.append(f"warning: {dep} {s} (artefact) vs {c} (environment)")
    return problems


def hard_problems(problems: list[str]) -> list[str]:
    return [p for p in problems if not p.startswith("warning:")]
