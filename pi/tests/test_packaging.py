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
BUILD_DEB = PI_DIR / "build_deb.sh"
MAKE_SUMS = PI_DIR.parent / "tools" / "release" / "make_sums.sh"
VERSION = re.search(
    r'^__version__ = "([^"]+)"$',
    (PI_DIR / "wmr_bridge" / "__init__.py").read_text(encoding="utf-8"),
    re.MULTILINE,
).group(1)
ROOT_VERSION = (PI_DIR.parent / "VERSION").read_text(encoding="utf-8").strip()


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


class PackagingTests(unittest.TestCase):
    def test_package_version_matches_root_version(self) -> None:
        self.assertEqual(VERSION, ROOT_VERSION)

    def test_systemd_unit_and_env_example_support_wmr_bridge_args(self) -> None:
        unit_text = SERVICE_FILE.read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=-/etc/wmr-bridge/wmr-bridge.env", unit_text)
        self.assertIn("$WMR_BRIDGE_ARGS", unit_text)
        self.assertIn("--advertise-mdns", unit_text)
        self.assertIn("User=wmr", unit_text)
        self.assertIn("SupplementaryGroups=input bluetooth", unit_text)
        # The service user cannot clear a boot-time rfkill soft block itself.
        self.assertIn("ExecStartPre=-+", unit_text)
        self.assertIn("rfkill unblock bluetooth", unit_text)
        # The unit stages a 5 GHz-preferred profile before the bridge starts.
        self.assertIn(
            "ExecStartPre=-+/usr/bin/python3 -m wmr_bridge --prefer-5ghz",
            unit_text,
        )
        # rfkill uses /dev/rfkill; AF_NETLINK supports iw cfg80211 queries and
        # route/interface checks.
        self.assertIn(
            "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK\n",
            unit_text,
        )

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

            installed_wrapper = destdir / "usr" / "bin" / "wmr-bridge"
            self.assertTrue(installed_wrapper.is_file())
            self.assertTrue(os.access(installed_wrapper, os.X_OK))
            run = subprocess.run(
                [str(installed_wrapper), "--version"],
                env={
                    **os.environ,
                    "WMR_BRIDGE_PREFIX": str(destdir / "opt" / "wmr-bridge"),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertIn(VERSION, run.stdout)

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
            self.assertEqual(mode, 0o640)

    def test_installer_preserves_existing_token_and_env_and_invokes_system_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            destdir = tmp_path / "rootfs"
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            log_file = tmp_path / "calls.log"

            _write_stub(bin_dir, "id", "exit 1")
            _write_stub(bin_dir, "getent", "exit 0")
            _write_stub(bin_dir, "useradd", f'echo "useradd $*" >> "{log_file}"')
            _write_stub(bin_dir, "usermod", f'echo "usermod $*" >> "{log_file}"')
            _write_stub(bin_dir, "chown", f'echo "chown $*" >> "{log_file}"')
            _write_stub(bin_dir, "udevadm", f'echo "udevadm $*" >> "{log_file}"')
            _write_stub(bin_dir, "systemctl", f'echo "systemctl $*" >> "{log_file}"')
            _write_stub(bin_dir, "nmcli", "exit 0")
            _write_stub(bin_dir, "python3", f'echo "python3 $*" >> "{log_file}"')

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
                "SUDO_USER": "pi",
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
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o640)
            self.assertEqual(custom_env.read_text(encoding="utf-8"), "WMR_BRIDGE_ARGS=--hand right\n")

            calls = log_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("useradd --system --create-home --groups input,bluetooth wmr", calls)
            self.assertIn("usermod -aG wmr pi", calls)
            self.assertIn(f"chown wmr:wmr {token_file}", calls)
            self.assertIn("udevadm control --reload-rules", calls)
            self.assertIn("udevadm trigger", calls)
            self.assertIn("python3 -m wmr_bridge --prefer-5ghz --no-reconnect", calls)
            self.assertIn("systemctl daemon-reload", calls)
            self.assertIn("systemctl enable bluetooth.service", calls)
            self.assertIn("systemctl start bluetooth.service", calls)
            self.assertIn("systemctl enable wmr-bridge.service", calls)
            self.assertIn("systemctl restart wmr-bridge.service", calls)


    def test_installer_without_bluetooth_group_still_creates_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            destdir = tmp_path / "rootfs"
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            log_file = tmp_path / "calls.log"

            _write_stub(bin_dir, "id", "exit 1")
            _write_stub(bin_dir, "getent", "exit 1")
            _write_stub(bin_dir, "useradd", f'echo "useradd $*" >> "{log_file}"')
            _write_stub(bin_dir, "chown", "exit 0")
            _write_stub(bin_dir, "udevadm", "exit 0")
            _write_stub(bin_dir, "systemctl", "exit 0")

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
            calls = log_file.read_text(encoding="utf-8")
            self.assertIn("useradd --system --create-home --groups input wmr", calls)
            self.assertNotIn("bluetooth", calls)

    def test_installer_installs_verified_deb_when_piped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            release = tmp_path / "release"
            build = subprocess.run(
                [str(BUILD_DEB), "--out", str(release)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, msg=build.stderr)

            # A published release carries the three factory images, the three
            # application images, the .deb, install.sh and one SHA256SUMS list.
            for variant in ("", "-left", "-right"):
                (release / f"wmr-bridge{variant}-factory.bin").write_bytes(b"firmware\n")
                (release / f"wmr-bridge{variant}-app.bin").write_bytes(b"app\n")
            shutil.copy(INSTALL_SH, release / "install.sh")
            sums = subprocess.run(
                ["/bin/sh", str(MAKE_SUMS), str(release)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(sums.returncode, 0, msg=sums.stderr)

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            calls = tmp_path / "calls.log"
            # apt-get runs as root but reads the local .deb through the _apt
            # sandbox user, so record the modes it would see.
            _write_stub(
                bin_dir,
                "apt-get",
                f'echo "apt-get $*" >> "{calls}"\n'
                f'stat -c "deb-mode %a" "$3" >> "{calls}"\n'
                f'stat -c "tmp-mode %a" "$(dirname "$3")" >> "{calls}"',
            )
            _write_stub(bin_dir, "systemctl", f'echo "systemctl $*" >> "{calls}"')

            env = {
                **os.environ,
                "WMR_BRIDGE_VERSION": VERSION,
                "WMR_BRIDGE_RELEASE_BASE_URL": release.as_uri(),
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
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
            self.assertIn(f"installing wmr-bridge_{VERSION}_all.deb", proc.stdout)

            calls_text = calls.read_text(encoding="utf-8")
            install_lines = [line for line in calls_text.splitlines() if line.startswith("apt-get install -y ")]
            self.assertEqual(len(install_lines), 1)
            self.assertTrue(install_lines[0].endswith(f"wmr-bridge_{VERSION}_all.deb"))
            self.assertIn("systemctl enable wmr-bridge.service", calls_text)
            self.assertIn("systemctl restart wmr-bridge.service", calls_text)
            self.assertIn("deb-mode 644", calls_text)
            self.assertIn("tmp-mode 755", calls_text)

            corrupt = tmp_path / "corrupt"
            shutil.copytree(release, corrupt)
            (corrupt / f"wmr-bridge_{VERSION}_all.deb").write_bytes(b"not a deb")
            calls.write_text("", encoding="utf-8")
            env["WMR_BRIDGE_RELEASE_BASE_URL"] = corrupt.as_uri()
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
            self.assertEqual(calls.read_text(encoding="utf-8"), "")

    def test_installer_requires_apt_for_piped_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            release = tmp_path / "release"
            release.mkdir()
            # The release carries no tarball, so piped mode must fail with a
            # usable pointer instead of fetching a missing asset.
            (release / "SHA256SUMS").write_text("", encoding="utf-8")
            shutil.copy(INSTALL_SH, release / "install.sh")

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            for name in ("curl", "wget", "mktemp", "rm", "sha256sum", "awk", "sed", "grep", "head"):
                found = shutil.which(name)
                if found is not None:
                    (bin_dir / name).symlink_to(found)

            env = {
                **os.environ,
                "WMR_BRIDGE_VERSION": VERSION,
                "WMR_BRIDGE_RELEASE_BASE_URL": release.as_uri(),
                "PATH": str(bin_dir),
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
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("needs apt-get", proc.stderr)
            self.assertIn("pi/build_package.sh", proc.stderr)


    def test_build_package_ships_root_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pkg"
            proc = subprocess.run(
                [str(BUILD_PACKAGE), "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            archive = out / f"wmr-bridge-{VERSION}.tar.gz"
            listing = subprocess.run(
                ["tar", "-tzf", str(archive)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(listing.returncode, 0, msg=listing.stderr)
            self.assertIn(f"wmr-bridge-{VERSION}/VERSION", listing.stdout.splitlines())

    def test_build_scripts_reject_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = subprocess.run(
                [str(BUILD_DEB), "--version", "9.9.9", "--out", str(tmp_path / "deb")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("version mismatch", proc.stderr)

            tree = tmp_path / "tree"
            shutil.copytree(
                PI_DIR,
                tree / "pi",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            (tree / "VERSION").write_text("9.9.9\n", encoding="utf-8")
            proc = subprocess.run(
                [str(tree / "pi" / "build_package.sh"), "--out", str(tmp_path / "pkg")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("version mismatch", proc.stderr)


if __name__ == "__main__":
    unittest.main()
