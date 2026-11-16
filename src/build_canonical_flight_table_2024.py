#!/usr/bin/env python3
"""Build stable 2024 BTS flight IDs and a source-reconciled cleaned-row crosswalk."""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, hashlib, io, json, platform, shutil, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from audit_canonical_flights_2024 import ID_COLS, VERSION, fid, ident

VALUE_COLS = ["Year","Month","DayofMonth","FlightDate","Reporting_Airline","Origin","Dest","CRSDepTime","DepTime","DepDelay","Cancelled","Diverted","DepDel15"]
CANON_COLS = ["flight_id","identity_version",*ID_COLS,"Year","Month","DayofMonth","DepTime","DepDelay","Cancelled","Diverted","DepDel15","source_month","source_archive","source_archive_sha256","source_csv_member","source_data_row_ordinal","reconciliation_raw_row_id","reconciliation_raw_signature_sha256"]
CROSS_COLS = ["cleaned_input_row_ordinal","cleaned_row_sha256","cleaned_duplicate_first_ordinal","cleaned_duplicate_group_occurrences","canonical_flight_id","identity_version","match_status","raw_candidate_count","source_month","source_data_row_ordinal","source_archive","source_archive_sha256","source_csv_member","reconciliation_raw_row_id","reconciliation_raw_signature_sha256"]

def writer(path, cols, compressed=False):
    if compressed:
        raw=path.open("wb"); gz=gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0)
        f=io.TextIOWrapper(gz,encoding="utf-8",newline="")
    else:
        raw=None; f=path.open("w",encoding="utf-8",newline="")
    w=csv.DictWriter(f,fieldnames=cols,lineterminator="\n"); w.writeheader()
    class W:
        def writerow(self,row): w.writerow(row)
        def close(self):
            try: f.close()
            finally:
                if raw: raw.close()
    return W()

def identity_from_json(value):
    if not value: raise ValueError("no complete identity JSON")
    obj=json.loads(value)
    if not isinstance(obj,dict): raise ValueError("identity JSON is not an object")
    values=ident(obj)
    return values,fid(values)

def crosswalk_status(n,complete):
    if n is None or n==0: return "no_exact_raw_match",False
    if n>1: return "ambiguous_multiple_raw_candidates",False
    if not complete: return "unique_exact_raw_match_identity_incomplete",False
    return "unique_exact_raw_match_identity_recovered",True

def classify_reuse(previous,identity_json,raw_signature):
    if previous is None: return "first"
    old_identity,old_sig=previous
    if old_identity!=identity_json: raise ValueError("flight_id SHA-256 collision: distinct normalized identities")
    return "duplicate_identity_same_source_signature" if old_sig==raw_signature else "duplicate_identity_conflicting_source_records"

