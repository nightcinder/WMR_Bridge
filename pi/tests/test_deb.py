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
ROOT_VERSION = (PI_DIR.parent / "VERSION").read_text(encoding="utf-8").strip()


def _have_dpkg_deb() -> bool:
    return subprocess.run(
        ["sh", "-c", "command -v dpkg-deb >/dev/null 2>&1"],
        check=False,
    ).returncode == 0


@unittest.skipUnless(_have_dpkg_deb(), "dpkg-deb is not installed")
class DebPackagingTests(unittest.TestCase):
    def test_package_version_matches_root_version(self) -> None:
        self.assertEqual(VERSION, ROOT_VERSION)

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
            self.assertIn(f" Version: {ROOT_VERSION}", info)
            self.assertIn(" Architecture: all", info)
            self.assertIn(
                " Depends: python3 (>= 3.10), python3-dbus-next, bluez, rfkill, avahi-utils, minisign",
                info,
            )
            self.assertIn(" Recommends: network-manager", info)

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
                "./opt/wmr-bridge/pi/minisign.pub",
                "./opt/wmr-bridge/samples/controller_frames.txt",
                "./usr/bin/wmr-bridge",
                "./etc/systemd/system/wmr-bridge.service",
                "./etc/systemd/system/wmr-bridge-upgrade.path",
                "./etc/systemd/system/wmr-bridge-upgrade.service",
                "./etc/systemd/system/wmr-bridge-bt-recover.path",
                "./etc/systemd/system/wmr-bridge-bt-recover.service",
                "./opt/wmr-bridge/pi/systemd/wmr-bridge-upgrade.path",
                "./opt/wmr-bridge/pi/systemd/wmr-bridge-upgrade.service",
                "./opt/wmr-bridge/pi/systemd/wmr-bridge-bt-recover.path",
                "./opt/wmr-bridge/pi/systemd/wmr-bridge-bt-recover.service",
                "./etc/udev/rules.d/99-wmr-bridge.rules",
                "./etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf",
            ):
                self.assertIn(path, contents)

            # The root oneshot runs this installed copy, so /opt must stay
            # root-owned and not writable by the wmr service user.
            for line in contents.splitlines():
                if line.rstrip().endswith("./opt/wmr-bridge/pi/systemd/wmr-bridge-upgrade.service"):
                    self.assertIn("root/root", line)
                    self.assertRegex(line, r"^-rw-r--r--")

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
            postinst = (control_dir / "postinst").read_text(encoding="utf-8")
            self.assertIn("input,bluetooth", postinst)
            self.assertIn("rfkill unblock bluetooth", postinst)
            self.assertIn("bluez_conf.py", postinst)
            self.assertIn("systemctl enable bluetooth.service", postinst)
            self.assertIn("systemctl restart bluetooth.service", postinst)
            self.assertIn("systemctl enable wmr-bridge-upgrade.path", postinst)
            self.assertIn("systemctl start wmr-bridge-upgrade.path", postinst)
            self.assertIn("systemctl enable wmr-bridge-bt-recover.path", postinst)
            self.assertIn("systemctl start wmr-bridge-bt-recover.path", postinst)
            self.assertIn("--prefer-5ghz --no-reconnect", postinst)
            self.assertNotIn(".pyc", contents)

    def test_installed_wrapper_prints_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            proc = subprocess.run(
                [str(BUILD), "--out", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            root = Path(tmp) / "root"
            subprocess.run(
                ["dpkg-deb", "-x", str(out / f"wmr-bridge_{VERSION}_all.deb"), str(root)],
                check=True,
            )
            # Every shipped module must match this tree exactly: a mixed
            # 0.3.13/0.3.14 tree produced a traceback whose line numbers did
            # not match the installed source.
            installed_modules = root / "opt" / "wmr-bridge" / "pi" / "wmr_bridge"
            for source in (PI_DIR / "wmr_bridge").glob("*.py"):
                self.assertEqual(
                    (installed_modules / source.name).read_bytes(),
                    source.read_bytes(),
                    source.name,
                )
            self.assertFalse(list(installed_modules.rglob("*.pyc")))

            wrapper = root / "usr" / "bin" / "wmr-bridge"
            self.assertTrue(os.access(wrapper, os.X_OK))
            run = subprocess.run(
                [str(wrapper), "--version"],
                env={**os.environ, "WMR_BRIDGE_PREFIX": str(root / "opt" / "wmr-bridge")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertIn(VERSION, run.stdout)

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
            self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o640)
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

    def test_postinst_adds_invoking_sudo_user_to_wmr_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            subprocess.run(
                [str(BUILD), "--out", str(out)],
                check=True,
                capture_output=True,
                text=True,
            )
            deb = out / f"wmr-bridge_{VERSION}_all.deb"
            control = Path(tmp) / "control"
            subprocess.run(["dpkg-deb", "-e", str(deb), str(control)], check=True)

            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            log_file = Path(tmp) / "calls.log"
            for name, body in (
                ("id", "exit 0"),
                ("usermod", f'echo "usermod $*" >> "{log_file}"'),
                ("chown", "exit 0"),
                ("udevadm", "exit 0"),
                ("rfkill", "exit 0"),
                ("systemctl", "exit 0"),
                ("nmcli", "exit 0"),
            ):
                shim = fake_bin / name
                shim.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
                shim.chmod(0o755)

            root = Path(tmp) / "rootfs"
            env = {
                **os.environ,
                "DESTDIR": str(root),
                "SUDO_USER": "pi",
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
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            calls = log_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("usermod -aG input,bluetooth wmr", calls)
            self.assertIn("usermod -aG wmr pi", calls)
            token = root / "etc" / "wmr-bridge" / "token"
            self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o640)

    def test_postinst_restarts_bluetooth_only_when_the_repair_policy_changes(self) -> None:
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

            main_conf = root / "etc" / "bluetooth" / "main.conf"
            main_conf.parent.mkdir(parents=True)
            main_conf.write_text(
                "[General]\n# JustWorksRepairing = never\n", encoding="utf-8"
            )

            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            calls = Path(tmp) / "calls.log"
            for name, body in (
                ("id", "exit 0"),
                ("useradd", f'echo "useradd $*" >> "{calls}"'),
                ("usermod", f'echo "usermod $*" >> "{calls}"'),
                ("chown", "exit 0"),
                ("udevadm", "exit 0"),
                ("rfkill", "exit 0"),
                ("nmcli", "exit 0"),
                ("systemctl", f'echo "systemctl $*" >> "{calls}"'),
            ):
                shim = fake_bin / name
                shim.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
                shim.chmod(0o755)

            systemd_run = Path(tmp) / "run-systemd"
            systemd_run.mkdir()
            env = {
                **os.environ,
                "DESTDIR": str(root),
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "WMR_SYSTEMD_RUN_DIR": str(systemd_run),
            }
            env.pop("WMR_SKIP_PRIVILEGED", None)

            def configure() -> None:
                proc = subprocess.run(
                    ["sh", str(control / "postinst"), "configure"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            configure()
            self.assertIn(
                "JustWorksRepairing = confirm",
                main_conf.read_text(encoding="utf-8"),
            )

            lines = calls.read_text(encoding="utf-8").splitlines()
            stop = lines.index("systemctl stop wmr-bridge.service")
            restart_bluetooth = lines.index("systemctl restart bluetooth.service")
            restart_bridge = lines.index("systemctl restart wmr-bridge.service")
            # A live GATT/advertising/scan client must not race bluetoothd.
            self.assertLess(stop, restart_bluetooth)
            self.assertLess(restart_bluetooth, restart_bridge)

            calls.write_text("", encoding="utf-8")
            configure()
            second = calls.read_text(encoding="utf-8").splitlines()
            # The policy is already set: an upgrade must leave the working
            # adapter (and its bonds) alone instead of restarting bluetoothd.
            self.assertNotIn("systemctl restart bluetooth.service", second)
            self.assertIn("systemctl restart wmr-bridge.service", second)


    def test_postinst_upgrade_skips_the_wifi_and_rfkill_reset(self) -> None:
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

            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            calls = Path(tmp) / "calls.log"
            for name, body in (
                ("id", "exit 0"),
                ("useradd", "exit 0"),
                ("usermod", "exit 0"),
                ("chown", "exit 0"),
                ("udevadm", "exit 0"),
                ("rfkill", f'echo "rfkill $*" >> "{calls}"'),
                ("nmcli", f'echo "nmcli $*" >> "{calls}"'),
                ("systemctl", f'echo "systemctl $*" >> "{calls}"'),
            ):
                shim = fake_bin / name
                shim.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
                shim.chmod(0o755)

            systemd_run = Path(tmp) / "run-systemd"
            systemd_run.mkdir()
            env = {
                **os.environ,
                "DESTDIR": str(root),
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "WMR_SYSTEMD_RUN_DIR": str(systemd_run),
            }
            env.pop("WMR_SKIP_PRIVILEGED", None)
            proc = subprocess.run(
                ["sh", str(control / "postinst"), "configure", "0.0.1"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            lines = calls.read_text(encoding="utf-8").splitlines()
            self.assertFalse([line for line in lines if line.startswith("nmcli ")])
            self.assertFalse([line for line in lines if line.startswith("rfkill ")])
            restarts = [line for line in lines if line == "systemctl restart wmr-bridge.service"]
            self.assertEqual(len(restarts), 1)


if __name__ == "__main__":
    unittest.main()
