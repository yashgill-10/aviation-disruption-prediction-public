"""Leakage-aware join helpers for the Airport/Network + Flight/Weather model."""

from __future__ import annotations

from collections.abc import Sequence
import re
import sys
from pathlib import Path

import pandas as pd


CANONICAL_ID_VERSION = "bts-flight-v1"
_CANONICAL_ID_RE = re.compile(r"^bts-flight-v1:[0-9a-f]{64}$")

CURRENT_OUTCOME_COLUMNS = {
    "DEP_DELAY",
    "ARR_DELAY",
    "DEP_TIME",
    "ARR_TIME",
    "WHEELS_OFF",
    "WHEELS_ON",
    "TAXI_OUT",
    "ACTUAL_ELAPSED_TIME",
    "CANCELLED",
    "DIVERTED",
    "DELAY_15",
}


def _validate_ids(frame: pd.DataFrame, *, table_name: str, flight_id: str) -> None:
    if flight_id not in frame.columns:
        raise KeyError(f"{table_name} is missing the stable key {flight_id!r}.")
    if frame[flight_id].isna().any():
        raise ValueError(f"{table_name}.{flight_id} contains missing values.")
    if frame[flight_id].duplicated().any():
        examples = frame.loc[frame[flight_id].duplicated(keep=False), flight_id].head(5).tolist()
        raise ValueError(f"{table_name} must have one row per flight; duplicate IDs include {examples}.")


def _load_canonical_identity():
    """Import the established ``bts-flight-v1`` implementation from ``src/``."""
    try:
        from src.canonical_flight_identity import VERSION, flight_id_for_row
        return VERSION, flight_id_for_row
    except ImportError:
        pass
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from src.canonical_flight_identity import VERSION, flight_id_for_row
    except ImportError as exc:
        raise RuntimeError(
            "Canonical identity module src.canonical_flight_identity is unavailable. "
            "Run from the repository root or supply bts-flight-v1 IDs directly; "
            "row-position IDs are not permitted."
        ) from exc
    return VERSION, flight_id_for_row


def _validate_canonical_id_values(frame: pd.DataFrame, *, table_name: str, flight_id: str) -> None:
    bad = frame.loc[~frame[flight_id].astype(str).str.match(_CANONICAL_ID_RE.pattern), flight_id]
    if len(bad):
        raise ValueError(
            f"{table_name}.{flight_id} must contain canonical {CANONICAL_ID_VERSION} IDs "
            f"('bts-flight-v1:<64 lowercase hex>'); found non-canonical values including {bad.head(5).tolist()}. "
            "Generate them with src.canonical_flight_identity.flight_id_for_row; "
            "row-position or arbitrary integer IDs are not permitted."
        )


def add_canonical_flight_id(
    canonical_flights: pd.DataFrame,
    *,
    flight_id: str = "flight_id",
) -> pd.DataFrame:
    """Expose the established ``bts-flight-v1`` canonical ID on the flight table.

    If ``flight_id`` is already present it must contain canonical
    ``bts-flight-v1:<sha256>`` values. If it is absent, IDs are computed from the
    nine normalized BTS schedule fields via ``src.canonical_flight_identity`` —
    never from dataframe row position. Call this before feature engineering,
    splitting, sampling, or reordering. Keep the resulting key in every branch's
    feature table. Do not independently regenerate IDs in the Airport/Network
    and Flight/Weather pipelines.
    """
    if flight_id in canonical_flights.columns:
        _validate_ids(canonical_flights, table_name="canonical_flights", flight_id=flight_id)
        _validate_canonical_id_values(canonical_flights, table_name="canonical_flights", flight_id=flight_id)
        return canonical_flights.copy()
    version, flight_id_for_row = _load_canonical_identity()
    if version != CANONICAL_ID_VERSION:
        raise RuntimeError(f"Unexpected canonical identity version {version!r}; expected {CANONICAL_ID_VERSION!r}.")
    output = canonical_flights.copy()
    output.insert(0, flight_id, [flight_id_for_row(row) for row in output.to_dict("records")])
    _validate_ids(output, table_name="canonical_flights", flight_id=flight_id)
    _validate_canonical_id_values(output, table_name="canonical_flights", flight_id=flight_id)
    return output


