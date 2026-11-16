"""Unittest-discoverable checks for the Airport/Network feature builder."""

import hashlib
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from airport_network_feature_builder import build_airport_network_features, horizon_features


def _cid(label: str) -> str:
    return f"bts-flight-v1:{hashlib.sha256(label.encode()).hexdigest()}"


def example_flights():
    return pd.DataFrame({
        "flight_id": [_cid(f"builder-test-{i}") for i in range(6)],
        "FL_DATE": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"],
        "MONTH": [1, 1, 1, 1, 1, 1],
        "DAY_OF_MONTH": [1, 1, 2, 2, 3, 3],
        "DEP_HOUR": [8, 9, 8, 9, 8, 9],
        "DEP_MINUTE": [0, 0, 0, 0, 0, 0],
        "OP_UNIQUE_CARRIER": ["UA"] * 6,
        "ORIGIN": ["SFO"] * 5 + ["LAX"],
        "DEST": ["ORD", "LAX", "JFK", "SEA", "BOS", "SFO"],
        "DEP_DELAY": [10, 20, 0, 30, 15, 5],
        "CANCELLED": [0, 0, 0, 0, 0, 0],
        "delay_15": [0, 1, 0, 1, 1, 0],
    })


class AirportNetworkFeatureBuilderTests(unittest.TestCase):
    def test_features_preserve_row_keys_and_use_prior_dates(self):
        flights = example_flights()
        features = build_airport_network_features(flights)
        self.assertEqual(features["flight_id"].tolist(), flights["flight_id"].tolist())
        self.assertEqual(features.loc[2, "airport_prior7_flights"], 2)
        self.assertEqual(features.loc[2, "airport_prior7_delay_rate"], 0.5)
        self.assertEqual(features.loc[2, "airport_prior7_avg_destinations_per_active_day"], 2)
        self.assertEqual(features.loc[5, "airport_prior7_flights"], 0)

    def test_schedule_and_recent_windows_are_horizon_specific(self):
        features = build_airport_network_features(example_flights())
        # The 09:00 SFO flight sees the 08:00 schedule and the 08:00 flight's
        # realized 08:10 departure only at the T−30m cutoff.
        self.assertEqual(features.loc[1, "scheduled_origin_departures_prior2h"], 1)
        self.assertEqual(features.loc[1, "tminus2h_recent_departures_2h"], 0)
        self.assertEqual(features.loc[1, "tminus30m_recent_departures_2h"], 1)
        self.assertEqual(len(horizon_features("T−2h")), 15)
        self.assertEqual(len(horizon_features("T−30m")), 15)


if __name__ == "__main__":
    unittest.main()
