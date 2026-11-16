#!/usr/bin/env python3
"""Validate one BTS monthly on-time performance CSV without modifying it."""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = [
    "Year", "Month", "FlightDate", "Reporting_Airline",
    "Flight_Number_Reporting_Airline", "OriginAirportID", "Origin",
    "DestAirportID", "Dest", "CRSDepTime", "DepDel15", "Cancelled",
    "Diverted", "Tail_Number", "Distance",
]
CORE_COLUMNS = [
    "FlightDate", "Reporting_Airline", "Tail_Number", "Origin", "Dest",
    "CRSDepTime", "DepDel15", "Cancelled", "Diverted", "Distance",
]


def value_key(value: object) -> str:
    if pd.isna(value):
        return "MISSING"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a BTS monthly flight CSV in chunks; no files are modified."
    )
    parser.add_argument("csv_path", type=Path, help="Path to the extracted BTS CSV")
    parser.add_argument("--chunksize", type=int, default=100_000,
                        help="Rows per chunk (default: 100000)")
    args = parser.parse_args()

    path = args.csv_path.expanduser().resolve()
    if not path.is_file():
        print(f"ERROR: CSV file does not exist: {path}", file=sys.stderr)
        return 2
    if args.chunksize < 1:
        print("ERROR: --chunksize must be positive", file=sys.stderr)
        return 2

    size_bytes = path.stat().st_size
    print("=== BTS monthly CSV validation ===")
    print(f"File: {path}")
    print(f"Exact size: {size_bytes:,} bytes ({size_bytes / (1024 ** 2):.2f} MiB)")

    try:
        # Read header separately; this is small and reveals trailing empty columns.
        header = pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns.tolist()
    except Exception as exc:  # Report file/encoding/CSV syntax issues usefully.
        print(f"ERROR: Unable to read CSV header: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    print(f"Column count: {len(header)}")
    unnamed = [name for name in header if str(name).startswith("Unnamed:")]
    print(f"Unnamed columns: {unnamed if unnamed else 'none'}")
    missing_required = [name for name in REQUIRED_COLUMNS if name not in header]
    print(f"Missing required columns: {missing_required if missing_required else 'none'}")

    counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    cancellation_counts: Counter[str] = Counter()
    diversion_counts: Counter[str] = Counter()
    dates_min = None
    dates_max = None
    blank_unnamed_counts: Counter[str] = Counter()

    try:
        reader = pd.read_csv(
            path,
            chunksize=args.chunksize,
            encoding="utf-8-sig",
            low_memory=False,
        )
        for chunk_number, chunk in enumerate(reader, start=1):
            counts["rows"] += len(chunk)
            for col in CORE_COLUMNS:
                if col in chunk.columns:
                    missing_counts[col] += int(chunk[col].isna().sum())

            if "FlightDate" in chunk.columns:
                dates = pd.to_datetime(chunk["FlightDate"], errors="coerce")
                current_min, current_max = dates.min(), dates.max()
                if pd.notna(current_min) and (dates_min is None or current_min < dates_min):
                    dates_min = current_min
                if pd.notna(current_max) and (dates_max is None or current_max > dates_max):
                    dates_max = current_max
                counts["invalid_FlightDate"] += int(dates.isna().sum())

            if "DepDel15" in chunk.columns:
                target = pd.to_numeric(chunk["DepDel15"], errors="coerce")
                for value, number in target.value_counts(dropna=False).items():
                    target_counts[value_key(value)] += int(number)
                counts["invalid_DepDel15_nonmissing"] += int(
                    (chunk["DepDel15"].notna() & target.isna()).sum()
                )
                counts["target_not_0_or_1"] += int(
                    (target.notna() & ~target.isin([0, 1])).sum()
                )

            if "Cancelled" in chunk.columns:
                for value, number in chunk["Cancelled"].value_counts(dropna=False).items():
                    cancellation_counts[value_key(value)] += int(number)
            if "Diverted" in chunk.columns:
                for value, number in chunk["Diverted"].value_counts(dropna=False).items():
                    diversion_counts[value_key(value)] += int(number)

            for col in unnamed:
                if col in chunk.columns:
                    blank_unnamed_counts[col] += int(chunk[col].notna().sum())

            if chunk_number == 1:
                sample_cols = [c for c in [
                    "FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline",
                    "Origin", "Dest", "CRSDepTime", "DepDel15", "Cancelled", "Diverted"
                ] if c in chunk.columns]
                print("\nFirst 5 rows (selected columns):")
                print(chunk[sample_cols].head(5).to_string(index=False))

    except Exception as exc:
        print(f"ERROR: CSV processing failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    print("\n=== Results ===")
    print(f"Total data rows: {counts['rows']:,}")
    print(f"FlightDate range: {dates_min.date() if dates_min is not None else 'unavailable'} to "
          f"{dates_max.date() if dates_max is not None else 'unavailable'}")
    print(f"Invalid/missing FlightDate values: {counts['invalid_FlightDate']:,}")
    print(f"DepDel15 counts: {dict(target_counts)}")
    print(f"Cancelled counts: {dict(cancellation_counts)}")
    print(f"Diverted counts: {dict(diversion_counts)}")
    print(f"DepDel15 values outside 0/1: {counts['target_not_0_or_1']:,}")
    print(f"DepDel15 non-empty values that could not be parsed: {counts['invalid_DepDel15_nonmissing']:,}")
    print("Missing core-field values:")
    for col in CORE_COLUMNS:
        if col in header:
            number = missing_counts[col]
            pct = (100 * number / counts["rows"]) if counts["rows"] else 0.0
            print(f"  {col}: {number:,} ({pct:.2f}%)")
    if unnamed:
        print("Non-empty cells in unnamed columns:")
        for col in unnamed:
            print(f"  {col}: {blank_unnamed_counts[col]:,}")

    if missing_required:
        print("\nSTATUS: WARNING — required columns are missing; inspect schema before modelling.")
        return 5
    if counts["rows"] == 0:
        print("\nSTATUS: ERROR — no data rows found.")
        return 6
    if size_bytes < 10_000_000:
        print("\nSTATUS: WARNING — file is under 10 MB; verify the extraction is complete. "
              "The source ZIP previously reported a much larger CSV member.")
        return 7

    print("\nSTATUS: Initial schema and file-size checks passed. Review the missingness and label counts before modelling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
