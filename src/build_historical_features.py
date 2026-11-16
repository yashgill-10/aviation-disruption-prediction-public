"""Build leakage-safe monthly, 7-day, 30-day, and time-of-day delay features.

Monthly features use only the three fully completed calendar months before a
flight's month.  Daily features use only completed calendar dates before the
flight date, with 7-day and 30-day windows.  The script reads 2022-2024 train
and validation partitions explicitly and never opens the 2025 test directory.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

TRAIN_YEARS = (2022, 2023)
VALIDATION_YEARS = (2024,)
BASE_COLUMNS = [
    "FlightDate",
    "Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "CRSArrTime",
    "Distance",
    "CRSElapsedTime",
    "DepDel15",
]
HISTORICAL_COLUMNS = [
    "prev3m_global_delay_rate",
    "prev3m_global_log_flights",
    "prev3m_airline_delay_rate",
    "prev3m_airline_log_flights",
    "prev3m_origin_delay_rate",
    "prev3m_origin_log_flights",
    "prev3m_route_delay_rate",
    "prev3m_route_log_flights",
    "prev3m_history_missing",
]
DAILY_HISTORICAL_COLUMNS = [
    f"prev{window}d_{group}_{suffix}"
    for window in (7, 30)
    for group in ("global", "airline", "origin", "route", "origin_hour")
    for suffix in ("delay_rate", "log_flights")
] + ["prev7d_history_missing", "prev30d_history_missing"]
SMOOTHING_FLIGHTS = 50.0
PROVENANCE_COLUMNS = [
    "flight_id", "identity_version", "identity_status", "source_archive", "source_archive_sha256",
    "source_csv_member", "source_row_ordinal",
]


def expected_month_files(root: Path, split: str, years: tuple[int, ...]) -> list[Path]:
    split_dir = root / "flights" / split
    expected = [
        split_dir / f"{year}-{month:02d}.parquet"
        for year in years
        for month in range(1, 13)
    ]
    missing = [path.name for path in expected if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"{split} split is incomplete; missing {len(missing)} month(s): "
            f"{', '.join(missing)}"
        )
    return expected


def select_target_month_files(target_dir: Path, month_labels: list[str] | None) -> list[Path]:
    if not month_labels:
        return expected_month_files(target_dir, "validation", VALIDATION_YEARS)
    if len(set(month_labels)) != len(month_labels):
        raise ValueError("Target month list contains duplicates.")
    paths = []
    for label in sorted(month_labels):
        parts = label.split("-")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(f"Invalid target month label: {label}")
        year, month = int(parts[0]), int(parts[1])
        if year not in VALIDATION_YEARS or not 1 <= month <= 12:
            raise ValueError(f"Target month must be a 2024 validation month: {label}")
        paths.append(target_dir / "flights" / "validation" / f"{label}.parquet")
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Target input month is missing: {missing[0]}")
    return paths


def sql_string(value: str | Path) -> str:
    """Return a safely quoted DuckDB SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def ensure_output_is_available(output_dir: Path) -> None:
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise FileExistsError(
            f"Output directory already exists and is not empty: {output_dir}. "
            "Choose a new output directory; existing data is never overwritten."
        )


