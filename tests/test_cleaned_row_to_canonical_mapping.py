import csv
import gzip
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from build_cleaned_row_to_canonical_mapping import (
    AMBIGUOUS_FIELDS, CROSS_FIELDS, DUPLICATE_FIELDS, EXPECTED_COUNTS,
    OUTPUT_FIELDS, SOURCE_FIELDS, UNMATCHED_FIELDS, build_mapping,
)

ID1 = "bts-flight-v1:" + "1" * 64
ID2 = "bts-flight-v1:" + "2" * 64
HA, HU, HI, H1, H2 = ("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64)


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_gz(path, fields, rows):
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
            writer = csv.DictWriter(text, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            text.flush()
            text.detach()


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ident, self.recon = self.root / "ident", self.root / "recon"
        self.ident.mkdir()
        self.recon.mkdir()
        self.output, self.source = self.root / "phase2_handoff_2024", self.root / "cleaned.csv.gz"
        self.values = [
            ["2024","1","1","2024-01-01","UA","LAX","JFK","5","4","-1","0","0","0"],
            ["2024","1","2","2024-01-02","AA","LAX","JFK","6","6","0","0","0","0"],
            ["2024","1","2","2024-01-02","AA","LAX","JFK","6","6","0","0","0","0"],
            ["2024","1","3","2024-01-03","DL","SEA","SFO","7","7","0","0","0","0"],
            ["2024","1","4","2024-01-04","F9","MIA","ATL","8","8","0","0","0","0"],
            ["2024","1","5","2024-01-05","UA","LAX","BOS","9","9","0","0","0","0"],
        ]
        self.write_source()
        self.cross = [
            self.crow(1,H1,1,1,ID1,"unique_exact_raw_match_identity_recovered",1,"r1"),
            self.crow(2,HA,2,2,"","ambiguous_multiple_raw_candidates",2,""),
            self.crow(3,HA,2,2,"","ambiguous_multiple_raw_candidates",2,""),
            self.crow(4,HU,4,1,"","no_exact_raw_match",0,""),
            self.crow(5,HI,5,1,"","unique_exact_raw_match_identity_incomplete",1,"rq"),
            self.crow(6,H2,6,1,ID2,"unique_exact_raw_match_identity_recovered",1,"r6"),
        ]
        self.write_cross()
        self.write_small_artifacts()

    def tearDown(self):
        self.tmp.cleanup()

    def write_source(self):
        write_gz(self.source, SOURCE_FIELDS, [dict(zip(SOURCE_FIELDS, x)) for x in self.values])

    @staticmethod
    def crow(n, h, first, count, fid, status, candidates, raw):
        return {
            "cleaned_input_row_ordinal": str(n), "cleaned_row_sha256": h,
            "cleaned_duplicate_first_ordinal": str(first), "cleaned_duplicate_group_occurrences": str(count),
            "canonical_flight_id": fid, "identity_version": "bts-flight-v1" if fid else "",
            "match_status": status, "raw_candidate_count": str(candidates), "reconciliation_raw_row_id": raw,
        }

    def write_cross(self, fields=CROSS_FIELDS):
        write_gz(self.ident / "cleaned_to_canonical_crosswalk.csv.gz", fields,
                 [{k:r.get(k,"") for k in fields} for r in self.cross])

    def write_small_artifacts(self):
        canonical_ids = sorted({
            r["canonical_flight_id"] for r in self.cross
            if r.get("canonical_flight_id")
        })
        write_gz(
            self.ident / "canonical_flights.csv.gz",
            ("flight_id", "identity_version"),
            [{"flight_id": fid, "identity_version": "bts-flight-v1"} for fid in canonical_ids],
        )
        write_csv(self.ident / "identity_quarantine.csv", ("reconciliation_raw_row_id",),
                  [{"reconciliation_raw_row_id":"rq"}])
        write_csv(self.ident / "ambiguous_cleaned_matches.csv", AMBIGUOUS_FIELDS, [{
            "cleaned_first_data_row_ordinal":"2","cleaned_row_sha256":HA,
            "cleaned_duplicate_row_ordinals":"2;3","raw_candidate_count":"2",
        }])
        write_csv(self.ident / "unmatched_cleaned_rows.csv", UNMATCHED_FIELDS, [{
            "cleaned_first_data_row_ordinal":"4","cleaned_row_sha256":HU,
            "cleaned_duplicate_row_ordinals":"4",
        }])
        write_csv(self.recon / "cleaned_duplicate_groups.csv", DUPLICATE_FIELDS, [{
            "row_sha256":HA,"occurrences":"2","duplicate_rows_beyond_first":"1",
            "original_data_row_ordinals":"2;3","sha256_collision_checked":"True",
        }])

    def run_build(self):
        counts = dict(EXPECTED_COUNTS)
        counts["unique_exact_raw_match_identity_recovered"] = 2
        counts["ambiguous_multiple_raw_candidates"] = 2
        counts["unique_exact_raw_match_identity_incomplete"] = 1
        counts["no_exact_raw_match"] = 1
        return build_mapping(self.source,self.ident,self.recon,self.output,expected_rows=6,
                             expected_status_counts=counts,expected_hashes=None)

    def test_pinned_compressed_source_hash_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "cleaned_input SHA-256 mismatch"):
            build_mapping(self.source,self.ident,self.recon,self.output,expected_rows=6,
                          expected_status_counts={"unique_exact_raw_match_identity_recovered":2,"ambiguous_multiple_raw_candidates":2,"unique_exact_raw_match_identity_incomplete":1,"no_exact_raw_match":1},
                          expected_hashes={"cleaned_input_compressed":"0"*64})
        self.assertFalse(self.output.exists())

    def test_crosswalk_id_absent_from_canonical_table_fails(self):
        write_gz(
            self.ident / "canonical_flights.csv.gz",
            ("flight_id", "identity_version"),
            [{"flight_id": ID1, "identity_version": "bts-flight-v1"}],
        )
        with self.assertRaisesRegex(ValueError, "absent from canonical table"):
            self.run_build()

    def test_mapping_ordinals_statuses_and_checksums(self):
        result = self.run_build()
        self.assertEqual(result["output"]["rows"], 6)
        self.assertEqual(result["output"]["columns"], list(OUTPUT_FIELDS))
        with gzip.open(self.output / "cleaned_row_to_canonical_mapping.csv.gz","rt",encoding="utf-8",newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["cleaned_input_row_ordinal"] for r in rows],[str(i) for i in range(1,7)])
        self.assertEqual([rows[i]["canonical_flight_id"] for i in (1,2,3,4)],["","","",""])
        manifest = json.loads((self.output / "mapping_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["output"]["sha256"],result["output"]["sha256"])
        self.assertEqual(manifest["output"]["compressed_bytes"],result["output"]["compressed_bytes"])
        for line in (self.output / "checksums_manifest.txt").read_text(encoding="utf-8").splitlines():
            digest,name = line.split("  ",1)
            self.assertEqual(hashlib.sha256((self.output/name).read_bytes()).hexdigest(),digest)

    def test_duplicate_ambiguous_original_rows_remain_separate_and_unmapped(self):
        self.run_build()
        with gzip.open(self.output / "cleaned_row_to_canonical_mapping.csv.gz","rt",encoding="utf-8",newline="") as f:
            rows=list(csv.DictReader(f))
        self.assertEqual([(rows[i]["cleaned_input_row_ordinal"],rows[i]["match_status"],rows[i]["canonical_flight_id"]) for i in (1,2)],
                         [("2","ambiguous_multiple_raw_candidates",""),("3","ambiguous_multiple_raw_candidates","")])

    def test_missing_or_duplicated_crosswalk_ordinal_fails(self):
        for bad in ("1","4"):
            self.cross[2]["cleaned_input_row_ordinal"]=bad
            self.write_cross()
            with self.assertRaisesRegex(ValueError,"ordinal"):
                self.run_build()
            self.cross[2]["cleaned_input_row_ordinal"]="3"

    def test_missing_or_inconsistent_crosswalk_fields_fail(self):
        self.write_cross(tuple(x for x in CROSS_FIELDS if x != "canonical_flight_id"))
        with self.assertRaisesRegex(ValueError,"missing required fields"):
            self.run_build()
        self.write_cross()
        self.cross[1]["canonical_flight_id"]=ID1
        self.write_cross()
        with self.assertRaisesRegex(ValueError,"ambiguous row"):
            self.run_build()

    def test_duplicate_canonical_id_fails(self):
        self.cross[5]["canonical_flight_id"]=ID1
        self.write_cross()
        with self.assertRaisesRegex(ValueError,"duplicate canonical_flight_id"):
            self.run_build()

    def test_duplicate_provenance_must_match_actual_records(self):
        self.values[2][5]="BOS"
        self.write_source()
        with self.assertRaisesRegex(ValueError,"duplicate source records differ"):
            self.run_build()


if __name__ == "__main__":
    unittest.main()
