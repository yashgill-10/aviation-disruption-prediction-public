# AIRCRAFT ROTATION / PROPAGATION PILOT — AIRPORT/NETWORK TRACK ONLY
# Paste this entire file into Colab after cells 2, 4, 5, and 8 have run.
# It does not access weather data or modify the Flight/Weather cells.
# Retrospective pilot: realized prior arrivals and BTS tail assignments are not guaranteed
# to have been operationally available at the prediction cutoff.

from pathlib import Path
from zipfile import ZipFile
import json
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    balanced_accuracy_score, average_precision_score, precision_recall_curve,
)
from sklearn.model_selection import train_test_split

# Preconditions
assert "df" in globals(), "Run the data loading and time-feature cells first."
required_project = {
    "FL_DATE", "OP_UNIQUE_CARRIER", "ORIGIN", "DEST", "CRS_DEP_TIME",
    "DEP_DELAY", "CANCELLED", "DIVERTED", "delay_15",
    "MONTH", "DAY_OF_MONTH", "DEP_HOUR", "DEP_MINUTE",
}
missing = required_project.difference(df.columns)
assert not missing, f"Project table is missing columns: {sorted(missing)}"
BTS_FOLDER = Path("/content/bts_on_time_2024_monthly")
bts_archives = sorted(BTS_FOLDER.glob("*.zip"))
assert bts_archives, f"No monthly BTS ZIPs found in {BTS_FOLDER}"

try:
    import airportsdata
except ImportError as exc:
    raise ImportError(
        "Install airportsdata in a separate Colab cell, then rerun this pilot."
    ) from exc

airport_lookup = airportsdata.load("IATA")
RANDOM_STATE = 42
SAMPLE_SIZES = {"train": 30_000, "calibration": 10_000, "test": 20_000}
BASE_NUMERIC = ["MONTH", "DAY_OF_MONTH", "DEP_HOUR", "DEP_MINUTE"]
BASE_CATEGORICAL = ["OP_UNIQUE_CARRIER", "ORIGIN", "DEST"]
AIRCRAFT_NUMERIC = ["prior_tail_arrival_available", "prior_tail_arr_delay_min", "minutes_since_prior_tail_arrival"]

# Small stratified samples preserve the chronological Jan-Aug / Sep / Oct-Dec design.
def _eligible_period(start, end, horizon_min, sample_size, seed):
    dates = pd.to_datetime(df["FL_DATE"])
    mask = (dates >= pd.Timestamp(start)) & (dates < pd.Timestamp(end))
    mask &= pd.to_numeric(df["CANCELLED"], errors="coerce").fillna(1).eq(0)
    mask &= pd.to_numeric(df["DIVERTED"], errors="coerce").fillna(0).eq(0)
    # Retain only flights that had not already departed by this forecast cutoff.
    mask &= pd.to_numeric(df["DEP_DELAY"], errors="coerce").gt(-horizon_min)
    columns = list(dict.fromkeys(BASE_NUMERIC + BASE_CATEGORICAL + ["FL_DATE", "CRS_DEP_TIME", "DEP_DELAY", "delay_15"]))
    frame = df.loc[mask, columns].copy()
    frame = frame.dropna(subset=BASE_NUMERIC + BASE_CATEGORICAL + ["FL_DATE", "CRS_DEP_TIME", "DEP_DELAY", "delay_15"])
    if frame["delay_15"].nunique() < 2:
        raise ValueError(f"Both target classes are required in period {start} to {end}.")
    n = min(sample_size, len(frame))
    if n < len(frame):
        frame, _ = train_test_split(
            frame, train_size=n, random_state=seed,
            stratify=frame["delay_15"].astype(int),
        )
    return frame.reset_index(drop=True)

# Sample each horizon separately. Baseline and aircraft-augmented variants are
# compared on identical retained rows within a horizon; samples differ across horizons.
pilot_parts = []
for horizon_min in (120, 30):
    for period_name, start, end in (
        ("train", "2024-01-01", "2024-09-01"),
        ("calibration", "2024-09-01", "2024-10-01"),
        ("test", "2024-10-01", "2025-01-01"),
    ):
        part = _eligible_period(
            start, end, horizon_min, SAMPLE_SIZES[period_name],
            RANDOM_STATE + horizon_min + len(pilot_parts),
        )
        part["horizon_min"] = horizon_min
        part["period"] = period_name
        pilot_parts.append(part)
pilot = pd.concat(pilot_parts, ignore_index=True)
pilot["pilot_id"] = np.arange(len(pilot), dtype=np.int32)

