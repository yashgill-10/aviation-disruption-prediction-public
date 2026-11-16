"""Compare three aviation-delay model exports on an approved canonical cohort.

The utility is intentionally strict: it will not silently shrink the cohort,
fill unavailable scores, or synthesize combined-model predictions.
"""

from __future__ import annotations

import argparse
import hashlib
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


MODELS = ("flight_weather", "airport_network", "combined")
SCORE_COLUMNS = {
    "flight_id",
    "score_status",
    "delay_probability",
    "decision_threshold",
    "predicted_delay_15",
    "unavailable_reason",
}


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"flight_id": "string"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_cohort(path: Path) -> pd.DataFrame:
    cohort = _read_csv(path)
    required = {"flight_id", "delay_15"}
    missing = sorted(required - set(cohort.columns))
    if missing:
        raise ValueError(f"Cohort is missing required columns: {missing}")
    blank_ids = cohort["flight_id"].astype("string").str.strip().eq("")
    if cohort["flight_id"].isna().any() or blank_ids.any() or cohort["flight_id"].duplicated().any():
        raise ValueError("Cohort flight_id values must be unique and nonmissing.")
    cohort["delay_15"] = pd.to_numeric(cohort["delay_15"], errors="coerce")
    if cohort["delay_15"].isna().any() or not cohort["delay_15"].isin([0, 1]).all():
        raise ValueError("Cohort delay_15 must be complete and binary.")
    if "partition" in cohort.columns and cohort["partition"].isna().any():
        raise ValueError("Cohort partition contains missing values.")
    return cohort


def _validate_predictions(
    name: str,
    path: Path,
    horizon: str,
    cohort: pd.DataFrame,
) -> pd.DataFrame:
    frame = _read_csv(path)
    missing = sorted(SCORE_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"{name} export is missing columns: {missing}")
    blank_ids = frame["flight_id"].astype("string").str.strip().eq("")
    if frame["flight_id"].isna().any() or blank_ids.any() or frame["flight_id"].duplicated().any():
        raise ValueError(f"{name} flight_id values must be unique and nonmissing.")

    expected_ids = set(cohort["flight_id"])
    observed_ids = set(frame["flight_id"])
    missing_ids = expected_ids - observed_ids
    extra_ids = observed_ids - expected_ids
    if missing_ids or extra_ids:
        raise ValueError(
            f"{name} IDs must exactly cover the approved cohort: "
            f"missing={len(missing_ids)}, extra={len(extra_ids)}."
        )

    if "forecast_horizon" in frame.columns:
        observed_horizons = frame["forecast_horizon"].astype("string")
        if observed_horizons.isna().any() or not observed_horizons.eq(horizon).all():
            present_horizons = sorted(set(observed_horizons.dropna().astype(str)))
            raise ValueError(f"{name} contains horizons {present_horizons} or missing values, expected only {horizon}.")

    if "delay_15" in frame.columns:
        target_check = frame[["flight_id", "delay_15"]].merge(
            cohort[["flight_id", "delay_15"]], on="flight_id", suffixes=("_model", "_cohort"),
            validate="one_to_one", sort=False,
        )
        model_target = pd.to_numeric(target_check["delay_15_model"], errors="coerce")
        if model_target.isna().any() or not model_target.eq(target_check["delay_15_cohort"]).all():
            raise ValueError(f"{name} target values disagree with the approved cohort.")
    if "partition" in frame.columns and "partition" in cohort.columns:
        partition_check = frame[["flight_id", "partition"]].merge(
            cohort[["flight_id", "partition"]], on="flight_id", suffixes=("_model", "_cohort"),
            validate="one_to_one", sort=False,
        )
        if not partition_check["partition_model"].eq(partition_check["partition_cohort"]).all():
            raise ValueError(f"{name} partition values disagree with the approved cohort.")

    allowed_status = {"scored", "unavailable"}
    if frame["score_status"].isna().any() or not set(frame["score_status"].astype(str)).issubset(allowed_status):
        raise ValueError(f"{name} score_status must contain only 'scored' or 'unavailable'.")
    scored = frame["score_status"].eq("scored")
    unavailable = ~scored
    probabilities = pd.to_numeric(frame["delay_probability"], errors="coerce")
    thresholds = pd.to_numeric(frame["decision_threshold"], errors="coerce")
    labels = pd.to_numeric(frame["predicted_delay_15"], errors="coerce")
    if probabilities[scored].isna().any() or not np.isfinite(probabilities[scored]).all() or not probabilities[scored].between(0, 1).all():
        raise ValueError(f"{name} scored rows need finite probabilities in [0, 1].")
    if probabilities[unavailable].notna().any():
        raise ValueError(f"{name} unavailable rows must have null probabilities.")
    if thresholds[scored].isna().any() or not np.isfinite(thresholds[scored]).all() or not thresholds[scored].between(0, 1).all():
        raise ValueError(f"{name} scored rows need finite thresholds in [0, 1].")
    if thresholds[scored].nunique() > 1:
        raise ValueError(f"{name} threshold must be constant within a horizon.")
    if thresholds[unavailable].notna().any():
        raise ValueError(f"{name} unavailable rows must have null decision thresholds.")
    if labels[scored].isna().any() or not labels[scored].isin([0, 1]).all():
        raise ValueError(f"{name} scored rows need binary predictions.")
    if labels[unavailable].notna().any():
        raise ValueError(f"{name} unavailable rows must have null predicted labels.")
    if scored.any() and not labels[scored].astype(int).eq((probabilities[scored] >= thresholds[scored]).astype(int)).all():
        raise ValueError(f"{name} labels do not agree with their recorded threshold.")
    reasons = frame["unavailable_reason"].fillna("").astype(str).str.strip()
    if (unavailable & reasons.eq("")).any():
        raise ValueError(f"{name} unavailable rows require explicit reasons.")
    if (scored & ~reasons.eq("")).any():
        raise ValueError(f"{name} scored rows must have blank unavailable_reason.")
    frame["delay_probability"] = probabilities
    frame["decision_threshold"] = thresholds
    frame["predicted_delay_15"] = labels
    return frame


