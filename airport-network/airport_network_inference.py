"""Inference helpers for the saved Airport/Network model bundle."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# These values describe the scored flight's realized outcome or post-cutoff
# state. They must never be part of the saved model's prediction feature list.
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

HORIZON_OFFSETS = {"T−2h": pd.Timedelta(hours=2), "T−30m": pd.Timedelta(minutes=30)}


def validate_operational_snapshot_cutoff(
    feature_rows: pd.DataFrame,
    *,
    horizon: str,
    scheduled_departure_utc: str,
    snapshot_available_at_utc: str,
) -> None:
    """Reject feature snapshots that became available after the forecast cutoff.

    Timestamp columns must contain timezone-aware values. This check validates
    the snapshot timestamp only; data lineage must separately prove that all
    underlying airport events and outcomes were actually present in the source
    feed by that time.
    """
    if horizon not in HORIZON_OFFSETS:
        raise ValueError(f"Unknown horizon {horizon!r}. Available horizons: {sorted(HORIZON_OFFSETS)}.")
    missing_columns = [
        col for col in (scheduled_departure_utc, snapshot_available_at_utc)
        if col not in feature_rows.columns
    ]
    if missing_columns:
        raise KeyError(f"Missing timestamp columns: {missing_columns}.")

    def as_utc(column: str) -> pd.Series:
        values = feature_rows[column]
        if values.isna().any():
            raise ValueError(f"Timestamp column {column!r} contains missing values.")
        aware = values.map(lambda value: pd.Timestamp(value).tzinfo is not None)
        if not aware.all():
            raise ValueError(f"Timestamp column {column!r} must contain timezone-aware timestamps.")
        return pd.to_datetime(values, utc=True, errors="raise")

    departure = as_utc(scheduled_departure_utc)
    available = as_utc(snapshot_available_at_utc)
    cutoff = departure - HORIZON_OFFSETS[horizon]
    late = available > cutoff
    if late.any():
        examples = feature_rows.index[late].tolist()[:5]
        raise ValueError(
            f"{int(late.sum())} feature snapshots became available after the {horizon} cutoff; "
            f"example row indices: {examples}."
        )


def load_airport_network_bundle(path: str | Path) -> dict[str, Any]:
    """Load the Colab-exported model bundle and check its required fields."""
    import joblib

    bundle = joblib.load(Path(path))
    required = {"models", "thresholds", "base_features", "network_features", "recent_features"}
    missing = required.difference(bundle)
    if missing:
        raise ValueError(f"Model bundle is missing keys: {sorted(missing)}")
    if not isinstance(bundle["models"], Mapping) or not isinstance(bundle["thresholds"], Mapping):
        raise TypeError("Bundle models and thresholds must be mappings keyed by forecast horizon.")
    return bundle


def predict_airport_network_delay(
    feature_ready_flights: pd.DataFrame,
    bundle: Mapping[str, Any],
    *,
    horizon: str = "T−2h",
) -> pd.DataFrame:
    """Return a copy of flight rows with probabilities and thresholded labels.

    Required feature columns are taken from the saved bundle, so the matching
    horizon-specific recent-state fields are enforced automatically.
    """
    models = bundle.get("models")
    thresholds = bundle.get("thresholds")
    if not isinstance(models, Mapping) or not isinstance(thresholds, Mapping):
        raise TypeError("Bundle must contain horizon-keyed models and thresholds.")
    if horizon not in models or horizon not in thresholds:
        available = sorted(set(models) & set(thresholds))
        raise ValueError(f"Unknown horizon {horizon!r}. Available horizons: {available}.")

    try:
        columns = (
            list(bundle["base_features"])
            + list(bundle["network_features"])
            + list(bundle["recent_features"][horizon])
        )
    except KeyError as exc:
        raise ValueError(f"Bundle has no feature definition for horizon {horizon!r}.") from exc
    if len(columns) != len(set(columns)):
        raise ValueError("Bundle feature definitions contain duplicate column names.")
    leaky_features = sorted(
        column for column in columns
        if str(column).upper() in CURRENT_OUTCOME_COLUMNS
    )
    if leaky_features:
        raise ValueError(
            "Model bundle includes current-flight outcomes or post-cutoff fields "
            f"as prediction features: {leaky_features}. Remove them and retrain."
        )

    missing = sorted(set(columns).difference(feature_ready_flights.columns))
    if missing:
        raise ValueError(
            f"Missing required pre-cutoff features for {horizon}: {missing}. "
            "Build the Airport/Network state as of that horizon before scoring."
        )
    threshold = float(thresholds[horizon])
    if not np.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError(f"Invalid decision threshold for {horizon}: {threshold}.")

    probabilities = np.asarray(models[horizon].predict_proba(feature_ready_flights[columns]))
    if probabilities.ndim != 2 or probabilities.shape != (len(feature_ready_flights), 2):
        raise ValueError(
            "Model predict_proba must return an (n_rows, 2) array for the binary delay target."
        )
    delay_probability = probabilities[:, 1]
    if not np.isfinite(delay_probability).all() or ((delay_probability < 0) | (delay_probability > 1)).any():
        raise ValueError("Model returned probabilities outside the finite [0, 1] range.")

    output = feature_ready_flights.copy()
    output["delay_probability"] = delay_probability
    output["predicted_delay_15"] = (delay_probability >= threshold).astype(np.int8)
    output["forecast_horizon"] = horizon
    output["decision_threshold"] = threshold
    return output


class _MockBinaryModel:
    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        p = frame["airport_prior7_delay_rate"].to_numpy(dtype=float)
        p = np.clip(p, 0, 1)
        return np.column_stack([1 - p, p])


def _self_check() -> None:
    features = pd.DataFrame({
        "flight_id": [201, 202, 203],
        "airport_prior7_delay_rate": [0.2, 0.6, 0.8],
        "recent_activity_t2h": [1, 2, 3],
        "recent_activity_t30m": [4, 5, 6],
    })
    bundle = {
        "models": {"T−2h": _MockBinaryModel(), "T−30m": _MockBinaryModel()},
        "thresholds": {"T−2h": 0.5, "T−30m": 0.7},
        "base_features": [],
        "network_features": ["airport_prior7_delay_rate"],
        "recent_features": {
            "T−2h": ["recent_activity_t2h"],
            "T−30m": ["recent_activity_t30m"],
        },
    }
    t2h = predict_airport_network_delay(features, bundle, horizon="T−2h")
    t30m = predict_airport_network_delay(features, bundle, horizon="T−30m")
    assert t2h["flight_id"].tolist() == features["flight_id"].tolist()
    assert t2h["predicted_delay_15"].tolist() == [0, 1, 1]
    assert t30m["predicted_delay_15"].tolist() == [0, 0, 1]
    assert "predicted_delay_15" not in features.columns

    try:
        predict_airport_network_delay(features.drop(columns="recent_activity_t2h"), bundle, horizon="T−2h")
    except ValueError as exc:
        assert "Missing required pre-cutoff features" in str(exc)
    else:
        raise AssertionError("Missing-feature guard did not reject incomplete input.")

    try:
        predict_airport_network_delay(features, bundle, horizon="T−1h")
    except ValueError as exc:
        assert "Unknown horizon" in str(exc)
    else:
        raise AssertionError("Horizon guard did not reject an unknown horizon.")

    leaky_bundle = dict(bundle)
    leaky_bundle["base_features"] = ["DEP_DELAY"]
    try:
        predict_airport_network_delay(features, leaky_bundle, horizon="T−2h")
    except ValueError as exc:
        assert "current-flight outcomes or post-cutoff fields" in str(exc)
    else:
        raise AssertionError("Leakage guard did not reject a realized delay feature.")

    timestamps = pd.DataFrame({
        "scheduled_departure_utc": ["2024-10-01T11:00:00Z", "2024-10-01T11:00:00Z"],
        "snapshot_available_at_utc": ["2024-10-01T08:59:00Z", "2024-10-01T09:00:00Z"],
    })
    validate_operational_snapshot_cutoff(
        timestamps, horizon="T−2h", scheduled_departure_utc="scheduled_departure_utc",
        snapshot_available_at_utc="snapshot_available_at_utc",
    )
    late_snapshot = timestamps.copy()
    late_snapshot.loc[1, "snapshot_available_at_utc"] = "2024-10-01T09:01:00Z"
    try:
        validate_operational_snapshot_cutoff(
            late_snapshot, horizon="T−2h", scheduled_departure_utc="scheduled_departure_utc",
            snapshot_available_at_utc="snapshot_available_at_utc",
        )
    except ValueError as exc:
        assert "became available after" in str(exc)
    else:
        raise AssertionError("Cutoff guard did not reject a late snapshot.")

    naive_timestamps = timestamps.copy()
    naive_timestamps["snapshot_available_at_utc"] = ["2024-10-01 08:59:00", "2024-10-01 09:00:00"]
    try:
        validate_operational_snapshot_cutoff(
            naive_timestamps, horizon="T−2h", scheduled_departure_utc="scheduled_departure_utc",
            snapshot_available_at_utc="snapshot_available_at_utc",
        )
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("Cutoff guard did not reject timezone-naive timestamps.")

    print("Inference smoke check passed: both horizons, thresholds, row preservation, no input mutation, missing-feature, horizon, leakage, and snapshot-cutoff guards.")


if __name__ == "__main__":
    _self_check()
