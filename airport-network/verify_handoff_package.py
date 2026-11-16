"""Verify the handoff directory manifest and optional ZIP/sidecar."""

from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "MANIFEST.sha256"
SIDECAR_SUFFIX = ".sha256"
IGNORED_DIRS = {"__pycache__", ".git"}
LINE_RE = re.compile(r"^([0-9a-fA-F]{64})  (.+)$")


def sha256_stream(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256_stream(stream)


def _load_manifest(root: Path) -> dict[str, str]:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"Missing {MANIFEST_NAME} in {root}")
    entries: dict[str, str] = {}
    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        match = LINE_RE.fullmatch(line)
        if not match:
            raise ValueError(f"Malformed manifest line {line_number}")
        digest, name = match.groups()
        rel = PurePosixPath(name)
        if rel.is_absolute() or (rel.parts and rel.parts[0].endswith(":")) or ".." in rel.parts or "\\" in name or ":" in name or name in {"", MANIFEST_NAME}:
            raise ValueError(f"Unsafe or invalid manifest path on line {line_number}: {name!r}")
        if name in entries:
            raise ValueError(f"Duplicate manifest path: {name}")
        entries[name] = digest.lower()
    if not entries:
        raise ValueError("Manifest is empty")
    return entries


def _directory_files(root: Path) -> set[str]:
    result = set()
    for path in root.rglob("*"):
        if not path.is_file() or any(part in IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root).as_posix()
        if rel != MANIFEST_NAME:
            result.add(rel)
    return result


def verify_directory(root: Path) -> int:
    root = root.resolve()
    entries = _load_manifest(root)
    actual = _directory_files(root)
    missing = sorted(set(entries) - actual)
    extra = sorted(actual - set(entries))
    if missing or extra:
        raise ValueError(f"Directory file-set mismatch: missing={missing[:5]}, extra={extra[:5]}")
    for name, expected in entries.items():
        actual_hash = sha256_file(root / PurePosixPath(name))
        if actual_hash != expected:
            raise ValueError(f"SHA-256 mismatch for {name}: expected {expected}, found {actual_hash}")
    return len(entries)


def verify_archive(root: Path, archive: Path) -> tuple[int, str]:
    root = root.resolve()
    archive = archive.resolve()
    entries = _load_manifest(root)
    sidecar = Path(str(archive) + SIDECAR_SUFFIX)
    if not sidecar.is_file():
        raise ValueError(f"Missing archive sidecar: {sidecar}")
    sidecar_lines = sidecar.read_text(encoding="ascii").split()
    if len(sidecar_lines) < 2 or sidecar_lines[1] != archive.name:
        raise ValueError("Archive sidecar must contain the archive SHA-256 and matching filename")
    archive_hash = sha256_file(archive)
    if archive_hash.lower() != sidecar_lines[0].lower():
        raise ValueError(f"Archive SHA-256 mismatch: sidecar {sidecar_lines[0]}, found {archive_hash}")
    with zipfile.ZipFile(archive) as zipped:
        bad_member = zipped.testzip()
        if bad_member:
            raise ValueError(f"ZIP integrity failure at {bad_member}")
        names = zipped.namelist()
        if len(names) != len(set(names)):
            raise ValueError("ZIP contains duplicate member names")
        expected_names = set(entries) | {MANIFEST_NAME}
        if set(names) != expected_names:
            raise ValueError("ZIP file set differs from the package manifest")
        archived_manifest = zipped.read(MANIFEST_NAME)
        if archived_manifest != (root / MANIFEST_NAME).read_bytes():
            raise ValueError("ZIP manifest differs from the extracted package manifest")
        for name, expected in entries.items():
            actual = hashlib.sha256(zipped.read(name)).hexdigest()
            if actual != expected:
                raise ValueError(f"ZIP member SHA-256 mismatch for {name}")
    return len(entries), archive_hash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Extracted handoff package directory")
    parser.add_argument("--archive", type=Path, help="Optional ZIP to verify against its adjacent .sha256 sidecar")
    args = parser.parse_args()
    count = verify_directory(args.root)
    if args.archive:
        archive_count, archive_hash = verify_archive(args.root, args.archive)
        if archive_count != count:
            raise ValueError("Directory and archive manifest counts differ")
        print(f"PASS: {count} manifest entries; ZIP and sidecar verified; ZIP SHA-256 {archive_hash}")
    else:
        print(f"PASS: {count} manifest entries verified in {args.root}")


if __name__ == "__main__":
    main()
