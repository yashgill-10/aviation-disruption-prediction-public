#!/usr/bin/env python3
"""Reconcile saved 2024 canonical IDs against retrospective horizon eligibility.

Reads the retained SQLite reconciliation database in read-only mode. It does
not open BTS ZIPs, rebuild indexes, create features, or score models.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import decimal
import gzip
import hashlib
import io
import json
import platform
import sqlite3
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from audit_canonical_flights_2024 import fid, ident  # noqa: E402

DEFAULT_RECON = Path("data/processed/airport_network_reconciliation_2024_20261012_retry1")
DEFAULT_IDENTITY = Path("data/processed/canonical_identity_2024_20261015_v1")
DEFAULT_OUTPUT = Path("data/processed/shared_cohort_2024_20261016_v1")
HORIZONS = {"t_minus_2h": 120, "t_minus_30m": 30}
MEMBERSHIP_FIELDS = [
    "flight_id", "identity_version", "horizon", "eligibility_rule",
    "FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline",
    "Origin", "Dest", "CRSDepTime", "DepDel15", "final_DEP_DELAY_used_for_retrospective_eligibility",
    "Cancelled", "reconciliation_raw_row_id", "source_month", "source_archive",
    "source_archive_sha256", "source_csv_member", "source_data_row_ordinal",
    "raw_key_candidate_count", "cleaned_key_occurrences", "cleaned_first_row_ordinal",
]
EXCEPTION_FIELDS = [
    "horizon", "source_layer", "status", "eligibility_rule",
    "FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline",
    "Origin", "Dest", "CRSDepTime", "DepDel15", "final_DEP_DELAY",
    "Cancelled", "flight_id", "reconciliation_raw_row_id", "source_month",
    "source_archive", "source_archive_sha256", "source_csv_member",
    "source_data_row_ordinal", "cleaned_first_row_ordinal",
    "raw_key_candidate_count", "cleaned_key_occurrences",
    "identity_missing_fields", "cleaned_row_ordinal", "cleaned_row_sha256", "reason",
]


def as_decimal(value: Any) -> decimal.Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = decimal.Decimal(str(value).strip())
    except decimal.InvalidOperation:
        return None
    return result if result.is_finite() else None


def eligible_at_horizon(delay: decimal.Decimal | None, horizon_minutes: int) -> bool:
    """Retrospective only: actual departure is strictly after the cutoff."""
    return delay is not None and delay > decimal.Decimal(-horizon_minutes)


def match_status(raw_candidates: int | None, clean_occurrences: int | None,
                 missing_identity: list[str], identity_json: str | None) -> str:
    if clean_occurrences is None or clean_occurrences == 0:
        return "raw_source_unmatched_to_cleaned_input"
    if raw_candidates is None or raw_candidates > 1:
        return "ambiguous_raw_candidates"
    if missing_identity or not identity_json:
        return "unique_source_match_identity_incomplete"
    if raw_candidates == 1:
        return "unique_exact_match_valid_canonical_id"
    return "unresolved_mapping_state"


def make_flight_id(identity_json: str) -> tuple[dict[str, str], str]:
    identity = json.loads(identity_json)
    normalized = ident(identity)
    return dict(zip(
        ["FlightDate", "DOT_ID_Reporting_Airline", "Reporting_Airline",
         "Flight_Number_Reporting_Airline", "OriginAirportID", "Origin",
         "DestAirportID", "Dest", "CRSDepTime"],
        normalized,
    )), fid(normalized)


def nested_id_set_comparison(t2_count: int, t30_count: int,
                             intersection_count: int) -> dict[str, int]:
    """Summarize row-verified nested horizon ID sets."""
    if min(t2_count, t30_count, intersection_count) < 0:
        raise ValueError("ID-set counts cannot be negative")
    if intersection_count > min(t2_count, t30_count):
        raise ValueError("intersection exceeds a horizon ID set")
    return {
        "t2h": t2_count,
        "t30m": t30_count,
        "intersection": intersection_count,
        "t2h_only": t2_count - intersection_count,
        "t30m_only": t30_count - intersection_count,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def gzip_csv_writer(path: Path, fields: list[str]) -> Iterator[csv.DictWriter]:
    raw = path.open("wb")
    compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
    text = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
    writer = csv.DictWriter(text, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    try:
        yield writer
    finally:
        text.close()
        raw.close()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def validate_inputs(root: Path, recon_dir: Path, identity_dir: Path) -> dict[str, Any]:
    identity_report_path = identity_dir / "canonical_identity_run_report.json"
    identity_sidecar = identity_dir / "canonical_identity_run_report.sha256"
    recon_report_path = recon_dir / "audit_report.json"
    source_audit_path = root / "data/processed/canonical_audit_2024_20261012/audit_report.json"
    db_path = recon_dir / "reconciliation.sqlite"
    required = [
        identity_report_path, identity_sidecar, recon_report_path, source_audit_path,
        db_path, identity_dir / "canonical_flights.csv.gz",
        identity_dir / "cleaned_to_canonical_crosswalk.csv.gz",
        identity_dir / "identity_quarantine.csv",
        identity_dir / "ambiguous_cleaned_matches.csv",
        identity_dir / "unmatched_cleaned_rows.csv",
    ]
    absent = [str(path) for path in required if not path.is_file()]
    if absent:
        raise FileNotFoundError("Missing required saved artifacts: " + "; ".join(absent))

    expected_report_sha = identity_sidecar.read_text(encoding="ascii").strip().split()[0].lower()
    actual_report_sha = sha256_file(identity_report_path)
    if actual_report_sha != expected_report_sha:
        raise ValueError("canonical identity report checksum does not match its sidecar")

    identity_report = load_json(identity_report_path)
    recon_report = load_json(recon_report_path)
    source_audit = load_json(source_audit_path)
    if identity_report.get("status") != "PASS":
        raise ValueError("canonical identity report is not PASS")
    if identity_report["counts"]["duplicate_id_groups"] != 0:
        raise ValueError("canonical table has duplicate ID groups")
    if identity_report["counts"]["sha256_collision_groups"] != 0:
        raise ValueError("canonical table has ID hash collisions")
    if identity_report["counts"]["crosswalk_errors"] != 0:
        raise ValueError("canonical crosswalk reports errors")
    if recon_report["matching"]["raw_eligible_rows_without_cleaned_exact_match"] != 0:
        raise ValueError("saved audit reports eligible raw rows without a cleaned match")
    if recon_report["cleaned"]["delay_15_vs_DEP_DELAY_at_15_mismatch_rows"] != 0:
        raise ValueError("cleaned target parity is not verified")
    return {
        "identity_report": identity_report,
        "reconciliation_report": recon_report,
        "source_audit": source_audit,
        "identity_report_sha256": actual_report_sha,
        "scratch_db": db_path,
        "source_audit_path": source_audit_path,
        "reconciliation_report_path": recon_report_path,
    }


def check_query_plan(db: sqlite3.Connection, query: str) -> list[str]:
    plan = [str(row[3]) for row in db.execute("EXPLAIN QUERY PLAN " + query)]
    joined = " ".join(plan).upper()
    if "AUTOMATIC" in joined:
        raise RuntimeError("query plan would create an automatic index; refusing to proceed")
    if "RK_KEY_IDX" not in joined or "CK_KEY_IDX" not in joined:
        raise RuntimeError("query plan does not use the saved raw/clean key indexes")
    return plan


def append_cleaned_unmatched_exceptions(path: Path, writer: csv.DictWriter) -> int:
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            writer.writerow({
                "horizon": "not_assessable_without_raw_match",
                "source_layer": "cleaned_input",
                "status": "cleaned_row_unmatched_to_raw_source",
                "cleaned_row_ordinal": row.get("cleaned_first_data_row_ordinal", ""),
                "cleaned_row_sha256": row.get("cleaned_row_sha256", ""),
                "raw_key_candidate_count": 0,
                "reason": row.get("reason", "No exact raw BTS source match"),
            })
            count += 1
    return count


def run(recon_dir: Path, identity_dir: Path, output_dir: Path) -> dict[str, Any]:
    recon_dir = resolve(ROOT, recon_dir)
    identity_dir = resolve(ROOT, identity_dir)
    output_dir = resolve(ROOT, output_dir)
    stage_dir = output_dir.with_name(output_dir.name + ".staging")
    if output_dir.exists():
        raise FileExistsError("Refusing to overwrite existing output directory: " + str(output_dir))
    if stage_dir.exists():
        raise FileExistsError("Preserving existing staging directory: " + str(stage_dir))

    inputs = validate_inputs(ROOT, recon_dir, identity_dir)
    identity_report = inputs["identity_report"]
    recon_report = inputs["reconciliation_report"]
    source_audit = inputs["source_audit"]
    db_path: Path = inputs["scratch_db"]
    db = sqlite3.connect("file:" + db_path.as_posix() + "?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        table_names = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        index_names = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        if not {"raw", "clean", "rk", "ck"}.issubset(table_names):
            raise RuntimeError("retained reconciliation DB is missing expected tables")
        if not {"rk_key_idx", "ck_key_idx"}.issubset(index_names):
            raise RuntimeError("retained reconciliation DB is missing expected key indexes")

        query = """
            SELECT r.id, r.month, r.source_row, r.archive, r.member, r.archive_sha,
                   r.raw_sig, r.k, r.vals, r.eligible, r.cancelled, r.depdel15,
                   r.identity_missing, r.identity_json, rk.n AS raw_candidates,
                   ck.n AS clean_occurrences, ck.first_ord AS clean_first_ordinal
            FROM raw AS r
            LEFT JOIN rk ON rk.k = r.k
            LEFT JOIN ck ON ck.k = r.k
            WHERE r.eligible = 1 AND r.cancelled = '0' AND r.depdel15 IN ('0', '1')
            ORDER BY r.id
        """
        query_plan = check_query_plan(db, query)

        counts: dict[str, dict[str, Any]] = {}
        ambiguous_keys: dict[str, set[str]] = {}
        id_set_counts = {
            "all_valid_canonical_ids": Counter(),
            "unambiguous_cleaned_matched_ids": Counter(),
        }
        for horizon in HORIZONS:
            counts[horizon] = Counter()
            ambiguous_keys[horizon] = set()

        stage_dir.mkdir(parents=True)
        members_paths = {
            key: stage_dir / (key + "_membership.csv.gz") for key in HORIZONS
        }
        exception_path = stage_dir / "cohort_exceptions.csv"
        writers = {}
        exception_count = 0
        unknown_delay_rows = 0
        row_query_count = 0

        with gzip_csv_writer(members_paths["t_minus_2h"], MEMBERSHIP_FIELDS) as member2, \
             gzip_csv_writer(members_paths["t_minus_30m"], MEMBERSHIP_FIELDS) as member30, \
             exception_path.open("w", encoding="utf-8", newline="") as exception_handle:
            exception_writer = csv.DictWriter(
                exception_handle, fieldnames=EXCEPTION_FIELDS,
                extrasaction="ignore", lineterminator="\n",
            )
            exception_writer.writeheader()
            writers = {"t_minus_2h": member2, "t_minus_30m": member30}

            cursor = db.execute(query)
            for row in cursor:
                row_query_count += 1
                values = json.loads(row["vals"])
                if len(values) != 13:
                    raise ValueError("unexpected saved raw value width at row " + str(row["id"]))
                delay = as_decimal(values[9])
                missing_identity = json.loads(row["identity_missing"] or "[]")
                raw_candidates = int(row["raw_candidates"]) if row["raw_candidates"] is not None else None
                clean_occurrences = int(row["clean_occurrences"]) if row["clean_occurrences"] is not None else None
                date = str(values[3])
                source_common = {
                    "FlightDate": date,
                    "Reporting_Airline": str(values[4]),
                    "Origin": str(values[5]),
                    "Dest": str(values[6]),
                    "CRSDepTime": str(values[7]),
                    "DepDel15": str(row["depdel15"]),
                    "final_DEP_DELAY": str(values[9]),
                    "Cancelled": str(row["cancelled"]),
                    "reconciliation_raw_row_id": str(row["id"]),
                    "source_month": str(row["month"]),
                    "source_archive": str(row["archive"]),
                    "source_archive_sha256": str(row["archive_sha"]),
                    "source_csv_member": str(row["member"]),
                    "source_data_row_ordinal": str(row["source_row"]),
                    "cleaned_first_row_ordinal": (
                        str(row["clean_first_ordinal"])
                        if row["clean_first_ordinal"] is not None else ""
                    ),
                    "raw_key_candidate_count": (
                        str(raw_candidates) if raw_candidates is not None else ""
                    ),
                    "cleaned_key_occurrences": (
                        str(clean_occurrences) if clean_occurrences is not None else ""
                    ),
                    "identity_missing_fields": json.dumps(missing_identity, separators=(",", ":")),
                }

                if delay is None:
                    unknown_delay_rows += 1
                    exception_writer.writerow({
                        **source_common,
                        "horizon": "unknown",
                        "source_layer": "raw_source",
                        "status": "missing_DEP_DELAY_cutoff_cannot_be_assessed",
                        "reason": "Saved eligible source row has no parseable final DEP_DELAY.",
                    })
                    exception_count += 1
                    continue

                status = match_status(raw_candidates, clean_occurrences,
                                      missing_identity, row["identity_json"])
                eligible = {
                    horizon: eligible_at_horizon(delay, minutes)
                    for horizon, minutes in HORIZONS.items()
                }
                identity_is_valid = bool(not missing_identity and row["identity_json"])
                if identity_is_valid:
                    for horizon, is_eligible in eligible.items():
                        if is_eligible:
                            id_set_counts["all_valid_canonical_ids"][horizon] += 1
                    if all(eligible.values()):
                        id_set_counts["all_valid_canonical_ids"]["intersection"] += 1
                is_unambiguous_match = status == "unique_exact_match_valid_canonical_id"
                if is_unambiguous_match:
                    for horizon, is_eligible in eligible.items():
                        if is_eligible:
                            id_set_counts["unambiguous_cleaned_matched_ids"][horizon] += 1
                    if all(eligible.values()):
                        id_set_counts["unambiguous_cleaned_matched_ids"]["intersection"] += 1

                for horizon, minutes in HORIZONS.items():
                    if not eligible[horizon]:
                        continue
                    c = counts[horizon]
                    c["raw_eligible_source_records"] += 1
                    if not missing_identity and row["identity_json"]:
                        c["source_records_with_valid_canonical_identity"] += 1
                    if status == "unique_exact_match_valid_canonical_id":
                        normalized, flight_id = make_flight_id(row["identity_json"])
                        c["unambiguously_mapped_records_with_id"] += 1
                        writers[horizon].writerow({
                            **source_common,
                            "flight_id": flight_id,
                            "identity_version": identity_report["identity_version"],
                            "horizon": horizon,
                            "eligibility_rule": "Cancelled=0 and final DEP_DELAY > -" + str(minutes),
                            "Flight_Number_Reporting_Airline": normalized["Flight_Number_Reporting_Airline"],
                            "DepDel15": str(row["depdel15"]),
                            "final_DEP_DELAY_used_for_retrospective_eligibility": str(values[9]),
                        })
                    else:
                        key_count = c
                        key_count["unresolved_source_records"] += 1
                        if status == "ambiguous_raw_candidates":
                            c["ambiguous_raw_candidate_records"] += 1
                            ambiguous_keys[horizon].add(str(row["k"]))
                        elif status == "raw_source_unmatched_to_cleaned_input":
                            c["raw_eligible_source_records_without_cleaned_match"] += 1
                        elif status == "unique_source_match_identity_incomplete":
                            c["incomplete_identity_source_records"] += 1
                        else:
                            c["unclassified_source_records"] += 1
                        exception_writer.writerow({
                            **source_common,
                            "horizon": horizon,
                            "source_layer": "raw_source",
                            "status": status,
                            "eligibility_rule": "Cancelled=0 and final DEP_DELAY > -" + str(minutes),
                            "reason": "No defensible cleaned-row-to-canonical-ID mapping.",
                        })
                        exception_count += 1

            unmatched_path = identity_dir / "unmatched_cleaned_rows.csv"
            exception_count += append_cleaned_unmatched_exceptions(
                unmatched_path, exception_writer
            )

        for horizon, c in counts.items():
            c["ambiguous_cleaned_groups"] = len(ambiguous_keys[horizon])
            c["unique_canonical_flight_ids_in_raw_eligible_cohort"] = c.get(
                "source_records_with_valid_canonical_identity", 0
            )
            c["defensible_id_matched_candidate_records"] = c.get(
                "unambiguously_mapped_records_with_id", 0
            )
            c["final_both_models_scored_records"] = None
            c["final_both_models_scored_reason"] = (
                "No horizon-specific row-level Flight/Weather and Airport/Network "
                "score artifacts were available; no inference was run."
            )
            category_sum = (
                c.get("unambiguously_mapped_records_with_id", 0)
                + c.get("ambiguous_raw_candidate_records", 0)
                + c.get("raw_eligible_source_records_without_cleaned_match", 0)
                + c.get("incomplete_identity_source_records", 0)
                + c.get("unclassified_source_records", 0)
            )
            if category_sum != c.get("raw_eligible_source_records", 0):
                raise RuntimeError("horizon eligibility categories do not sum to raw cohort")
            if c.get("raw_eligible_source_records_without_cleaned_match", 0) != 0:
                raise RuntimeError("eligible raw row lacked an exact cleaned match")
            if c.get("unclassified_source_records", 0) != 0:
                raise RuntimeError("unclassified eligible raw row encountered")

        t2 = counts["t_minus_2h"]
        t30 = counts["t_minus_30m"]
        raw_id_t2 = t2["unique_canonical_flight_ids_in_raw_eligible_cohort"]
        raw_id_t30 = t30["unique_canonical_flight_ids_in_raw_eligible_cohort"]
        mapped_t2 = t2["defensible_id_matched_candidate_records"]
        mapped_t30 = t30["defensible_id_matched_candidate_records"]
        if raw_id_t30 > raw_id_t2 or mapped_t30 > mapped_t2:
            raise RuntimeError("T−30m eligibility is not a subset of T−2h eligibility")

        with (identity_dir / "unmatched_cleaned_rows.csv").open(
            encoding="utf-8-sig", newline=""
        ) as unmatched_handle:
            clean_unmatched_count = sum(1 for _ in csv.DictReader(unmatched_handle))
        report = {
            "status": "PASS",
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "scope": "Saved 2024 BTS/reconciliation artifacts only; retrospective cohort membership, no model scoring.",
            "identity_version": identity_report["identity_version"],
            "query_rows_read_from_saved_database": row_query_count,
            "query_plan": query_plan,
            "inputs": {
                "canonical_table": {
                    "path": str(identity_dir / "canonical_flights.csv.gz"),
                    "rows_recorded": identity_report["counts"]["canonical_flights"],
                    **identity_report["output_sha256"]["canonical_flights.csv.gz"],
                },
                "cleaned_crosswalk": {
                    "path": str(identity_dir / "cleaned_to_canonical_crosswalk.csv.gz"),
                    "rows_recorded": identity_report["counts"]["crosswalk_original_rows"],
                    **identity_report["output_sha256"]["cleaned_to_canonical_crosswalk.csv.gz"],
                },
                "identity_run_report": {
                    "path": str(identity_dir / "canonical_identity_run_report.json"),
                    "sha256_verified_from_sidecar": inputs["identity_report_sha256"],
                },
                "reconciliation_report": {
                    "path": str(inputs["reconciliation_report_path"]),
                    "raw_rows": recon_report["raw_bts"]["source_rows"],
                    "cleaned_rows": recon_report["cleaned"]["rows_before_dedup"],
                    "deduplicated_cleaned_rows": recon_report["cleaned"]["rows_after_dedup"],
                    "target_mismatch_rows": recon_report["cleaned"]["delay_15_vs_DEP_DELAY_at_15_mismatch_rows"],
                },
                "horizon_audit_report": {
                    "path": str(inputs["source_audit_path"]),
                    "id_valid_t2h_rows_recorded": source_audit["reconstructed"]["an_tminus2h_retrospective_distinct_records"],
                    "id_valid_t30m_rows_recorded": source_audit["reconstructed"]["an_tminus30m_retrospective_distinct_records"],
                },
                "reconciliation_sqlite": {
                    "path": str(db_path),
                    "bytes": db_path.stat().st_size,
                    "sha256": None,
                    "sha256_note": "Not recalculated because this retained database is approximately 20 GB; read-only schema and indexed joins were used.",
                    "tables_read": ["raw", "rk", "ck"],
                    "indexes_used": ["rk_key_idx", "ck_key_idx"],
                },
                "cleaned_only_unmatched_rows": clean_unmatched_count,
            },
            "horizons": {
                "t_minus_2h": dict(t2),
                "t_minus_30m": dict(t30),
            },
            "canonical_id_set_comparison": {
                "raw_eligible_ids": {
                    **nested_id_set_comparison(
                        raw_id_t2, raw_id_t30,
                        id_set_counts["all_valid_canonical_ids"]["intersection"],
                    ),
                    "basis": "Intersection was counted row by row while evaluating both strict final-DEP_DELAY cutoffs. Global canonical ID duplicate and hash-collision audits both passed.",
                },
                "unambiguous_cleaned_matched_ids": {
                    **nested_id_set_comparison(
                        mapped_t2, mapped_t30,
                        id_set_counts["unambiguous_cleaned_matched_ids"]["intersection"],
                    ),
                    "basis": "Intersection was counted row by row among unique raw-to-cleaned matches; each row is checked against both strict cutoffs.",
                },
            },
            "exceptions": {
                "raw_source_unmatched_rows_by_horizon": {
                    "t2h": t2.get("raw_eligible_source_records_without_cleaned_match", 0),
                    "t30m": t30.get("raw_eligible_source_records_without_cleaned_match", 0),
                },
                "cleaned_rows_without_raw_match": clean_unmatched_count,
                "quarantined_identity_rows_total": identity_report["counts"]["quarantined_rows"],
                "incomplete_identity_records_by_horizon": {
                    "t2h": t2.get("incomplete_identity_source_records", 0),
                    "t30m": t30.get("incomplete_identity_source_records", 0),
                },
                "ambiguous_raw_candidate_records_by_horizon": {
                    "t2h": t2.get("ambiguous_raw_candidate_records", 0),
                    "t30m": t30.get("ambiguous_raw_candidate_records", 0),
                },
                "exception_file_rows": exception_count,
            },
            "validation": {
                "expected_source_target_eligible_rows": recon_report["raw_bts"]["noncancelled_binary_target_inmonth_eligible_rows"],
                "target_mismatch_rows": recon_report["cleaned"]["delay_15_vs_DEP_DELAY_at_15_mismatch_rows"],
                "duplicate_flight_id_groups": identity_report["counts"]["duplicate_id_groups"],
                "flight_id_hash_collision_groups": identity_report["counts"]["sha256_collision_groups"],
                "canonical_crosswalk_errors": identity_report["counts"]["crosswalk_errors"],
                "raw_archives_opened": 0,
                "indexes_created": 0,
                "features_generated": 0,
                "models_scored_or_trained": 0,
            },
            "membership_files": {},
            "exception_file": {},
            "limitations": [
                "Eligibility is retrospective and uses final BTS cancellation and DEP_DELAY outcomes.",
                "Unambiguous canonical-ID membership is not proof that either model has row-level scores.",
                "No final paired model-scored cohort size is claimed.",
                "Airport/Network feature availability timestamps remain unverified for live use.",
            ],
        }
        if sum((t2.get("raw_eligible_source_records", 0), t30.get("raw_eligible_source_records", 0))) == 0:
            raise RuntimeError("no horizon-eligible rows were found")
        if t2.get("unique_canonical_flight_ids_in_raw_eligible_cohort") != source_audit["reconstructed"]["an_tminus2h_retrospective_distinct_records"]:
            raise RuntimeError("T−2h ID-valid count differs from the saved canonical audit")
        if t30.get("unique_canonical_flight_ids_in_raw_eligible_cohort") != source_audit["reconstructed"]["an_tminus30m_retrospective_distinct_records"]:
            raise RuntimeError("T−30m ID-valid count differs from the saved canonical audit")

        for horizon, path in members_paths.items():
            report["membership_files"][horizon] = {
                "path": str(output_dir / path.name),
                "rows": counts[horizon].get("unambiguously_mapped_records_with_id", 0),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        report["exception_file"] = {
            "path": str(output_dir / exception_path.name),
            "rows": exception_count,
            "bytes": exception_path.stat().st_size,
            "sha256": sha256_file(exception_path),
        }
        report_path = stage_dir / "cohort_reconciliation_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report_sha = sha256_file(report_path)
        (stage_dir / "cohort_reconciliation_report.sha256").write_text(
            report_sha + "  cohort_reconciliation_report.json\n", encoding="ascii"
        )
        checksum_lines = []
        for name in [*map(lambda p: p.name, members_paths.values()), exception_path.name,
                     report_path.name]:
            path = stage_dir / name
            checksum_lines.append(sha256_file(path) + "  " + name)
        (stage_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")
        stage_dir.rename(output_dir)
        report["output_directory"] = str(output_dir)
        report["report_sha256"] = report_sha
        return report
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconciliation-dir", type=Path, default=DEFAULT_RECON)
    parser.add_argument("--identity-dir", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = run(args.reconciliation_dir, args.identity_dir, args.output_dir)
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

