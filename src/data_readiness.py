#!/usr/bin/env python3
"""Report local canonical-data readiness without rescanning BTS source archives.

This command reads small manifests and headers by default.  ``--verify-hashes``
hashes the named local files and can be slow for multi-gigabyte exports; it
never writes, uploads, or regenerates data.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def header(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
        return stream.readline().rstrip("\r\n").split(",")


def file_report(path: Path, expected_hash: str | None, verify: bool) -> dict[str, Any]:
    present = path.is_file()
    item: dict[str, Any] = {"path": str(path), "present": present}
    if not present:
        item["verification_status"] = "MISSING"
        return item
    item.update({"bytes": path.stat().st_size, "schema_header": header(path)})
    if expected_hash:
        item["recorded_sha256"] = expected_hash
    if verify:
        actual = digest(path)
        item["actual_sha256"] = actual
        if expected_hash is None:
            item["hash_matches_record"] = None
            item["verification_status"] = "NOT_VERIFIABLE"
        elif actual == expected_hash:
            item["hash_matches_record"] = True
            item["verification_status"] = "PASS"
        else:
            item["hash_matches_record"] = False
            item["verification_status"] = "MISMATCH"
    else:
        item["verification_status"] = "RECORDED_NOT_RECOMPUTED" if expected_hash else "NOT_VERIFIABLE"
    return item


def verification_failures(report: dict[str, Any]) -> list[str]:
    """Return present files whose requested hash verification is not a pass.

    Strict verification fails for:
    - Files present with no recorded expected hash (NOT_VERIFIABLE)
    - Files present with hash mismatch (MISMATCH)
    """
    failures: list[str] = []

    def visit(value: Any, label: str) -> None:
        if isinstance(value, dict):
            if value.get("present") and value.get("verification_status") in {"NOT_VERIFIABLE", "MISMATCH"}:
                failures.append(label)
            for key, child in value.items():
                visit(child, f"{label}.{key}" if label else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{label}[{index}]")

    visit(report, "")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-hashes", action="store_true")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    identity_path = PROCESSED / "canonical_identity_2024_20261015_v1" / "canonical_identity_run_report.json"
    cohort_path = PROCESSED / "shared_cohort_2024_20261016_v1" / "cohort_reconciliation_report.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8")) if identity_path.is_file() else {}
    cohort = json.loads(cohort_path.read_text(encoding="utf-8")) if cohort_path.is_file() else {}
    mapping_manifest_path = PROCESSED / "phase2_handoff_2024" / "mapping_manifest.json"
    mapping_manifest = json.loads(mapping_manifest_path.read_text(encoding="utf-8")) if mapping_manifest_path.is_file() else {}
    score_manifest_path = PROCESSED / "flight_weather_score_exports_2024_h2_v1" / "run_manifest.json"
    score_manifest = json.loads(score_manifest_path.read_text(encoding="utf-8")) if score_manifest_path.is_file() else {}
    identity_outputs = identity.get("output_sha256", {})
    membership = cohort.get("membership_files", {})
    score_root = PROCESSED / "flight_weather_score_exports_2024_h2_v1"
    output: dict[str, Any] = {
        "command": "data_readiness", "hashes_verified_now": args.verify_hashes,
        "original_cleaned_source": file_report(Path("data/raw/flights_2024_cleaned_verified.csv.gz"),
            identity.get("cleaned_input_provenance", {}).get("compressed_sha256"), args.verify_hashes),
        "canonical": file_report(PROCESSED / "canonical_identity_2024_20261015_v1" / "canonical_flights.csv.gz",
            identity_outputs.get("canonical_flights.csv.gz", {}).get("sha256"), args.verify_hashes),
        "mapping": file_report(PROCESSED / "phase2_handoff_2024" / "cleaned_row_to_canonical_mapping.csv.gz",
            mapping_manifest.get("output", {}).get("sha256"), args.verify_hashes),
        "horizons": {},
        "known_identity_counts": identity.get("counts", {}),
        "unresolved_policy": "Unresolved/ambiguous IDs are retained as explicit exceptions and never assigned.",
        "airport_network": {"status": "no score export/model manifest found in this worktree", "combined_modelling_ready": False},
    }
    for horizon in ("t_minus_2h", "t_minus_30m"):
        member = membership.get(horizon, {})
        score_name = f"{horizon}_scores.csv.gz"
        output["horizons"][horizon] = {
            "membership": file_report(Path(member.get("path", "")), member.get("sha256"), args.verify_hashes),
            "membership_rows_recorded": member.get("rows"),
            "flight_weather_scores": file_report(score_root / score_name,
                score_manifest.get("outputs", {}).get(horizon, {}).get("output_sha256"), args.verify_hashes),
            "flight_weather_h2_rows_recorded": {"t_minus_2h": 3569958, "t_minus_30m": 3569770}[horizon],
        }
    text = json.dumps(output, indent=2, sort_keys=True)
    failures = verification_failures(output) if args.verify_hashes else []
    output["verification_failures"] = failures
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    text = json.dumps(output, indent=2, sort_keys=True)
    print(text)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
