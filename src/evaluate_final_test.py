"""Score the frozen history-augmented baseline on 2025 for an early check.

This is an exploratory baseline score, not the scheduled final evaluation of
the integrated weather/horizon model. Test-month outcomes may enter
the predeclared three-complete-month lagged features for later test months;
the current and future target months are excluded from each row's features.
No 2025 result is used to refit or tune the model, calibrator, or threshold.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
from scipy.special import expit

from build_historical_features import (
    TRAIN_YEARS,
    VALIDATION_YEARS,
    build_one_month,
    ensure_output_is_available,
    expected_month_files,
    make_group_history,
    sql_string,
)
from train_baseline import (
    BASE_PARQUET_COLUMNS,
    HISTORICAL_FEATURES,
    calibration_bins,
    encode_frame,
    iter_frames,
    score_predictions,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/processed/depdel15")
    parser.add_argument(
        "--model-path",
        default="data/processed/depdel15/historical_baseline/historical_logistic_sgd.joblib",
    )
    parser.add_argument(
        "--development-metrics",
        default="data/processed/depdel15/historical_baseline/metrics.json",
    )
    parser.add_argument("--output-dir", default="data/processed/depdel15/final_test")
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args()


def evaluate_slices(
    target: np.ndarray,
    probability: np.ndarray,
    threshold: float,
    dates: pd.DatetimeIndex,
    slice_values: pd.Series,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for value in sorted(slice_values.dropna().unique()):
        selected = slice_values.to_numpy() == value
        if selected.sum() == 0 or np.unique(target[selected]).size < 2:
            continue
        result[str(value)] = score_predictions(
            target[selected], probability[selected], threshold
        )
    return result


def main() -> int:
    args = parse_args()
    if args.batch_size < 1 or args.threads < 1:
        print("ERROR: batch size and thread count must be positive.", file=sys.stderr)
        return 2

    data_dir = Path(args.data_dir).resolve()
    model_path = Path(args.model_path).resolve()
    development_metrics_path = Path(args.development_metrics).resolve()
    output_dir = Path(args.output_dir).resolve()
    try:
        train_files = expected_month_files(data_dir, "train", TRAIN_YEARS)
        validation_files = expected_month_files(data_dir, "validation", VALIDATION_YEARS)
        test_files = expected_month_files(data_dir, "test", (2025,))
        ensure_output_is_available(output_dir)
        if not model_path.is_file():
            raise FileNotFoundError(f"Frozen model was not found: {model_path}")
        if not development_metrics_path.is_file():
            raise FileNotFoundError(
                f"Development metrics were not found: {development_metrics_path}"
            )
    except (FileNotFoundError, FileExistsError, NotADirectoryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    artifact = joblib.load(model_path)
    development_metrics = json.loads(development_metrics_path.read_text(encoding="utf-8"))
    if artifact.get("target") != "DepDel15":
        print("ERROR: model artifact target is not DepDel15.", file=sys.stderr)
        return 2
    if artifact.get("historical_features") != HISTORICAL_FEATURES:
        print("ERROR: model artifact is not the expected history-augmented model.", file=sys.stderr)
        return 2
    if artifact.get("train_years") != [2022, 2023] or artifact.get("validation_year") != 2024:
        print("ERROR: model artifact has unexpected development years.", file=sys.stderr)
        return 2
    try:
        train_prevalence = float(development_metrics["train_positive_prevalence"])
        threshold = float(artifact["f1_threshold_selected_on_2024_first_half"])
        calibrator = artifact["calibrator"]
        classifier = artifact["classifier"]
        encoder = artifact["encoder"]
    except (KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: model or development metrics are incomplete: {exc}", file=sys.stderr)
        return 2

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.building-", dir=output_dir.parent
    ) as staging_name:
        staging_dir = Path(staging_name)
        feature_root = staging_dir / "historical_features"
        connection = duckdb.connect()
        try:
            connection.execute(f"SET threads = {int(args.threads)}")
            temp_dir = staging_dir / "duckdb_temp"
            temp_dir.mkdir()
            connection.execute(f"SET temp_directory = {sql_string(temp_dir)}")

            source_files = train_files + validation_files + test_files
            print(
                "Building the predeclared three-month historical features for 2025...",
                flush=True,
            )
            make_group_history(connection, source_files)
            row_counts: dict[str, int] = {}
            for index, source_path in enumerate(test_files, 1):
                month_label = source_path.stem
                destination = feature_root / "flights" / "test" / source_path.name
                rows = build_one_month(
                    connection, source_path, destination, month_label
                )
                source_rows = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM read_parquet({sql_string(source_path)})"
                    ).fetchone()[0]
                )
                if rows != source_rows:
                    raise RuntimeError(
                        f"{month_label}: enriched row count {rows} differs from source {source_rows}"
                    )
                row_counts[month_label] = rows
                print(
                    f"  built {month_label}: {rows:,} rows ({index}/{len(test_files)})",
                    flush=True,
                )
        except Exception as exc:
            print(f"ERROR: could not build 2025 baseline features: {exc}", file=sys.stderr)
            return 1
        finally:
            connection.close()

        shutil.rmtree(staging_dir / "duckdb_temp", ignore_errors=True)

        test_feature_files = [
            feature_root / "flights" / "test" / path.name for path in test_files
        ]
        columns = BASE_PARQUET_COLUMNS + HISTORICAL_FEATURES
        target_parts: list[np.ndarray] = []
        score_parts: list[np.ndarray] = []
        date_parts: list[pd.Series] = []
        rows_scored = 0
        print("Scoring 2025 with the frozen model and 2024 calibrator...", flush=True)
        try:
            for index, path in enumerate(test_feature_files, 1):
                for frame in iter_frames(path, columns, args.batch_size):
                    features, target = encode_frame(frame, encoder, include_historical=True)
                    scores = classifier.decision_function(features).astype(
                        np.float32, copy=False
                    )
                    target_parts.append(target)
                    score_parts.append(scores)
                    date_parts.append(pd.to_datetime(frame["FlightDate"], errors="coerce"))
                    rows_scored += len(target)
                print(
                    f"  scored {path.stem} ({index}/{len(test_feature_files)})",
                    flush=True,
                )
        except Exception as exc:
            print(f"ERROR: 2025 baseline scoring failed: {exc}", file=sys.stderr)
            return 1

        target = np.concatenate(target_parts)
        scores = np.concatenate(score_parts)
        dates = pd.DatetimeIndex(pd.concat(date_parts, ignore_index=True))
        if len(target) != sum(row_counts.values()) or rows_scored != len(target):
            print("ERROR: scored row count differs from the built test features.", file=sys.stderr)
            return 1
        raw_probability = expit(scores).astype(np.float32, copy=False)
        calibrated_probability = calibrator.predict_proba(scores.reshape(-1, 1))[:, 1].astype(
            np.float32, copy=False
        )
        prior_probability = np.full(len(target), train_prevalence, dtype=np.float32)

        metrics = {
            "purpose": "Early exploratory 2025 score check of the frozen history-augmented baseline.",
            "target": "DepDel15",
            "test_period": [dates.min().strftime("%Y-%m-%d"), dates.max().strftime("%Y-%m-%d")],
            "rows_scored": int(len(target)),
            "features": [*artifact["numeric_features"], *artifact["historical_features"]],
            "model_artifact": str(model_path),
            "development_metrics": str(development_metrics_path),
            "calibration": {
                "method": "Frozen Platt calibrator fitted on January-June 2024.",
                "threshold": threshold,
                "threshold_selection": "F1 threshold selected on January-June 2024 only.",
                "no_2025_refitting_or_threshold_selection": True,
            },
            "historical_feature_availability": {
                "window": "Three complete calendar months before the flight month.",
                "same_month_or_future_targets_used": False,
                "prior_test_months_may_contribute": True,
                "note": (
                    "For later 2025 months, outcomes from earlier complete test months are used "
                    "only in the predeclared lagged historical-rate window. No 2025 outcome is "
                    "used to fit the model, calibrator, or threshold. BTS publication latency "
                    "is not modeled."
                ),
            },
            "test_month_row_counts": row_counts,
            "models": {
                "training_prevalence_prior": score_predictions(target, prior_probability),
                "frozen_model_uncalibrated": score_predictions(target, raw_probability),
                "frozen_model_platt_calibrated_at_0.5": score_predictions(
                    target, calibrated_probability
                ),
                "frozen_model_platt_calibrated_at_2024_f1_threshold": score_predictions(
                    target, calibrated_probability, threshold
                ),
            },
            "calibration_bins": calibration_bins(target, calibrated_probability),
            "quarterly_metrics_at_frozen_threshold": evaluate_slices(
                target,
                calibrated_probability,
                threshold,
                dates,
                pd.Series(dates.quarter, name="quarter"),
            ),
            "monthly_metrics_at_frozen_threshold": evaluate_slices(
                target,
                calibrated_probability,
                threshold,
                dates,
                pd.Series(dates.strftime("%Y-%m"), name="month"),
            ),
            "test_use": {
                "used_for_model_or_feature_selection": False,
                "used_for_calibration_or_threshold_selection": False,
                "used_for_exploratory_baseline_scoring": True,
            },
            "limitations": [
                "Weather, airport-state, and aircraft-rotation features are not included.",
                "Lagged historical rates assume prior complete-month BTS outcomes are available; actual publication latency is not modeled.",
                "Quarterly and monthly metrics are diagnostic slices of the same 2025 holdout, not independent test sets.",
            ],
        }
        (staging_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
        )
        feature_metadata = {
            "dataset": "2025 BTS features for the early exploratory baseline score check",
            "source_splits_read": {"train": [2022, 2023], "validation": [2024], "test": [2025]},
            "test_labels_used_for_scoring": True,
            "test_months_used_only_as_lagged_history_for_later_months": True,
            "same_month_or_future_outcomes_used": False,
            "history_window": "three fully completed calendar months before the flight month",
            "row_counts": row_counts,
        }
        (feature_root / "metadata.json").write_text(
            json.dumps(feature_metadata, indent=2) + "\n", encoding="utf-8"
        )

        if output_dir.exists():
            output_dir.rmdir()
        os.replace(staging_dir, output_dir)

    print("\n=== Early exploratory 2025 baseline results ===", flush=True)
    final_result = metrics["models"]["frozen_model_platt_calibrated_at_2024_f1_threshold"]
    print(
        f"AP={final_result['average_precision']:.4f}, "
        f"ROC-AUC={final_result['roc_auc']:.4f}, "
        f"F1={final_result['f1']:.4f}, precision={final_result['precision']:.4f}, "
        f"recall={final_result['recall']:.4f}, Brier={final_result['brier_score']:.4f}",
        flush=True,
    )
    print(f"Metrics: {output_dir / 'metrics.json'}", flush=True)
    print(f"Lagged test features: {output_dir / 'historical_features'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
