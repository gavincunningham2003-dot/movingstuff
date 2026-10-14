"""Portfolio CSV parsing and validation.

The CSV contract (one source of truth, also served at ``GET /portfolio/csv-contract``):

* ``latitude`` / ``longitude`` are the only required columns.
* ``property_id`` is optional: a stable ``row-<n>`` id is generated when the column is
  absent or a cell is empty.
* Monetary columns are distinguished rather than aliased onto each other:
  ``property_value`` (building replacement value), ``contents_value``, ``insured_value``
  (sum insured / TIV), ``limit`` (policy limit) and ``deductible``.
* An **empty** amount means "not supplied" and is never an error. A **malformed** amount
  (text that is not a number after stripping ``$``, ``,``, ``USD``) is a *field-level*
  warning: the row is kept for hazard assessment, the value is treated as missing and the
  problem is reported with its row, field and the offending text. A **negative** amount
  makes the row invalid.
* Duplicates are classified, never silently dropped:

  - ``exact`` — same ``property_id`` *and* same location (within ~5 m) as an earlier row:
    a re-submitted exposure. Excluded from counts and monetary totals.
  - ``shared_location`` — same location as an earlier row but a different ``property_id``
    (several policies, units or structures at one site). Kept, flagged, and surfaced as a
    location accumulation.
  - ``repeated_id`` — same ``property_id`` as an earlier row at a different location.
    Kept and flagged.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from catrix.geo.validation import check_coordinates, dedup_key

LOCATION_PRECISION_M = 5.0
MAX_ROWS = 100_000

# Canonical column -> (required, description, accepted header aliases). The frontend reads
# this table from the API so that the upload preview and the parser never disagree.
CSV_CONTRACT: list[dict[str, Any]] = [
    {
        "name": "property_id",
        "required": False,
        "description": "Your identifier for the exposure; generated as row-<n> when missing.",
        "aliases": ["property_id", "propertyid", "prop_id", "id", "location_id", "loc_id", "ref"],
    },
    {
        "name": "latitude",
        "required": True,
        "description": "Decimal degrees, WGS84.",
        "aliases": ["latitude", "lat", "y"],
    },
    {
        "name": "longitude",
        "required": True,
        "description": "Decimal degrees, WGS84.",
        "aliases": ["longitude", "lon", "lng", "long", "x"],
    },
    {
        "name": "address",
        "required": False,
        "description": "Street address, used for display only.",
        "aliases": ["address", "street_address", "street", "addr"],
    },
    {
        "name": "property_value",
        "required": False,
        "description": "Building replacement value, USD (basis of gross physical loss).",
        "aliases": [
            "property_value",
            "building_value",
            "replacement_value",
            "replacement_cost",
            "rcv",
            "value",
        ],
    },
    {
        "name": "contents_value",
        "required": False,
        "description": "Contents replacement value, USD (added to the building value).",
        "aliases": ["contents_value", "contents", "contents_insured_value"],
    },
    {
        "name": "insured_value",
        "required": False,
        "description": "Sum insured / total insured value, USD (caps net insured loss when no limit is given).",
        "aliases": ["insured_value", "tiv", "total_insured_value", "sum_insured", "insured", "si"],
    },
    {
        "name": "limit",
        "required": False,
        "description": "Policy limit, USD (defaults to the sum insured).",
        "aliases": ["limit", "policy_limit", "coverage_limit"],
    },
    {
        "name": "deductible",
        "required": False,
        "description": "Policy deductible, USD.",
        "aliases": ["deductible", "ded", "deductible_amount"],
    },
    {
        "name": "policy_id",
        "required": False,
        "description": "Policy reference.",
        "aliases": ["policy_id", "policy", "policy_number", "policy_no", "account_id"],
    },
]
COLUMN_ALIASES: dict[str, list[str]] = {c["name"]: list(c["aliases"]) for c in CSV_CONTRACT}
REQUIRED = [c["name"] for c in CSV_CONTRACT if c["required"]]
AMOUNT_COLUMNS = ("property_value", "contents_value", "insured_value", "limit", "deductible")


@dataclass
class ValidationReport:
    n_rows: int
    n_valid: int
    n_invalid: int
    n_duplicates: int  # exact duplicates (excluded from totals)
    column_map: dict[str, str]
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_shared_location: int = 0  # kept, flagged
    n_repeated_id: int = 0  # kept, flagged
    n_locations: int = 0  # distinct valid locations
    n_generated_ids: int = 0


def _norm(name: str) -> str:
    """``"Policy Limit"``, ``"policy-limit"`` and ``"POLICY_LIMIT"`` all become ``policy_limit``."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def map_columns(columns: list[str]) -> dict[str, str]:
    """Map canonical column names to the headers actually present (first alias wins)."""
    present = {_norm(c): c for c in columns}
    out: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if _norm(a) in present:
                out[canonical] = present[_norm(a)]
                break
    return out


