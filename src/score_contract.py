"""Canonical score-export adapter and validation helpers.

Airport/Network handoffs use ``flight_id`` while the Phase 2 mapping calls the
same value ``canonical_flight_id``.  This module makes that conversion explicit
and deliberately rejects ambiguous inputs instead of coercing hashed IDs.
"""
from __future__ import annotations

import re
from typing import Iterable

import pandas as pd

FLIGHT_ID_PATTERN = re.compile(r"^bts-flight-v1:[0-9a-f]{64}$")
HORIZONS = {"t_minus_2h", "t_minus_30m"}


def adapt_canonical_flight_id(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a string ``flight_id`` column.

    Exactly one of ``flight_id`` and ``canonical_flight_id`` must be supplied,
    unless both are present and row-wise identical.  IDs are never numerically
    converted; malformed/non-resolved IDs remain values for the caller to
    report, rather than being guessed or dropped.
    """
    has_flight = "flight_id" in frame.columns
    has_canonical = "canonical_flight_id" in frame.columns
    if not has_flight and not has_canonical:
        raise ValueError("score input needs flight_id or canonical_flight_id")
    result = frame.copy()
    if has_flight and has_canonical:
        left = result["flight_id"].fillna("").astype("string").str.strip()
        right = result["canonical_flight_id"].fillna("").astype("string").str.strip()
        if not left.equals(right):
            raise ValueError("flight_id and canonical_flight_id disagree")
    elif has_canonical:
        result["flight_id"] = result["canonical_flight_id"]
    result["flight_id"] = result["flight_id"].astype("string").str.strip()
    return result


def validate_score_rows(frame: pd.DataFrame, *, require_target: bool = False) -> dict[str, int]:
    """Validate common handoff invariants without removing any input row."""
    required = {"flight_id", "horizon", "score_status"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("score input missing columns: " + ", ".join(sorted(missing)))
    if require_target and "DepDel15" not in frame.columns:
        raise ValueError("matched evaluation input missing DepDel15")
    ids = frame["flight_id"].fillna("").astype("string").str.strip()
    horizons = frame["horizon"].fillna("").astype("string")
    status = frame["score_status"].fillna("").astype("string")
    duplicate_keys = int(pd.DataFrame({"id": ids, "h": horizons}).duplicated().sum())
    invalid_ids = int((~ids.str.fullmatch(FLIGHT_ID_PATTERN.pattern)).sum())
    invalid_horizons = int((~horizons.isin(HORIZONS)).sum())
    invalid_status = int((~status.isin({"scored", "unavailable"})).sum())
    if "p_delay15" in frame.columns:
        probability = pd.to_numeric(frame["p_delay15"], errors="coerce")
    elif "predicted_delay_probability" in frame.columns:
        probability = pd.to_numeric(frame["predicted_delay_probability"], errors="coerce")
    else:
        probability = pd.Series(float("nan"), index=frame.index)
    bad_scored_probability = int(((status == "scored") & (~probability.between(0, 1))).sum())
    return {
        "rows": len(frame), "unique_flight_ids": int(ids.nunique()),
        "duplicate_flight_horizon_keys": duplicate_keys, "invalid_flight_ids": invalid_ids,
        "invalid_horizons": invalid_horizons, "invalid_status": invalid_status,
        "bad_scored_probabilities": bad_scored_probability,
        "scored_rows": int((status == "scored").sum()),
        "unavailable_rows": int((status == "unavailable").sum()),
    }
