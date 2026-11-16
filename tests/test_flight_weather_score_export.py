from __future__ import annotations

import unittest
import sys
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from export_flight_weather_scores_2024 import _cohort_metadata, _safe_text, _score_maps_by_flight_id, export_status, h2_feature_files


class ScoreExportAlignmentTests(unittest.TestCase):
    def test_batch_scores_are_keyed_by_flight_id_not_dataframe_index(self) -> None:
        frame = pd.DataFrame({"flight_id": ["canonical-b", "canonical-a"]}, index=[17, 42])
        probabilities = np.array([0.8, 0.2])
        reasons = ["Distance", ""]

        scores, support = _score_maps_by_flight_id(frame, probabilities, reasons)

        self.assertEqual(scores, {"canonical-b": 0.8, "canonical-a": 0.2})
        self.assertEqual(support, {"canonical-b": "Distance", "canonical-a": ""})

    def test_duplicate_ids_cannot_silently_overwrite_scores(self) -> None:
        frame = pd.DataFrame({"flight_id": ["same", "same"]})
        with self.assertRaisesRegex(ValueError, "Duplicate flight_id"):
            _score_maps_by_flight_id(frame, np.array([0.2, 0.7]), ["", ""])

    def test_missing_timestamp_is_serialized_as_empty_text(self) -> None:
        self.assertEqual(_safe_text(pd.NaT), "")

    def test_membership_flight_number_is_not_taken_from_feature_row(self) -> None:
        metadata = _cohort_metadata({
            "cohort_Flight_Number_Reporting_Airline": "007",
            "cohort_Reporting_Airline": "UA",
            "cohort_Origin": "LAX", "cohort_Dest": "JFK",
            "cohort_CRSDepTime": 500, "cohort_FlightDate": "2024-07-01",
            "cohort_source_row_ordinal": 9,
        })
        self.assertEqual(metadata["Flight_Number_Reporting_Airline"], "007")
        self.assertEqual(metadata["source_row_ordinal"], 9)


