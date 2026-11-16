"""Map BTS airport codes to nearby NOAA GHCNh weather stations.

The map is built only from the 2022-2024 development/validation flight files.
It intentionally does not inspect the 2025 exploratory test split.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import airportsdata
import duckdb
import numpy as np
import pandas as pd


DEVELOPMENT_MONTHS = {
    "train": (2022, 2023),
    "validation": (2024,),
}
STATION_COLUMNS = ["GHCN_ID", "NAME", "LATITUDE", "LONGITUDE", "ICAO"]
OUTPUT_COLUMNS = [
    "airport_code",
    "flight_movements",
    "airport_icao",
    "airport_name",
    "airport_latitude",
    "airport_longitude",
    "timezone",
    "station_id",
    "station_icao",
    "station_name",
    "station_latitude",
    "station_longitude",
    "station_distance_km",
    "match_method",
]


def expected_flight_files(data_dir: Path) -> list[Path]:
    files = [
        data_dir / "flights" / split / f"{year}-{month:02d}.parquet"
        for split, years in DEVELOPMENT_MONTHS.items()
        for year in years
        for month in range(1, 13)
    ]
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"Development/validation flight data is incomplete: {missing[0]}"
        )
    return files


def flight_movement_counts(data_dir: Path) -> pd.DataFrame:
    files = expected_flight_files(data_dir)
    connection = duckdb.connect()
    try:
        result = connection.execute(
            """
            SELECT airport_code, SUM(movements)::BIGINT AS flight_movements
            FROM (
                SELECT TRIM(CAST(Origin AS VARCHAR)) AS airport_code,
                       COUNT(*)::BIGINT AS movements
                FROM read_parquet(?)
                WHERE Origin IS NOT NULL
                GROUP BY 1
                UNION ALL
                SELECT TRIM(CAST(Dest AS VARCHAR)) AS airport_code,
                       COUNT(*)::BIGINT AS movements
                FROM read_parquet(?)
                WHERE Dest IS NOT NULL
                GROUP BY 1
            )
            WHERE airport_code <> ''
            GROUP BY 1
            ORDER BY flight_movements DESC, airport_code
            """,
            [[str(path) for path in files], [str(path) for path in files]],
        ).df()
    finally:
        connection.close()
    result["airport_code"] = result["airport_code"].astype(str).str.upper()
    return result


def great_circle_km(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Vectorized haversine distances from one airport to station coordinates."""
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = np.radians(lats)
    lon2 = np.radians(lons)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    haversine = (
        np.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    )
    return 6371.0088 * 2.0 * np.arcsin(np.sqrt(np.clip(haversine, 0.0, 1.0)))


