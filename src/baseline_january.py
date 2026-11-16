"""Train a leakage-conscious development baseline on BTS January 2024 data.

This script is a pipeline smoke test, not final model evaluation. It uses
Jan 1-24 for training and Jan 25-31 for a chronological development holdout.
It excludes cancelled flights, uses only scheduled / calendar features, and
leaves the source CSV unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

REQUIRED_COLUMNS = [
    "FlightDate",
    "Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "CRSArrTime",
    "Cancelled",
    "DepDel15",
    "Distance",
    "CRSElapsedTime",
]
CATEGORICAL_FEATURES = ["Reporting_Airline", "Origin", "Dest"]
NUMERIC_FEATURES = [
    "dep_hour_sin",
    "dep_hour_cos",
    "dep_minute",
    "arr_hour_sin",
    "arr_hour_cos",
    "arr_minute",
    "weekday_sin",
    "weekday_cos",
    "month_sin",
    "month_cos",
    "Distance",
    "CRSElapsedTime",
]


def parse_hhmm(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Parse BTS local HHMM scheduled times; invalid values become missing."""
    values = pd.to_numeric(series, errors="coerce")
    valid = values.notna() & values.between(0, 2400) & ((values % 100) < 60)
    hours = np.floor(values / 100).where(valid) % 24
    minutes = (values % 100).where(valid)
    return hours.astype("float64"), minutes.astype("float64")


