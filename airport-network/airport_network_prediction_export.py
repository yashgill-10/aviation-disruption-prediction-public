"""Export Airport/Network predictions with explicit score status.

Use the reviewed canonical ``flight_id`` or, before the crosswalk is applied,
the source's 1-based ``cleaned_input_row_ordinal``. No identifiers are inferred.
Retrospective BTS feature availability remains explicitly unverified.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from airport_network_inference import predict_airport_network_delay


HORIZONS = ("T−2h", "T−30m")
VALID_KEY_COLUMNS = {"flight_id", "cleaned_input_row_ordinal"}
ELIGIBILITY_COLUMNS = {"eligible_for_scoring", "unavailable_reason"}


def _canonical_id_validator():
    try:
        from src.canonical_flight_identity import is_canonical_flight_id
    except ImportError as exc:
        raise RuntimeError(
            "Canonical flight_id format validation requires src.canonical_flight_identity. "
            "Run from the repository root; the ordinal-keyed preliminary export does not need it."
        ) from exc
    return is_canonical_flight_id


def _validate_keyed_table(frame: pd.DataFrame, *, name: str, key_column: str) -> None:
    if key_column not in frame:
        raise KeyError(f"{name} must include the selected '{key_column}' key column.")
    blank_keys = frame[key_column].astype("string").str.strip().eq("")
    if frame[key_column].isna().any() or blank_keys.any():
        raise ValueError(f"{name}.{key_column} contains missing or blank keys.")
    if key_column == "cleaned_input_row_ordinal":
        numeric_keys = pd.to_numeric(frame[key_column], errors="coerce")
        if (
            numeric_keys.isna().any()
            or not np.isfinite(numeric_keys.to_numpy(dtype=np.float64)).all()
            or (numeric_keys < 1).any()
            or not numeric_keys.eq(np.floor(numeric_keys)).all()
        ):
            raise ValueError(f"{name}.{key_column} must contain positive 1-based integer row ordinals.")
    if key_column == "flight_id":
        is_canonical = _canonical_id_validator()
        bad = frame.loc[~frame[key_column].map(is_canonical), key_column]
        if len(bad):
            raise ValueError(
                f"{name}.{key_column} must contain canonical bts-flight-v1 IDs "
                f"('bts-flight-v1:<64 lowercase hex>'); found non-canonical values including {bad.head(5).tolist()}."
            )
    if frame[key_column].duplicated().any():
        examples = frame.loc[frame[key_column].duplicated(keep=False), key_column].head(5).tolist()
        raise ValueError(f"{name} must contain one row per selected key; duplicates include {examples}.")

def _feature_columns(bundle: Mapping[str, Any], horizon: str) -> list[str]:
    try:
        columns = (
            list(bundle["base_features"])
            + list(bundle["network_features"])
            + list(bundle["recent_features"][horizon])
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Bundle has no valid feature definition for {horizon}.") from exc
    if len(columns) != len(set(columns)):
        raise ValueError(f"Bundle feature definition for {horizon} contains duplicate columns.")
    return columns


def export_airport_network_predictions(
    canonical_flights: pd.DataFrame,
    features_by_horizon: Mapping[str, pd.DataFrame],
    eligibility_by_horizon: Mapping[str, pd.DataFrame],
    bundle: Mapping[str, Any],
    *,
    flight_id: str = "flight_id",
    key_column: str | None = None,
    target: str = "delay_15",
    partition: str = "partition",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return row-level scores and coverage/reason counts for both horizons.

    Use ``key_column="flight_id"`` for canonical exports or
    ``key_column="cleaned_input_row_ordinal"`` for preliminary exports. The
    ordinal is the 1-based data-record position, excluding the header, in the
    exact source CSV and must be preserved through all transformations.
    ``flight_id`` remains a compatibility alias for existing callers. Each
    eligibility table must cover the base keys exactly once and state a reason
    for each ineligible row. Missing scores remain null.

    This function never synthesizes IDs, imputes a missing score, or changes the
    saved bundle's feature list. Retrospective BTS feature availability remains
    unverified at the prediction cutoffs.
    """
    if set(features_by_horizon) != set(HORIZONS):
        raise ValueError(f"Provide features for exactly these horizons: {list(HORIZONS)}.")
    if set(eligibility_by_horizon) != set(HORIZONS):
        raise ValueError(f"Provide eligibility for exactly these horizons: {list(HORIZONS)}.")
    selected_key = "flight_id" if key_column is None and flight_id == "flight_id" else (flight_id if key_column is None else key_column)
    if selected_key not in VALID_KEY_COLUMNS:
        raise ValueError(f"key_column must be one of {sorted(VALID_KEY_COLUMNS)}.")
    _validate_keyed_table(canonical_flights, name="canonical_flights", key_column=selected_key)
    canonical_ids = canonical_flights[selected_key].tolist()
    canonical_id_set = set(canonical_ids)
    base_columns = [selected_key]
    for column in (target, partition):
        if column in canonical_flights.columns:
            base_columns.append(column)
    base = canonical_flights[base_columns].copy().reset_index(drop=True)

    output_frames: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        features = features_by_horizon[horizon].copy()
        eligibility = eligibility_by_horizon[horizon].copy()
        _validate_keyed_table(features, name=f"features[{horizon}]", key_column=selected_key)
        _validate_keyed_table(eligibility, name=f"eligibility[{horizon}]", key_column=selected_key)
        missing_status_columns = sorted(ELIGIBILITY_COLUMNS.difference(eligibility.columns))
        if missing_status_columns:
            raise KeyError(f"eligibility[{horizon}] is missing columns: {missing_status_columns}.")
        if set(eligibility[selected_key]) != canonical_id_set:
            raise ValueError(f"eligibility[{horizon}] IDs must exactly cover canonical_flights.")
        if not set(features[selected_key]).issubset(canonical_id_set):
            raise ValueError(f"features[{horizon}] contains IDs absent from canonical_flights.")
        eligibility["eligible_for_scoring"] = eligibility["eligible_for_scoring"].astype("boolean")
        if eligibility["eligible_for_scoring"].isna().any():
            raise ValueError(f"eligibility[{horizon}].eligible_for_scoring contains null values.")
        false_rows = ~eligibility["eligible_for_scoring"].astype(bool)
        no_reason = eligibility["unavailable_reason"].fillna("").astype(str).str.strip().eq("")
        if (false_rows & no_reason).any():
            raise ValueError(f"Every ineligible row in eligibility[{horizon}] needs an explicit reason.")
        true_rows = ~false_rows
        if (true_rows & ~no_reason).any():
            raise ValueError(f"Eligible rows in eligibility[{horizon}] must have a blank unavailable_reason.")

        feature_lookup = features.set_index(selected_key, drop=False)
        status_lookup = eligibility.set_index(selected_key, drop=False)
        columns = _feature_columns(bundle, horizon)
        missing_model_columns = sorted(set(columns).difference(features.columns))
        if missing_model_columns:
            # All rows are unavailable for this horizon, with the exact absent
            # model inputs visible in the row-level output and coverage report.
            missing_columns_reason = "required_model_columns_absent:" + ",".join(missing_model_columns)
        else:
            missing_columns_reason = ""

        rows = base.copy()
        rows["forecast_horizon"] = horizon
        rows["delay_probability"] = np.nan
        rows["decision_threshold"] = np.nan
        rows["predicted_delay_15"] = pd.Series(pd.NA, index=rows.index, dtype="Int64")
        rows["score_status"] = "unavailable"
        rows["unavailable_reason"] = ""
        rows["feature_availability"] = "retrospective_bts_availability_unverified"

        score_positions: list[int] = []
        score_ids: list[Any] = []
        for position, identifier in enumerate(canonical_ids):
            status = status_lookup.loc[identifier]
            if not bool(status["eligible_for_scoring"]):
                rows.at[position, "unavailable_reason"] = str(status["unavailable_reason"]).strip()
                continue
            if identifier not in feature_lookup.index:
                rows.at[position, "unavailable_reason"] = "feature_row_missing"
                continue
            if missing_columns_reason:
                rows.at[position, "unavailable_reason"] = missing_columns_reason
                continue
            feature_row = feature_lookup.loc[identifier, columns]
            missing_values = feature_row.index[feature_row.isna()].tolist()
            if missing_values:
                rows.at[position, "unavailable_reason"] = "required_model_values_missing:" + ",".join(missing_values)
                continue
            nonfinite_values = [
                column for column in columns
                if pd.api.types.is_numeric_dtype(features[column].dtype)
                and not np.isfinite(pd.to_numeric(pd.Series([feature_row[column]]), errors="coerce").iloc[0])
            ]
            if nonfinite_values:
                rows.at[position, "unavailable_reason"] = "required_model_values_nonfinite:" + ",".join(nonfinite_values)
                continue
            score_positions.append(position)
            score_ids.append(identifier)

        if score_positions:
            score_features = features.set_index(selected_key, drop=False).loc[score_ids].reset_index(drop=True)
            score_features = score_features.loc[:, [selected_key, *columns]]
            scored = predict_airport_network_delay(score_features, bundle, horizon=horizon)
            rows.loc[score_positions, "delay_probability"] = scored["delay_probability"].to_numpy()
            rows.loc[score_positions, "decision_threshold"] = scored["decision_threshold"].to_numpy()
            rows.loc[score_positions, "predicted_delay_15"] = scored["predicted_delay_15"].to_numpy()
            rows.loc[score_positions, "score_status"] = "scored"

        rows["unavailable_reason"] = rows["unavailable_reason"].replace("", pd.NA)
        output_frames.append(rows)
        reason_counts = (
            rows.loc[rows["score_status"].eq("unavailable"), "unavailable_reason"]
            .fillna("unspecified")
            .value_counts()
            .to_dict()
        )
        coverage_rows.append({
            "forecast_horizon": horizon,
            "expected_flights": len(rows),
            "scored_flights": int(rows["score_status"].eq("scored").sum()),
            "unavailable_flights": int(rows["score_status"].eq("unavailable").sum()),
            "score_coverage": float(rows["score_status"].eq("scored").mean()) if len(rows) else 0.0,
            "unavailable_reason_counts": "; ".join(f"{key}={value}" for key, value in sorted(reason_counts.items())),
        })

    predictions = pd.concat(output_frames, ignore_index=True)
    coverage = pd.DataFrame(coverage_rows)
    return predictions, coverage
