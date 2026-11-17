"""Focused synthetic contract checks for the M3 input and partition helpers."""
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np

MODULE_PATH = Path(__file__).with_name("train_evaluate_m3.py")
spec = importlib.util.spec_from_file_location("m3_experiment", MODULE_PATH)
m3 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(m3)


def write_gzip_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class M3ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.old_expected_sept = m3.EXPECTED_SEPTEMBER["t_minus_2h"]
        self.old_expected_hash = m3.EXPECTED_SCORE_SHA[("flight_weather", "t_minus_2h")]
        m3.EXPECTED_SEPTEMBER["t_minus_2h"] = 1
        self.h = "t_minus_2h"
        self.id_by_month = {month: f"bts-flight-v1:{month:02d}" for month in (9, 10, 11, 12)}
        self.paths = {
            "flight_weather_scores": self.root / "fw.csv.gz",
            "airport_network_scores": self.root / "an.csv.gz",
            "airport_network_targets": self.root / "targets.csv.gz",
            "airport_network_features": self.root / "features.csv.gz",
        }
        self.write_inputs()
        m3.EXPECTED_SCORE_SHA[("flight_weather", self.h)] = m3.sha256_file(self.paths["flight_weather_scores"])

    def tearDown(self):
        m3.EXPECTED_SEPTEMBER["t_minus_2h"] = self.old_expected_sept
        m3.EXPECTED_SCORE_SHA[("flight_weather", self.h)] = self.old_expected_hash
        self.temp.cleanup()

    def write_inputs(self, *, mismatch_month: int | None = None, duplicate_fw: bool = False,
                     wrong_fw_horizon: bool = False, missing_probability: bool = False) -> None:
        fw_fields = ["flight_id", "horizon", "score_status", "unavailable_reason", "predicted_delay_probability",
                     "predicted_delay_15", "threshold", "DepDel15", "FlightDate"]
        an_fields = ["flight_id", "horizon", "score_status", "p_delay15", "decision_threshold",
                     "predicted_label", "unavailable_reason"]
        target_fields = ["flight_id", "horizon", "DepDel15"]
        feature_fields = ["flight_id", "horizon", "MONTH"]
        fw_rows, an_rows, target_rows, feature_rows = [], [], [], []
        for month in (9, 10, 11, 12):
            fid = self.id_by_month[month]
            y = str(month % 2)
            p_fw, p_an = (0.8, 0.7) if y == "1" else (0.2, 0.1)
            fw_rows.append({"flight_id": fid, "horizon": "bad" if wrong_fw_horizon else self.h,
                            "score_status": "scored", "unavailable_reason": "",
                            "predicted_delay_probability": "" if missing_probability and month == 9 else str(p_fw),
                            "predicted_delay_15": str(int(p_fw >= 0.5)), "threshold": "0.5", "DepDel15": y,
                            "FlightDate": f"2024-{month:02d}-15"})
            y_an = str(1 - int(y)) if mismatch_month == month else y
            an_rows.append({"flight_id": fid, "horizon": self.h, "score_status": "scored", "p_delay15": str(p_an),
                            "decision_threshold": "0.4", "predicted_label": str(int(p_an >= 0.4)),
                            "unavailable_reason": ""})
            target_rows.append({"flight_id": fid, "horizon": self.h, "DepDel15": y_an})
            feature_rows.append({"flight_id": fid, "horizon": self.h, "MONTH": str(month)})
        if duplicate_fw:
            fw_rows.append(fw_rows[0].copy())
        write_gzip_csv(self.paths["flight_weather_scores"], fw_fields, fw_rows)
        write_gzip_csv(self.paths["airport_network_scores"], an_fields, an_rows)
        write_gzip_csv(self.paths["airport_network_targets"], target_fields, target_rows)
        write_gzip_csv(self.paths["airport_network_features"], feature_fields, feature_rows)
        m3.EXPECTED_SCORE_SHA[("flight_weather", self.h)] = m3.sha256_file(self.paths["flight_weather_scores"])

    def run_join(self):
        fw_manifest = {"models": {self.h: {"threshold": 0.5}},
                       "outputs": {self.h: {"output_sha256": m3.sha256_file(self.paths["flight_weather_scores"])}}}
        an_manifest = {
            "saved_thresholds_and_rule": {self.h: 0.4},
            "horizons": {self.h: {
                "score_export": {"score_file_sha256": m3.sha256_file(self.paths["airport_network_scores"])},
                "target_table": {"sha256": m3.sha256_file(self.paths["airport_network_targets"])},
                "feature_inputs": {"sha256": m3.sha256_file(self.paths["airport_network_features"])},
            }},
        }
        return m3.load_and_join_horizon(self.h, self.paths, fw_manifest, an_manifest)

    def test_exact_canonical_join_and_separate_periods(self):
        periods, report = self.run_join()
        self.assertEqual(report["september_training_rows"], 1)
        self.assertEqual(report["october_threshold_selection_rows"], 1)
        self.assertEqual(report["november_december_evaluation_rows"], 2)
        self.assertEqual(len(periods[9]["y"]), 1)
        self.assertEqual(len(periods[10]["y"]), 1)
        self.assertEqual(len(periods[11]["y"]) + len(periods[12]["y"]), 2)

    def test_target_mismatch_aborts_join(self):
        self.write_inputs(mismatch_month=9)
        with self.assertRaisesRegex(ValueError, "target-mismatched"):
            self.run_join()

    def test_duplicate_canonical_id_aborts(self):
        self.write_inputs(duplicate_fw=True)
        with self.assertRaisesRegex(ValueError, "Flight/Weather input integrity"):
            self.run_join()

    def test_horizon_mismatch_aborts(self):
        self.write_inputs(wrong_fw_horizon=True)
        with self.assertRaisesRegex(ValueError, "horizon mismatch"):
            self.run_join()

    def test_missing_probability_aborts(self):
        self.write_inputs(missing_probability=True)
        with self.assertRaisesRegex(ValueError, "Flight/Weather input integrity"):
            self.run_join()

    def test_probability_validation_and_logit_clipping(self):
        self.assertEqual(m3.finite_probability("0.5", "synthetic"), 0.5)
        with self.assertRaises(ValueError):
            m3.finite_probability("NaN", "synthetic")
        x = m3.logit_features(np.array([0.0, 1.0]), np.array([1.0, 0.0]))
        self.assertTrue(np.isfinite(x).all())

    def test_partition_dates_are_disjoint_and_no_eval_month_is_train(self):
        self.assertEqual(m3.partition_for_month(9), "meta_training")
        self.assertEqual(m3.partition_for_month(10), "threshold_selection")
        self.assertEqual(m3.partition_for_month(11), "evaluation")
        self.assertEqual(m3.partition_for_month(12), "evaluation")
        self.assertFalse(set(m3.M3_TRAIN_MONTHS) & set(m3.M3_THRESHOLD_MONTHS + m3.M3_EVALUATION_MONTHS))
        self.assertEqual(set(m3.M3_TRAIN_MONTHS + m3.M3_THRESHOLD_MONTHS + m3.M3_EVALUATION_MONTHS), set(m3.MONTHS))

    def test_horizon_files_are_independent(self):
        self.assertNotEqual(m3.source_paths("t_minus_2h"), m3.source_paths("t_minus_30m"))
        self.assertEqual(m3.HORIZONS, ("t_minus_2h", "t_minus_30m"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
