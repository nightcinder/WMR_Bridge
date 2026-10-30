"""Tests for pi/install.sh and systemd/udev packaging files."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

PI_DIR = Path(__file__).resolve().parents[1]
INSTALL_SH = PI_DIR / "install.sh"
SERVICE_FILE = PI_DIR / "systemd" / "wmr-bridge.service"
ENV_EXAMPLE = PI_DIR / "systemd" / "wmr-bridge.env.example"
UDEV_RULES = PI_DIR / "udev" / "99-wmr-bridge.rules"
POWERSAVE_CONF = PI_DIR / "networkmanager" / "99-wmr-bridge-powersave.conf"
BUILD_PACKAGE = PI_DIR / "build_package.sh"
VERSION = re.search(
    r'^__version__ = "([^"]+)"$',
    (PI_DIR / "wmr_bridge" / "__init__.py").read_text(encoding="utf-8"),
    re.MULTILINE,
).group(1)


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


class PackagingTests(unittest.TestCase):
    def test_systemd_unit_and_env_example_support_wmr_bridge_args(self) -> None:
        unit_text = SERVICE_FILE.read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=-/etc/wmr-bridge/wmr-bridge.env", unit_text)
        self.assertIn("$WMR_BRIDGE_ARGS", unit_text)
        self.assertIn("User=wmr", unit_text)
        self.assertIn("SupplementaryGroups=input", unit_text)

        env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("WMR_BRIDGE_ARGS", env_text)
        self.assertIn("--hand", env_text)

    def test_installer_stages_into_destdir_without_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destdir = Path(tmp) / "rootfs"
            env = {
                **os.environ,
                "DESTDIR": str(destdir),
                "WMR_SKIP_PRIVILEGED": "1",
            }
            proc = subprocess.run(
                ["/bin/sh", str(INSTALL_SH)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            opt_pi = destdir / "opt" / "wmr-bridge" / "pi"
            self.assertTrue((opt_pi / "wmr_bridge" / "__main__.py").is_file())
            self.assertTrue((opt_pi / "tools" / "wifi_probe.py").is_file())
            self.assertTrue((opt_pi / "install.sh").is_file())
            self.assertTrue((destdir / "opt" / "wmr-bridge" / "docs" / "PI_BRIDGE.md").is_file())

            installed_udev = destdir / "etc" / "udev" / "rules.d" / "99-wmr-bridge.rules"
            self.assertTrue(installed_udev.is_file())
            self.assertEqual(installed_udev.read_text(encoding="utf-8"), UDEV_RULES.read_text(encoding="utf-8"))

            installed_unit = destdir / "etc" / "systemd" / "system" / "wmr-bridge.service"
            self.assertTrue(installed_unit.is_file())
            self.assertEqual(installed_unit.read_text(encoding="utf-8"), SERVICE_FILE.read_text(encoding="utf-8"))

            installed_powersave = destdir / "etc" / "NetworkManager" / "conf.d" / "99-wmr-bridge-powersave.conf"
            self.assertTrue(installed_powersave.is_file())
            self.assertEqual(
                installed_powersave.read_text(encoding="utf-8"),
                POWERSAVE_CONF.read_text(encoding="utf-8"),
            )

            installed_env_example = destdir / "etc" / "wmr-bridge" / "wmr-bridge.env.example"
            installed_env = destdir / "etc" / "wmr-bridge" / "wmr-bridge.env"
            self.assertTrue(installed_env_example.is_file())
            self.assertTrue(installed_env.is_file())
            self.assertEqual(installed_env.read_text(encoding="utf-8"), ENV_EXAMPLE.read_text(encoding="utf-8"))

            token_file = destdir / "etc" / "wmr-bridge" / "token"
            self.assertTrue(token_file.is_file())
            token_value = token_file.read_text(encoding="utf-8").strip()
            self.assertGreaterEqual(len(token_value), 24)
            mode = stat.S_IMODE(token_file.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_installer_preserves_existing_token_and_env_and_invokes_system_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            destdir = tmp_path / "rootfs"
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            log_file = tmp_path / "calls.log"

            _write_stub(bin_dir, "id", "exit 1")
            _write_stub(bin_dir, "useradd", f'echo "useradd $*" >> "{log_file}"')
            _write_stub(bin_dir, "chown", f'echo "chown $*" >> "{log_file}"')
            _write_stub(bin_dir, "udevadm", f'echo "udevadm $*" >> "{log_file}"')
            _write_stub(bin_dir, "systemctl", f'echo "systemctl $*" >> "{log_file}"')

            etc_dir = destdir / "etc" / "wmr-bridge"
            etc_dir.mkdir(parents=True)
            token_file = etc_dir / "token"
            token_file.write_text("existing-secret-token\n", encoding="utf-8")
            token_file.chmod(0o644)

            custom_env = etc_dir / "wmr-bridge.env"
            custom_env.write_text("WMR_BRIDGE_ARGS=--hand right\n", encoding="utf-8")

            env = {
                **os.environ,
                "DESTDIR": str(destdir),
                "WMR_SKIP_PRIVILEGED": "0",
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            }
            proc = subprocess.run(
                ["/bin/sh", str(INSTALL_SH)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            self.assertEqual(token_file.read_text(encoding="utf-8"), "existing-secret-token\n")
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
            self.assertEqual(custom_env.read_text(encoding="utf-8"), "WMR_BRIDGE_ARGS=--hand right\n")

            calls = log_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("useradd --system --create-home --groups input wmr", calls)
            self.assertIn(f"chown wmr:wmr {token_file}", calls)
            self.assertIn("udevadm control --reload-rules", calls)
            self.assertIn("udevadm trigger", calls)
            self.assertIn("systemctl daemon-reload", calls)


    def test_installer_fetches_and_verifies_release_when_piped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            release = tmp_path / "release"
            build = subprocess.run(
                [str(BUILD_PACKAGE), "--out", str(release)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, msg=build.stderr)

            rootfs = tmp_path / "rootfs"
            env = {
                **os.environ,
                "WMR_BRIDGE_VERSION": VERSION,
                "WMR_BRIDGE_RELEASE_BASE_URL": release.as_uri(),
                "DESTDIR": str(rootfs),
                "WMR_SKIP_PRIVILEGED": "1",
            }
            proc = subprocess.run(
                ["/bin/sh", "-s"],
                input=INSTALL_SH.read_text(encoding="utf-8"),
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertIn(f"using release {VERSION}", proc.stdout)
            self.assertTrue(
                (rootfs / "opt" / "wmr-bridge" / "pi" / "wmr_bridge" / "__main__.py").is_file()
            )
            self.assertTrue((rootfs / "etc" / "wmr-bridge" / "token").is_file())

            corrupt = tmp_path / "corrupt"
            shutil.copytree(release, corrupt)
            (corrupt / f"wmr-bridge-{VERSION}.tar.gz").write_bytes(b"not a tarball")
            bad_rootfs = tmp_path / "bad-rootfs"
            env["WMR_BRIDGE_RELEASE_BASE_URL"] = corrupt.as_uri()
            env["DESTDIR"] = str(bad_rootfs)
            proc = subprocess.run(
                ["/bin/sh", "-s"],
                input=INSTALL_SH.read_text(encoding="utf-8"),
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("sha256 verification failed", proc.stderr)
            self.assertFalse(bad_rootfs.exists())


if __name__ == "__main__":
    unittest.main()
