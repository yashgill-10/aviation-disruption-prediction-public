"""Train schedule-only, history-augmented, or METAR-augmented DepDel15 models.

Both modes train on 2022-2023 and evaluate on 2024. The script never opens the
2025 test directory. SGD logistic regression and the feature encoder stream
Parquet files in batches so the full dataset does not need to fit in memory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.special import expit
from scipy.sparse import csr_matrix, hstack
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    precision_recall_curve,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import OneHotEncoder

CATEGORICAL_FEATURES = ["Reporting_Airline", "Origin", "Dest"]
BASE_PARQUET_COLUMNS = [
    "FlightDate",
    *CATEGORICAL_FEATURES,
    "CRSDepTime",
    "CRSArrTime",
    "Distance",
    "CRSElapsedTime",
    "DepDel15",
]
NUMERIC_FEATURES = [
    "dep_hour_sin",
    "dep_hour_cos",
    "dep_minute_scaled",
    "dep_time_missing",
    "arr_hour_sin",
    "arr_hour_cos",
    "arr_minute_scaled",
    "arr_time_missing",
    "weekday_sin",
    "weekday_cos",
    "month_sin",
    "month_cos",
    "distance_scaled",
    "distance_missing",
    "elapsed_scaled",
    "elapsed_missing",
    "date_missing",
]
HISTORICAL_FEATURES = [
    "prev3m_global_delay_rate",
    "prev3m_global_log_flights",
    "prev3m_airline_delay_rate",
    "prev3m_airline_log_flights",
    "prev3m_origin_delay_rate",
    "prev3m_origin_log_flights",
    "prev3m_route_delay_rate",
    "prev3m_route_log_flights",
    "prev3m_history_missing",
] + [
    f"prev{window}d_{group}_{suffix}"
    for window in (7, 30)
    for group in ("global", "airline", "origin", "route", "origin_hour")
    for suffix in ("delay_rate", "log_flights")
] + ["prev7d_history_missing", "prev30d_history_missing"]
WEATHER_VALUE_FEATURES = {
    "temperature_c": 50.0,
    "dew_point_temperature_c": 50.0,
    "wind_speed_m_s": 60.0,
    "wind_gust_m_s": 60.0,
    "visibility_km": 20.0,
    "sea_level_pressure_hpa": 1200.0,
}
WEATHER_FLAG_FEATURES = [
    "weather_rain",
    "weather_snow",
    "weather_fog_or_mist",
    "weather_thunderstorm",
]
WEATHER_ENDPOINTS = ["origin", "destination"]
WEATHER_PARQUET_COLUMNS = ["origin_weather_age_minutes", "destination_weather_age_minutes"]
for _endpoint in WEATHER_ENDPOINTS:
    WEATHER_PARQUET_COLUMNS.append(f"{_endpoint}_weather_missing")
    WEATHER_PARQUET_COLUMNS.append(f"{_endpoint}_wind_direction_deg_fresh")
    for _field in WEATHER_VALUE_FEATURES:
        WEATHER_PARQUET_COLUMNS.append(f"{_endpoint}_{_field}_fresh")
    for _field in WEATHER_FLAG_FEATURES:
        WEATHER_PARQUET_COLUMNS.append(f"{_endpoint}_{_field}_fresh")
CLASSES = np.array([0, 1], dtype=np.int8)
MIN_FREQUENCY = 20


def expected_month_files(data_dir: Path, split: str, years: tuple[int, ...]) -> list[Path]:
    """Return the exact expected months; never glob or inspect the test split."""
    split_dir = data_dir / "flights" / split
    expected = [
        split_dir / f"{year}-{month:02d}.parquet"
        for year in years
        for month in range(1, 13)
    ]
    missing = [path.name for path in expected if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"{split} split is incomplete; missing {len(missing)} month(s): {', '.join(missing)}"
        )
    return expected


def iter_frames(path: Path, columns: list[str], batch_size: int):
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
        yield batch.to_pandas()


def clean_categories(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame[CATEGORICAL_FEATURES].copy()
    for column in CATEGORICAL_FEATURES:
        values = result[column].astype("string").str.strip()
        values = values.mask(values.eq(""), pd.NA)
        result[column] = values.fillna("UNKNOWN").astype(str)
    return result


def parse_hhmm(series: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parse local scheduled HHMM values, including BTS 2400 as midnight."""
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
    valid = np.isfinite(values) & (values >= 0) & (values <= 2400) & ((values % 100) < 60)
    hours = np.where(valid, np.floor(values / 100) % 24, np.nan)
    minutes = np.where(valid, values % 100, np.nan)
    return hours, minutes, ~valid