# Match sampled project flights to BTS tail numbers using airline, route, date, and scheduled time.
def _normalized_time(series):
    values = pd.to_numeric(series, errors="coerce")
    values = values.mask(values.eq(2400), 0)
    return values.round().astype("Int32")

pilot["_join_key"] = (
    pd.to_datetime(pilot["FL_DATE"]).dt.strftime("%Y-%m-%d") + "|" +
    pilot["OP_UNIQUE_CARRIER"].astype(str) + "|" +
    pilot["ORIGIN"].astype(str) + "|" + pilot["DEST"].astype(str) + "|" +
    _normalized_time(pilot["CRS_DEP_TIME"]).astype(str)
)
join_keys = set(pilot["_join_key"])
match_chunks = []
match_columns = ["FlightDate", "Reporting_Airline", "Tail_Number", "Origin", "Dest", "CRSDepTime"]
for archive in bts_archives:
    with ZipFile(archive) as zf:
        csv_members = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if not csv_members:
            continue
        with zf.open(csv_members[0]) as source:
            for chunk in pd.read_csv(source, usecols=match_columns, chunksize=200_000, low_memory=False):
                chunk = chunk.dropna(subset=match_columns)
                chunk["_join_key"] = (
                    pd.to_datetime(chunk["FlightDate"], errors="coerce").dt.strftime("%Y-%m-%d") + "|" +
                    chunk["Reporting_Airline"].astype(str) + "|" + chunk["Origin"].astype(str) + "|" +
                    chunk["Dest"].astype(str) + "|" + _normalized_time(chunk["CRSDepTime"]).astype(str)
                )
                found = chunk.loc[chunk["_join_key"].isin(join_keys), ["_join_key", "Tail_Number"]].copy()
                if not found.empty:
                    match_chunks.append(found)
if not match_chunks:
    raise RuntimeError("No BTS tail-number matches were found for the sampled flights.")
matched = pd.concat(match_chunks, ignore_index=True).dropna(subset=["Tail_Number"])
key_tail_counts = matched.groupby("_join_key")["Tail_Number"].nunique()
unique_keys = key_tail_counts.index[key_tail_counts.eq(1)]
unique_tail_map = (
    matched.loc[matched["_join_key"].isin(unique_keys)]
    .drop_duplicates("_join_key").set_index("_join_key")["Tail_Number"]
)
pilot["Tail_Number"] = pilot["_join_key"].map(unique_tail_map)
ambiguous_keys = set(key_tail_counts.index[key_tail_counts.gt(1)])
pilot.loc[pilot["_join_key"].isin(ambiguous_keys), "Tail_Number"] = np.nan
sample_tails = set(pilot["Tail_Number"].dropna().astype(str))
if not sample_tails:
    raise RuntimeError("No unique tail-number matches remain after ambiguous joins were excluded.")

