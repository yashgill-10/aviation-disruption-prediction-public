#!/usr/bin/env python3
"""Verify and publish an already materialized canonical-flight staging directory."""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, hashlib, json, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from audit_canonical_flights_2024 import ID_COLS, VERSION, fid, ident

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def run_tests():
    cmd=[sys.executable,"-m","unittest","discover","-s","tests","-p","test_canonical_flight_identity.py","-v"]
    r=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,encoding="utf-8",errors="replace")
    output=(r.stdout or "")+(r.stderr or "")
    if r.returncode: raise RuntimeError("focused tests failed:\n"+output[-8000:])
    return {"status":"PASS","command":" ".join(cmd),"output":output[-8000:]}

def gzip_rows(path):
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        rd=csv.DictReader(f)
        if rd.fieldnames is None: raise ValueError("missing header "+path.name)
        for row in rd: yield rd.fieldnames,row

def build(stage,out,recon):
    if not stage.is_dir(): raise FileNotFoundError(stage)
    if out.exists(): raise FileExistsError("refusing to overwrite "+str(out))
    report=json.loads((recon/"audit_report.json").read_text(encoding="utf-8"))
    test_result=run_tests()
    expected_arch={int(a["month"]):(a["name"],a["sha256"],a["source_rows"]) for a in report["source_archives"]}
    verify_dir=stage/".verify_ids"
    if verify_dir.exists(): raise FileExistsError("preserving existing verifier temp directory")
    verify_dir.mkdir()
    handles=[(verify_dir/f"{i:x}.txt").open("w",encoding="ascii") for i in range(16)]
    canonical_n=0; month_counts=collections.Counter(); id_errors=0
    can_path=stage/"canonical_flights.csv.gz"
    for header,row in gzip_rows(can_path):
        if canonical_n==0:
            required=["flight_id","identity_version",*ID_COLS,"source_month","source_archive","source_archive_sha256","source_data_row_ordinal"]
            if header!=required and not set(required).issubset(header): raise ValueError("canonical schema is missing required columns")
        flight_id=row["flight_id"]
        if row["identity_version"]!=VERSION or not flight_id.startswith(VERSION+":"): id_errors+=1
        expected=fid(ident(row))
        if expected!=flight_id: id_errors+=1
        handles[int(flight_id.split(":",1)[1][0],16)].write(flight_id+"\n")
        month=int(row["source_month"]); month_counts[month]+=1
        archive,archive_sha,_=expected_arch[month]
        if (row["source_archive"],row["source_archive_sha256"])!=(archive,archive_sha):
            raise ValueError("canonical row lineage does not match saved source inventory")
        canonical_n+=1
        if canonical_n%500000==0: print(f"VERIFY_CANONICAL_ROWS {canonical_n:,}",flush=True)
    for h in handles: h.close()
    dupids=set(); duplicates=0; unique_ids=0
    for p in verify_dir.glob("*.txt"):
        seen=set()
        with p.open(encoding="ascii") as f:
            for line in f:
                x=line.rstrip("\n")
                if x in seen: dupids.add(x); duplicates+=1
                else: seen.add(x)
        unique_ids+=len(seen)
    shutil.rmtree(verify_dir)

    cross_n=0; last_ordinal=0; statuses=collections.Counter(); dedup=collections.Counter(); cross_errors=0
    cross_path=stage/"cleaned_to_canonical_crosswalk.csv.gz"
    for header,row in gzip_rows(cross_path):
        ordinal=int(row["cleaned_input_row_ordinal"]); first=int(row["cleaned_duplicate_first_ordinal"])
        if ordinal!=last_ordinal+1: cross_errors+=1
        if first>ordinal: cross_errors+=1
        status=row["match_status"]; flight_id=row["canonical_flight_id"]
        if status=="unique_exact_raw_match_identity_recovered":
            if not flight_id.startswith(VERSION+":"): cross_errors+=1
        elif flight_id: cross_errors+=1
        n=int(row["raw_candidate_count"])
        if status=="ambiguous_multiple_raw_candidates" and n<2: cross_errors+=1
        if status=="no_exact_raw_match" and n!=0: cross_errors+=1
        statuses[status]+=1
        if ordinal==first: dedup[status]+=1
        last_ordinal=ordinal; cross_n+=1
        if cross_n%500000==0: print(f"VERIFY_CROSSWALK_ROWS {cross_n:,}",flush=True)

    qrows=list(csv.DictReader((stage/"identity_quarantine.csv").open(encoding="utf-8",newline="")))
    f9_rows=[]
    for r in qrows:
        vals=json.loads(r["raw_selected_values_json"])
        v=dict(zip(["Year","Month","DayofMonth","FlightDate","Reporting_Airline","Origin","Dest","CRSDepTime","DepTime","DepDelay","Cancelled","Diverted","DepDel15"],vals))
        if v.get("Reporting_Airline")=="F9" and v.get("FlightDate")=="2024-08-25" and v.get("Origin")=="MIA" and v.get("Dest")=="ATL" and v.get("CRSDepTime")=="0600": f9_rows.append(r)
    ambiguous=list(csv.DictReader((stage/"ambiguous_cleaned_matches.csv").open(encoding="utf-8",newline="")))
    unmatched=list(csv.DictReader((stage/"unmatched_cleaned_rows.csv").open(encoding="utf-8",newline="")))
    for r in ambiguous:
        if int(r["raw_candidate_count"])!=len(json.loads(r["candidate_source_records_json"])): cross_errors+=1
    conflicts_path=stage/"identity_conflicts.csv"
    if not conflicts_path.exists():
        with conflicts_path.open("w",encoding="utf-8",newline="") as f:
            csv.writer(f,lineterminator="\n").writerow(["flight_id","normalized_identity_variants","raw_source_record_signatures","source_rows_json","classification"])
    blockers=[]
    checks=[
        (canonical_n==int(report["raw_bts"]["source_rows"])-1,"canonical row count"),
        (cross_n==int(report["cleaned"]["rows_before_dedup"]),"original cleaned crosswalk row count"),
        (sum(dedup.values())==int(report["cleaned"]["rows_after_dedup"]),"deduplicated cleaned row count"),
        (dedup["unique_exact_raw_match_identity_recovered"]==6982525,"unique identified crosswalk matches"),
        (dedup["unique_exact_raw_match_identity_incomplete"]==1,"incomplete identity match"),
        (dedup["ambiguous_multiple_raw_candidates"]==110,"ambiguous groups"),
        (dedup["no_exact_raw_match"]==20,"unmatched groups"),
        (len(ambiguous)==110 and len(unmatched)==20,"exception reports"),
        (len(qrows)==1 and len(f9_rows)==1,"F9 identity quarantine"),
        (unique_ids==canonical_n and not dupids and duplicates==0,"flight ID uniqueness"),
        (id_errors==0,"ID recomputation/schema"),
        (cross_errors==0,"crosswalk row/status validity"),
        (sum(month_counts.values())==canonical_n,"source lineage coverage"),
    ]
    blockers=[name+" check failed" for ok,name in checks if not ok]
    out_hashes={}
    names=["canonical_flights.csv.gz","cleaned_to_canonical_crosswalk.csv.gz","identity_quarantine.csv","ambiguous_cleaned_matches.csv","unmatched_cleaned_rows.csv","identity_conflicts.csv"]
    for name in names:
        p=stage/name
        out_hashes[name]={"bytes":p.stat().st_size,"sha256":sha(p)}
    result={
        "status":"BLOCKED" if blockers else "PASS",
        "finalization_mode":"verified and finalized existing staging outputs; the completed SQLite source scan and cleaned-row join were not rerun",
        "created_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "python":sys.version.split()[0],"identity_version":VERSION,"identity_fields_in_hash_order":ID_COLS,
        "identity_definition":"SHA-256 over compact UTF-8 JSON of normalized BTS identity fields, using the existing audit normalizer.",
        "cleaned_input_provenance":report["input"],"source_archive_inventory":report["source_archives"],
        "archive_hash_validation":"Copied from the completed source audit and preflight-matched to SQLite; ZIPs were not reopened.",
        "reconciliation_report_counts":{"raw_rows":report["raw_bts"]["source_rows"],"raw_non_cancelled_binary_target_candidates":report["raw_bts"]["noncancelled_binary_target_inmonth_eligible_rows"],"raw_exact_full_source_duplicate_rows":report["raw_bts"]["exact_full_source_row_duplicate_rows"],"raw_rows_with_incomplete_identity":report["raw_bts"]["rows_with_incomplete_canonical_identity"],"raw_repeated_13_field_key_groups":report["raw_bts"]["repeated_13_field_key_groups"],"raw_rows_in_repeated_13_field_key_groups":report["raw_bts"]["raw_rows_in_repeated_13_field_key_groups"],"raw_extra_rows_in_repeated_13_field_key_groups":report["raw_bts"]["extra_source_rows_in_repeated_13_field_key_groups"],"cleaned_original":report["cleaned"]["rows_before_dedup"],"cleaned_duplicate_groups":report["cleaned"]["exact_duplicate_groups"],"cleaned_duplicate_rows_beyond_first":report["cleaned"]["exact_duplicate_rows_beyond_first"],"cleaned_deduplicated":report["cleaned"]["rows_after_dedup"],"cleaned_date_part_inconsistencies":report["cleaned"]["date_parts_inconsistent_with_FL_DATE"],"unique_matches":report["matching"]["unique_one_to_one_full_tuple_matches"],"ambiguous":report["matching"]["cleaned_rows_with_multiple_raw_candidates"],"unmatched":report["matching"]["cleaned_deduplicated_rows_without_exact_raw_match"]},
        "counts":{"canonical_flights":canonical_n,"quarantined_rows":len(qrows),"F9_target_rows":len(f9_rows),"crosswalk_original_rows":cross_n,"crosswalk_deduplicated_rows":sum(dedup.values()),"deduplicated_match_statuses":dict(dedup),"all_original_row_statuses":dict(statuses),"ambiguous_exception_groups":len(ambiguous),"unmatched_exception_groups":len(unmatched),"unique_flight_ids":unique_ids,"duplicate_id_groups":len(dupids),"duplicate_id_rows":duplicates,"ID_schema_or_recompute_errors":id_errors,"crosswalk_errors":cross_errors,"canonical_month_counts":dict(month_counts)},
        "target_and_eligibility":{"target":"BTS DepDel15; compared with DEP_DELAY >= 15 minutes","cleaned_label_mismatches":report["cleaned"]["delay_15_vs_DEP_DELAY_at_15_mismatch_rows"],"unique_matches_source_A_N_eligible":report["matching"]["unique_matches_whose_source_record_is_A_N_eligible"],"unique_matches_source_cancelled":report["matching"]["unique_matches_whose_source_record_is_cancelled"],"unique_matches_otherwise_ineligible":report["matching"]["unique_matches_otherwise_ineligible"],"raw_eligible_rows_without_cleaned_exact_match":report["matching"]["raw_eligible_rows_without_cleaned_exact_match"],"raw_eligible_rows_in_ambiguous_groups":report["matching"]["raw_eligible_rows_in_ambiguous_groups"],"live_cutoff_note":"Final BTS outcomes do not establish eligibility at T-2h or T-30m."},
        "tests":test_result,"output_sha256":out_hashes,"blockers":blockers,
        "database":"Retained unchanged; finalization read generated outputs and the compact saved report only.",
        "not_performed":["training, tuning, feature generation, inference or scoring","dashboard integration, PR merge, branch switch, commit, push or deploy"]
    }
    rp=stage/"canonical_identity_run_report.json"
    rp.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    report_hash=sha(rp)
    (stage/"canonical_identity_run_report.sha256").write_text(report_hash+"  canonical_identity_run_report.json\n",encoding="ascii")
    if blockers:
        print(json.dumps({"status":"BLOCKED","stage":str(stage),"blockers":blockers},indent=2)); return 2
    stage.rename(out)
    print(json.dumps({"status":"PASS","output_dir":str(out),"report_sha256":report_hash,"counts":result["counts"]},indent=2))
    return 0

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage-dir",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True)
    p.add_argument("--reconciliation-dir",type=Path,required=True)
    a=p.parse_args()
    raise SystemExit(build(a.stage_dir,a.out_dir,a.reconciliation_dir))

if __name__=="__main__": main()
