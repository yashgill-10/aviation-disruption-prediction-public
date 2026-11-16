import json, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from audit_canonical_flights_2024 import ID_COLS, VERSION, fid, ident
from build_canonical_flight_table_2024 import classify_reuse, crosswalk_status, identity_from_json

def sample():
    return {"FlightDate":"2024-01-01","DOT_ID_Reporting_Airline":"19805","Reporting_Airline":"UA","Flight_Number_Reporting_Airline":"7","OriginAirportID":"12892","Origin":"LAX","DestAirportID":"12478","Dest":"JFK","CRSDepTime":"5.0"}

class IdentityTests(unittest.TestCase):
    def test_stable_versioned_id_and_normalization(self):
        r=sample(); n=ident(r)
        self.assertEqual(n[-1],"0005")
        self.assertEqual(fid(n),fid(ident(dict(r))))
        self.assertTrue(fid(n).startswith(VERSION+":"))
    def test_flight_number_and_scheduled_time_are_identity_components(self):
        r=sample(); base=fid(ident(r))
        self.assertNotEqual(base,fid(ident(dict(r,Flight_Number_Reporting_Airline="8"))))
        self.assertNotEqual(base,fid(ident(dict(r,CRSDepTime="10"))))
    def test_missing_identity_is_rejected(self):
        with self.assertRaises(ValueError): ident(dict(sample(),Flight_Number_Reporting_Airline=""))
    def test_duplicate_record_conflict_and_hash_collision(self):
        n=json.dumps(ident(sample()),separators=(",",":")); prior=(n,"sig")
        self.assertEqual(classify_reuse(None,n,"sig"),"first")
        self.assertEqual(classify_reuse(prior,n,"sig"),"duplicate_identity_same_source_signature")
        self.assertEqual(classify_reuse(prior,n,"other"),"duplicate_identity_conflicting_source_records")
        other=json.dumps(ident(dict(sample(),Reporting_Airline="AA")),separators=(",",":"))
        with self.assertRaisesRegex(ValueError,"SHA-256 collision"): classify_reuse(prior,other,"x")
    def test_ambiguous_or_incomplete_crosswalk_never_gets_id(self):
        self.assertEqual(crosswalk_status(0,True),("no_exact_raw_match",False))
        self.assertEqual(crosswalk_status(2,True),("ambiguous_multiple_raw_candidates",False))
        self.assertEqual(crosswalk_status(1,False),("unique_exact_raw_match_identity_incomplete",False))
        self.assertEqual(crosswalk_status(1,True),("unique_exact_raw_match_identity_recovered",True))
    def test_identity_json_uses_existing_normalizer(self):
        r=sample(); vals,value=identity_from_json(json.dumps(r))
        self.assertEqual(vals,ident(r)); self.assertEqual(value,fid(vals)); self.assertEqual(len(ID_COLS),9)

if __name__=="__main__": unittest.main()
