#!/usr/bin/env python3
"""Inventory BTS monthly ZIPs and optionally verify every member CRC."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from bts_archives import inventory_month


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"),
                        help="BTS archive directory (default: data/raw)")
    parser.add_argument("--years", nargs="+", type=int, default=[2022, 2023, 2024, 2025],
                        help="years to inventory (default: 2022 2023 2024 2025)")
    parser.add_argument("--months", nargs="+", type=int, choices=range(1, 13), default=None,
                        help="months to inventory; default is all 12")
    parser.add_argument("--list-only", action="store_true",
                        help="check ZIP structure/member names but skip full CRC reads")
    parser.add_argument("--require-complete", action="store_true",
                        help="return exit code 1 unless every requested archive passes full CRC checks")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    years = sorted(set(args.years))
    months = sorted(set(args.months or range(1, 13)))
    data_dir = args.data_dir.expanduser().resolve()
    verify_crc = not args.list_only

    print("BTS monthly archive inventory")
    print(f"Directory: {data_dir}")
    print(f"CRC verification: {'enabled' if verify_crc else 'skipped (--list-only)'}")
    print("Partial .part files are only listed; they are never opened, deleted, or renamed.\n")

    records = []
    counts: Counter[str] = Counter()
    for year in years:
        for month in months:
            record = inventory_month(data_dir, year, month, verify_crc=verify_crc)
            records.append(record)
            counts[str(record["state"])] += 1
            if record["state"] == "valid" and not record["crc_verified"]:
                label = "ZIP-OK / CRC UNCHECKED"
            else:
                label = str(record["state"]).upper()
            size = record["archive_bytes"]
            size_text = f"{size / (1024 * 1024):.1f} MiB" if isinstance(size, int) else "-"
            print(f"{year}-{month:02d}  {label:24} {size_text:>10}  {record['detail']}")

    print("\nCoverage by year:")
    for year in years:
        year_records = [record for record in records if record["year"] == year]
        verified = sum(record["state"] == "valid" and record["crc_verified"] for record in year_records)
        structurally_valid = sum(record["state"] == "valid" for record in year_records)
        if months == list(range(1, 13)):
            complete = "complete with CRC verification" if verified == 12 else "not complete/verified"
            print(f"  {year}: {verified}/12 CRC-verified; {structurally_valid}/12 structurally valid; {complete}")
        else:
            print(f"  {year}: {verified}/{len(months)} CRC-verified; {structurally_valid}/{len(months)} structurally valid")

    print("\nRequested-month totals: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    if args.require_complete:
        if not verify_crc or any(record["state"] != "valid" or not record["crc_verified"] for record in records):
            print("STATUS: requested archives are not all CRC-verified.")
            return 1
    print("STATUS: inventory finished; source archives were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
