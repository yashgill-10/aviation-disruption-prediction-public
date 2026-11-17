"""Summarize local Airport/Network scores on the historical Oct-Dec split.

This is retrospective, ordinal-keyed analysis only; it is not a matched
Flight/Weather comparison or a blind final test.
"""

from __future__ import annotations

import csv
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "provenance_summary.json").read_text(encoding="utf-8"))
HORIZONS = {
    "T−2h": "preliminary_t_minus_2h_scores_by_source_ordinal.csv.gz",
    "T−30m": "preliminary_t_minus_30m_scores_by_source_ordinal.csv.gz",
}
SPLIT_MONTHS = {10, 11, 12}
MIN_GROUP_N = 1000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metric_row(counts: dict[str, float]) -> dict[str, float | int]:
    n = int(counts["n"])
    tp, fp, fn, tn = (int(counts[k]) for k in ("tp", "fp", "fn", "tn"))
    pos = tp + fn
    neg = tn + fp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / pos if pos else 0.0
    specificity = tn / neg if neg else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "n": n,
        "actual_delays": pos,
        "actual_delay_rate": pos / n if n else 0.0,
        "predicted_delays": tp + fp,
        "predicted_delay_rate": (tp + fp) / n if n else 0.0,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "false_positive_rate": fp / neg if neg else 0.0,
        "false_negative_rate": fn / pos if pos else 0.0,
        "f1": f1,
        "accuracy": (tp + tn) / n if n else 0.0,
        "balanced_accuracy": (recall + specificity) / 2,
        "brier_score": counts["brier_sum"] / n if n else 0.0,
    }


def add_group(groups, key, y, pred, score):
    c = groups[key]
    c["n"] += 1
    c["tp"] += int(y == 1 and pred == 1)
    c["fp"] += int(y == 0 and pred == 1)
    c["fn"] += int(y == 1 and pred == 0)
    c["tn"] += int(y == 0 and pred == 0)
    c["brier_sum"] += (score - y) ** 2


