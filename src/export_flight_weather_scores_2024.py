from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
import platform
from pathlib import Path
import sys
import tempfile
from typing import Any

import duckdb
import joblib
import numpy as np
import pandas as pd
import sklearn
import pyarrow.parquet as pq

from canonical_flight_identity import VERSION
from train_baseline import CATEGORICAL_FEATURES, encode_frame, clean_categories

HORIZONS = ("t_minus_2h", "t_minus_30m")
EVALUATION_START = "2024-07-01"
EVALUATION_END_EXCLUSIVE = "2025-01-01"
H2_MONTHS = ("2024-07", "2024-08", "2024-09", "2024-10", "2024-11", "2024-12")
CUTOFF_AVAILABILITY_STATUS = "retrospective_source_availability_unverified"

OUTPUT_FIELDS = [
    "flight_id", "horizon", "score_status", "unavailable_reason",
    "predicted_delay_probability", "predicted_delay_15", "threshold", "DepDel15",
    "eligibility_rule", "final_DEP_DELAY_used_for_retrospective_eligibility",
    "FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline", "Origin", "Dest", "CRSDepTime",
    "identity_status", "source_archive", "source_archive_sha256", "source_csv_member", "source_row_ordinal",
    "origin_weather_missing", "destination_weather_missing", "origin_weather_age_minutes", "destination_weather_age_minutes",
    "model_input_support_status", "unsupported_model_inputs", "cutoff_availability_status",
    "model_name", "model_sha256", "feature_schema_sha256",
]


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feature_schema(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": artifact.get("target"),
        "categorical_features": [str(value) for value in artifact.get("categorical_features", [])],
        "numeric_features": [str(value) for value in artifact.get("numeric_features", [])],
        "historical_features": [str(value) for value in artifact.get("historical_features", [])],
        "weather_features": [str(value) for value in artifact.get("weather_features", [])],
        "weather_numeric_features": [str(value) for value in artifact.get("weather_numeric_features", [])],
    }


