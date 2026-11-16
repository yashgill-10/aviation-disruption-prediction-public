#!/usr/bin/env python3
"""Shared, read-only helpers for BTS monthly ZIP archives."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zipfile
import zlib


ARCHIVE_NAME = "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
CSV_MARKER = "on_time_reporting_carrier_on_time_performance"


@dataclass(frozen=True)
class ArchiveCheck:
    state: str
    detail: str
    csv_member: str | None = None
    crc_verified: bool = False


def expected_archive(data_dir: Path, year: int, month: int) -> Path:
    return data_dir / ARCHIVE_NAME.format(year=year, month=month)


def inspect_archive(path: Path, verify_crc: bool = False) -> ArchiveCheck:
    """Check ZIP structure and its BTS CSV member, optionally reading every CRC.

    This function never extracts, renames, or modifies an archive.
    """
    if not path.is_file():
        return ArchiveCheck("missing", "archive file does not exist")
    try:
        with zipfile.ZipFile(path) as archive:
            csv_members = [
                member for member in archive.namelist()
                if member.lower().endswith(".csv") and CSV_MARKER in member.lower()
            ]
            if len(csv_members) != 1:
                return ArchiveCheck(
                    "invalid",
                    f"expected exactly one BTS on-time CSV member; found {len(csv_members)}",
                )
            member = csv_members[0]
            if verify_crc:
                bad_member = archive.testzip()
                if bad_member is not None:
                    return ArchiveCheck("invalid", f"CRC check failed for member {bad_member}", member)
            detail = "ZIP structure and BTS CSV member are valid"
            if verify_crc:
                detail += "; all member CRC checks passed"
            else:
                detail += "; CRC not checked"
            return ArchiveCheck("valid", detail, member, verify_crc)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError,
            EOFError, NotImplementedError, zlib.error) as exc:
        return ArchiveCheck("invalid", f"{type(exc).__name__}: {exc}")


def inventory_month(data_dir: Path, year: int, month: int, verify_crc: bool = False) -> dict[str, object]:
    """Describe one expected month. Partial downloads are only stat'ed, never opened."""
    path = expected_archive(data_dir, year, month)
    partial = Path(str(path) + ".part")
    partial_exists = partial.is_file()
    partial_bytes = partial.stat().st_size if partial_exists else None

    if not path.exists():
        if partial_exists:
            return {
                "year": year,
                "month": month,
                "path": str(path),
                "state": "partial",
                "detail": f"only .part file exists ({partial_bytes} bytes); left untouched",
                "csv_member": None,
                "archive_bytes": None,
                "crc_verified": False,
                "partial_path": str(partial),
                "partial_bytes": partial_bytes,
            }
        return {
            "year": year,
            "month": month,
            "path": str(path),
            "state": "missing",
            "detail": "no archive or .part file found",
            "csv_member": None,
            "archive_bytes": None,
            "crc_verified": False,
            "partial_path": None,
            "partial_bytes": None,
        }

    checked = inspect_archive(path, verify_crc=verify_crc)
    detail = checked.detail
    if partial_exists:
        detail += f"; separate .part file also exists ({partial_bytes} bytes) and was left untouched"
    return {
        "year": year,
        "month": month,
        "path": str(path),
        "state": checked.state,
        "detail": detail,
        "csv_member": checked.csv_member,
        "archive_bytes": path.stat().st_size,
        "crc_verified": checked.crc_verified,
        "partial_path": str(partial) if partial_exists else None,
        "partial_bytes": partial_bytes,
    }
