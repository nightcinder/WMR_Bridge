#!/usr/bin/env python3
"""Tests for tools/release/make_sums.sh.

Checks that the release step covers every attached asset, keeps the digest
file out of itself, and produces a list `sha256sum -c` accepts. No network,
no release and no firmware build.

Run: python3 tests/test_release_sums.py
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "tools" / "release" / "make_sums.sh"
SIGN = HERE.parent / "tools" / "release" / "sign_sums.sh"

ASSETS = (
    "wmr-bridge-factory.bin",
    "wmr-bridge-left-factory.bin",
    "wmr-bridge-right-factory.bin",
    "wmr-bridge-app.bin",
    "wmr-bridge-left-app.bin",
    "wmr-bridge-right-app.bin",
    "wmr-bridge_0.2.2_all.deb",
    "install.sh",
)


def run(script, *args, cwd=None, env=None):
    return subprocess.run(
        ["/bin/sh", str(script), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def have_minisign():
    return shutil.which("minisign") is not None


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
        (self.dist / "install.sh").unlink()
        self.assertEqual(run(SCRIPT, str(self.dist)).returncode, 0)

        lines = (self.dist / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        names = [line.split("  ", 1)[1] for line in lines]
        self.assertIn("notes.txt", names)
        self.assertNotIn("install.sh", names)

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


@unittest.skipUnless(have_minisign(), "minisign is not installed")
class SignSumsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        for name in ASSETS:
            (self.dist / name).write_bytes(f"payload of {name}\n".encode())
        proc = run(SCRIPT, str(self.dist))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.pub, self.key = self.keypair("release")

    def keypair(self, name):
        directory = self.root / name
        directory.mkdir()
        pub = directory / "minisign.pub"
        key = directory / "minisign.key"
        proc = subprocess.run(
            ["minisign", "-G", "-W", "-f", "-p", str(pub), "-s", str(key)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        return pub, key

    def sign(self, key=None, pub=None):
        env = {
            **os.environ,
            "MINISIGN_SECRET_KEY": (key or self.key).read_text(encoding="utf-8"),
            "WMR_MINISIGN_PUBKEY": str(pub or self.pub),
        }
        return run(SIGN, str(self.dist), env=env)

    def test_signs_the_digest_list_and_verifies_with_the_pinned_key(self):
        proc = self.sign()
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("SHA256SUMS.minisig: signed", proc.stdout)

        sig = self.dist / "SHA256SUMS.minisig"
        self.assertTrue(sig.is_file())
        verify = subprocess.run(
            [
                "minisign",
                "-V",
                "-q",
                "-m",
                str(self.dist / "SHA256SUMS"),
                "-x",
                str(sig),
                "-p",
                str(self.pub),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(verify.returncode, 0, msg=verify.stderr)

    def test_secret_key_is_required(self):
        env = {**os.environ, "WMR_MINISIGN_PUBKEY": str(self.pub)}
        env.pop("MINISIGN_SECRET_KEY", None)
        proc = run(SIGN, str(self.dist), env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("MINISIGN_SECRET_KEY is not set", proc.stderr)
        self.assertFalse((self.dist / "SHA256SUMS.minisig").exists())

    def test_a_signature_from_another_key_is_rejected(self):
        _other_pub, other_key = self.keypair("other")
        proc = self.sign(key=other_key, pub=self.pub)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not verify", proc.stderr)

    def test_missing_sums_is_an_error(self):
        empty = self.root / "empty"
        empty.mkdir()
        proc = run(SIGN, str(empty), env={
            **os.environ,
            "MINISIGN_SECRET_KEY": self.key.read_text(encoding="utf-8"),
            "WMR_MINISIGN_PUBKEY": str(self.pub),
        })
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing or empty", proc.stderr)

    def test_missing_directory_is_an_error(self):
        proc = run(SIGN, str(self.root / "absent"), env={
            **os.environ,
            "MINISIGN_SECRET_KEY": self.key.read_text(encoding="utf-8"),
            "WMR_MINISIGN_PUBKEY": str(self.pub),
        })
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a directory", proc.stderr)


if __name__ == "__main__":
    unittest.main()