def _metrics(y_true: np.ndarray, probability: np.ndarray, threshold: float) -> dict[str, Any]:
    prediction = (probability >= threshold).astype(int)
    result: dict[str, Any] = {
        "n": int(len(y_true)),
        "delay_rate": float(y_true.mean()),
        "predicted_positive_rate": float(prediction.mean()),
        "average_precision_pr_auc": float(average_precision_score(y_true, probability)),
        "roc_auc": float(roc_auc_score(y_true, probability)) if len(np.unique(y_true)) == 2 else None,
        "brier_score": float(brier_score_loss(y_true, probability)),
        "log_loss": float(log_loss(y_true, probability, labels=[0, 1])),
        "accuracy": float(accuracy_score(y_true, prediction)),
        "precision": float(precision_score(y_true, prediction, zero_division=0)),
        "recall": float(recall_score(y_true, prediction, zero_division=0)),
        "f1": float(f1_score(y_true, prediction, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, prediction)),
        "confusion_matrix_tn_fp_fn_tp": confusion_matrix(y_true, prediction, labels=[0, 1]).ravel().tolist(),
        "decision_threshold": float(threshold),
    }
    return result


def evaluate(
    cohort_path: Path,
    score_paths: dict[str, Path],
    horizon: str,
    output_stem: Path,
) -> dict[str, Any]:
    cohort = _validate_cohort(cohort_path)
    scores = {name: _validate_predictions(name, path, horizon, cohort) for name, path in score_paths.items()}
    merged = cohort.copy()
    for name, frame in scores.items():
        cols = frame[["flight_id", "score_status", "delay_probability", "decision_threshold", "predicted_delay_15", "unavailable_reason"]].rename(
            columns={col: f"{name}_{col}" for col in ["score_status", "delay_probability", "decision_threshold", "predicted_delay_15", "unavailable_reason"]}
        )
        merged = merged.merge(cols, on="flight_id", how="left", validate="one_to_one", sort=False)
        if not merged["flight_id"].reset_index(drop=True).equals(cohort["flight_id"].reset_index(drop=True)):
            raise AssertionError(f"Joining {name} changed the approved cohort order.")
    matched = pd.Series(True, index=merged.index)
    report: dict[str, Any] = {
        "horizon": horizon,
        "approved_cohort_rows": int(len(cohort)),
        "inputs": {
            "canonical_cohort": {"path": str(cohort_path), "bytes": cohort_path.stat().st_size, "sha256": _sha256(cohort_path)},
            **{name: {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)} for name, path in score_paths.items()},
        },
        "models": {},
    }
    for name, frame in scores.items():
        scored = frame["score_status"].eq("scored")
        matched &= merged[f"{name}_score_status"].eq("scored")
        reason_counts = (
            frame.loc[~scored, "unavailable_reason"].fillna("").astype(str).value_counts().sort_index().to_dict()
        )
        report["models"][name] = {
            "scored_rows": int(scored.sum()),
            "unavailable_rows": int((~scored).sum()),
            "coverage": float(scored.mean()) if len(frame) else 0.0,
            "unavailable_reason_counts": {str(k): int(v) for k, v in reason_counts.items()},
        }
    matched_rows = merged.loc[matched].copy()
    if matched_rows.empty:
        raise ValueError("No flight IDs are scored by all three models; matched metrics cannot be computed.")
    y = matched_rows["delay_15"].to_numpy(dtype=int)
    report["matched_all_three_scored_rows"] = int(len(matched_rows))
    report["matched_delay_rate"] = float(y.mean())
    report["matched_metrics"] = {}
    for name in MODELS:
        threshold = float(matched_rows[f"{name}_decision_threshold"].iloc[0])
        probability = matched_rows[f"{name}_delay_probability"].to_numpy(dtype=float)
        report["matched_metrics"][name] = _metrics(y, probability, threshold)
    if "partition" in matched_rows.columns:
        report["matched_metrics_by_partition"] = {}
        for partition, group in matched_rows.groupby("partition", dropna=False, sort=True):
            group_y = group["delay_15"].to_numpy(dtype=int)
            report["matched_metrics_by_partition"][str(partition)] = {
                name: _metrics(
                    group_y,
                    group[f"{name}_delay_probability"].to_numpy(dtype=float),
                    float(group[f"{name}_decision_threshold"].iloc[0]),
                )
                for name in MODELS
            }
    report["protocol_status"] = "RETROSPECTIVE_MATCHED_COMPARISON; interpret per agreed protocol; not live-readiness evidence"
    report["unavailable_rows_are_preserved"] = True
    report["thresholds_were_reused_from_model_exports"] = True

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    horizon_slug = horizon.replace("−", "minus").replace("-", "minus")
    all_rows_path = output_stem.with_name(f"{output_stem.name}_{horizon_slug}_all_cohort_rows.csv.gz")
    matched_rows_path = output_stem.with_name(f"{output_stem.name}_{horizon_slug}_matched_rows.csv.gz")
    summary_path = output_stem.with_name(f"{output_stem.name}_{horizon_slug}_summary.json")
    merged.to_csv(all_rows_path, index=False, compression="gzip")
    matched_rows.to_csv(matched_rows_path, index=False, compression="gzip")
    report["all_cohort_rows_file"] = all_rows_path.name
    report["matched_rows_file"] = matched_rows_path.name
    report["outputs"] = {
        "all_cohort_rows": {"path": all_rows_path.name, "bytes": all_rows_path.stat().st_size, "sha256": _sha256(all_rows_path)},
        "matched_rows": {"path": matched_rows_path.name, "bytes": matched_rows_path.stat().st_size, "sha256": _sha256(matched_rows_path)},
    }
    summary_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", required=True, choices=["T-2h", "T-30m"])
    parser.add_argument("--cohort", required=True, type=Path, help="Approved canonical-ID cohort CSV/GZ with flight_id and delay_15")
    for name in MODELS:
        parser.add_argument(f"--{name.replace('_', '-')}", required=True, type=Path)
    parser.add_argument("--output-stem", required=True, type=Path)
    args = parser.parse_args()
    score_paths = {name: getattr(args, name) for name in MODELS}
    horizon = args.horizon.replace("-", "−")
    report = evaluate(args.cohort, score_paths, horizon, args.output_stem)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
