#!/usr/bin/env python3
"""Audit BTS departure-delay labels against cancellation/diversion status.

Reads only the four columns needed for this audit and never modifies the source CSV.
Usage:
  python audit_bts_target.py path/to/month.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="Path to one BTS monthly CSV")
    args = parser.parse_args()

    if not args.csv_path.is_file():
        print(f"ERROR: File not found: {args.csv_path}", file=sys.stderr)
        return 2

    required = ["FlightDate", "DepDel15", "Cancelled", "Diverted"]
    try:
        df = pd.read_csv(
            args.csv_path,
            usecols=required,
            dtype={"DepDel15": "string", "Cancelled": "string", "Diverted": "string"},
            low_memory=False,
        )
    except ValueError as exc:
        print(f"ERROR: Required column missing or unreadable: {exc}", file=sys.stderr)
        return 2

    target_raw = df["DepDel15"].str.strip()
    target = pd.to_numeric(target_raw, errors="coerce")
    cancelled_raw = df["Cancelled"].str.strip()
    diverted_raw = df["Diverted"].str.strip()
    cancelled = pd.to_numeric(cancelled_raw, errors="coerce")
    diverted = pd.to_numeric(diverted_raw, errors="coerce")

    invalid_target = target_raw.notna() & target_raw.ne("") & target.isna()
    outside_binary = target.notna() & ~target.isin([0, 1])
    target_label = target.map({0: "0 (on time / under 15 min)", 1: "1 (15+ min delay)"}).fillna("MISSING")

    print("=== BTS departure-label audit ===")
    print(f"File: {args.csv_path.resolve()}")
    print(f"Rows: {len(df):,}")
    date_parsed = pd.to_datetime(df["FlightDate"], errors="coerce")
    if date_parsed.notna().any():
        print(f"Date range: {date_parsed.min().date()} to {date_parsed.max().date()}")
    print(f"Missing DepDel15: {target.isna().sum():,} ({target.isna().mean():.2%})")
    print(f"Non-empty DepDel15 values that failed numeric parsing: {invalid_target.sum():,}")
    print(f"DepDel15 values outside 0/1: {outside_binary.sum():,}")

    print("\nDepDel15 by Cancelled status (blank labels shown as MISSING):")
    cancel_label = cancelled.map({0: "0 (not cancelled)", 1: "1 (cancelled)"}).fillna("MISSING/OTHER")
    print(pd.crosstab(cancel_label, target_label, margins=True, dropna=False).to_string())

    print("\nDepDel15 by Diverted status:")
    divert_label = diverted.map({0: "0 (not diverted)", 1: "1 (diverted)"}).fillna("MISSING/OTHER")
    print(pd.crosstab(divert_label, target_label, margins=True, dropna=False).to_string())

    cancelled_rows = cancelled.eq(1)
    not_cancelled_rows = cancelled.eq(0)
    eligible = not_cancelled_rows & target.isin([0, 1])
    print("\nEligibility summary for a departure-delay classifier:")
    print(f"Rows marked cancelled: {cancelled_rows.sum():,}")
    print(f"Cancelled rows with a non-missing DepDel15 label: {(cancelled_rows & target.notna()).sum():,}")
    print(f"Non-cancelled rows: {not_cancelled_rows.sum():,}")
    print(f"Non-cancelled rows with a valid 0/1 target: {eligible.sum():,}")
    if eligible.any():
        eligible_counts = target.loc[eligible].value_counts().reindex([0, 1], fill_value=0)
        print(f"Eligible target counts: {{0: {int(eligible_counts.loc[0]):,}, 1: {int(eligible_counts.loc[1]):,}}}")
        print(f"Eligible positive-class prevalence: {(target.loc[eligible].eq(1).mean()):.2%}")
    print("\nNo rows were removed or modified; this is a read-only audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
