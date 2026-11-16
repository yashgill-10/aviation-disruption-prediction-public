from __future__ import annotations

import unittest
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_readiness import file_report, verification_failures


class HashVerificationTests(unittest.TestCase):
    def test_file_without_expected_hash_reports_not_verifiable(self) -> None:
        """Test that a file with no expected hash is NOT_VERIFIABLE, not a match."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            test_file = root / "test.csv"
            test_file.write_text("data", encoding="utf-8")

            report = file_report(test_file, expected_hash=None, verify=True)

            self.assertEqual(report["present"], True)
            self.assertEqual(report["verification_status"], "NOT_VERIFIABLE")
            self.assertIsNone(report["hash_matches_record"])
            self.assertIn("actual_sha256", report)

    def test_file_with_expected_hash_reports_match_only_on_verified_match(self) -> None:
        """Test that MATCH is only reported when actual hash matches expected."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            test_file = root / "test.csv"
            test_file.write_text("data", encoding="utf-8")

            # Correct hash
            import hashlib
            expected = hashlib.sha256(b"data").hexdigest()
            report = file_report(test_file, expected_hash=expected, verify=True)

            self.assertEqual(report["verification_status"], "PASS")
            self.assertTrue(report["hash_matches_record"])
            self.assertEqual(report["actual_sha256"], expected)

            # Incorrect hash
            wrong_hash = "0" * 64
            report = file_report(test_file, expected_hash=wrong_hash, verify=True)

            self.assertEqual(report["verification_status"], "MISMATCH")
            self.assertFalse(report["hash_matches_record"])

    def test_verification_failures_includes_not_verifiable_files(self) -> None:
        """Test that verification_failures marks NOT_VERIFIABLE as a failure."""
        report = {
            "file1": {
                "present": True,
                "verification_status": "NOT_VERIFIABLE",
            },
            "file2": {
                "present": True,
                "verification_status": "PASS",
            },
        }

        failures = verification_failures(report)
        self.assertIn("file1", failures)
        self.assertNotIn("file2", failures)

    def test_verification_failures_includes_mismatch_files(self) -> None:
        """Test that verification_failures marks MISMATCH as a failure."""
        report = {
            "file1": {
                "present": True,
                "verification_status": "MISMATCH",
            },
            "file2": {
                "present": True,
                "verification_status": "PASS",
            },
        }

        failures = verification_failures(report)
        self.assertIn("file1", failures)
        self.assertNotIn("file2", failures)

    def test_verification_failures_ignores_missing_files(self) -> None:
        """Test that missing files are not counted as verification failures."""
        report = {
            "file1": {
                "present": False,
                "verification_status": "MISSING",
            },
            "file2": {
                "present": True,
                "verification_status": "NOT_VERIFIABLE",
            },
        }

        failures = verification_failures(report)
        self.assertNotIn("file1", failures)
        self.assertIn("file2", failures)

    def test_strict_verification_fails_on_not_verifiable(self) -> None:
        """Test that strict verification mode fails when any file lacks expected hash."""
        report = {
            "status": "checked",
            "hashes_verified_now": True,
            "file1": {
                "present": True,
                "verification_status": "NOT_VERIFIABLE",
            },
        }

        failures = verification_failures(report)
        self.assertEqual(len(failures), 1)
        self.assertIn("file1", failures)


if __name__ == "__main__":
    unittest.main()