def make_group_history(connection: duckdb.DuckDBPyConnection, source_files: list[Path]) -> None:
    file_literals = ", ".join(sql_string(path) for path in source_files)
    connection.execute(
        f"""
        CREATE TEMP VIEW historical_source AS
        SELECT
            CAST(FlightDate AS DATE) AS flight_date,
            CAST(date_trunc('month', CAST(FlightDate AS DATE)) AS DATE) AS flight_month,
            COALESCE(NULLIF(TRIM(CAST(Reporting_Airline AS VARCHAR)), ''), 'UNKNOWN') AS airline,
            COALESCE(NULLIF(TRIM(CAST(Origin AS VARCHAR)), ''), 'UNKNOWN') AS origin,
            COALESCE(NULLIF(TRIM(CAST(Dest AS VARCHAR)), ''), 'UNKNOWN') AS destination,
            CASE
                WHEN TRY_CAST(CRSDepTime AS INTEGER) BETWEEN 0 AND 2400
                 AND TRY_CAST(CRSDepTime AS INTEGER) % 100 < 60
                THEN CAST(FLOOR(TRY_CAST(CRSDepTime AS DOUBLE) / 100) AS INTEGER) % 24
                ELSE -1
            END AS dep_hour,
            CAST(DepDel15 AS INTEGER) AS delayed
        FROM read_parquet([{file_literals}], union_by_name = true)
        """
    )
    connection.execute(
        """
        CREATE TEMP TABLE monthly_groups AS
        WITH grouped AS (
            SELECT
                flight_month,
                airline,
                origin,
                destination,
                grouping_id(airline, origin, destination) AS grouping_code,
                COUNT(*)::BIGINT AS flights,
                SUM(delayed)::BIGINT AS delayed_flights
            FROM historical_source
            GROUP BY GROUPING SETS (
                (flight_month, airline),
                (flight_month, origin),
                (flight_month, origin, destination),
                (flight_month)
            )
        )
        SELECT
            flight_month,
            CASE grouping_code
                WHEN 3 THEN 'airline'
                WHEN 5 THEN 'origin'
                WHEN 4 THEN 'route'
                WHEN 7 THEN 'global'
                ELSE error('Unexpected grouping code in historical aggregation')
            END AS group_type,
            CASE grouping_code
                WHEN 3 THEN airline
                WHEN 5 THEN origin
                WHEN 4 THEN origin
                ELSE '__GLOBAL__'
            END AS key1,
            CASE grouping_code WHEN 4 THEN destination ELSE '' END AS key2,
            flights,
            delayed_flights
        FROM grouped
        """
    )
    connection.execute(
        """
        CREATE TEMP TABLE history_snapshots AS
        SELECT
            target.flight_month,
            groups.group_type,
            groups.key1,
            groups.key2,
            SUM(groups.flights)::BIGINT AS flights,
            SUM(groups.delayed_flights)::BIGINT AS delayed_flights
        FROM (SELECT DISTINCT flight_month FROM historical_source) AS target
        JOIN monthly_groups AS groups
          ON groups.flight_month >= target.flight_month - INTERVAL 3 MONTH
         AND groups.flight_month < target.flight_month
        GROUP BY target.flight_month, groups.group_type, groups.key1, groups.key2
        """
    )
    connection.execute(
        """
        CREATE TEMP TABLE daily_groups AS
        WITH grouped AS (
            SELECT
                flight_date,
                airline,
                origin,
                destination,
                dep_hour,
                grouping_id(airline, origin, destination, dep_hour) AS grouping_code,
                COUNT(*)::BIGINT AS flights,
                SUM(delayed)::BIGINT AS delayed_flights
            FROM historical_source
            GROUP BY GROUPING SETS (
                (flight_date, airline),
                (flight_date, origin),
                (flight_date, origin, destination),
                (flight_date, origin, dep_hour),
                (flight_date)
            )
        )
        SELECT
            flight_date,
            CASE grouping_code
                WHEN 7 THEN 'airline'
                WHEN 11 THEN 'origin'
                WHEN 9 THEN 'route'
                WHEN 10 THEN 'origin_hour'
                WHEN 15 THEN 'global'
                ELSE error('Unexpected grouping code in daily historical aggregation')
            END AS group_type,
            CASE grouping_code
                WHEN 7 THEN airline
                WHEN 11 THEN origin
                WHEN 9 THEN origin
                WHEN 10 THEN origin
                ELSE '__GLOBAL__'
            END AS key1,
            CASE grouping_code
                WHEN 9 THEN destination
                WHEN 10 THEN CAST(dep_hour AS VARCHAR)
                ELSE ''
            END AS key2,
            flights,
            delayed_flights
        FROM grouped
        """
    )
    connection.execute(
        """
        CREATE TEMP TABLE daily_snapshots AS
        SELECT
            flight_date,
            group_type,
            key1,
            key2,
            flights,
            delayed_flights,
            SUM(flights) OVER (
                PARTITION BY group_type, key1, key2 ORDER BY flight_date
                RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING
            )::BIGINT AS flights_7d,
            SUM(delayed_flights) OVER (
                PARTITION BY group_type, key1, key2 ORDER BY flight_date
                RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING
            )::BIGINT AS delayed_7d,
            SUM(flights) OVER (
                PARTITION BY group_type, key1, key2 ORDER BY flight_date
                RANGE BETWEEN INTERVAL 30 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING
            )::BIGINT AS flights_30d,
            SUM(delayed_flights) OVER (
                PARTITION BY group_type, key1, key2 ORDER BY flight_date
                RANGE BETWEEN INTERVAL 30 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING
            )::BIGINT AS delayed_30d
        FROM daily_groups
        """
    )


