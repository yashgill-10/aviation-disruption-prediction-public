#!/usr/bin/env python3
"""Chunked, leakage-conscious BTS CSV-to-Parquet preprocessing helpers."""
from __future__ import annotations

import calendar
import hashlib
from pathlib import Path
import zipfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bts_archives import inspect_archive
from canonical_flight_identity import ID_COLS, VERSION, fid, ident


RAW_COLUMNS = [
    "FlightDate",
    "Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "CRSArrTime",
    "Cancelled",
    "DepDel15",
    "Distance",
    "CRSElapsedTime",
    "DOT_ID_Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "OriginAirportID",
    "DestAirportID",
]
CATEGORICAL_COLUMNS = ["Reporting_Airline", "Origin", "Dest"]
SCHEDULED_NUMERIC_COLUMNS = ["CRSDepTime", "CRSArrTime", "Distance", "CRSElapsedTime"]
OUTPUT_COLUMNS = [
    "FlightDate", "Reporting_Airline", "Origin", "Dest", "CRSDepTime", "CRSArrTime",
    "Distance", "CRSElapsedTime", "DepDel15", "source_year", "source_month", "split",
    "flight_id", "identity_version", "identity_status", "source_archive", "source_archive_sha256",
    "source_csv_member", "source_row_ordinal",
]
PROVENANCE_COLUMNS = [
    "flight_id", "identity_version", "identity_status", "source_archive", "source_archive_sha256",
    "source_csv_member", "source_row_ordinal",
]
PARQUET_SCHEMA = pa.schema([
    pa.field("FlightDate", pa.timestamp("ns")),
    pa.field("Reporting_Airline", pa.string()),
    pa.field("Origin", pa.string()),
    pa.field("Dest", pa.string()),
    pa.field("CRSDepTime", pa.float32()),
    pa.field("CRSArrTime", pa.float32()),
    pa.field("Distance", pa.float32()),
    pa.field("CRSElapsedTime", pa.float32()),
    pa.field("DepDel15", pa.int8()),
    pa.field("source_year", pa.int16()),
    pa.field("source_month", pa.int8()),
    pa.field("split", pa.string()),
    pa.field("flight_id", pa.string()),
    pa.field("identity_version", pa.string()),
    pa.field("identity_status", pa.string()),
    pa.field("source_archive", pa.string()),
    pa.field("source_archive_sha256", pa.string()),
    pa.field("source_csv_member", pa.string()),
    pa.field("source_row_ordinal", pa.int64()),
])


def split_for_year(year: int) -> str:
    if year in (2022, 2023):
        return "train"
    if year == 2024:
        return "validation"
    if year == 2025:
        return "test"
    if year >= 2026:
        return "operational_history"
    raise ValueError(f"No chronological split is defined for {year}.")


def _minmax(current_min: pd.Timestamp | None, current_max: pd.Timestamp | None,
            values: pd.Series) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if values.notna().any():
        value_min = values.min()
        value_max = values.max()
        if current_min is None or value_min < current_min:
            current_min = value_min
        if current_max is None or value_max > current_max:
            current_max = value_max
    return current_min, current_max


