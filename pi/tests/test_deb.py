"""Tests for pi/build_deb.sh and the Debian maintainer scripts."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

PI_DIR = Path(__file__).resolve().parents[1]
BUILD = PI_DIR / "build_deb.sh"
VERSION = re.search(
    r'^__version__ = "([^"]+)"$',
    (PI_DIR / "wmr_bridge" / "__init__.py").read_text(encoding="utf-8"),
    re.MULTILINE,
).group(1)


def _have_dpkg_deb() -> bool:
    return subprocess.run(
        ["sh", "-c", "command -v dpkg-deb >/dev/null 2>&1"],
        check=False,
    ).returncode == 0


@unittest.skipUnless(_have_dpkg_deb(), "dpkg-deb is not installed")
class DebPackagingTests(unittest.TestCase):
    def test_build_deb_has_architecture_all_and_conffiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            proc = subprocess.run(
                [str(BUILD), "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            deb = out / f"wmr-bridge_{VERSION}_all.deb"
            self.assertTrue(deb.is_file(), msg=proc.stdout)

            sums = out / f"wmr-bridge_{VERSION}_all.deb.sha256"
            self.assertTrue(sums.is_file(), msg=proc.stdout)
            check = subprocess.run(
                ["sha256sum", "-c", sums.name],
                cwd=out,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check.returncode, 0, msg=check.stderr)
            self.assertIn(f"{deb.name}: OK", check.stdout)

            info = subprocess.run(
                ["dpkg-deb", "-I", str(deb)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertIn(" Package: wmr-bridge", info)
            self.assertIn(f" Version: {VERSION}", info)
            self.assertIn(" Architecture: all", info)

            contents = subprocess.run(
                ["dpkg-deb", "-c", str(deb)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            for path in (
                "./opt/wmr-bridge/pi/wmr_bridge/__main__.py",
                "./opt/wmr-bridge/pi/tools/wifi_probe.py",
                "./opt/wmr-bridge/pi/networkmanager/99-wmr-bridge-powersave.conf",
                "./opt/wmr-bridge/pi/install.sh",
                "./opt/wmr-bridge/samples/controller_frames.txt",
                "./etc/systemd/system/wmr-bridge.service",
                "./etc/udev/rules.d/99-wmr-bridge.rules",
                "./etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf",
            ):
                self.assertIn(path, contents)

            control_dir = Path(tmp) / "control"
            subprocess.run(
                ["dpkg-deb", "-e", str(deb), str(control_dir)],
                check=True,
            )
            conffiles = (control_dir / "conffiles").read_text(encoding="utf-8").split()
            self.assertIn("/etc/wmr-bridge/wmr-bridge.env", conffiles)
            self.assertIn(
                "/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf",
                conffiles,
            )
            for script in ("postinst", "prerm", "postrm"):
                self.assertTrue(os.access(control_dir / script, os.X_OK), script)

    def test_shipped_installer_runs_from_the_installed_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            proc = subprocess.run(
                [str(BUILD), "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            installed = Path(tmp) / "installed"
            subprocess.run(
                ["dpkg-deb", "-x", str(out / f"wmr-bridge_{VERSION}_all.deb"), str(installed)],
                check=True,
            )

            staged = Path(tmp) / "stage"
            proc = subprocess.run(
                ["sh", str(installed / "opt" / "wmr-bridge" / "pi" / "install.sh")],
                env={
                    **os.environ,
                    "DESTDIR": str(staged),
                    "WMR_SKIP_PRIVILEGED": "1",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertTrue((staged / "opt/wmr-bridge/pi/install.sh").is_file())
            self.assertTrue(
                (staged / "etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf").is_file()
            )

    def test_postinst_names_missing_user_instead_of_failing_at_chown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            subprocess.run(
                [str(BUILD), "--out", str(out)],
                check=True,
                capture_output=True,
                text=True,
            )
            control = Path(tmp) / "control"
            subprocess.run(
                ["dpkg-deb", "-e", str(out / f"wmr-bridge_{VERSION}_all.deb"), str(control)],
                check=True,
            )

            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            for name in ("id", "useradd"):
                shim = fake_bin / name
                shim.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                shim.chmod(0o755)

            root = Path(tmp) / "rootfs"
            env = {
                **os.environ,
                "DESTDIR": str(root),
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            }
            env.pop("WMR_SKIP_PRIVILEGED", None)
            proc = subprocess.run(
                ["sh", str(control / "postinst"), "configure"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("wmr", proc.stderr)

    def test_postinst_creates_token_and_keeps_it_on_reinstall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            subprocess.run(
                [str(BUILD), "--out", str(out)],
                check=True,
                capture_output=True,
                text=True,
            )
            deb = out / f"wmr-bridge_{VERSION}_all.deb"
            root = Path(tmp) / "rootfs"
            control = Path(tmp) / "control"
            subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
            subprocess.run(["dpkg-deb", "-e", str(deb), str(control)], check=True)

            env = {
                **os.environ,
                "DESTDIR": str(root),
                "WMR_SKIP_PRIVILEGED": "1",
            }

            def maintainer(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["sh", str(control / script), *arguments],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            proc = maintainer("postinst", "configure")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            token = root / "etc" / "wmr-bridge" / "token"
            self.assertTrue(token.is_file())
            self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
            first = token.read_text(encoding="utf-8").strip()
            self.assertGreaterEqual(len(first), 24)
            self.assertTrue((root / "etc" / "wmr-bridge" / "wmr-bridge.env").is_file())

            proc = maintainer("prerm", "upgrade", VERSION)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            proc = maintainer("postinst", "configure", VERSION)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(token.read_text(encoding="utf-8").strip(), first)

            proc = maintainer("prerm", "remove")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            proc = maintainer("postrm", "remove")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertTrue(token.is_file(), "remove must keep the token")

            proc = maintainer("postrm", "purge")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertFalse(token.exists(), "purge must remove the token")


if __name__ == "__main__":
    unittest.main()