def _validate_predeparture_features(
    frame: pd.DataFrame,
    *,
    table_name: str,
    flight_id: str,
    forbidden_columns: set[str],
) -> None:
    forbidden = {name.upper() for name in forbidden_columns}
    found = sorted(
        column for column in frame.columns
        if column != flight_id and str(column).upper() in forbidden
    )
    if found:
        raise ValueError(
            f"{table_name} includes current-flight outcomes or status fields: {found}. "
            "Remove them before constructing a pre-departure feature table."
        )


def combine_predeparture_features(
    base_flights: pd.DataFrame,
    airport_features: pd.DataFrame,
    weather_features: pd.DataFrame,
    *,
    flight_id: str = "flight_id",
    target: str = "delay_15",
    forbidden_columns: Sequence[str] = tuple(CURRENT_OUTCOME_COLUMNS),
) -> pd.DataFrame:
    """Join row-aligned feature tables without changing base-flight order.

    The base table must hold one row per flight and include the date and target.
    Airport and weather tables must also hold exactly one row per flight and use
    the same stable ID. The function does not infer or fix a key from route/date.
    """
    if target not in base_flights.columns:
        raise KeyError(f"Base table is missing target {target!r}.")
    if "FL_DATE" not in base_flights.columns:
        raise KeyError("Base table is missing FL_DATE for chronological evaluation.")

    tables = (
        ("base_flights", base_flights),
        ("airport_features", airport_features),
        ("weather_features", weather_features),
    )
    for name, frame in tables:
        _validate_ids(frame, table_name=name, flight_id=flight_id)
        if name != "base_flights":
            base_ids = set(base_flights[flight_id])
            feature_ids = set(frame[flight_id])
            missing_ids = base_ids - feature_ids
            extra_ids = feature_ids - base_ids
            if missing_ids or extra_ids:
                raise ValueError(
                    f"{name} flight IDs must exactly match base_flights: "
                    f"missing={len(missing_ids)}, extra={len(extra_ids)}."
                )
    _validate_predeparture_features(
        airport_features, table_name="airport_features", flight_id=flight_id,
        forbidden_columns=set(forbidden_columns) | {target},
    )
    _validate_predeparture_features(
        weather_features, table_name="weather_features", flight_id=flight_id,
        forbidden_columns=set(forbidden_columns) | {target},
    )

    airport_columns = set(airport_features.columns) - {flight_id}
    weather_columns = set(weather_features.columns) - {flight_id}
    overlap = sorted(airport_columns & weather_columns)
    existing = sorted((airport_columns | weather_columns) & (set(base_flights.columns) - {flight_id}))
    if overlap or existing:
        raise ValueError(
            "Feature names must be unique across the three input tables. "
            f"Airport/weather overlap: {overlap}; overlap with base table: {existing}."
        )

    merged = base_flights.merge(
        airport_features, on=flight_id, how="left", validate="one_to_one", sort=False, indicator="_airport_join"
    )
    missing_airport = int(merged["_airport_join"].eq("left_only").sum())
    merged = merged.drop(columns="_airport_join")
    merged = merged.merge(
        weather_features, on=flight_id, how="left", validate="one_to_one", sort=False, indicator="_weather_join"
    )
    missing_weather = int(merged["_weather_join"].eq("left_only").sum())
    merged = merged.drop(columns="_weather_join")

    if len(merged) != len(base_flights):
        raise AssertionError("The feature joins changed the number of base-flight rows.")
    if missing_airport or missing_weather:
        raise ValueError(
            "Feature tables do not cover every base flight: "
            f"missing airport rows={missing_airport}, missing weather rows={missing_weather}."
        )
    if not merged[flight_id].reset_index(drop=True).equals(base_flights[flight_id].reset_index(drop=True)):
        raise AssertionError("The feature joins changed base-flight row order.")
    merged["FL_DATE"] = pd.to_datetime(merged["FL_DATE"], errors="raise")
    if merged[target].isna().any():
        raise ValueError(f"Target {target!r} contains missing values.")
    if not merged[target].isin([0, 1]).all():
        raise ValueError(f"Target {target!r} must be binary (0/1).")
    return merged


