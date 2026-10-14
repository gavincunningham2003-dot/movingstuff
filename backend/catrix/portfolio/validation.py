"""Portfolio CSV parsing and validation."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from catrix.geo.validation import check_coordinates, dedup_key

COLUMN_ALIASES: dict[str, list[str]] = {
    "property_id": ["property_id", "propertyid", "id", "location_id", "loc_id", "ref"],
    "latitude": ["latitude", "lat", "y"],
    "longitude": ["longitude", "lon", "lng", "long", "x"],
    "address": ["address", "street_address", "addr"],
    "property_value": ["property_value", "value", "building_value", "replacement_cost", "rcv"],
    "insured_value": ["insured_value", "tiv", "total_insured_value", "sum_insured", "limit"],
    "policy_id": ["policy_id", "policy", "portfolio_id", "account_id"],
    "deductible": ["deductible", "ded"],
}
REQUIRED = ["property_id", "latitude", "longitude"]
MAX_ROWS = 100_000


@dataclass
class ValidationReport:
    n_rows: int
    n_valid: int
    n_invalid: int
    n_duplicates: int
    column_map: dict[str, str]
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def map_columns(columns: list[str]) -> dict[str, str]:
    lower = {c.lower().strip(): c for c in columns}
    out: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in lower:
                out[canonical] = lower[a]
                break
    return out


def parse_portfolio_csv(content: bytes | str) -> tuple[pd.DataFrame, ValidationReport]:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    try:
        raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except Exception as exc:
        raise ValueError(f"could not parse CSV: {exc}") from exc
    if len(raw) == 0:
        raise ValueError("CSV has no data rows")
    if len(raw) > MAX_ROWS:
        raise ValueError(f"CSV has {len(raw)} rows; maximum is {MAX_ROWS}")
    cmap = map_columns(list(raw.columns))
    missing = [c for c in REQUIRED if c not in cmap]
    if missing:
        raise ValueError(
            f"missing required columns: {missing} (accepted names: {', '.join(COLUMN_ALIASES[missing[0]])})"
        )

    df = pd.DataFrame(
        {
            "row_number": range(1, len(raw) + 1),
            "property_id": raw[cmap["property_id"]].str.strip(),
            "lat": pd.to_numeric(raw[cmap["latitude"]], errors="coerce"),
            "lon": pd.to_numeric(raw[cmap["longitude"]], errors="coerce"),
            "address_text": raw[cmap["address"]].str.strip() if "address" in cmap else None,
            "property_value": pd.to_numeric(
                raw[cmap["property_value"]].str.replace(r"[,$]", "", regex=True), errors="coerce"
            )
            if "property_value" in cmap
            else None,
            "insured_value": pd.to_numeric(
                raw[cmap["insured_value"]].str.replace(r"[,$]", "", regex=True), errors="coerce"
            )
            if "insured_value" in cmap
            else None,
            "policy_id": raw[cmap["policy_id"]].str.strip() if "policy_id" in cmap else None,
            "deductible": pd.to_numeric(
                raw[cmap["deductible"]].str.replace(r"[,$]", "", regex=True), errors="coerce"
            )
            if "deductible" in cmap
            else None,
        }
    )
    errors: list[dict[str, Any]] = []
    empty_id = df["property_id"].fillna("") == ""
    for rn in df.loc[empty_id, "row_number"]:
        errors.append({"row": int(rn), "error": "missing property_id"})
    check = check_coordinates(df, require_california=True)
    for rn, la, lo in df.loc[~check.valid_mask, ["row_number", "lat", "lon"]].itertuples(
        index=False
    ):
        if pd.isna(la) or pd.isna(lo):
            reason = "missing or non-numeric coordinates"
        elif abs(la) > 90 or abs(lo) > 180:
            reason = "impossible coordinate values"
        elif la == 0 and lo == 0:
            reason = "0,0 coordinates"
        else:
            reason = "outside California bounding box"
        errors.append({"row": int(rn), "error": reason})
    bad_financial = np.zeros(len(df), dtype=bool)
    for col in ("property_value", "insured_value", "deductible"):
        if df[col] is not None and df[col].notna().any():
            bad = (df[col].notna() & (df[col] < 0)).to_numpy()
            for rn in df.loc[bad, "row_number"]:
                errors.append({"row": int(rn), "error": f"negative {col}"})
            bad_financial |= bad
    # A row with an impossible financial value is invalid, not merely annotated.
    valid = check.valid_mask & ~empty_id.to_numpy() & ~bad_financial
    dup_ids = df["property_id"].duplicated(keep="first") & valid
    df["dedup_key"] = [
        dedup_key(la, lo, precision_m=5.0) if ok else None
        for la, lo, ok in zip(df["lat"], df["lon"], valid, strict=True)
    ]
    dup_loc = df["dedup_key"].duplicated(keep="first") & df["dedup_key"].notna()
    df["is_duplicate_of"] = None
    first_by_key = df[df["dedup_key"].notna()].groupby("dedup_key")["row_number"].first()
    df.loc[dup_loc, "is_duplicate_of"] = df.loc[dup_loc, "dedup_key"].map(first_by_key)
    warnings: list[str] = []
    if dup_ids.any():
        warnings.append(
            f"{int(dup_ids.sum())} rows repeat a property_id already seen (kept; flagged)."
        )
    if dup_loc.any():
        warnings.append(
            f"{int(dup_loc.sum())} rows share a location (within ~5 m) with an earlier row (kept; flagged as duplicates in analysis)."
        )
    if "insured_value" not in cmap and "property_value" not in cmap:
        warnings.append("No value columns supplied: financial scenarios will not be computed.")
    df["valid"] = valid
    report = ValidationReport(
        n_rows=len(df),
        n_valid=int(valid.sum()),
        n_invalid=int((~valid).sum()),
        n_duplicates=int(dup_loc.sum()),
        column_map=cmap,
        errors=errors[:500],
        warnings=warnings,
    )
    return df, report
