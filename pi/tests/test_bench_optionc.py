"""Unit tests for the Pi-side Option C bench preflight and capture helper."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PI_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PI_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import bench_optionc  # noqa: E402
from wmr_bridge.ble_gatt import write_ble_status  # noqa: E402


class OptionCPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.boot_id_path = self.root / "boot_id"
        self.boot_id_path.write_text("boot-optionc-1\n", encoding="utf-8")
        self.dropin_dir = self.root / "wmr-bridge.service.d"
        self.env_file = self.root / "wmr-bridge.env"
        self.env_file.write_text('WMR_BRIDGE_ARGS=""\n', encoding="utf-8")

        write_ble_status(
            {
                "mode": "active",
                "load_cuts": True,
                "mtu": 185,
                "subscribed": [],
                "decimated": 0,
                "ble_dropped": 0,
            },
            self.state_dir,
            boot_id_path=self.boot_id_path,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _runner(
        self,
        *,
        bt_show: str = "Controller AA:BB:CC:DD:EE:FF\n\tPowered: yes\n\tActiveInstances: 0x01 (1)\n",
        devices: str = "",
        device_info: dict[str, str] | None = None,
        klog: str = "Nov 23 12:00:00 pi kernel: Bluetooth: Core ver 2.22\n",
        dropin_paths: str = "",
        exec_start: str = "/usr/bin/python3 -m wmr_bridge --bind 0.0.0.0",
    ):
        info_map = device_info or {}

        def run(cmd):
            args = list(cmd)
            if args[:2] == ["bluetoothctl", "show"]:
                return bench_optionc.CommandResult(0, bt_show)
            if args[:2] == ["bluetoothctl", "devices"]:
                return bench_optionc.CommandResult(0, devices)
            if args[:2] == ["bluetoothctl", "info"] and len(args) >= 3:
                return bench_optionc.CommandResult(0, info_map.get(args[2], ""))
            if args[:2] == ["journalctl", "-k"]:
                return bench_optionc.CommandResult(0, klog)
            if args[:3] == ["systemctl", "show", "-p"]:
                prop = args[3]
                if prop == "DropInPaths":
                    return bench_optionc.CommandResult(0, dropin_paths)
                if prop == "ExecStart":
                    return bench_optionc.CommandResult(0, exec_start)
            return bench_optionc.CommandResult(1, "")

        return run

    def test_preflight_passes_when_active_with_instance(self) -> None:
        ok, msgs = bench_optionc.run_preflight(
            state_dir=self.state_dir,
            boot_id_path=self.boot_id_path,
            dropin_dirs=(self.dropin_dir,),
            env_file=self.env_file,
            runner=self._runner(),
            which=lambda _cmd: "/usr/bin/btmon",
            pid_alive=lambda _p: True,
        )
        self.assertTrue(ok, msgs)

    def test_preflight_passes_with_bonded_le_link_when_active_instances_zero(self) -> None:
        runner = self._runner(
            bt_show="Controller AA:BB:CC:DD:EE:FF\n\tPowered: yes\n\tActiveInstances: 0x00 (0)\n",
            devices="Device 11:22:33:44:55:66 Pixel 11 Pro\n",
            device_info={
                "11:22:33:44:55:66": "Device 11:22:33:44:55:66\n\tPaired: yes\n\tConnected: yes\n\tUUID: Human Interface Device (00001812-0000-1000-8000-00805f9b34fb)\n"
            },
        )
        ok, msgs = bench_optionc.run_preflight(
            state_dir=self.state_dir,
            boot_id_path=self.boot_id_path,
            dropin_dirs=(self.dropin_dir,),
            env_file=self.env_file,
            runner=runner,
            which=lambda _cmd: "/usr/bin/btmon",
            pid_alive=lambda _p: True,
        )
        self.assertTrue(ok, msgs)
        self.assertTrue(any("ActiveInstances == 0" in m for m in msgs))

    def test_preflight_fails_when_active_instances_zero_and_no_bonded_le_link(self) -> None:
        runner = self._runner(
            bt_show="Controller AA:BB:CC:DD:EE:FF\n\tPowered: yes\n\tActiveInstances: 0x00 (0)\n",
        )
        ok, msgs = bench_optionc.run_preflight(
            state_dir=self.state_dir,
            boot_id_path=self.boot_id_path,
            dropin_dirs=(self.dropin_dir,),
            env_file=self.env_file,
            runner=runner,
            which=lambda _cmd: "/usr/bin/btmon",
            pid_alive=lambda _p: True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("ActiveInstances == 0" in m for m in msgs))

    def test_preflight_fails_on_systemd_dropin_including_90_serial_bench(self) -> None:
        self.dropin_dir.mkdir(parents=True)
        (self.dropin_dir / "90-serial-bench.conf").write_text("[Service]\n", encoding="utf-8")
        ok, msgs = bench_optionc.run_preflight(
            state_dir=self.state_dir,
            boot_id_path=self.boot_id_path,
            dropin_dirs=(self.dropin_dir,),
            env_file=self.env_file,
            runner=self._runner(),
            which=lambda _cmd: "/usr/bin/btmon",
            pid_alive=lambda _p: True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("90-serial-bench.conf" in m for m in msgs))

    def test_preflight_fails_on_no_ble_load_cuts_override(self) -> None:
        self.env_file.write_text('WMR_BRIDGE_ARGS="--no-ble-load-cuts"\n', encoding="utf-8")
        ok, msgs = bench_optionc.run_preflight(
            state_dir=self.state_dir,
            boot_id_path=self.boot_id_path,
            dropin_dirs=(self.dropin_dir,),
            env_file=self.env_file,
            runner=self._runner(),
            which=lambda _cmd: "/usr/bin/btmon",
            pid_alive=lambda _p: True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("--no-ble-load-cuts" in m for m in msgs))

    def test_preflight_fails_on_current_boot_suppression(self) -> None:
        (self.state_dir / "bt-recover.state.json").write_text(
            json.dumps({"boot_id": "boot-optionc-1", "suppressed": True}),
            encoding="utf-8",
        )
        ok, msgs = bench_optionc.run_preflight(
            state_dir=self.state_dir,
            boot_id_path=self.boot_id_path,
            dropin_dirs=(self.dropin_dir,),
            env_file=self.env_file,
            runner=self._runner(),
            which=lambda _cmd: "/usr/bin/btmon",
            pid_alive=lambda _p: True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("bt-recover.state.json" in m for m in msgs))

    def test_preflight_fails_on_prior_kernel_fault_and_missing_btmon(self) -> None:
        ok, msgs = bench_optionc.run_preflight(
            state_dir=self.state_dir,
            boot_id_path=self.boot_id_path,
            dropin_dirs=(self.dropin_dir,),
            env_file=self.env_file,
            runner=self._runner(
                klog="Nov 23 12:00:00 pi kernel: Bluetooth: hci0: Opcode 0x0c03 failed: -110\n"
            ),
            which=lambda _cmd: None,
            pid_alive=lambda _p: True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("btmon is absent" in m for m in msgs))
        self.assertTrue(any("0x0c03" in m for m in msgs))


class CaptureAndRedactionTests(unittest.TestCase):
    def test_redaction_strips_macs_keys_and_tokens(self) -> None:
        raw = (
            "Device AA:BB:CC:DD:EE:FF /org/bluez/hci0/dev_11_22_33_44_55_66\n"
            "Long Term Key: 00112233445566778899aabbccddeeff\n"
            "Link Key: ff00ee11dd22cc33bb44aa5599668877\n"
            'FF13 payload: {"type":"wmr_ble_session","token":"super-secret-token-123"}\n'
            "wmr-bridge --token my-cli-secret\n"
        )
        redacted = bench_optionc.redact_text(raw)
        self.assertNotIn("AA:BB:CC:DD:EE:FF", redacted)
        self.assertNotIn("11_22_33_44_55_66", redacted)
        self.assertNotIn("00112233445566778899aabbccddeeff", redacted)
        self.assertNotIn("ff00ee11dd22cc33bb44aa5599668877", redacted)
        self.assertNotIn("super-secret-token-123", redacted)
        self.assertNotIn("my-cli-secret", redacted)
        self.assertIn("[REDACTED_MAC]", redacted)
        self.assertIn("[REDACTED_KEY]", redacted)
        self.assertIn("[REDACTED_TOKEN]", redacted)

    def test_finalize_capture_rejects_empty_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            btsnoop = out_dir / "btmon.btsnoop"
            raw_journal = out_dir / "pi-journal.raw.txt"
            btsnoop.write_bytes(b"")
            raw_journal.write_text("some journal\n", encoding="utf-8")
            ok, msgs = bench_optionc.finalize_capture(
                out_dir, btsnoop_path=btsnoop, raw_journal_path=raw_journal
            )
            self.assertFalse(ok)
            self.assertTrue(any("empty or missing btmon" in m for m in msgs))

            btsnoop.write_bytes(b"btsnoop-bytes")
            raw_journal.write_text("", encoding="utf-8")
            ok, msgs = bench_optionc.finalize_capture(
                out_dir, btsnoop_path=btsnoop, raw_journal_path=raw_journal
            )
            self.assertFalse(ok)
            self.assertTrue(any("empty or missing journal" in m for m in msgs))

    def test_finalize_capture_writes_redacted_outputs_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            btsnoop = out_dir / "btmon.btsnoop"
            raw_journal = out_dir / "pi-journal.raw.txt"
            btsnoop.write_bytes(b"btsnoop-data")
            raw_journal.write_text(
                'Nov 23 12:00:00 pi wmr-bridge: {"token":"secret-tok"} dev AA:BB:CC:DD:EE:FF\n',
                encoding="utf-8",
            )

            def fake_runner(cmd):
                if cmd[:2] == ["btmon", "-r"]:
                    return bench_optionc.CommandResult(
                        0,
                        "> ACL Data RX: Handle 64 flags 0x02 dlen 62\n"
                        "  ATT: Handle Value Notification (0x1b) len 58\n"
                        "  Connection Interval: 15.00 ms (12)\n"
                        "  Peer Address: AA:BB:CC:DD:EE:FF\n"
                        "  Long Term Key: deadbeefcafebabe0011223344556677\n",
                    )
                return bench_optionc.CommandResult(1, "")

            ok, _msgs = bench_optionc.finalize_capture(
                out_dir,
                btsnoop_path=btsnoop,
                raw_journal_path=raw_journal,
                runner=fake_runner,
            )
            self.assertTrue(ok)
            btmon_txt = (out_dir / "btmon.txt").read_text(encoding="utf-8")
            journal_txt = (out_dir / "pi-journal.txt").read_text(encoding="utf-8")
            summary_txt = (out_dir / "summary.txt").read_text(encoding="utf-8")

            self.assertNotIn("AA:BB:CC:DD:EE:FF", btmon_txt)
            self.assertNotIn("deadbeefcafebabe0011223344556677", btmon_txt)
            self.assertNotIn("secret-tok", journal_txt)
            self.assertNotIn("AA:BB:CC:DD:EE:FF", journal_txt)
            self.assertIn("le_pdu_events=2", summary_txt)
            self.assertIn("15.00 ms (12)", summary_txt)
            self.assertIn("raw_btsnoop_policy=local-only", summary_txt)


if __name__ == "__main__":
    unittest.main()