def read_duplicates(path):
    result={}
    with path.open(encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            ords=[int(x) for x in row["original_data_row_ordinals"].split(";") if x]
            if len(ords)<2 or ords!=sorted(ords): raise ValueError("invalid duplicate ordinals")
            result[row["row_sha256"]]={"first":ords[0],"ordinals":ords}
    return result

def file_sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()

def gzip_count(path,expected):
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        rd=csv.reader(f)
        if not next(rd,None): raise ValueError("missing gzip CSV header")
        count=sum(1 for _ in rd)
    if count!=expected: raise ValueError(f"{path.name} has {count} rows, expected {expected}")
    return count

def run_tests(root):
    cmd=[sys.executable,"-m","unittest","discover","-s","tests","-p","test_canonical_flight_identity.py","-v"]
    r=subprocess.run(cmd,cwd=root,capture_output=True,text=True,encoding="utf-8",errors="replace")
    out=(r.stdout or "")+(r.stderr or "")
    if r.returncode: raise RuntimeError("focused tests failed:\n"+out[-8000:])
    return {"status":"PASS","command":" ".join(cmd),"output":out[-8000:]}

def build(recon,out):
    if out.exists(): raise FileExistsError("refusing to overwrite "+str(out))
    stage=out.with_name(out.name+".staging")
    if stage.exists(): raise FileExistsError("preserving existing staging directory "+str(stage))
    tests=run_tests(ROOT)
    dbpath=recon/"reconciliation.sqlite"
    stage.mkdir(parents=True)
    db=sqlite3.connect(f"file:{dbpath.as_posix()}?mode=ro",uri=True); db.row_factory=sqlite3.Row
    try:
        report=json.loads((recon/"audit_report.json").read_text(encoding="utf-8"))
        counts_expected={"raw":report["raw_bts"]["source_rows"],"clean":report["cleaned"]["rows_before_dedup"],"cu":report["cleaned"]["rows_after_dedup"],"ck":report["cleaned"]["rows_after_dedup"]}
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(counts_expected).issubset(tables) or "rk" not in tables: raise ValueError("missing reconciliation tables")
        dbcounts={t:int(db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in (*counts_expected,"rk")}
        if any(dbcounts[k]!=int(v) for k,v in counts_expected.items()): raise ValueError("SQLite counts differ from saved report")
        m=report["matching"]
        for key,n in [("unique_one_to_one_full_tuple_matches",6982526),("cleaned_rows_with_multiple_raw_candidates",110),("cleaned_deduplicated_rows_without_exact_raw_match",20)]:
            if int(m[key])!=n: raise ValueError(f"reconciliation {key} mismatch")
        if int(report["identity_recovery"]["unique_matches_with_incomplete_identity_fields"])!=1: raise ValueError("expected one incomplete identity")
        archives=report["source_archives"]
        dbinv={int(month):(name,sha,int(n)) for month,name,sha,n in db.execute("SELECT month,archive,archive_sha,COUNT(*) FROM raw GROUP BY month,archive,archive_sha")}
        repinv={int(a["month"]):(a["name"],a["sha256"],int(a["source_rows"])) for a in archives}
        if len(archives)!=12 or dbinv!=repinv: raise ValueError("saved archive lineage differs from completed hash/CRC inventory")
        join=("SELECT c.ordinal,c.raw_hash,c.k,r.n,r.first_id,rr.id,rr.identity_json,rr.month,rr.source_row,rr.archive,rr.member,rr.archive_sha,rr.raw_sig FROM clean c LEFT JOIN rk r ON r.k=c.k LEFT JOIN raw rr ON rr.id=r.first_id ORDER BY c.ordinal")
        plan=[str(x[3]) for x in db.execute("EXPLAIN QUERY PLAN "+join)]
        if any("AUTOMATIC" in x.upper() for x in plan): raise ValueError("query would create automatic index")
        dups=read_duplicates(recon/"cleaned_duplicate_groups.csv")
        duplicate_rows=sum(len(v["ordinals"])-1 for v in dups.values())
        if len(dups)!=110 or duplicate_rows!=110: raise ValueError("duplicate ordinal list differs from audit")

        qw=writer(stage/"identity_quarantine.csv",["reconciliation_raw_row_id","source_month","source_data_row_ordinal","source_archive","source_archive_sha256","source_csv_member","raw_identity_missing_fields","identity_json","raw_selected_values_json","reason"])
        cw=writer(stage/"canonical_flights.csv.gz",CANON_COLS,True)
        conflictw=writer(stage/"identity_conflicts.csv",["flight_id","normalized_identity_variants","raw_source_record_signatures","source_rows_json","classification"] )
        shards=stage/".id_shards"; shards.mkdir()
        handles=[(shards/f"{i:x}.txt").open("w",encoding="ascii") for i in range(16)]
        source_n=canon_n=quarantine_n=f9_n=0; qreasons=collections.Counter()
        raw_sql="SELECT id,month,source_row,archive,member,archive_sha,raw_sig,vals,identity_missing,identity_json FROM raw ORDER BY id"
        for r in db.execute(raw_sql):
            source_n+=1
            try:
                values,flight_id=identity_from_json(r["identity_json"])
                identity=dict(zip(ID_COLS,values))
                v=json.loads(r["vals"])
                if len(v)!=len(VALUE_COLS): raise ValueError("unexpected saved source-value width")
                v=dict(zip(VALUE_COLS,v))
                cw.writerow({"flight_id":flight_id,"identity_version":VERSION,**identity,"Year":v["Year"],"Month":v["Month"],"DayofMonth":v["DayofMonth"],"DepTime":v["DepTime"],"DepDelay":v["DepDelay"],"Cancelled":v["Cancelled"],"Diverted":v["Diverted"],"DepDel15":v["DepDel15"],"source_month":r["month"],"source_archive":r["archive"],"source_archive_sha256":r["archive_sha"],"source_csv_member":r["member"],"source_data_row_ordinal":r["source_row"],"reconciliation_raw_row_id":r["id"],"reconciliation_raw_signature_sha256":r["raw_sig"]})
                handles[int(flight_id.split(":",1)[1][0],16)].write(flight_id+"\n"); canon_n+=1
            except (TypeError,ValueError,KeyError,json.JSONDecodeError) as exc:
                qreasons[str(exc)]+=1
                try: v=dict(zip(VALUE_COLS,json.loads(r["vals"] or "[]")))
                except (TypeError,ValueError,json.JSONDecodeError): v={}
                f9_n+=int(v.get("Reporting_Airline")=="F9" and v.get("FlightDate")=="2024-08-25" and v.get("Origin")=="MIA" and v.get("Dest")=="ATL" and v.get("CRSDepTime")=="0600")
                qw.writerow({"reconciliation_raw_row_id":r["id"],"source_month":r["month"],"source_data_row_ordinal":r["source_row"],"source_archive":r["archive"],"source_archive_sha256":r["archive_sha"],"source_csv_member":r["member"],"raw_identity_missing_fields":r["identity_missing"],"identity_json":r["identity_json"],"raw_selected_values_json":r["vals"],"reason":str(exc)})
                quarantine_n+=1
            if source_n%500000==0: print(f"CANONICAL_SOURCE_ROWS {source_n:,}",flush=True)
        cw.close(); qw.close()
        for h in handles: h.close()
        dupids=set(); dupidrows=0; uniqueids=0
        for shard in shards.glob("*.txt"):
            seen=set()
            with shard.open(encoding="ascii") as f:
                for line in f:
                    x=line.rstrip("\n")
                    if x in seen: dupids.add(x); dupidrows+=1
                    else: seen.add(x)
            uniqueids+=len(seen)
        if dupids:
            groups={x:{"identities":set(),"sigs":set(),"sources":[]} for x in dupids}
            for r in db.execute(raw_sql):
                try: values,x=identity_from_json(r["identity_json"])
                except (TypeError,ValueError,KeyError,json.JSONDecodeError): continue
                if x in groups:
                    g=groups[x]; g["identities"].add(json.dumps(values,separators=(",",":"))); g["sigs"].add(r["raw_sig"])
                    g["sources"].append({"month":r["month"],"source_row":r["source_row"],"archive":r["archive"],"archive_sha256":r["archive_sha"]})
            for x,g in groups.items():
                if len(g["identities"])>1: label="sha256_collision_distinct_identities"
                elif len(g["sigs"])>1: label="same_identity_conflicting_source_records"
                else: label="same_identity_same_source_signature"
                conflictw.writerow({"flight_id":x,"normalized_identity_variants":json.dumps(sorted(g["identities"])),"raw_source_record_signatures":json.dumps(sorted(g["sigs"])),"source_rows_json":json.dumps(g["sources"],separators=(",",":")),"classification":label})
        conflictw.close()
        shutil.rmtree(shards)

        xw=writer(stage/"cleaned_to_canonical_crosswalk.csv.gz",CROSS_COLS,True)
        aw=writer(stage/"ambiguous_cleaned_matches.csv",["cleaned_first_data_row_ordinal","cleaned_row_sha256","cleaned_duplicate_row_ordinals","cleaned_exact_key","raw_candidate_count","candidate_source_records_json"])
        uw=writer(stage/"unmatched_cleaned_rows.csv",["cleaned_first_data_row_ordinal","cleaned_row_sha256","cleaned_duplicate_row_ordinals","cleaned_exact_key","reason"])
        statuses=collections.Counter(); dedup=collections.Counter(); exception_hashes=set()
        cross_n=ambig_n=unmatched_n=ambig_groups=unmatched_groups=0
        cand_sql="SELECT id,month,source_row,archive,member,archive_sha,raw_sig,identity_missing,identity_json FROM raw WHERE k=? ORDER BY id"
        for r in db.execute(join):
            ordinal=int(r["ordinal"]); rowhash=r["raw_hash"]; dup=dups.get(rowhash)
            first=int(dup["first"]) if dup else ordinal; ords=dup["ordinals"] if dup else [ordinal]
            n=int(r["n"]) if r["n"] is not None else 0; complete=False; flight_id=""
            if n==1:
                try: _,flight_id=identity_from_json(r["identity_json"]); complete=True
                except (TypeError,ValueError,KeyError,json.JSONDecodeError): pass
            status,assign=crosswalk_status(n,complete)
            if not assign: flight_id=""
            src={}
            if n==1: src={"source_month":r["month"],"source_data_row_ordinal":r["source_row"],"source_archive":r["archive"],"source_archive_sha256":r["archive_sha"],"source_csv_member":r["member"],"reconciliation_raw_row_id":r["id"],"reconciliation_raw_signature_sha256":r["raw_sig"]}
            xw.writerow({"cleaned_input_row_ordinal":ordinal,"cleaned_row_sha256":rowhash,"cleaned_duplicate_first_ordinal":first,"cleaned_duplicate_group_occurrences":len(ords),"canonical_flight_id":flight_id,"identity_version":VERSION if assign else "","match_status":status,"raw_candidate_count":n,**src})
            cross_n+=1; statuses[status]+=1
            if ordinal==first: dedup[status]+=1
            if status=="ambiguous_multiple_raw_candidates":
                ambig_n+=1
                if rowhash not in exception_hashes:
                    exception_hashes.add(rowhash)
                    candidates=[{"reconciliation_raw_row_id":x["id"],"month":x["month"],"source_row":x["source_row"],"archive":x["archive"],"archive_sha256":x["archive_sha"],"csv_member":x["member"],"raw_signature_sha256":x["raw_sig"],"identity_missing_fields":x["identity_missing"],"identity_json":x["identity_json"]} for x in db.execute(cand_sql,(r["k"],))]
                    aw.writerow({"cleaned_first_data_row_ordinal":first,"cleaned_row_sha256":rowhash,"cleaned_duplicate_row_ordinals":";".join(map(str,ords)),"cleaned_exact_key":r["k"],"raw_candidate_count":n,"candidate_source_records_json":json.dumps(candidates,separators=(",",":"))})
                    ambig_groups+=1
            elif status=="no_exact_raw_match":
                unmatched_n+=1
                if rowhash not in exception_hashes:
                    exception_hashes.add(rowhash)
                    uw.writerow({"cleaned_first_data_row_ordinal":first,"cleaned_row_sha256":rowhash,"cleaned_duplicate_row_ordinals":";".join(map(str,ords)),"cleaned_exact_key":r["k"],"reason":"No exact 13-field raw BTS match in the saved reconciliation."})
                    unmatched_groups+=1
            if cross_n%500000==0: print(f"CROSSWALK_ROWS {cross_n:,}",flush=True)
        xw.close(); aw.close(); uw.close()
        expected_canon=int(report["raw_bts"]["source_rows"])-int(report["raw_bts"]["rows_with_incomplete_canonical_identity"])
        counts={"raw_source_rows":source_n,"canonical_flights":canon_n,"identity_quarantine_rows":quarantine_n,"cleaned_original_rows":cross_n,"cleaned_deduplicated_rows":sum(dedup.values()),"cleaned_duplicate_groups":len(dups),"cleaned_duplicate_rows_beyond_first":duplicate_rows,"cleaned_unique_exact_matches_with_id":dedup["unique_exact_raw_match_identity_recovered"],"cleaned_unique_matches_identity_incomplete":dedup["unique_exact_raw_match_identity_incomplete"],"cleaned_ambiguous_deduplicated_rows":dedup["ambiguous_multiple_raw_candidates"],"cleaned_unmatched_deduplicated_rows":dedup["no_exact_raw_match"],"ambiguous_original_cleaned_rows":ambig_n,"unmatched_original_cleaned_rows":unmatched_n,"ambiguous_exception_groups":ambig_groups,"unmatched_exception_groups":unmatched_groups,"unique_flight_ids":uniqueids,"duplicate_flight_id_groups":len(dupids),"duplicate_flight_id_rows_beyond_first":dupidrows,"quarantine_reason_counts":dict(qreasons),"F9_MIA_ATL_2024_08_25_0600_quarantined":f9_n}
        checks=[(source_n==int(report["raw_bts"]["source_rows"]),"raw row count mismatch"),(cross_n==int(report["cleaned"]["rows_before_dedup"]),"crosswalk row coverage mismatch"),(sum(dedup.values())==int(report["cleaned"]["rows_after_dedup"]),"deduplicated row count mismatch"),(canon_n==expected_canon,"canonical count mismatch"),(quarantine_n==1 and f9_n==1,"F9 quarantine mismatch"),(dedup["unique_exact_raw_match_identity_recovered"]==6982525,"unique ID crosswalk count mismatch"),(dedup["unique_exact_raw_match_identity_incomplete"]==1,"incomplete mapping count mismatch"),(dedup["ambiguous_multiple_raw_candidates"]==110,"ambiguous count mismatch"),(dedup["no_exact_raw_match"]==20,"unmatched count mismatch"),(ambig_groups==110 and unmatched_groups==20,"exception group count mismatch"),(uniqueids==canon_n and not dupids,"flight ID uniqueness failed")]
        blockers=[label for ok,label in checks if not ok]
        gzip_checks={}
        if not blockers:
            gzip_checks["canonical_flights.csv.gz"]=gzip_count(stage/"canonical_flights.csv.gz",canon_n)
            gzip_checks["cleaned_to_canonical_crosswalk.csv.gz"]=gzip_count(stage/"cleaned_to_canonical_crosswalk.csv.gz",cross_n)
        names=["canonical_flights.csv.gz","cleaned_to_canonical_crosswalk.csv.gz","identity_quarantine.csv","ambiguous_cleaned_matches.csv","unmatched_cleaned_rows.csv","identity_conflicts.csv"]
        hashes={n:{"bytes":(stage/n).stat().st_size,"sha256":file_sha(stage/n)} for n in names}
        result={"status":"BLOCKED" if blockers else "PASS","created_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"python":sys.version.split()[0],"platform":platform.platform(),"identity_version":VERSION,"identity_fields_in_hash_order":ID_COLS,"identity_definition":"SHA-256 over compact UTF-8 JSON of normalized BTS identity fields using the existing audit normalizer.","scope":"Raw 2024 BTS identity table and exact source crosswalk; not a byte-for-byte reconstruction of upstream cleaning.","cleaned_input_provenance":report["input"],"source_archive_inventory":archives,"archive_hash_validation":"Saved SQLite inventory agrees with completed CRC/hash report; source ZIPs were not reopened or rehashed.","reconciliation_db":str(dbpath),"reconciliation_db_counts":dbcounts,"crosswalk_query_plan":plan,"counts":counts,"crosswalk_status_counts_original_rows":dict(statuses),"crosswalk_status_counts_deduplicated_rows":dict(dedup),"target_and_eligibility":{"cleaned_label_mismatches":report["cleaned"]["delay_15_vs_DEP_DELAY_at_15_mismatch_rows"],"operational_cutoff_note":"BTS final outcomes do not establish eligibility at T-2h or T-30m."},"population_difference":report["population_difference"],"tests":tests,"gzip_row_count_validation":gzip_checks,"output_hashes":hashes,"blockers":blockers,"not_performed":["training, tuning, inference, scoring, feature generation","dashboard integration, PR merge, branch switch, commit or push"],"scratch_database":"Retained; opened read-only."}
        rp=stage/"canonical_identity_run_report.json"; rp.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        report_hash=file_sha(rp); (stage/"canonical_identity_run_report.sha256").write_text(report_hash+"  canonical_identity_run_report.json\n",encoding="ascii")
        if blockers:
            print(json.dumps({"status":"BLOCKED","staging":str(stage),"blockers":blockers},indent=2)); return 2
        stage.rename(out)
        print(json.dumps({"status":"PASS","output_dir":str(out),"report_sha256":report_hash,"counts":counts},indent=2)); return 0
    finally:
        db.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reconciliation-dir",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True)
    a=p.parse_args()
    raise SystemExit(build(a.reconciliation_dir,a.out_dir))

if __name__=="__main__":
    main()
