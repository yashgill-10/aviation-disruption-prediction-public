from __future__ import annotations

import csv
import hashlib
from contextlib import redirect_stdout
import io
from pathlib import Path
import struct
import json
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bts_archives import ARCHIVE_NAME, expected_archive, inspect_archive, inventory_month
from bts_preprocessing import OUTPUT_COLUMNS, PROVENANCE_COLUMNS, process_month, split_for_year
from canonical_flight_identity import flight_id_for_row
import download_bts_months
import build_historical_features
import build_ghcnh_weather_features
import prepare_bts_dataset
import prepare_bts_scoring_targets
import train_baseline


CSV_HEADER = [
    "FlightDate", "Reporting_Airline", "Origin", "Dest", "CRSDepTime", "CRSArrTime",
    "Cancelled", "DepDel15", "Distance", "CRSElapsedTime", "DepDelayMinutes", "Diverted",
    "DOT_ID_Reporting_Airline", "Flight_Number_Reporting_Airline", "OriginAirportID", "DestAirportID",
]
ROWS = [
    ["2022-01-02", "AA", "SFO", "JFK", "800", "1600", "0", "0", "2586", "360", "0", "0", "19805", "1", "12892", "12478"],
    ["2022-01-03", "UA", "ORD", "LAX", "1000", "1200", "0", "1", "1744", "240", "20", "0", "19977", "7", "13930", "12892"],
    ["2022-01-04", "AA", "SEA", "LAX", "900", "1100", "1", "1", "954", "120", "", "0", "19805", "2", "14747", "12892"],
    ["2022-01-05", "DL", "ATL", "BOS", "700", "1000", "0", "", "946", "180", "", "0", "19790", "20", "10397", "10721"],
    ["2022-02-01", "UA", "ORD", "SFO", "600", "900", "0", "0", "1846", "270", "0", "0", "19977", "100", "13930", "12892"],
    ["not-a-date", "AA", "JFK", "BOS", "800", "900", "0", "bad", "184", "60", "", "1", "19805", "3", "12478", "10721"],
    ["2022-01-06", "", "MIA", "ATL", "", "", "", "0", "600", "120", "", "0", "", "", "13303", "10397"],
    ["2022-01-07", "AA", "SFO", "JFK", "900", "1700", "0", "0", "2586", "360", "0", "0", "19805", "", "12892", "12478"],
]


def write_archive(path: Path, year: int = 2022, month: int = 1, rows=ROWS) -> None:
    member = ARCHIVE_NAME.format(year=year, month=month).removesuffix(".zip") + ".csv"
    normalized_rows = []
    for index, row in enumerate(rows, 1):
        row = list(row)
        if len(row) == len(CSV_HEADER) - 4:
            row.extend(["19805", str(index), "12892", "12478"])
        if len(row) != len(CSV_HEADER):
            raise ValueError("test archive row does not match the BTS fixture schema")
        normalized_rows.append(row)
    with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", delete=False) as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        writer.writerows(normalized_rows)
        csv_path = Path(handle.name)
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(csv_path, member)
    finally:
        csv_path.unlink(missing_ok=True)


class BTSArchiveTests(unittest.TestCase):
    def test_inventory_reports_partial_without_opening_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            partial = Path(str(expected_archive(data_dir, 2022, 1)) + ".part")
            partial.write_bytes(b"not a zip")
            record = inventory_month(data_dir, 2022, 1, verify_crc=True)
            self.assertEqual(record["state"], "partial")
            self.assertIn("left untouched", str(record["detail"]))
            self.assertTrue(partial.exists())

    def test_full_crc_check_detects_corrupted_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "valid.zip"
            write_archive(archive_path)
            self.assertEqual(inspect_archive(archive_path, verify_crc=True).state, "valid")
            raw = bytearray(archive_path.read_bytes())
            central_directory = raw.find(b"PK\x01\x02")
            self.assertGreaterEqual(central_directory, 0)
            crc_offset = central_directory + 16
            original_crc = struct.unpack_from("<I", raw, crc_offset)[0]
            struct.pack_into("<I", raw, crc_offset, original_crc ^ 1)
            corrupt_path = Path(temp) / "corrupt.zip"
            corrupt_path.write_bytes(raw)
            check = inspect_archive(corrupt_path, verify_crc=True)
            self.assertEqual(check.state, "invalid")
            self.assertIn("CRC", check.detail)


