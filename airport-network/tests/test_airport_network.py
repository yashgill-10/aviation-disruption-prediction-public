"""Small data-independent checks for the Airport/Network repo interface."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from airport_network_inference import predict_airport_network_delay  # noqa: E402
from airport_network_integration import (  # noqa: E402
    add_canonical_flight_id,
    attach_airport_network_predictions,
    chronological_partitions,
    combine_predeparture_features,
)


def _cid(label: str) -> str:
    return f"bts-flight-v1:{hashlib.sha256(label.encode()).hexdigest()}"


IDS = [_cid("airport-network-test-0"), _cid("airport-network-test-1"), _cid("airport-network-test-2")]


class MockBinaryModel:
    def predict_proba(self, frame: pd.DataFrame):
        p = frame["airport_prior7_delay_rate"].to_numpy(dtype=float)
        return np.column_stack([1 - p, p])


class AirportNetworkRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = add_canonical_flight_id(pd.DataFrame({
            "flight_id": list(IDS),
            "FL_DATE": ["2024-08-31", "2024-09-15", "2024-10-02"],
            "delay_15": [0, 1, 0],
            "ORIGIN": ["A", "B", "C"],
        }))
        self.airport = pd.DataFrame({
            "flight_id": self.base["flight_id"],
            "airport_prior7_delay_rate": [0.2, 0.6, 0.8],
            "recent_activity": [1, 2, 3],
        })
        self.weather = pd.DataFrame({"flight_id": self.base["flight_id"], "wind_speed": [3, 8, 5]})
        self.bundle = {
            "models": {"T−2h": MockBinaryModel(), "T−30m": MockBinaryModel()},
            "thresholds": {"T−2h": 0.5, "T−30m": 0.7},
            "base_features": [],
            "network_features": ["airport_prior7_delay_rate"],
            "recent_features": {
                "T−2h": ["recent_activity"],
                "T−30m": ["recent_activity"],
            },
        }

    def test_feature_join_and_chronological_partitions(self):
        combined = combine_predeparture_features(self.base, self.airport, self.weather)
        train, calibration, test = chronological_partitions(combined)
        self.assertEqual([len(train), len(calibration), len(test)], [1, 1, 1])
        self.assertEqual(combined["flight_id"].tolist(), IDS)

    def test_inference_and_dashboard_score_handoff_preserve_rows(self):
        scores = predict_airport_network_delay(self.airport, self.bundle, horizon="T−2h")
        flight_weather = pd.DataFrame({
            "flight_id": [IDS[2], IDS[0], IDS[1]],
            "flight_weather_delay_probability": [0.4, 0.1, 0.3],
        })
        combined = attach_airport_network_predictions(
            flight_weather, scores, expected_horizon="T−2h"
        )
        self.assertEqual(combined["flight_id"].tolist(), [IDS[2], IDS[0], IDS[1]])
        self.assertEqual(
            combined["airport_network_delay_probability"].tolist(), [0.8, 0.2, 0.6]
        )
        self.assertEqual(
            combined["airport_network_forecast_horizon"].tolist(), ["T−2h"] * 3
        )

    def test_score_handoff_rejects_wrong_horizon_and_key_coverage(self):
        scores = predict_airport_network_delay(self.airport, self.bundle, horizon="T−2h")
        with self.assertRaisesRegex(ValueError, "Expected only"):
            attach_airport_network_predictions(
                self.base, scores, expected_horizon="T−30m"
            )
        with self.assertRaisesRegex(ValueError, "exactly match"):
            attach_airport_network_predictions(self.base, scores.iloc[:-1])

    def test_canonical_id_generation_uses_bts_flight_v1_and_rejects_foreign_ids(self):
        bts_row = {
            "FlightDate": "2024-08-31",
            "DOT_ID_Reporting_Airline": "19805",
            "Reporting_Airline": "UA",
            "Flight_Number_Reporting_Airline": "1001",
            "OriginAirportID": "13780",
            "Origin": "SFO",
            "DestAirportID": "13940",
            "Dest": "JFK",
            "CRSDepTime": "0830",
        }
        frame = pd.DataFrame([{**bts_row, "FL_DATE": "2024-08-31", "delay_15": 0}])
        keyed = add_canonical_flight_id(frame)
        self.assertRegex(keyed["flight_id"].iloc[0], r"^bts-flight-v1:[0-9a-f]{64}$")
        with self.assertRaisesRegex(ValueError, "canonical"):
            add_canonical_flight_id(pd.DataFrame({
                "flight_id": [0],
                "FL_DATE": ["2024-08-31"],
            }))

    def test_score_handoff_rejects_invalid_probabilities_and_name_collisions(self):
        scores = predict_airport_network_delay(self.airport, self.bundle, horizon="T−2h")
        bad = scores.copy()
        bad.loc[0, "delay_probability"] = 1.2
        with self.assertRaisesRegex(ValueError, "probabilities"):
            attach_airport_network_predictions(self.base, bad)
        collision = self.base.assign(airport_network_delay_probability=0.5)
        with self.assertRaisesRegex(ValueError, "already exist"):
            attach_airport_network_predictions(collision, scores)


if __name__ == "__main__":
    unittest.main()
