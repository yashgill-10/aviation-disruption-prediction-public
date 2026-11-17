"""Retrospective two-horizon logistic stacking experiment from frozen score exports.

Run from the project workspace with its existing sklearn 1.6.1 environment:
  python -B outputs/airport_network_canonical_integration/m3_september_2024_exploratory_v1/train_evaluate_m3.py

The script refuses to overwrite generated outputs. The directory may contain this
script and its focused test file before the first run; any other existing file is
treated as a conflict. Base-model predictions are read only and never regenerated.
"""
from __future__ import annotations

import array
import csv
import gzip
import hashlib
import json
import math
import os
import platform
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    confusion_matrix,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

OUT = Path(__file__).resolve().parent
# External inputs are private and not distributed. Point the environment
# variables below at the approved local copies; the relative defaults preserve
# the original workspace layout for the private reproduction environment.
BASE = Path(os.environ.get("M3_AN_EXPORT_DIR", str(OUT.parent)))
_workspace_root = OUT.parents[2] if len(OUT.parents) > 2 else OUT.parent
ROOT = Path(os.environ.get("M3_WORKSPACE_ROOT", str(_workspace_root)))
FW_DIR = Path(os.environ.get(
    "M3_FLIGHT_WEATHER_EXPORT_DIR",
    "data/flight_weather_score_exports_2024_h2_v1",
))
FW_MANIFEST_PATH = FW_DIR / "run_manifest.json"
AN_MANIFEST_PATH = BASE / "run_manifest.json"
PRELIM_MANIFEST_PATH = Path(os.environ.get(
    "M3_PRELIM_MANIFEST",
    str(ROOT / "outputs" / "phase2_local_preliminary_scores" / "run_manifest.json"),
))
AN_BUILDER_PATH = Path(os.environ.get(
    "M3_AN_BUILDER",
    str(ROOT / "outputs" / "build_airport_network_preliminary_scores.py"),
))

