"""Prepare recent BTS months as operational history, separate from model splits.

These months are never used to fit or evaluate the research models. They are
read only by the current-flight inference feature builder, which uses records
strictly earlier than each prediction date.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd
import pyarrow.parquet as pq

from bts_archives import inventory_month
from bts_preprocessing import process_month


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/depdel15/flights/operational_history"))
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--months", type=int, nargs="+", default=list(range(1, 8)))
    parser.add_argument("--chunksize", type=int, default=100_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.year < 2026 or not 1 <= args.chunksize:
        print("ERROR: this history-only processor expects year >= 2026 and a positive chunksize.", file=sys.stderr)
        return 2
    if not args.months or any(month < 1 or month > 12 for month in args.months):
        print("ERROR: provide one or more month numbers from 1 through 12.", file=sys.stderr)
        return 2

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_rows: list[dict[str, object]] = []
    errors = []
    for month in sorted(set(args.months)):
        record = inventory_month(input_dir, args.year, month, verify_crc=False)
        destination = output_dir / f"{args.year}-{month:02d}.parquet"
        audit = {
            "year": args.year,
            "month": month,
            "archive_path": record["path"],
            "archive_state": record["state"],
            "archive_detail": record["detail"],
            "parquet_path": str(destination),
        }
        if record["state"] != "valid":
            audit["status"] = "error"
            audit["error"] = "Archive is missing or invalid; existing files and partial downloads were left unchanged."
            errors.append(f"{args.year}-{month:02d}: {record['state']}")
            audit_rows.append(audit)
            continue
        if destination.exists():
            try:
                metadata = pq.read_metadata(destination)
                audit.update(status="already_present", rows=metadata.num_rows, bytes=destination.stat().st_size)
            except Exception as exc:
                audit.update(status="error", error=f"Existing Parquet could not be read: {type(exc).__name__}: {exc}")
                errors.append(f"{args.year}-{month:02d}: invalid existing Parquet")
            audit_rows.append(audit)
            continue

        temporary = destination.with_name(f".{destination.stem}.partial.parquet")
        temporary.unlink(missing_ok=True)
        try:
            result = process_month(Path(str(record["path"])), args.year, month, temporary, chunksize=args.chunksize)
            if result["eligible_rows"]:
                temporary.replace(destination)
            else:
                temporary.unlink(missing_ok=True)
            audit.update(result)
            audit["parquet_path"] = str(destination) if result["eligible_rows"] else None
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            audit.update(status="error", error=f"{type(exc).__name__}: {exc}")
            errors.append(f"{args.year}-{month:02d}: processing failed")
        audit_rows.append(audit)
        print(
            f"{args.year}-{month:02d}: {audit.get('status')} "
            f"({audit.get('eligible_rows', audit.get('rows', 0)):,} eligible rows)",
            flush=True,
        )

    audit_frame = pd.DataFrame(audit_rows).sort_values("month", kind="stable")
    audit_path = output_dir / "monthly_audit.csv"
    audit_frame.to_csv(audit_path, index=False)
    successful = audit_frame[audit_frame["status"].isin(["processed", "processed_no_eligible_rows", "already_present"])]
    metadata = {
        "dataset": "BTS DepDel15 operational history only",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "year": args.year,
        "months_requested": sorted(set(args.months)),
        "model_training_or_evaluation_used": False,
        "inference_rule": "The current-flight feature builder uses only prior dates and prior completed months.",
        "processed_months": int(successful["month"].nunique()),
        "latest_eligible_date": max(
            (str(value) for value in audit_frame.get("eligible_date_max", pd.Series(dtype=str)).dropna()),
            default=None,
        ),
        "monthly_audit": str(audit_path),
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"History-only BTS Parquet directory: {output_dir}")
    if errors:
        print("Errors: " + "; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
