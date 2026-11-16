"""Download NOAA GHCNh station-year Parquet files for mapped airports.

The downloader is resumable: existing files are skipped, successful downloads
are validated and atomically renamed, and unavailable station-years are logged.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import re
import sys
import time

import pandas as pd
import pyarrow.parquet as pq
import requests


BASE_URL = (
    "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/"
    "hourly/access/by-year/{year}/parquet/GHCNh_{station}_{year}.parquet"
)
STATION_ID_PATTERN = re.compile(r"^[A-Z0-9-]{4,20}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--station-map",
        type=Path,
        default=Path("data/processed/depdel15/weather/station_map.csv"),
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/ghcnh"))
    parser.add_argument("--years", type=int, nargs="+", default=[2022, 2023, 2024])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--limit-stations",
        type=int,
        default=None,
        help="download only the N highest-volume airport stations (useful for a pilot)",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


def file_path(data_dir: Path, station: str, year: int) -> Path:
    return data_dir / str(year) / f"GHCNh_{station}_{year}.parquet"


def download_one(
    station: str,
    year: int,
    data_dir: Path,
    timeout_seconds: int,
) -> dict[str, object]:
    destination = file_path(data_dir, station, year)
    if destination.is_file():
        try:
            metadata = pq.read_metadata(destination)
            return {
                "station_id": station,
                "year": year,
                "status": "already_present",
                "bytes": destination.stat().st_size,
                "rows": metadata.num_rows,
                "url": BASE_URL.format(station=station, year=year),
                "error": "",
            }
        except Exception:
            # Leave the questionable file untouched for review; do not hide it
            # by deleting or replacing user data automatically.
            return {
                "station_id": station,
                "year": year,
                "status": "invalid_existing_file",
                "bytes": destination.stat().st_size,
                "rows": None,
                "url": BASE_URL.format(station=station, year=year),
                "error": "Existing Parquet metadata could not be read.",
            }

    url = BASE_URL.format(station=station, year=year)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error = ""
    for attempt in range(3):
        try:
            with requests.get(
                url,
                stream=True,
                timeout=(15, timeout_seconds),
                headers={"User-Agent": "aviation-disruption-prediction/0.1 (research)"},
            ) as response:
                if response.status_code == 404:
                    return {
                        "station_id": station,
                        "year": year,
                        "status": "not_available",
                        "bytes": 0,
                        "rows": None,
                        "url": url,
                        "error": "NOAA returned HTTP 404 for this station-year.",
                    }
                response.raise_for_status()
                byte_count = 0
                with partial.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
                            byte_count += len(chunk)
                if byte_count == 0:
                    raise IOError("NOAA returned an empty response body.")
            metadata = pq.read_metadata(partial)
            os.replace(partial, destination)
            return {
                "station_id": station,
                "year": year,
                "status": "downloaded",
                "bytes": byte_count,
                "rows": metadata.num_rows,
                "url": url,
                "error": "",
            }
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        partial.unlink(missing_ok=True)
        if attempt < 2:
            time.sleep(2**attempt)

    return {
        "station_id": station,
        "year": year,
        "status": "failed",
        "bytes": 0,
        "rows": None,
        "url": url,
        "error": last_error,
    }


def main() -> int:
    args = parse_args()
    if args.workers < 1 or args.timeout_seconds < 1:
        print("ERROR: workers and timeout must be positive.", file=sys.stderr)
        return 2
    if args.limit_stations is not None and args.limit_stations < 1:
        print("ERROR: --limit-stations must be positive.", file=sys.stderr)
        return 2
    if not args.station_map.is_file():
        print(f"ERROR: station map not found: {args.station_map}", file=sys.stderr)
        return 2

    try:
        station_map = pd.read_csv(args.station_map, dtype={"station_id": "string"})
        if "station_id" not in station_map or "flight_movements" not in station_map:
            raise ValueError("Station map must contain station_id and flight_movements columns.")
        station_map = station_map.dropna(subset=["station_id"]).copy()
        station_map["station_id"] = station_map["station_id"].astype(str).str.strip().str.upper()
        invalid = sorted(
            station for station in station_map["station_id"].unique()
            if not STATION_ID_PATTERN.fullmatch(station)
        )
        if invalid:
            raise ValueError(f"Unsafe or malformed NOAA station ID(s): {', '.join(invalid[:10])}")
        stations = (
            station_map.groupby("station_id", as_index=False)["flight_movements"]
            .sum()
            .sort_values(["flight_movements", "station_id"], ascending=[False, True])
        )
        if args.limit_stations is not None:
            stations = stations.head(args.limit_stations)
        jobs = [(station, year) for station in stations["station_id"] for year in sorted(set(args.years))]
        args.data_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"Checking {len(jobs)} station-year file(s) across {len(stations)} stations; "
        f"workers={args.workers}; format=Parquet",
        flush=True,
    )
    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download_one, station, year, args.data_dir, args.timeout_seconds): (station, year)
            for station, year in jobs
        }
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if index % 25 == 0 or index == len(jobs):
                print(f"Completed {index}/{len(jobs)} station-year files", flush=True)

    manifest = pd.DataFrame(results).sort_values(["year", "station_id"])
    manifest_path = args.data_dir / "download_manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    summary = manifest["status"].value_counts().to_dict()
    total_bytes = int(pd.to_numeric(manifest["bytes"], errors="coerce").fillna(0).sum())
    print(f"Statuses: {summary}")
    print(f"Bytes downloaded or already present: {total_bytes:,}")
    print(f"Manifest: {manifest_path}")
    failures = manifest[manifest["status"].isin(["failed", "invalid_existing_file"])]
    if not failures.empty:
        print("Failed files:")
        print(failures[["station_id", "year", "status", "error"]].head(20).to_string(index=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