def attach_airport_network_predictions(
    flight_weather_predictions: pd.DataFrame,
    airport_network_predictions: pd.DataFrame,
    *,
    flight_id: str = "flight_id",
    probability_col: str = "delay_probability",
    label_col: str = "predicted_delay_15",
    horizon_col: str = "forecast_horizon",
    expected_horizon: str | None = None,
) -> pd.DataFrame:
    """Attach Airport/Network scores to Flight/Weather dashboard rows by ID.

    Both inputs must contain exactly one row per identical flight-ID set. The
    output keeps the Flight/Weather row order and adds namespaced columns so
    score fields cannot silently overwrite the other model's output.
    """
    _validate_ids(flight_weather_predictions, table_name="flight_weather_predictions", flight_id=flight_id)
    _validate_ids(airport_network_predictions, table_name="airport_network_predictions", flight_id=flight_id)
    left_ids = set(flight_weather_predictions[flight_id])
    right_ids = set(airport_network_predictions[flight_id])
    if left_ids != right_ids:
        raise ValueError(
            "Airport/Network prediction IDs must exactly match Flight/Weather rows: "
            f"missing={len(left_ids - right_ids)}, extra={len(right_ids - left_ids)}."
        )
    required = {probability_col, label_col}
    missing = sorted(required.difference(airport_network_predictions.columns))
    if missing:
        raise KeyError(f"Airport/Network predictions are missing output columns: {missing}.")
    if expected_horizon is not None:
        if horizon_col not in airport_network_predictions.columns:
            raise KeyError(f"Airport/Network predictions are missing horizon column {horizon_col!r}.")
        observed = airport_network_predictions[horizon_col].astype(str)
        if not observed.eq(expected_horizon).all():
            raise ValueError(
                f"Expected only {expected_horizon!r} Airport/Network scores; "
                f"found {sorted(set(observed))}."
            )

    probabilities = pd.to_numeric(airport_network_predictions[probability_col], errors="coerce")
    if probabilities.isna().any() or not probabilities.between(0, 1).all():
        raise ValueError("Airport/Network probabilities must be finite values in [0, 1].")
    labels = pd.to_numeric(airport_network_predictions[label_col], errors="coerce")
    if labels.isna().any() or not labels.isin([0, 1]).all():
        raise ValueError("Airport/Network predicted labels must be binary (0/1).")

    score_columns = [probability_col, label_col]
    if horizon_col in airport_network_predictions.columns:
        score_columns.append(horizon_col)
    if "decision_threshold" in airport_network_predictions.columns:
        score_columns.append("decision_threshold")
    namespaced = airport_network_predictions[[flight_id, *score_columns]].rename(
        columns={
            probability_col: "airport_network_delay_probability",
            label_col: "airport_network_predicted_delay_15",
            horizon_col: "airport_network_forecast_horizon",
            "decision_threshold": "airport_network_decision_threshold",
        }
    )
    collisions = sorted(
        (set(namespaced.columns) - {flight_id}) & set(flight_weather_predictions.columns)
    )
    if collisions:
        raise ValueError(f"Airport/Network output columns already exist in Flight/Weather rows: {collisions}.")

    combined = flight_weather_predictions.merge(
        namespaced, on=flight_id, how="left", validate="one_to_one", sort=False
    )
    if len(combined) != len(flight_weather_predictions):
        raise AssertionError("Attaching Airport/Network predictions changed the row count.")
    if not combined[flight_id].reset_index(drop=True).equals(
        flight_weather_predictions[flight_id].reset_index(drop=True)
    ):
        raise AssertionError("Attaching Airport/Network predictions changed Flight/Weather row order.")
    return combined


