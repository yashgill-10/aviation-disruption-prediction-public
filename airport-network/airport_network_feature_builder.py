"""Build the retrospective Airport/Network features used by the Colab model.

Input rows must represent the canonical, deduplicated BTS table, in original
row order, with FL_DATE, DEP_HOUR, DEP_MINUTE, ORIGIN, DEST, DEP_DELAY,
CANCELLED, and delay_15. Any provenance key, including
``cleaned_input_row_ordinal``, is retained as a regular input column. This
reproduces the 2024 experiment; recent event features use realized BTS
departure delays and are not live-feed safe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


AIRPORT_FEATURES = [
    "airport_prior7_flights",
    "airport_prior7_delay_rate",
    "airport_prior7_mean_delay_min",
    "airport_prior7_avg_destinations_per_active_day",
]
SCHEDULE_FEATURE = "scheduled_origin_departures_prior2h"
RECENT_FEATURES = {
    "T−2h": [
        "tminus2h_recent_departures_2h",
        "tminus2h_recent_delay_rate_2h",
        "tminus2h_recent_mean_delay_min_2h",
    ],
    "T−30m": [
        "tminus30m_recent_departures_2h",
        "tminus30m_recent_delay_rate_2h",
        "tminus30m_recent_mean_delay_min_2h",
    ],
}
GENERATED_COLUMNS = AIRPORT_FEATURES + [SCHEDULE_FEATURE] + [
    col for cols in RECENT_FEATURES.values() for col in cols
]
REQUIRED_COLUMNS = {
    "FL_DATE", "DEP_HOUR", "DEP_MINUTE", "ORIGIN", "DEST", "DEP_DELAY",
    "CANCELLED", "delay_15",
}


def build_airport_network_features(flights: pd.DataFrame) -> pd.DataFrame:
    """Return a row-order-preserving copy with leakage-controlled features.

    The prior-seven-calendar-day aggregate uses only earlier dates. Same-day
    recent-departure features are reconstructed from scheduled time + DEP_DELAY
    and filtered to the two hours before each horizon's cutoff. All times are
    naive origin-local BTS times; events from different airports are never
    compared. Stable identifier and provenance columns, if present, are
