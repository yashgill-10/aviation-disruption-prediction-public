from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from score_contract import adapt_canonical_flight_id, validate_score_rows


ID = "bts-flight-v1:" + "a" * 64


class ScoreContractTests(unittest.TestCase):
    def test_adapter_preserves_hashed_id_as_string(self) -> None:
        result = adapt_canonical_flight_id(pd.DataFrame({"canonical_flight_id": [ID]}))
        self.assertEqual(result.loc[0, "flight_id"], ID)

    def test_adapter_rejects_disagreeing_aliases(self) -> None:
        with self.assertRaisesRegex(ValueError, "disagree"):
            adapt_canonical_flight_id(pd.DataFrame({"flight_id": [ID], "canonical_flight_id": [ID[:-1] + "b"]}))

    def test_validation_reports_duplicate_keys(self) -> None:
        frame = pd.DataFrame({"flight_id": [ID, ID], "horizon": ["t_minus_2h"] * 2,
                              "score_status": ["scored", "scored"], "p_delay15": [.2, .3]})
        self.assertEqual(validate_score_rows(frame)["duplicate_flight_horizon_keys"], 1)


if __name__ == "__main__":
    unittest.main()
