#!/usr/bin/env python3
"""Tests for tools/release/make_sums.sh.

Checks that the release step covers every attached asset, keeps the digest
file out of itself, and produces a list `sha256sum -c` accepts. No network,
no release and no firmware build.

Run: python3 tests/test_release_sums.py
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "tools" / "release" / "make_sums.sh"

ASSETS = (
    "wmr-bridge-0.2.2.tar.gz",
    "wmr-bridge-0.2.2.tar.gz.sha256",
    "wmr-bridge_0.2.2_all.deb",
    "wmr-bridge_0.2.2_all.deb.sha256",
    "install.sh",
    "wmr-bridge-factory.bin",
    "wmr-bridge-left-factory.bin",
    "manifest.json",
)


def run(script, *args, cwd=None):
    return subprocess.run(
        ["/bin/sh", str(script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


class ReleaseSumsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dist = Path(self.tmp.name) / "dist"
        self.dist.mkdir()
        for name in ASSETS:
            (self.dist / name).write_bytes(f"payload of {name}\n".encode())

    def test_sums_cover_every_asset_except_itself(self):
        proc = run(SCRIPT, str(self.dist))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

        sums = self.dist / "SHA256SUMS"
        lines = sums.read_text(encoding="ascii").splitlines()
        names = [line.split("  ", 1)[1] for line in lines]
        self.assertEqual(names, sorted(ASSETS))
        self.assertNotIn("SHA256SUMS", names)
        self.assertEqual(proc.stdout.strip(), f"SHA256SUMS: {len(ASSETS)} assets in {self.dist}")

        check = subprocess.run(
            ["sha256sum", "-c", "SHA256SUMS"],
            cwd=self.dist,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(check.returncode, 0, msg=check.stderr)

    def test_check_fails_when_an_asset_changes(self):
        self.assertEqual(run(SCRIPT, str(self.dist)).returncode, 0)
        (self.dist / "install.sh").write_bytes(b"tampered\n")
        check = subprocess.run(
            ["sha256sum", "-c", "SHA256SUMS"],
            cwd=self.dist,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(check.returncode, 0)
        self.assertIn("install.sh: FAILED", check.stdout)

    def test_rerun_replaces_stale_entries(self):
        self.assertEqual(run(SCRIPT, str(self.dist)).returncode, 0)
        (self.dist / "notes.txt").write_bytes(b"added later\n")
        (self.dist / "manifest.json").unlink()
        self.assertEqual(run(SCRIPT, str(self.dist)).returncode, 0)

        lines = (self.dist / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        names = [line.split("  ", 1)[1] for line in lines]
        self.assertIn("notes.txt", names)
        self.assertNotIn("manifest.json", names)

    def test_empty_directory_is_an_error(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        proc = run(SCRIPT, str(empty))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no release assets", proc.stderr)

    def test_missing_directory_is_an_error(self):
        proc = run(SCRIPT, str(Path(self.tmp.name) / "absent"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a directory", proc.stderr)


if __name__ == "__main__":
    unittest.main()