def process_month(archive_path: Path, year: int, month: int, output_path: Path,
                  chunksize: int = 100_000) -> dict[str, object]:
    """Stream one BTS ZIP member to one Parquet file without extracting the CSV.

    The output path must not exist. It is suitable for a temporary path; callers
    should rename it to its final name only after this function succeeds.
    """
    if chunksize < 1:
        raise ValueError("chunksize must be positive")
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output_path}")
    checked = inspect_archive(archive_path, verify_crc=False)
    if checked.state != "valid" or not checked.csv_member:
        raise ValueError(f"archive is not structurally valid: {checked.detail}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    audit: dict[str, object] = {
        "year": year,
        "month": month,
        "split": split_for_year(year),
        "archive_path": str(archive_path),
        "status": "processing",
        "source_rows": 0,
        "cancelled_rows_excluded": 0,
        "unknown_cancellation_rows": 0,
        "missing_target_rows": 0,
        "invalid_target_rows": 0,
        "nonbinary_target_rows": 0,
        "invalid_date_rows": 0,
        "out_of_month_date_rows": 0,
        "eligible_rows": 0,
        "identity_resolved_rows": 0,
        "identity_unresolved_rows": 0,
        "source_archive_sha256": None,
        "source_csv_member": checked.csv_member,
        "target_0": 0,
        "target_1": 0,
        "observed_date_count": 0,
        "observed_date_min": None,
        "observed_date_max": None,
        "missing_month_dates": "",
        "eligible_date_min": None,
        "eligible_date_max": None,
        "crc_verified": False,
        "parquet_path": None,
        "error": "",
    }
    source_sha256 = hashlib.sha256()
    with archive_path.open("rb") as archive_stream:
        for block in iter(lambda: archive_stream.read(1024 * 1024), b""):
            source_sha256.update(block)
    archive_sha256 = source_sha256.hexdigest()
    audit["source_archive_sha256"] = archive_sha256
    audit["source_archive"] = archive_path.name

    source_min = source_max = None
    eligible_min = eligible_max = None
    observed_days: set[int] = set()
    writer: pq.ParquetWriter | None = None

    try:
        with zipfile.ZipFile(archive_path) as archive:
            with archive.open(checked.csv_member, "r") as member_stream:
                reader = pd.read_csv(
                    member_stream,
                    usecols=RAW_COLUMNS,
                    dtype={column: "string" for column in RAW_COLUMNS},
                    chunksize=chunksize,
                    low_memory=False,
                    encoding="utf-8-sig",
                )
                for chunk in reader:
                    rows = len(chunk)
                    source_offset = int(audit["source_rows"])
                    audit["source_rows"] += rows
                    dates = pd.to_datetime(chunk["FlightDate"].str.strip(), errors="coerce")
                    target_raw = chunk["DepDel15"].str.strip()
                    cancelled_raw = chunk["Cancelled"].str.strip()
                    target = pd.to_numeric(target_raw, errors="coerce")
                    cancelled = pd.to_numeric(cancelled_raw, errors="coerce")

                    target_missing = (target_raw.isna() | target_raw.eq("")).fillna(True)
                    target_invalid = (~target_missing & target.isna()).fillna(False)
                    target_nonbinary = (target.notna() & ~target.isin([0, 1])).fillna(False)
                    cancellation_unknown = ~cancelled.isin([0, 1])
                    date_valid = dates.notna()
                    date_in_month = date_valid & dates.dt.year.eq(year) & dates.dt.month.eq(month)
                    date_outside_month = date_valid & ~date_in_month
                    eligible = (
                        cancelled.eq(0).fillna(False)
                        & target.isin([0, 1]).fillna(False)
                        & date_in_month.fillna(False)
                    )

                    audit["cancelled_rows_excluded"] += int(cancelled.eq(1).sum())
                    audit["unknown_cancellation_rows"] += int(cancellation_unknown.sum())
                    audit["missing_target_rows"] += int(target_missing.sum())
                    audit["invalid_target_rows"] += int(target_invalid.sum())
                    audit["nonbinary_target_rows"] += int(target_nonbinary.sum())
                    audit["invalid_date_rows"] += int((~date_valid).sum())
                    audit["out_of_month_date_rows"] += int(date_outside_month.sum())

                    source_min, source_max = _minmax(source_min, source_max, dates)
                    if date_in_month.any():
                        observed_days.update(int(day) for day in dates.loc[date_in_month].dt.day.unique())
                    if not eligible.any():
                        continue

                    selected_dates = dates.loc[eligible].reset_index(drop=True)
                    eligible_min, eligible_max = _minmax(eligible_min, eligible_max, selected_dates)
                    selected_target = target.loc[eligible].astype("int8").reset_index(drop=True)
                    audit["eligible_rows"] += len(selected_target)
                    audit["target_0"] += int(selected_target.eq(0).sum())
                    audit["target_1"] += int(selected_target.eq(1).sum())

                    prepared = pd.DataFrame({"FlightDate": selected_dates})
                    for column in CATEGORICAL_COLUMNS:
                        values = chunk.loc[eligible, column].astype("string").str.strip()
                        values = values.mask(values.eq(""), pd.NA)
                        prepared[column] = values.reset_index(drop=True)
                    for column in SCHEDULED_NUMERIC_COLUMNS:
                        values = pd.to_numeric(chunk.loc[eligible, column], errors="coerce").astype("float32")
                        prepared[column] = values.reset_index(drop=True)
                    prepared["DepDel15"] = selected_target
                    prepared["source_year"] = pd.Series(year, index=prepared.index, dtype="int16")
                    prepared["source_month"] = pd.Series(month, index=prepared.index, dtype="int8")
                    prepared["split"] = pd.Series(split_for_year(year), index=prepared.index, dtype="string")

                    # IDs and ordinals are attached while each raw BTS source row is still present.
                    # Ordinals are one-based within the original CSV member, before any filtering.
                    source_ordinals = pd.Series(
                        range(source_offset + 1, source_offset + rows + 1),
                        index=chunk.index,
                        dtype="int64",
                    ).loc[eligible].reset_index(drop=True)
                    flight_ids: list[str | None] = []
                    identity_status: list[str] = []
                    for identity_row in chunk.loc[eligible, ID_COLS].to_dict(orient="records"):
                        try:
                            flight_ids.append(fid(ident(identity_row)))
                            identity_status.append("resolved")
                        except (TypeError, ValueError):
                            flight_ids.append(None)
                            identity_status.append("unresolved_incomplete_or_invalid")
                    unresolved = sum(status != "resolved" for status in identity_status)
                    audit["identity_unresolved_rows"] += unresolved
                    audit["identity_resolved_rows"] += len(identity_status) - unresolved
                    prepared["flight_id"] = pd.Series(flight_ids, dtype="string")
                    prepared["identity_version"] = pd.Series(VERSION, index=prepared.index, dtype="string")
                    prepared["identity_status"] = pd.Series(identity_status, dtype="string")
                    prepared["source_archive"] = pd.Series(archive_path.name, index=prepared.index, dtype="string")
                    prepared["source_archive_sha256"] = pd.Series(archive_sha256, index=prepared.index, dtype="string")
                    prepared["source_csv_member"] = pd.Series(checked.csv_member, index=prepared.index, dtype="string")
                    prepared["source_row_ordinal"] = source_ordinals
                    prepared = prepared[OUTPUT_COLUMNS]

                    table = pa.Table.from_pandas(prepared, schema=PARQUET_SCHEMA,
                                                 preserve_index=False, safe=True)
                    if writer is None:
                        writer = pq.ParquetWriter(output_path, PARQUET_SCHEMA, compression="zstd")
                    writer.write_table(table, row_group_size=len(prepared))

        if writer is not None:
            writer.close()
            writer = None

        month_days = calendar.monthrange(year, month)[1]
        missing_days = sorted(set(range(1, month_days + 1)) - observed_days)
        audit["observed_date_count"] = len(observed_days)
        audit["observed_date_min"] = f"{year:04d}-{month:02d}-{min(observed_days):02d}" if observed_days else None
        audit["observed_date_max"] = f"{year:04d}-{month:02d}-{max(observed_days):02d}" if observed_days else None
        audit["missing_month_dates"] = ";".join(f"{year:04d}-{month:02d}-{day:02d}" for day in missing_days)
        audit["source_date_min"] = source_min.strftime("%Y-%m-%d") if source_min is not None else None
        audit["source_date_max"] = source_max.strftime("%Y-%m-%d") if source_max is not None else None
        audit["eligible_date_min"] = eligible_min.strftime("%Y-%m-%d") if eligible_min is not None else None
        audit["eligible_date_max"] = eligible_max.strftime("%Y-%m-%d") if eligible_max is not None else None
        audit["crc_verified"] = True
        audit["status"] = "processed" if audit["eligible_rows"] else "processed_no_eligible_rows"
        return audit
    except Exception:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        output_path.unlink(missing_ok=True)
        raise
