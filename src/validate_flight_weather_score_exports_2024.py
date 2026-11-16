from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import duckdb


HORIZONS = ("t_minus_2h", "t_minus_30m")


def sql_path(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def hash_and_size(path: Path, decompressed: bool = False) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    open_file = gzip.open if decompressed else open
    with open_file(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"sha256": digest.hexdigest(), "bytes": size}


def validate_horizon(
    connection: duckdb.DuckDBPyConnection,
    horizon: str,
    score_path: Path,
    membership_path: Path,
    expected: dict[str, Any],
) -> dict[str, Any]:
    score_view = f"scores_{horizon}"
    member_view = f"members_{horizon}"
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW {score_view} AS SELECT * FROM read_csv_auto({sql_path(score_path)}, header=true)"
    )
    connection.execute(
        f"""CREATE OR REPLACE TEMP VIEW {member_view} AS
            SELECT * FROM read_csv_auto({sql_path(membership_path)}, header=true)
            WHERE CAST(FlightDate AS DATE) >= DATE '2024-07-01'
              AND CAST(FlightDate AS DATE) < DATE '2025-01-01'"""
    )
    row = connection.execute(
        f"""SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT flight_id) AS unique_ids,
                   COUNT(*) FILTER (WHERE flight_id IS NULL OR trim(CAST(flight_id AS VARCHAR))='') AS null_ids,
                   COUNT(*) FILTER (WHERE score_status='scored') AS scored_rows,
                   COUNT(*) FILTER (WHERE score_status IS DISTINCT FROM 'scored') AS unavailable_rows,
                   COUNT(*) FILTER (WHERE horizon IS DISTINCT FROM {sql_string(horizon)}) AS wrong_horizon,
                   COUNT(*) FILTER (WHERE predicted_delay_probability IS NULL
                       OR NOT isfinite(TRY_CAST(predicted_delay_probability AS DOUBLE))
                       OR TRY_CAST(predicted_delay_probability AS DOUBLE)<0
                       OR TRY_CAST(predicted_delay_probability AS DOUBLE)>1) AS invalid_scores,
                   COUNT(*) FILTER (WHERE DepDel15 IS NULL OR DepDel15 NOT IN (0,1)) AS invalid_targets,
                   COUNT(*) FILTER (WHERE predicted_delay_15 IS NULL
                       OR predicted_delay_15 IS DISTINCT FROM CASE WHEN predicted_delay_probability>=threshold THEN 1 ELSE 0 END) AS threshold_label_mismatches,
                   COUNT(DISTINCT threshold) AS distinct_thresholds,
                   MIN(threshold) AS threshold,
                   SUM(DepDel15) AS positive_targets,
                   SUM(predicted_delay_15) AS positive_predictions,
                   COUNT(*) FILTER (WHERE model_input_support_status='out_of_training_support') AS out_of_support_rows,
                   COUNT(*) FILTER (WHERE contains(string_split(coalesce(unsupported_model_inputs,''), ';'),'Distance')) AS missing_distance_rows,
                   COUNT(*) FILTER (WHERE origin_weather_missing=1) AS origin_weather_missing_rows,
                   COUNT(*) FILTER (WHERE destination_weather_missing=1) AS destination_weather_missing_rows,
                   COUNT(*) FILTER (WHERE cutoff_availability_status IS DISTINCT FROM 'retrospective_source_availability_unverified') AS wrong_availability_status,
                   COUNT(*) FILTER (WHERE final_DEP_DELAY_used_for_retrospective_eligibility IS NULL) AS missing_retrospective_eligibility_value
            FROM {score_view}"""
    ).fetchone()
    columns = [
        "rows", "unique_ids", "null_ids", "scored_rows", "unavailable_rows", "wrong_horizon",
        "invalid_scores", "invalid_targets", "threshold_label_mismatches", "distinct_thresholds",
        "threshold", "positive_targets", "positive_predictions", "out_of_support_rows",
        "missing_distance_rows", "origin_weather_missing_rows", "destination_weather_missing_rows",
        "wrong_availability_status", "missing_retrospective_eligibility_value",
    ]
    stats = dict(zip(columns, row))
    stats = {key: (value.item() if hasattr(value, "item") else value) for key, value in stats.items()}

    membership_rows, membership_unique, membership_nulls, membership_bad_labels, membership_bad_cancelled = _membership_integrity_counts(
        connection, member_view
    )
    extra, missing, target_mismatches, horizon_mismatches = connection.execute(
        f"""SELECT
              (SELECT COUNT(*) FROM {score_view} s LEFT JOIN {member_view} m USING(flight_id) WHERE m.flight_id IS NULL),
              (SELECT COUNT(*) FROM {member_view} m LEFT JOIN {score_view} s USING(flight_id) WHERE s.flight_id IS NULL),
              (SELECT COUNT(*) FROM {score_view} s JOIN {member_view} m USING(flight_id)
               WHERE s.DepDel15 IS NULL OR s.DepDel15 IS DISTINCT FROM m.DepDel15),
              (SELECT COUNT(*) FROM {score_view} s JOIN {member_view} m USING(flight_id)
               WHERE s.horizon IS NULL OR s.horizon IS DISTINCT FROM m.horizon)"""
    ).fetchone()
    identity_provenance_mismatches = connection.execute(
        f"""SELECT COUNT(*)
            FROM {score_view} s JOIN {member_view} m USING(flight_id)
            WHERE s.identity_status IS NULL OR s.identity_status IS DISTINCT FROM 'resolved'
               OR s.source_archive IS NULL OR s.source_archive IS DISTINCT FROM m.source_archive
               OR s.source_archive_sha256 IS NULL OR s.source_archive_sha256 IS DISTINCT FROM m.source_archive_sha256
               OR s.source_csv_member IS NULL OR s.source_csv_member IS DISTINCT FROM m.source_csv_member
               OR s.source_row_ordinal IS NULL OR s.source_row_ordinal IS DISTINCT FROM m.source_data_row_ordinal"""
    ).fetchone()[0]
    stats.update({
        "membership_rows": int(membership_rows),
        "membership_unique_ids": int(membership_unique),
        "membership_null_ids": int(membership_nulls),
        "membership_invalid_targets": int(membership_bad_labels),
        "membership_cancelled_rows": int(membership_bad_cancelled),
        "extra_score_ids": int(extra),
        "missing_score_ids": int(missing),
        "target_mismatches": int(target_mismatches),
        "membership_horizon_mismatches": int(horizon_mismatches),
        "identity_provenance_mismatches": int(identity_provenance_mismatches),
    })

    categories = connection.execute(
        f"""SELECT field, COUNT(*) AS rows
            FROM {score_view}, UNNEST(string_split(coalesce(unsupported_model_inputs,''), ';')) AS names(field)
            WHERE trim(field)<>''
            GROUP BY field ORDER BY field"""
    ).fetchall()
    stats["unsupported_input_counts"] = {field: int(count) for field, count in categories}

    compressed = hash_and_size(score_path)
    decompressed = hash_and_size(score_path, decompressed=True)
    stats["output_file"] = score_path.name
    stats["compressed"] = compressed
    stats["decompressed"] = decompressed
    stats["expected_export_summary"] = expected

    failures = []
    if stats["rows"] != membership_rows or stats["unique_ids"] != membership_unique:
        failures.append("score row/unique-ID count differs from membership")
    if stats["null_ids"] or stats["membership_null_ids"] or stats["membership_invalid_targets"]:
        failures.append("null IDs or invalid membership values")
    if stats["membership_cancelled_rows"]:
        failures.append("membership includes cancelled or unknown cancellation-status flights")
    if stats["scored_rows"] != membership_rows or stats["unavailable_rows"]:
        failures.append("score coverage is incomplete")
    if stats["wrong_horizon"] or stats["invalid_scores"] or stats["invalid_targets"]:
        failures.append("invalid horizon, score, or target")
    if stats["threshold_label_mismatches"] or stats["distinct_thresholds"] != 1:
        failures.append("threshold labels or saved threshold are inconsistent")
    if stats["extra_score_ids"] or stats["missing_score_ids"] or stats["target_mismatches"] or stats["membership_horizon_mismatches"]:
        failures.append("score IDs/targets do not exactly match membership")
    if stats["identity_provenance_mismatches"]:
        failures.append("score identity/source provenance does not exactly match membership")
    if stats["wrong_availability_status"] or stats["missing_retrospective_eligibility_value"]:
        failures.append("retrospective availability/eligibility audit fields are incomplete")
    if compressed["sha256"] != expected.get("output_sha256") or compressed["bytes"] != expected.get("output_bytes"):
        failures.append("compressed output checksum/size differs from run manifest")
    stats["validation"] = "FAIL" if failures else "PASS"
    stats["failures"] = failures
    return stats



