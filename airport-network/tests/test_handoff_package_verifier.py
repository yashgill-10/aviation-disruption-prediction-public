"""Tests for handoff package manifest and archive verification."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))
import verify_handoff_package as verifier  # noqa: E402


class HandoffPackageVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=PACKAGE_ROOT)
        self.root = Path(self.temp.name) / "package"
        self.root.mkdir()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "report.md").write_text("ok\n", encoding="utf-8")
        (self.root / "source.py").write_text("print('ok')\n", encoding="utf-8")
        self._write_manifest()
        self.archive = Path(self.temp.name) / "package.zip"
        self._write_archive()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _files(self):
        return [self.root / "docs" / "report.md", self.root / "source.py"]

    def _write_manifest(self):
        lines = []
        for path in self._files():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.relative_to(self.root).as_posix()}")
        (self.root / verifier.MANIFEST_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_archive(self):
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for path in self._files() + [self.root / verifier.MANIFEST_NAME]:
                zipped.write(path, path.relative_to(self.root).as_posix())
        digest = verifier.sha256_file(self.archive)
        Path(str(self.archive) + ".sha256").write_text(f"{digest}  {self.archive.name}\n", encoding="ascii")

    def test_valid_directory_and_zip_sidecar(self):
        self.assertEqual(verifier.verify_directory(self.root), 2)
        count, _ = verifier.verify_archive(self.root, self.archive)
        self.assertEqual(count, 2)

    def test_detects_changed_file(self):
        (self.root / "source.py").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            verifier.verify_directory(self.root)

    def test_detects_unlisted_file(self):
        (self.root / "extra.txt").write_text("extra\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "file-set mismatch"):
            verifier.verify_directory(self.root)

    def test_detects_archive_sidecar_mismatch(self):
        Path(str(self.archive) + ".sha256").write_text("0" * 64 + f"  {self.archive.name}\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "Archive SHA-256 mismatch"):
            verifier.verify_archive(self.root, self.archive)

    def test_rejects_traversal_manifest_path(self):
        (self.root / verifier.MANIFEST_NAME).write_text("0" * 64 + "  ../outside.txt\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsafe or invalid manifest path"):
            verifier.verify_directory(self.root)

    def test_rejects_windows_drive_manifest_path(self):
        (self.root / verifier.MANIFEST_NAME).write_text("0" * 64 + "  C:/outside.txt\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsafe or invalid manifest path"):
            verifier.verify_directory(self.root)


if __name__ == "__main__":
    unittest.main()