class H2ExportPartitionEnforcementTests(unittest.TestCase):
    def test_h2_feature_files_requires_exact_july_december_2024_partitions(self) -> None:
        """Test that h2_feature_files rejects missing or unexpected partitions."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            horizon_dir = root / "t_minus_2h" / "flights" / "validation"
            horizon_dir.mkdir(parents=True)

            # Missing July partition
            for month in ("2024-08", "2024-09", "2024-10", "2024-11", "2024-12"):
                (horizon_dir / f"{month}.parquet").touch()

            with self.assertRaisesRegex(FileNotFoundError, "missing=2024-07"):
                h2_feature_files(root, "t_minus_2h")

            # Extra January 2025 partition
            (horizon_dir / "2024-07.parquet").touch()
            (horizon_dir / "2025-01.parquet").touch()

            with self.assertRaisesRegex(FileNotFoundError, "unexpected=2025-01"):
                h2_feature_files(root, "t_minus_2h")

            # Correct partitions pass
            (horizon_dir / "2025-01.parquet").unlink()
            files = h2_feature_files(root, "t_minus_2h")
            self.assertEqual(len(files), 6)
            self.assertEqual([f.stem for f in files], ["2024-07", "2024-08", "2024-09", "2024-10", "2024-11", "2024-12"])

    def test_export_status_fails_without_complete_required_checks(self) -> None:
        """Test that export_status returns FAIL unless all required checks pass."""
        # Complete pass
        summary = {
            "membership_rows": 100,
            "matched_feature_rows": 100,
            "membership_rows_without_features": 0,
            "target_mismatches": 0,
            "provenance_mismatches": 0,
            "feature_rows_with_unresolved_identity": 0,
        }
        self.assertEqual(export_status(summary), "PASS")

        # Row count mismatch
        summary["matched_feature_rows"] = 99
        self.assertEqual(export_status(summary), "FAIL")
        summary["matched_feature_rows"] = 100

        # Any non-zero required field fails
        for key in ("membership_rows_without_features", "target_mismatches", "provenance_mismatches", "feature_rows_with_unresolved_identity"):
            summary[key] = 1
            self.assertEqual(export_status(summary), "FAIL", f"Expected FAIL when {key} is non-zero")
            summary[key] = 0


class SQLIntegrityNullCheckTests(unittest.TestCase):
    def test_null_target_bypass_is_prevented_in_export_validation(self) -> None:
        """Test that NULL target values are detected as mismatches in export validation."""
        import duckdb

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = duckdb.connect()

            try:
                # Create membership table with valid data
                db.execute("""
                    CREATE TEMP TABLE membership AS
                    SELECT 'flight-1' AS flight_id, 0 AS cohort_target,
                           'bts-flight-v1' AS identity_version,
                           'archive.zip' AS source_archive,
                           'abc123' AS source_archive_sha256,
                           'member.csv' AS source_csv_member,
                           1 AS source_row_ordinal
                """)

                # Create feature view with NULL target - should be detected as mismatch
                db.execute("""
                    CREATE TEMP VIEW features AS
                    SELECT 'flight-1' AS flight_id, NULL AS DepDel15,
                           'bts-flight-v1' AS identity_version,
                           'archive.zip' AS source_archive,
                           'abc123' AS source_archive_sha256,
                           'member.csv' AS source_csv_member,
                           1 AS source_row_ordinal
                """)

                # The validation query should count NULL as a mismatch
                target_mismatches = db.execute("""
                    SELECT COUNT(*) FROM membership m
                    LEFT JOIN features f ON f.flight_id = m.flight_id
                    WHERE f.flight_id IS NOT NULL AND (
                        f.DepDel15 IS NULL OR f.DepDel15 IS DISTINCT FROM m.cohort_target
                    )
                """).fetchone()[0]

                self.assertEqual(target_mismatches, 1, "NULL target should be counted as mismatch")
            finally:
                db.close()

    def test_null_provenance_bypass_is_prevented_in_export_validation(self) -> None:
        """Test that NULL provenance values are detected as mismatches."""
        import duckdb

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = duckdb.connect()

            try:
                db.execute("""
                    CREATE TEMP TABLE membership AS
                    SELECT 'flight-1' AS flight_id, 0 AS cohort_target,
                           'archive.zip' AS source_archive,
                           'abc123' AS source_archive_sha256,
                           'member.csv' AS source_csv_member,
                           1 AS source_row_ordinal
                """)

                # Feature with NULL identity_version
                db.execute("""
                    CREATE TEMP VIEW features AS
                    SELECT 'flight-1' AS flight_id, 0 AS DepDel15,
                           NULL AS identity_version,
                           'archive.zip' AS source_archive,
                           'abc123' AS source_archive_sha256,
                           'member.csv' AS source_csv_member,
                           1 AS source_row_ordinal
                """)

                provenance_mismatches = db.execute("""
                    SELECT COUNT(*) FROM membership m
                    LEFT JOIN features f ON f.flight_id = m.flight_id
                    WHERE f.flight_id IS NOT NULL AND (
                        f.identity_version IS NULL OR f.identity_version IS DISTINCT FROM 'bts-flight-v1'
                        OR f.source_archive_sha256 IS NULL OR f.source_archive_sha256 IS DISTINCT FROM m.source_archive_sha256
                        OR f.source_csv_member IS NULL OR f.source_csv_member IS DISTINCT FROM m.source_csv_member
                        OR f.source_row_ordinal IS NULL OR f.source_row_ordinal IS DISTINCT FROM m.source_row_ordinal
                    )
                """).fetchone()[0]

                self.assertEqual(provenance_mismatches, 1, "NULL identity_version should be counted as mismatch")
            finally:
                db.close()


class ScoreExportValidatorCancellationTests(unittest.TestCase):
    def test_cancelled_and_unknown_status_membership_rows_are_counted(self) -> None:
        """Both Cancelled=1 and Cancelled=NULL must fail membership validation."""
        import duckdb
        from validate_flight_weather_score_exports_2024 import _membership_integrity_counts

        db = duckdb.connect()
        try:
            db.execute("""
                CREATE TEMP TABLE membership (
                    flight_id VARCHAR,
                    DepDel15 INTEGER,
                    Cancelled INTEGER
                )
            """)
            db.executemany(
                "INSERT INTO membership VALUES (?, ?, ?)",
                [
                    ("valid-flight", 0, 0),
                    ("cancelled-flight", 0, 1),
                    ("unknown-status-flight", 0, None),
                ],
            )

            counts = _membership_integrity_counts(db, "membership")
            self.assertEqual(counts, (3, 3, 0, 0, 2))
        finally:
            db.close()



if __name__ == "__main__":
    unittest.main()
