"""Compare the two frozen baseline models on the same 2025 population."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit

from build_historical_features import expected_month_files
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
    parser.add_argument("--output-dir", default="data/processed/depdel15/final_test")
    parser.add_argument("--batch-size", type=int, default=100_000)
    return parser.parse_args()


def load_artifact(path: Path, *, historical: bool) -> tuple[dict, float]:
    if not path.is_file():
        raise FileNotFoundError(f"Model artifact was not found: {path}")
    artifact = joblib.load(path)
    expected_features = HISTORICAL_FEATURES if historical else []
    if artifact.get("target") != "DepDel15":
        raise ValueError(f"Unexpected target in model artifact: {path}")
    if artifact.get("historical_features", []) != expected_features:
        raise ValueError(f"Unexpected feature set in model artifact: {path}")
    if artifact.get("train_years") != [2022, 2023] or artifact.get("validation_year") != 2024:
        raise ValueError(f"Unexpected development years in model artifact: {path}")
    threshold = float(artifact["f1_threshold_selected_on_2024_first_half"])
    return artifact, threshold


def score_model(
    files: list[Path],
    artifact: dict,
    *,
    historical: bool,
    batch_size: int,
) -> tuple[np.ndarray, np.ndarray, pd.DatetimeIndex]:
    columns = BASE_PARQUET_COLUMNS + (HISTORICAL_FEATURES if historical else [])
    target_parts: list[np.ndarray] = []
    score_parts: list[np.ndarray] = []
    date_parts: list[pd.Series] = []
    for index, path in enumerate(files, 1):
        print(f"  scoring {path.stem} ({index}/{len(files)})", flush=True)
        for frame in iter_frames(path, columns, batch_size):
            features, target = encode_frame(
                frame, artifact["encoder"], include_historical=historical
            )
            scores = artifact["classifier"].decision_function(features).astype(
                np.float32, copy=False
            )
            target_parts.append(target)
            score_parts.append(scores)
            date_parts.append(pd.to_datetime(frame["FlightDate"], errors="coerce"))
    return (
        np.concatenate(target_parts),
        np.concatenate(score_parts),
        pd.DatetimeIndex(pd.concat(date_parts, ignore_index=True)),
    )


def slice_metrics(
    target: np.ndarray,
    probability: np.ndarray,
    dates: pd.DatetimeIndex,
    threshold: float,
    labels: pd.Series,
) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    values = labels.to_numpy()
    for value in sorted(pd.unique(values)):
        mask = values == value
        if mask.sum() == 0 or np.unique(target[mask]).size < 2:
            continue
        output[str(value)] = score_predictions(target[mask], probability[mask], threshold)
    return output


def summarize_model(
    target: np.ndarray,
    scores: np.ndarray,
    dates: pd.DatetimeIndex,
    artifact: dict,
    threshold: float,
    train_prevalence: float,
) -> dict[str, object]:
    raw_probability = expit(scores).astype(np.float32, copy=False)
    calibrated_probability = artifact["calibrator"].predict_proba(scores.reshape(-1, 1))[:, 1].astype(
        np.float32, copy=False
    )
    prior_probability = np.full(len(target), train_prevalence, dtype=np.float32)
    return {
        "training_prevalence_prior": score_predictions(target, prior_probability),
        "uncalibrated": score_predictions(target, raw_probability),
        "platt_calibrated_at_0.5": score_predictions(target, calibrated_probability),
        "platt_calibrated_at_frozen_2024_threshold": score_predictions(
            target, calibrated_probability, threshold
        ),
        "calibration_bins": calibration_bins(target, calibrated_probability),
        "threshold_selected_on_2024_h1": threshold,
        "quarterly_at_frozen_threshold": slice_metrics(
            target,
            calibrated_probability,
            dates,
            threshold,
            pd.Series(dates.quarter, name="quarter"),
        ),
        "monthly_at_frozen_threshold": slice_metrics(
            target,
            calibrated_probability,
            dates,
            threshold,
            pd.Series(dates.strftime("%Y-%m"), name="month"),
        ),
    }


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        print("ERROR: batch size must be positive.", file=sys.stderr)
        return 2

    data_dir = Path(args.data_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    metrics_path = output_dir / "comparison_metrics.json"
    schedule_path = data_dir / "baseline" / "schedule_logistic_sgd.joblib"
    history_path = data_dir / "historical_baseline" / "historical_logistic_sgd.joblib"
    try:
        test_files = expected_month_files(data_dir, "test", (2025,))
        historical_test_files = [
            output_dir / "historical_features" / "flights" / "test" / path.name
            for path in test_files
        ]
        missing_features = [path.name for path in historical_test_files if not path.is_file()]
        if missing_features:
            raise FileNotFoundError(
                "Lagged historical test features are missing: " + ", ".join(missing_features)
            )
        if metrics_path.exists():
            raise FileExistsError(f"Comparison metrics already exist: {metrics_path}")
        schedule_artifact, schedule_threshold = load_artifact(schedule_path, historical=False)
        history_artifact, history_threshold = load_artifact(history_path, historical=True)
        schedule_dev = json.loads(
            (data_dir / "baseline" / "metrics.json").read_text(encoding="utf-8")
        )
        history_dev = json.loads(
            (data_dir / "historical_baseline" / "metrics.json").read_text(encoding="utf-8")
        )
        schedule_prior = float(schedule_dev["train_positive_prevalence"])
        history_prior = float(history_dev["train_positive_prevalence"])
    except (FileNotFoundError, FileExistsError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Scoring the frozen schedule-only model on 2025...", flush=True)
    schedule_target, schedule_scores, schedule_dates = score_model(
        test_files, schedule_artifact, historical=False, batch_size=args.batch_size
    )
    print("Scoring the frozen schedule-plus-history model on the same 2025 period...", flush=True)
    history_target, history_scores, history_dates = score_model(
        historical_test_files,
        history_artifact,
        historical=True,
        batch_size=args.batch_size,
    )

    if (
        len(schedule_target) != len(history_target)
        or int(schedule_target.sum()) != int(history_target.sum())
        or not np.isclose(float(schedule_target.mean()), float(history_target.mean()))
    ):
        print("ERROR: model runs do not have the same 2025 target population.", file=sys.stderr)
        return 1
    if schedule_dates.min() != history_dates.min() or schedule_dates.max() != history_dates.max():
        print("ERROR: model runs do not cover the same 2025 date range.", file=sys.stderr)
        return 1

    schedule_result = summarize_model(
        schedule_target,
        schedule_scores,
        schedule_dates,
        schedule_artifact,
        schedule_threshold,
        schedule_prior,
    )
    history_result = summarize_model(
        history_target,
        history_scores,
        history_dates,
        history_artifact,
        history_threshold,
        history_prior,
    )
    metric_names = [
        "average_precision",
        "roc_auc",
        "brier_score",
        "precision",
        "recall",
        "f1",
    ]
    schedule_operating = schedule_result["platt_calibrated_at_frozen_2024_threshold"]
    history_operating = history_result["platt_calibrated_at_frozen_2024_threshold"]
    improvement = {
        name: float(history_operating[name] - schedule_operating[name])
        for name in metric_names
    }
    metrics = {
        "purpose": "Early exploratory paired 2025 score check of two frozen 2022-2023 baselines.",
        "test_period": [schedule_dates.min().strftime("%Y-%m-%d"), schedule_dates.max().strftime("%Y-%m-%d")],
        "target": "DepDel15",
        "rows": int(len(schedule_target)),
        "positive_prevalence": float(schedule_target.mean()),
        "model_selection_and_thresholds": "Both baseline families and their calibrators/thresholds were fixed using 2024 before this 2025 score check; weather and horizon models are not included.",
        "evaluation_status": "Exploratory baseline results only. The scheduled final evaluation must not be described as an independent blind test on this already-inspected 2025 year.",
        "models": {
            "schedule_only": {
                "artifact": str(schedule_path),
                "development_metrics": str(data_dir / "baseline" / "metrics.json"),
                **schedule_result,
            },
            "schedule_plus_historical": {
                "artifact": str(history_path),
                "development_metrics": str(data_dir / "historical_baseline" / "metrics.json"),
                **history_result,
            },
        },
        "historical_minus_schedule_at_each_models_frozen_2024_threshold": improvement,
        "historical_feature_test_protocol": {
            "window": "Three complete calendar months before the flight month.",
            "same_month_or_future_test_outcomes_in_features": False,
            "prior_test_months_may_contribute_to_later_month_features": True,
            "BTS_publication_latency_modeled": False,
            "note": (
                "This is a chronological rolling evaluation: earlier complete test months may "
                "supply lagged historical rates to later months, but no 2025 labels are used to "
                "fit the models, calibrators, or thresholds."
            ),
        },
        "limitations": [
            "Weather, airport-state, and aircraft-rotation features are not included.",
            "Lagged historical rates assume prior complete-month BTS outcomes are available; actual publication latency is not modeled.",
            "Monthly and quarterly slices are diagnostics within the same holdout, not independent test sets.",
            "Do not use these 2025 results to select features, models, or thresholds.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    print("\n=== Early exploratory 2025 baseline comparison ===", flush=True)
    for key in ("schedule_only", "schedule_plus_historical"):
        result = metrics["models"][key]["platt_calibrated_at_frozen_2024_threshold"]
        print(
            f"{key}: AP={result['average_precision']:.4f}, "
            f"ROC-AUC={result['roc_auc']:.4f}, F1={result['f1']:.4f}, "
            f"precision={result['precision']:.4f}, recall={result['recall']:.4f}, "
            f"Brier={result['brier_score']:.4f}",
            flush=True,
        )
    print(f"Metrics: {metrics_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