# Convert local BTS times to UTC with each airport's IANA timezone; exclude DST-ambiguous times.
def _local_time_to_utc(frame, airport_col, date_col, time_col):
    result = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    hhmm = pd.to_numeric(frame[time_col], errors="coerce")
    valid = hhmm.notna() & hhmm.ge(0) & hhmm.le(2400) & (hhmm.mod(100).lt(60))
    for airport_code, index in frame.loc[valid].groupby(airport_col, observed=True).groups.items():
        tz = airport_lookup.get(str(airport_code), {}).get("tz")
        if not tz:
            continue
        local_hhmm = hhmm.loc[index].astype("int64")
        date_values = pd.to_datetime(frame.loc[index, date_col], errors="coerce").dt.normalize()
        next_day = local_hhmm.eq(2400)
        local_hhmm = local_hhmm.mask(next_day, 0)
        date_values = date_values + pd.to_timedelta(next_day.astype("int8"), unit="D")
        local_naive = (
            date_values + pd.to_timedelta(local_hhmm // 100, unit="h")
            + pd.to_timedelta(local_hhmm % 100, unit="m")
        )
        localized = local_naive.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
        result.loc[index] = localized
    return result

# Second streaming pass: retain only completed arrival events for sampled tail numbers.
event_parts = []
event_columns = ["FlightDate", "Tail_Number", "Origin", "DepTime", "ArrDelay", "ActualElapsedTime", "Cancelled", "Diverted"]
for archive in bts_archives:
    with ZipFile(archive) as zf:
        csv_members = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if not csv_members:
            continue
        with zf.open(csv_members[0]) as source:
            for chunk in pd.read_csv(source, usecols=event_columns, chunksize=200_000, low_memory=False):
                chunk = chunk.loc[
                    chunk["Tail_Number"].astype(str).isin(sample_tails)
                    & pd.to_numeric(chunk["Cancelled"], errors="coerce").fillna(1).eq(0)
                    & pd.to_numeric(chunk["Diverted"], errors="coerce").fillna(0).eq(0)
                ].copy()
                if chunk.empty:
                    continue
                chunk["ArrDelay"] = pd.to_numeric(chunk["ArrDelay"], errors="coerce")
                chunk["ActualElapsedTime"] = pd.to_numeric(chunk["ActualElapsedTime"], errors="coerce")
                chunk = chunk.dropna(subset=["ArrDelay", "ActualElapsedTime", "DepTime"])
                chunk["departure_utc"] = _local_time_to_utc(chunk, "Origin", "FlightDate", "DepTime")
                chunk = chunk.dropna(subset=["departure_utc"])
                chunk["arrival_utc"] = chunk["departure_utc"] + pd.to_timedelta(chunk["ActualElapsedTime"], unit="m")
                event_parts.append(chunk[["Tail_Number", "arrival_utc", "ArrDelay"]])
if not event_parts:
    raise RuntimeError("No valid prior-aircraft arrival events were found for matched sampled tails.")
events = pd.concat(event_parts, ignore_index=True)
events["Tail_Number"] = events["Tail_Number"].astype(str)
events = events.drop_duplicates().sort_values("arrival_utc").reset_index(drop=True)

# Compute forecast cutoff timestamps in the origin airport's local timezone, then convert to UTC.
pilot["scheduled_utc"] = _local_time_to_utc(pilot, "ORIGIN", "FL_DATE", "CRS_DEP_TIME")
pilot["cutoff_utc"] = pilot["scheduled_utc"] - pd.to_timedelta(pilot["horizon_min"], unit="m")
left = pilot.dropna(subset=["cutoff_utc", "Tail_Number"]).copy()
left["Tail_Number"] = left["Tail_Number"].astype(str)
left = left.sort_values("cutoff_utc")
events = events.sort_values("arrival_utc")
left = pd.merge_asof(
    left, events, left_on="cutoff_utc", right_on="arrival_utc", by="Tail_Number",
    direction="backward", allow_exact_matches=False,
)
left["prior_tail_arrival_available"] = left["arrival_utc"].notna().astype("int8")
left["minutes_since_prior_tail_arrival"] = (
    (left["cutoff_utc"] - left["arrival_utc"]).dt.total_seconds() / 60
).clip(lower=0, upper=10_080)
left["prior_tail_arr_delay_min"] = left["ArrDelay"]
# Missing chain history is retained as missing for tree-model imputation via a sentinel plus flag.
left["minutes_since_prior_tail_arrival"] = left["minutes_since_prior_tail_arrival"].fillna(10_080)
left["prior_tail_arr_delay_min"] = left["prior_tail_arr_delay_min"].fillna(0).clip(-120, 1_000)

# Train matched-sample baselines and aircraft-augmented models separately per horizon.
def _best_threshold(y_true, probability):
    precision, recall, thresholds = precision_recall_curve(y_true, probability)
    f1 = 2 * precision[:-1] * recall[:-1] / (precision[:-1] + recall[:-1] + 1e-12)
    i = int(np.nanargmax(f1))
    return float(thresholds[i])

def _score(y_true, probability, threshold, label, horizon, n):
    prediction = (probability >= threshold).astype("int8")
    return {
        "Forecast": label, "Horizon_minutes": horizon, "Test_sample_n": int(n),
        "Threshold_from_September": threshold,
        "Accuracy": accuracy_score(y_true, prediction),
        "Precision": precision_score(y_true, prediction, zero_division=0),
        "Recall": recall_score(y_true, prediction, zero_division=0),
        "F1": f1_score(y_true, prediction, zero_division=0),
        "Balanced_accuracy": balanced_accuracy_score(y_true, prediction),
        "PR_AUC": average_precision_score(y_true, probability),
    }

results = []
for horizon_min in (120, 30):
    horizon_data = left.loc[left["horizon_min"].eq(horizon_min)].copy()
    train = horizon_data.loc[horizon_data["period"].eq("train")].dropna(subset=["Tail_Number"])
    calibration = horizon_data.loc[horizon_data["period"].eq("calibration")].dropna(subset=["Tail_Number"])
    test = horizon_data.loc[horizon_data["period"].eq("test")].dropna(subset=["Tail_Number"])
    if min(len(train), len(calibration), len(test)) < 100:
        print(f"Skipping T-{horizon_min}m: too few uniquely matched rows after time conversion.")
        continue
    numeric = BASE_NUMERIC + AIRCRAFT_NUMERIC
    categorical = BASE_CATEGORICAL
    model_input = numeric + categorical
    for label, use_aircraft in (("Schedule/route baseline", False), ("Schedule/route + prior aircraft arrival", True)):
        cols = model_input if use_aircraft else BASE_NUMERIC + BASE_CATEGORICAL
        transformer = ColumnTransformer([
            ("numeric", "passthrough", [c for c in cols if c in numeric]),
            ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=True), [c for c in cols if c in categorical]),
        ])
        model = Pipeline([
            ("preprocess", transformer),
            ("classifier", RandomForestClassifier(
                n_estimators=50, max_depth=12, min_samples_leaf=10,
                class_weight="balanced_subsample", n_jobs=-1, random_state=RANDOM_STATE,
            )),
        ])
        model.fit(train[cols], train["delay_15"].astype(int))
        calibration_probability = model.predict_proba(calibration[cols])[:, 1]
        threshold = _best_threshold(calibration["delay_15"].astype(int), calibration_probability)
        test_probability = model.predict_proba(test[cols])[:, 1]
        results.append(_score(test["delay_15"].astype(int), test_probability, threshold, label, horizon_min, len(test)))

