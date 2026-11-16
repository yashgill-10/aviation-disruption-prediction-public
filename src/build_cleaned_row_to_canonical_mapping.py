#!/usr/bin/env python3
"""Generate the validated Phase 2 cleaned-row to canonical-ID mapping."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import itertools
import json
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = Path("data/processed/canonical_identity_2024_20261015_v1")
RECON = Path("data/processed/airport_network_reconciliation_2024_20261012_retry1")
OUTPUT = Path("data/processed/phase2_handoff_2024")
SOURCE_FIELDS = (
    "YEAR", "MONTH", "DAY_OF_MONTH", "FL_DATE", "OP_UNIQUE_CARRIER",
    "ORIGIN", "DEST", "CRS_DEP_TIME", "DEP_TIME", "DEP_DELAY",
    "CANCELLED", "DIVERTED", "delay_15",
)
CROSS_FIELDS = (
    "cleaned_input_row_ordinal", "cleaned_row_sha256",
    "cleaned_duplicate_first_ordinal", "cleaned_duplicate_group_occurrences",
    "canonical_flight_id", "identity_version", "match_status",
    "raw_candidate_count", "reconciliation_raw_row_id",
)
CROSS_FIELDS = ("cleaned_input_row_ordinal", "cleaned_row_sha256", "cleaned_duplicate_first_ordinal", "cleaned_duplicate_group_occurrences", "canonical_flight_id", "identity_version", "match_status", "raw_candidate_count", "reconciliation_raw_row_id")
DUPLICATE_FIELDS = ("row_sha256", "occurrences", "duplicate_rows_beyond_first", "original_data_row_ordinals", "sha256_collision_checked")
AMBIGUOUS_FIELDS = ("cleaned_first_data_row_ordinal", "cleaned_row_sha256", "cleaned_duplicate_row_ordinals", "raw_candidate_count")
UNMATCHED_FIELDS = ("cleaned_first_data_row_ordinal", "cleaned_row_sha256", "cleaned_duplicate_row_ordinals")

STATUSES = (
    "unique_exact_raw_match_identity_recovered",
    "ambiguous_multiple_raw_candidates",
    "unique_exact_raw_match_identity_incomplete",
    "no_exact_raw_match",
)
OUTPUT_FIELDS = ("cleaned_input_row_ordinal", "canonical_flight_id", "match_status")
EXPECTED_ROWS = 6_982_766
EXPECTED_COUNTS = {
    STATUSES[0]: 6_982_525,
    STATUSES[1]: 220,
    STATUSES[2]: 1,
    STATUSES[3]: 20,
}
PINNED_HASHES = {
    "cleaned_input_compressed": "18a78b549736d2498c24cb625bdeef8be137aa710ab7b80e8a906dd204571445",
    "cleaned_input_uncompressed": "01a4b75de0615063b24e31edda69d81730059199e76ccea45b37ca9d00379bf1",
    "crosswalk": "3856b381d375bbc1cf04fc1fcbf2df63132ed12ced4e62df0def86b9f195da84",
    "canonical_flights": "62c60f3aa01ac9ff8129f51f49dce7809cfd634429260705312cb0c52b40df3c",
    "identity_quarantine": "bef2613f6d4ad39d50f51d0b7d632363d0fa7922e54dfe714fd56c458e582375",
    "ambiguous_cleaned_matches": "e80a732c2776cc3aa34ec56378c79574e3346b278acd08ba958459c2f808b910",
    "unmatched_cleaned_rows": "54f123fe6c4303992e042f4a6f40f7ae610b2573b4ab09319635d7a5c89aeecc",
}
HEX = re.compile(r"^[0-9a-f]{64}$")
FLIGHT_ID = re.compile(r"^bts-flight-v1:[0-9a-f]{64}$")


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def info(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"required input missing: {path}")
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": file_sha(path)}


def fields_ok(names: list[str] | None, required: tuple[str, ...], label: str) -> None:
    missing = [x for x in required if names is None or x not in names]
    if names is None or missing:
        raise ValueError(f"{label}: missing required fields: {', '.join(missing)}")


def ordinals(text: str, label: str) -> tuple[int, ...]:
    parts = text.split(";") if text else []
    if not parts or any(not x.isdecimal() for x in parts):
        raise ValueError(f"{label}: invalid original row ordinals")
    values = tuple(map(int, parts))
    if any(x < 1 for x in values) or tuple(sorted(set(values))) != values:
        raise ValueError(f"{label}: ordinals must be positive, unique, ascending")
    return values


def duplicate_provenance(path: Path) -> tuple[dict[str, tuple[int, ...]], dict[int, str]]:
    groups, by_ordinal = {}, {}
    required = ("row_sha256", "occurrences", "duplicate_rows_beyond_first",
                "original_data_row_ordinals", "sha256_collision_checked")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields_ok(reader.fieldnames, required, str(path))
        for line, row in enumerate(reader, 2):
            fingerprint = row["row_sha256"].strip()
            if not HEX.fullmatch(fingerprint) or fingerprint in groups:
                raise ValueError(f"{path}:{line}: invalid/repeated fingerprint")
            values = ordinals(row["original_data_row_ordinals"], f"{path}:{line}")
            try:
                count, extra = int(row["occurrences"]), int(row["duplicate_rows_beyond_first"])
            except ValueError as exc:
                raise ValueError(f"{path}:{line}: invalid duplicate count") from exc
            if count != len(values) or count < 2 or extra != count - 1:
                raise ValueError(f"{path}:{line}: counts disagree with ordinal list")
            if row["sha256_collision_checked"].strip().lower() != "true":
                raise ValueError(f"{path}:{line}: duplicate hash collision check not confirmed")
            for n in values:
                if n in by_ordinal:
                    raise ValueError(f"{path}:{line}: ordinal occurs in multiple groups")
                by_ordinal[n] = fingerprint
            groups[fingerprint] = values
    return groups, by_ordinal


def exception_groups(path: Path, ambiguous: bool = False) -> dict[str, tuple[int, ...]]:
    required = ("cleaned_first_data_row_ordinal", "cleaned_row_sha256",
                "cleaned_duplicate_row_ordinals")
    if ambiguous:
        required += ("raw_candidate_count",)
    groups = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields_ok(reader.fieldnames, required, str(path))
        for line, row in enumerate(reader, 2):
            fingerprint = row["cleaned_row_sha256"].strip()
            if not HEX.fullmatch(fingerprint) or fingerprint in groups:
                raise ValueError(f"{path}:{line}: invalid/repeated fingerprint")
            values = ordinals(row["cleaned_duplicate_row_ordinals"], f"{path}:{line}")
            if int(row["cleaned_first_data_row_ordinal"]) != values[0]:
                raise ValueError(f"{path}:{line}: first ordinal disagrees with list")
            if ambiguous and int(row["raw_candidate_count"]) < 2:
                raise ValueError(f"{path}:{line}: ambiguous group has fewer than two candidates")
            groups[fingerprint] = values
    return groups


def quarantine_id(path: Path) -> str:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields_ok(reader.fieldnames, ("reconciliation_raw_row_id",), str(path))
        rows = list(reader)
    if len(rows) != 1 or not rows[0]["reconciliation_raw_row_id"].strip():
        raise ValueError(f"{path}: expected one quarantined source record with an ID")
    return rows[0]["reconciliation_raw_row_id"].strip()


class HashReader(io.RawIOBase):
    def __init__(self, source: Any, digest: Any):
        self.source, self.digest, self.bytes_read = source, digest, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        data = self.source.read(len(buffer))
        if not data:
            return 0
        n = len(data)
        buffer[:n] = data
        self.digest.update(data)
        self.bytes_read += n
        return n

    def close(self) -> None:
        if not self.closed:
            self.source.close()
        super().close()


def verify_output(path: Path, expected_rows: int, expected_counts: dict[str, int]) -> dict[str, Any]:
    counts, total = Counter(), 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != list(OUTPUT_FIELDS):
            raise ValueError("output mapping header mismatch")
        for total, row in enumerate(reader, 1):
            if row["cleaned_input_row_ordinal"] != str(total):
                raise ValueError(f"output ordinal gap or duplicate at row {total}")
            status, flight_id = row["match_status"], row["canonical_flight_id"]
            if status not in STATUSES:
                raise ValueError(f"unknown output status at row {total}")
            if status == STATUSES[0]:
                if not FLIGHT_ID.fullmatch(flight_id or ""):
                    raise ValueError(f"invalid mapped flight ID at row {total}")
            elif flight_id:
                raise ValueError(f"unresolved row has canonical ID at row {total}")
            counts[status] += 1
    actual = {s: counts[s] for s in STATUSES}
    expected = {s: int(expected_counts.get(s, 0)) for s in STATUSES}
    if total != expected_rows or actual != expected:
        raise ValueError(f"output totals mismatch: rows={total}, statuses={actual}")
    return {"rows": total, "status_counts": actual}


def load_canonical_flight_ids(path: Path, db: sqlite3.Connection) -> int:
    """Load the canonical table into the guarded mapping database.

    The crosswalk is not itself proof that a syntactically valid hash belongs to
    the canonical table.  Keep that membership check local and exact.
    """
    db.execute("CREATE TABLE canonical_ids (flight_id TEXT PRIMARY KEY) WITHOUT ROWID")
    count = 0
    batch: list[tuple[str]] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields_ok(reader.fieldnames, ("flight_id",), str(path))
        for line, row in enumerate(reader, 2):
            flight_id = (row.get("flight_id") or "").strip()
            if not FLIGHT_ID.fullmatch(flight_id):
                raise ValueError(f"{path}:{line}: invalid canonical flight ID")
            batch.append((flight_id,))
            count += 1
            if len(batch) >= 25_000:
                try:
                    db.executemany("INSERT INTO canonical_ids VALUES (?)", batch)
                except sqlite3.IntegrityError as exc:
                    raise ValueError("canonical table contains duplicate flight_id values") from exc
                batch.clear()
    if batch:
        try:
            db.executemany("INSERT INTO canonical_ids VALUES (?)", batch)
        except sqlite3.IntegrityError as exc:
            raise ValueError("canonical table contains duplicate flight_id values") from exc
    if count == 0:
        raise ValueError("canonical table is empty")
    return count


def build_mapping(cleaned_input: Path, identity_dir: Path, reconciliation_dir: Path,
                  output_dir: Path, *, expected_rows: int,
                  expected_status_counts: dict[str, int],
                  expected_hashes: dict[str, str] | None) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    paths = {
        "cleaned_input": cleaned_input,
        "crosswalk": identity_dir / "cleaned_to_canonical_crosswalk.csv.gz",
        "canonical_flights": identity_dir / "canonical_flights.csv.gz",
        "identity_quarantine": identity_dir / "identity_quarantine.csv",
        "ambiguous_cleaned_matches": identity_dir / "ambiguous_cleaned_matches.csv",
        "unmatched_cleaned_rows": identity_dir / "unmatched_cleaned_rows.csv",
        "duplicate_groups": reconciliation_dir / "cleaned_duplicate_groups.csv",
    }
    inputs = {key: info(path) for key, path in paths.items()}
    for key, record in inputs.items():
        expected_key = "cleaned_input_compressed" if key == "cleaned_input" else key
        expected = (expected_hashes or {}).get(expected_key)
        if expected and record["sha256"] != expected:
            raise ValueError(f"{key} SHA-256 mismatch")
    duplicate, ordinal_to_hash = duplicate_provenance(paths["duplicate_groups"])
    ambiguous = exception_groups(paths["ambiguous_cleaned_matches"], ambiguous=True)
    unmatched = exception_groups(paths["unmatched_cleaned_rows"])
    if duplicate != ambiguous:
        raise ValueError("duplicate-group ordinals disagree with ambiguous-row provenance")
    if set(duplicate) & set(unmatched):
        raise ValueError("a cleaned group is both ambiguous and unmatched")
    if any(n in ordinal_to_hash for values in unmatched.values() for n in values):
        raise ValueError("unmatched ordinal also appears in duplicate provenance")
    quarantined = quarantine_id(paths["identity_quarantine"])

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent))
    output_path = stage / "cleaned_row_to_canonical_mapping.csv.gz"
    manifest_path = stage / "mapping_manifest.json"
    checksums_path = stage / "checksums_manifest.txt"
    db = sqlite3.connect(stage / "mapped_ids.sqlite")
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("CREATE TABLE ids (flight_id TEXT PRIMARY KEY) WITHOUT ROWID")
    canonical_id_count = load_canonical_flight_ids(paths["canonical_flights"], db)
    counts, duplicate_seen = Counter(), Counter()
    first_rows, ambiguous_seen, unmatched_seen = {}, set(), set()
    mapped = total = 0
    input_digest = hashlib.sha256()
    source = gzip.open(paths["cleaned_input"], "rb")
    hashed = HashReader(source, input_digest)
    source_text = io.TextIOWrapper(io.BufferedReader(hashed), encoding="utf-8-sig", newline="")
    raw_out = output_path.open("wb")
    gzip_out = gzip.GzipFile(filename="", mode="wb", fileobj=raw_out, mtime=0)
    text_out = io.TextIOWrapper(gzip_out, encoding="utf-8", newline="")
    try:
        source_reader = csv.reader(source_text)
        header = next(source_reader, None)
        if tuple(header or ()) != SOURCE_FIELDS:
            raise ValueError(f"source header mismatch: {header}")
        writer = csv.DictWriter(text_out, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        with gzip.open(paths["crosswalk"], "rt", encoding="utf-8", newline="") as f:
            cross = csv.DictReader(f)
            fields_ok(cross.fieldnames, CROSS_FIELDS, str(paths["crosswalk"]))
            marker = object()
            id_batch = []

            def flush_ids():
                if id_batch:
                    try:
                        db.executemany("INSERT INTO ids VALUES (?)", id_batch)
                    except sqlite3.IntegrityError as exc:
                        raise ValueError("duplicate canonical_flight_id among mapped records") from exc
                    id_batch.clear()

            for n, (source_row, row) in enumerate(itertools.zip_longest(source_reader, cross, fillvalue=marker), 1):
                if source_row is marker or row is marker:
                    raise ValueError(f"source/crosswalk row-count mismatch at ordinal {n}")
                if len(source_row) != len(SOURCE_FIELDS):
                    raise ValueError(f"source record {n} has the wrong field count")
                if row.get("cleaned_input_row_ordinal") != str(n):
                    raise ValueError(f"crosswalk ordinal gap, duplicate, or reorder at record {n}")
                try:
                    first = int(row["cleaned_duplicate_first_ordinal"])
                    occurrences = int(row["cleaned_duplicate_group_occurrences"])
                    candidates = int(row["raw_candidate_count"])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid crosswalk numeric value at ordinal {n}") from exc
                fingerprint = (row.get("cleaned_row_sha256") or "").strip()
                if not HEX.fullmatch(fingerprint):
                    raise ValueError(f"invalid cleaned-row fingerprint at ordinal {n}")
                group_hash = ordinal_to_hash.get(n)
                if group_hash:
                    group_ordinals = duplicate[group_hash]
                    if fingerprint != group_hash or first != group_ordinals[0] or occurrences != len(group_ordinals):
                        raise ValueError(f"duplicate provenance/crosswalk mismatch at ordinal {n}")
                    current = tuple(source_row)
                    if group_hash in first_rows and first_rows[group_hash] != current:
                        raise ValueError(f"duplicate source records differ at ordinal {n}")
                    first_rows.setdefault(group_hash, current)
                    duplicate_seen[group_hash] += 1
                elif first != n or occurrences != 1 or fingerprint in duplicate:
                    raise ValueError(f"nonduplicate ordinal {n} has duplicate metadata")

                status = row.get("match_status") or ""
                flight_id = (row.get("canonical_flight_id") or "").strip()
                raw_id = (row.get("reconciliation_raw_row_id") or "").strip()
                if status not in STATUSES:
                    raise ValueError(f"unknown match_status {status!r} at ordinal {n}")
                if status == STATUSES[0]:
                    if candidates != 1 or not FLIGHT_ID.fullmatch(flight_id):
                        raise ValueError(f"unique row has invalid candidate count or ID at ordinal {n}")
                    if row.get("identity_version") != "bts-flight-v1" or not raw_id:
                        raise ValueError(f"unique row has inconsistent identity provenance at ordinal {n}")
                    if db.execute(
                        "SELECT 1 FROM canonical_ids WHERE flight_id = ?", (flight_id,)
                    ).fetchone() is None:
                        raise ValueError(f"mapped flight ID is absent from canonical table at ordinal {n}")
                    id_batch.append((flight_id,))
                    mapped += 1
                    if len(id_batch) >= 25_000:
                        flush_ids()
                elif status == STATUSES[1]:
                    if candidates < 2 or flight_id or fingerprint not in ambiguous or n not in ambiguous[fingerprint]:
                        raise ValueError(f"ambiguous row inconsistent or assigned an ID at ordinal {n}")
                    ambiguous_seen.add(n)
                elif status == STATUSES[3]:
                    if candidates != 0 or flight_id or fingerprint not in unmatched or n not in unmatched[fingerprint]:
                        raise ValueError(f"unmatched row inconsistent or assigned an ID at ordinal {n}")
                    unmatched_seen.add(n)
                elif candidates != 1 or flight_id or raw_id != quarantined:
                    raise ValueError(f"incomplete identity inconsistent with quarantine at ordinal {n}")
                counts[status] += 1
                writer.writerow({"cleaned_input_row_ordinal": n, "canonical_flight_id": flight_id, "match_status": status})
                total = n

        flush_ids()
        text_out.flush()
        text_out.close()
        raw_out.close()
        source_text.close()
        if total != expected_rows:
            raise ValueError(f"crosswalk has {total} rows; expected {expected_rows}")
        actual_counts = {s: counts[s] for s in STATUSES}
        expected_counts = {s: int(expected_status_counts.get(s, 0)) for s in STATUSES}
        if actual_counts != expected_counts or sum(expected_counts.values()) != expected_rows:
            raise ValueError(f"status count mismatch: expected {expected_counts}, found {actual_counts}")
        if any(duplicate_seen[h] != len(v) for h, v in duplicate.items()):
            raise ValueError("crosswalk does not cover every duplicate source ordinal")
        if ambiguous_seen != {n for values in ambiguous.values() for n in values}:
            raise ValueError("ambiguous exception ordinals differ from crosswalk")
        if unmatched_seen != {n for values in unmatched.values() for n in values}:
            raise ValueError("unmatched exception ordinals differ from crosswalk")
        if mapped != expected_counts[STATUSES[0]]:
            raise ValueError("mapped-ID count differs from unique complete match count")
        db.commit()
        if db.execute("SELECT COUNT(*) FROM ids").fetchone()[0] != mapped:
            raise ValueError("mapped canonical IDs are not unique")
        db.close()
        db = None
        (stage / "mapped_ids.sqlite").unlink(missing_ok=True)
        uncompressed_hash = input_digest.hexdigest()
        if expected_hashes and uncompressed_hash != expected_hashes.get("cleaned_input_uncompressed"):
            raise ValueError("cleaned input decompressed SHA-256 mismatch")

        output_check = verify_output(output_path, expected_rows, expected_counts)
        output_info = {
            "file": output_path.name, "rows": output_check["rows"],
            "columns": list(OUTPUT_FIELDS), "compressed_bytes": output_path.stat().st_size,
            "sha256": file_sha(output_path),
        }
        manifest = {
            "schema_version": "cleaned-row-to-canonical-flight-id-v1",
            "identity_version": "bts-flight-v1",
            "cleaned_input": {
                **inputs["cleaned_input"], "decompressed_sha256": uncompressed_hash,
                "decompressed_bytes": hashed.bytes_read, "data_rows": total,
                "ordinal_convention": "1-based data-record ordinal, excluding header",
            },
            "inputs": {k: v for k, v in inputs.items() if k != "cleaned_input"},
            "duplicate_provenance": {
                "groups": len(duplicate), "original_duplicate_rows": sum(map(len, duplicate.values())),
                "rows_beyond_first": sum(len(v) - 1 for v in duplicate.values()),
                "ambiguous_groups": len(ambiguous), "ambiguous_original_rows": len(ambiguous_seen),
                "unmatched_groups": len(unmatched), "unmatched_original_rows": len(unmatched_seen),
                "incomplete_identity_rows": 1,
            },
            "status_counts": actual_counts, "mapped_rows": mapped, "unresolved_rows": total - mapped,
            "canonical_table_rows_checked": canonical_id_count,
            "output": output_info,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_sha = file_sha(manifest_path)
        checksums_path.write_text(
            f"{output_info['sha256']}  {output_path.name}\n{manifest_sha}  {manifest_path.name}\n",
            encoding="utf-8",
        )
        for line in checksums_path.read_text(encoding="utf-8").splitlines():
            checksum, filename = line.split("  ", 1)
            if file_sha(stage / filename) != checksum:
                raise ValueError(f"checksum manifest verification failed for {filename}")
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("mapping manifest did not round-trip")
        stage.rename(output_dir)
        return {"output_dir": str(output_dir), "output": output_info,
                "manifest": str(output_dir / manifest_path.name),
                "checksums_manifest": str(output_dir / checksums_path.name),
                "status_counts": actual_counts}
    except Exception:
        if db is not None:
            db.close()
        for handle in (source_text, text_out, raw_out):
            try:
                handle.close()
            except Exception:
                pass
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleaned-input", type=Path, required=True)
    parser.add_argument("--identity-dir", type=Path, default=ROOT / IDENTITY)
    parser.add_argument("--reconciliation-dir", type=Path, default=ROOT / RECON)
    parser.add_argument("--output-dir", type=Path, default=ROOT / OUTPUT)
    args = parser.parse_args()
    result = build_mapping(
        args.cleaned_input, args.identity_dir, args.reconciliation_dir, args.output_dir,
        expected_rows=EXPECTED_ROWS, expected_status_counts=EXPECTED_COUNTS,
        expected_hashes=PINNED_HASHES,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
