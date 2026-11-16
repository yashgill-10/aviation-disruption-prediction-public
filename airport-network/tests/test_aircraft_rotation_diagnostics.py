"""Synthetic tests for aircraft-rotation coverage diagnostics."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pandas as pd

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PILOT_SCRIPT = PACKAGE_ROOT / "aircraft_rotation_propagation_pilot.py"


def load_diagnostics_builder():
    parsed = ast.parse(PILOT_SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_rotation_diagnostics"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {}
    exec(compile(module, str(PILOT_SCRIPT), "exec"), namespace)
    return namespace["_build_rotation_diagnostics"]


class AircraftRotationDiagnosticsTests(unittest.TestCase):
    def test_counts_match_and_cutoff_denominator_is_explicit(self):
        build = load_diagnostics_builder()
        pilot = pd.DataFrame({
            "horizon_min": [120, 120, 30, 30],
            "period": ["train", "test", "train", "test"],
            "Tail_Number": ["N1", None, "N2", "N3"],
            "scheduled_utc": [
                pd.Timestamp("2024-01-01", tz="UTC"),
                pd.Timestamp("2024-01-02", tz="UTC"),
                pd.NaT,
                pd.Timestamp("2024-01-04", tz="UTC"),
            ],
        })
        left = pd.DataFrame({"prior_tail_arrival_available": [1, 0]})
        key_tail_counts = pd.Series([1, 2, 1], index=["K1", "K2", "K3"])

        result = build(
            pilot,
            left,
            {"K1", "K2", "K3", "K4"},
            key_tail_counts,
            {"K2"},
            pd.Index(["K1", "K3"]),
        )

        self.assertEqual(result["sample_rows"], 4)
        self.assertEqual(result["distinct_sample_join_keys"], 4)
        self.assertEqual(result["distinct_unique_tail_keys"], 2)
        self.assertEqual(result["distinct_ambiguous_tail_keys"], 1)
        self.assertEqual(result["distinct_unmatched_tail_keys"], 1)
        self.assertEqual(result["rows_with_unique_tail_match"], 3)
        self.assertEqual(result["rows_with_valid_scheduled_cutoff_utc"], 3)
        self.assertEqual(result["rows_retained_after_unique_tail_and_valid_cutoff_filters"], 2)
        self.assertEqual(result["prior_arrival_feature_coverage_among_retained_rows"], 0.5)
        self.assertEqual(result["sample_rows_by_horizon_and_period"]["120m/train"], 1)
        self.assertIn("not coverage over all sampled eligible rows", result["coverage_denominator_note"])


if __name__ == "__main__":
    unittest.main()