HORIZONS = ("t_minus_2h", "t_minus_30m")
MONTHS = (9, 10, 11, 12)
M3_TRAIN_MONTHS = (9,)
M3_THRESHOLD_MONTHS = (10,)
M3_EVALUATION_MONTHS = (11, 12)
EXPECTED_SEPTEMBER = {"t_minus_2h": 579_111, "t_minus_30m": 579_074}
EXPECTED_SCORE_SHA = {
    ("flight_weather", "t_minus_2h"): "4a44b8405b41380f46c310f129e819651a496c6e86cbda81d700494f7fcc4d93",
    ("flight_weather", "t_minus_30m"): "5f9950a3a3c98e8032755c3a043886838b6dd08d08033fbfe64a376bed047753",
}
EPSILON = 1e-6
MODEL_CONFIG = {
    "features": ["logit_flight_weather_probability", "logit_airport_network_probability"],
    "probability_clip_epsilon": EPSILON,
    "logit": "ln(p / (1 - p)) after clipping p to [epsilon, 1-epsilon]",
    "scaler": "sklearn.preprocessing.StandardScaler; fit on September rows only",
    "estimator": "sklearn.linear_model.LogisticRegression",
    "estimator_parameters": {"penalty": "l2", "C": 1.0, "solver": "lbfgs", "max_iter": 1000, "class_weight": None},
    "positive_class": 1,
    "seed": "Not applicable; deterministic full-batch LogisticRegression(lbfgs) with fixed inputs/configuration.",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_fresh_output_dir() -> None:
    # The operator verified the requested versioned directory was absent before
    # creating it. Permit only this script/test as staging files on first run.
    allowed = {"train_evaluate_m3.py", "test_m3_contract.py", "__pycache__"}
    unexpected = [p.name for p in OUT.iterdir() if p.name not in allowed]
    if unexpected:
        raise FileExistsError(f"Refusing to overwrite existing M3 outputs: {unexpected}")


def finite_probability(value: str, label: str) -> float:
    if value is None or not str(value).strip():
        raise ValueError(f"Missing {label} probability")
    p = float(value)
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError(f"Invalid {label} probability outside finite [0,1]")
    return p


def validate_canonical_id(fid: str) -> str:
    if not fid or not fid.startswith("bts-flight-v1:"):
        raise ValueError("Missing or invalid canonical flight_id")
    return fid


def partition_for_month(month: int) -> str:
    if month in M3_TRAIN_MONTHS:
        return "meta_training"
    if month in M3_THRESHOLD_MONTHS:
        return "threshold_selection"
    if month in M3_EVALUATION_MONTHS:
        return "evaluation"
    raise ValueError(f"Month {month} is outside the approved M3 protocol")


def validate_target_pair(y_fw: int, y_an: int) -> int:
    if y_fw not in (0, 1) or y_an not in (0, 1):
        raise ValueError("Targets must be binary")
    if y_fw != y_an:
        raise ValueError("Flight/Weather and Airport/Network targets disagree")
    return y_fw


def validate_component_record(*, horizon: str, observed_horizon: str, status: str,
                              reason: str, probability: str, threshold: str,
                              label: str, model: str) -> tuple[float, float, int]:
    if observed_horizon != horizon:
        raise ValueError(f"Horizon mismatch in {model} score input")
    if status != "scored" or (reason or "").strip():
        raise ValueError(f"Unexpected unavailable {model} prediction in approved common cohort")
    p = finite_probability(probability, model)
    t = finite_probability(threshold, f"{model} threshold")
    yhat = int(label)
    if yhat not in (0, 1) or yhat != int(p >= t):
        raise ValueError(f"{model} binary decision inconsistent with stored continuous score/threshold")
    return p, t, yhat


def logit_features(p_fw: np.ndarray, p_an: np.ndarray, *, epsilon: float = EPSILON) -> np.ndarray:
    """Transform only the two probabilities; no ID/target/date/threshold features."""
    a = np.asarray(p_fw, dtype=np.float64)
    b = np.asarray(p_an, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("Component probabilities must be aligned one-dimensional arrays")
    if not np.isfinite(a).all() or not np.isfinite(b).all() or ((a < 0) | (a > 1) | (b < 0) | (b > 1)).any():
        raise ValueError("Component probabilities must be finite and within [0,1]")
    a = np.clip(a, epsilon, 1 - epsilon)
    b = np.clip(b, epsilon, 1 - epsilon)
    return np.column_stack((np.log(a / (1 - a)), np.log(b / (1 - b))))


def select_f1_threshold(y: np.ndarray, probability: np.ndarray) -> tuple[float, float]:
    precision, recall, thresholds = precision_recall_curve(y, probability, pos_label=1)
    if thresholds.size == 0:
        raise ValueError("Cannot select a threshold from an empty or single-class October sample")
    f1 = 2 * precision[:-1] * recall[:-1] / (precision[:-1] + recall[:-1] + 1e-15)
    best = int(np.nanargmax(f1))
    return float(thresholds[best]), float(f1[best])


def metrics(y: np.ndarray, probability: np.ndarray, threshold: float) -> dict[str, Any]:
    y = np.asarray(y, dtype=np.int8)
    p = np.asarray(probability, dtype=np.float64)
    pred = (p >= threshold).astype(np.int8)
    return {
        "n": int(len(y)),
        "average_precision": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "threshold": float(threshold),
        "confusion_matrix_tn_fp_fn_tp": confusion_matrix(y, pred, labels=[0, 1]).ravel().tolist(),
    }


def source_paths(horizon: str) -> dict[str, Path]:
    return {
        "flight_weather_scores": FW_DIR / f"{horizon}_scores.csv.gz",
        "airport_network_scores": BASE / f"airport_network_{horizon}_scores.csv.gz",
        "airport_network_targets": BASE / f"airport_network_{horizon}_evaluation_targets.csv.gz",
        "airport_network_features": BASE / f"airport_network_{horizon}_feature_inputs.csv.gz",
    }


def empty_period() -> dict[str, Any]:
    return {
        "p_fw": array.array("d"), "p_an": array.array("d"), "y": array.array("b"),
        "fw_threshold": array.array("d"), "fw_label": array.array("b"),
        "an_threshold": array.array("d"), "an_label": array.array("b"),
        "ids": [],
    }


def load_and_join_horizon(horizon: str, paths: dict[str, Path], fw_manifest: dict[str, Any],
                          an_manifest: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    fw_path = paths["flight_weather_scores"]
    fw_hash = sha256_file(fw_path)
    expected = EXPECTED_SCORE_SHA[("flight_weather", horizon)]
    if fw_hash != expected:
        raise ValueError(f"Flight/Weather score checksum mismatch for {horizon}")
    if fw_manifest["outputs"][horizon]["output_sha256"] != fw_hash:
        raise ValueError(f"Flight/Weather score hash differs from its run manifest for {horizon}")
    an_horizon_manifest = an_manifest["horizons"][horizon]
    source_hashes = {key: sha256_file(path) for key, path in paths.items()}
    expected_an_hashes = {
        "airport_network_scores": an_horizon_manifest["score_export"]["score_file_sha256"],
        "airport_network_targets": an_horizon_manifest["target_table"]["sha256"],
        "airport_network_features": an_horizon_manifest["feature_inputs"]["sha256"],
    }
    for key, expected_hash in expected_an_hashes.items():
        if source_hashes[key] != expected_hash:
            raise ValueError(f"Airport/Network source checksum mismatch for {horizon}/{key}")

    fw_rows: dict[str, tuple[int, float, float, int, int]] = {}
    fw_count = defaultdict(int)
    fw_duplicate_rows = 0
    fw_duplicate_by_month = defaultdict(int)
    fw_missing_prob = 0
    fw_unavailable = 0
    fw_thresholds: set[float] = set()
    fw_model = fw_manifest["models"][horizon]
    expected_fw_threshold = float(fw_model["threshold"])
    with gzip.open(fw_path, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["horizon"] != horizon:
                raise ValueError(f"Flight/Weather horizon mismatch in {horizon} export")
            date = r["FlightDate"]
            try:
                parsed_date = datetime.strptime(date[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError) as exc:
                raise ValueError("Malformed Flight/Weather date") from exc
            if parsed_date.year != 2024:
                raise ValueError("Malformed or out-of-year Flight/Weather date")
            month = parsed_date.month
            if month not in MONTHS:
                continue
            fw_count[month] += 1
            status = r["score_status"]
            reason = r["unavailable_reason"] or ""
            if status != "scored":
                fw_unavailable += 1
            prob_text = r["predicted_delay_probability"]
            if not prob_text or not prob_text.strip():
                fw_missing_prob += 1
                continue
            p, threshold, label = validate_component_record(
                horizon=horizon, observed_horizon=r["horizon"], status=status,
                reason=reason, probability=prob_text, threshold=r["threshold"],
                label=r["predicted_delay_15"], model="Flight/Weather",
            )
            y = int(r["DepDel15"])
            if y not in (0, 1):
                raise ValueError("Non-binary Flight/Weather target")
            if not math.isclose(threshold, expected_fw_threshold, abs_tol=1e-12):
                raise ValueError("Flight/Weather score threshold differs from run manifest")
            fw_thresholds.add(threshold)
            fid = validate_canonical_id(r["flight_id"])
            if fid in fw_rows:
                fw_duplicate_rows += 1
                fw_duplicate_by_month[month] += 1
                continue
            fw_rows[fid] = (y, p, threshold, label, month)
    if fw_duplicate_rows or fw_missing_prob or fw_unavailable:
        raise ValueError(f"Flight/Weather input integrity failure for {horizon}")
    if len(fw_thresholds) != 1:
        raise ValueError(f"Flight/Weather threshold is not constant for {horizon}")

    periods = {m: empty_period() for m in MONTHS}
    an_count = defaultdict(int)
    an_duplicate_rows = 0
    an_duplicate_by_month = defaultdict(int)
    an_missing_prob = 0
    an_unavailable = 0
    an_extra_ids = 0
    target_mismatches = 0
    common = defaultdict(int)
    an_thresholds: set[float] = set()
    expected_an_threshold = float(an_manifest["saved_thresholds_and_rule"][horizon])
    with gzip.open(paths["airport_network_scores"], "rt", encoding="utf-8", newline="") as fs, \
         gzip.open(paths["airport_network_targets"], "rt", encoding="utf-8", newline="") as ft, \
         gzip.open(paths["airport_network_features"], "rt", encoding="utf-8", newline="") as ff:
        score_it, target_it, feature_it = csv.DictReader(fs), csv.DictReader(ft), csv.DictReader(ff)
        for s, t, x in zip(score_it, target_it, feature_it, strict=True):
            month = int(x["MONTH"])
            if month not in MONTHS:
                continue
            an_count[month] += 1
            if s["flight_id"] != t["flight_id"] or s["flight_id"] != x["flight_id"]:
                raise ValueError("Airport/Network score, target, and feature keys differ")
            if s["horizon"] != horizon or t["horizon"] != horizon or x["horizon"] != horizon:
                raise ValueError(f"Airport/Network horizon mismatch for {horizon}")
            status = s["score_status"]
            reason = s["unavailable_reason"] or ""
            if status != "scored":
                an_unavailable += 1
            prob_text = s["p_delay15"]
            if not prob_text or not prob_text.strip():
                an_missing_prob += 1
                continue
            p, threshold, label = validate_component_record(
                horizon=horizon, observed_horizon=s["horizon"], status=status,
                reason=reason, probability=prob_text, threshold=s["decision_threshold"],
                label=s["predicted_label"], model="Airport/Network",
            )
            y = int(t["DepDel15"])
            if y not in (0, 1):
                raise ValueError("Non-binary Airport/Network target")
            if not math.isclose(threshold, expected_an_threshold, abs_tol=1e-12):
                raise ValueError("Airport/Network score threshold differs from run manifest")
            an_thresholds.add(threshold)
            fid = validate_canonical_id(s["flight_id"])
            fw_record = fw_rows.get(fid, "__missing__")
            if fw_record == "__extra_seen__":
                an_duplicate_rows += 1
                an_duplicate_by_month[month] += 1
                continue
            if fw_record == "__missing__":
                an_extra_ids += 1
                fw_rows[fid] = "__extra_seen__"
                continue
            if fw_record is None:
                an_duplicate_rows += 1
                an_duplicate_by_month[month] += 1
                continue
            fw_y, fw_p, fw_threshold, fw_label, fw_month = fw_record
            if month != fw_month:
                raise ValueError("September/October/November/December membership differs across sources")
            try:
                validate_target_pair(fw_y, y)
            except ValueError:
                target_mismatches += 1
                continue
            fw_rows[fid] = None  # duplicate Airport/Network keys are caught without a second large set.
            common[month] += 1
            z = periods[month]
            z["p_fw"].append(fw_p); z["p_an"].append(p); z["y"].append(y)
            z["fw_threshold"].append(fw_threshold); z["fw_label"].append(fw_label)
            z["an_threshold"].append(threshold); z["an_label"].append(label)
            if month >= 10:
                z["ids"].append(fid)

    fw_missing_ids = sum(v is not None for v in fw_rows.values())
    if fw_duplicate_rows or fw_missing_ids or an_duplicate_rows or an_extra_ids or target_mismatches:
        raise ValueError(f"Incomplete/duplicate/target-mismatched exact ID join for {horizon}")
    if fw_missing_prob or an_missing_prob or fw_unavailable or an_unavailable:
        raise ValueError(f"Missing/unavailable probabilities in M3 input for {horizon}")
    if len(an_thresholds) != 1:
        raise ValueError(f"Airport/Network threshold is not constant for {horizon}")
    for month in MONTHS:
        if fw_count[month] != an_count[month] or fw_count[month] != common[month]:
            raise ValueError(f"Input coverage mismatch for {horizon}, month {month}")
    if common[9] != EXPECTED_SEPTEMBER[horizon]:
        raise ValueError(f"September common-ID count changed for {horizon}")
    if common[10] == 0 or common[11] == 0 or common[12] == 0:
        raise ValueError(f"Missing October–December period for {horizon}")

    report = {
        "status": "PASS",
        "counts_by_month": {str(m): {"flight_weather": fw_count[m], "airport_network": an_count[m], "common_ids": common[m]} for m in MONTHS},
        "september_training_rows": common[9],
        "october_threshold_selection_rows": common[10],
        "november_december_evaluation_rows": common[11] + common[12],
        "september_duplicate_ids_flight_weather": fw_duplicate_by_month[9],
        "september_duplicate_ids_airport_network": an_duplicate_by_month[9],
        "duplicate_id_rows_flight_weather_sep_dec": fw_duplicate_rows,
        "duplicate_id_rows_airport_network_sep_dec": an_duplicate_rows,
        "target_mismatches": target_mismatches,
        "missing_probabilities_flight_weather": fw_missing_prob,
        "missing_probabilities_airport_network": an_missing_prob,
        "unavailable_flight_weather": fw_unavailable,
        "unavailable_airport_network": an_unavailable,
        "extra_ids_airport_network": an_extra_ids,
        "missing_ids_airport_network": fw_missing_ids,
        "component_thresholds": {"flight_weather": next(iter(fw_thresholds)), "airport_network": next(iter(an_thresholds))},
        "source_sha256": {"flight_weather_scores": fw_hash, **source_hashes},
        "duplicate_id_rows_flight_weather_sep_dec": fw_duplicate_rows,
        "duplicate_id_rows_airport_network_sep_dec": an_duplicate_rows,
        "duplicate_id_rows_by_month_flight_weather": {str(m): fw_duplicate_by_month[m] for m in MONTHS},
        "duplicate_id_rows_by_month_airport_network": {str(m): an_duplicate_by_month[m] for m in MONTHS},
    }
    return periods, report


def export_scores(path: Path, horizon: str, partitions: list[tuple[str, dict[str, Any], np.ndarray, float]]) -> int:
    columns = ["flight_id", "target", "horizon", "partition", "delay_probability", "decision_threshold",
               "predicted_label", "score_status", "unavailable_reason", "flight_weather_probability",
               "airport_network_probability", "flight_weather_score_status", "airport_network_score_status"]
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as gz:
            import io
            text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
            writer = csv.DictWriter(text, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            written = 0
            for partition, data, m3, threshold in partitions:
                p_fw = np.frombuffer(data["p_fw"], dtype=np.float64)
                p_an = np.frombuffer(data["p_an"], dtype=np.float64)
                y = np.frombuffer(data["y"], dtype=np.int8)
                if not (len(data["ids"]) == len(m3) == len(y)):
                    raise ValueError(f"Export row count mismatch for {horizon}/{partition}")
                for i, fid in enumerate(data["ids"]):
                    writer.writerow({
                        "flight_id": fid,
                        "target": int(y[i]),
                        "horizon": horizon,
                        "partition": partition,
                        "delay_probability": f"{m3[i]:.17g}",
                        "decision_threshold": f"{threshold:.17g}",
                        "predicted_label": int(m3[i] >= threshold),
                        "score_status": "scored",
                        "unavailable_reason": "",
                        "flight_weather_probability": f"{p_fw[i]:.17g}",
                        "airport_network_probability": f"{p_an[i]:.17g}",
                        "flight_weather_score_status": "scored",
                        "airport_network_score_status": "scored",
                    })
                    written += 1
            text.flush()
    return written


def partition_score_rows(data: dict[str, Any], model: Pipeline) -> np.ndarray:
    p_fw = np.frombuffer(data["p_fw"], dtype=np.float64)
    p_an = np.frombuffer(data["p_an"], dtype=np.float64)
    y = np.frombuffer(data["y"], dtype=np.int8)
    x = logit_features(p_fw, p_an)
    m3 = model.predict_proba(x)[:, 1]
    return m3


def load_manifests() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    fw = json.loads(FW_MANIFEST_PATH.read_text(encoding="utf-8"))
    an = json.loads(AN_MANIFEST_PATH.read_text(encoding="utf-8"))
    preliminary = json.loads(PRELIM_MANIFEST_PATH.read_text(encoding="utf-8"))
    return fw, an, preliminary


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main() -> None:
    ensure_fresh_output_dir()
    required = [FW_MANIFEST_PATH, AN_MANIFEST_PATH, PRELIM_MANIFEST_PATH, AN_BUILDER_PATH]
    if not all(p.is_file() for p in required):
        raise FileNotFoundError("A required source manifest or builder file is missing; nothing was trained.")
    fw_manifest, an_manifest, prelim_manifest = load_manifests()
    if fw_manifest.get("status") != "PASS" or not an_manifest.get("status", "").startswith("AIRPORT_NETWORK_CANONICAL_EXPORTS_VALIDATED"):
        raise ValueError("Source manifests do not report a valid handoff; nothing was trained.")

    prepared: dict[str, tuple[dict[int, dict[str, Any]], dict[str, Any]]] = {}
    # Validate both complete horizon joins before fitting either M3 model.
    for horizon in HORIZONS:
        paths = source_paths(horizon)
        if not all(p.is_file() for p in paths.values()):
            raise FileNotFoundError(f"Source score/target/feature file missing for {horizon}; nothing was trained.")
        periods, validation = load_and_join_horizon(horizon, paths, fw_manifest, an_manifest)
        prepared[horizon] = (periods, validation)

    all_results: dict[str, Any] = {}
    for horizon in HORIZONS:
        periods, validation = prepared[horizon]
        # Fit only September; October and Nov-Dec are held out from both scaler and estimator fitting.
        if set(M3_TRAIN_MONTHS) & (set(M3_THRESHOLD_MONTHS) | set(M3_EVALUATION_MONTHS)) or set(M3_THRESHOLD_MONTHS) & set(M3_EVALUATION_MONTHS):
            raise AssertionError("M3 date partitions overlap")
        if set(M3_TRAIN_MONTHS + M3_THRESHOLD_MONTHS + M3_EVALUATION_MONTHS) != set(MONTHS):
            raise AssertionError("M3 date partitions do not cover exactly September–December")
        train = periods[M3_TRAIN_MONTHS[0]]
        y_train = np.frombuffer(train["y"], dtype=np.int8)
        x_train = logit_features(np.frombuffer(train["p_fw"], dtype=np.float64), np.frombuffer(train["p_an"], dtype=np.float64))
        if len(y_train) != EXPECTED_SEPTEMBER[horizon] or len(np.unique(y_train)) != 2:
            raise ValueError(f"Invalid September M3 training set for {horizon}")
        model = Pipeline([
            ("scaler", StandardScaler()),
            ("logistic_regression", LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000,
                                                         class_weight=None, penalty="l2")),
        ])
        model.fit(x_train, y_train)
        classes = model.named_steps["logistic_regression"].classes_.astype(int).tolist()
        if classes != [0, 1]:
            raise ValueError(f"Unexpected class order for {horizon}: {classes}")

        p_oct = partition_score_rows(periods[M3_THRESHOLD_MONTHS[0]], model)
        y_oct = np.frombuffer(periods[M3_THRESHOLD_MONTHS[0]]["y"], dtype=np.int8)
        threshold, october_f1 = select_f1_threshold(y_oct, p_oct)
        p_nov = partition_score_rows(periods[M3_EVALUATION_MONTHS[0]], model)
        p_dec = partition_score_rows(periods[M3_EVALUATION_MONTHS[1]], model)

        eval_y = np.concatenate([np.frombuffer(periods[m]["y"], dtype=np.int8) for m in M3_EVALUATION_MONTHS])
        eval_fw = np.concatenate([np.frombuffer(periods[m]["p_fw"], dtype=np.float64) for m in M3_EVALUATION_MONTHS])
        eval_an = np.concatenate([np.frombuffer(periods[m]["p_an"], dtype=np.float64) for m in M3_EVALUATION_MONTHS])
        eval_fw_threshold = float(np.frombuffer(periods[M3_EVALUATION_MONTHS[0]]["fw_threshold"], dtype=np.float64)[0])
        eval_an_threshold = float(np.frombuffer(periods[M3_EVALUATION_MONTHS[0]]["an_threshold"], dtype=np.float64)[0])
        if any(not np.all(np.frombuffer(periods[m]["fw_threshold"], dtype=np.float64) == eval_fw_threshold) for m in M3_EVALUATION_MONTHS):
            raise ValueError("Flight/Weather thresholds vary in November–December")
        if any(not np.all(np.frombuffer(periods[m]["an_threshold"], dtype=np.float64) == eval_an_threshold) for m in M3_EVALUATION_MONTHS):
            raise ValueError("Airport/Network thresholds vary in November–December")
        p_m3 = np.concatenate((p_nov, p_dec))
        p_mean = (eval_fw + eval_an) / 2.0
        comparison = {
            "matched_common_scored_count": int(len(eval_y)),
            "flight_weather": metrics(eval_y, eval_fw, eval_fw_threshold),
            "airport_network": metrics(eval_y, eval_an, eval_an_threshold),
            "m3_logistic_stack": metrics(eval_y, p_m3, threshold),
            "component_probability_mean_descriptive_only": metrics(eval_y, p_mean, 0.5),
            "mean_benchmark_threshold_note": "Fixed 0.5 only for descriptive binary metrics; no tuning or model selection used this benchmark.",
        }
        model_path = OUT / f"m3_{horizon}_model.joblib"
        joblib.dump(model, model_path, compress=3)
        score_path = OUT / f"m3_{horizon}_scores.csv.gz"
        export_rows = export_scores(score_path, horizon, [
            ("october_threshold_selection", periods[M3_THRESHOLD_MONTHS[0]], p_oct, threshold),
            ("november_december_evaluation", periods[M3_EVALUATION_MONTHS[0]], p_nov, threshold),
            ("november_december_evaluation", periods[M3_EVALUATION_MONTHS[1]], p_dec, threshold),
        ])
        if export_rows != validation["october_threshold_selection_rows"] + validation["november_december_evaluation_rows"]:
            raise ValueError("M3 export row count differs from validated partition counts")
        # Re-read persisted CSV and recompute all aggregate scores from its ND partition.
        saved_y, saved_p = [], []
        with gzip.open(score_path, "rt", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["partition"] == "november_december_evaluation":
                    if row["horizon"] != horizon or row["score_status"] != "scored" or row["unavailable_reason"]:
                        raise ValueError("Invalid saved M3 evaluation row status/horizon")
                    saved_y.append(int(row["target"]))
                    saved_p.append(float(row["delay_probability"]))
        if len(saved_y) != len(eval_y) or not np.allclose(saved_y, eval_y, rtol=0, atol=0) or not np.allclose(saved_p, p_m3, rtol=1e-12, atol=1e-15):
            raise AssertionError("Saved M3 export does not reproduce in-memory evaluation inputs/metrics")
        saved_metrics = metrics(np.asarray(saved_y, dtype=np.int8), np.asarray(saved_p), threshold)
        if not math.isclose(saved_metrics["average_precision"], comparison["m3_logistic_stack"]["average_precision"], rel_tol=1e-12, abs_tol=1e-15):
            raise AssertionError("Aggregate AP does not reproduce from saved M3 export")
        all_results[horizon] = {
            "input_validation": validation,
            "training": {"period": "2024-09-01 through 2024-09-30", "n": int(len(y_train)),
                         "positive_count": int(y_train.sum()), "negative_count": int((y_train == 0).sum()),
                         "class_order": classes, "fit_months": [9]},
            "threshold_selection": {"period": "2024-10-01 through 2024-10-31", "n": int(len(y_oct)),
                                    "selected_threshold_max_f1": threshold, "october_f1_at_selected_threshold": october_f1},
            "evaluation": {"period": "2024-11-01 through 2024-12-31", "n": int(len(eval_y)),
                           "exploratory_not_blind_final_test": True, "comparison": comparison},
            "m3_model_path": model_path.name,
            "m3_model_sha256": sha256_file(model_path),
            "m3_score_export_path": score_path.name,
            "m3_score_export_rows": export_rows,
            "m3_score_export_sha256": sha256_file(score_path),
            "saved_export_metric_reproducibility": "PASS: saved M3 score CSV reread; targets, probabilities, and AP reproduce within stated floating-point tolerance.",
        }

    report = {
        "status": "PASS_RETROSPECTIVE_EXPLORATORY_M3",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "periods": {"meta_training": "2024-09-01 through 2024-09-30", "threshold_selection": "2024-10-01 through 2024-10-31", "evaluation": "2024-11-01 through 2024-12-31"},
        "interpretation": "Retrospective exploratory stacking; Nov-Dec has already been inspected, not a blind final test.",
        "m3_configuration": MODEL_CONFIG,
        "horizons": all_results,
        "limitations": [
            "Airport/Network original scoring-time builder source has not been recovered; recorded/current builder hashes differ.",
            "Airport/Network historical and recent activity features use retrospective BTS outcomes; schedule/history availability timestamps are unverified.",
            "NOAA actual receipt timestamps are unavailable; Flight/Weather uses observation-time plus 15-minute availability proxy.",
            "These results are retrospective research, not live operational evidence.",
            "No untouched final-test period is established; the 2025 period was previously inspected.",
            "Flight/Weather calibration and score-generation provenance is documented in its handoff manifest, but its scoring code/calibrator artifact was not independently available in this workspace.",
        ],
        "base_models_retrained": False,
        "base_predictions_regenerated": False,
    }
    report_path = OUT / "m3_evaluation_report.json"
    write_json(report_path, report)

    fw_manifest_hash = sha256_file(FW_MANIFEST_PATH)
    an_manifest_hash = sha256_file(AN_MANIFEST_PATH)
    current_builder_hash = sha256_file(AN_BUILDER_PATH)
    recorded_builder_hash = prelim_manifest["feature_builder_sha256"]
    m3_manifest = {
        "status": "PASS_RETROSPECTIVE_EXPLORATORY_M3",
        "created_utc": report["created_utc"],
        "dates": report["periods"],
        "cohort_target_horizons": {"identity": "bts-flight-v1 canonical flight_id", "target": "DepDel15 = 1 iff BTS departure delay >= 15 minutes among non-cancelled flights", "horizons": list(HORIZONS)},
        "source_manifests": {"flight_weather_run_manifest_sha256": fw_manifest_hash, "airport_network_run_manifest_sha256": an_manifest_hash},
        "source_score_hashes_and_validation": {h: all_results[h]["input_validation"] for h in HORIZONS},
        "source_models": {
            "flight_weather": {h: {"description": "Saved SGDClassifier plus saved Platt LogisticRegression calibrator; trained 2022–2023; calibration/threshold selection Jan–Jun 2024.", "sha256": fw_manifest["models"][h]["sha256"]} for h in HORIZONS},
            "airport_network": {"description": "Saved horizon-specific sklearn Pipeline: one-hot preprocessing plus RandomForestClassifier; fitted on Jan–Aug 2024 sample; September used for threshold selection.", "bundle_sha256": an_manifest["model_bundle_sha256"]},
        },
        "airport_network_feature_builder": {"recorded_scoring_builder_sha256": recorded_builder_hash, "current_local_builder_sha256": current_builder_hash, "original_builder_recovered": False, "identity_status": "MISMATCH; original scoring-time bytes unavailable"},
        "m3_configuration": MODEL_CONFIG,
        "runtime_versions": {"python": platform.python_version(), "numpy": np.__version__, "scikit_learn": sklearn.__version__, "joblib": joblib.__version__},
        "horizons": {h: {"training": all_results[h]["training"], "threshold_selection": all_results[h]["threshold_selection"], "evaluation": {"period": all_results[h]["evaluation"]["period"], "n": all_results[h]["evaluation"]["n"]}, "m3_model_sha256": all_results[h]["m3_model_sha256"], "m3_score_export_sha256": all_results[h]["m3_score_export_sha256"], "m3_score_export_rows": all_results[h]["m3_score_export_rows"], "saved_export_metric_reproducibility": all_results[h]["saved_export_metric_reproducibility"]} for h in HORIZONS},
        "limitations": report["limitations"],
        "base_models_retrained": False,
        "base_predictions_regenerated": False,
    }
    manifest_path = OUT / "m3_run_manifest.json"
    write_json(manifest_path, m3_manifest)
    (OUT / "m3_run_manifest.sha256").write_text(f"{sha256_file(manifest_path)}  m3_run_manifest.json\n", encoding="utf-8")
    write_readme()
    # Final inventory hash list excludes only itself and __pycache__.
    checksum_lines = []
    for p in sorted(OUT.iterdir()):
        if p.is_file() and p.name != "SHA256SUMS.txt":
            checksum_lines.append(f"{sha256_file(p)}  {p.name}")
    (OUT / "SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    verify_checksums()
    print(json.dumps({h: {"train_n": all_results[h]["training"]["n"], "october_n": all_results[h]["threshold_selection"]["n"], "october_threshold": all_results[h]["threshold_selection"]["selected_threshold_max_f1"], "nov_dec_n": all_results[h]["evaluation"]["n"], "comparison": all_results[h]["evaluation"]["comparison"]} for h in HORIZONS}, indent=2))
    print(f"Manifest SHA-256: {sha256_file(manifest_path)}")
    print(f"Verified {len(checksum_lines)} output checksums.")


def write_readme() -> None:
    text = """# M3 retrospective logistic stacking experiment

This local experiment fits only the two M3 logistic stacking models using existing, matched September 2024 component probabilities. It does not retrain either base model or regenerate base predictions.

## Reproduction

From the project workspace, run the existing scikit-learn 1.6.1 environment:

```powershell
& '.\\outputs\\phase2_ordinal_reconstruction\\venv_sklearn_1_6_1\\Scripts\\python.exe' -B '.\\outputs\\airport_network_canonical_integration\\m3_september_2024_exploratory_v1\\train_evaluate_m3.py'
```

The script validates full input hashes, canonical-ID uniqueness, target parity, probability/status validity, horizon/date membership, and exact September counts before fitting. It refuses to overwrite generated files.

## Protocol

- Meta-train: September 2024 only, independently per horizon.
- Fit `StandardScaler` on September logit-transformed component probabilities, then L2 `LogisticRegression(C=1.0, solver='lbfgs', max_iter=1000, class_weight=None)`.
- Select each M3 decision threshold on October 2024 by F1; do not refit the model or scaler.
- Evaluate November–December 2024 with frozen M3 models/thresholds. This period was already inspected and is exploratory, not a blind final test.
- The arithmetic mean component probability is descriptive only; its binary metrics use a fixed 0.5 threshold, not a tuned value.

The score exports contain October threshold-selection and November–December evaluation predictions. September in-sample M3 predictions are deliberately not exported.

## Limitations

The original Airport/Network scoring-time builder source is unrecovered and its recorded hash differs from the current local builder. Airport/Network features use retrospective BTS outcomes; BTS schedule/history availability timestamps are unverified. NOAA actual receipt timestamps are unavailable; Flight/Weather uses an observation-time-plus-15-minute proxy. Flight/Weather score chronology is documented by its manifest, but its scoring implementation/calibrator artifact was not independently present in this workspace. These are retrospective research results, not live operational evidence. No untouched final-test period is established; the 2025 period was previously inspected.

See `m3_evaluation_report.json`, `m3_run_manifest.json`, and `SHA256SUMS.txt` for counts, metrics, source hashes, artifact hashes, validation outcomes, and provenance details.
"""
    (OUT / "README.md").write_text(text, encoding="utf-8")


def verify_checksums() -> None:
    manifest = OUT / "SHA256SUMS.txt"
    entries = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        if Path(name).name != name:
            raise ValueError("Unexpected nested path in output checksum manifest")
        if sha256_file(OUT / name) != expected:
            raise ValueError(f"Output checksum mismatch: {name}")
        entries += 1
    if entries != len([p for p in OUT.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt"]):
        raise ValueError("Output checksum manifest does not cover every output file")


if __name__ == "__main__":
    main()