def analyze(horizon: str, filename: Path, threshold: float) -> dict:
    all_y: list[int] = []
    all_p: list[float] = []
    totals = defaultdict(float)
    by_month = defaultdict(lambda: defaultdict(float))
    by_carrier = defaultdict(lambda: defaultdict(float))
    by_origin = defaultdict(lambda: defaultdict(float))
    bins = [defaultdict(float) for _ in range(10)]
    eligible_test_rows = 0

    with gzip.open(filename, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            month = int(row["MONTH"]) if row["MONTH"] else 0
            if month not in SPLIT_MONTHS or row["score_status"] != "scored":
                continue
            y_float = float(row["delay_15"])
            if y_float not in (0.0, 1.0):
                raise ValueError(f"Non-binary target on row ordinal {row['cleaned_input_row_ordinal']}")
            y = int(y_float)
            p = float(row["delay_probability"])
            pred = int(row["predicted_delay_15"])
            eligible_test_rows += 1
            all_y.append(y)
            all_p.append(p)
            add_group({"all": totals}, "all", y, pred, p)
            add_group(by_month, str(month), y, pred, p)
            add_group(by_carrier, row["OP_UNIQUE_CARRIER"] or "<missing>", y, pred, p)
            add_group(by_origin, row["ORIGIN"] or "<missing>", y, pred, p)
            b = bins[min(9, int(p * 10))]
            b["n"] += 1
            b["sum_score"] += p
            b["sum_target"] += y

    y_array = np.asarray(all_y, dtype=np.uint8)
    p_array = np.asarray(all_p, dtype=np.float32)
    summary = metric_row(totals)
    summary.update({
        "horizon": horizon,
        "evaluation_period": "2024-10-01 through 2024-12-31",
        "threshold": threshold,
        "pr_auc_average_precision": float(average_precision_score(y_array, p_array)),
        "roc_auc": float(roc_auc_score(y_array, p_array)),
        "rows_in_split_scored": eligible_test_rows,
        "limitations": [
            "Ordinal-keyed local reconstruction; canonical-ID cohort membership has not been joined.",
            "Historical exploratory period; not a blind final test.",
            "BTS feature availability at the prediction cutoff remains unverified.",
            "The original packaged smoke CSV had incorrect time fields; the source-consistent record reproduces its score exactly.",
        ],
    })

    calibration = []
    ece = 0.0
    for i, b in enumerate(bins):
        if not b["n"]:
            continue
        mean_p = b["sum_score"] / b["n"]
        observed = b["sum_target"] / b["n"]
        ece += b["n"] / eligible_test_rows * abs(mean_p - observed)
        calibration.append({
            "bin": f"[{i/10:.1f}, {(i+1)/10:.1f}{']' if i == 9 else ')'}",
            "n": int(b["n"]),
            "mean_probability": mean_p,
            "observed_delay_rate": observed,
        })
    summary["expected_calibration_error_10_bins"] = ece

    def groups_to_rows(groups):
        rows = []
        for group, counts in groups.items():
            result = {"group": group, **metric_row(counts)}
            if result["n"] >= MIN_GROUP_N:
                rows.append(result)
        return sorted(rows, key=lambda r: r["n"], reverse=True)

    return {
        "summary": summary,
        "by_month": groups_to_rows(by_month),
        "by_carrier_min_n_1000": groups_to_rows(by_carrier),
        "by_origin_min_n_1000": groups_to_rows(by_origin),
        "calibration_bins": calibration,
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def compare_saved_results(results: dict[str, dict], recorded_path: Path, output_path: Path) -> None:
    """Compare this reconstruction with the package's recorded holdout rows."""
    with recorded_path.open("r", encoding="utf-8-sig", newline="") as stream:
        recorded = list(csv.DictReader(stream))
    output = []
    for horizon, result in results.items():
        horizon_rows = [r for r in recorded if r["Horizon"] == horizon]
        original = next(
            r for r in horizon_rows
            if r["Model"] == "Random Forest" and r["Feature set"] == "Schedule/route + Airport/Network"
        )
        cm = json.loads(original["Confusion matrix"])
        s = result["summary"]
        rows = [
            ("test_rows", int(original["Test flights"]), s["n"]),
            ("accuracy", float(original["Accuracy"]), s["accuracy"]),
            ("precision", float(original["Precision"]), s["precision"]),
            ("recall", float(original["Recall"]), s["recall"]),
            ("f1", float(original["F1-score"]), s["f1"]),
            ("balanced_accuracy", float(original["Balanced accuracy"]), s["balanced_accuracy"]),
            ("pr_auc", float(original["PR-AUC"]), s["pr_auc_average_precision"]),
            ("true_negatives", int(cm[0][0]), s["true_negatives"]),
            ("false_positives", int(cm[0][1]), s["false_positives"]),
            ("false_negatives", int(cm[1][0]), s["false_negatives"]),
            ("true_positives", int(cm[1][1]), s["true_positives"]),
        ]
        for metric, original_value, local_value in rows:
            delta = float(local_value) - float(original_value)
            output.append({
                "horizon": horizon,
                "metric": metric,
                "saved_report": original_value,
                "local_reconstruction": local_value,
                "delta_local_minus_saved": delta,
                "matches_within_1e_minus_6": abs(delta) <= 1e-6,
            })
    write_csv(output_path, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--t2h-scores", type=Path, default=HERE / HORIZONS["T−2h"])
    parser.add_argument("--t30m-scores", type=Path, default=HERE / HORIZONS["T−30m"])
    parser.add_argument(
        "--saved-results",
        type=Path,
        default=HERE.parent / "colab_model_artifacts" / "aviation_airport_network_results.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    score_paths = {"T−2h": args.t2h_scores, "T−30m": args.t30m_scores}
    for horizon, path in score_paths.items():
        if not path.is_file():
            parser.error(f"Missing {horizon} score input: {path}")
        actual_hash = sha256_file(path)
        expected_hash = MANIFEST["score_export_inputs"][horizon]["sha256"]
        if actual_hash != expected_hash:
            parser.error(f"{horizon} score SHA-256 mismatch: expected {expected_hash}, found {actual_hash}")
    if not args.saved_results.is_file():
        parser.error(f"Missing saved aggregate results CSV: {args.saved_results}")
    expected_results_hash = MANIFEST["saved_aggregate_report_sha256"]
    actual_results_hash = sha256_file(args.saved_results)
    if actual_results_hash != expected_results_hash:
        parser.error(f"Saved aggregate results SHA-256 mismatch: expected {expected_results_hash}, found {actual_results_hash}")
    results = {
        horizon: analyze(
            horizon,
            path,
            float(MANIFEST["score_export_inputs"][horizon]["decision_threshold"]),
        )
        for horizon, path in score_paths.items()
    }
    (args.output_dir / "oct_dec_error_analysis.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    compare_saved_results(
        results,
        args.saved_results,
        args.output_dir / "saved_holdout_reproduction_comparison.csv",
    )
    for horizon, result in results.items():
        stem = "t_minus_2h" if horizon == "T−2h" else "t_minus_30m"
        for key in ("by_month", "by_carrier_min_n_1000", "by_origin_min_n_1000"):
            write_csv(args.output_dir / f"{stem}_{key}.csv", result[key])
        print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
