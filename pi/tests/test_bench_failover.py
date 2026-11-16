"""Tests for pi/tools/bench_failover.sh: root gate, trap restore, T0/T1 log."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PI_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PI_DIR / "tools" / "bench_failover.sh"

# One stub for every `ip` call the script makes. FAIL_DOWN simulates an
# interface that cannot be taken down; NO_ADDR simulates DHCP that never
# returns an address.
IP_STUB = """#!/bin/sh
set -u
printf '%s\\n' "$*" >> "__LOG__"
case "$*" in
    "link set wlan0 down")
        [ -z "${FAIL_DOWN:-}" ] || exit 1
        ;;
    "-o -4 addr show dev wlan0")
        [ -n "${NO_ADDR:-}" ] ||
            printf '%s\\n' "2: wlan0    inet 192.168.1.50/24 brd 192.168.1.255 scope global wlan0"
        ;;
esac
exit 0
"""


class BenchFailoverTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.ip_log = self.tmp / "ip.log"
        self.ip_log.write_text("", encoding="utf-8")
        self.log = self.tmp / "failover.log"
        self.log.write_text("", encoding="utf-8")

        for name, body in (
            ("ip", IP_STUB.replace("__LOG__", str(self.ip_log))),
            # Records the detach and never runs the child, so a test stays
            # synchronous even when it exercises --detach.
            ("nohup", '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(self.ip_log) + '"\n'),
        ):
            path = self.bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)

        # `id -u` decides the root gate; nothing else calls id.
        id_stub = self.bin_dir / "id"
        id_stub.write_text(
            '#!/bin/sh\nprintf \'%s\\n\' "${STUB_UID:-0}"\n', encoding="utf-8"
        )
        id_stub.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        # BASH_ENV would re-source a startup file in the script and could
        # replace the stub PATH, so keep it out of the child environment.
        base = {
            key: value
            for key, value in os.environ.items()
            if key not in ("BASH_ENV", "ENV")
        }
        base["PATH"] = f"{self.bin_dir}:{base.get('PATH', '')}"
        base.update(extra)
        return base

    def args(self, *extra: str) -> list[str]:
        return [str(SCRIPT), "--down-seconds", "0", "--log", str(self.log), *extra]

    def ip_calls(self) -> list[str]:
        return self.ip_log.read_text(encoding="utf-8").splitlines()

    def wait_for_ip_call(self, needle: str) -> list[str]:
        deadline = time.monotonic() + 5
        while not any(needle in call for call in self.ip_calls()):
            self.assertLess(time.monotonic(), deadline, f"no ip call matching {needle!r}")
            time.sleep(0.02)
        return self.ip_calls()

    def test_script_parses(self) -> None:
        proc = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_requires_root_before_detaching(self) -> None:
        proc = subprocess.run(
            self.args("--detach"),
            env=self.env(STUB_UID="1000"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 77, msg=proc.stderr)
        self.assertIn("must run as root", proc.stderr)
        # The root check precedes the detach: no nohup child, no ip change.
        self.assertEqual(self.ip_calls(), [])

    def test_detach_reexecutes_the_script_under_nohup(self) -> None:
        proc = subprocess.run(
            self.args("--detach"),
            env=self.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("detached pid", proc.stdout)
        calls = self.wait_for_ip_call("--detached")
        self.assertEqual(len(calls), 1, msg=calls)
        self.assertIn("--detached", calls[0])
        self.assertIn("--down-seconds 0", calls[0])

    def test_failure_takes_the_interface_back_up(self) -> None:
        proc = subprocess.run(
            self.args(),
            env=self.env(FAIL_DOWN="1"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("link set wlan0 up", self.ip_calls())
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("FAIL: ip link set wlan0 down", text)
        self.assertIn("restore: ip link set wlan0 up", text)

    def test_signal_takes_the_interface_back_up(self) -> None:
        proc = subprocess.Popen(
            self.args("--timeout", "15"),
            env=self.env(NO_ADDR="1"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.wait_for_ip_call("link set wlan0 up")
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 128 + signal.SIGTERM)
        self.assertIn("link set wlan0 up", self.ip_calls())
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("signal: TERM", text)
        self.assertIn("restore: ip link set wlan0 up", text)

    def test_records_t0_and_t1_ipv4_recovery(self) -> None:
        proc = subprocess.run(
            self.args(),
            env=self.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=self.log.read_text(encoding="utf-8"))
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("T0 wlan0 up issued", text)
        self.assertIn("T1 wlan0 IPv4 192.168.1.50/24 after ", text)
        self.assertIn("PASS: T1 - T0 = ", text)


if __name__ == "__main__":
    unittest.main()
