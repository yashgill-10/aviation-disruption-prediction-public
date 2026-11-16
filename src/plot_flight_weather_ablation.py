"""Plot the held-out 2024 flight-feature ablation from saved model metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
THRESHOLD_MODEL = "logistic_platt_calibrated_at_calibration_f1_threshold"


def read_metrics(path: Path) -> dict[str, float]:
    with path.open("r", encoding="utf-8") as file:
        metrics = json.load(file)
    evaluation = metrics["temporal_calibration_check"]["evaluation_models"][THRESHOLD_MODEL]
    return {
        "average_precision": float(evaluation["average_precision"]),
        "roc_auc": float(evaluation["roc_auc"]),
        "calibrated_brier": float(evaluation["brier_score"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "figures" / "flight-weather-ablation-2024.png",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = [
        ROOT / "data/processed/depdel15/baseline/metrics.json",
        ROOT / "data/processed/depdel15/models/historical_expanded/metrics.json",
        ROOT / "data/processed/depdel15/weather_models/t_minus_2h/metrics.json",
        ROOT / "data/processed/depdel15/weather_models/t_minus_30m/metrics.json",
    ]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Required metrics are missing: {missing[0]}")

    models = [read_metrics(path) for path in paths]
    labels = [
        "Flight only",
        "+ History",
        "+ Weather\n(T−2h)",
        "+ Weather\n(T−30m)",
    ]
    colors = ["#426A8C", "#398378", "#D59A38", "#C45A4A"]
    x = np.arange(len(labels))
    specs = [
        ("average_precision", "Average precision", (0.25, 0.39), [0.26, 0.30, 0.34, 0.38]),
        ("roc_auc", "ROC-AUC", (0.62, 0.71), [0.62, 0.65, 0.68, 0.71]),
        ("calibrated_brier", "Calibrated Brier\n(lower is better)", (0.14, 0.16), [0.14, 0.145, 0.15, 0.155, 0.16]),
    ]

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    figure, axes = plt.subplots(1, 3, figsize=(11.5, 4.4), constrained_layout=True)
    for axis, (metric, title, limits, ticks) in zip(axes, specs, strict=True):
        values = [model[metric] for model in models]
        axis.plot(x, values, color="#8796A5", linewidth=1.4, zorder=1)
        for index, (value, color) in enumerate(zip(values, colors, strict=True)):
            axis.scatter(index, value, s=48, color=color, edgecolor="white", linewidth=0.7, zorder=2)
            offset = 9 if index % 2 == 0 else -15
            axis.annotate(
                f"{value:.4f}",
                (index, value),
                xytext=(0, offset),
                textcoords="offset points",
                ha="center",
                va="bottom" if offset > 0 else "top",
                fontsize=8,
                color="#263645",
            )
        axis.set_title(title, fontsize=10, pad=12)
        axis.set_xticks(x, labels)
        axis.set_xlim(-0.35, len(labels) - 0.65)
        axis.set_ylim(*limits)
        axis.set_yticks(ticks)
        axis.grid(axis="y", color="#D8DEE5", linewidth=0.7, alpha=0.8)
        axis.tick_params(axis="x", length=0, pad=8)
        axis.tick_params(axis="y", labelsize=8, colors="#465565")

    figure.suptitle(
        "Flight-feature ablation | July–December 2024",
        fontsize=12,
        fontweight="bold",
    )
    figure.text(
        0.5,
        -0.015,
        "Weather models also include history. Trained on 2022–2023; calibration and threshold selection used January–June 2024.",
        ha="center",
        fontsize=8,
        color="#465565",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=220, bbox_inches="tight", facecolor="white")
    print(f"Saved {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
