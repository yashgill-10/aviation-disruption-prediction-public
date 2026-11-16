#!/usr/bin/env python3
"""Create a compact, leakage-safe BTS DepDel15 Parquet dataset."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import uuid

import pandas as pd

from bts_archives import inventory_month
from bts_preprocessing import OUTPUT_COLUMNS, PROVENANCE_COLUMNS, process_month
from canonical_flight_identity import ID_COLS, VERSION


YEARS = range(2022, 2026)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"),
                        help="directory containing BTS monthly ZIPs (default: data/raw)")
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/depdel15"),
                        help="new or empty output directory (default: data/processed/depdel15)")
    parser.add_argument("--chunksize", type=int, default=100_000,
                        help="CSV rows per processing chunk (default: 100000)")
    return parser.parse_args()


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _base_audit(record: dict[str, object], status: str, error: str = "") -> dict[str, object]:
    return {
        "year": record["year"],
        "month": record["month"],
        "split": "train" if record["year"] in (2022, 2023) else "validation" if record["year"] == 2024 else "test",
        "archive_path": record["path"],
        "archive_state": record["state"],
        "archive_detail": record["detail"],
        "status": status,
        "source_rows": 0,
        "cancelled_rows_excluded": 0,
        "unknown_cancellation_rows": 0,
        "missing_target_rows": 0,
        "invalid_target_rows": 0,
        "nonbinary_target_rows": 0,
        "invalid_date_rows": 0,
        "out_of_month_date_rows": 0,
        "eligible_rows": 0,
        "target_0": 0,
        "target_1": 0,
        "observed_date_count": 0,
        "observed_date_min": None,
        "observed_date_max": None,
        "missing_month_dates": "",
        "source_date_min": None,
        "source_date_max": None,
        "eligible_date_min": None,
        "eligible_date_max": None,
        "crc_verified": False,
        "parquet_path": None,
        "error": error,
    }


def _process_to_temp(record: dict[str, object], final_path: Path, chunksize: int,
                     output_root: Path) -> dict[str, object]:
    temp_path = final_path.with_name(f".{final_path.stem}.{uuid.uuid4().hex}.partial.parquet")
    try:
        result = process_month(Path(str(record["path"])), int(record["year"]),
                               int(record["month"]), temp_path, chunksize=chunksize)
        result["archive_state"] = record["state"]
        result["archive_detail"] = record["detail"]
        if result["eligible_rows"]:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.replace(final_path)
            result["parquet_path"] = str(final_path.relative_to(output_root))
        else:
            temp_path.unlink(missing_ok=True)
        return result
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        result = _base_audit(record, "error", f"{type(exc).__name__}: {exc}")
        result["archive_state"] = record["state"]
        result["archive_detail"] = record["detail"]
        return result


def main() -> int:
    args = parse_args()
    if args.chunksize < 1:
        print("ERROR: --chunksize must be positive", file=sys.stderr)
        return 2

    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if _within(output_dir, input_dir):
        print("ERROR: output directory must not be inside the raw input directory.", file=sys.stderr)
        return 2
    if output_dir.exists():
        if not output_dir.is_dir():
            print(f"ERROR: output path is not a directory: {output_dir}", file=sys.stderr)
            return 2
        if any(output_dir.iterdir()):
            print(f"ERROR: output directory is not empty; choose a new path: {output_dir}", file=sys.stderr)
            return 2

    records = [inventory_month(input_dir, year, month, verify_crc=False)
               for year in YEARS for month in range(1, 13)]
    by_key = {(int(record["year"]), int(record["month"])): record for record in records}
    test_records = [by_key[(2025, month)] for month in range(1, 13)]
    test_archives_ready = all(record["state"] == "valid" for record in test_records)

    output_dir.mkdir(parents=True, exist_ok=True)
    audit_rows: list[dict[str, object]] = []
    processing_errors = False

    for year in (2022, 2023, 2024):
        split = "train" if year in (2022, 2023) else "validation"
        for month in range(1, 13):
            record = by_key[(year, month)]
            if record["state"] != "valid":
                audit_rows.append(_base_audit(record, str(record["state"])))
                continue
            destination = output_dir / "flights" / split / f"{year}-{month:02d}.parquet"
            result = _process_to_temp(record, destination, args.chunksize, output_dir)
            audit_rows.append(result)
            if result["status"] == "error":
                processing_errors = True

    test_included = False
    if not test_archives_ready:
        missing = [f"{int(record['year'])}-{int(record['month']):02d}"
                   for record in test_records if record["state"] != "valid"]
        note = "2025 test withheld because the full 12-month archive set is not structurally complete; " + ", ".join(missing)
        for record in test_records:
            row = _base_audit(record, "test_suppressed_incomplete_year", note)
            row["archive_state"] = record["state"]
            row["archive_detail"] = record["detail"]
            audit_rows.append(row)
    else:
        staging_test = output_dir / ".staging_test_2025"
        staging_test.mkdir(parents=True, exist_ok=False)
        test_results = []
        for record in test_records:
            destination = staging_test / f"{int(record['year'])}-{int(record['month']):02d}.parquet"
            result = _process_to_temp(record, destination, args.chunksize, output_dir)
            test_results.append(result)

        for result in test_results:
            if (result["status"] in ("processed", "processed_no_eligible_rows")
                    and int(result["observed_date_count"]) == 0):
                result["status"] = "error"
                result["error"] = "no valid FlightDate values fall inside this archive month"
        test_ok = all(
            result["status"] in ("processed", "processed_no_eligible_rows")
            and result["crc_verified"]
            and int(result["observed_date_count"]) > 0
            for result in test_results
        )
        if test_ok:
            final_test_dir = output_dir / "flights" / "test"
            final_test_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_test.replace(final_test_dir)
            for result in test_results:
                if result["parquet_path"]:
                    result["parquet_path"] = str((final_test_dir / Path(str(result["parquet_path"])).name).relative_to(output_dir))
            test_included = True
        else:
            shutil.rmtree(staging_test)
            processing_errors = True
            for result in test_results:
                if result["status"] != "error":
                    result["status"] = "test_suppressed_due_to_month_error"
                    result["parquet_path"] = None
                    result["error"] = "another 2025 month failed; no partial test year was retained"
        audit_rows.extend(test_results)

    audit = pd.DataFrame(audit_rows).sort_values(["year", "month"], kind="stable")
    audit_path = output_dir / "monthly_audit.csv"
    audit.to_csv(audit_path, index=False)

    year_coverage = {}
    for year in YEARS:
        year_rows = audit.loc[audit["year"].eq(year)]
        year_coverage[str(year)] = {
            "months_expected": 12,
            "structurally_valid_archives": int(year_rows["archive_state"].eq("valid").sum()),
            "months_processed": int(year_rows["status"].isin(["processed", "processed_no_eligible_rows"]).sum()),
            "eligible_rows": int(year_rows["eligible_rows"].sum()),
            "crc_verified_months": int(year_rows["crc_verified"].fillna(False).astype(bool).sum()),
        }

    metadata = {
        "dataset": "BTS DepDel15 departure disruption classification",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(input_dir),
        "target": "DepDel15",
        "target_definition": "BTS indicator that departure delay was at least 15 minutes; retained values are 0 or 1",
        "eligibility": "Cancelled must equal 0, DepDel15 must be 0 or 1, and FlightDate must fall in the archive month",
        "split_definition": {"train": [2022, 2023], "validation": [2024], "test": [2025]},
        "test_2025_included": test_included,
        "test_year_rule": "The 2025 test split is emitted only when all 12 monthly ZIPs are structurally valid, each contains at least one valid in-month FlightDate, and all 12 streams finish with valid CRCs.",
        "feature_columns": OUTPUT_COLUMNS,
        "canonical_identity": {
            "version": VERSION,
            "fields": ID_COLS,
            "definition": "SHA-256 over compact UTF-8 JSON of normalized BTS identity fields, matching the shared bts-flight-v1 implementation.",
            "provenance_columns": PROVENANCE_COLUMNS,
            "unresolved_policy": "Retain target-eligible rows with null flight_id and an explicit identity_status; do not guess missing identity fields.",
            "model_input_policy": "Identity and source provenance columns are metadata; train_baseline uses explicit predictor lists and excludes them.",
        },
        "post_outcome_fields_not_read": [
            "DepTime", "ArrTime", "DepDelay", "ArrDelay", "DepDelayMinutes", "ArrDelayMinutes",
            "TaxiOut", "WheelsOff", "WheelsOn", "Diverted", "CancellationCode",
            "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay",
        ],
        "not_a_model_evaluation": True,
        "year_coverage": year_coverage,
        "monthly_audit": "monthly_audit.csv",
        "parquet_root": "flights/",
        "notes": [
            "Raw ZIP archives were read in chunks; CSVs were not extracted.",
            "Cancelled flights and rows with missing/invalid targets or dates were excluded from Parquet.",
            "Missing archives and partial .part downloads were not modified.",
            "Weather and network features are not included in this first dataset.",
        ],
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    print("BTS DepDel15 preprocessing finished")
    print(f"Output: {output_dir}")
    for year, coverage in year_coverage.items():
        print(f"{year}: {coverage['structurally_valid_archives']}/12 ZIPs structurally valid, "
              f"{coverage['months_processed']}/12 processed, {coverage['eligible_rows']:,} eligible rows")
    print(f"2025 untouched test split included: {'yes' if test_included else 'no'}")
    print(f"Monthly audit: {audit_path}")
    if processing_errors:
        print("STATUS: one or more archives failed during processing; inspect monthly_audit.csv.", file=sys.stderr)
        return 1
    print("STATUS: available months processed; coverage and target eligibility are recorded in the audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