def build_one_month(
    connection: duckdb.DuckDBPyConnection,
    source_path: Path,
    destination: Path,
    month_label: str,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    month_date = f"{month_label}-01"
    source = sql_string(source_path)
    output = sql_string(destination)
    source_columns = {
        str(row[0])
        for row in connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet({source}, union_by_name = true)"
        ).fetchall()
    }
    provenance_present = source_columns.intersection(PROVENANCE_COLUMNS)
    if provenance_present and provenance_present != set(PROVENANCE_COLUMNS):
        missing = sorted(set(PROVENANCE_COLUMNS) - provenance_present)
        raise ValueError(f"Source has incomplete flight provenance columns: {', '.join(missing)}")
    provenance_projection = ""
    if provenance_present:
        provenance_projection = ",\n                    " + ",\n                    ".join(
            f"f.{column}" for column in PROVENANCE_COLUMNS
        )
    daily_groups = ("global", "airline", "origin", "route", "origin_hour")
    daily_join_parts = []
    daily_stat_columns = []
    daily_excluded_columns = []
    daily_feature_expressions = []
    for group in daily_groups:
        alias = f"daily_{group}"
        conditions = [
            f"{alias}.flight_date = CAST(f.FlightDate AS DATE)",
            f"{alias}.group_type = '{group}'",
        ]
        if group == "airline":
            conditions.append(
                f"{alias}.key1 = COALESCE(NULLIF(TRIM(CAST(f.Reporting_Airline AS VARCHAR)), ''), 'UNKNOWN')"
            )
        elif group in ("origin", "route", "origin_hour"):
            conditions.append(
                f"{alias}.key1 = COALESCE(NULLIF(TRIM(CAST(f.Origin AS VARCHAR)), ''), 'UNKNOWN')"
            )
            if group == "route":
                conditions.append(
                    f"{alias}.key2 = COALESCE(NULLIF(TRIM(CAST(f.Dest AS VARCHAR)), ''), 'UNKNOWN')"
                )
            elif group == "origin_hour":
                conditions.append(
                    f"{alias}.key2 = CASE WHEN TRY_CAST(f.CRSDepTime AS INTEGER) BETWEEN 0 AND 2400 "
                    f"AND TRY_CAST(f.CRSDepTime AS INTEGER) % 100 < 60 "
                    f"THEN CAST(CAST(FLOOR(TRY_CAST(f.CRSDepTime AS DOUBLE) / 100) AS INTEGER) % 24 AS VARCHAR) "
                    "ELSE '-1' END"
                )
        daily_join_parts.append(
            f"LEFT JOIN daily_snapshots AS {alias} ON " + " AND ".join(conditions)
        )
        for window in (7, 30):
            flights_name = f"{group}_{window}d_flights"
            delayed_name = f"{group}_{window}d_delayed"
            daily_stat_columns.extend(
                [
                    f"{alias}.flights_{window}d AS {flights_name}",
                    f"{alias}.delayed_{window}d AS {delayed_name}",
                ]
            )
            daily_excluded_columns.extend([flights_name, delayed_name])
            global_flights = f"global_{window}d_flights"
            global_delayed = f"global_{window}d_delayed"
            if group == "global":
                rate_expression = (
                    f"CASE WHEN {global_flights} > 0 THEN "
                    f"{global_delayed}::DOUBLE / {global_flights} ELSE NULL END"
                )
            else:
                rate_expression = (
                    f"CASE WHEN {global_flights} > 0 THEN "
                    f"(COALESCE({delayed_name}, 0) + {SMOOTHING_FLIGHTS} * "
                    f"{global_delayed} / {global_flights}) "
                    f"/ (COALESCE({flights_name}, 0) + {SMOOTHING_FLIGHTS}) ELSE NULL END"
                )
            daily_feature_expressions.extend(
                [
                    f"{rate_expression} AS prev{window}d_{group}_delay_rate",
                    f"LN(1 + COALESCE({flights_name}, 0)) AS prev{window}d_{group}_log_flights",
                ]
            )
    daily_feature_expressions.extend(
        [
            "CASE WHEN global_7d_flights > 0 THEN 0 ELSE 1 END::TINYINT AS prev7d_history_missing",
            "CASE WHEN global_30d_flights > 0 THEN 0 ELSE 1 END::TINYINT AS prev30d_history_missing",
        ]
    )
    daily_columns_sql = ",\n                    ".join(daily_stat_columns)
    daily_joins_sql = "\n                ".join(daily_join_parts)
    daily_exclude_sql = ", " + ", ".join(daily_excluded_columns)
    daily_features_sql = ",\n                ".join(daily_feature_expressions)
    connection.execute(
        f"""
        COPY (
            WITH flights AS (
                SELECT *, CAST(date_trunc('month', CAST(FlightDate AS DATE)) AS DATE) AS flight_month
                FROM read_parquet({source}, union_by_name = true)
                WHERE CAST(date_trunc('month', CAST(FlightDate AS DATE)) AS DATE) = DATE {sql_string(month_date)}
            ),
            joined AS (
                SELECT
                    f.FlightDate,
                    f.Reporting_Airline,
                    f.Origin,
                    f.Dest,
                    f.CRSDepTime,
                    f.CRSArrTime,
                    f.Distance,
                    f.CRSElapsedTime,
                    f.DepDel15{provenance_projection},
                    global_hist.flights AS global_flights,
                    global_hist.delayed_flights AS global_delayed,
                    airline_hist.flights AS airline_flights,
                    airline_hist.delayed_flights AS airline_delayed,
                    origin_hist.flights AS origin_flights,
                    origin_hist.delayed_flights AS origin_delayed,
                    route_hist.flights AS route_flights,
                    route_hist.delayed_flights AS route_delayed,
                    {daily_columns_sql}
                FROM flights AS f
                LEFT JOIN history_snapshots AS global_hist
                  ON global_hist.flight_month = f.flight_month
                 AND global_hist.group_type = 'global'
                LEFT JOIN history_snapshots AS airline_hist
                  ON airline_hist.flight_month = f.flight_month
                 AND airline_hist.group_type = 'airline'
                 AND airline_hist.key1 = COALESCE(NULLIF(TRIM(CAST(f.Reporting_Airline AS VARCHAR)), ''), 'UNKNOWN')
                LEFT JOIN history_snapshots AS origin_hist
                  ON origin_hist.flight_month = f.flight_month
                 AND origin_hist.group_type = 'origin'
                 AND origin_hist.key1 = COALESCE(NULLIF(TRIM(CAST(f.Origin AS VARCHAR)), ''), 'UNKNOWN')
                LEFT JOIN history_snapshots AS route_hist
                  ON route_hist.flight_month = f.flight_month
                 AND route_hist.group_type = 'route'
                 AND route_hist.key1 = COALESCE(NULLIF(TRIM(CAST(f.Origin AS VARCHAR)), ''), 'UNKNOWN')
                 AND route_hist.key2 = COALESCE(NULLIF(TRIM(CAST(f.Dest AS VARCHAR)), ''), 'UNKNOWN')
                {daily_joins_sql}
            )
            SELECT
                * EXCLUDE (global_flights, global_delayed, airline_flights, airline_delayed,
                           origin_flights, origin_delayed, route_flights, route_delayed{daily_exclude_sql}),
                CASE WHEN global_flights > 0
                     THEN global_delayed::DOUBLE / global_flights ELSE NULL END
                    AS prev3m_global_delay_rate,
                LN(1 + COALESCE(global_flights, 0)) AS prev3m_global_log_flights,
                CASE WHEN global_flights > 0
                     THEN (COALESCE(airline_delayed, 0) + {SMOOTHING_FLIGHTS} * global_delayed / global_flights)
                          / (COALESCE(airline_flights, 0) + {SMOOTHING_FLIGHTS})
                     ELSE NULL END AS prev3m_airline_delay_rate,
                LN(1 + COALESCE(airline_flights, 0)) AS prev3m_airline_log_flights,
                CASE WHEN global_flights > 0
                     THEN (COALESCE(origin_delayed, 0) + {SMOOTHING_FLIGHTS} * global_delayed / global_flights)
                          / (COALESCE(origin_flights, 0) + {SMOOTHING_FLIGHTS})
                     ELSE NULL END AS prev3m_origin_delay_rate,
                LN(1 + COALESCE(origin_flights, 0)) AS prev3m_origin_log_flights,
                CASE WHEN global_flights > 0
                     THEN (COALESCE(route_delayed, 0) + {SMOOTHING_FLIGHTS} * global_delayed / global_flights)
                          / (COALESCE(route_flights, 0) + {SMOOTHING_FLIGHTS})
                     ELSE NULL END AS prev3m_route_delay_rate,
                LN(1 + COALESCE(route_flights, 0)) AS prev3m_route_log_flights,
                CASE WHEN global_flights IS NULL THEN 1 ELSE 0 END::TINYINT
                    AS prev3m_history_missing,
                {daily_features_sql}
            FROM joined
        ) TO {output} (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    count = connection.execute(
        f"SELECT COUNT(*) FROM read_parquet({output})"
    ).fetchone()[0]
    return int(count)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/processed/depdel15")
    parser.add_argument("--output-dir", default="data/processed/depdel15/historical")
    parser.add_argument("--target-input-dir", type=Path,
                        help="Optional separate validation input containing identity-preserving target rows.")
    parser.add_argument("--target-months", nargs="+",
                        help="Build only selected 2024 validation feature months; requires those month files.")
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.threads < 1:
        print("ERROR: --threads must be positive.", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir).resolve()
    target_data_dir = Path(args.target_input_dir).resolve() if args.target_input_dir else data_dir
    output_dir = Path(args.output_dir).resolve()
    target_only = args.target_input_dir is not None or args.target_months is not None
    try:
        train_files = expected_month_files(data_dir, "train", TRAIN_YEARS)
        validation_files = expected_month_files(data_dir, "validation", VALIDATION_YEARS)
        target_files = (
            select_target_month_files(target_data_dir, args.target_months)
            if target_only
            else validation_files
        )
        ensure_output_is_available(output_dir)
    except (FileNotFoundError, FileExistsError, NotADirectoryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.building-", dir=output_dir.parent
    ) as staging_name:
        staging_dir = Path(staging_name)
        connection = duckdb.connect()
        try:
            connection.execute(f"SET threads = {int(args.threads)}")
            temp_dir = staging_dir / "duckdb_temp"
            temp_dir.mkdir()
            connection.execute(f"SET temp_directory = {sql_string(temp_dir)}")
            source_files = train_files + validation_files
            print("Building leakage-safe monthly and daily group statistics from 2022-2024 only...", flush=True)
            make_group_history(connection, source_files)

            row_counts: dict[str, int] = {}
            split_files = (
                [("validation", target_files)]
                if target_only
                else [("train", train_files), ("validation", validation_files)]
            )
            for split, paths in split_files:
                for index, source_path in enumerate(paths, 1):
                    month_label = source_path.stem
                    destination = staging_dir / "flights" / split / source_path.name
                    rows = build_one_month(connection, source_path, destination, month_label)
                    source_rows = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM read_parquet({sql_string(source_path)})"
                        ).fetchone()[0]
                    )
                    if rows != source_rows:
                        raise RuntimeError(
                            f"{month_label}: enriched row count {rows} differs from source {source_rows}"
                        )
                    row_counts[f"{split}/{month_label}"] = rows
                    print(
                        f"  {split}: {month_label} {rows:,} rows ({index}/{len(paths)})",
                        flush=True,
                    )

            metadata = {
                "dataset": "BTS DepDel15 with leakage-safe historical delay features",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_data_dir": str(data_dir),
                "target_input_data_dir": str(target_data_dir),
                "target_months": [path.stem for path in target_files],
                "target_only_feature_output": target_only,
                "source_splits_read": {"train": [2022, 2023], "validation": [2024]},
                "test_2025_read": False,
                "history_windows": [
                    "three fully completed calendar months before the flight month",
                    "prior 7 and 30 completed calendar days, excluding the flight date",
                ],
                "group_features": [
                    "system-wide delay rate",
                    "airline delay rate",
                    "origin-airport delay rate",
                    "origin-destination route delay rate",
                    "7-day and 30-day global, airline, origin, route, and origin-airport-by-departure-hour rates",
                ],
                "smoothing_rules": {
                    "monthly": "Airline, origin, and route rates use a 50-flight prior weighted by the system-wide rate from the same prior three-month window.",
                    "daily": "Airline, origin, route, and origin-hour rates use a 50-flight prior weighted by the corresponding global rate from the same lookback window.",
                },
                "availability_rule": (
                    "The monthly rates use only fully completed months. Daily rates use dates strictly before the target flight date, so no same-day outcome or later month is used. "
                    "Historical rates describe eligible non-cancelled flights."
                ),
                "feature_columns": BASE_COLUMNS + HISTORICAL_COLUMNS + DAILY_HISTORICAL_COLUMNS,
                "provenance_columns": PROVENANCE_COLUMNS,
                "provenance_is_model_input": False,
                "row_counts": row_counts,
                "parquet_root": "flights/",
            }
            (staging_dir / "metadata.json").write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )
        finally:
            connection.close()

        shutil.rmtree(staging_dir / "duckdb_temp", ignore_errors=True)
        if output_dir.exists():
            output_dir.rmdir()  # Only an empty directory can pass the preflight check.
        os.replace(staging_dir, output_dir)

    print(f"Historical-feature data: {output_dir}", flush=True)
    print("The 2025 test split was not opened.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