def chronological_partitions(
    combined: pd.DataFrame,
    *,
    date_col: str = "FL_DATE",
    train_end: str = "2024-09-01",
    calibration_end: str = "2024-10-01",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return Jan-Aug fit, September calibration, and Oct-Dec test partitions."""
    if date_col not in combined.columns:
        raise KeyError(f"Combined table is missing date column {date_col!r}.")
    dates = pd.to_datetime(combined[date_col], errors="raise")
    train_cutoff = pd.Timestamp(train_end)
    calibration_cutoff = pd.Timestamp(calibration_end)
    if train_cutoff >= calibration_cutoff:
        raise ValueError("train_end must be earlier than calibration_end.")

    train = combined.loc[dates < train_cutoff].copy()
    calibration = combined.loc[(dates >= train_cutoff) & (dates < calibration_cutoff)].copy()
    test = combined.loc[dates >= calibration_cutoff].copy()
    if train.empty or calibration.empty or test.empty:
        raise ValueError("All three chronological partitions must contain at least one flight.")
    if train[date_col].max() >= calibration[date_col].min():
        raise AssertionError("Training and calibration periods are not chronological.")
    if calibration[date_col].max() >= test[date_col].min():
        raise AssertionError("Calibration and test periods are not chronological.")
    return train, calibration, test


def _self_check() -> None:
    """Small executable smoke check for joins, row preservation, and leakage guards."""
    import hashlib

    def _cid(label: str) -> str:
        return f"bts-flight-v1:{hashlib.sha256(label.encode()).hexdigest()}"

    base = add_canonical_flight_id(pd.DataFrame({
        "flight_id": [_cid("self-check-0"), _cid("self-check-1"), _cid("self-check-2")],
        "FL_DATE": ["2024-08-31", "2024-09-15", "2024-10-02"],
        "delay_15": [0, 1, 0],
        "ORIGIN": ["A", "B", "C"],
    }))
    airport = pd.DataFrame({"flight_id": base["flight_id"], "airport_prior7_delay_rate": [0.1, 0.2, 0.3]})
    weather = pd.DataFrame({"flight_id": base["flight_id"], "wind_speed": [3.0, 8.0, 5.0]})
    combined = combine_predeparture_features(base, airport, weather)
    train, calibration, test = chronological_partitions(combined)
    assert combined["flight_id"].tolist() == base["flight_id"].tolist()
    assert [len(train), len(calibration), len(test)] == [1, 1, 1]

    bad_weather = weather.assign(DEP_DELAY=[0, 1, 2])
    try:
        combine_predeparture_features(base, airport, bad_weather)
    except ValueError as exc:
        assert "outcomes or status fields" in str(exc)
    else:
        raise AssertionError("Leakage guard did not reject DEP_DELAY.")

    duplicate_airport = pd.concat([airport, airport.iloc[[0]]], ignore_index=True)
    try:
        combine_predeparture_features(base, duplicate_airport, weather)
    except ValueError as exc:
        assert "one row per flight" in str(exc)
    else:
        raise AssertionError("Duplicate-key guard did not reject a repeated flight ID.")

    missing_weather = weather.iloc[:-1].copy()
    try:
        combine_predeparture_features(base, airport, missing_weather)
    except ValueError as exc:
        assert "missing=1, extra=0" in str(exc)
    else:
        raise AssertionError("Coverage guard did not reject a missing weather row.")

    extra_weather = pd.concat([
        weather,
        pd.DataFrame({"flight_id": [99], "wind_speed": [7.0]}),
    ], ignore_index=True)
    try:
        combine_predeparture_features(base, airport, extra_weather)
    except ValueError as exc:
        assert "missing=0, extra=1" in str(exc)
    else:
        raise AssertionError("Exact-key guard did not reject an extra feature row.")

    print("Integration adapter smoke check passed: exact key sets, one-to-one join, row order, time split, leakage, duplicates, and coverage.")


if __name__ == "__main__":
    _self_check()