preserved as-is through the row-order-preserving feature joins.
    """
    missing = sorted(REQUIRED_COLUMNS.difference(flights.columns))
    if missing:
        raise KeyError(f"Missing required input columns: {missing}")
    if flights.empty:
        result = flights.copy()
        for col in GENERATED_COLUMNS:
            result[col] = pd.Series(dtype="float32")
        return result

    result = flights.drop(columns=[c for c in GENERATED_COLUMNS if c in flights]).copy()
    result.reset_index(drop=True, inplace=True)
    dates = pd.to_datetime(result["FL_DATE"], errors="raise").dt.normalize()
    origin = result["ORIGIN"].astype("string")
    destination = result["DEST"].astype("string")
    result["FL_DATE"] = dates
    result["ORIGIN"] = origin
    result["DEST"] = destination
    delay = pd.to_numeric(result["DEP_DELAY"], errors="coerce").to_numpy(dtype=np.float64)
    cancelled = pd.to_numeric(result["CANCELLED"], errors="raise").to_numpy() != 0
    minutes = (
        pd.to_numeric(result["DEP_HOUR"], errors="raise").to_numpy(dtype=np.int64) * 60
        + pd.to_numeric(result["DEP_MINUTE"], errors="raise").to_numpy(dtype=np.int64)
    )
    scheduled = dates + pd.to_timedelta(minutes, unit="m")
    scheduled_ns = scheduled.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    safe_delay = np.nan_to_num(delay, nan=0.0)
    actual_ns = scheduled_ns + (safe_delay * 60_000_000_000).astype(np.int64)
    two_hours_ns = int(pd.Timedelta(hours=2).value)
    origin_groups = result.groupby("ORIGIN", observed=True, sort=False).indices

    # Daily airport history. Reindex to a complete airport/calendar grid so a
    # no-flight day contributes zero volume and zero outcomes, as in Colab.
    history = pd.DataFrame({
        "ORIGIN": origin,
        "FL_DATE": dates,
        "DEST": destination,
        "delay_15": pd.to_numeric(result["delay_15"], errors="raise"),
        "DEP_DELAY": np.nan_to_num(delay, nan=0.0),
        "operated": ~cancelled,
    })
    history = history.loc[history["operated"]].groupby(
        ["ORIGIN", "FL_DATE"], observed=True, sort=False
    ).agg(
        daily_flights=("delay_15", "size"),
        daily_delayed=("delay_15", "sum"),
        daily_delay_sum=("DEP_DELAY", "sum"),
        daily_destinations=("DEST", "nunique"),
    ).reset_index()
    airports = pd.Index(origin.dropna().unique(), name="ORIGIN")
    days = pd.date_range(dates.min(), dates.max(), freq="D", name="FL_DATE")
    grid = pd.MultiIndex.from_product([airports, days], names=["ORIGIN", "FL_DATE"]).to_frame(index=False)
    grid = grid.merge(history, on=["ORIGIN", "FL_DATE"], how="left", validate="one_to_one")
    daily_cols = ["daily_flights", "daily_delayed", "daily_delay_sum", "daily_destinations"]
    grid[daily_cols] = grid[daily_cols].fillna(0.0)
    grid["daily_active"] = (grid["daily_flights"] > 0).astype(np.float64)
    grouped = grid.groupby("ORIGIN", sort=False, observed=True)
    for source, name in [
        ("daily_flights", "prior7_flights"),
        ("daily_delayed", "prior7_delayed"),
        ("daily_delay_sum", "prior7_delay_sum"),
        ("daily_destinations", "prior7_destinations"),
        ("daily_active", "prior7_active_days"),
    ]:
        grid[name] = grouped[source].transform(lambda values: values.shift(1).rolling(7, min_periods=1).sum())
    denom = grid["prior7_flights"].fillna(0).to_numpy(dtype=float)
    active = grid["prior7_active_days"].fillna(0).to_numpy(dtype=float)
    delayed = grid["prior7_delayed"].fillna(0).to_numpy(dtype=float)
    delay_sum = grid["prior7_delay_sum"].fillna(0).to_numpy(dtype=float)
    destinations = grid["prior7_destinations"].fillna(0).to_numpy(dtype=float)
    grid["airport_prior7_flights"] = denom
    grid["airport_prior7_delay_rate"] = np.divide(
        delayed, denom, out=np.zeros(len(grid)), where=denom > 0
    )
    grid["airport_prior7_mean_delay_min"] = np.divide(
        delay_sum, denom, out=np.zeros(len(grid)), where=denom > 0
    )
    grid["airport_prior7_avg_destinations_per_active_day"] = np.divide(
        destinations, active, out=np.zeros(len(grid)), where=active > 0
    )
    lookup = grid[["ORIGIN", "FL_DATE"] + AIRPORT_FEATURES]
    result["_feature_row_order"] = np.arange(len(result), dtype=np.int64)
    result = result.merge(
        lookup, on=["ORIGIN", "FL_DATE"], how="left", validate="many_to_one", sort=False
    )
    result.sort_values("_feature_row_order", kind="stable", inplace=True)
    result.drop(columns="_feature_row_order", inplace=True)
    result.reset_index(drop=True, inplace=True)

    # Count other scheduled departures strictly before the target time and no
    # more than two hours before it, grouped by origin-local schedule clock.
    for rows in origin_groups.values():
        schedule = np.sort(scheduled_ns[rows])
        target = scheduled_ns[rows]
        left = np.searchsorted(schedule, target - two_hours_ns, side="left")
        right = np.searchsorted(schedule, target, side="left")
        if SCHEDULE_FEATURE not in result:
            result[SCHEDULE_FEATURE] = np.zeros(len(result), dtype=np.float32)
        result.loc[rows, SCHEDULE_FEATURE] = (right - left).astype(np.float32)

    # Reconstructed actual departure events in the strict two-hour lookback.
    # Per-origin prefix sums make each flight's window query efficient.
    cutoffs = {
        "T−2h": scheduled_ns - int(pd.Timedelta(hours=2).value),
        "T−30m": scheduled_ns - int(pd.Timedelta(minutes=30).value),
    }
    for horizon, columns in RECENT_FEATURES.items():
        counts = np.zeros(len(result), dtype=np.float32)
        delayed_counts = np.zeros(len(result), dtype=np.float32)
        delay_sums = np.zeros(len(result), dtype=np.float64)
        for rows in origin_groups.values():
            events = rows[~cancelled[rows] & np.isfinite(delay[rows])]
            if not len(events):
                continue
            order = np.argsort(actual_ns[events], kind="mergesort")
            event_rows = events[order]
            event_times = actual_ns[event_rows]
            event_delays = delay[event_rows]
            prefix_delay = np.r_[0.0, np.cumsum(event_delays, dtype=np.float64)]
            prefix_delayed = np.r_[0, np.cumsum((event_delays >= 15).astype(np.int64))]
            cutoff = cutoffs[horizon][rows]
            end = np.searchsorted(event_times, cutoff, side="left")
            start = np.searchsorted(event_times, cutoff - two_hours_ns, side="left")
            counts[rows] = end - start
            delayed_counts[rows] = prefix_delayed[end] - prefix_delayed[start]
            delay_sums[rows] = prefix_delay[end] - prefix_delay[start]
        rates = np.divide(delayed_counts, counts, out=np.zeros(len(result), dtype=np.float32), where=counts > 0)
        means = np.divide(delay_sums, counts, out=np.zeros(len(result), dtype=np.float64), where=counts > 0)
        result[columns[0]] = counts
        result[columns[1]] = rates
        result[columns[2]] = means.astype(np.float32)
    result[GENERATED_COLUMNS] = result[GENERATED_COLUMNS].astype(np.float32)
    assert len(result) == len(flights)
    assert result[GENERATED_COLUMNS].notna().all().all()
    return result


def horizon_features(horizon: str) -> list[str]:
    """Return the complete ordered model input list for one horizon."""
    if horizon not in RECENT_FEATURES:
        raise ValueError(f"horizon must be one of {list(RECENT_FEATURES)}")
    return [
        "MONTH", "DAY_OF_MONTH", "DEP_HOUR", "DEP_MINUTE", "OP_UNIQUE_CARRIER", "ORIGIN", "DEST",
        *AIRPORT_FEATURES, SCHEDULE_FEATURE, *RECENT_FEATURES[horizon],
    ]
