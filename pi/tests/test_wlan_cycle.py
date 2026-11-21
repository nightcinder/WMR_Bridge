"""Tests for pi/tools/wlan_cycle.sh: cycle, trap restore, BT invariants."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PI_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PI_DIR / "tools" / "wlan_cycle.sh"

IP_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "__LOG__"
case "$*" in
    "link set wlan0 down") [ -z "${FAIL_DOWN:-}" ] || exit 1 ;;
esac
exit 0
"""

# First `list` call is the before sample, the second the after sample.
BT_STUB = """#!/bin/sh
state="__STATE__"
n=$(cat "$state" 2>/dev/null || echo 0)
case "$1" in
    list)
        n=$((n + 1))
        echo "$n" >"$state"
        if [ "$n" -eq 1 ]; then count="${BT_BEFORE_COUNT:-2}"; else count="${BT_AFTER_COUNT:-0}"; fi
        i=1
        while [ "$i" -le "$count" ]; do
            echo "Controller AA:BB:CC:DD:EE:0$i wmr"
            i=$((i + 1))
        done
        ;;
    show)
        if [ "$(cat "$state")" -eq 1 ]; then p="${BT_BEFORE_POWERED:-yes}"; else p="${BT_AFTER_POWERED:-yes}"; fi
        echo "Controller $2"
        printf '\\tPowered: %s\\n' "$p"
        ;;
esac
exit 0
"""

SYSTEMCTL_STUB = """#!/bin/sh
case "$*" in
    "show -p ActiveState --value wmr-bridge-bt-recover.service")
        printf '%s\\n' "${RECOVERY_STATE:-inactive}"
        ;;
esac
exit 0
"""


class WlanCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.ip_log = self.tmp / "ip.log"
        self.ip_log.write_text("", encoding="utf-8")
        self.bt_state = self.tmp / "bt.state"
        self.bt_state.write_text("0", encoding="utf-8")
        stubs = {
            "ip": IP_STUB.replace("__LOG__", str(self.ip_log)),
            "bluetoothctl": BT_STUB.replace("__STATE__", str(self.bt_state)),
            "systemctl": SYSTEMCTL_STUB,
            "id": '#!/bin/sh\nprintf \'%s\\n\' "${STUB_UID:-0}"\n',
        }
        for name, body in stubs.items():
            path = self.bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        base = {
            key: value
            for key, value in os.environ.items()
            if key not in ("BASH_ENV", "ENV")
        }
        base["PATH"] = f"{self.bin_dir}:{base.get('PATH', '')}"
        base.update(extra)
        return base

    def args(self, seconds: str) -> list[str]:
        return [str(SCRIPT), "--interface", "wlan0", seconds]

    def ip_calls(self) -> list[str]:
        return self.ip_log.read_text(encoding="utf-8").splitlines()

    def test_script_parses(self) -> None:
        proc = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_normal_cycle_restores_and_records_before_after(self) -> None:
        proc = subprocess.run(
            self.args("0"), env=self.env(), capture_output=True, text=True, check=False
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(
            self.ip_calls(), ["link set wlan0 down", "link set wlan0 up"]
        )
        self.assertIn("bt-before: controllers=2 powered=yes", proc.stdout)
        self.assertIn("bt-after: controllers=0 powered=no", proc.stdout)

    def test_records_a_power_loss_across_the_cycle(self) -> None:
        proc = subprocess.run(
            self.args("0"),
            env=self.env(
                BT_BEFORE_COUNT="1",
                BT_BEFORE_POWERED="yes",
                BT_AFTER_COUNT="1",
                BT_AFTER_POWERED="no",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("bt-before: controllers=1 powered=yes", proc.stdout)
        self.assertIn("bt-after: controllers=1 powered=no", proc.stdout)

    def test_sigint_restores_the_interface(self) -> None:
        proc = subprocess.Popen(
            self.args("30"),
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while "link set wlan0 down" not in self.ip_calls():
                self.assertLess(time.monotonic(), deadline, "no down call")
                time.sleep(0.02)
            proc.send_signal(signal.SIGINT)
            _, err = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertEqual(proc.returncode, 130, msg=err)
        self.assertIn("link set wlan0 up", self.ip_calls())
        self.assertIn("signal: INT", err)

    def test_refuses_while_recovery_is_running(self) -> None:
        # The oneshot is `activating` while its ladder runs; `is-active`
        # would report inactive for that whole window.
        for state in ("activating", "active", "reloading", "refreshing"):
            with self.subTest(state=state):
                proc = subprocess.run(
                    self.args("0"),
                    env=self.env(RECOVERY_STATE=state),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 75, msg=proc.stderr)
                self.assertIn(
                    f"wmr-bridge-bt-recover.service is {state}", proc.stderr
                )
                self.assertEqual(self.ip_calls(), [])


if __name__ == "__main__":
    unittest.main()