pilot_results = pd.DataFrame(results)
if pilot_results.empty:
    raise RuntimeError("No horizon completed: inspect tail-match, timezone, and sample coverage diagnostics.")
OUTPUT_DIR = Path("/content")
results_path = OUTPUT_DIR / "aviation_aircraft_rotation_pilot_results.csv"
features_path = OUTPUT_DIR / "aviation_aircraft_rotation_pilot_sample_features.csv"
diagnostics_path = OUTPUT_DIR / "aviation_aircraft_rotation_pilot_diagnostics.json"
pilot_results.to_csv(results_path, index=False)
left.to_csv(features_path, index=False)
def _build_rotation_diagnostics(pilot, left, join_keys, key_tail_counts, ambiguous_keys, unique_keys):
    return {
    "status": "RETROSPECTIVE_PILOT_DIAGNOSTICS",
    "sample_rows": int(len(pilot)),
    "sample_rows_by_horizon_and_period": {
        f"{int(horizon)}m/{period}": int(count)
        for (horizon, period), count in pilot.groupby(["horizon_min", "period"], observed=True).size().items()
    },
    "distinct_sample_join_keys": int(len(join_keys)),
    "distinct_unique_tail_keys": int(len(unique_keys)),
    "distinct_ambiguous_tail_keys": int(len(ambiguous_keys)),
    "distinct_unmatched_tail_keys": int(len(join_keys - set(key_tail_counts.index))),
    "rows_with_unique_tail_match": int(pilot["Tail_Number"].notna().sum()),
    "rows_with_valid_scheduled_cutoff_utc": int(pilot["scheduled_utc"].notna().sum()),
    "rows_retained_after_unique_tail_and_valid_cutoff_filters": int(len(left)),
    "prior_arrival_feature_coverage_among_retained_rows": float(left["prior_tail_arrival_available"].mean()),
    "coverage_denominator_note": "Prior-arrival coverage is conditional on a unique tail match and valid scheduled cutoff UTC; it is not coverage over all sampled eligible rows.",
    "operational_availability": "Unverified: retrospective BTS tail assignments and arrival outcomes are not timestamped as prediction-time observations.",
    }


diagnostics = _build_rotation_diagnostics(pilot, left, join_keys, key_tail_counts, ambiguous_keys, unique_keys)
diagnostics_path.write_text(json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8")
print("Sampled eligible rows:", len(pilot))
print("Unique tail matches:", diagnostics["rows_with_unique_tail_match"], "| distinct ambiguous keys:", len(ambiguous_keys), "| distinct unmatched keys:", diagnostics["distinct_unmatched_tail_keys"])
print("Rows with valid scheduled cutoff UTC:", diagnostics["rows_with_valid_scheduled_cutoff_utc"], "| retained for model/features:", len(left))
print("Prior-arrival feature coverage among retained rows:", f"{left['prior_tail_arrival_available'].mean():.2%}")
print("Monthly BTS archives read:", len(bts_archives))
print("Saved:", results_path, features_path, "and", diagnostics_path)
display(pilot_results.round(4))