def airport_record(
    airport_code: str,
    iata_airports: dict[str, dict[str, object]],
    icao_airports: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    record = iata_airports.get(airport_code)
    if record is not None:
        return record
    # BTS airport fields are usually IATA/FAA location identifiers.  Some
    # small stations are only present in the airport database by FAA LID.
    for candidate in icao_airports.values():
        if str(candidate.get("lid", "")).upper() == airport_code:
            return candidate
    return None


def match_airport(
    airport_code: str,
    flight_movements: int,
    iata_airports: dict[str, dict[str, object]],
    icao_airports: dict[str, dict[str, object]],
    stations: pd.DataFrame,
    station_lats: np.ndarray,
    station_lons: np.ndarray,
    station_by_icao: dict[str, pd.DataFrame],
    max_nearest_km: float,
) -> dict[str, object]:
    airport = airport_record(airport_code, iata_airports, icao_airports)
    empty = {column: None for column in OUTPUT_COLUMNS}
    empty.update(airport_code=airport_code, flight_movements=flight_movements)
    if airport is None:
        empty["match_method"] = "airport_metadata_missing"
        return empty

    try:
        lat = float(airport["lat"])
        lon = float(airport["lon"])
    except (KeyError, TypeError, ValueError):
        empty.update(
            airport_icao=airport.get("icao"),
            airport_name=airport.get("name"),
            timezone=airport.get("tz"),
            match_method="airport_coordinates_missing",
        )
        return empty

    airport_icao = str(airport.get("icao", "")).upper()
    exact_codes = list(dict.fromkeys(filter(None, [airport_icao, f"K{airport_code}", f"P{airport_code}", f"T{airport_code}"])))
    exact_candidates = [station_by_icao[code] for code in exact_codes if code in station_by_icao]
    if exact_candidates:
        candidates = pd.concat(exact_candidates, ignore_index=True).drop_duplicates("GHCN_ID")
        distances = great_circle_km(
            lat,
            lon,
            candidates["_latitude"].to_numpy(dtype=float),
            candidates["_longitude"].to_numpy(dtype=float),
        )
        candidate_index = int(np.argmin(distances))
        if float(distances[candidate_index]) <= 50.0:
            station = candidates.iloc[candidate_index]
            distance = float(distances[candidate_index])
            method = "icao"
        else:
            station = None
    else:
        station = None

    if station is None:
        distances = great_circle_km(lat, lon, station_lats, station_lons)
        candidate_index = int(np.argmin(distances))
        distance = float(distances[candidate_index])
        if distance > max_nearest_km:
            empty.update(
                airport_icao=airport_icao or None,
                airport_name=airport.get("name"),
                airport_latitude=lat,
                airport_longitude=lon,
                timezone=airport.get("tz"),
                station_distance_km=distance,
                match_method="no_station_within_radius",
            )
            return empty
        station = stations.iloc[candidate_index]
        method = "nearest_station"

    return {
        "airport_code": airport_code,
        "flight_movements": flight_movements,
        "airport_icao": airport_icao or None,
        "airport_name": airport.get("name"),
        "airport_latitude": lat,
        "airport_longitude": lon,
        "timezone": airport.get("tz"),
        "station_id": str(station["GHCN_ID"]),
        "station_icao": str(station["ICAO"]) if pd.notna(station["ICAO"]) else None,
        "station_name": str(station["NAME"]),
        "station_latitude": float(station["_latitude"]),
        "station_longitude": float(station["_longitude"]),
        "station_distance_km": distance,
        "match_method": method,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/depdel15"))
    parser.add_argument(
        "--station-list",
        type=Path,
        default=Path("data/raw/weather_pilot/ghcnh/ghcnh-station-list.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/depdel15/weather/station_map.csv"),
    )
    parser.add_argument("--max-nearest-km", type=float, default=25.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_nearest_km <= 0:
        print("ERROR: --max-nearest-km must be positive.", file=sys.stderr)
        return 2
    if not args.station_list.is_file():
        print(f"ERROR: NOAA GHCNh station list not found: {args.station_list}", file=sys.stderr)
        return 2
    if args.output.exists() and not args.overwrite:
        print(f"ERROR: output already exists; pass --overwrite to replace it: {args.output}", file=sys.stderr)
        return 2

    try:
        stations = pd.read_csv(args.station_list, dtype={"GHCN_ID": "string", "ICAO": "string"})
        missing = [column for column in STATION_COLUMNS if column not in stations]
        if missing:
            raise ValueError(f"NOAA station list lacks required columns: {', '.join(missing)}")
        stations["_latitude"] = pd.to_numeric(stations["LATITUDE"], errors="coerce")
        stations["_longitude"] = pd.to_numeric(stations["LONGITUDE"], errors="coerce")
        stations = stations.dropna(subset=["_latitude", "_longitude"]).reset_index(drop=True)
        if stations.empty:
            raise ValueError("No weather stations with coordinates were found in the NOAA inventory.")

        station_by_icao = {
            str(icao).upper(): group.reset_index(drop=True)
            for icao, group in stations.dropna(subset=["ICAO"]).groupby("ICAO")
        }
        iata_airports = airportsdata.load("IATA")
        icao_airports = airportsdata.load("ICAO")
        counts = flight_movement_counts(args.data_dir.resolve())
        rows = [
            match_airport(
                row.airport_code,
                int(row.flight_movements),
                iata_airports,
                icao_airports,
                stations,
                stations["_latitude"].to_numpy(dtype=float),
                stations["_longitude"].to_numpy(dtype=float),
                station_by_icao,
                args.max_nearest_km,
            )
            for row in counts.itertuples(index=False)
        ]
        result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_name(f".{args.output.name}.partial")
        result.to_csv(temp, index=False)
        temp.replace(args.output)

        mapped = result["station_id"].notna()
        coverage = float(result.loc[mapped, "flight_movements"].sum() / result["flight_movements"].sum())
        method_counts = result["match_method"].value_counts().to_dict()
        print(f"Airport codes: {len(result)}; mapped: {int(mapped.sum())}; unmapped: {int((~mapped).sum())}")
        print(f"Flight-movement coverage: {coverage:.4%}")
        print(f"Match methods: {method_counts}")
        print(f"Station map: {args.output}")
        if (~mapped).any():
            print("Unmapped codes:", ", ".join(result.loc[~mapped, "airport_code"].head(30)))
    except (OSError, ValueError, KeyError, duckdb.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
