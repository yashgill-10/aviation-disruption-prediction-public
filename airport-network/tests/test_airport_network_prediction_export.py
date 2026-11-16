"""Tests for canonical-ID Airport/Network prediction and coverage exports."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from airport_network_prediction_export import export_airport_network_predictions  # noqa: E402


def _cid(label: str) -> str:
    return f"bts-flight-v1:{hashlib.sha256(label.encode()).hexdigest()}"


ID_901 = _cid("export-901")
ID_305 = _cid("export-305")
ID_712 = _cid("export-712")
ALL_IDS = [ID_901, ID_305, ID_712]


class MockBinaryModel:
    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        p = frame["airport_prior7_delay_rate"].to_numpy(dtype=float)
        return np.column_stack([1 - p, p])


class AirportNetworkPredictionExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canonical = pd.DataFrame({
            "flight_id": list(ALL_IDS),
            "delay_15": [0, 1, 0],
            "partition": ["test", "test", "test"],
        })
        self.bundle = {
            "models": {"T−2h": MockBinaryModel(), "T−30m": MockBinaryModel()},
            "thresholds": {"T−2h": 0.5, "T−30m": 0.7},
            "base_features": [],
            "network_features": ["airport_prior7_delay_rate"],
            "recent_features": {
                "T−2h": ["recent_t2h"],
                "T−30m": ["recent_t30m"],
            },
        }
        self.features = {
            "T−2h": pd.DataFrame({
                "flight_id": [ID_712, ID_901],
                "airport_prior7_delay_rate": [0.8, 0.2],
                "recent_t2h": [2, 1],
            }),
            "T−30m": pd.DataFrame({
                "flight_id": [ID_901, ID_712],
                "airport_prior7_delay_rate": [0.2, 0.8],
                "recent_t30m": [5, 6],
            }),
        }
        self.eligibility = {
            h: pd.DataFrame({
                "flight_id": [ID_712, ID_901, ID_305],
                "eligible_for_scoring": [True, True, False],
                "unavailable_reason": ["", "", "quarantined_identity"],
            })
            for h in ("T−2h", "T−30m")
        }

    def test_preserves_ids_order_and_reports_missing_scores_for_each_horizon(self):
        predictions, coverage = export_airport_network_predictions(
            self.canonical, self.features, self.eligibility, self.bundle
        )
        self.assertEqual(predictions["forecast_horizon"].drop_duplicates().tolist(), ["T−2h", "T−30m"])
        for horizon in ("T−2h", "T−30m"):
            current = predictions.loc[predictions["forecast_horizon"].eq(horizon)]
            self.assertEqual(current["flight_id"].tolist(), ALL_IDS)
            self.assertEqual(current["delay_15"].tolist(), [0, 1, 0])
            self.assertEqual(current.iloc[1]["score_status"], "unavailable")
            self.assertEqual(current.iloc[1]["unavailable_reason"], "quarantined_identity")
            self.assertTrue(pd.isna(current.iloc[1]["delay_probability"]))
            self.assertTrue(pd.isna(current.iloc[1]["predicted_delay_15"]))
            self.assertEqual(current["feature_availability"].unique().tolist(), ["retrospective_bts_availability_unverified"])
        t2 = predictions.loc[predictions["forecast_horizon"].eq("T−2h")].set_index("flight_id")
        self.assertEqual(t2.loc[ID_901, "predicted_delay_15"], 0)
        self.assertEqual(t2.loc[ID_712, "predicted_delay_15"], 1)
        self.assertEqual(coverage["expected_flights"].tolist(), [3, 3])
        self.assertEqual(coverage["scored_flights"].tolist(), [2, 2])
        self.assertEqual(coverage["unavailable_flights"].tolist(), [1, 1])
        self.assertEqual(coverage["score_coverage"].tolist(), [2 / 3, 2 / 3])
        self.assertIn("quarantined_identity=1", coverage.loc[0, "unavailable_reason_counts"])

    def test_missing_feature_row_and_null_model_input_get_explicit_reasons(self):
        self.features["T−2h"] = self.features["T−2h"].copy()
        self.features["T−2h"].loc[self.features["T−2h"]["flight_id"].eq(ID_901), "airport_prior7_delay_rate"] = np.nan
        predictions, coverage = export_airport_network_predictions(
            self.canonical, self.features, self.eligibility, self.bundle
        )
        t2 = predictions.loc[predictions["forecast_horizon"].eq("T−2h")].set_index("flight_id")
        self.assertEqual(t2.loc[ID_901, "unavailable_reason"], "required_model_values_missing:airport_prior7_delay_rate")
        self.assertEqual(t2.loc[ID_712, "score_status"], "scored")
        self.assertIn("required_model_values_missing:airport_prior7_delay_rate=1", coverage.loc[0, "unavailable_reason_counts"])
        self.features["T−30m"] = self.features["T−30m"].loc[self.features["T−30m"]["flight_id"].ne(ID_712)]
        predictions, _ = export_airport_network_predictions(
            self.canonical, self.features, self.eligibility, self.bundle
        )
        t30 = predictions.loc[predictions["forecast_horizon"].eq("T−30m")].set_index("flight_id")
        self.assertEqual(t30.loc[ID_712, "unavailable_reason"], "feature_row_missing")
        self.assertTrue(pd.isna(t30.loc[ID_712, "delay_probability"]))

    def test_rejects_duplicate_ids_and_incomplete_eligibility(self):
        duplicate = self.canonical.copy()
        duplicate.loc[2, "flight_id"] = ID_901
        with self.assertRaisesRegex(ValueError, "duplicates include"):
            export_airport_network_predictions(duplicate, self.features, self.eligibility, self.bundle)
        incomplete = dict(self.eligibility)
        incomplete["T−2h"] = incomplete["T−2h"].iloc[:-1]
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            export_airport_network_predictions(self.canonical, self.features, incomplete, self.bundle)

    def test_rejects_whitespace_only_key(self):
        invalid = self.canonical.copy()
        invalid["flight_id"] = invalid["flight_id"].astype("string")
        invalid.loc[0, "flight_id"] = "   "
        with self.assertRaisesRegex(ValueError, "missing or blank keys"):
            export_airport_network_predictions(invalid, self.features, self.eligibility, self.bundle)

    def test_rejects_null_keys_in_every_keyed_input(self):
        for table_name in ("canonical", "features", "eligibility"):
            with self.subTest(table=table_name):
                canonical = self.canonical.copy()
                features = {h: frame.copy() for h, frame in self.features.items()}
                eligibility = {h: frame.copy() for h, frame in self.eligibility.items()}
                if table_name == "canonical":
                    canonical["flight_id"] = canonical["flight_id"].astype(object)
                    canonical.loc[0, "flight_id"] = None
                elif table_name == "features":
                    features["T−2h"]["flight_id"] = features["T−2h"]["flight_id"].astype(object)
                    features["T−2h"].loc[0, "flight_id"] = None
                else:
                    eligibility["T−2h"]["flight_id"] = eligibility["T−2h"]["flight_id"].astype(object)
                    eligibility["T−2h"].loc[0, "flight_id"] = None
                with self.assertRaisesRegex(ValueError, "missing or blank keys"):
                    export_airport_network_predictions(
                        canonical, features, eligibility, self.bundle
                    )

    def test_rejects_ineligible_row_without_reason_and_noncanonical_feature_id(self):
        bad_status = dict(self.eligibility)
        bad_status["T−2h"] = bad_status["T−2h"].copy()
        bad_status["T−2h"].loc[bad_status["T−2h"]["flight_id"].eq(ID_305), "unavailable_reason"] = " "
        with self.assertRaisesRegex(ValueError, "needs an explicit reason"):
            export_airport_network_predictions(self.canonical, self.features, bad_status, self.bundle)
        bad_features = dict(self.features)
        bad_features["T−30m"] = bad_features["T−30m"].copy()
        bad_features["T−30m"].loc[0, "flight_id"] = _cid("export-9999")
        with self.assertRaisesRegex(ValueError, "absent from canonical_flights"):
            export_airport_network_predictions(self.canonical, bad_features, self.eligibility, self.bundle)

    def test_nonfinite_numeric_model_value_is_unavailable_not_a_zero_score(self):
        self.features["T−2h"] = self.features["T−2h"].copy()
        self.features["T−2h"].loc[self.features["T−2h"]["flight_id"].eq(ID_901), "airport_prior7_delay_rate"] = np.inf
        predictions, _ = export_airport_network_predictions(
            self.canonical, self.features, self.eligibility, self.bundle
        )
        row = predictions.loc[
            predictions["forecast_horizon"].eq("T−2h") & predictions["flight_id"].eq(ID_901)
        ].iloc[0]
        self.assertEqual(row["score_status"], "unavailable")
        self.assertEqual(row["unavailable_reason"], "required_model_values_nonfinite:airport_prior7_delay_rate")
        self.assertTrue(pd.isna(row["delay_probability"]))
        self.assertTrue(pd.isna(row["predicted_delay_15"]))


    def test_preliminary_export_preserves_cleaned_input_ordinals(self):
        ordinal_ids = [901, 305, 712]
        base = self.canonical.copy()
        base["cleaned_input_row_ordinal"] = ordinal_ids
        base = base.drop(columns=["flight_id"])
        features = {
            horizon: frame.assign(cleaned_input_row_ordinal=[712, 901] if horizon == "T−2h" else [901, 712])
            .drop(columns=["flight_id"])
            for horizon, frame in self.features.items()
        }
        # Rebuild eligibility with ordinals aligned to canonical order [901, 305, 712].
        ordinal_eligibility = {}
        for horizon, frame in self.eligibility.items():
            order = {v: i for i, v in enumerate(ALL_IDS)}
            frame = frame.copy()
            frame["cleaned_input_row_ordinal"] = frame["flight_id"].map(
                lambda v: ordinal_ids[order[v]]
            )
            frame = frame.drop(columns=["flight_id"])
            ordinal_eligibility[horizon] = frame
        predictions, coverage = export_airport_network_predictions(
            base, features, ordinal_eligibility, self.bundle,
            key_column="cleaned_input_row_ordinal",
        )
        self.assertIn("cleaned_input_row_ordinal", predictions.columns)
        self.assertNotIn("flight_id", predictions.columns)
        for horizon in ("T−2h", "T−30m"):
            current = predictions.loc[predictions["forecast_horizon"].eq(horizon)]
            self.assertEqual(current["cleaned_input_row_ordinal"].tolist(), [901, 305, 712])
        self.assertEqual(coverage["expected_flights"].tolist(), [3, 3])

    def test_canonical_export_rejects_noncanonical_flight_id_formats(self):
        hex64 = "a" * 64
        cases = {
            "numeric_id": 12345,
            "wrong_namespace": f"flight-v1:{hex64}",
            "wrong_version_prefix": f"bts-flight-v2:{hex64}",
            "unprefixed_digest": hex64,
            "short_digest": f"bts-flight-v1:{'a' * 63}",
            "long_digest": f"bts-flight-v1:{'a' * 65}",
            "non_hex_digest": f"bts-flight-v1:{'g' * 64}",
            "uppercase_digest": f"bts-flight-v1:{'A' * 64}",
            "padded_valid_id": f"  {_cid('export-901')}  ",
        }
        for case, bad_id in cases.items():
            with self.subTest(case=case):
                canonical = self.canonical.copy()
                canonical["flight_id"] = canonical["flight_id"].astype(object)
                canonical.loc[0, "flight_id"] = bad_id
                with self.assertRaisesRegex(ValueError, "canonical bts-flight-v1"):
                    export_airport_network_predictions(
                        canonical, self.features, self.eligibility, self.bundle
                    )

    def test_canonical_export_rejects_bad_ids_in_features_and_eligibility(self):
        bad_id = f"not-canonical:{'0' * 64}"
        for table_name in ("features", "eligibility"):
            with self.subTest(table=table_name):
                features = {h: frame.copy() for h, frame in self.features.items()}
                eligibility = {h: frame.copy() for h, frame in self.eligibility.items()}
                if table_name == "features":
                    features["T−2h"].loc[0, "flight_id"] = bad_id
                else:
                    eligibility["T−2h"].loc[0, "flight_id"] = bad_id
                with self.assertRaisesRegex(ValueError, "canonical bts-flight-v1"):
                    export_airport_network_predictions(
                        self.canonical, features, eligibility, self.bundle
                    )

    def test_canonical_export_accepts_valid_bts_flight_v1_ids(self):
        predictions, coverage = export_airport_network_predictions(
            self.canonical, self.features, self.eligibility, self.bundle
        )
        self.assertEqual(set(predictions["flight_id"]), set(ALL_IDS))
        for horizon in ("T−2h", "T−30m"):
            self.assertEqual(
                coverage.loc[coverage["forecast_horizon"].eq(horizon), "expected_flights"].iloc[0],
                3,
            )

if __name__ == "__main__":
    unittest.main()