class BTSDownloaderTests(unittest.TestCase):
    def test_existing_archive_skip_does_not_recheck_member_crc(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            raw_dir = Path(temp)
            archive_path = raw_dir / download_bts_months.FILE_TEMPLATE.format(year=2022, month=1)
            write_archive(archive_path)
            with zipfile.ZipFile(archive_path, "a", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("padding.bin", b"x" * (download_bts_months.MIN_ARCHIVE_BYTES + 1))
            with patch.object(download_bts_months, "is_valid_zip", side_effect=AssertionError("CRC scan")):
                with redirect_stdout(io.StringIO()):
                    self.assertTrue(download_bts_months.download_one(None, 2022, 1, raw_dir))

    def test_downloader_lock_rejects_a_second_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            with download_bts_months.exclusive_download_lock(output_dir):
                source_dir = Path(__file__).resolve().parents[1] / "src"
                child_code = (
                    "import sys\n"
                    "from pathlib import Path\n"
                    "sys.path.insert(0, sys.argv[2])\n"
                    "from download_bts_months import exclusive_download_lock, DownloadLockError\n"
                    "try:\n"
                    "    with exclusive_download_lock(Path(sys.argv[1])): pass\n"
                    "except DownloadLockError:\n"
                    "    raise SystemExit(3)\n"
                    "raise SystemExit(0)\n"
                )
                child = subprocess.run(
                    [sys.executable, "-c", child_code, str(output_dir), str(source_dir)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(child.returncode, 3, child.stdout + child.stderr)

            with download_bts_months.exclusive_download_lock(output_dir):
                pass


class BaselineFeatureTests(unittest.TestCase):
    def test_scheduled_hhmm_parser_handles_midnight_and_invalid_values(self) -> None:
        hours, minutes, missing = train_baseline.parse_hhmm(
            pd.Series([0, 1259, 2400, 1260, None])
        )
        self.assertEqual(hours[:3].tolist(), [0.0, 12.0, 0.0])
        self.assertEqual(minutes[:3].tolist(), [0.0, 59.0, 0.0])
        self.assertEqual(missing.tolist(), [False, False, False, True, True])

    def test_numeric_features_are_finite_and_have_fixed_width(self) -> None:
        frame = pd.DataFrame({
            "FlightDate": pd.to_datetime(["2024-01-01", "bad"], errors="coerce"),
            "CRSDepTime": [800, None],
            "CRSArrTime": [1200, 2460],
            "Distance": [1000, None],
            "CRSElapsedTime": [120, None],
        })
        features = train_baseline.numeric_features(frame)
        self.assertEqual(features.shape, (2, len(train_baseline.NUMERIC_FEATURES)))
        self.assertTrue(np.isfinite(features).all())

    def test_identity_metadata_does_not_change_model_feature_matrix(self) -> None:
        frame = pd.DataFrame({
            "FlightDate": [pd.Timestamp("2024-07-01")],
            "Reporting_Airline": ["AA"], "Origin": ["SFO"], "Dest": ["JFK"],
            "CRSDepTime": [800], "CRSArrTime": [1600], "Distance": [2586],
            "CRSElapsedTime": [360], "DepDel15": [1],
        })
        encoder = train_baseline.OneHotEncoder(handle_unknown="ignore")
        encoder.fit(frame[train_baseline.CATEGORICAL_FEATURES])
        baseline, target = train_baseline.encode_frame(frame, encoder)
        with_metadata = frame.assign(
            flight_id="bts-flight-v1:fixture", identity_version="bts-flight-v1",
            identity_status="resolved", source_archive="sample.zip",
            source_archive_sha256="a" * 64, source_csv_member="sample.csv",
            source_row_ordinal=17,
        )
        enriched, enriched_target = train_baseline.encode_frame(with_metadata, encoder)
        np.testing.assert_array_equal(baseline.toarray(), enriched.toarray())
        np.testing.assert_array_equal(target, enriched_target)

    def test_score_threshold_and_calibration_bins_are_reported(self) -> None:
        target = np.array([0, 0, 1, 1], dtype=np.int8)
        probability = np.array([0.1, 0.4, 0.6, 0.9], dtype=np.float32)
        metrics = train_baseline.score_predictions(target, probability, threshold=0.6)
        self.assertEqual(metrics["threshold"], 0.6)
        self.assertEqual(metrics["confusion_matrix"], {"tn": 2, "fp": 0, "fn": 0, "tp": 2})
        bins = train_baseline.calibration_bins(target, probability, bins=2)
        self.assertEqual(sum(item["rows"] for item in bins), 4)


class HistoricalFeatureTests(unittest.TestCase):
    def test_target_month_selector_reads_only_requested_identity_months(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for label in ("2024-07", "2024-12"):
                path = root / "flights" / "validation" / f"{label}.parquet"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            selected = build_historical_features.select_target_month_files(
                root, ["2024-12", "2024-07"]
            )
            self.assertEqual([path.stem for path in selected], ["2024-07", "2024-12"])
            with self.assertRaises(ValueError):
                build_historical_features.select_target_month_files(root, ["2025-07"])

    def test_history_uses_only_the_three_completed_months_before_each_flight_month(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "sample.parquet"
            rows = []
            for row_index, (flight_date, target) in enumerate([
                ("2023-08-03", 1),  # outside January's three-month window
                ("2023-10-03", 0),
                ("2023-11-03", 1),
                ("2023-12-03", 1),
                ("2024-01-03", 0),  # must not leak into its own features
                ("2024-02-03", 0),
            ]):
                rows.append({
                    "FlightDate": pd.Timestamp(flight_date),
                    "Reporting_Airline": "AA",
                    "Origin": "AAA",
                    "Dest": "BBB",
                    "CRSDepTime": 800,
                    "CRSArrTime": 1000,
                    "Distance": 100,
                    "CRSElapsedTime": 120,
                    "DepDel15": target,
                    "flight_id": f"fixture-flight-{row_index}",
                    "identity_version": "bts-flight-v1",
                    "identity_status": "resolved",
                    "source_archive": "fixture.zip",
                    "source_archive_sha256": "b" * 64,
                    "source_csv_member": "fixture.csv",
                    "source_row_ordinal": row_index + 1,
                })
            pd.DataFrame(rows).to_parquet(source_path, index=False)

            import duckdb

            connection = duckdb.connect()
            try:
                build_historical_features.make_group_history(connection, [source_path])
                january_path = root / "2024-01.parquet"
                february_path = root / "2024-02.parquet"
                january_rows = build_historical_features.build_one_month(
                    connection, source_path, january_path, "2024-01"
                )
                february_rows = build_historical_features.build_one_month(
                    connection, source_path, february_path, "2024-02"
                )
            finally:
                connection.close()

            january = pd.read_parquet(january_path)
            february = pd.read_parquet(february_path)
            self.assertEqual(january_rows, 1)
            self.assertEqual(february_rows, 1)
            self.assertAlmostEqual(january.loc[0, "prev3m_global_delay_rate"], 2 / 3)
            self.assertAlmostEqual(january.loc[0, "prev3m_route_delay_rate"], 2 / 3)
            self.assertAlmostEqual(january.loc[0, "prev3m_global_log_flights"], np.log(4))
            self.assertEqual(january.loc[0, "prev3m_history_missing"], 0)
            self.assertEqual(january.loc[0, "flight_id"], "fixture-flight-4")
            self.assertEqual(int(january.loc[0, "source_row_ordinal"]), 5)
            # January's completed labels are available to February, while February's own label is not.
            self.assertAlmostEqual(february.loc[0, "prev3m_global_delay_rate"], 2 / 3)
            self.assertAlmostEqual(february.loc[0, "prev3m_global_log_flights"], np.log(4))
            self.assertEqual(february.loc[0, "flight_id"], "fixture-flight-5")


class WeatherIdentityPreservationTests(unittest.TestCase):
    def test_month_filter_requires_only_selected_weather_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for label in ("2024-07", "2024-12"):
                path = root / "flights" / "validation" / f"{label}.parquet"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            selected = build_ghcnh_weather_features.expected_flight_files(
                root, ["2024-07", "2024-12"]
            )
            self.assertEqual([item[3].stem for item in selected], ["2024-07", "2024-12"])
            with self.assertRaises(FileNotFoundError):
                build_ghcnh_weather_features.expected_flight_files(root)

    def test_horizon_weather_join_preserves_ids_and_source_ordinals(self) -> None:
        import duckdb

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.parquet"
            destination = root / "weather.parquet"
            pd.DataFrame({
                "FlightDate": pd.to_datetime(["2024-07-01", "2024-07-01"]),
                "Reporting_Airline": ["AA", "AA"], "Origin": ["AAA", "AAA"],
                "Dest": ["BBB", "BBB"], "CRSDepTime": [900, 900], "CRSArrTime": [1100, 1100],
                "Distance": [100, 100], "CRSElapsedTime": [120, 120], "DepDel15": [0, 1],
                "flight_id": ["flight-a", "flight-b"],
                "identity_version": ["bts-flight-v1"] * 2,
                "identity_status": ["resolved"] * 2,
                "source_archive": ["source.zip"] * 2,
                "source_archive_sha256": ["c" * 64] * 2,
                "source_csv_member": ["source.csv"] * 2,
                "source_row_ordinal": [31, 32],
            }).to_parquet(source, index=False)
            connection = duckdb.connect()
            try:
                connection.execute("""CREATE TEMP TABLE airport_map (
                    airport_code VARCHAR, station_id VARCHAR, timezone VARCHAR
                )""")
                connection.executemany(
                    "INSERT INTO airport_map VALUES (?, ?, ?)",
                    [("AAA", "STATION_A", "America/Los_Angeles"),
                     ("BBB", "STATION_B", "America/Los_Angeles")],
                )
                connection.execute("""CREATE TEMP TABLE ghcnh_weather (
                    station_id VARCHAR, available_at_utc TIMESTAMPTZ,
                    temperature_c DOUBLE, dew_point_temperature_c DOUBLE, wind_speed_m_s DOUBLE,
                    wind_direction_deg DOUBLE, wind_gust_m_s DOUBLE, visibility_km DOUBLE,
                    sea_level_pressure_hpa DOUBLE, weather_rain TINYINT, weather_snow TINYINT,
                    weather_fog_or_mist TINYINT, weather_thunderstorm TINYINT
                )""")
                for horizon_name, horizon_minutes in (("t_minus_2h", 120), ("t_minus_30m", 30)):
                    horizon_output = root / f"{horizon_name}.parquet"
                    stats = build_ghcnh_weather_features.build_month(
                        connection, source, horizon_output, "validation", "2024-07", horizon_name,
                        horizon_minutes, 180, None, False,
                    )
                    result = pd.read_parquet(horizon_output)
                    self.assertEqual(stats["rows"], 2)
                    self.assertEqual(
                        result.set_index("flight_id")["source_row_ordinal"].astype(int).to_dict(),
                        {"flight-a": 31, "flight-b": 32},
                    )
                    self.assertEqual(
                        result.set_index("flight_id")["source_archive_sha256"].to_dict(),
                        {"flight-a": "c" * 64, "flight-b": "c" * 64},
                    )
            finally:
                connection.close()


class BTSPreprocessingTests(unittest.TestCase):
    def test_h2_target_preparer_emits_identity_rows_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw_dir = root / "raw"
            raw_dir.mkdir()
            archive = expected_archive(raw_dir, 2024, 7)
            row = [
                "2024-07-01", "AA", "SFO", "JFK", "800", "1600", "0", "1",
                "2586", "360", "20", "0", "19805", "1", "12892", "12478",
            ]
            write_archive(archive, 2024, 7, [row])
            output = root / "prepared"
            status = prepare_bts_scoring_targets.main([
                "--input-dir", str(raw_dir), "--output-dir", str(output),
                "--months", "2024-07", "--chunksize", "1",
            ])
            self.assertEqual(status, 0)
            parquet_path = output / "flights" / "validation" / "2024-07.parquet"
            prepared = pd.read_parquet(parquet_path)
            self.assertEqual(len(prepared), 1)
            self.assertEqual(int(prepared.loc[0, "source_row_ordinal"]), 1)
            self.assertEqual(prepared.loc[0, "identity_status"], "resolved")
            self.assertEqual(prepared.loc[0, "source_archive_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertFalse(json.loads((output / "metadata.json").read_text(encoding="utf-8"))["test_2025_read"])
            before = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
            status = prepare_bts_scoring_targets.main([
                "--input-dir", str(raw_dir), "--output-dir", str(output),
                "--months", "2024-07",
            ])
            self.assertEqual(status, 2)
            self.assertEqual(hashlib.sha256(parquet_path.read_bytes()).hexdigest(), before)

    def test_split_boundaries_are_fixed(self) -> None:
        self.assertEqual(split_for_year(2022), "train")
        self.assertEqual(split_for_year(2023), "train")
        self.assertEqual(split_for_year(2024), "validation")
        self.assertEqual(split_for_year(2025), "test")
        self.assertEqual(split_for_year(2026), "operational_history")
        with self.assertRaises(ValueError):
            split_for_year(2021)

    def test_month_stream_filters_labels_dates_and_leakage_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = expected_archive(root, 2022, 1)
            write_archive(archive_path)
            output_path = root / "2022-01.parquet"
            audit = process_month(archive_path, 2022, 1, output_path, chunksize=2)

            self.assertEqual(audit["source_rows"], 8)
            self.assertEqual(audit["cancelled_rows_excluded"], 1)
            self.assertEqual(audit["unknown_cancellation_rows"], 1)
            self.assertEqual(audit["missing_target_rows"], 1)
            self.assertEqual(audit["invalid_target_rows"], 1)
            self.assertEqual(audit["invalid_date_rows"], 1)
            self.assertEqual(audit["out_of_month_date_rows"], 1)
            self.assertEqual(audit["eligible_rows"], 3)
            self.assertEqual((audit["target_0"], audit["target_1"]), (2, 1))
            self.assertEqual(audit["identity_resolved_rows"], 2)
            self.assertEqual(audit["identity_unresolved_rows"], 1)
            self.assertTrue(audit["crc_verified"])
            self.assertEqual(audit["observed_date_count"], 6)

            processed = pd.read_parquet(output_path)
            self.assertEqual(processed.columns.tolist(), OUTPUT_COLUMNS)
            self.assertNotIn("DepDelayMinutes", processed.columns)
            self.assertNotIn("Diverted", processed.columns)
            self.assertEqual(processed["DepDel15"].tolist(), [0, 1, 0])
            self.assertEqual(set(processed["split"]), {"train"})
            self.assertEqual(processed["source_year"].unique().tolist(), [2022])
            self.assertEqual(processed["source_row_ordinal"].tolist(), [1, 2, 8])
            self.assertEqual(processed["identity_status"].tolist(), ["resolved", "resolved", "unresolved_incomplete_or_invalid"])
            self.assertTrue(processed.loc[:1, "flight_id"].notna().all())
            self.assertTrue(pd.isna(processed.loc[2, "flight_id"]))
            expected_id = flight_id_for_row({
                "FlightDate": "2022-01-02", "DOT_ID_Reporting_Airline": "19805",
                "Reporting_Airline": "AA", "Flight_Number_Reporting_Airline": "1",
                "OriginAirportID": "12892", "Origin": "SFO", "DestAirportID": "12478",
                "Dest": "JFK", "CRSDepTime": "800",
            })
            self.assertEqual(processed.loc[0, "flight_id"], expected_id)
            self.assertEqual(processed.loc[0, "source_archive_sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest())
            self.assertEqual(processed.loc[0, "source_csv_member"], ARCHIVE_NAME.format(year=2022, month=1).removesuffix(".zip") + ".csv")
            self.assertEqual(set(PROVENANCE_COLUMNS).issubset(processed.columns), True)

    def test_2025_is_suppressed_when_any_month_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw_dir = root / "raw"
            raw_dir.mkdir()
            for month in range(1, 12):
                row = [f"2025-{month:02d}-01", "AA", "SFO", "JFK", "800", "1600",
                       "0", "1", "2586", "360", "20", "0"]
                write_archive(expected_archive(raw_dir, 2025, month), 2025, month, [row])
            output_dir = root / "processed"
            with patch("sys.argv", ["prepare_bts_dataset", "--input-dir", str(raw_dir),
                                     "--output-dir", str(output_dir)]):
                with redirect_stdout(io.StringIO()):
                    status = prepare_bts_dataset.main()
            self.assertEqual(status, 0)
            metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertFalse(metadata["test_2025_included"])
            audit = pd.read_csv(output_dir / "monthly_audit.csv")
            test_rows = audit.loc[audit["year"].eq(2025)]
            self.assertEqual(len(test_rows), 12)
            self.assertTrue(test_rows["status"].eq("test_suppressed_incomplete_year").all())
            self.assertFalse((output_dir / "flights" / "test").exists())

    def test_complete_2025_year_is_committed_as_one_test_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw_dir = root / "raw"
            raw_dir.mkdir()
            for month in range(1, 13):
                row = [f"2025-{month:02d}-01", "AA", "SFO", "JFK", "800", "1600",
                       "0", "1", "2586", "360", "20", "0"]
                write_archive(expected_archive(raw_dir, 2025, month), 2025, month, [row])
            output_dir = root / "processed"
            with patch("sys.argv", ["prepare_bts_dataset", "--input-dir", str(raw_dir),
                                     "--output-dir", str(output_dir)]):
                with redirect_stdout(io.StringIO()):
                    status = prepare_bts_dataset.main()
            self.assertEqual(status, 0)
            metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertTrue(metadata["test_2025_included"])
            test_files = sorted((output_dir / "flights" / "test").glob("*.parquet"))
            self.assertEqual(len(test_files), 12)
            audit = pd.read_csv(output_dir / "monthly_audit.csv")
            test_rows = audit.loc[audit["year"].eq(2025)]
            self.assertEqual(int(test_rows["eligible_rows"].sum()), 12)
            self.assertTrue(test_rows["crc_verified"].all())


if __name__ == "__main__":
    unittest.main()