def parse_amount(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Parse a money column. Returns ``(values, malformed)``: empty cells are NaN and not
    malformed; cells that are not numeric after stripping ``$``, ``,``, spaces and ``USD``
    are NaN *and* malformed."""
    text = series.fillna("").astype(str).str.strip()
    cleaned = (
        text.str.replace(r"(?i)usd", "", regex=True)
        .str.replace(r"[,$\s]", "", regex=True)
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # accounting negatives
    )
    values = pd.to_numeric(cleaned, errors="coerce")
    malformed = (text != "") & values.isna()
    return values.astype(float), malformed


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

    n = len(raw)
    row_number = pd.Series(range(1, n + 1), name="row_number")
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []

    def err(
        row: int, message: str, *, fld: str | None = None, severity: str = "error", **extra: Any
    ):
        e: dict[str, Any] = {"row": int(row), "error": message, "severity": severity}
        if fld:
            e["field"] = fld
        e.update(extra)
        errors.append(e)

    # Identifiers ------------------------------------------------------------------------
    if "property_id" in cmap:
        pid = raw[cmap["property_id"]].fillna("").str.strip()
    else:
        pid = pd.Series([""] * n)
    generated = pid == ""
    pid = pid.where(~generated, "row-" + row_number.astype(str))
    n_generated = int(generated.sum())
    if n_generated:
        warnings.append(
            f"{n_generated} rows had no property_id; ids row-<n> were generated."
            if "property_id" in cmap
            else f"No property_id column: ids row-<n> were generated for all {n} rows."
        )

    # Amounts ------------------------------------------------------------------------------
    amounts: dict[str, pd.Series] = {}
    bad_financial = np.zeros(n, dtype=bool)
    for col in AMOUNT_COLUMNS:
        if col not in cmap:
            amounts[col] = pd.Series([np.nan] * n, dtype=float)
            continue
        values, malformed = parse_amount(raw[cmap[col]])
        for rn, txt in zip(row_number[malformed], raw.loc[malformed, cmap[col]], strict=True):
            err(
                rn,
                f"malformed {col} {str(txt).strip()!r} ignored (treated as not supplied)",
                fld=col,
                severity="warning",
                value=str(txt).strip(),
            )
        negative = values.notna() & (values < 0)
        for rn in row_number[negative]:
            err(rn, f"negative {col}", fld=col)
        bad_financial |= negative.to_numpy()
        amounts[col] = values

    df = pd.DataFrame(
        {
            "row_number": row_number,
            "property_id": pid,
            "lat": pd.to_numeric(raw[cmap["latitude"]], errors="coerce"),
            "lon": pd.to_numeric(raw[cmap["longitude"]], errors="coerce"),
            "address_text": raw[cmap["address"]].str.strip() if "address" in cmap else None,
            "property_value": amounts["property_value"],
            "contents_value": amounts["contents_value"],
            "insured_value": amounts["insured_value"],
            "limit": amounts["limit"],
            "deductible": amounts["deductible"],
            "policy_id": raw[cmap["policy_id"]].str.strip() if "policy_id" in cmap else None,
        }
    )

    # Coordinates ----------------------------------------------------------------------------
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
        err(rn, reason, fld="latitude/longitude")
    # A row with an impossible financial value is invalid, not merely annotated.
    valid = check.valid_mask & ~bad_financial

    # Duplicates --------------------------------------------------------------------------
    df["location_key"] = [
        dedup_key(la, lo, precision_m=LOCATION_PRECISION_M) if ok else None
        for la, lo, ok in zip(df["lat"], df["lon"], valid, strict=True)
    ]
    df["duplicate_kind"] = None
    df["is_duplicate_of"] = None
    vdf = df[valid]
    first_by_loc = vdf.groupby("location_key")["row_number"].first()
    first_by_id = vdf.groupby("property_id")["row_number"].first()
    first_by_both = vdf.groupby(["property_id", "location_key"])["row_number"].first()
    kinds: list[str | None] = []
    dup_of: list[int | None] = []
    for r in vdf.itertuples(index=False):
        both_first = int(first_by_both[(r.property_id, r.location_key)])
        loc_first = int(first_by_loc[r.location_key])
        id_first = int(first_by_id[r.property_id])
        if both_first != r.row_number:
            kinds.append("exact")
            dup_of.append(both_first)
        elif loc_first != r.row_number:
            kinds.append("shared_location")
            dup_of.append(loc_first)
        elif id_first != r.row_number:
            kinds.append("repeated_id")
            dup_of.append(id_first)
        else:
            kinds.append(None)
            dup_of.append(None)
    df.loc[valid, "duplicate_kind"] = pd.Series(kinds, index=vdf.index, dtype=object)
    df.loc[valid, "is_duplicate_of"] = pd.Series(dup_of, index=vdf.index, dtype=object)
    n_exact = int((df["duplicate_kind"] == "exact").sum())
    n_shared = int((df["duplicate_kind"] == "shared_location").sum())
    n_repeated = int((df["duplicate_kind"] == "repeated_id").sum())
    if n_exact:
        warnings.append(
            f"{n_exact} rows repeat an earlier row's property_id at the same location "
            f"(exact duplicates; excluded from counts and monetary totals)."
        )
    if n_shared:
        warnings.append(
            f"{n_shared} rows share a location (within ~{LOCATION_PRECISION_M:g} m) with an "
            f"earlier row under a different property_id (kept; reported as location accumulation)."
        )
    if n_repeated:
        warnings.append(
            f"{n_repeated} rows reuse a property_id already seen at a different location (kept; flagged)."
        )
    if all(c not in cmap for c in AMOUNT_COLUMNS[:3]):
        warnings.append("No value columns supplied: financial scenarios will not be computed.")
    elif "insured_value" not in cmap and "limit" not in cmap:
        warnings.append(
            "No sum insured or limit supplied: net insured losses are not capped by a policy limit."
        )
    df["valid"] = valid
    report = ValidationReport(
        n_rows=n,
        n_valid=int(valid.sum()),
        n_invalid=int((~valid).sum()),
        n_duplicates=n_exact,
        column_map=cmap,
        errors=errors[:500],
        warnings=warnings,
        n_shared_location=n_shared,
        n_repeated_id=n_repeated,
        n_locations=int(vdf["location_key"].nunique()),
        n_generated_ids=n_generated,
    )
    return df, report
