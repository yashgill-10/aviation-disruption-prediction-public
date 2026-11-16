#!/usr/bin/env python3
"""Independently validate the Phase 2 row-to-canonical-ID mapping.

Reads existing mapping/crosswalk/provenance files only. With
--write-handoff-manifest, writes a new combined checksum list after validation
has passed; it never regenerates or modifies the mapping or Phase 1 artifacts.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import itertools
import json
import re
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/processed/phase2_handoff_2024"
IDENTITY = ROOT / "data/processed/canonical_identity_2024_20261015_v1"
RECON = ROOT / "data/processed/airport_network_reconciliation_2024_20261012_retry1"
COHORT = ROOT / "data/processed/shared_cohort_2024_20261016_v1"
MAPPING = OUT / "cleaned_row_to_canonical_mapping.csv.gz"
MAPPING_MANIFEST = OUT / "mapping_manifest.json"
GENERATOR_CHECKSUMS = OUT / "checksums_manifest.txt"
CROSSWALK = IDENTITY / "cleaned_to_canonical_crosswalk.csv.gz"
DUPLICATE_GROUPS = RECON / "cleaned_duplicate_groups.csv"
HANDOFF_CHECKSUMS = OUT / "phase2_handoff_checksums.txt"
MAPPING_FIELDS = ["cleaned_input_row_ordinal", "canonical_flight_id", "match_status"]
EXPECTED_COUNTS = {
    "unique_exact_raw_match_identity_recovered": 6_982_525,
    "ambiguous_multiple_raw_candidates": 220,
    "unique_exact_raw_match_identity_incomplete": 1,
    "no_exact_raw_match": 20,
}
FLIGHT_ID = re.compile(r"^bts-flight-v1:[0-9a-f]{64}$")
HEX = re.compile(r"^[0-9a-f]{64}$")


class DigestReader(io.RawIOBase):
    """A streaming binary reader that hashes and counts bytes as consumed."""

    def __init__(self, source: Any):
        self.source = source
        self.digest = hashlib.sha256()
        self.byte_count = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        data = self.source.read(len(buffer))
        if not data:
            return 0
        size = len(data)
        buffer[:size] = data
        self.digest.update(data)
        self.byte_count += size
        return size


def open_hashed_gzip_text(path: Path):
    """Return (text, compressed digest reader, decompressed digest reader, file)."""
    raw = path.open("rb")
    compressed = DigestReader(raw)
    compressed_buffer = io.BufferedReader(compressed)
    gz = gzip.GzipFile(fileobj=compressed_buffer, mode="rb")
    decompressed = DigestReader(gz)
    text = io.TextIOWrapper(io.BufferedReader(decompressed), encoding="utf-8", newline="")
    return text, compressed, decompressed, raw


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parse_ordinals(value: str, label: str) -> tuple[int, ...]:
    try:
        values = tuple(int(part) for part in value.split(";") if part)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid ordinal list") from exc
    if not values or any(n < 1 for n in values) or tuple(sorted(set(values))) != values:
        raise ValueError(f"{label}: ordinals must be positive, unique, and ascending")
    return values


def load_duplicate_provenance(path: Path) -> tuple[dict[int, tuple[str, int, int]], dict[str, int]]:
    by_ordinal: dict[int, tuple[str, int, int]] = {}
    groups = 0
    extra_rows = 0
    required = {
        "row_sha256", "occurrences", "duplicate_rows_beyond_first",
        "original_data_row_ordinals", "sha256_collision_checked",
    }
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("duplicate-group provenance schema mismatch")
        for line, row in enumerate(reader, 2):
            fingerprint = row["row_sha256"].strip()
            if not HEX.fullmatch(fingerprint):
                raise ValueError(f"{path}:{line}: invalid row fingerprint")
            ordinals = parse_ordinals(row["original_data_row_ordinals"], f"{path}:{line}")
            occurrences = int(row["occurrences"])
            beyond_first = int(row["duplicate_rows_beyond_first"])
            if occurrences != len(ordinals) or occurrences < 2 or beyond_first != occurrences - 1:
                raise ValueError(f"{path}:{line}: duplicate counts disagree with ordinals")
            if row["sha256_collision_checked"].strip().lower() != "true":
                raise ValueError(f"{path}:{line}: hash-collision check is not confirmed")
            groups += 1
            extra_rows += beyond_first
            for ordinal in ordinals:
                if ordinal in by_ordinal:
                    raise ValueError(f"{path}:{line}: ordinal appears in multiple groups")
                by_ordinal[ordinal] = (fingerprint, ordinals[0], occurrences)
    return by_ordinal, {"groups": groups, "rows_beyond_first": extra_rows, "original_rows": len(by_ordinal)}


def verify_generator_checksums(mapping_sha: str, manifest_sha: str) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line_number, line in enumerate(GENERATOR_CHECKSUMS.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            digest, filename = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(f"generator checksum line {line_number}: invalid syntax") from exc
        if not HEX.fullmatch(digest) or Path(filename).name != filename:
            raise ValueError(f"generator checksum line {line_number}: invalid hash or filename")
        if filename in expected:
            raise ValueError(f"generator checksum repeats {filename}")
        expected[filename] = digest
    actual = {
        MAPPING.name: mapping_sha,
        MAPPING_MANIFEST.name: manifest_sha,
    }
    if expected != actual:
        raise ValueError(f"generator checksum manifest mismatch: {expected}")
    return expected


def validate() -> dict[str, Any]:
    for path in (MAPPING, MAPPING_MANIFEST, GENERATOR_CHECKSUMS, CROSSWALK, DUPLICATE_GROUPS):
        if not path.is_file():
            raise FileNotFoundError(f"required validation input missing: {path}")
    manifest = json.loads(MAPPING_MANIFEST.read_text(encoding="utf-8"))
    mapping_info = manifest.get("output", {})
    input_info = manifest.get("inputs", {})
    expected_crosswalk = input_info.get("crosswalk", {})
    expected_duplicates = input_info.get("duplicate_groups", {})
    if not expected_crosswalk or not expected_duplicates:
        raise ValueError("mapping manifest lacks crosswalk or duplicate-provenance metadata")

    dup_by_ordinal, dup_stats = load_duplicate_provenance(DUPLICATE_GROUPS)
    mapping_text, mapping_compressed, mapping_decompressed, mapping_file = open_hashed_gzip_text(MAPPING)
    cross_text, cross_compressed, cross_decompressed, cross_file = open_hashed_gzip_text(CROSSWALK)
    counts: Counter[str] = Counter()
    total = mapped = 0
    id_batch: list[tuple[str]] = []
    sentinel = object()
    try:
        mapping_reader = csv.DictReader(mapping_text)
        cross_reader = csv.DictReader(cross_text)
        if mapping_reader.fieldnames != MAPPING_FIELDS:
            raise ValueError(f"mapping columns mismatch: {mapping_reader.fieldnames}")
        cross_required = {
            "cleaned_input_row_ordinal", "cleaned_row_sha256",
            "cleaned_duplicate_first_ordinal", "cleaned_duplicate_group_occurrences",
            "canonical_flight_id", "match_status",
        }
        if not cross_reader.fieldnames or not cross_required.issubset(cross_reader.fieldnames):
            raise ValueError("saved crosswalk is missing required comparison columns")

        with tempfile.TemporaryDirectory(prefix="phase2_mapping_validation_") as temp_dir:
            db = sqlite3.connect(str(Path(temp_dir) / "mapped_ids.sqlite"))
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("CREATE TABLE ids (flight_id TEXT PRIMARY KEY) WITHOUT ROWID")
            for ordinal, pair in enumerate(
                itertools.zip_longest(mapping_reader, cross_reader, fillvalue=sentinel), 1
            ):
                mapped_row, cross_row = pair
                if mapped_row is sentinel or cross_row is sentinel:
                    raise ValueError(f"mapping/crosswalk row-count mismatch at ordinal {ordinal}")
                total = ordinal
                ordinal_text = str(ordinal)
                flight_id = mapped_row["canonical_flight_id"] or ""
                status = mapped_row["match_status"] or ""
                if mapped_row["cleaned_input_row_ordinal"] != ordinal_text:
                    raise ValueError(f"mapping ordinal gap or duplicate at row {ordinal}")
                if (
                    cross_row["cleaned_input_row_ordinal"] != ordinal_text
                    or (cross_row["canonical_flight_id"] or "") != flight_id
                    or cross_row["match_status"] != status
                ):
                    raise ValueError(f"mapping differs from saved crosswalk at ordinal {ordinal}")
                if status not in EXPECTED_COUNTS:
                    raise ValueError(f"unknown match status at ordinal {ordinal}: {status}")
                counts[status] += 1

                provenance = dup_by_ordinal.get(ordinal)
                if provenance:
                    fingerprint, first_ordinal, occurrences = provenance
                    if (
                        cross_row["cleaned_row_sha256"] != fingerprint
                        or int(cross_row["cleaned_duplicate_first_ordinal"]) != first_ordinal
                        or int(cross_row["cleaned_duplicate_group_occurrences"]) != occurrences
                        or status != "ambiguous_multiple_raw_candidates"
                        or flight_id
                    ):
                        raise ValueError(f"duplicate provenance mismatch at ordinal {ordinal}")
                elif status == "ambiguous_multiple_raw_candidates":
                    raise ValueError(f"ambiguous row lacks duplicate provenance at ordinal {ordinal}")
                elif (
                    int(cross_row["cleaned_duplicate_first_ordinal"]) != ordinal
                    or int(cross_row["cleaned_duplicate_group_occurrences"]) != 1
                ):
                    raise ValueError(f"unexpected duplicate metadata at ordinal {ordinal}")

                if status == "unique_exact_raw_match_identity_recovered":
                    if not FLIGHT_ID.fullmatch(flight_id):
                        raise ValueError(f"invalid canonical ID at ordinal {ordinal}")
                    id_batch.append((flight_id,))
                    mapped += 1
                    if len(id_batch) >= 25_000:
                        try:
                            db.executemany("INSERT INTO ids VALUES (?)", id_batch)
                        except sqlite3.IntegrityError as exc:
                            raise ValueError("canonical IDs are not unique among mapped rows") from exc
                        id_batch.clear()
                elif flight_id:
                    raise ValueError(f"unresolved row has a canonical ID at ordinal {ordinal}")
            if id_batch:
                try:
                    db.executemany("INSERT INTO ids VALUES (?)", id_batch)
                except sqlite3.IntegrityError as exc:
                    raise ValueError("canonical IDs are not unique among mapped rows") from exc
            db.commit()
            unique_ids = db.execute("SELECT COUNT(*) FROM ids").fetchone()[0]
            db.close()
    finally:
        mapping_text.close()
        cross_text.close()
        mapping_file.close()
        cross_file.close()

    actual_counts = {status: counts[status] for status in EXPECTED_COUNTS}
    if total != 6_982_766 or actual_counts != EXPECTED_COUNTS:
        raise ValueError(f"mapping row/status counts differ from expected: {total}, {actual_counts}")
    if mapped != 6_982_525 or unique_ids != mapped:
        raise ValueError(f"mapped/unique ID counts disagree: mapped={mapped}, unique={unique_ids}")
    if dup_stats != {"groups": 110, "rows_beyond_first": 110, "original_rows": 220}:
        raise ValueError(f"duplicate-group totals differ: {dup_stats}")
    if mapping_compressed.byte_count != MAPPING.stat().st_size:
        raise ValueError("mapping compressed byte count differs from file size")
    if cross_compressed.byte_count != CROSSWALK.stat().st_size:
        raise ValueError("crosswalk compressed byte count differs from file size")

    mapping_sha = mapping_compressed.digest.hexdigest()
    mapping_uncompressed_sha = mapping_decompressed.digest.hexdigest()
    crosswalk_sha = cross_compressed.digest.hexdigest()
    if mapping_sha != mapping_info.get("sha256") or mapping_compressed.byte_count != mapping_info.get("compressed_bytes"):
        raise ValueError("mapping compressed hash/size disagrees with mapping_manifest.json")
    if crosswalk_sha != expected_crosswalk.get("sha256") or cross_compressed.byte_count != expected_crosswalk.get("bytes"):
        raise ValueError("saved crosswalk hash/size disagrees with mapping_manifest.json")
    if sha256_file(DUPLICATE_GROUPS) != expected_duplicates.get("sha256") or DUPLICATE_GROUPS.stat().st_size != expected_duplicates.get("bytes"):
        raise ValueError("duplicate-group provenance hash/size disagrees with mapping_manifest.json")
    manifest_sha = sha256_file(MAPPING_MANIFEST)
    generator_checks = verify_generator_checksums(mapping_sha, manifest_sha)
    if mapping_info.get("rows") != total or mapping_info.get("columns") != MAPPING_FIELDS:
        raise ValueError("mapping output metadata disagrees with independent validation")
    if manifest.get("status_counts") != actual_counts or manifest.get("mapped_rows") != mapped or manifest.get("unresolved_rows") != total - mapped:
        raise ValueError("mapping status metadata disagrees with independent validation")
    provenance_meta = manifest.get("duplicate_provenance", {})
    if provenance_meta.get("groups") != dup_stats["groups"] or provenance_meta.get("original_duplicate_rows") != dup_stats["original_rows"] or provenance_meta.get("rows_beyond_first") != dup_stats["rows_beyond_first"] or provenance_meta.get("ambiguous_original_rows") != len(dup_by_ordinal):
        raise ValueError("duplicate-provenance metadata disagrees with the source file")

    return {
        "status": "PASS",
        "mapping": {
            "path": str(MAPPING), "rows": total, "status_counts": actual_counts,
            "mapped_unique_ids": unique_ids,
            "compressed_bytes": mapping_compressed.byte_count, "compressed_sha256": mapping_sha,
            "decompressed_bytes": mapping_decompressed.byte_count,
            "decompressed_sha256": mapping_uncompressed_sha,
        },
        "crosswalk": {"compressed_bytes": cross_compressed.byte_count, "sha256": crosswalk_sha},
        "duplicate_provenance": dup_stats,
        "mapping_manifest": {"path": str(MAPPING_MANIFEST), "bytes": MAPPING_MANIFEST.stat().st_size, "sha256": manifest_sha},
        "generator_checksum_entries": generator_checks,
    }


def create_combined_manifest(summary: dict[str, Any]) -> dict[str, Any]:
    if HANDOFF_CHECKSUMS.exists():
        raise FileExistsError(f"refusing to overwrite {HANDOFF_CHECKSUMS}")
    relative_paths = [
        MAPPING.relative_to(ROOT), MAPPING_MANIFEST.relative_to(ROOT),
        COHORT / "t_minus_2h_membership.csv.gz", COHORT / "t_minus_30m_membership.csv.gz",
        COHORT / "cohort_exceptions.csv", COHORT / "cohort_reconciliation_report.json",
        COHORT / "cohort_reconciliation_report.sha256",
    ]
    entries: list[tuple[str, str]] = []
    for path in relative_paths:
        absolute = path if path.is_absolute() else ROOT / path
        if not absolute.is_file():
            raise FileNotFoundError(f"handoff artifact missing: {absolute}")
        entries.append((sha256_file(absolute), absolute.relative_to(ROOT).as_posix()))
    report_entry = next((digest for digest, name in entries if name.endswith("cohort_reconciliation_report.json")), None)
    sidecar_path = COHORT / "cohort_reconciliation_report.sha256"
    sidecar = sidecar_path.read_text(encoding="utf-8").strip().split()
    if len(sidecar) != 2 or sidecar[0] != report_entry or Path(sidecar[1]).name != "cohort_reconciliation_report.json":
        raise ValueError("cohort report sidecar does not match the report bytes")

    content = "".join(f"{digest}  {name}\n" for digest, name in entries)
    with HANDOFF_CHECKSUMS.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
    parsed: list[tuple[str, str]] = []
    for line_number, line in enumerate(HANDOFF_CHECKSUMS.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(f"combined manifest syntax failure at line {line_number}")
        digest, name = match.groups()
        path = ROOT / Path(name)
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"combined manifest hash verification failed: {name}")
        parsed.append((digest, name))
    if parsed != entries:
        raise ValueError("combined manifest entries differ after reread")
    return {"path": str(HANDOFF_CHECKSUMS), "bytes": HANDOFF_CHECKSUMS.stat().st_size, "entries": entries, "verified": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-handoff-manifest", action="store_true", help="after validation, create a new combined manifest; refuses to overwrite")
    args = parser.parse_args()
    result = validate()
    if args.write_handoff_manifest:
        result["combined_handoff_manifest"] = create_combined_manifest(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())