def feature_schema_hash(artifact: dict[str, Any]) -> str:
    payload = json.dumps(feature_schema(artifact), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-root", type=Path, default=Path("data/processed/flight_weather_score_prep_2024_h2_v1/weather"))
    parser.add_argument("--cohort-dir", type=Path, default=Path("data/processed/shared_cohort_2024_20261016_v1"))
    parser.add_argument("--model-root", type=Path, default=Path("data/processed/depdel15/weather_models"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/flight_weather_score_exports_2024_h2_v1"))
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args(argv)


def h2_feature_files(feature_root: Path, horizon: str) -> list[Path]:
    """Return exactly the six July--December feature partitions for a horizon."""
    directory = feature_root / horizon / "flights" / "validation"
    actual = {path.stem: path for path in directory.glob("*.parquet")}
    expected = set(H2_MONTHS)
    missing = sorted(expected - set(actual))
    unexpected = sorted(set(actual) - expected)
    if missing or unexpected or len(actual) != len(H2_MONTHS):
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if unexpected:
            detail.append("unexpected=" + ",".join(unexpected))
        raise FileNotFoundError(f"{horizon}: expected exactly July--December 2024 feature files ({'; '.join(detail)})")
    return [actual[label] for label in H2_MONTHS]


def export_status(summary: dict[str, int]) -> str:
    """Only a fully aligned, complete cohort can be a passing H2 score export.

    PASS requires:
    - Exact July-December 2024 partition coverage (enforced by h2_feature_files)
    - All cohort rows have matching feature rows
    - No target mismatches between cohort and features
    - No provenance mismatches
    - No unresolved identity status in feature rows
    - No missing required cohort features
    """
    required_zero = (
        "membership_rows_without_features",
        "target_mismatches",
        "provenance_mismatches",
        "feature_rows_with_unresolved_identity",
    )
    if summary["membership_rows"] != summary["matched_feature_rows"]:
        return "FAIL"
    if not all(summary.get(key, 0) == 0 for key in required_zero):
        return "FAIL"
    return "PASS"


def _validate_horizon_inputs(
    connection: duckdb.DuckDBPyConnection,
    horizon: str,
    feature_files: list[Path],
    membership_path: Path,
) -> dict[str, int]:
    feature_list = ", ".join(sql_string(path.resolve()) for path in feature_files)
    membership_sql = sql_string(membership_path.resolve())
    feature_view = f"features_{horizon}"
    membership_table = f"membership_{horizon}"
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW {feature_view} AS "
        f"SELECT * FROM read_parquet([{feature_list}], union_by_name=true)"
    )
    connection.execute(
        f"""CREATE OR REPLACE TEMP TABLE {membership_table} AS
        SELECT flight_id, horizon, eligibility_rule, CAST(FlightDate AS DATE) AS FlightDate,
               Reporting_Airline, Flight_Number_Reporting_Airline, Origin, Dest, CRSDepTime,
               CAST(DepDel15 AS INTEGER) AS cohort_target,
               CAST(final_DEP_DELAY_used_for_retrospective_eligibility AS DOUBLE) AS retrospective_dep_delay,
               CAST(Cancelled AS INTEGER) AS Cancelled,
               source_archive, source_archive_sha256, source_csv_member,
               CAST(source_data_row_ordinal AS BIGINT) AS source_row_ordinal
        FROM read_csv_auto({membership_sql}, header=true)
        WHERE CAST(FlightDate AS DATE) >= DATE '{EVALUATION_START}'
          AND CAST(FlightDate AS DATE) < DATE '{EVALUATION_END_EXCLUSIVE}'"""
    )
    membership_count, membership_ids, null_membership_ids, bad_labels, bad_cancelled, wrong_horizon = connection.execute(
        f"""SELECT COUNT(*), COUNT(DISTINCT flight_id),
                   COUNT(*) FILTER (WHERE flight_id IS NULL OR trim(CAST(flight_id AS VARCHAR))=''),
                   COUNT(*) FILTER (WHERE cohort_target IS NULL OR cohort_target NOT IN (0,1)),
                   COUNT(*) FILTER (WHERE Cancelled IS NULL OR Cancelled <> 0),
                   COUNT(*) FILTER (WHERE horizon IS DISTINCT FROM {sql_string(horizon)})
            FROM {membership_table}"""
    ).fetchone()
    if membership_count != membership_ids or null_membership_ids or bad_labels or bad_cancelled or wrong_horizon:
        raise ValueError(
            f"{horizon}: invalid membership keys/labels/statuses "
            f"(rows={membership_count}, unique={membership_ids}, null_ids={null_membership_ids}, "
            f"bad_labels={bad_labels}, cancelled={bad_cancelled}, horizon_mismatch={wrong_horizon})"
        )

    feature_rows, feature_ids, feature_nulls, feature_unresolved = connection.execute(
        f"""SELECT COUNT(*), COUNT(DISTINCT flight_id),
                   COUNT(*) FILTER (WHERE flight_id IS NULL),
                   COUNT(*) FILTER (WHERE identity_status IS DISTINCT FROM 'resolved')
            FROM {feature_view}"""
    ).fetchone()
    if feature_rows - feature_nulls != feature_ids:
        raise ValueError(f"{horizon}: duplicate non-null flight_id values in feature rows")
    if feature_nulls != feature_unresolved:
        raise ValueError(f"{horizon}: null ID and unresolved-status counts disagree")

    matched, missing_features, target_mismatches, provenance_mismatches = connection.execute(
        f"""SELECT COUNT(*) FILTER (WHERE f.flight_id IS NOT NULL),
                   COUNT(*) FILTER (WHERE f.flight_id IS NULL),
                   COUNT(*) FILTER (WHERE f.flight_id IS NOT NULL AND (
                        f.DepDel15 IS NULL OR f.DepDel15 IS DISTINCT FROM m.cohort_target
                   )),
                   COUNT(*) FILTER (WHERE f.flight_id IS NOT NULL AND (
                        f.identity_version IS NULL OR f.identity_version IS DISTINCT FROM 'bts-flight-v1'
                        OR f.source_archive_sha256 IS NULL OR f.source_archive_sha256 IS DISTINCT FROM m.source_archive_sha256
                        OR f.source_csv_member IS NULL OR f.source_csv_member IS DISTINCT FROM m.source_csv_member
                        OR f.source_row_ordinal IS NULL OR f.source_row_ordinal IS DISTINCT FROM m.source_row_ordinal
                   ))
            FROM {membership_table} m
            LEFT JOIN {feature_view} f ON f.flight_id = m.flight_id"""
    ).fetchone()
    if target_mismatches or provenance_mismatches:
        raise ValueError(
            f"{horizon}: cohort target/provenance mismatch "
            f"(target={target_mismatches}, provenance={provenance_mismatches})"
        )
    if missing_features:
        raise ValueError(f"{horizon}: {missing_features} cohort IDs have no required feature row")
    extra_features = connection.execute(
        f"""SELECT COUNT(*) FROM {feature_view} f
            LEFT JOIN {membership_table} m ON m.flight_id=f.flight_id
            WHERE f.flight_id IS NOT NULL AND m.flight_id IS NULL"""
    ).fetchone()[0]
    return {
        "membership_rows": int(membership_count),
        "membership_unique_ids": int(membership_ids),
        "feature_rows": int(feature_rows),
        "feature_unique_nonnull_ids": int(feature_ids),
        "feature_rows_with_unresolved_identity": int(feature_unresolved),
        "matched_feature_rows": int(matched),
        "membership_rows_without_features": int(missing_features),
        "feature_rows_outside_retrospective_cohort": int(extra_features),
        "target_mismatches": int(target_mismatches),
        "provenance_mismatches": int(provenance_mismatches),
    }


def _safe_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _score_maps_by_flight_id(
    valid: pd.DataFrame,
    probabilities: np.ndarray,
    support_reasons: list[str],
) -> tuple[dict[str, float], dict[str, str]]:
    ids = valid["flight_id"].astype(str).tolist()
    if len(ids) != len(probabilities) or len(ids) != len(support_reasons):
        raise ValueError("Score outputs do not align with the feature batch")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate flight_id values in scored batch")
    return dict(zip(ids, probabilities.tolist())), dict(zip(ids, support_reasons))


def _safe_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    result = float(value)
    return result if np.isfinite(result) else None


def _cohort_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Return identity metadata from the canonical membership side of a join.

    Feature files are model inputs, not an identity authority.  In particular,
    older Flight/Weather parquet files do not contain the reporting flight
    number.  Keeping this mapping separate prevents a left join from turning a
    valid canonical field into a blank export value.
    """
    return {
        "FlightDate": _safe_text(record.get("cohort_FlightDate")),
        "Reporting_Airline": record.get("cohort_Reporting_Airline"),
        "Flight_Number_Reporting_Airline": record.get("cohort_Flight_Number_Reporting_Airline"),
        "Origin": record.get("cohort_Origin"),
        "Dest": record.get("cohort_Dest"),
        "CRSDepTime": record.get("cohort_CRSDepTime"),
        "source_archive": record.get("cohort_source_archive"),
        "source_archive_sha256": record.get("cohort_source_archive_sha256"),
        "source_csv_member": record.get("cohort_source_csv_member"),
        "source_row_ordinal": _safe_int(record.get("cohort_source_row_ordinal")),
    }


def _batch_predictions(frame: pd.DataFrame, artifact: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    model_frame = frame.copy()
    model_frame["DepDel15"] = pd.to_numeric(model_frame["DepDel15"], errors="coerce")
    if model_frame["DepDel15"].isna().any():
        raise ValueError("Matched feature batch has missing DepDel15 labels")
    matrix, target = encode_frame(
        model_frame,
        artifact["encoder"],
        include_historical=True,
        include_weather=True,
    )
    raw_score = artifact["classifier"].decision_function(matrix)
    probability = artifact["calibrator"].predict_proba(raw_score.reshape(-1, 1))[:, 1]
    if len(probability) != len(frame) or not np.isfinite(probability).all():
        raise ValueError("Model returned non-finite or misaligned probabilities")
    if ((probability < 0) | (probability > 1)).any():
        raise ValueError("Model returned probabilities outside [0, 1]")

    categories = clean_categories(model_frame)
    unknown_masks = {}
    for index, column in enumerate(CATEGORICAL_FEATURES):
        known = {str(value) for value in artifact["encoder"].categories_[index]}
        unknown_masks[column] = (~categories[column].isin(known)).to_numpy()
    distance_missing = pd.to_numeric(model_frame["Distance"], errors="coerce").isna().to_numpy()
    unsupported = []
    for row_index in range(len(frame)):
        names = [column for column in CATEGORICAL_FEATURES if unknown_masks[column][row_index]]
        if distance_missing[row_index]:
            names.append("Distance")
        unsupported.append(names)
    return probability, target, [";".join(names) for names in unsupported]


def _write_horizon_export(
    connection: duckdb.DuckDBPyConnection,
    horizon: str,
    artifact: dict[str, Any],
    model_sha: str,
    schema_sha: str,
    output_path: Path,
    batch_size: int,
    input_summary: dict[str, int],
) -> dict[str, Any]:
    threshold = float(artifact["f1_threshold_selected_on_2024_first_half"])
    membership_table = f"membership_{horizon}"
    feature_view = f"features_{horizon}"
    # Keep authoritative membership metadata under non-conflicting aliases.  The
    # feature parquet is allowed to omit these identity fields, so relying on
    # ``f.*`` silently wrote blank flight numbers in the first H2 exports.
    query = f"""SELECT m.flight_id AS cohort_flight_id, m.horizon AS cohort_horizon,
                      m.eligibility_rule, m.cohort_target, m.retrospective_dep_delay,
                      m.FlightDate AS cohort_FlightDate,
                      m.Reporting_Airline AS cohort_Reporting_Airline,
                      m.Flight_Number_Reporting_Airline AS cohort_Flight_Number_Reporting_Airline,
                      m.Origin AS cohort_Origin, m.Dest AS cohort_Dest,
                      m.CRSDepTime AS cohort_CRSDepTime,
                      m.source_archive AS cohort_source_archive,
                      m.source_archive_sha256 AS cohort_source_archive_sha256,
                      m.source_csv_member AS cohort_source_csv_member,
                      m.source_row_ordinal AS cohort_source_row_ordinal,
                      f.*
               FROM {membership_table} m
               LEFT JOIN {feature_view} f ON f.flight_id=m.flight_id
               ORDER BY m.flight_id"""
    cursor = connection.execute(query)
    batch_reader = cursor.fetch_record_batch(rows_per_batch=batch_size)
    counters = {
        "rows_written": 0,
        "scored_rows": 0,
        "unavailable_rows": 0,
        "out_of_training_support_rows": 0,
        "distance_missing_rows": 0,
        "unknown_category_rows": 0,
        "origin_weather_missing_rows": 0,
        "destination_weather_missing_rows": 0,
    }
    raw_file = output_path.open("wb")
    try:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
                writer.writeheader()
                for arrow_batch in batch_reader:
                    frame = arrow_batch.to_pandas()
                    feature_present = frame["flight_id"].notna()
                    resolved = frame["identity_status"].fillna("").eq("resolved")
                    labels_match = pd.to_numeric(frame["DepDel15"], errors="coerce").eq(
                        pd.to_numeric(frame["cohort_target"], errors="coerce")
                    )
                    score_mask = feature_present & resolved & labels_match
                    valid = frame.loc[score_mask].copy()
                    probabilities = np.empty(len(valid), dtype=np.float64)
                    support_reasons: list[str] = []
                    targets = np.empty(len(valid), dtype=np.int8)
                    if len(valid):
                        probabilities, targets, support_reasons = _batch_predictions(valid, artifact)

                    valid_prob_by_id, support_by_id = _score_maps_by_flight_id(
                        valid, probabilities, support_reasons
                    )
                    for record in frame.to_dict(orient="records"):
                        feature_id = record.get("flight_id")
                        feature_id_text = "" if feature_id is None or pd.isna(feature_id) else str(feature_id)
                        if not feature_id_text:
                            status, reason, probability, predicted, support = (
                                "unavailable", "feature_row_missing_or_unresolved_id", None, None, "unavailable"
                            )
                            unsupported_text = ""
                            counters["unavailable_rows"] += 1
                        elif feature_id_text not in valid_prob_by_id:
                            status, reason, probability, predicted, support = (
                                "unavailable", "feature_identity_or_target_validation_failed", None, None, "unavailable"
                            )
                            unsupported_text = ""
                            counters["unavailable_rows"] += 1
                        else:
                            status, reason = "scored", ""
                            probability = valid_prob_by_id[feature_id_text]
                            predicted = int(probability >= threshold)
                            unsupported_text = support_by_id[feature_id_text]
                            support = "out_of_training_support" if unsupported_text else "category_distance_checks_passed"
                            counters["scored_rows"] += 1
                            if unsupported_text:
                                counters["out_of_training_support_rows"] += 1
                                if "Distance" in unsupported_text.split(";"):
                                    counters["distance_missing_rows"] += 1
                                if any(name in CATEGORICAL_FEATURES for name in unsupported_text.split(";")):
                                    counters["unknown_category_rows"] += 1
                        origin_missing = _safe_int(record.get("origin_weather_missing"))
                        destination_missing = _safe_int(record.get("destination_weather_missing"))
                        counters["origin_weather_missing_rows"] += int(origin_missing == 1)
                        counters["destination_weather_missing_rows"] += int(destination_missing == 1)
                        writer.writerow({
                            "flight_id": record.get("cohort_flight_id"),
                            "horizon": horizon,
                            "score_status": status,
                            "unavailable_reason": reason,
                            "predicted_delay_probability": probability if probability is not None else "",
                            "predicted_delay_15": predicted if predicted is not None else "",
                            "threshold": threshold,
                            "DepDel15": _safe_int(record.get("cohort_target")),
                            "eligibility_rule": record.get("eligibility_rule"),
                            "final_DEP_DELAY_used_for_retrospective_eligibility": _safe_float(record.get("retrospective_dep_delay")),
                            "identity_status": record.get("identity_status"),
                            "origin_weather_missing": origin_missing,
                            "destination_weather_missing": destination_missing,
                            "origin_weather_age_minutes": _safe_float(record.get("origin_weather_age_minutes")),
                            "destination_weather_age_minutes": _safe_float(record.get("destination_weather_age_minutes")),
                            "model_input_support_status": support,
                            "unsupported_model_inputs": unsupported_text,
                            "cutoff_availability_status": CUTOFF_AVAILABILITY_STATUS,
                            "model_name": f"weather_logistic_sgd_{horizon}",
                            "model_sha256": model_sha,
                            "feature_schema_sha256": schema_sha,
                            **_cohort_metadata(record),
                        })
                        counters["rows_written"] += 1
                        if counters["rows_written"] % 500_000 == 0:
                            print(f"{horizon}: exported {counters['rows_written']:,}/{input_summary['membership_rows']:,} rows", flush=True)
    finally:
        raw_file.close()
    if counters["rows_written"] != input_summary["membership_rows"]:
        raise RuntimeError(
            f"{horizon}: export rows {counters['rows_written']} do not match membership rows {input_summary['membership_rows']}"
        )
    return {
        **input_summary,
        **counters,
        "threshold": threshold,
        "model_file": str(artifact.get("_source_path", "")),
        "model_sha256": model_sha,
        "feature_schema_sha256": schema_sha,
        "output_file": output_path.name,
        "output_bytes": output_path.stat().st_size,
        "output_sha256": sha256_file(output_path),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size < 1 or args.threads < 1:
        print("ERROR: batch size and threads must be positive", file=sys.stderr)
        return 2
    feature_root = args.feature_root.expanduser().resolve()
    cohort_dir = args.cohort_dir.expanduser().resolve()
    model_root = args.model_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        print(f"ERROR: refusing to overwrite existing score export directory: {output_dir}", file=sys.stderr)
        return 2
    target_metadata = feature_root.parent / "targets" / "metadata.json"
    historical_metadata = feature_root.parent / "historical" / "metadata.json"
    weather_metadata = feature_root / "weather_feature_build.json"
    for path in (target_metadata, historical_metadata, weather_metadata):
        if not path.is_file():
            print(f"ERROR: required feature-build metadata is missing: {path}", file=sys.stderr)
            return 2
    target_report = json.loads(target_metadata.read_text(encoding="utf-8"))
    historical_report = json.loads(historical_metadata.read_text(encoding="utf-8"))
    weather_report = json.loads(weather_metadata.read_text(encoding="utf-8"))
    if target_report.get("test_2025_read") or historical_report.get("test_2025_read") or weather_report.get("test_2025_read_by_this_feature_build"):
        print("ERROR: a feature input reports that 2025 was read; refusing H2 export.", file=sys.stderr)
        return 2
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix=f".{output_dir.name}.building-", dir=output_dir.parent) as staging_name:
            staging = Path(staging_name)
            db = duckdb.connect()
            summaries = {}
            model_metadata = {}
            try:
                db.execute(f"SET threads={int(args.threads)}")
                temp_dir = staging / "duckdb_temp"
                temp_dir.mkdir()
                db.execute(f"SET temp_directory={sql_string(temp_dir.resolve())}")
                for horizon in HORIZONS:
                    files = h2_feature_files(feature_root, horizon)
                    membership_path = cohort_dir / f"{horizon}_membership.csv.gz"
                    model_path = model_root / horizon / "weather_logistic_sgd.joblib"
                    if not membership_path.is_file() or not model_path.is_file():
                        raise FileNotFoundError(f"{horizon}: membership or saved model file is missing")
                    artifact = joblib.load(model_path)
                    if artifact.get("target") != "DepDel15" or artifact.get("train_years") != [2022, 2023] or artifact.get("validation_year") != 2024:
                        raise ValueError(f"{horizon}: saved model metadata does not match the established target/split")
                    if not artifact.get("historical_features") or not artifact.get("weather_features"):
                        raise ValueError(f"{horizon}: saved artifact is not the expected history/weather model")
                    artifact["_source_path"] = str(model_path)
                    model_sha = sha256_file(model_path)
                    schema = feature_schema(artifact)
                    schema_sha = feature_schema_hash(artifact)
                    summary = _validate_horizon_inputs(db, horizon, files, membership_path)
                    output_path = staging / f"{horizon}_scores.csv.gz"
                    summaries[horizon] = _write_horizon_export(
                        db, horizon, artifact, model_sha, schema_sha, output_path,
                        args.batch_size, summary,
                    )
                    model_metadata[horizon] = {
                        "path": str(model_path),
                        "sha256": model_sha,
                        "feature_schema": schema,
                        "feature_schema_sha256": schema_sha,
                        "threshold": float(artifact["f1_threshold_selected_on_2024_first_half"]),
                        "training_years": artifact["train_years"],
                        "calibration_and_threshold_period": "2024-01 through 2024-06",
                        "evaluation_period": "2024-07 through 2024-12; exploratory because previously inspected",
                    }
                db.close()
            except Exception:
                db.close()
                raise
            feature_inputs = {}
            overall_status = "PASS" if all(export_status(summary) == "PASS" for summary in summaries.values()) else "FAIL"
            if overall_status != "PASS":
                raise RuntimeError("score export validation did not establish complete H2 cohort coverage")
            for horizon in HORIZONS:
                feature_files = h2_feature_files(feature_root, horizon)
                feature_inputs[horizon] = [
                    {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                    for path in feature_files
                ]
            membership_inputs = {
                horizon: {
                    "path": str(cohort_dir / f"{horizon}_membership.csv.gz"),
                    "bytes": (cohort_dir / f"{horizon}_membership.csv.gz").stat().st_size,
                    "sha256": sha256_file(cohort_dir / f"{horizon}_membership.csv.gz"),
                }
                for horizon in HORIZONS
            }
            with (target_metadata.parent / "monthly_audit.csv").open("r", encoding="utf-8-sig", newline="") as audit_stream:
                archive_audit = list(csv.DictReader(audit_stream))
            manifest = {
                "status": overall_status,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "runtime_versions": {
                    "python": platform.python_version(),
                    "numpy": np.__version__,
                    "pandas": pd.__version__,
                    "scikit_learn": sklearn.__version__,
                    "joblib": joblib.__version__,
                    "duckdb": duckdb.__version__,
                },
                "run_parameters": {
                    "batch_size": int(args.batch_size),
                    "duckdb_threads": int(args.threads),
                    "evaluation_months": ["2024-07", "2024-08", "2024-09", "2024-10", "2024-11", "2024-12"],
                },
                "identity_version": VERSION,
                "target": "BTS DepDel15; departure delay of at least 15 minutes among non-cancelled flights",
                "evaluation_period": "2024-07 through 2024-12; exploratory, not a blind final test",
                "calibration_and_threshold_selection_period": "2024-01 through 2024-06",
                "training_period": "2022-2023",
                "models": model_metadata,
                "outputs": summaries,
                "feature_input_files": feature_inputs,
                "membership_input_files": membership_inputs,
                "target_input_metadata": {"path": str(target_metadata), "sha256": sha256_file(target_metadata)},
                "target_monthly_audit": {"path": str(target_metadata.parent / "monthly_audit.csv"), "sha256": sha256_file(target_metadata.parent / "monthly_audit.csv"), "source_archives": archive_audit},
                "historical_build_metadata": {"path": str(historical_metadata), "sha256": sha256_file(historical_metadata)},
                "weather_build_metadata": {"path": str(weather_metadata), "sha256": sha256_file(weather_metadata)},
                "eligibility": "Cohort membership uses final BTS cancellation and DEP_DELAY only for retrospective eligibility; neither is a predictor. DEP_DELAY is included in exports only as eligibility audit provenance.",
                "prediction_time_limitations": [
                    "BTS historical-feature and schedule-vintage availability timestamps are not verified.",
                    "NOAA FM15 weather uses observation time plus the existing 15-minute availability proxy; actual receipt timestamps are unavailable.",
                    "These exports are retrospective research scores and do not establish live operational readiness or calibration for current flights.",
                ],
                "scoring_policy": "Saved classifier and calibrator only; no fitting, recalibration, threshold selection, or model changes were performed.",
            }
            manifest_path = staging / "run_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            if output_dir.exists():
                raise FileExistsError(f"Output appeared during export; refusing to overwrite: {output_dir}")
            os.replace(staging, output_dir)
        print(f"Flight/Weather score exports: {output_dir}")
        for horizon in HORIZONS:
            row = summaries[horizon]
            print(
                f"{horizon}: cohort={row['membership_rows']:,}, scored={row['scored_rows']:,}, "
                f"unavailable={row['unavailable_rows']:,}, out_of_support={row['out_of_training_support_rows']:,}"
            )
        print(f"Run manifest: {output_dir / 'run_manifest.json'}")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
