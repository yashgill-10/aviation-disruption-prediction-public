from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

import pandas as pd

from bts_archives import expected_archive, inventory_month
from bts_preprocessing import OUTPUT_COLUMNS, PROVENANCE_COLUMNS, process_month
from canonical_flight_identity import ID_COLS, VERSION

EVALUATION_MONTHS = tuple(f"2024-{month:02d}" for month in range(7, 13))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/flight_weather_score_prep_2024_h2_v1/targets"),
    )
    parser.add_argument("--months", nargs="+", choices=EVALUATION_MONTHS, default=list(EVALUATION_MONTHS))
    parser.add_argument("--chunksize", type=int, default=100_000)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.chunksize < 1:
        print("ERROR: --chunksize must be positive.", file=sys.stderr)
        return 2
    if len(set(args.months)) != len(args.months):
        print("ERROR: --months contains duplicates.", file=sys.stderr)
        return 2

    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        print(f"ERROR: refusing to overwrite existing output: {output_dir}", file=sys.stderr)
        return 2
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{output_dir.name}.building-", dir=output_dir.parent
        ) as staging_name:
            staging = Path(staging_name)
            archive_records = []
            for label in sorted(args.months):
                year_text, month_text = label.split("-")
                year, month = int(year_text), int(month_text)
                archive_path = expected_archive(input_dir, year, month)
                inventory = inventory_month(input_dir, year, month, verify_crc=False)
                if inventory["state"] != "valid":
                    raise FileNotFoundError(
                        f"{label}: source archive is {inventory['state']}: {inventory['detail']}"
                    )
                month_output = staging / "flights" / "validation" / f"{label}.parquet"
                result = process_month(
                    archive_path, year, month, month_output, chunksize=args.chunksize
                )
                if result["status"] not in ("processed", "processed_no_eligible_rows"):
                    raise RuntimeError(f"{label}: preprocessing did not complete successfully")
                if not result["crc_verified"]:
                    raise RuntimeError(
                        f"{label}: CSV member CRC was not verified during streaming"
                    )
                result["archive_state"] = inventory["state"]
                result["source_archive"] = archive_path.name
                result["source_csv_member"] = inventory["csv_member"]
                result["parquet_path"] = f"flights/validation/{label}.parquet"
                archive_records.append(result)

            pd.DataFrame(archive_records).to_csv(staging / "monthly_audit.csv", index=False)
            metadata = {
                "dataset": "Flight/Weather retrospective evaluation targets with canonical identity provenance",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_data_dir": str(input_dir),
                "target": "DepDel15",
                "target_definition": "BTS indicator that departure delay was at least 15 minutes; eligible values are binary and flights are non-cancelled.",
                "evaluation_period": ["2024-07-01", "2024-12-31"],
                "months": sorted(args.months),
                "split": "validation/evaluation; not used for fitting or calibration",
                "test_2025_read": False,
                "identity": {
                    "version": VERSION,
                    "fields": ID_COLS,
                    "provenance_columns": PROVENANCE_COLUMNS,
                    "unresolved_policy": "Rows remain in the prepared source with null flight_id and an explicit identity status.",
                    "source_row_ordinal": "One-based data-row ordinal in the original BTS CSV member, assigned before filtering.",
                },
                "model_predictor_policy": "Identity and source provenance columns are metadata; the existing saved model encoder uses its fixed feature schema.",
                "row_counts_by_month": {
                    f"{row['year']}-{int(row['month']):02d}": int(row["eligible_rows"])
                    for row in archive_records
                },
                "feature_columns": OUTPUT_COLUMNS,
                "monthly_audit": "monthly_audit.csv",
            }
            (staging / "metadata.json").write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )
            if output_dir.exists():
                raise FileExistsError(
                    f"Output appeared during build; refusing to overwrite: {output_dir}"
                )
            os.replace(staging, output_dir)

        print(f"Prepared target rows: {output_dir}")
        for row in archive_records:
            print(
                f"{row['year']}-{int(row['month']):02d}: {int(row['eligible_rows']):,} rows; "
                f"identity unresolved={int(row['identity_unresolved_rows']):,}"
            )
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