def make_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create features available from schedule/calendar information only."""
    X = df[["Reporting_Airline", "Origin", "Dest"]].copy()
    for col in CATEGORICAL_FEATURES:
        X[col] = X[col].fillna("UNKNOWN").astype(str)

    dep_hour, dep_minute = parse_hhmm(df["CRSDepTime"])
    arr_hour, arr_minute = parse_hhmm(df["CRSArrTime"])
    date = df["FlightDate"]
    weekday = date.dt.dayofweek.astype("float64")
    month = date.dt.month.astype("float64")

    X["dep_hour_sin"] = np.sin(2 * np.pi * dep_hour / 24)
    X["dep_hour_cos"] = np.cos(2 * np.pi * dep_hour / 24)
    X["dep_minute"] = dep_minute
    X["arr_hour_sin"] = np.sin(2 * np.pi * arr_hour / 24)
    X["arr_hour_cos"] = np.cos(2 * np.pi * arr_hour / 24)
    X["arr_minute"] = arr_minute
    X["weekday_sin"] = np.sin(2 * np.pi * weekday / 7)
    X["weekday_cos"] = np.cos(2 * np.pi * weekday / 7)
    X["month_sin"] = np.sin(2 * np.pi * (month - 1) / 12)
    X["month_cos"] = np.cos(2 * np.pi * (month - 1) / 12)
    X["Distance"] = pd.to_numeric(df["Distance"], errors="coerce")
    X["CRSElapsedTime"] = pd.to_numeric(df["CRSElapsedTime"], errors="coerce")
    return X[CATEGORICAL_FEATURES + NUMERIC_FEATURES]


def score_predictions(y_true: np.ndarray, pred: np.ndarray, prob: np.ndarray) -> dict:
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "rows": int(len(y_true)),
        "positive_prevalence": float(np.mean(y_true)),
        "accuracy": float(accuracy_score(y_true, pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, pred)),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, prob)),
        "average_precision": float(average_precision_score(y_true, prob)),
        "brier_score": float(brier_score_loss(y_true, prob)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="Path to the original monthly BTS CSV")
    parser.add_argument(
        "--validation-start",
        default="2024-01-25",
        help="Inclusive first date for chronological validation (default: 2024-01-25)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/processed/jan2024_baseline",
        help="Directory for development metrics (default: data/processed/jan2024_baseline)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.is_file():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 2

    print("=== BTS January baseline (development only) ===", flush=True)
    print(f"Input: {csv_path.resolve()}", flush=True)
    print(f"File size: {csv_path.stat().st_size:,} bytes", flush=True)
    print("Reading required columns only...", flush=True)
    try:
        df = pd.read_csv(csv_path, usecols=REQUIRED_COLUMNS, low_memory=False)
    except Exception as exc:
        print(f"ERROR reading CSV or expected columns: {exc}", file=sys.stderr)
        return 2

    df["FlightDate"] = pd.to_datetime(df["FlightDate"], errors="coerce")
    for col in ["Cancelled", "DepDel15", "Distance", "CRSElapsedTime"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    rows_before = len(df)
    invalid_date = int(df["FlightDate"].isna().sum())
    # Exclude every cancelled flight even if BTS reports a target value for it.
    eligible = df.loc[(df["Cancelled"] == 0) & df["DepDel15"].isin([0, 1]) & df["FlightDate"].notna()].copy()
    if eligible.empty:
        print("ERROR: No eligible rows remain after target filtering.", file=sys.stderr)
        return 2

    eligible["DepDel15"] = eligible["DepDel15"].astype("int8")
    cutoff = pd.Timestamp(args.validation_start)
    train = eligible.loc[eligible["FlightDate"] < cutoff].copy()
    valid = eligible.loc[eligible["FlightDate"] >= cutoff].copy()
    if train.empty or valid.empty:
        print(
            f"ERROR: Empty train or validation split. Train rows={len(train)}, validation rows={len(valid)}; "
            f"check --validation-start ({cutoff.date()}).",
            file=sys.stderr,
        )
        return 2
    if train["DepDel15"].nunique() < 2 or valid["DepDel15"].nunique() < 2:
        print("ERROR: Both classes must be present in train and validation for this metric set.", file=sys.stderr)
        return 2

    X_train = make_features(train)
    X_valid = make_features(valid)
    y_train = train["DepDel15"].to_numpy(dtype=np.int8)
    y_valid = valid["DepDel15"].to_numpy(dtype=np.int8)

    preprocessor = ColumnTransformer(
        transformers=[
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=20)),
                    ]
                ),
                CATEGORICAL_FEATURES,
            ),
            (
                "numeric",
                Pipeline(steps=[("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]),
                NUMERIC_FEATURES,
            ),
        ],
        remainder="drop",
    )

    print(f"Rows in source file: {rows_before:,}", flush=True)
    print(f"Rows with invalid/missing flight dates: {invalid_date:,}", flush=True)
    print(f"Eligible non-cancelled rows with a valid target: {len(eligible):,}", flush=True)
    print(f"Excluded cancelled rows: {int((df['Cancelled'] == 1).sum()):,}", flush=True)
    print(f"Train: {len(train):,} rows, {train['FlightDate'].min().date()} to {train['FlightDate'].max().date()}, positive rate {y_train.mean():.2%}", flush=True)
    print(f"Validation: {len(valid):,} rows, {valid['FlightDate'].min().date()} to {valid['FlightDate'].max().date()}, positive rate {y_valid.mean():.2%}", flush=True)
    print("Training majority-class baseline...", flush=True)

    models = {
        "majority_baseline": DummyClassifier(strategy="most_frequent"),
        "logistic_regression": Pipeline(
            steps=[
                ("preprocess", preprocessor),
                ("model", LogisticRegression(solver="lbfgs", max_iter=300, random_state=42)),
            ]
        ),
    }
    all_metrics: dict[str, object] = {
        "purpose": "January 2024 pipeline smoke test; NOT final performance evaluation",
        "input_file": str(csv_path.resolve()),
        "source_rows": int(rows_before),
        "invalid_date_rows": invalid_date,
        "eligible_rows": int(len(eligible)),
        "cancelled_rows_excluded": int((df["Cancelled"] == 1).sum()),
        "validation_start": cutoff.strftime("%Y-%m-%d"),
        "train_period": [train["FlightDate"].min().strftime("%Y-%m-%d"), train["FlightDate"].max().strftime("%Y-%m-%d")],
        "validation_period": [valid["FlightDate"].min().strftime("%Y-%m-%d"), valid["FlightDate"].max().strftime("%Y-%m-%d")],
        "train_rows": int(len(train)),
        "validation_rows": int(len(valid)),
        "train_target_counts": {str(int(k)): int(v) for k, v in train["DepDel15"].value_counts().sort_index().items()},
        "validation_target_counts": {str(int(k)): int(v) for k, v in valid["DepDel15"].value_counts().sort_index().items()},
        "features": {"categorical": CATEGORICAL_FEATURES, "numeric": NUMERIC_FEATURES},
        "models": {},
    }
    confusion_rows = []

    for name, model in models.items():
        if name == "majority_baseline":
            model.fit(np.zeros((len(y_train), 1)), y_train)
            pred = model.predict(np.zeros((len(y_valid), 1))).astype(int)
            prob = model.predict_proba(np.zeros((len(y_valid), 1)))[:, list(model.classes_).index(1)]
        else:
            print("Training logistic regression (first run may take a few minutes)...", flush=True)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                model.fit(X_train, y_train)
                for warning in caught:
                    print(f"WARNING during logistic regression: {warning.message}", flush=True)
            pred = model.predict(X_valid).astype(int)
            prob = model.predict_proba(X_valid)[:, list(model.classes_).index(1)]

        metrics = score_predictions(y_valid, pred, prob)
        all_metrics["models"][name] = metrics
        confusion_rows.append({"model": name, **metrics["confusion_matrix"]})
        print(f"\n{name}:", flush=True)
        for key in ["accuracy", "balanced_accuracy", "precision", "recall", "f1", "roc_auc", "average_precision", "brier_score"]:
            print(f"  {key}: {metrics[key]:.4f}", flush=True)
        print(f"  confusion_matrix: {metrics['confusion_matrix']}", flush=True)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / "metrics.json"
    confusion_path = out_dir / "confusion_matrices.csv"
    metrics_path.write_text(json.dumps(all_metrics, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(confusion_rows).to_csv(confusion_path, index=False)
    print("\n=== Saved development outputs ===", flush=True)
    print(f"Metrics: {metrics_path.resolve()}", flush=True)
    print(f"Confusion matrices: {confusion_path.resolve()}", flush=True)
    print("NOTE: January-only metrics are development diagnostics, not final project results.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