def _cyclic(values: np.ndarray, period: float, offset: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    angle = (values - offset) * (2 * np.pi / period)
    return np.sin(angle), np.cos(angle)


def numeric_features(frame: pd.DataFrame) -> np.ndarray:
    """Encode only pre-departure schedule and calendar information."""
    dep_hour, dep_minute, dep_missing = parse_hhmm(frame["CRSDepTime"])
    arr_hour, arr_minute, arr_missing = parse_hhmm(frame["CRSArrTime"])

    dates = pd.to_datetime(frame["FlightDate"], errors="coerce")
    weekday = dates.dt.dayofweek.to_numpy(dtype=np.float64, na_value=np.nan)
    month = dates.dt.month.to_numpy(dtype=np.float64, na_value=np.nan)
    date_missing = ~np.isfinite(weekday) | ~np.isfinite(month)
    weekday = np.nan_to_num(weekday, nan=0.0)
    month = np.nan_to_num(month, nan=1.0)

    dep_sin, dep_cos = _cyclic(np.nan_to_num(dep_hour, nan=0.0), 24)
    arr_sin, arr_cos = _cyclic(np.nan_to_num(arr_hour, nan=0.0), 24)
    weekday_sin, weekday_cos = _cyclic(weekday, 7)
    month_sin, month_cos = _cyclic(month, 12, offset=1)

    distance = pd.to_numeric(frame["Distance"], errors="coerce").to_numpy(
        dtype=np.float64, na_value=np.nan
    )
    elapsed = pd.to_numeric(frame["CRSElapsedTime"], errors="coerce").to_numpy(
        dtype=np.float64, na_value=np.nan
    )
    distance_missing = ~np.isfinite(distance)
    elapsed_missing = ~np.isfinite(elapsed)

    values = np.column_stack(
        [
            dep_sin,
            dep_cos,
            np.nan_to_num(dep_minute / 59, nan=0.0),
            dep_missing.astype(np.float64),
            arr_sin,
            arr_cos,
            np.nan_to_num(arr_minute / 59, nan=0.0),
            arr_missing.astype(np.float64),
            weekday_sin,
            weekday_cos,
            month_sin,
            month_cos,
            np.nan_to_num(distance / 5000, nan=0.0, posinf=0.0, neginf=0.0),
            distance_missing.astype(np.float64),
            np.nan_to_num(elapsed / 1000, nan=0.0, posinf=0.0, neginf=0.0),
            elapsed_missing.astype(np.float64),
            date_missing.astype(np.float64),
        ]
    )
    return values.astype(np.float32, copy=False)


def fit_encoder(train_files: list[Path], rows_per_month: int) -> OneHotEncoder:
    """Fit category vocabulary using a small sample from every training month."""
    samples = []
    for path in train_files:
        batches = iter_frames(path, CATEGORICAL_FEATURES, rows_per_month)
        first_batch = next(batches, None)
        if first_batch is not None and not first_batch.empty:
            samples.append(clean_categories(first_batch))
    if not samples:
        raise ValueError("No training rows were available to fit the categorical encoder.")

    # Make an explicit missing-value category available even when the sample has none.
    unknown_rows = pd.DataFrame({column: ["UNKNOWN"] * MIN_FREQUENCY for column in CATEGORICAL_FEATURES})
    sample = pd.concat([*samples, unknown_rows], ignore_index=True)
    encoder = OneHotEncoder(
        handle_unknown="ignore",
        min_frequency=MIN_FREQUENCY,
        sparse_output=True,
        dtype=np.float32,
    )
    encoder.fit(sample)
    return encoder


def encode_frame(
    frame: pd.DataFrame,
    encoder: OneHotEncoder,
    include_historical: bool = False,
    include_weather: bool = False,
) -> tuple[csr_matrix, np.ndarray]:
    categories = clean_categories(frame)
    categorical_matrix = encoder.transform(categories)
    numeric = numeric_features(frame)
    if include_historical:
        missing = [column for column in HISTORICAL_FEATURES if column not in frame]
        if missing:
            raise ValueError(f"Historical features are missing columns: {', '.join(missing)}")
        historical = frame[HISTORICAL_FEATURES].apply(pd.to_numeric, errors="coerce")
        historical_values = historical.to_numpy(dtype=np.float64, na_value=np.nan)
        historical_values = np.nan_to_num(
            historical_values, nan=0.0, posinf=0.0, neginf=0.0
        ).astype(np.float32, copy=False)
        numeric = np.column_stack([numeric, historical_values]).astype(np.float32, copy=False)
    if include_weather:
        weather_values, _ = weather_numeric_features(frame)
        numeric = np.column_stack([numeric, weather_values]).astype(np.float32, copy=False)
    numeric_matrix = csr_matrix(numeric)
    features = hstack([categorical_matrix, numeric_matrix], format="csr").astype(
        np.float32, copy=False
    )
    target = pd.to_numeric(frame["DepDel15"], errors="coerce").to_numpy(dtype=np.int8)
    if not np.isin(target, CLASSES).all():
        raise ValueError("Processed Parquet contains a nonbinary or missing DepDel15 target.")
    return features, target


def weather_numeric_features(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """Scale observed weather and add explicit field-level missingness flags."""
    columns: list[np.ndarray] = []
    names: list[str] = []

    def numeric_column(name: str) -> np.ndarray:
        return pd.to_numeric(frame[name], errors="coerce").to_numpy(
            dtype=np.float64, na_value=np.nan
        )

    for endpoint in WEATHER_ENDPOINTS:
        for field, scale in WEATHER_VALUE_FEATURES.items():
            name = f"{endpoint}_{field}_fresh"
            values = numeric_column(name)
            missing = ~np.isfinite(values)
            columns.extend(
                [
                    np.nan_to_num(values / scale, nan=0.0, posinf=0.0, neginf=0.0),
                    missing.astype(np.float64),
                ]
            )
            names.extend([name, f"{name}_missing"])

        direction_name = f"{endpoint}_wind_direction_deg_fresh"
        direction = numeric_column(direction_name)
        direction_missing = ~np.isfinite(direction)
        angle = np.deg2rad(np.nan_to_num(direction, nan=0.0) % 360.0)
        columns.extend(
            [
                np.sin(angle),
                np.cos(angle),
                direction_missing.astype(np.float64),
            ]
        )
        names.extend(
            [f"{endpoint}_wind_direction_sin", f"{endpoint}_wind_direction_cos", f"{direction_name}_missing"]
        )

        for field in WEATHER_FLAG_FEATURES:
            name = f"{endpoint}_{field}_fresh"
            values = numeric_column(name)
            missing = ~np.isfinite(values)
            columns.extend(
                [
                    np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0),
                    missing.astype(np.float64),
                ]
            )
            names.extend([name, f"{name}_missing"])

        age_name = f"{endpoint}_weather_age_minutes"
        age = numeric_column(age_name)
        missing_age = ~np.isfinite(age)
        columns.extend(
            [
                np.nan_to_num(age / 180.0, nan=0.0, posinf=0.0, neginf=0.0),
                missing_age.astype(np.float64),
                numeric_column(f"{endpoint}_weather_missing"),
            ]
        )
        names.extend([f"{endpoint}_weather_age_scaled", f"{endpoint}_weather_age_missing", f"{endpoint}_weather_missing"])

    result = np.column_stack(columns).astype(np.float32, copy=False)
    result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    return result, names


def score_predictions(
    y_true: np.ndarray, probability: np.ndarray, threshold: float = 0.5
) -> dict[str, object]:
    prediction = (probability >= threshold).astype(np.int8)
    tn, fp, fn, tp = confusion_matrix(y_true, prediction, labels=[0, 1]).ravel()
    return {
        "rows": int(len(y_true)),
        "positive_prevalence": float(np.mean(y_true)),
        "threshold": float(threshold),
        "accuracy": float(accuracy_score(y_true, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, prediction)),
        "precision": float(precision_score(y_true, prediction, zero_division=0)),
        "recall": float(recall_score(y_true, prediction, zero_division=0)),
        "f1": float(f1_score(y_true, prediction, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "average_precision": float(average_precision_score(y_true, probability)),
        "brier_score": float(brier_score_loss(y_true, probability)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def calibration_bins(y_true: np.ndarray, probability: np.ndarray, bins: int = 10) -> list[dict[str, object]]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    assignments = np.minimum(np.searchsorted(edges, probability, side="right") - 1, bins - 1)
    output = []
    for index in range(bins):
        selected = assignments == index
        if selected.any():
            output.append({
                "lower_bound": float(edges[index]),
                "upper_bound": float(edges[index + 1]),
                "rows": int(selected.sum()),
                "mean_predicted_probability": float(probability[selected].mean()),
                "observed_positive_rate": float(y_true[selected].mean()),
            })
    return output


def error_analysis(
    y_true: np.ndarray,
    probability: np.ndarray,
    context: pd.DataFrame,
    threshold: float,
    include_weather: bool,
    min_group_rows: int = 5_000,
) -> dict[str, object]:
    """Summarize calibrated performance by time, carrier, airport, and weather availability."""
    groups: dict[str, pd.Series] = {
        "month": context["month"].astype("string"),
        "airline": context["Reporting_Airline"].fillna("UNKNOWN").astype("string"),
        "origin_airport": context["Origin"].fillna("UNKNOWN").astype("string"),
    }
    hours, _, missing = parse_hhmm(context["CRSDepTime"])
    groups["scheduled_departure_hour"] = pd.Series(
        np.where(missing, "unknown", np.nan_to_num(np.floor(hours), nan=-1).astype(int).astype(str)),
        index=context.index,
        dtype="string",
    )
    if include_weather:
        origin_missing = pd.to_numeric(context["origin_weather_missing"], errors="coerce").fillna(1).astype(bool)
        destination_missing = pd.to_numeric(context["destination_weather_missing"], errors="coerce").fillna(1).astype(bool)
        availability = np.select(
            [origin_missing & destination_missing, origin_missing, destination_missing],
            ["both_missing", "origin_missing", "destination_missing"],
            default="both_available",
        )
        groups["weather_availability"] = pd.Series(availability, index=context.index, dtype="string")

    result: dict[str, object] = {
        "period": "2024-07 through 2024-12",
        "probabilities": "Platt calibrated on 2024-01 through 2024-06",
        "threshold": float(threshold),
        "minimum_group_rows": min_group_rows,
        "segments": {},
    }
    segment_results: dict[str, list[dict[str, object]]] = {}
    for group_name, labels in groups.items():
        value_counts = labels.value_counts(dropna=False)
        if group_name == "origin_airport":
            value_counts = value_counts.head(20)
        rows = []
        for value, count in value_counts.items():
            if count < min_group_rows:
                continue
            selected = labels.eq(value).to_numpy()
            if np.unique(y_true[selected]).size < 2:
                continue
            try:
                scores = score_predictions(y_true[selected], probability[selected], threshold)
            except ValueError:
                continue
            rows.append({"group": str(value), **scores})
        segment_results[group_name] = rows
    result["segments"] = segment_results
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/processed/depdel15")
    parser.add_argument(
        "--historical-features-dir",
        default=None,
        help="optional root of build_historical_features.py output; uses 3-month, 7-day, 30-day, and time-of-day history",
    )
    parser.add_argument(
        "--weather-features-dir",
        default=None,
        help="optional root for one horizon of build_ghcnh_weather_features.py output; includes history and METARs",
    )
    parser.add_argument("--output-dir", default="data/processed/depdel15/baseline")
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--sample-rows-per-month", type=int, default=20_000)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--alpha", type=float, default=1e-5, help="L2 regularization for the streaming logistic classifier")
    parser.add_argument("--overwrite", action="store_true", help="replace existing baseline outputs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size < 1 or args.sample_rows_per_month < 1 or args.epochs < 1 or not np.isfinite(args.alpha) or args.alpha <= 0:
        print("ERROR: batch size, sample size, epochs, and alpha must be positive.", file=sys.stderr)
        return 2

    if args.historical_features_dir and args.weather_features_dir:
        print("ERROR: choose either --historical-features-dir or --weather-features-dir.", file=sys.stderr)
        return 2
    data_dir = Path(args.weather_features_dir or args.historical_features_dir or args.data_dir).resolve()
    include_weather = args.weather_features_dir is not None
    include_historical = args.historical_features_dir is not None or include_weather
    parquet_columns = BASE_PARQUET_COLUMNS + (
        HISTORICAL_FEATURES if include_historical else []
    ) + (WEATHER_PARQUET_COLUMNS if include_weather else [])
    output_dir = Path(args.output_dir).resolve()
    try:
        train_files = expected_month_files(data_dir, "train", (2022, 2023))
        validation_files = expected_month_files(data_dir, "validation", (2024,))
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    metrics_path = output_dir / "metrics.json"
    model_name = (
        "weather_logistic_sgd.joblib" if include_weather
        else "historical_logistic_sgd.joblib" if include_historical
        else "schedule_logistic_sgd.joblib"
    )
    model_path = output_dir / model_name
    if not args.overwrite and (metrics_path.exists() or model_path.exists()):
        print(
            f"ERROR: baseline outputs already exist in {output_dir}; choose another --output-dir "
            "or pass --overwrite.",
            file=sys.stderr,
        )
        return 2

    experiment_name = (
        "schedule + historical delay + point-in-time METAR" if include_weather
        else "schedule + monthly/daily historical delay" if include_historical
        else "schedule-only"
    )
    print(f"=== {experiment_name} BTS baseline ===", flush=True)
    print("Training years: 2022-2023", flush=True)
    print("Validation year: 2024", flush=True)
    print("The 2025 test directory will not be opened.", flush=True)
    print(f"Fitting categorical encoder from up to {args.sample_rows_per_month:,} rows per training month...", flush=True)
    encoder = fit_encoder(train_files, args.sample_rows_per_month)
    print(f"Categorical feature count: {sum(len(categories) for categories in encoder.categories_):,}", flush=True)

    classifier = SGDClassifier(
        loss="log_loss",
        penalty="l2",
        alpha=args.alpha,
        learning_rate="optimal",
        average=True,
        random_state=42,
    )
    rng = np.random.default_rng(42)
    train_rows = 0
    train_counts = np.zeros(2, dtype=np.int64)
    train_min = train_max = None
    initialized = False

    for epoch in range(1, args.epochs + 1):
        print(f"Training epoch {epoch}/{args.epochs}...", flush=True)
        for month_index, path in enumerate(train_files, 1):
            for frame in iter_frames(path, parquet_columns, args.batch_size):
                features, target = encode_frame(frame, encoder, include_historical, include_weather)
                order = rng.permutation(len(target))
                features = features[order]
                target = target[order]
                if not initialized:
                    classifier.partial_fit(features, target, classes=CLASSES)
                    initialized = True
                else:
                    classifier.partial_fit(features, target)
                if epoch == 1:
                    train_rows += len(target)
                    train_counts += np.bincount(target, minlength=2)
                    dates = pd.to_datetime(frame["FlightDate"], errors="coerce")
                    batch_min, batch_max = dates.min(), dates.max()
                    if pd.notna(batch_min) and (train_min is None or batch_min < train_min):
                        train_min = batch_min
                    if pd.notna(batch_max) and (train_max is None or batch_max > train_max):
                        train_max = batch_max
            print(f"  epoch {epoch}: completed {path.stem} ({month_index}/{len(train_files)})", flush=True)

    validation_y_parts = []
    validation_score_parts = []
    calibration_y_parts = []
    calibration_score_parts = []
    evaluation_y_parts = []
    evaluation_score_parts = []
    evaluation_context_parts = []
    validation_rows = 0
    validation_min = validation_max = None
    for month_index, path in enumerate(validation_files, 1):
        for frame in iter_frames(path, parquet_columns, args.batch_size):
            features, target = encode_frame(frame, encoder, include_historical, include_weather)
            decision_score = classifier.decision_function(features).astype(np.float32, copy=False)
            validation_y_parts.append(target)
            validation_score_parts.append(decision_score)
            month = int(path.stem[-2:])
            if month <= 6:
                calibration_y_parts.append(target)
                calibration_score_parts.append(decision_score)
            else:
                evaluation_y_parts.append(target)
                evaluation_score_parts.append(decision_score)
                context_columns = ["Reporting_Airline", "Origin", "CRSDepTime"]
                if include_weather:
                    context_columns.extend(["origin_weather_missing", "destination_weather_missing"])
                context = frame[context_columns].copy()
                context["month"] = path.stem[-2:]
                evaluation_context_parts.append(context)
            validation_rows += len(target)
            dates = pd.to_datetime(frame["FlightDate"], errors="coerce")
            batch_min, batch_max = dates.min(), dates.max()
            if pd.notna(batch_min) and (validation_min is None or batch_min < validation_min):
                validation_min = batch_min
            if pd.notna(batch_max) and (validation_max is None or batch_max > validation_max):
                validation_max = batch_max
        print(f"  scored {path.stem} ({month_index}/{len(validation_files)})", flush=True)

    y_valid = np.concatenate(validation_y_parts)
    validation_scores = np.concatenate(validation_score_parts)
    logistic_probability = expit(validation_scores).astype(np.float32, copy=False)
    calibration_y = np.concatenate(calibration_y_parts)
    calibration_scores = np.concatenate(calibration_score_parts)
    evaluation_y = np.concatenate(evaluation_y_parts)
    evaluation_scores = np.concatenate(evaluation_score_parts)
    evaluation_context = pd.concat(evaluation_context_parts, ignore_index=True)
    train_prevalence = float(train_counts[1] / train_rows)
    prior_probability = np.full(len(y_valid), train_prevalence, dtype=np.float32)

    calibrator = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000, random_state=42)
    calibrator.fit(calibration_scores.reshape(-1, 1), calibration_y)
    calibrated_on_calibration = calibrator.predict_proba(calibration_scores.reshape(-1, 1))[:, 1]
    calibrated_on_evaluation = calibrator.predict_proba(evaluation_scores.reshape(-1, 1))[:, 1]
    calibration_precision, calibration_recall, candidate_thresholds = precision_recall_curve(
        calibration_y, calibrated_on_calibration
    )
    candidate_f1 = (
        2 * calibration_precision[:-1] * calibration_recall[:-1]
        / np.maximum(calibration_precision[:-1] + calibration_recall[:-1], 1e-12)
    )
    selected_threshold = float(candidate_thresholds[int(np.argmax(candidate_f1))])
    evaluation_raw_probability = expit(evaluation_scores).astype(np.float32, copy=False)
    evaluation_prior_probability = np.full(len(evaluation_y), train_prevalence, dtype=np.float32)
    weather_numeric_feature_names = (
        weather_numeric_features(
            pd.DataFrame({name: pd.Series(dtype="float64") for name in WEATHER_PARQUET_COLUMNS})
        )[1]
        if include_weather
        else []
    )

    metrics = {
        "purpose": (
            "Development experiment using scheduled/calendar, prior-three-month historical, and lagged METAR features; not final test evaluation."
            if include_weather
            else
            "Development experiment using scheduled/calendar, prior-three-month, and prior-7/30-day historical features; "
            "not final test evaluation."
            if include_historical
            else "Development baseline using scheduled/calendar features; not final test evaluation."
        ),
        "target": "DepDel15",
        "data_dir": str(data_dir),
        "train_period": [train_min.strftime("%Y-%m-%d"), train_max.strftime("%Y-%m-%d")],
        "validation_period": [validation_min.strftime("%Y-%m-%d"), validation_max.strftime("%Y-%m-%d")],
        "train_rows": int(train_rows),
        "validation_rows": int(validation_rows),
        "train_target_counts": {str(index): int(count) for index, count in enumerate(train_counts)},
        "train_positive_prevalence": train_prevalence,
        "features": {
            "categorical": CATEGORICAL_FEATURES,
            "numeric": NUMERIC_FEATURES
            + (HISTORICAL_FEATURES if include_historical else [])
            + weather_numeric_feature_names,
            "availability": (
                "scheduled/calendar, prior-three-completed-month and prior-7/30-day historical statistics, and latest METAR available by the prediction cutoff"
                if include_weather
                else
                "scheduled/calendar, prior-three-completed-month and prior-7/30-day historical statistics"
                if include_historical
                else "scheduled and calendar information only"
            ),
        },
        "models": {
            "train_prior_baseline": score_predictions(y_valid, prior_probability),
            "streaming_logistic_regression": score_predictions(y_valid, logistic_probability),
        },
        "temporal_calibration_check": {
            "method": "Platt scaling fitted on 2024-01 through 2024-06 decision scores",
            "calibration_rows": int(len(calibration_y)),
            "calibration_positive_prevalence": float(np.mean(calibration_y)),
            "f1_threshold_selected_on_calibration_period": selected_threshold,
            "evaluation_period": "2024-07 through 2024-12",
            "evaluation_models": {
                "train_prior_baseline": score_predictions(
                    evaluation_y, evaluation_prior_probability
                ),
                "logistic_uncalibrated_at_0.5": score_predictions(
                    evaluation_y, evaluation_raw_probability
                ),
                "logistic_platt_calibrated_at_0.5": score_predictions(
                    evaluation_y, calibrated_on_evaluation
                ),
                "logistic_platt_calibrated_at_calibration_f1_threshold": score_predictions(
                    evaluation_y, calibrated_on_evaluation, selected_threshold
                ),
            },
            "calibration_bins_on_evaluation": calibration_bins(
                evaluation_y, calibrated_on_evaluation
            ),
            "error_analysis": error_analysis(
                evaluation_y,
                calibrated_on_evaluation,
                evaluation_context,
                selected_threshold,
                include_weather,
            ),
        },
        "training": {
            "algorithm": "SGDClassifier(loss='log_loss')",
            "epochs": args.epochs,
            "alpha": args.alpha,
            "batch_size": args.batch_size,
            "encoder_sample_rows_per_month": args.sample_rows_per_month,
            "test_2025_read": False,
            "historical_features_included": include_historical,
            "weather_features_included": include_weather,
            "weather_horizon": Path(args.weather_features_dir).name if include_weather else None,
        },
        "limitations": [
            *([] if include_historical else ["Historical delay rates are not included."]),
            *([] if include_weather else ["Observed weather features are not included."]),
            "Weather inputs are observed GHCNh METAR values available by the scheduled-departure cutoff; they are not archived forecasts for future conditions.",
            "Historical outcome features use BTS labels from earlier flights; live deployment freshness depends on how quickly recent outcomes become available.",
            "The temporal calibration and threshold check uses the first half of 2024 for fitting and the second half for evaluation; the selected threshold is not a final operating policy.",
            "The 2025 partition is not read during this training run. It was opened for an earlier frozen-baseline exploration, so it is not a fully blind test and must not guide this model's tuning.",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = {
        "encoder": encoder,
        "classifier": classifier,
        "calibrator": calibrator,
        "f1_threshold_selected_on_2024_first_half": selected_threshold,
        "categorical_features": CATEGORICAL_FEATURES,
        "numeric_features": NUMERIC_FEATURES,
        "historical_features": HISTORICAL_FEATURES if include_historical else [],
        "weather_features": WEATHER_PARQUET_COLUMNS if include_weather else [],
        "weather_numeric_features": weather_numeric_feature_names,
        "target": "DepDel15",
        "train_years": [2022, 2023],
        "validation_year": 2024,
    }
    joblib.dump(artifact, model_path, compress=3)
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    print("\n=== 2024 validation results ===", flush=True)
    for name, result in metrics["models"].items():
        print(
            f"{name}: AP={result['average_precision']:.4f}, ROC-AUC={result['roc_auc']:.4f}, "
            f"recall@0.5={result['recall']:.4f}, Brier={result['brier_score']:.4f}",
            flush=True,
        )
    for name, result in metrics["temporal_calibration_check"]["evaluation_models"].items():
        print(
            f"Jul-Dec {name}: AP={result['average_precision']:.4f}, "
            f"ROC-AUC={result['roc_auc']:.4f}, recall={result['recall']:.4f}, "
            f"Brier={result['brier_score']:.4f}",
            flush=True,
        )
    print(f"F1 threshold selected on Jan-Jun 2024: {selected_threshold:.4f}", flush=True)
    print(f"Train rows: {train_rows:,}; validation rows: {validation_rows:,}", flush=True)
    print(f"Metrics: {metrics_path}", flush=True)
    print(f"Model: {model_path}", flush=True)
    print("The 2025 test split was not read.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