def _membership_integrity_counts(connection, member_view):
    """Count membership IDs, targets, and invalid cancellation statuses."""
    return connection.execute(
        f"""SELECT COUNT(*), COUNT(DISTINCT flight_id),
                   COUNT(*) FILTER (WHERE flight_id IS NULL OR trim(CAST(flight_id AS VARCHAR))=''),
                   COUNT(*) FILTER (WHERE DepDel15 IS NULL OR DepDel15 NOT IN (0,1)),
                   COUNT(*) FILTER (
                       WHERE TRY_CAST(Cancelled AS INTEGER) IS DISTINCT FROM 0
                   )
            FROM {member_view}"""
    ).fetchone()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/flight_weather_score_exports_2024_h2_v1"))
    parser.add_argument("--cohort-dir", type=Path, default=Path("data/processed/shared_cohort_2024_20261016_v1"))
    args = parser.parse_args(argv)
    output_dir = args.output_dir.expanduser().resolve()
    cohort_dir = args.cohort_dir.expanduser().resolve()
    manifest_path = output_dir / "run_manifest.json"
    if not manifest_path.is_file():
        print(f"ERROR: run manifest is missing: {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    db = duckdb.connect()
    try:
        reports = {}
        for horizon in HORIZONS:
            score_path = output_dir / f"{horizon}_scores.csv.gz"
            membership_path = cohort_dir / f"{horizon}_membership.csv.gz"
            if not score_path.is_file() or not membership_path.is_file():
                raise FileNotFoundError(f"Missing score or membership file for {horizon}")
            reports[horizon] = validate_horizon(
                db, horizon, score_path, membership_path, manifest["outputs"][horizon]
            )
    finally:
        db.close()
    result = {"status": "PASS" if all(item["validation"] == "PASS" for item in reports.values()) else "FAIL", "horizons": reports}
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
