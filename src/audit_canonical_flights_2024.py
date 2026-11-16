#!/usr/bin/env python3
"""Audit 2024 BTS identity, population, and target parity; standard library only."""
from __future__ import annotations
import argparse, csv, datetime as dt, decimal, gzip, hashlib, io, json, platform, re, sqlite3, sys, zipfile
from pathlib import Path
from canonical_flight_identity import ID_COLS, VERSION, dateval, fid, ident, inttok, missing, num, timeval

AN_PRE, AN_POST, FW_REPORTED = 6982766, 6982656, 6982746
REQUIRED = set(ID_COLS + ["Cancelled","DepDel15","DepDelay"])
CROSS_COLS = ["flight_id","identity_version",*ID_COLS,"source_archive","source_archive_sha256","source_csv_member","source_row_ordinal","full_record_sha256"]

def selftest():
    a=["2024-01-01","19805","UA","7","12892","LAX","12478","JFK","0005"]
    assert fid(a)==fid(list(a)) and fid(a)!=fid([*a[:-1],"0010"])
    assert fid(a)!=fid([*a[:2],"AA",*a[3:]])
    assert timeval("5.0")=="0005" and timeval("2400")=="2400"
def asint(v):
    n=num(v)
    return int(n) if n is not None and n==n.to_integral_value() else None
def parsedate(v):
    if missing(v): return None
    try: return dt.date.fromisoformat(v.strip()[:10])
    except ValueError: return None
def validtime(v):
    try: return int(timeval(v))
    except (ValueError,decimal.InvalidOperation): return None
def member(z):
    xs=[x for x in z.namelist() if x.lower().endswith(".csv") and not x.endswith("/")]
    if len(xs)!=1: raise ValueError(f"expected one CSV member, found {len(xs)}")
    return xs[0]
def gzwriter(path,cols):
    raw=path.open("wb"); gz=gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0)
    txt=io.TextIOWrapper(gz,encoding="utf-8",newline=""); w=csv.writer(txt,lineterminator="\n"); w.writerow(cols)
    class W:
        def writerow(self,row): w.writerow(row)
        def close(self):
            try: txt.close()
            finally: raw.close()
    return W()
def read_fw(path):
    with path.open(encoding="utf-8-sig",newline="") as f:
        return {int(r["month"]):int(r["eligible_rows"]) for r in csv.DictReader(f) if r.get("year")=="2024"}
def qcount(db,sql): return int(db.execute(sql).fetchone()[0] or 0)
def totals(db):
    valid=qcount(db,"SELECT SUM(raw_n) FROM v"); unique=qcount(db,"SELECT COUNT(*) FROM v")
    sets=db.execute("""SELECT SUM(fw),SUM(an),SUM(fw AND an),SUM(fw AND NOT an),SUM(an AND NOT fw)
      FROM (SELECT fid,MAX(fw_n>0) fw,MAX(an_n>0) an FROM v GROUP BY fid)""").fetchone()
    return {
      "rows_with_valid_id":valid,"distinct_full_records":unique,"exact_duplicate_rows":valid-unique,
      "exact_duplicate_groups":qcount(db,"SELECT COUNT(*) FROM v WHERE raw_n>1"),
      "unique_flight_ids":qcount(db,"SELECT COUNT(DISTINCT fid) FROM v"),
      "flight_id_hash_collisions":qcount(db,"SELECT COUNT(*) FROM (SELECT fid FROM v GROUP BY fid HAVING COUNT(DISTINCT ident_json)>1)"),
      "conflicting_identity_keys":qcount(db,"SELECT COUNT(*) FROM (SELECT fid,ident_json FROM v GROUP BY fid,ident_json HAVING COUNT(*)>1)"),
      "conflicting_extra_variants":qcount(db,"SELECT SUM(n-1) FROM (SELECT COUNT(*) n FROM v GROUP BY fid,ident_json HAVING COUNT(*)>1)"),
      "fw_eligible_rows":qcount(db,"SELECT SUM(fw_n) FROM v"),
      "fw_eligible_distinct_records":qcount(db,"SELECT COUNT(*) FROM v WHERE fw_n>0"),
      "an_clean_candidate_pre_dedup_rows":qcount(db,"SELECT SUM(an_n) FROM v"),
      "an_clean_candidate_distinct_records":qcount(db,"SELECT COUNT(*) FROM v WHERE an_n>0"),
      "an_candidate_duplicate_rows":qcount(db,"SELECT SUM(an_n-1) FROM v WHERE an_n>1"),
      "an_non_cancelled_candidate_rows":qcount(db,"SELECT SUM(op_n) FROM v"),
      "an_non_cancelled_distinct_records":qcount(db,"SELECT COUNT(*) FROM v WHERE op_n>0"),
      "an_tminus2h_retrospective_distinct_records":qcount(db,"SELECT COUNT(*) FROM v WHERE t2_n>0"),
      "an_tminus30m_retrospective_distinct_records":qcount(db,"SELECT COUNT(*) FROM v WHERE t30_n>0"),
      "target_comparable_rows":qcount(db,"SELECT SUM(comp_n) FROM v"),
      "target_mismatch_rows":qcount(db,"SELECT SUM(mis_n) FROM v"),
      "target_mismatch_distinct_records":qcount(db,"SELECT COUNT(*) FROM v WHERE mis_n>0"),
      "population_unique_id_sets":{"fw_ids":int(sets[0] or 0),"an_candidate_ids":int(sets[1] or 0),
        "intersection":int(sets[2] or 0),"fw_only":int(sets[3] or 0),"an_only":int(sets[4] or 0)}
    }
