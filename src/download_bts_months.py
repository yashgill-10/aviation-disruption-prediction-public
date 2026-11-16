"""Download and verify BTS monthly on-time-performance ZIP archives.

Downloads official Reporting Carrier On-Time Performance monthly archives from
https://transtats.bts.gov/PREZIP/ into <repo>/data/raw. Existing archives are
skipped after a fast ZIP-directory/member check; use inventory_bts_archives.py
when you need a full CRC verification.

Examples (run from the repository root):
    python src/download_bts_months.py --years 2024 --months 2
    python src/download_bts_months.py --years 2022 2023 2024 2025
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Iterator

import requests

BASE_URL = "https://transtats.bts.gov/PREZIP"
FILE_TEMPLATE = (
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
USER_AGENT = "aviation-disruption-course-project/1.0 (public BTS data download)"
CHUNK_SIZE = 1024 * 1024
MIN_ARCHIVE_BYTES = 100_000
LOCK_FILENAME = ".bts-download.lock"


def is_valid_zip(path: Path) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                return False, f"CRC/integrity error in member: {bad_member}"
            csv_members = [
                item for item in archive.namelist()
                if item.lower().endswith(".csv")
                and "on_time_reporting_carrier_on_time_performance" in item.lower()
            ]
            if len(csv_members) != 1:
                return False, f"expected one on-time CSV, found {len(csv_members)}"
            return True, f"valid; CSV member: {csv_members[0]}"
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return False, str(exc)


def has_expected_bts_member(path: Path) -> tuple[bool, str]:
    """Quickly inspect an existing ZIP without decompressing members or checking CRCs."""
    try:
        size = path.stat().st_size
        if size < MIN_ARCHIVE_BYTES:
            return False, f"archive is too small ({size} bytes)"
        with zipfile.ZipFile(path) as archive:
            csv_members = [
                item for item in archive.namelist()
                if item.lower().endswith(".csv")
                and "on_time_reporting_carrier_on_time_performance" in item.lower()
            ]
            if len(csv_members) != 1:
                return False, f"expected one on-time CSV, found {len(csv_members)}"
            return True, f"ZIP directory and BTS CSV member are present; CRC not rechecked ({csv_members[0]})"
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return False, str(exc)


class DownloadLockError(RuntimeError):
    """Raised when another downloader already owns the output directory."""


@contextmanager
def exclusive_download_lock(output_dir: Path) -> Iterator[None]:
    """Hold an OS-managed lock so a second downloader cannot share partial files."""
    lock_path = output_dir / LOCK_FILENAME
    handle = lock_path.open("a+b")
    acquired = False
    windows_lock = os.name == "nt"
    try:
        if windows_lock:
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise DownloadLockError(
                    f"another downloader holds {lock_path}; let it finish before starting another"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise DownloadLockError(
                    f"another downloader holds {lock_path}; let it finish before starting another"
                ) from exc

        acquired = True
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n".encode("ascii"))
        handle.flush()
        yield
    finally:
        if acquired:
            if windows_lock:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def download_one(session: requests.Session, year: int, month: int, destination: Path) -> bool:
    filename = FILE_TEMPLATE.format(year=year, month=month)
    target = destination / filename
    if target.exists():
        valid, detail = has_expected_bts_member(target)
        if valid:
            print(f"SKIP  {year}-{month:02d}: existing archive is {detail}")
            return True
        print(f"ERROR {year}-{month:02d}: existing file is not valid: {detail}")
        print("       It was left unchanged. Run the inventory CRC check before deciding what to do.")
        return False

    url = f"{BASE_URL}/{filename}"
    partial = destination / (filename + ".part")

    for attempt in range(1, 4):
        downloaded = 0
        try:
            if partial.exists():
                partial.unlink()
            print(f"GET   {year}-{month:02d}: {url} (attempt {attempt}/3)")
            with session.get(url, stream=True, timeout=(20, 120)) as response:
                if response.status_code != 200:
                    print(f"ERROR {year}-{month:02d}: HTTP {response.status_code}")
                    if response.status_code in (403, 404):
                        return False
                    response.raise_for_status()
                expected = response.headers.get("Content-Length")
                expected_bytes = int(expected) if expected and expected.isdigit() else None
                next_report = 10 * CHUNK_SIZE
                with partial.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        output.write(chunk)
                        downloaded += len(chunk)
                        if downloaded >= next_report:
                            if expected_bytes:
                                pct = 100 * downloaded / expected_bytes
                                print(f"       {downloaded / (1024**2):.1f} MiB / {expected_bytes / (1024**2):.1f} MiB ({pct:.0f}%)")
                            else:
                                print(f"       {downloaded / (1024**2):.1f} MiB downloaded")
                            next_report += 10 * CHUNK_SIZE
            if downloaded < MIN_ARCHIVE_BYTES:
                raise ValueError(f"download too small ({downloaded} bytes); response may not be a ZIP")
            valid, detail = is_valid_zip(partial)
            if not valid:
                raise ValueError(f"archive validation failed: {detail}")
            os.replace(partial, target)
            print(f"OK    {year}-{month:02d}: {downloaded / (1024**2):.1f} MiB; {detail}")
            return True
        except (requests.RequestException, OSError, ValueError, zipfile.BadZipFile) as exc:
            print(f"WARN  {year}-{month:02d}: attempt failed: {exc}")
            try:
                if partial.exists():
                    partial.unlink()
            except OSError:
                pass
            if attempt < 3:
                time.sleep(2 * attempt)

    print(f"FAILED {year}-{month:02d}: all attempts failed")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years", nargs="+", type=int, default=[2022, 2023, 2024, 2025],
        help="years to download (default: 2022 2023 2024 2025)",
    )
    parser.add_argument(
        "--months", nargs="+", type=int, choices=range(1, 13), default=None,
        help="optional months to download; by default downloads all 12 months for each year",
    )
    parser.add_argument(
        "--output-dir", default="data/raw",
        help="output directory relative to the repository root (default: data/raw)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    invalid_years = [year for year in args.years if year < 1987 or year > 2026]
    if invalid_years:
        print(f"ERROR: unsupported year(s): {invalid_years}; BTS table starts in 1987", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    months = sorted(set(args.months)) if args.months else list(range(1, 13))

    print("BTS official monthly archive downloader")
    print(f"Output directory: {output_dir}")
    print(f"Years: {', '.join(map(str, sorted(set(args.years))))}")
    print(f"Months: {', '.join(f'{month:02d}' for month in months)}")
    print("Existing archives with the expected ZIP/member structure will be skipped; raw CSVs are not extracted.")
    print("Only one downloader can run against an output directory at a time.")
    print("Source: https://transtats.bts.gov/PREZIP/\n")

    try:
        with exclusive_download_lock(output_dir):
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT})
            results: list[tuple[int, int, bool]] = []
            for year in sorted(set(args.years)):
                for month in months:
                    ok = download_one(session, year, month, output_dir)
                    results.append((year, month, ok))
    except DownloadLockError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    failed = [(year, month) for year, month, ok in results if not ok]
    passed = len(results) - len(failed)
    print("\n=== Download summary ===")
    print(f"Successful or already valid: {passed}/{len(results)}")
    if failed:
        print("Failed months:")
        for year, month in failed:
            print(f"  {year}-{month:02d}")
        print("Fix the listed failures and rerun the same command; valid files will be skipped.")
        return 1
    print("All requested monthly archives passed ZIP and CSV-member checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
