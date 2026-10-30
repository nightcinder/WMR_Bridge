"""Pre-flight checks for the Pi package and the no-hardware daemon dry run."""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

PI_DIR = Path(__file__).resolve().parents[1]
BUILD = PI_DIR / "build_package.sh"
BUILD_DEB = PI_DIR / "build_deb.sh"
VERIFY = PI_DIR / "tools" / "verify_package.sh"
DRY_RUN = PI_DIR / "tools" / "dry_run.sh"


class PreflightTests(unittest.TestCase):
    def test_package_has_installer_and_replay_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            proc = subprocess.run(
                [str(BUILD), "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            archive = out / "wmr-bridge-0.2.1.tar.gz"
            self.assertTrue(archive.is_file(), msg=proc.stdout)
            self.assertTrue((out / "wmr-bridge-0.2.1.tar.gz.sha256").is_file())

            with tarfile.open(archive) as tar:
                names = set(tar.getnames())
            prefix = "wmr-bridge-0.2.1/"
            for member in (
                "pi/install.sh",
                "pi/wmr_bridge/__main__.py",
                "pi/tools/wifi_probe.py",
                "pi/systemd/wmr-bridge.service",
                "pi/udev/99-wmr-bridge.rules",
                "docs/PI_BRIDGE.md",
                "samples/controller_frames.txt",
            ):
                self.assertIn(prefix + member, names)
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))

            check = subprocess.run(
                [str(VERIFY), str(archive)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check.returncode, 0, msg=check.stderr)
            self.assertIn("OK: wmr-bridge-0.2.1.tar.gz", check.stdout)

    def test_dry_run_serves_replay_from_the_installed_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            build = subprocess.run(
                [str(BUILD), "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, msg=build.stderr)
            proc = subprocess.run(
                [str(DRY_RUN), str(out / "wmr-bridge-0.2.1.tar.gz")],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS install:", proc.stdout)
        self.assertIn("PASS replay:", proc.stdout)
        self.assertIn("PASS dry run:", proc.stdout)


    def test_dry_run_installs_and_replays_the_deb(self) -> None:
        if subprocess.run(
            ["sh", "-c", "command -v dpkg-deb >/dev/null 2>&1"],
            check=False,
        ).returncode != 0:
            self.skipTest("dpkg-deb is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            build = subprocess.run(
                [str(BUILD_DEB), "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, msg=build.stderr)
            deb = out / "wmr-bridge_0.2.1_all.deb"
            self.assertTrue(deb.is_file(), msg=build.stdout)
            proc = subprocess.run(
                [str(DRY_RUN), str(deb)],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS deb:", proc.stdout)
        self.assertIn("PASS install:", proc.stdout)
        self.assertIn("PASS reinstall:", proc.stdout)
        self.assertIn("PASS replay:", proc.stdout)
        self.assertIn("PASS remove:", proc.stdout)
        self.assertIn("PASS dry run:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
