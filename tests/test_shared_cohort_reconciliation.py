import decimal
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audit_canonical_flights_2024 import fid, ident
from reconcile_shared_cohort_2024 import (
    eligible_at_horizon,
    make_flight_id,
    match_status,
    nested_id_set_comparison,
)


class SharedCohortTests(unittest.TestCase):
    def test_retrospective_cutoffs_are_strict(self):
        self.assertFalse(eligible_at_horizon(decimal.Decimal("-120"), 120))
        self.assertTrue(eligible_at_horizon(decimal.Decimal("-119"), 120))
        self.assertFalse(eligible_at_horizon(decimal.Decimal("-30"), 30))
        self.assertTrue(eligible_at_horizon(decimal.Decimal("-29"), 30))
        self.assertFalse(eligible_at_horizon(None, 30))

    def test_horizon_id_intersection_and_differences(self):
        result = nested_id_set_comparison(100, 70, 70)
        self.assertEqual(result, {
            "t2h": 100, "t30m": 70, "intersection": 70,
            "t2h_only": 30, "t30m_only": 0,
        })

    def test_invalid_intersection_is_rejected(self):
        with self.assertRaises(ValueError):
            nested_id_set_comparison(100, 70, 71)

    def test_only_unique_complete_mapping_is_matched(self):
        self.assertEqual(
            match_status(2, 1, [], '{"identity":"valid"}'),
            "ambiguous_raw_candidates",
        )
        self.assertEqual(
            match_status(1, 1, ["Flight_Number_Reporting_Airline"], None),
            "unique_source_match_identity_incomplete",
        )
        self.assertEqual(
            match_status(1, 1, [], '{"identity":"valid"}'),
            "unique_exact_match_valid_canonical_id",
        )

    def test_id_reuses_canonical_identity_normalization(self):
        identity = {
            "FlightDate": "2024-01-01",
            "DOT_ID_Reporting_Airline": "19805",
            "Reporting_Airline": "UA",
            "Flight_Number_Reporting_Airline": "7",
            "OriginAirportID": "12892",
            "Origin": "LAX",
            "DestAirportID": "12478",
            "Dest": "JFK",
            "CRSDepTime": "5.0",
        }
        normalized, flight_id = make_flight_id(json.dumps(identity))
        self.assertEqual(flight_id, fid(ident(identity)))
        self.assertEqual(normalized["CRSDepTime"], "0005")


if __name__ == "__main__":
    unittest.main()
