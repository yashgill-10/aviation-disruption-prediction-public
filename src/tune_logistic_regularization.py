"""Select SGD logistic L2 regularization using a 2022/2023 chronological split.

Train each candidate on 2022 only and choose alpha by average precision on
2023. The selected alpha is then supplied to train_baseline.py, which refits
on 2022-2023 and calibrates/evaluates on disjoint 2024 periods.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.special import expit
from sklearn.linear_model import SGDClassifier

from train_baseline import (
    CLASSES,
    HISTORICAL_FEATURES,
    WEATHER_PARQUET_COLUMNS,
    encode_frame,
    expected_month_files,
    fit_encoder,
    iter_frames,
    score_predictions,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/depdel15"))
    parser.add_argument("--weather-features-dir", type=Path, default=None)
    parser.add_argument("--alphas", type=float, nargs="+", default=[1e-4, 1e-5, 1e-6])
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--sample-rows-per-month", type=int, default=20_000)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size < 1 or args.sample_rows_per_month < 1 or args.epochs < 1:
        print("ERROR: batch size, sample size, and epochs must be positive.", file=sys.stderr)
        return 2
    if not args.alphas or any(not np.isfinite(alpha) or alpha <= 0 for alpha in args.alphas):
        print("ERROR: all alpha values must be positive and finite.", file=sys.stderr)
        return 2
    data_dir = (args.weather_features_dir or args.data_dir).resolve()
    include_weather = args.weather_features_dir is not None
    try:
        train_2022 = expected_month_files(data_dir, "train", (2022,))
        validation_2023 = [data_dir / "flights" / "train" / f"2023-{month:02d}.parquet" for month in range(1, 13)]
        missing = [path for path in validation_2023 if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"2023 tuning partition is incomplete: {missing[0]}")
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Hyperparameter selection uses 2022 for fit and 2023 for ranking only.", flush=True)
    print("2024 calibration/evaluation and the 2025 test directory are not opened.", flush=True)
    encoder = fit_encoder(train_2022, args.sample_rows_per_month)
    candidates = []
    for alpha in sorted(set(args.alphas), reverse=True):
        print(f"Training candidate alpha={alpha:g}...", flush=True)
        classifier = SGDClassifier(
            loss="log_loss",
            penalty="l2",
            alpha=alpha,
            learning_rate="optimal",
            average=True,
            random_state=42,
        )
        rng = np.random.default_rng(42)
        initialized = False
        for epoch in range(1, args.epochs + 1):
            for month_index, path in enumerate(train_2022, 1):
                columns = [
                    "FlightDate", "Reporting_Airline", "Origin", "Dest", "CRSDepTime",
                    "CRSArrTime", "Distance", "CRSElapsedTime", "DepDel15",
                    *HISTORICAL_FEATURES,
                    *(WEATHER_PARQUET_COLUMNS if include_weather else []),
                ]
                for frame in iter_frames(path, columns, args.batch_size):
                    features, target = encode_frame(frame, encoder, include_historical=True, include_weather=include_weather)
                    order = rng.permutation(len(target))
                    features, target = features[order], target[order]
                    if not initialized:
                        classifier.partial_fit(features, target, classes=CLASSES)
                        initialized = True
                    else:
                        classifier.partial_fit(features, target)
                if epoch == args.epochs:
                    print(f"  trained {path.stem} ({month_index}/12)", flush=True)

        y_parts = []
        score_parts = []
        validation_columns = [
            "FlightDate", "Reporting_Airline", "Origin", "Dest", "CRSDepTime",
            "CRSArrTime", "Distance", "CRSElapsedTime", "DepDel15",
            *HISTORICAL_FEATURES,
            *(WEATHER_PARQUET_COLUMNS if include_weather else []),
        ]
        for month_index, path in enumerate(validation_2023, 1):
            for frame in iter_frames(path, validation_columns, args.batch_size):
                features, target = encode_frame(frame, encoder, include_historical=True, include_weather=include_weather)
                y_parts.append(target)
                score_parts.append(classifier.decision_function(features).astype(np.float32, copy=False))
            print(f"  ranked {path.stem} ({month_index}/12)", flush=True)
        y_true = np.concatenate(y_parts)
        scores = np.concatenate(score_parts)
        probability = expit(scores)
        result = score_predictions(y_true, probability, threshold=0.5)
        result.update(alpha=float(alpha), epochs=args.epochs)
        candidates.append(result)
        print(f"alpha={alpha:g}: AP={result['average_precision']:.4f}; ROC-AUC={result['roc_auc']:.4f}", flush=True)

    selected = max(candidates, key=lambda result: (result["average_precision"], result["roc_auc"], -result["alpha"]))
    output = {
        "purpose": "Regularization selection on 2023, before final 2024 calibration/evaluation.",
        "data_dir": str(data_dir),
        "training_year": 2022,
        "selection_year": 2023,
        "weather_horizon": data_dir.name if include_weather else None,
        "validation_2024_read": False,
        "test_2025_read": False,
        "selection_metric": "average_precision, with ROC-AUC as tie-breaker",
        "selected_alpha": selected["alpha"],
        "candidates": candidates,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Selected alpha={selected['alpha']:g}; results: {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
