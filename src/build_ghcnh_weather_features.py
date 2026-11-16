"""Join point-in-time NOAA GHCNh METAR observations to BTS flight features.

Weather is attached separately for the origin and destination airport at each
prediction horizon.  NOAA's observation time is treated as available 15
minutes later, and reports older than three hours are marked missing.  The
script reads only the 2022-2024 train/validation feature partitions; it never
opens the 2025 exploratory test split.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import duckdb
import pandas as pd


PARTITIONS = {"train": (2022, 2023), "validation": (2024,)}
HORIZONS = {"t_minus_2h": 120, "t_minus_30m": 30}
WEATHER_FIELDS = [
    "temperature_c",
    "dew_point_temperature_c",
    "wind_speed_m_s",
    "wind_direction_deg",
    "wind_gust_m_s",
    "visibility_km",
    "sea_level_pressure_hpa",
    "weather_rain",
    "weather_snow",
    "weather_fog_or_mist",
    "weather_thunderstorm",
]


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def expected_flight_files(
    historical_dir: Path,
    months: list[str] | None = None,
) -> list[tuple[str, int, int, Path]]:
    if months:
        if len(set(months)) != len(months):
            raise ValueError("Requested weather months contain duplicates.")
        result = []
        for label in sorted(months):
            parts = label.split("-")
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                raise ValueError(f"Invalid month label: {label}")
            year, month = int(parts[0]), int(parts[1])
            if year != 2024 or not 1 <= month <= 12:
                raise ValueError(f"Selected feature months must be 2024 validation months: {label}")
            result.append(("validation", year, month,
                           historical_dir / "flights" / "validation" / f"{label}.parquet"))
    else:
        result = []
        for split, years in PARTITIONS.items():
            for year in years:
                for month in range(1, 13):
                    result.append(
                        (split, year, month,
                         historical_dir / "flights" / split / f"{year}-{month:02d}.parquet")
                    )
    missing = [path for _, _, _, path in result if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Historical train/validation features are incomplete: {missing[0]}")
    return result


def get_weather_files(
    weather_dir: Path,
    stations: list[str],
    needed_years: set[int],
    allow_missing: bool,
) -> list[Path]:
    expected = [
        weather_dir / str(year) / f"GHCNh_{station}_{year}.parquet"
        for year in sorted(needed_years)
        for station in stations
    ]
    present = [path for path in expected if path.is_file()]
    missing = [path for path in expected if not path.is_file()]
    if missing and not allow_missing:
        raise FileNotFoundError(
            f"Missing {len(missing):,} station-year weather file(s); first is {missing[0]}. "
            "Run src/download_ghcnh_weather.py or pass --allow-missing-weather for a pilot."
        )
    if not present:
        raise FileNotFoundError(f"No NOAA GHCNh Parquet files found under {weather_dir}.")
    if missing:
        print(
            f"WARNING: {len(missing):,} station-year file(s) are absent; affected airport weather will be missing.",
            file=sys.stderr,
        )
    return present


def make_weather_view(connection: duckdb.DuckDBPyConnection, files: list[Path]) -> None:
    file_list = ", ".join(sql_string(path.resolve()) for path in files)
    connection.execute(
        f"""
        CREATE TEMP TABLE ghcnh_weather AS
        WITH reports AS (
            SELECT
                CAST(STATION AS VARCHAR) AS station_id,
                timezone('UTC', CAST(DATE AS TIMESTAMP) + INTERVAL 15 MINUTE) AS available_at_utc,
                CASE WHEN temperature_Report_Type = 'FM15'
                     THEN TRY_CAST(temperature AS DOUBLE) END AS temperature_c,
                CASE WHEN dew_point_temperature_Report_Type = 'FM15'
                     THEN TRY_CAST(dew_point_temperature AS DOUBLE) END AS dew_point_temperature_c,
                CASE WHEN wind_speed_Report_Type = 'FM15'
                     THEN TRY_CAST(wind_speed AS DOUBLE) END AS wind_speed_m_s,
                CASE WHEN wind_direction_Report_Type = 'FM15'
                     THEN TRY_CAST(wind_direction AS DOUBLE) END AS wind_direction_deg,
                CASE WHEN wind_gust_Report_Type = 'FM15'
                     THEN TRY_CAST(wind_gust AS DOUBLE) END AS wind_gust_m_s,
                CASE WHEN visibility_Report_Type = 'FM15'
                     THEN TRY_CAST(visibility AS DOUBLE) END AS visibility_km,
                CASE WHEN sea_level_pressure_Report_Type = 'FM15'
                     THEN TRY_CAST(sea_level_pressure AS DOUBLE) END AS sea_level_pressure_hpa,
                concat_ws(' ',
                    CASE WHEN pres_wx_AU1_Report_Type = 'FM15' THEN pres_wx_AU1 END,
                    CASE WHEN pres_wx_AU2_Report_Type = 'FM15' THEN pres_wx_AU2 END,
                    CASE WHEN pres_wx_AU3_Report_Type = 'FM15' THEN pres_wx_AU3 END,
                    CASE WHEN pres_wx_MW1_Report_Type = 'FM15' THEN pres_wx_MW1 END,
                    CASE WHEN pres_wx_MW2_Report_Type = 'FM15' THEN pres_wx_MW2 END,
                    CASE WHEN pres_wx_MW3_Report_Type = 'FM15' THEN pres_wx_MW3 END
                ) AS present_weather
            FROM read_parquet([{file_list}], union_by_name = true)
            WHERE temperature_Report_Type = 'FM15'
        )
        SELECT
            station_id,
            available_at_utc,
            CASE WHEN temperature_c BETWEEN -90 AND 60 THEN temperature_c END AS temperature_c,
            CASE WHEN dew_point_temperature_c BETWEEN -100 AND 60 THEN dew_point_temperature_c END AS dew_point_temperature_c,
            CASE WHEN wind_speed_m_s BETWEEN 0 AND 100 THEN wind_speed_m_s END AS wind_speed_m_s,
            CASE WHEN wind_direction_deg BETWEEN 0 AND 360 THEN wind_direction_deg END AS wind_direction_deg,
            CASE WHEN wind_gust_m_s BETWEEN 0 AND 125 THEN wind_gust_m_s END AS wind_gust_m_s,
            CASE WHEN visibility_km BETWEEN 0 AND 100 THEN visibility_km END AS visibility_km,
            CASE WHEN sea_level_pressure_hpa BETWEEN 800 AND 1100 THEN sea_level_pressure_hpa END AS sea_level_pressure_hpa,
            CASE WHEN regexp_matches(present_weather, '(RA|DZ)') THEN 1 ELSE 0 END::TINYINT AS weather_rain,
            CASE WHEN regexp_matches(present_weather, '(SN|SG|IC|PL|GR|GS)') THEN 1 ELSE 0 END::TINYINT AS weather_snow,
            CASE WHEN regexp_matches(present_weather, '(FG|BR|HZ)') THEN 1 ELSE 0 END::TINYINT AS weather_fog_or_mist,
            CASE WHEN regexp_matches(present_weather, 'TS') THEN 1 ELSE 0 END::TINYINT AS weather_thunderstorm
        FROM reports
        """
    )


def build_month(
    connection: duckdb.DuckDBPyConnection,
    source: Path,
    destination: Path,
    split: str,
    month_label: str,
    horizon_name: str,
    horizon_minutes: int,
    max_weather_age_minutes: int,
    airport_codes: list[str] | None,
    overwrite: bool,
) -> dict[str, object]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output exists; pass --overwrite to replace it: {destination}")
    temp_output = destination.with_suffix(destination.suffix + ".part")
    temp_output.unlink(missing_ok=True)
    source_sql = sql_string(source.resolve())
    output_sql = sql_string(temp_output.resolve())
    airport_filter = ""
    if airport_codes:
        quoted = ", ".join(sql_string(code) for code in airport_codes)
        airport_filter = f"WHERE upper(trim(CAST(Origin AS VARCHAR))) IN ({quoted}) OR upper(trim(CAST(Dest AS VARCHAR))) IN ({quoted})"

    connection.execute(
        f"""
        COPY (
            WITH source_flights AS (
                SELECT * FROM read_parquet({source_sql}, union_by_name = true)
                {airport_filter}
            ),
            scheduled AS (
                SELECT
                    f.*,
                    om.station_id AS origin_station_id,
                    om.timezone AS origin_timezone,
                    dm.station_id AS destination_station_id,
                    dm.timezone AS destination_timezone,
                    CASE
                        WHEN TRY_CAST(f.CRSDepTime AS INTEGER) BETWEEN 0 AND 2400
                         AND TRY_CAST(f.CRSDepTime AS INTEGER) % 100 < 60
                        THEN CAST(FLOOR(TRY_CAST(f.CRSDepTime AS DOUBLE) / 100) * 60
                             + TRY_CAST(f.CRSDepTime AS INTEGER) % 100 AS BIGINT)
                    END AS scheduled_minute
                FROM source_flights f
                LEFT JOIN airport_map om ON upper(trim(CAST(f.Origin AS VARCHAR))) = om.airport_code
                LEFT JOIN airport_map dm ON upper(trim(CAST(f.Dest AS VARCHAR))) = dm.airport_code
            ),
            cutoffs AS (
                SELECT
                    *,
                    CASE WHEN scheduled_minute IS NOT NULL AND origin_timezone IS NOT NULL
                         THEN timezone(origin_timezone,
                              CAST(FlightDate AS DATE) + scheduled_minute * INTERVAL 1 MINUTE)
                              - INTERVAL {int(horizon_minutes)} MINUTE
                    END AS origin_cutoff_utc,
                    CASE WHEN scheduled_minute IS NOT NULL AND origin_timezone IS NOT NULL
                         THEN timezone(origin_timezone,
                              CAST(FlightDate AS DATE) + scheduled_minute * INTERVAL 1 MINUTE)
                              - INTERVAL {int(horizon_minutes)} MINUTE
                    END AS destination_cutoff_utc
                FROM scheduled
            ),
            origin_joined AS (
                SELECT
                    f.*,
                    w.available_at_utc AS origin_observation_available_at_utc,
                    w.temperature_c AS origin_temperature_c,
                    w.dew_point_temperature_c AS origin_dew_point_temperature_c,
                    w.wind_speed_m_s AS origin_wind_speed_m_s,
                    w.wind_direction_deg AS origin_wind_direction_deg,
                    w.wind_gust_m_s AS origin_wind_gust_m_s,
                    w.visibility_km AS origin_visibility_km,
                    w.sea_level_pressure_hpa AS origin_sea_level_pressure_hpa,
                    w.weather_rain AS origin_weather_rain,
                    w.weather_snow AS origin_weather_snow,
                    w.weather_fog_or_mist AS origin_weather_fog_or_mist,
                    w.weather_thunderstorm AS origin_weather_thunderstorm
                FROM cutoffs f
                ASOF LEFT JOIN ghcnh_weather w
                  ON f.origin_station_id = w.station_id
                 AND f.origin_cutoff_utc >= w.available_at_utc
            ),
            both_joined AS (
                SELECT
                    f.*,
                    w.available_at_utc AS destination_observation_available_at_utc,
                    w.temperature_c AS destination_temperature_c,
                    w.dew_point_temperature_c AS destination_dew_point_temperature_c,
                    w.wind_speed_m_s AS destination_wind_speed_m_s,
                    w.wind_direction_deg AS destination_wind_direction_deg,
                    w.wind_gust_m_s AS destination_wind_gust_m_s,
                    w.visibility_km AS destination_visibility_km,
                    w.sea_level_pressure_hpa AS destination_sea_level_pressure_hpa,
                    w.weather_rain AS destination_weather_rain,
                    w.weather_snow AS destination_weather_snow,
                    w.weather_fog_or_mist AS destination_weather_fog_or_mist,
                    w.weather_thunderstorm AS destination_weather_thunderstorm
                FROM origin_joined f
                ASOF LEFT JOIN ghcnh_weather w
                  ON f.destination_station_id = w.station_id
                 AND f.destination_cutoff_utc >= w.available_at_utc
            ),
            aged AS (
                SELECT *,
                    date_diff('second', origin_observation_available_at_utc, origin_cutoff_utc) / 60.0 AS origin_weather_age_minutes,
                    date_diff('second', destination_observation_available_at_utc, destination_cutoff_utc) / 60.0 AS destination_weather_age_minutes
                FROM both_joined
            )
            SELECT
                * EXCLUDE (scheduled_minute),
                CASE WHEN origin_observation_available_at_utc IS NULL OR origin_weather_age_minutes > {int(max_weather_age_minutes)} THEN 1 ELSE 0 END::TINYINT AS origin_weather_missing,
                CASE WHEN destination_observation_available_at_utc IS NULL OR destination_weather_age_minutes > {int(max_weather_age_minutes)} THEN 1 ELSE 0 END::TINYINT AS destination_weather_missing,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_temperature_c END AS origin_temperature_c_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_dew_point_temperature_c END AS origin_dew_point_temperature_c_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_wind_speed_m_s END AS origin_wind_speed_m_s_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_wind_direction_deg END AS origin_wind_direction_deg_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_wind_gust_m_s END AS origin_wind_gust_m_s_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_visibility_km END AS origin_visibility_km_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_sea_level_pressure_hpa END AS origin_sea_level_pressure_hpa_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_weather_rain END AS origin_weather_rain_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_weather_snow END AS origin_weather_snow_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_weather_fog_or_mist END AS origin_weather_fog_or_mist_fresh,
                CASE WHEN origin_weather_age_minutes <= {int(max_weather_age_minutes)} THEN origin_weather_thunderstorm END AS origin_weather_thunderstorm_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_temperature_c END AS destination_temperature_c_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_dew_point_temperature_c END AS destination_dew_point_temperature_c_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_wind_speed_m_s END AS destination_wind_speed_m_s_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_wind_direction_deg END AS destination_wind_direction_deg_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_wind_gust_m_s END AS destination_wind_gust_m_s_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_visibility_km END AS destination_visibility_km_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_sea_level_pressure_hpa END AS destination_sea_level_pressure_hpa_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_weather_rain END AS destination_weather_rain_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_weather_snow END AS destination_weather_snow_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_weather_fog_or_mist END AS destination_weather_fog_or_mist_fresh,
                CASE WHEN destination_weather_age_minutes <= {int(max_weather_age_minutes)} THEN destination_weather_thunderstorm END AS destination_weather_thunderstorm_fresh
            FROM aged
        ) TO {output_sql} (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    temp_output.replace(destination)
    stats = connection.execute(
        f"""SELECT count(*) AS rows,
                   avg(CASE WHEN origin_weather_missing = 0 THEN 1.0 ELSE 0.0 END) AS origin_weather_coverage,
                   avg(CASE WHEN destination_weather_missing = 0 THEN 1.0 ELSE 0.0 END) AS destination_weather_coverage,
                   avg(origin_weather_age_minutes) AS origin_mean_age_minutes,
                   avg(destination_weather_age_minutes) AS destination_mean_age_minutes
            FROM read_parquet({sql_string(destination.resolve())})"""
    ).fetchone()
    return {
        "split": split,
        "month": month_label,
        "horizon": horizon_name,
        "horizon_minutes": horizon_minutes,
        "rows": int(stats[0]),
        "origin_weather_coverage": stats[1],
        "destination_weather_coverage": stats[2],
        "origin_mean_report_age_minutes": stats[3],
        "destination_mean_report_age_minutes": stats[4],
        "output": str(destination),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-features-dir", type=Path, default=Path("data/processed/depdel15/historical"))
    parser.add_argument("--weather-dir", type=Path, default=Path("data/raw/ghcnh"))
    parser.add_argument("--station-map", type=Path, default=Path("data/processed/depdel15/weather/station_map.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/depdel15/weather"))
    parser.add_argument("--months", nargs="+", help="Optional month labels such as 2024-01.")
    parser.add_argument("--airport-codes", nargs="+", help="Pilot filter: retain flights touching these airports.")
    parser.add_argument("--horizons", nargs="+", choices=sorted(HORIZONS), default=list(HORIZONS))
    parser.add_argument("--max-weather-age-minutes", type=int, default=180)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--allow-missing-weather", action="store_true", help="Allow pilot runs with incomplete station-year downloads.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_weather_age_minutes <= 0 or args.threads <= 0:
        print("ERROR: max weather age and threads must be positive.", file=sys.stderr)
        return 2
    if not args.station_map.is_file():
        print(f"ERROR: station map not found: {args.station_map}", file=sys.stderr)
        return 2
    try:
        available = expected_flight_files(args.historical_features_dir, args.months)
        station_map = pd.read_csv(args.station_map, dtype={"station_id": "string"})
        if not {"airport_code", "station_id", "timezone"}.issubset(station_map.columns):
            raise ValueError("Station map must include airport_code, station_id, and timezone.")
        stations = sorted(station_map["station_id"].dropna().astype(str).unique())
        years = {year for _, year, _, _ in available}
        if any(month == 1 for _, _, month, _ in available):
            years.add(min(years) - 1)
        weather_files = get_weather_files(args.weather_dir, stations, years, args.allow_missing_weather)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect()
        connection.execute(f"SET threads = {int(args.threads)}")
        connection.execute(
            f"""CREATE TEMP VIEW airport_map AS
                SELECT upper(trim(airport_code)) AS airport_code,
                       CAST(station_id AS VARCHAR) AS station_id,
                       CAST(timezone AS VARCHAR) AS timezone
                FROM read_csv_auto({sql_string(args.station_map.resolve())})
                WHERE station_id IS NOT NULL"""
        )
        make_weather_view(connection, weather_files)
        summary = []
        file_years = {int(path.parent.name) for path in weather_files}
        for year in sorted({item[1] for item in available}):
            year_items = [item for item in available if item[1] == year]
            years_to_load = {year}
            if any(item[2] == 1 for item in year_items):
                years_to_load.add(year - 1)
            year_weather_files = [
                path for path in weather_files if int(path.parent.name) in years_to_load
            ]
            if not year_weather_files:
                raise FileNotFoundError(f"No weather files are available for flights in {year}.")
            try:
                connection.execute("DROP TABLE IF EXISTS ghcnh_weather")
            except duckdb.Error:
                pass
            print(f"Loading NOAA station observations for {year}: {len(year_weather_files):,} Parquet files", flush=True)
            make_weather_view(connection, year_weather_files)
            for split, item_year, month, source in year_items:
                month_label = f"{item_year}-{month:02d}"
                for horizon_name in args.horizons:
                    horizon_root = args.output_dir / horizon_name / "flights" / split
                    output = horizon_root / f"{month_label}.parquet"
                    summary.append(
                        build_month(
                            connection,
                            source,
                            output,
                            split,
                            month_label,
                            horizon_name,
                            HORIZONS[horizon_name],
                            args.max_weather_age_minutes,
                            [code.upper() for code in args.airport_codes] if args.airport_codes else None,
                            args.overwrite,
                        )
                    )
                    print(
                        f"{split} {month_label} {horizon_name}: {summary[-1]['rows']:,} rows; "
                        f"weather coverage origin={summary[-1]['origin_weather_coverage']:.1%}, "
                        f"destination={summary[-1]['destination_weather_coverage']:.1%}",
                        flush=True,
                    )
        connection.close()
        metadata = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "NOAA GHCNh, FM15 METAR observations",
            "observation_availability_buffer_minutes": 15,
            "maximum_weather_age_minutes": args.max_weather_age_minutes,
            "horizons_minutes": {name: HORIZONS[name] for name in args.horizons},
            "development_validation_only": True,
            "test_2025_read_by_this_feature_build": False,
            "weather_features": WEATHER_FIELDS,
            "months": summary,
        }
        metadata_path = args.output_dir / "weather_feature_build.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        print(f"Metadata: {metadata_path}")
    except (OSError, ValueError, duckdb.Error, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
