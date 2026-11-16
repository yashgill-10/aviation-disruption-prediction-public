"""Synthetic contract tests for the local canonical join and matched evaluator."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "evaluation"))

import evaluate_matched_models as evaluator  # noqa: E402
import join_ordinal_scores_to_canonical_ids as joiner  # noqa: E402


def _cid(label: str) -> str:
    return f"bts-flight-v1:{hashlib.sha256(label.encode()).hexdigest()}"


class Phase2JoinAndEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=PACKAGE_ROOT)
        self.root = Path(self.temp.name)
        self.ids = [_cid(f"matched-eval-{i}") for i in range(4)]
        self.targets = [0, 1, 0, 1]
        self.probabilities = [0.15, 0.85, 0.20, 0.75]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_ordinal_scores(self, path: Path, horizon: str) -> None:
        threshold = 0.5
        pd.DataFrame({
            "cleaned_input_row_ordinal": [1, 2, 3, 4],
            "forecast_horizon": [horizon] * 4,
            "delay_15": self.targets,
            "delay_probability": self.probabilities,
            "decision_threshold": [threshold] * 4,
            "predicted_delay_15": [int(p >= threshold) for p in self.probabilities],
            "score_status": ["scored"] * 4,
            "unavailable_reason": [""] * 4,
            "feature_availability_status": ["synthetic_test_only"] * 4,
        }).to_csv(path, index=False)

    def _write_canonical_scores(self, path: Path, horizon: str, bias: float = 0.0) -> None:
        threshold = 0.5
        probabilities = [min(1.0, max(0.0, p + bias)) for p in self.probabilities]
        pd.DataFrame({
            "flight_id": self.ids,
            "forecast_horizon": [horizon] * 4,
            "delay_15": self.targets,
            "partition": ["test"] * 4,
            "delay_probability": probabilities,
            "decision_threshold": [threshold] * 4,
            "predicted_delay_15": [int(p >= threshold) for p in probabilities],
            "score_status": ["scored"] * 4,
            "unavailable_reason": [""] * 4,
        }).to_csv(path, index=False)

    def test_join_preserves_cohort_and_marks_ambiguous_id_unavailable(self) -> None:
        mapping_path = self.root / "mapping.csv"
        pd.DataFrame({
            "cleaned_input_row_ordinal": [1, 2, 3, 4, 5],
            "flight_id": [*self.ids, self.ids[3]],
        }).to_csv(mapping_path, index=False)
        mapping, duplicate_ids = joiner.validate_mapping(
            mapping_path,
            ordinal_column="cleaned_input_row_ordinal",
            id_column="flight_id",
            expected_source_rows=5,
            expected_sha256=None,
        )
        membership_path = self.root / "membership.csv"
        pd.DataFrame({"flight_id": self.ids, "delay_15": self.targets}).to_csv(membership_path, index=False)
        scores_path = self.root / "scores.csv"
        self._write_ordinal_scores(scores_path, "T−2h")

        result = joiner.rekey_one_horizon(
            "T−2h", membership_path, scores_path, mapping, duplicate_ids,
            id_column="flight_id", ordinal_column="cleaned_input_row_ordinal",
            expected_members=4, output_dir=self.root / "joined",
        )
        joined = pd.read_csv(result["output"], dtype={"flight_id": "string"})
        self.assertEqual(joined["flight_id"].tolist(), self.ids)
        self.assertEqual(result["scored_rows"], 3)
        self.assertEqual(result["unavailable_rows"], 1)
        row = joined.loc[joined["flight_id"].eq(self.ids[3])].iloc[0]
        self.assertEqual(row["score_status"], "unavailable")
        self.assertEqual(row["unavailable_reason"], "canonical_mapping_not_one_to_one")
        self.assertTrue(pd.isna(row["delay_probability"]))

    def test_mapping_rejects_duplicate_source_ordinals(self) -> None:
        path = self.root / "bad_mapping.csv"
        pd.DataFrame({
            "cleaned_input_row_ordinal": [1, 1, 3],
            "flight_id": self.ids[:3],
        }).to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, "exactly one row per"):
            joiner.validate_mapping(
                path, ordinal_column="cleaned_input_row_ordinal", id_column="flight_id",
                expected_source_rows=3, expected_sha256=None,
            )

    def test_mapping_rejects_numeric_equivalent_ordinal_spellings(self) -> None:
        path = self.root / "normalized_duplicate_mapping.csv"
        pd.DataFrame({
            "cleaned_input_row_ordinal": ["1", "1.0", "3"],
            "flight_id": self.ids[:3],
        }).to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, "one row per nonmissing source ordinal"):
            joiner.validate_mapping(
                path, ordinal_column="cleaned_input_row_ordinal", id_column="flight_id",
                expected_source_rows=3, expected_sha256=None,
            )

    def test_join_rejects_score_ordinal_outside_verified_source(self) -> None:
        mapping_path = self.root / "mapping.csv"
        pd.DataFrame({
            "cleaned_input_row_ordinal": [1, 2, 3, 4],
            "flight_id": self.ids,
        }).to_csv(mapping_path, index=False)
        mapping, duplicate_ids = joiner.validate_mapping(
            mapping_path, ordinal_column="cleaned_input_row_ordinal", id_column="flight_id",
            expected_source_rows=4, expected_sha256=None,
        )
        membership_path = self.root / "membership.csv"
        pd.DataFrame({"flight_id": self.ids, "delay_15": self.targets}).to_csv(membership_path, index=False)
        scores_path = self.root / "scores.csv"
        self._write_ordinal_scores(scores_path, "T−2h")
        scores = pd.read_csv(scores_path)
        extra = scores.iloc[[0]].copy()
        extra["cleaned_input_row_ordinal"] = 5
        scores = pd.concat([scores, extra], ignore_index=True)
        scores.to_csv(scores_path, index=False)
        with self.assertRaisesRegex(ValueError, "outside the verified source mapping"):
            joiner.rekey_one_horizon(
                "T−2h", membership_path, scores_path, mapping, duplicate_ids,
                id_column="flight_id", ordinal_column="cleaned_input_row_ordinal",
                expected_members=4, output_dir=self.root / "joined",
            )

    def test_join_rejects_missing_or_nonbinary_score_target(self) -> None:
        mapping_path = self.root / "mapping.csv"
        pd.DataFrame({"cleaned_input_row_ordinal": [1, 2, 3, 4], "flight_id": self.ids}).to_csv(mapping_path, index=False)
        mapping, duplicate_ids = joiner.validate_mapping(
            mapping_path, ordinal_column="cleaned_input_row_ordinal", id_column="flight_id",
            expected_source_rows=4, expected_sha256=None,
        )
        membership_path = self.root / "membership.csv"
        pd.DataFrame({"flight_id": self.ids, "delay_15": self.targets}).to_csv(membership_path, index=False)
        scores_path = self.root / "scores.csv"
        self._write_ordinal_scores(scores_path, "T−2h")
        scores = pd.read_csv(scores_path)
        scores.loc[0, "delay_15"] = 2
        scores.to_csv(scores_path, index=False)
        with self.assertRaisesRegex(ValueError, "complete and binary"):
            joiner.rekey_one_horizon(
                "T−2h", membership_path, scores_path, mapping, duplicate_ids,
                id_column="flight_id", ordinal_column="cleaned_input_row_ordinal",
                expected_members=4, output_dir=self.root / "joined",
            )

    def test_end_to_end_matched_metrics_for_three_independent_variants(self) -> None:
        horizon = "T−30m"
        slug = "t_minus_30m"
        mapping_path = self.root / "mapping.csv"
        pd.DataFrame({
            "cleaned_input_row_ordinal": [1, 2, 3, 4],
            "flight_id": self.ids,
        }).to_csv(mapping_path, index=False)
        mapping, duplicate_ids = joiner.validate_mapping(
            mapping_path, ordinal_column="cleaned_input_row_ordinal", id_column="flight_id",
            expected_source_rows=4, expected_sha256=None,
        )
        membership_path = self.root / "membership.csv"
        cohort_path = self.root / "cohort.csv"
        cohort = pd.DataFrame({
            "flight_id": self.ids, "delay_15": self.targets, "partition": ["test"] * 4,
        })
        cohort.to_csv(cohort_path, index=False)
        cohort[["flight_id", "delay_15"]].to_csv(membership_path, index=False)
        ordinal_scores = self.root / "airport_ordinal.csv"
        self._write_ordinal_scores(ordinal_scores, horizon)
        joined_result = joiner.rekey_one_horizon(
            horizon, membership_path, ordinal_scores, mapping, duplicate_ids,
            id_column="flight_id", ordinal_column="cleaned_input_row_ordinal",
            expected_members=4, output_dir=self.root / "joined",
        )

        fw_path = self.root / "flight_weather.csv"
        combined_path = self.root / "combined.csv"
        self._write_canonical_scores(fw_path, horizon, bias=0.02)
        self._write_canonical_scores(combined_path, horizon, bias=-0.02)
        report = evaluator.evaluate(
            cohort_path,
            {
                "flight_weather": fw_path,
                "airport_network": Path(joined_result["output"]),
                "combined": combined_path,
            },
            horizon,
            self.root / "comparison" / "metrics",
        )
        self.assertEqual(report["approved_cohort_rows"], 4)
        self.assertEqual(report["matched_all_three_scored_rows"], 4)
        self.assertEqual(set(report["matched_metrics"]), set(evaluator.MODELS))

    def test_evaluator_rejects_incomplete_model_cohort(self) -> None:
        cohort_path = self.root / "cohort.csv"
        pd.DataFrame({"flight_id": self.ids, "delay_15": self.targets}).to_csv(cohort_path, index=False)
        predictions = self.root / "predictions.csv"
        self._write_canonical_scores(predictions, "T−2h")
        pd.DataFrame({
            "flight_id": self.ids[:-1], "score_status": ["scored"] * 3,
            "delay_probability": [0.2, 0.8, 0.3], "decision_threshold": [0.5] * 3,
            "predicted_delay_15": [0, 1, 0], "unavailable_reason": [""] * 3,
        }).to_csv(predictions, index=False)
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            evaluator._validate_predictions("flight_weather", predictions, "T−2h", evaluator._validate_cohort(cohort_path))

    def test_evaluator_rejects_missing_horizon_values(self) -> None:
        cohort_path = self.root / "cohort.csv"
        pd.DataFrame({"flight_id": self.ids, "delay_15": self.targets}).to_csv(cohort_path, index=False)
        predictions = self.root / "predictions.csv"
        self._write_canonical_scores(predictions, "T−2h")
        frame = pd.read_csv(predictions)
        frame.loc[0, "forecast_horizon"] = pd.NA
        frame.to_csv(predictions, index=False)
        with self.assertRaisesRegex(ValueError, "missing values"):
            evaluator._validate_predictions("flight_weather", predictions, "T−2h", evaluator._validate_cohort(cohort_path))

    def test_evaluator_rejects_whitespace_only_canonical_id(self) -> None:
        cohort_path = self.root / "cohort.csv"
        pd.DataFrame({"flight_id": [self.ids[0], " ", self.ids[2], self.ids[3]], "delay_15": self.targets}).to_csv(cohort_path, index=False)
        with self.assertRaisesRegex(ValueError, "unique and nonmissing"):
            evaluator._validate_cohort(cohort_path)

    def test_evaluator_rejects_target_disagreement(self) -> None:
        cohort_path = self.root / "cohort.csv"
        pd.DataFrame({"flight_id": self.ids, "delay_15": self.targets}).to_csv(cohort_path, index=False)
        predictions = self.root / "predictions.csv"
        self._write_canonical_scores(predictions, "T−2h")
        frame = pd.read_csv(predictions)
        frame.loc[0, "delay_15"] = 1
        frame.to_csv(predictions, index=False)
        with self.assertRaisesRegex(ValueError, "target values disagree"):
            evaluator._validate_predictions("flight_weather", predictions, "T−2h", evaluator._validate_cohort(cohort_path))


if __name__ == "__main__":
    unittest.main()
