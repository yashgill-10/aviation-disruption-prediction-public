"""Score scheduled flights with trained horizon models and current METARs.

Input CSVs contain BTS schedule fields. Historical features are recomputed from
local BTS Parquet partitions, and current METARs are retrieved only for airports
present in the input file. The model is not retrained by this script.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
import json
from pathlib import Path
import re
import sys
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import duckdb
import joblib
import numpy as np
import pandas as pd
import requests

from train_baseline import HISTORICAL_FEATURES, encode_frame


METAR_URL = "https://aviationweather.gov/api/data/metar"
METERS_PER_SECOND_PER_KNOT = 0.514444
KILOMETERS_PER_STATUTE_MILE = 1.609344
CURRENT_WEATHER_FIELDS = [
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--station-map", type=Path, default=Path("data/processed/depdel15/weather/station_map.csv"))
    parser.add_argument("--history-dir", type=Path, default=Path("data/processed/depdel15/flights"))
    parser.add_argument(
        "--history-splits",
        nargs="+",
        choices=("train", "validation", "test", "operational_history"),
        default=("train", "operational_history"),
        help="BTS partitions available for prior-history features; validation and test are excluded by default because they are reserved evaluation partitions",
    )
    parser.add_argument("--model-t-minus-2h", type=Path, required=True)
    parser.add_argument("--model-t-minus-30m", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--max-weather-age-minutes", type=int, default=180)
    parser.add_argument("--api-hours", type=int, default=6, help="recent METAR history requested from the API")
    parser.add_argument(
        "--weather-mode",
        choices=("live", "none"),
        default="live",
        help="Fetch current METAR observations, or explicitly score with weather marked missing",
    )
    return parser.parse_args()


def scheduled_departure_utc(
    flight_date: Any,
    crs_dep_time: Any,
    tz_name: Any,
) -> datetime | None:
    """Convert a BTS local schedule to UTC; omit invalid or DST-ambiguous times."""
    if pd.isna(flight_date) or pd.isna(crs_dep_time) or pd.isna(tz_name):
        return None
    try:
        numeric = float(crs_dep_time)
        if not numeric.is_integer() or numeric < 0 or numeric > 2400:
            return None
        hhmm = int(numeric)
        hour, minute = divmod(hhmm, 100)
        if minute >= 60 or hour > 24 or (hour == 24 and minute != 0):
            return None
        local_date = pd.Timestamp(flight_date).date()
        local_naive = datetime.combine(local_date, time.min) + timedelta(minutes=hour * 60 + minute)
        zone = ZoneInfo(str(tz_name))
    except (TypeError, ValueError, OverflowError, ZoneInfoNotFoundError):
        return None

    fold_0 = local_naive.replace(tzinfo=zone, fold=0)
    fold_1 = local_naive.replace(tzinfo=zone, fold=1)
    if fold_0.utcoffset() != fold_1.utcoffset():
        return None
    round_trip = fold_0.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
    if round_trip != local_naive:
        return None
    return fold_0.astimezone(timezone.utc)


def parse_visibility_km(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().upper().replace("SM", "")
    if not text:
        return None
    if text.endswith("+"):
        text = text[:-1]
    if text.startswith("M"):
        # METAR 'M1/4' denotes less than one quarter statute mile.
        miles = 0.125
    else:
        try:
            pieces = text.split()
            if len(pieces) == 2 and "/" in pieces[1]:
                miles = float(pieces[0]) + float(Fraction(pieces[1]))
            elif "/" in text:
                miles = float(Fraction(text))
            else:
                miles = float(text)
        except (ValueError, ZeroDivisionError):
            return None
    if not np.isfinite(miles) or miles < 0:
        return None
    return min(miles, 10.0) * KILOMETERS_PER_STATUTE_MILE


def utc_datetime(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def report_available_at(record: dict[str, Any]) -> datetime | None:
    observed = None
    if record.get("obsTime") is not None:
        try:
            observed = datetime.fromtimestamp(float(record["obsTime"]), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            observed = None
    if observed is None:
        observed = utc_datetime(record.get("reportTime"))
    if observed is None:
        return utc_datetime(record.get("receiptTime"))
    conservative_proxy = observed + timedelta(minutes=15)
    received = utc_datetime(record.get("receiptTime"))
    return max(conservative_proxy, received) if received is not None else conservative_proxy


def fetch_metars(
    station_ids: list[str],
    timeout_seconds: int,
    hours: int,
) -> tuple[dict[str, list[dict[str, Any]]], datetime]:
    reports: dict[str, list[dict[str, Any]]] = {}
    unique_ids = sorted(set(station_ids))
    for index in range(0, len(unique_ids), 40):
        batch = unique_ids[index : index + 40]
        response = requests.get(
            METAR_URL,
            params={"ids": ",".join(batch), "format": "json", "hours": hours},
            timeout=timeout_seconds,
            headers={"User-Agent": "aviation-disruption-prediction/0.1 (research prototype)"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("AviationWeather METAR API returned an unexpected JSON response.")
        for record in payload:
            station = str(record.get("icaoId", "")).upper().strip()
            if station:
                reports.setdefault(station, []).append(record)
        if index + 40 < len(unique_ids):
            time_module.sleep(0.8)

    for station_records in reports.values():
        station_records.sort(
            key=lambda record: report_available_at(record) or datetime.min.replace(tzinfo=timezone.utc)
        )
    return reports, datetime.now(timezone.utc)


def fetch_weather_for_inference(
    station_ids: list[str],
    timeout_seconds: int,
    hours: int,
    mode: str,
) -> tuple[dict[str, list[dict[str, Any]]], datetime, str, str | None]:
    """Fetch live METARs, or preserve model inference with explicit missing weather."""
    if mode == "none":
        return {}, datetime.now(timezone.utc), "omitted", None
    try:
        reports, fetched_at = fetch_metars(station_ids, timeout_seconds, hours)
        return reports, fetched_at, "available" if reports else "no_observations", None
    except (requests.RequestException, ValueError) as exc:
        warning = f"METAR observations unavailable; scored with weather marked missing ({exc})."
        return {}, datetime.now(timezone.utc), "unavailable", warning


def to_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def decode_report(record: dict[str, Any]) -> dict[str, float | int | None]:
    temp = to_float(record.get("temp"))
    dew = to_float(record.get("dewp"))
    speed_knots = to_float(record.get("wspd"))
    gust_knots = to_float(record.get("wgst"))
    direction = to_float(record.get("wdir"))
    pressure = to_float(record.get("slp"))
    if pressure is None:
        pressure = to_float(record.get("altim"))
    weather = str(record.get("wxString") or "").upper()
    return {
        "temperature_c": temp if temp is not None and -90 <= temp <= 60 else None,
        "dew_point_temperature_c": dew if dew is not None and -100 <= dew <= 60 else None,
        "wind_speed_m_s": speed_knots * METERS_PER_SECOND_PER_KNOT if speed_knots is not None and 0 <= speed_knots <= 194 else None,
        "wind_direction_deg": direction if direction is not None and 0 <= direction <= 360 else None,
        "wind_gust_m_s": gust_knots * METERS_PER_SECOND_PER_KNOT if gust_knots is not None and 0 <= gust_knots <= 243 else None,
        "visibility_km": parse_visibility_km(record.get("visib")),
        "sea_level_pressure_hpa": pressure if pressure is not None and 800 <= pressure <= 1100 else None,
        "weather_rain": int(bool(re.search(r"RA|DZ", weather))),
        "weather_snow": int(bool(re.search(r"SN|SG|IC|PL|GR|GS", weather))),
        "weather_fog_or_mist": int(bool(re.search(r"FG|BR|HZ", weather))),
        "weather_thunderstorm": int("TS" in weather),
    }


def select_weather(
    records: list[dict[str, Any]],
    cutoff: datetime | None,
    fetched_at: datetime,
    max_age_minutes: int,
) -> dict[str, Any]:
    selected = None
    # A scheduled cutoff can still be in the future when scoring an upcoming
    # flight. Use only data already available now, and measure staleness at
    # the earlier of that future cutoff and the retrieval time.
    as_of = min(cutoff, fetched_at) if cutoff is not None else None
    if cutoff is not None:
        eligible = [
            record for record in records
            if (available := report_available_at(record)) is not None
            and available <= as_of
        ]
        if eligible:
            selected = eligible[-1]

    result: dict[str, Any] = {"available_at_utc": None, "age_minutes": None, "missing": 1}
    result.update({field: None for field in CURRENT_WEATHER_FIELDS})
    if selected is None:
        return result

    available_at = report_available_at(selected)
    age_minutes = max(0.0, (as_of - available_at).total_seconds() / 60.0)
    result["available_at_utc"] = available_at.isoformat()
    result["age_minutes"] = age_minutes
    if age_minutes > max_age_minutes:
        return result
    result["missing"] = 0
    result.update(decode_report(selected))
    return result


def score_horizon(
    base_frame: pd.DataFrame,
    airport_map: pd.DataFrame,
    reports: dict[str, list[dict[str, Any]]],
    fetched_at: datetime,
    horizon_name: str,
    horizon_minutes: int,
    artifact_path: Path,
    max_age_minutes: int,
) -> pd.DataFrame:
    artifact = joblib.load(artifact_path)
    frame = base_frame.copy()
    origin_meta = airport_map.set_index("airport_code").reindex(frame["Origin"].astype(str).str.upper())
    destination_meta = airport_map.set_index("airport_code").reindex(frame["Dest"].astype(str).str.upper())
    cutoffs: list[datetime | None] = []
    for row_index, (_, row) in enumerate(frame.iterrows()):
        timezone_name = origin_meta.iloc[row_index]["timezone"]
        departure_utc = scheduled_departure_utc(row["FlightDate"], row["CRSDepTime"], timezone_name)
        cutoffs.append(departure_utc - timedelta(minutes=horizon_minutes) if departure_utc else None)

    weather_data: dict[str, list[Any]] = {}
    for endpoint, airport_meta in (("origin", origin_meta), ("destination", destination_meta)):
        endpoint_weather = []
        for row_index, cutoff in enumerate(cutoffs):
            icao = airport_meta.iloc[row_index]["airport_icao"]
            if pd.isna(icao) or not str(icao).strip():
                icao = airport_meta.iloc[row_index]["station_icao"]
            records = reports.get(str(icao).upper(), []) if not pd.isna(icao) else []
            endpoint_weather.append(select_weather(records, cutoff, fetched_at, max_age_minutes))
        for field in ("available_at_utc", "age_minutes", "missing", *CURRENT_WEATHER_FIELDS):
            weather_data[f"{endpoint}_{field}"] = [item[field] for item in endpoint_weather]
        for field in CURRENT_WEATHER_FIELDS:
            frame[f"{endpoint}_{field}_fresh"] = weather_data[f"{endpoint}_{field}"]
        frame[f"{endpoint}_weather_age_minutes"] = weather_data[f"{endpoint}_age_minutes"]
        frame[f"{endpoint}_weather_missing"] = weather_data[f"{endpoint}_missing"]

    # The trained feature encoder requires this target column, but inference
    # must never consume a supplied target value.
    frame["DepDel15"] = 0
    missing_history = [field for field in HISTORICAL_FEATURES if field not in frame.columns]
    if missing_history:
        raise ValueError(
            "Input CSV lacks leakage-safe historical features. Missing: "
            + ", ".join(missing_history)
        )
    features, _ = encode_frame(frame, artifact["encoder"], include_historical=True, include_weather=True)
    decision = artifact["classifier"].decision_function(features).astype(np.float64, copy=False)
    probability = artifact["calibrator"].predict_proba(decision.reshape(-1, 1))[:, 1]
    threshold = float(artifact["f1_threshold_selected_on_2024_first_half"])

    result = base_frame.drop(columns=["DepDel15"], errors="ignore").copy()
    result[f"prediction_cutoff_utc_{horizon_name}"] = [value.isoformat() if value else None for value in cutoffs]
    result[f"predicted_delay_probability_{horizon_name}"] = probability
    result[f"predicted_delay_{horizon_name}"] = (probability >= threshold).astype(np.int8)
    result[f"decision_threshold_{horizon_name}"] = threshold
    for endpoint in ("origin", "destination"):
        result[f"{endpoint}_weather_available_at_utc_{horizon_name}"] = weather_data[f"{endpoint}_available_at_utc"]
        result[f"{endpoint}_weather_age_minutes_{horizon_name}"] = weather_data[f"{endpoint}_age_minutes"]
        result[f"{endpoint}_weather_missing_{horizon_name}"] = weather_data[f"{endpoint}_missing"]
        for field in CURRENT_WEATHER_FIELDS:
            result[f"{endpoint}_{field}_{horizon_name}"] = weather_data[f"{endpoint}_{field}"]
    return result


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def add_current_history_features(
    flights: pd.DataFrame,
    history_dir: Path,
    history_splits: tuple[str, ...] | list[str] = ("train", "operational_history"),
) -> tuple[pd.DataFrame, str]:
    """Recompute prior-only historical rates from the latest local BTS partitions."""
    history_dir = Path(history_dir)
    source_files = []
    for split in history_splits:
        split_dir = history_dir / split
        if split_dir.is_dir():
            source_files.extend(
                path for path in split_dir.glob("*.parquet")
                if re.fullmatch(r"20\d{2}-\d{2}", path.stem)
            )
    source_files = sorted(source_files)
    if not source_files:
        raise FileNotFoundError(f"No processed BTS month Parquet files were found under {history_dir}.")

    dep_time = pd.to_numeric(flights["CRSDepTime"], errors="coerce")
    valid_time = dep_time.notna() & dep_time.mod(1).eq(0) & dep_time.between(0, 2400)
    valid_time &= dep_time.mod(100).lt(60)
    dep_hour = np.where(valid_time, (np.floor(dep_time.fillna(0) / 100).astype(int) % 24), -1)
    current = pd.DataFrame(
        {
            "_row_id": np.arange(len(flights), dtype=np.int64),
            "flight_date": pd.to_datetime(flights["FlightDate"], errors="coerce").dt.date,
            "airline": flights["Reporting_Airline"].fillna("UNKNOWN").astype(str).str.strip().replace("", "UNKNOWN"),
            "origin": flights["Origin"].fillna("UNKNOWN").astype(str).str.strip().str.upper().replace("", "UNKNOWN"),
            "destination": flights["Dest"].fillna("UNKNOWN").astype(str).str.strip().str.upper().replace("", "UNKNOWN"),
            "dep_hour": dep_hour.astype(np.int16),
        }
    )
    current["dep_hour_key"] = current["dep_hour"].astype(str)
    c = duckdb.connect()
    try:
        c.register("current_input", current)
        path_list = ", ".join(sql_string(path.resolve()) for path in source_files)
        c.execute(
            f"""CREATE TEMP VIEW history_source AS
                SELECT CAST(FlightDate AS DATE) AS flight_date,
                       CAST(date_trunc('month', CAST(FlightDate AS DATE)) AS DATE) AS flight_month,
                       COALESCE(NULLIF(TRIM(CAST(Reporting_Airline AS VARCHAR)), ''), 'UNKNOWN') AS airline,
                       COALESCE(NULLIF(UPPER(TRIM(CAST(Origin AS VARCHAR))), ''), 'UNKNOWN') AS origin,
                       COALESCE(NULLIF(UPPER(TRIM(CAST(Dest AS VARCHAR))), ''), 'UNKNOWN') AS destination,
                       CASE WHEN TRY_CAST(CRSDepTime AS INTEGER) BETWEEN 0 AND 2400
                                  AND TRY_CAST(CRSDepTime AS INTEGER) % 100 < 60
                            THEN CAST(FLOOR(TRY_CAST(CRSDepTime AS DOUBLE) / 100) AS INTEGER) % 24
                            ELSE -1 END AS dep_hour,
                       CAST(DepDel15 AS INTEGER) AS delayed
                FROM read_parquet([{path_list}], union_by_name = true)
                WHERE TRY_CAST(DepDel15 AS INTEGER) IN (0, 1)"""
        )
        c.execute(
            """CREATE TEMP TABLE daily_groups AS
               WITH grouped AS (
                   SELECT flight_date, airline, origin, destination, dep_hour,
                          grouping_id(airline, origin, destination, dep_hour) AS grouping_code,
                          COUNT(*)::BIGINT AS flights, SUM(delayed)::BIGINT AS delayed_flights
                   FROM history_source
                   GROUP BY GROUPING SETS (
                       (flight_date, airline), (flight_date, origin),
                       (flight_date, origin, destination), (flight_date, origin, dep_hour),
                       (flight_date)
                   )
               )
               SELECT flight_date,
                      CASE grouping_code WHEN 7 THEN 'airline' WHEN 11 THEN 'origin'
                           WHEN 9 THEN 'route' WHEN 10 THEN 'origin_hour' WHEN 15 THEN 'global'
                           ELSE error('Unexpected current-history grouping code') END AS group_type,
                      CASE grouping_code WHEN 7 THEN airline WHEN 11 THEN origin WHEN 9 THEN origin
                           WHEN 10 THEN origin ELSE '__GLOBAL__' END AS key1,
                      CASE grouping_code WHEN 9 THEN destination WHEN 10 THEN CAST(dep_hour AS VARCHAR)
                           ELSE '' END AS key2,
                      flights, delayed_flights
               FROM grouped"""
        )
        c.execute(
            """CREATE TEMP TABLE monthly_groups AS
               WITH grouped AS (
                   SELECT flight_month, airline, origin, destination,
                          grouping_id(airline, origin, destination) AS grouping_code,
                          COUNT(*)::BIGINT AS flights, SUM(delayed)::BIGINT AS delayed_flights
                   FROM history_source
                   GROUP BY GROUPING SETS (
                       (flight_month, airline), (flight_month, origin),
                       (flight_month, origin, destination), (flight_month)
                   )
               )
               SELECT flight_month,
                      CASE grouping_code WHEN 3 THEN 'airline' WHEN 5 THEN 'origin'
                           WHEN 4 THEN 'route' WHEN 7 THEN 'global'
                           ELSE error('Unexpected current-month grouping code') END AS group_type,
                      CASE grouping_code WHEN 3 THEN airline WHEN 5 THEN origin WHEN 4 THEN origin
                           ELSE '__GLOBAL__' END AS key1,
                      CASE grouping_code WHEN 4 THEN destination ELSE '' END AS key2,
                      flights, delayed_flights
               FROM grouped"""
        )

        daily_rollups = []
        for window in (7, 30):
            for group in ("global", "airline", "origin", "route", "origin_hour"):
                prefix = f"{group}_{window}d"
                daily_rollups.extend(
                    [
                        f"SUM(d.flights) FILTER (WHERE d.group_type = '{group}' AND d.flight_date >= f.flight_date - INTERVAL {window} DAY) AS {prefix}_flights",
                        f"SUM(d.delayed_flights) FILTER (WHERE d.group_type = '{group}' AND d.flight_date >= f.flight_date - INTERVAL {window} DAY) AS {prefix}_delayed",
                    ]
                )
        daily_join = """LEFT JOIN daily_groups d
              ON d.flight_date >= f.flight_date - INTERVAL 30 DAY
             AND d.flight_date < f.flight_date
             AND (d.group_type = 'global'
               OR (d.group_type = 'airline' AND d.key1 = f.airline)
               OR (d.group_type = 'origin' AND d.key1 = f.origin)
               OR (d.group_type = 'route' AND d.key1 = f.origin AND d.key2 = f.destination)
               OR (d.group_type = 'origin_hour' AND d.key1 = f.origin AND d.key2 = f.dep_hour_key))"""

        monthly_rollups = []
        for group in ("global", "airline", "origin", "route"):
            monthly_rollups.extend(
                [
                    f"SUM(m.flights) FILTER (WHERE m.group_type = '{group}') AS {group}_3m_flights",
                    f"SUM(m.delayed_flights) FILTER (WHERE m.group_type = '{group}') AS {group}_3m_delayed",
                ]
            )
        monthly_join = """LEFT JOIN monthly_groups m
              ON m.flight_month >= f.date_month - INTERVAL 3 MONTH
             AND m.flight_month < f.date_month
             AND (m.group_type = 'global'
               OR (m.group_type = 'airline' AND m.key1 = f.airline)
               OR (m.group_type = 'origin' AND m.key1 = f.origin)
               OR (m.group_type = 'route' AND m.key1 = f.origin AND m.key2 = f.destination))"""

        feature_expressions = []
        for window in (7, 30):
            global_flights = f"global_{window}d_flights"
            global_delayed = f"global_{window}d_delayed"
            for group in ("global", "airline", "origin", "route", "origin_hour"):
                flights_name = f"{group}_{window}d_flights"
                delayed_name = f"{group}_{window}d_delayed"
                if group == "global":
                    rate = f"CASE WHEN {global_flights} > 0 THEN {global_delayed}::DOUBLE / {global_flights} END"
                else:
                    rate = (
                        f"CASE WHEN {global_flights} > 0 THEN "
                        f"(COALESCE({delayed_name}, 0) + 50.0 * {global_delayed} / {global_flights}) "
                        f"/ (COALESCE({flights_name}, 0) + 50.0) END"
                    )
                feature_expressions.extend(
                    [
                        f"{rate} AS prev{window}d_{group}_delay_rate",
                        f"LN(1 + COALESCE({flights_name}, 0)) AS prev{window}d_{group}_log_flights",
                    ]
                )
        feature_expressions.extend(
            [
                "CASE WHEN global_7d_flights > 0 THEN 0 ELSE 1 END::TINYINT AS prev7d_history_missing",
                "CASE WHEN global_30d_flights > 0 THEN 0 ELSE 1 END::TINYINT AS prev30d_history_missing",
            ]
        )
        monthly_features = []
        global_flights = "global_3m_flights"
        global_delayed = "global_3m_delayed"
        for group in ("global", "airline", "origin", "route"):
            flights_name = f"{group}_3m_flights"
            delayed_name = f"{group}_3m_delayed"
            rate = (
                f"CASE WHEN {global_flights} > 0 THEN {global_delayed}::DOUBLE / {global_flights} END"
                if group == "global"
                else f"CASE WHEN {global_flights} > 0 THEN (COALESCE({delayed_name}, 0) + 50.0 * {global_delayed} / {global_flights}) / (COALESCE({flights_name}, 0) + 50.0) END"
            )
            monthly_features.extend(
                [
                    f"{rate} AS prev3m_{group}_delay_rate",
                    f"LN(1 + COALESCE({flights_name}, 0)) AS prev3m_{group}_log_flights",
                ]
            )
        monthly_features.append(
            "CASE WHEN global_3m_flights > 0 THEN 0 ELSE 1 END::TINYINT AS prev3m_history_missing"
        )

        query = f"""
            WITH current_flights AS (
                SELECT *, CAST(date_trunc('month', flight_date) AS DATE) AS date_month
                FROM current_input
            ),
            current_daily AS (
                SELECT f._row_id, {', '.join(daily_rollups)}
                FROM current_flights f
                {daily_join}
                GROUP BY f._row_id
            ),
            current_monthly AS (
                SELECT f._row_id, {', '.join(monthly_rollups)}
                FROM current_flights f
                {monthly_join}
                GROUP BY f._row_id
            ),
            current_stats AS (
                SELECT d.*, m.* EXCLUDE (_row_id)
                FROM current_daily d JOIN current_monthly m USING (_row_id)
            )
            SELECT current_stats.*,
                   (SELECT MAX(flight_date) FROM history_source) AS history_source_max_date,
                   {', '.join(monthly_features)},
                   {', '.join(feature_expressions)}
            FROM current_stats
            ORDER BY _row_id
        """
        features = c.execute(query).df()
        max_date = str(features["history_source_max_date"].dropna().max())
    finally:
        c.close()

    result = flights.reset_index(drop=True).copy()
    for column in features.columns:
        if column == "_row_id":
            continue
        result[column] = features[column].to_numpy()
    result["history_age_days"] = (
        pd.to_datetime(result["FlightDate"]).dt.normalize()
        - pd.to_datetime(result["history_source_max_date"])
    ).dt.days
    return result, max_date


def main() -> int:
    args = parse_args()
    if args.timeout_seconds < 1 or args.max_weather_age_minutes < 1 or args.api_hours < 1:
        print("ERROR: timeout, report age, and API lookback must be positive.", file=sys.stderr)
        return 2
    for path in (args.input_csv, args.station_map, args.model_t_minus_2h, args.model_t_minus_30m):
        if not path.is_file():
            print(f"ERROR: required file not found: {path}", file=sys.stderr)
            return 2
    if not args.history_dir.is_dir():
        print(f"ERROR: historical BTS data directory not found: {args.history_dir}", file=sys.stderr)
        return 2
    summary_path = args.output_csv.with_suffix(".summary.json")
    existing_outputs = [path for path in (args.output_csv, summary_path) if path.exists()]
    if existing_outputs:
        print("ERROR: refusing to overwrite existing inference output(s): " + ", ".join(map(str, existing_outputs)), file=sys.stderr)
        return 2
    try:
        flights = pd.read_csv(args.input_csv)
        required = {
            "FlightDate", "Reporting_Airline", "Origin", "Dest", "CRSDepTime",
            "CRSArrTime", "Distance", "CRSElapsedTime",
        }
        missing = sorted(required - set(flights.columns))
        if missing:
            raise ValueError("Input CSV is missing required schedule/history columns: " + ", ".join(missing))
        if flights.empty:
            raise ValueError("Input CSV contains no scheduled flights.")
        flights["Origin"] = flights["Origin"].astype("string").str.strip().str.upper()
        flights["Dest"] = flights["Dest"].astype("string").str.strip().str.upper()
        flights["FlightDate"] = pd.to_datetime(flights["FlightDate"], errors="coerce")
        if flights["FlightDate"].isna().any():
            raise ValueError("Input CSV contains invalid FlightDate values.")
        flights, history_max_date = add_current_history_features(
            flights, args.history_dir, args.history_splits
        )

        airport_map = pd.read_csv(args.station_map, dtype={"airport_code": "string"})
        required_map = {"airport_code", "airport_icao", "station_icao", "timezone"}
        if not required_map.issubset(airport_map.columns):
            raise ValueError("Station map lacks airport ICAO, station ICAO, or timezone fields.")
        airport_map["airport_code"] = airport_map["airport_code"].astype(str).str.strip().str.upper()
        airport_by_code = airport_map.set_index("airport_code")
        needed_codes = set(flights["Origin"].dropna()) | set(flights["Dest"].dropna())
        missing_airports = sorted(needed_codes - set(airport_by_code.index))
        if missing_airports:
            print(
                "WARNING: airport codes absent from the weather map will receive missing weather: "
                + ", ".join(missing_airports[:20]),
                file=sys.stderr,
            )
        icao_ids = []
        for code in sorted(needed_codes & set(airport_by_code.index)):
            record = airport_by_code.loc[code]
            icao = record["airport_icao"]
            if pd.isna(icao) or not str(icao).strip():
                icao = record["station_icao"]
            if not pd.isna(icao) and str(icao).strip():
                icao_ids.append(str(icao).strip().upper())
        reports, fetched_at, weather_status, weather_warning = fetch_weather_for_inference(
            icao_ids, args.timeout_seconds, args.api_hours, args.weather_mode
        )

        outputs = []
        for horizon_name, horizon_minutes, model_path in (
            ("t_minus_2h", 120, args.model_t_minus_2h),
            ("t_minus_30m", 30, args.model_t_minus_30m),
        ):
            outputs.append(
                score_horizon(
                    flights,
                    airport_map,
                    reports,
                    fetched_at,
                    horizon_name,
                    horizon_minutes,
                    model_path,
                    args.max_weather_age_minutes,
                )
            )
        result = pd.concat(outputs, axis=1)
        result = result.loc[:, ~result.columns.duplicated()]
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.output_csv, index=False)
        summary = {
            "generated_at_utc": fetched_at.isoformat(),
            "input_flights": int(len(flights)),
            "weather_stations_requested": len(set(icao_ids)),
            "weather_stations_returned": len(reports),
            "output_csv": str(args.output_csv),
            "horizons": ["t_minus_2h", "t_minus_30m"],
            "weather_mode": args.weather_mode,
            "weather_source": "AviationWeather.gov decoded METAR API" if args.weather_mode == "live" else "Weather explicitly omitted for offline demonstration",
            "weather_status": weather_status,
            "weather_warning": weather_warning,
            "history_source_directory": str(args.history_dir),
            "history_splits_read": list(args.history_splits),
            "history_archive_latest_date": history_max_date,
            "history_age_days_max": int(pd.to_numeric(flights["history_age_days"], errors="coerce").max()),
            "history_features_recomputed_from_prior_flights": True,
            "prediction_cutoffs_follow_origin_airport_local_time": True,
            "dst_ambiguous_or_nonexistent_cutoffs_mark_weather_missing": True,
        }
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Scored {len(flights):,} flight(s); output: {args.output_csv}")
        print(f"Run summary: {summary_path}")
    except (OSError, ValueError, requests.RequestException, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