def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--raw-dir",type=Path,required=True)
    p.add_argument("--fw-monthly-audit",type=Path,required=True); p.add_argument("--out-dir",type=Path,required=True)
    a=p.parse_args(); selftest()
    if a.out_dir.exists(): raise FileExistsError(f"refusing to overwrite {a.out_dir}")
    expected=read_fw(a.fw_monthly_audit)
    if set(expected)!=set(range(1,13)): raise ValueError("saved F/W monthly audit must have 2024 months 1-12")
    paths=sorted(a.raw_dir.glob("On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_*.zip"),
      key=lambda x:int(re.search(r"_2024_(\d+)\.zip$",x.name).group(1)))
    if len(paths)!=12: raise ValueError(f"expected 12 monthly ZIPs; found {len(paths)}")
    a.out_dir.mkdir(parents=True)
    db=sqlite3.connect(a.out_dir/"audit.sqlite")
    db.execute("PRAGMA journal_mode=OFF"); db.execute("PRAGMA synchronous=OFF"); db.execute("PRAGMA temp_store=MEMORY"); db.execute("PRAGMA cache_size=-200000")
    db.execute("""CREATE TABLE v(fid TEXT,ident_json TEXT,full_sig TEXT,first_month INT,first_row INT,raw_n INT,fw_n INT,an_n INT,op_n INT,t2_n INT,t30_n INT,comp_n INT,mis_n INT,depdel15 TEXT,an_target TEXT,cancelled TEXT,PRIMARY KEY(fid,ident_json,full_sig)) WITHOUT ROWID""")
    ins="INSERT OR IGNORE INTO v VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    upd="""UPDATE v SET raw_n=raw_n+1,fw_n=fw_n+?,an_n=an_n+?,op_n=op_n+?,t2_n=t2_n+?,t30_n=t30_n+?,comp_n=comp_n+?,mis_n=mis_n+?
      WHERE fid=? AND ident_json=? AND full_sig=?"""
    months={}; archives=[]; source_rows=0; missing_ids=0; malformed=0; cancelled_rows=0; unknown_cancel=0
    miss_target=bad_target=nonbinary_target=miss_delay=bad_date=out_month=an_missing=an_bad_time=0
    id_examples=[]; can_tmp=a.out_dir/"canonical.partial"; cross_tmp=a.out_dir/"crosswalk.partial"
    cross=gzwriter(cross_tmp,CROSS_COLS); can=None; head=None; dup_rows=0
    for path in paths:
        month=int(re.search(r"_2024_(\d+)\.zip$",path.name).group(1))
        h=hashlib.sha256()
        with path.open("rb") as f:
            for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
        archive_sha=h.hexdigest()
        m=months.setdefault(month,{"month":month,"source_rows":0,"fw_eligible":0,"an_complete_candidates":0,
          "an_non_cancelled":0,"an_t2h_retrospective":0,"an_t30m_retrospective":0,"exact_duplicate_rows":0,
          "fw_duplicate_rows":0,"an_duplicate_rows":0,"missing_identity_rows":0})
        with zipfile.ZipFile(path) as z:
            bad=z.testzip()
            if bad: raise ValueError(f"CRC failure {path.name}:{bad}")
            mem=member(z)
            with z.open(mem,"r") as b:
                reader=csv.DictReader(io.TextIOWrapper(b,encoding="utf-8-sig",newline="")); cols=reader.fieldnames
                if not cols or REQUIRED.difference(cols) or len(set(cols))!=len(cols): raise ValueError(f"missing/duplicate header in {path.name}")
                if head is None:
                    head=cols
                    can=gzwriter(can_tmp,[*cols,"flight_id","identity_version","source_archive","source_archive_sha256","source_csv_member","source_row_ordinal","full_record_sha256"])
                elif cols!=head: raise ValueError("monthly headers differ")
                print(f"PROCESSING {path.name} sha256={archive_sha}",flush=True)
                for row_no,row in enumerate(reader,1):
                    source_rows+=1; m["source_rows"]+=1
                    if None in row: malformed+=1; continue
                    d=parsedate(row.get("FlightDate")); c=asint(row.get("Cancelled"))
                    d15n=num(row.get("DepDel15")); d15=int(d15n) if d15n is not None and d15n==d15n.to_integral_value() else None
                    dn=num(row.get("DepDelay")); delay=float(dn) if dn is not None else None
                    at=int(dn>=15) if dn is not None else None
                    inmonth=d is not None and d.year==2024 and d.month==month
                    fw=int(c==0 and d15 in (0,1) and inmonth)
                    t=validtime(row.get("CRSDepTime"))
                    complete=(d is not None and d.year==2024 and all(not missing(row.get(x)) for x in
                      ["Reporting_Airline","Origin","Dest","CRSDepTime","DepDelay"]) and num(row.get("CRSDepTime")) is not None and dn is not None)
                    an=int(complete); op=int(an and c==0); t2=int(op and t is not None and delay is not None and delay>-120); t30=int(op and t is not None and delay is not None and delay>-30)
                    comp=int(d15 in (0,1) and at is not None); mismatch=int(comp and d15!=at)
                    m["fw_eligible"]+=fw; m["an_complete_candidates"]+=an; m["an_non_cancelled"]+=op; m["an_t2h_retrospective"]+=t2; m["an_t30m_retrospective"]+=t30
                    cancelled_rows+=int(c==1); unknown_cancel+=int(c not in (0,1)); miss_target+=int(missing(row.get("DepDel15")))
                    bad_target+=int(not missing(row.get("DepDel15")) and d15n is None); nonbinary_target+=int(d15n is not None and d15 not in (0,1))
                    miss_delay+=int(dn is None); bad_date+=int(d is None); out_month+=int(d is not None and not inmonth)
                    an_missing+=int(not complete); an_bad_time+=int(complete and t is None)
                    try: iv=ident(row)
                    except (ValueError,decimal.InvalidOperation) as e:
                        missing_ids+=1; m["missing_identity_rows"]+=1
                        if len(id_examples)<25: id_examples.append([month,row_no,str(e)])
                        continue
                    flight_id=fid(iv); ij=json.dumps(iv,ensure_ascii=False,separators=(",",":"))
                    cells=[row.get(x) for x in cols]; raw_json=json.dumps(cells,ensure_ascii=False,separators=(",",":"))
                    sig=hashlib.sha256(raw_json.encode()).hexdigest()
                    cur=db.execute(ins,(flight_id,ij,sig,month,row_no,1,fw,an,op,t2,t30,comp,mismatch,
                      str(d15) if d15 in (0,1) else None,str(at) if at is not None else None,str(c) if c is not None else None))
                    fresh=cur.rowcount==1
                    if not fresh:
                        db.execute(upd,(fw,an,op,t2,t30,comp,mismatch,flight_id,ij,sig)); dup_rows+=1
                        m["exact_duplicate_rows"]+=1; m["fw_duplicate_rows"]+=fw; m["an_duplicate_rows"]+=an
                    cross.writerow([flight_id,VERSION,*iv,path.name,archive_sha,mem,row_no,sig])
                    if fresh: can.writerow([*cells,flight_id,VERSION,path.name,archive_sha,mem,row_no,sig])
                    if source_rows%250000==0: db.commit()
                db.commit()
        archives.append({"month":month,"name":path.name,"bytes":path.stat().st_size,"sha256":archive_sha,"csv_member":mem,"crc":"PASS","source_rows":m["source_rows"]})
        print(f"COMPLETED month={month:02d} rows={m['source_rows']:,}",flush=True)
    can.close(); cross.close()
    s=totals(db)
    dup_sigs={r[0] for r in db.execute("SELECT full_sig FROM v WHERE raw_n>1")}
    seen={}; full_hash_collisions=set()
    if dup_sigs:
        for path in paths:
            with zipfile.ZipFile(path) as z:
                with z.open(member(z),"r") as b:
                    rd=csv.DictReader(io.TextIOWrapper(b,encoding="utf-8-sig",newline="")); cols=rd.fieldnames or []
                    for row in rd:
                        if None in row: continue
                        raw=json.dumps([row.get(x) for x in cols],ensure_ascii=False,separators=(",",":"))
                        sig=hashlib.sha256(raw.encode()).hexdigest()
                        if sig in dup_sigs:
                            if sig in seen and seen[sig]!=raw: full_hash_collisions.add(sig)
                            else: seen[sig]=raw
    diffs=a.out_dir/"row_level_population_differences.csv"
    with diffs.open("w",encoding="utf-8",newline="") as f:
        w=csv.writer(f,lineterminator="\n"); w.writerow(["flight_id","identity_json","month","source_row","raw_count","fw_count","an_candidate_count","an_non_cancelled","an_t2h","an_t30m","target_comparable","target_mismatch","DepDel15","derived_delay_15","Cancelled"])
        for r in db.execute("""SELECT fid,ident_json,first_month,first_row,raw_n,fw_n,an_n,op_n,t2_n,t30_n,comp_n,mis_n,depdel15,an_target,cancelled
          FROM v WHERE ((fw_n>0)!=(an_n>0)) OR mis_n>0 ORDER BY first_month,first_row"""): w.writerow(r)
    with (a.out_dir/"identity_conflicts.csv").open("w",encoding="utf-8",newline="") as f:
        w=csv.writer(f,lineterminator="\n"); w.writerow(["flight_id","identity_json","full_record_sha256","raw_count","month","row","DepDel15","derived_delay_15","Cancelled"])
        for r in db.execute("""SELECT fid,ident_json,full_sig,raw_n,first_month,first_row,depdel15,an_target,cancelled FROM v
          WHERE (fid,ident_json) IN (SELECT fid,ident_json FROM v GROUP BY fid,ident_json HAVING COUNT(*)>1) ORDER BY fid,first_month,first_row"""): w.writerow(r)
    with (a.out_dir/"identity_invalid_rows.csv").open("w",encoding="utf-8",newline="") as f:
        w=csv.writer(f,lineterminator="\n"); w.writerow(["month","source_row","reason"]); w.writerows(id_examples)
    with (a.out_dir/"flight_id_hash_collisions.csv").open("w",encoding="utf-8",newline="") as f:
        w=csv.writer(f,lineterminator="\n"); w.writerow(["flight_id","identity_variants"])
        for r in db.execute("SELECT fid,COUNT(DISTINCT ident_json) FROM v GROUP BY fid HAVING COUNT(DISTINCT ident_json)>1"): w.writerow(r)
    comparisons=[]
    for mo in range(1,13):
        months[mo]["saved_fw_eligible"]=expected[mo]; months[mo]["fw_monthly_match"]=months[mo]["fw_eligible"]==expected[mo]
        comparisons.append({"month":mo,"derived":months[mo]["fw_eligible"],"saved":expected[mo],"match":months[mo]["fw_monthly_match"]})
    with (a.out_dir/"monthly_stage_counts.csv").open("w",encoding="utf-8",newline="") as f:
        fields=list(months[1]); w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n"); w.writeheader(); w.writerows(months[m] for m in range(1,13))
    with (a.out_dir/"source_archives.csv").open("w",encoding="utf-8",newline="") as f:
        fields=list(archives[0]); w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n"); w.writeheader(); w.writerows(archives)
    blockers=[]
    if missing_ids: blockers.append("missing/invalid identity fields")
    if malformed: blockers.append("malformed CSV rows")
    if s["flight_id_hash_collisions"]: blockers.append("flight_id SHA-256 collision")
    if s["conflicting_identity_keys"]: blockers.append("same identity has multiple distinct full BTS records")
    if full_hash_collisions: blockers.append("full-record SHA-256 collision")
    if sum(x["match"] for x in comparisons)!=12: blockers.append("Flight/Weather monthly counts did not all match")
    status="blocked" if blockers else "built"
    diff_count=max(0,sum(1 for _ in diffs.open(encoding="utf-8"))-1)
    report={
      "run_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"python":sys.version.split()[0],"platform":platform.platform(),
      "script_version":VERSION,"scope":"2024 BTS source archives only","source_rows":source_rows,"archives":archives,
      "cancelled_rows":cancelled_rows,"unknown_cancellation_rows":unknown_cancel,
      "missing_DepDel15_rows":miss_target,"invalid_DepDel15_rows":bad_target,"nonbinary_DepDel15_rows":nonbinary_target,
      "missing_DepDelay_rows":miss_delay,"invalid_FlightDate_rows":bad_date,"out_of_month_FlightDate_rows":out_month,
      "A_N_missing_required_input_rows":an_missing,"A_N_invalid_scheduled_time_rows":an_bad_time,
      "missing_or_invalid_identity_rows":missing_ids,"identity_examples":id_examples,"malformed_CSV_rows":malformed,
      "reconstructed":s,"target_parity":{"comparable_rows":qcount(db,"SELECT SUM(comp_n) FROM v"),"mismatch_rows":qcount(db,"SELECT SUM(mis_n) FROM v")},
      "reported_populations":{"A_N_pre_dedup":AN_PRE,"A_N_post_dedup":AN_POST,"A_N_reported_duplicate_removals":AN_PRE-AN_POST,
        "F_W_eligible":FW_REPORTED,"reported_A_N_minus_F_W":AN_POST-FW_REPORTED},
      "A_N_reconstruction_limit":"Original cleaned file/build script/manifest unavailable; notebook candidate is reconstructed from BTS with Reporting_Airline used as OP_UNIQUE_CARRIER proxy.",
      "FW_monthly_audit":{"months":comparisons,"matching_months":sum(x["match"] for x in comparisons)},
      "duplicate_verification":{"duplicate_hash_groups_rechecked":len(dup_sigs),"full_record_hash_collisions":len(full_hash_collisions)},
      "row_level_population_difference_records":diff_count,"canonical_status":status,"canonical_blockers":blockers,
      "canonical_row_count":s["distinct_full_records"] if not blockers else None,
      "crosswalk_row_count":source_rows-missing_ids if not blockers else None,
      "parquet_record_level_compared":False,
      "parquet_note":"Did not use the project .venv or a Parquet reader; raw monthly counts were checked against the saved Flight/Weather monthly audit.",
      "eligibility_note":"Retrospective Airport/Network eligibility uses CANCELLED=0 and scheduled time + final DEP_DELAY > scheduled time minus horizon; this is outcome-derived and does not establish operational availability."
    }
    if not blockers:
        can_tmp.replace(a.out_dir/"canonical_bts_flights_2024.csv.gz")
        cross_tmp.replace(a.out_dir/"flight_id_crosswalk_2024.csv.gz")
    else:
        can_tmp.unlink(missing_ok=True); cross_tmp.unlink(missing_ok=True)
    (a.out_dir/"audit_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    db.close(); (a.out_dir/"audit.sqlite").unlink(missing_ok=True)
    print(json.dumps({"status":status,"source_rows":source_rows,"canonical_rows":report["canonical_row_count"],
      "crosswalk_rows":report["crosswalk_row_count"],"fw_monthly_audit_matches":report["FW_monthly_audit"]["matching_months"],
      "exact_duplicate_rows":s["exact_duplicate_rows"],"conflicting_identity_keys":s["conflicting_identity_keys"],
      "flight_id_hash_collisions":s["flight_id_hash_collisions"],"missing_identity_rows":missing_ids,
      "target_mismatch_rows":report["target_parity"]["mismatch_rows"],"difference_records":diff_count,
      "blockers":blockers,"output_dir":str(a.out_dir)},indent=2),flush=True)
    return 0 if not blockers else 2

if __name__=="__main__":
    raise SystemExit(main())

