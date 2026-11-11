"""Tests for hidraw discovery and report normalisation (no hardware)."""

from __future__ import annotations

import os
import pty
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from wmr_bridge.bluez import (
    BluezAutoPairer,
    BluezDevice,
    HidrawDevice,
    HidrawReader,
    WMR_ENABLE_SPACING,
    collect_status,
    discover_hidraw,
    disconnect_device,
    hand_from_name,
    hand_from_override,
    normalize_report,
    pair_results,
    parse_adapter,
    parse_devices,
    parse_info,
    scan_and_pair,
    set_device_blocked,
)
from wmr_bridge.bridge import Bridge
from wmr_bridge.decode import BTN_TRACKPAD_TOUCH
from wmr_bridge.inputs import HidrawManager


def payload() -> bytes:
    return bytes(range(44))


class NormaliseTests(unittest.TestCase):
    def test_firmware_frame_passes_through(self) -> None:
        frame = b"\xA1\x01" + payload()
        self.assertEqual(normalize_report(frame), frame)

    def test_hidraw_report_id_is_prefixed(self) -> None:
        report = b"\x01" + payload()
        self.assertEqual(normalize_report(report), b"\xA1" + report)

    def test_payload_only_is_accepted(self) -> None:
        self.assertEqual(normalize_report(payload()), b"\xA1\x01" + payload())

    def test_other_shapes_are_rejected(self) -> None:
        self.assertIsNone(normalize_report(b""))
        self.assertIsNone(normalize_report(b"\xA1\x02" + payload()))
        self.assertIsNone(normalize_report(b"\x02" + payload()))
        self.assertIsNone(normalize_report(payload() + b"\x00"))


class DiscoveryTests(unittest.TestCase):
    def make_tree(self, root: Path, nodes: dict[str, dict[str, str]]) -> None:
        for node, values in nodes.items():
            directory = root / node / "device"
            directory.mkdir(parents=True)
            (directory / "uevent").write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )

    def test_finds_wmr_names_and_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_tree(
                root,
                {
                    "hidraw0": {
                        "HID_NAME": "Motion controller - Right",
                        "HID_UNIQ": "aa:bb:cc:dd:ee:ff",
                        "HID_PHYS": "cc:98:8b:00:00:01",
                    },
                    "hidraw1": {
                        "HID_NAME": "Some keyboard",
                        "HID_UNIQ": "11:22:33:44:55:66",
                    },
                    "hidraw2": {
                        "HID_NAME": "Motion controller - Left",
                        "HID_UNIQ": "aa:bb:cc:dd:ee:00",
                    },
                },
            )
            devices = discover_hidraw(str(root))
            self.assertEqual([device.path for device in devices], ["/dev/hidraw0", "/dev/hidraw2"])
            self.assertEqual(devices[0].hand, 1)
            self.assertEqual(devices[1].hand, 0)
            self.assertEqual(devices[0].uniq, "aa:bb:cc:dd:ee:ff")
            everything = discover_hidraw(str(root), include_all=True)
            self.assertEqual(len(everything), 3)

    def test_tolerates_missing_uevent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "hidraw7").mkdir()
            os.makedirs(root / "not-hidraw")
            self.assertEqual(discover_hidraw(str(root)), [])
            self.assertEqual(discover_hidraw(str(root), include_all=True)[0].path, "/dev/hidraw7")


class SelectionTests(unittest.TestCase):
    def test_names(self) -> None:
        self.assertEqual(hand_from_name("Motion controller - Left"), 0)
        self.assertEqual(hand_from_name(" motion CONTROLLER - right "), 1)
        self.assertIsNone(hand_from_name("Motion controller"))

    def test_override_precedence_and_ambiguity(self) -> None:
        device = HidrawDevice("/dev/hidraw3", "Motion controller - Left", "AA:BB:CC:DD:EE:FF")
        self.assertEqual(hand_from_override(device, None, None), 0)
        self.assertEqual(hand_from_override(device, "aa:bb:cc:dd:ee:ff", None), 0)
        self.assertEqual(hand_from_override(device, None, "hidraw3"), 1)
        with self.assertRaises(ValueError):
            hand_from_override(device, "aa:bb:cc:dd:ee:ff", "hidraw3")

    def test_matches(self) -> None:
        device = HidrawDevice("/dev/hidraw3", "Motion controller - Left", "AA:BB:CC:DD:EE:FF")
        self.assertTrue(device.matches("aa:bb:cc:dd:ee:ff"))
        self.assertTrue(device.matches("/dev/hidraw3"))
        self.assertTrue(device.matches("hidraw3"))
        self.assertTrue(device.matches("left"))
        self.assertFalse(device.matches("right"))


class HidrawReaderTests(unittest.TestCase):
    LEFT_ENABLE = bytes.fromhex("0603010002")
    RIGHT_ENABLE = bytes.fromhex("060302e102")

    def test_open_uses_read_write_and_sends_both_enable_reports(self) -> None:
        with mock.patch("wmr_bridge.bluez.os.open", return_value=7) as opened, \
             mock.patch("wmr_bridge.bluez.os.write") as written, \
             mock.patch("wmr_bridge.bluez.time.sleep") as slept:
            reader = HidrawReader("/dev/hidraw0", clock=lambda: 100.0)
            self.assertEqual(reader.open(), 7)

        self.assertTrue(reader.writable)
        flags = opened.call_args.args[1]
        self.assertEqual(flags & os.O_RDWR, os.O_RDWR)
        self.assertEqual(opened.call_args.args[0], "/dev/hidraw0")
        self.assertEqual(
            [call.args[1][:5] for call in written.call_args_list],
            [self.LEFT_ENABLE, self.RIGHT_ENABLE],
        )
        self.assertTrue(all(len(call.args[1]) == 64 for call in written.call_args_list))
        self.assertTrue(all(call.args[0] == 7 for call in written.call_args_list))
        # The second enable report must be spaced by WMR_CFG_SPACING_MS.
        slept.assert_called_once_with(WMR_ENABLE_SPACING)

    def test_open_falls_back_to_read_only_and_skips_keep_alive(self) -> None:
        with mock.patch(
            "wmr_bridge.bluez.os.open",
            side_effect=[PermissionError(13, "permission denied"), 9],
        ) as opened, mock.patch("wmr_bridge.bluez.os.write") as written:
            reader = HidrawReader("/dev/hidraw0")
            self.assertEqual(reader.open(), 9)
            written.assert_not_called()

        self.assertFalse(reader.writable)
        self.assertEqual(opened.call_args_list[0].args[1] & os.O_RDWR, os.O_RDWR)
        self.assertFalse(opened.call_args_list[1].args[1] & os.O_RDWR)
        self.assertFalse(reader.keep_alive(now=1000.0))
        written.assert_not_called()

    def test_keep_alive_resends_only_after_the_interval(self) -> None:
        now = [100.0]
        with mock.patch("wmr_bridge.bluez.os.open", return_value=7), \
             mock.patch("wmr_bridge.bluez.os.write") as written:
            reader = HidrawReader(
                "/dev/hidraw0",
                keep_alive_interval=10.0,
                clock=lambda: now[0],
            )
            reader.open()
            self.assertEqual(written.call_count, 2)

            written.reset_mock()
            now[0] = 109.9
            self.assertFalse(reader.keep_alive())
            written.assert_not_called()

            now[0] = 110.0
            self.assertTrue(reader.keep_alive())
            self.assertEqual(
                [call.args[1][:5] for call in written.call_args_list],
                [self.LEFT_ENABLE, self.RIGHT_ENABLE],
            )


class _Hand:
    def __init__(self, connected: bool) -> None:
        self.connected = connected


class _Bridge:
    def __init__(self, left: bool = False, right: bool = False) -> None:
        self.hands = (_Hand(left), _Hand(right))


def _completed(args: list[str], stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


class BluezParseTests(unittest.TestCase):
    def test_devices_lists_addresses_and_names(self) -> None:
        output = (
            "Device AA:BB:CC:DD:EE:00 Motion controller - Left\n"
            "Device 11:22:33:44:55:66 Some keyboard\n"
        )
        devices = parse_devices(output)
        self.assertEqual([device.address for device in devices], ["AA:BB:CC:DD:EE:00", "11:22:33:44:55:66"])
        self.assertEqual([device.hand for device in devices], [0, None])

    def test_info_reads_flags(self) -> None:
        output = (
            "Device AA:BB:CC:DD:EE:01 (public)\n"
            "        Name: Motion controller - Right\n"
            "        Paired: yes\n"
            "        Trusted: no\n"
            "        Connected: yes\n"
        )
        info = parse_info(output)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.name, "Motion controller - Right")
        self.assertEqual(info.hand, 1)
        self.assertTrue(info.paired)
        self.assertFalse(info.trusted)
        self.assertTrue(info.connected)

    def test_scan_output_parses_new_and_changed_name_lines(self) -> None:
        output = (
            "[NEW] Device AA:BB:CC:DD:EE:00 Motion controller - Left\n"
            "[CHG] Device AA:BB:CC:DD:EE:00 Name: Motion controller - Left\n"
            "[CHG] Device AA:BB:CC:DD:EE:00 RSSI: -40\n"
            "[CHG] Device AA:BB:CC:DD:EE:01 Name: Motion controller - Right\n"
            "Device 11:22:33:44:55:66 Some keyboard\n"
        )
        devices = parse_devices(output)
        self.assertEqual(
            [(device.address, device.name) for device in devices],
            [
                ("AA:BB:CC:DD:EE:00", "Motion controller - Left"),
                ("AA:BB:CC:DD:EE:01", "Motion controller - Right"),
                ("11:22:33:44:55:66", "Some keyboard"),
            ],
        )
        self.assertEqual([device.hand for device in devices], [0, 1, None])

    def test_adapter_state_is_parsed(self) -> None:
        output = (
            "Controller AA:BB:CC:DD:EE:FF (public)\n"
            "\tName: wmr-pi\n"
            "\tPowered: yes\n"
            "\tDiscoverable: no\n"
            "\tPairable: yes\n"
            "\tDiscovering: yes\n"
        )
        adapter = parse_adapter(output)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.address, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(adapter.name, "wmr-pi")
        self.assertTrue(adapter.powered)
        self.assertTrue(adapter.pairable)
        self.assertTrue(adapter.discovering)
        self.assertIsNone(parse_adapter("No default controller available\n"))

    def test_pair_replies_keep_order_and_reason(self) -> None:
        output = (
            "Attempting to pair with AA:BB:CC:DD:EE:00\n"
            "Pairing successful\n"
            "Attempting to pair with AA:BB:CC:DD:EE:01\n"
            "Failed to pair: org.bluez.Error.AuthenticationFailed\n"
        )
        self.assertEqual(
            pair_results(output),
            [(True, ""), (False, "org.bluez.Error.AuthenticationFailed")],
        )


class StatusTests(unittest.TestCase):
    def make_tree(self, root: Path, node: str, uniq: str, name: str) -> None:
        directory = root / node / "device"
        directory.mkdir(parents=True)
        (directory / "uevent").write_text(
            f"HID_NAME={name}\nHID_UNIQ={uniq}\n", encoding="utf-8"
        )

    def test_collects_adapter_bond_and_hidraw_state(self) -> None:
        address = "AA:BB:CC:DD:EE:00"
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "show":
                return _completed(
                    args,
                    "Controller 11:22:33:44:55:66 (public)\n"
                    "\tName: wmr-pi\n\tPowered: yes\n\tPairable: yes\n"
                    "\tDiscovering: yes\n",
                )
            if args[1] == "devices":
                return _completed(args, f"Device {address} Motion controller - Left\n")
            if args[1] == "info":
                return _completed(
                    args,
                    f"Device {address} (public)\n"
                    "\tName: Motion controller - Left\n"
                    "\tPaired: yes\n\tTrusted: yes\n\tConnected: yes\n",
                )
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as temp:
            self.make_tree(Path(temp), "hidraw0", address, "Motion controller - Left")
            status = collect_status(runner=runner, sysfs_root=temp)

        self.assertIsNotNone(status.adapter)
        assert status.adapter is not None
        self.assertEqual(status.adapter.name, "wmr-pi")
        self.assertTrue(status.adapter.discovering)
        self.assertEqual([device.address for device in status.devices], [address])
        self.assertTrue(status.devices[0].paired)
        self.assertTrue(status.devices[0].trusted)
        self.assertEqual(status.hidraw[0].path, "/dev/hidraw0")
        self.assertEqual(status.hidraw[0].hand, 0)
        self.assertEqual([call[1] for call in calls], ["show", "devices", "info"])


class BluezAutoPairerTests(unittest.TestCase):
    LEFT = "AA:BB:CC:DD:EE:00"
    RIGHT = "AA:BB:CC:DD:EE:01"

    def make_pairer(self, bridge: _Bridge, runner, *, allowed_hands=(0, 1),
                    which=lambda _command: "/usr/bin/bluetoothctl", session=None,
                    scan_seconds: float = 1.0,
                    pairing_window_open=None,
                    on_scan_end=None,
                    rfkill_root="/nonexistent/rfkill"):
        return BluezAutoPairer(
            bridge,
            allowed_hands=allowed_hands,
            runner=runner,
            session=session or (lambda _seconds, _should_pair: []),
            pairing_window_open=pairing_window_open,
            on_scan_end=on_scan_end,
            which=which,
            scan_seconds=scan_seconds,
            poll_interval=1.0,
            rfkill_root=rfkill_root,
        )

    @staticmethod
    def make_rfkill_tree(root: Path, soft: bool = True, hard: bool = False) -> None:
        switch = root / "rfkill0"
        switch.mkdir(parents=True)
        (switch / "type").write_text("bluetooth\n", encoding="utf-8")
        (switch / "name").write_text("hci0\n", encoding="utf-8")
        (switch / "soft").write_text(f"{int(soft)}\n", encoding="utf-8")
        (switch / "hard").write_text(f"{int(hard)}\n", encoding="utf-8")

    def test_unpaired_device_is_left_to_the_scan_session(self) -> None:
        calls: list[list[str]] = []
        scans: list[bool] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "power":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.LEFT} Motion controller - Left\n")
            if args[1] == "info":
                return _completed(args, "        Paired: no\n        Trusted: no\n"
                                        "        Connected: no\n")
            return _completed(args)

        def session(_seconds: float, _should_pair) -> list:
            scans.append(True)
            return []

        self.assertFalse(self.make_pairer(_Bridge(), runner, session=session)._tick())

        # A separate bluetoothctl pair has no pairing agent, so pairing must
        # happen inside the interactive scan session instead.
        self.assertEqual([call[1:] for call in calls if call[1] == "pair"], [])
        self.assertEqual(scans, [True])

    def test_discovers_trusts_and_connects_matching_controller(self) -> None:
        calls: list[list[str]] = []
        discovered = BluezDevice(self.RIGHT, "Motion controller - Right")

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "power":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args)
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: no\n"
                                        "        Connected: no\n")
            return _completed(args)

        pairer = self.make_pairer(
            _Bridge(), runner, session=lambda _seconds, _should_pair: [discovered]
        )
        self.assertTrue(pairer._tick())

        actions = [call[1:] for call in calls if call[1] in ("pair", "trust", "connect")]
        self.assertEqual(actions, [["trust", self.RIGHT], ["connect", self.RIGHT]])

    def test_powers_the_adapter_on_before_scanning(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            return _completed(args)

        self.make_pairer(_Bridge(), runner)._tick()

        self.assertEqual(calls[0][1:], ["power", "on"])

    def test_adapter_failure_warns_once_and_keeps_retrying(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            return _completed(args, "No default controller available\n", 1)

        pairer = self.make_pairer(_Bridge(), runner)
        with self.assertLogs("wmr_bridge.bluez", level="WARNING") as logs:
            self.assertFalse(pairer._tick())
            self.assertFalse(pairer._tick())

        # A blocked or absent adapter is retried on later ticks instead of
        # disabling auto-pair for the life of the process.
        self.assertEqual([call[1:] for call in calls], [["power", "on"], ["power", "on"]])
        warnings = [line for line in logs.output if "adapter is not usable" in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn("retrying", warnings[0])

    def test_soft_blocked_adapter_is_unblocked_then_powered_on(self) -> None:
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as temp:
            rfkill_root = Path(temp)
            self.make_rfkill_tree(rfkill_root, soft=True)

            def runner(args: list[str], timeout: float):
                calls.append(args)
                return _completed(args)

            pairer = self.make_pairer(_Bridge(), runner, rfkill_root=str(rfkill_root))
            with self.assertLogs("wmr_bridge.bluez", level="INFO") as logs:
                self.assertFalse(pairer._tick())

        self.assertEqual(calls[0][:3], ["rfkill", "unblock", "bluetooth"])
        self.assertEqual(calls[1][1:], ["power", "on"])
        self.assertTrue(any("cleared the rfkill" in line for line in logs.output))

    def test_bonded_offline_controller_is_left_to_page_in(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "power":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.RIGHT} Motion controller - Right\n")
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: yes\n"
                                        "        Connected: no\n")
            return _completed(args)

        def session(_seconds: float, _should_pair) -> list:
            raise AssertionError("a bonded offline hand must not trigger a scan")

        pairer = self.make_pairer(_Bridge(left=True), runner, session=session)
        self.assertFalse(pairer._tick())

        actions = [call[1:] for call in calls if call[1] in ("pair", "trust", "connect")]
        self.assertEqual(actions, [])

    def test_trusts_paired_but_untrusted_controller_without_connecting(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "power":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.RIGHT} Motion controller - Right\n")
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: no\n"
                                        "        Connected: no\n")
            return _completed(args)

        def session(_seconds: float, _should_pair) -> list:
            raise AssertionError("a paired hand must not trigger a scan")

        pairer = self.make_pairer(_Bridge(left=True), runner, session=session)
        self.assertTrue(pairer._tick())

        actions = [call[1:] for call in calls if call[1] in ("pair", "trust", "connect")]
        self.assertEqual(actions, [["trust", self.RIGHT]])

    def test_scan_session_found_controller_is_trusted_and_connected(self) -> None:
        calls: list[list[str]] = []
        discovered = BluezDevice(self.RIGHT, "Motion controller - Right")

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "power":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args)
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: no\n"
                                        "        Connected: no\n")
            return _completed(args)

        def session(seconds: float, should_pair) -> list:
            self.assertEqual(seconds, 1.0)
            self.assertTrue(should_pair(discovered))
            self.assertFalse(should_pair(BluezDevice(self.LEFT, "Some keyboard")))
            return [discovered]

        pairer = self.make_pairer(_Bridge(), runner, session=session)
        self.assertTrue(pairer._tick())

        actions = [call[1:] for call in calls if call[1] in ("pair", "trust", "connect")]
        self.assertEqual(actions, [["trust", self.RIGHT], ["connect", self.RIGHT]])

    def test_scan_failure_logs_once_and_disables_pairing(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            return _completed(args)

        pairer = self.make_pairer(
            _Bridge(), runner, session=lambda _seconds, _should_pair: None
        )
        with self.assertLogs("wmr_bridge.bluez", level="WARNING") as logs:
            self.assertFalse(pairer._tick())
            self.assertFalse(pairer._tick())

        self.assertEqual(
            [call[1:] for call in calls],
            [["power", "on"], ["devices"], ["pairable", "off"]],
        )
        self.assertTrue(any("could not scan" in line for line in logs.output))

    def test_pairable_is_cleared_after_a_scan(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            return _completed(args)

        pairer = self.make_pairer(
            _Bridge(), runner, session=lambda _seconds, _should_pair: []
        )
        self.assertFalse(pairer._tick())

        self.assertEqual(
            [call[1:] for call in calls if call[1] == "pairable"],
            [["pairable", "off"]],
        )

    def test_pairable_stays_on_while_the_ble_window_is_open(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            return _completed(args)

        pairer = self.make_pairer(
            _Bridge(),
            runner,
            session=lambda _seconds, _should_pair: [],
            pairing_window_open=lambda: True,
        )
        self.assertFalse(pairer._tick())

        self.assertEqual([call for call in calls if call[1] == "pairable"], [])

    def test_scan_end_callback_runs_after_a_scan(self) -> None:
        scans: list[bool] = []

        def runner(args: list[str], timeout: float):
            return _completed(args)

        pairer = self.make_pairer(
            _Bridge(),
            runner,
            session=lambda _seconds, _should_pair: [],
            pairing_window_open=lambda: True,
            on_scan_end=lambda: scans.append(True),
        )
        pairer._tick()

        self.assertEqual(scans, [True])

    def test_scan_end_callback_runs_when_the_scan_raises(self) -> None:
        scans: list[bool] = []

        def session(_seconds: float, _should_pair) -> list:
            raise RuntimeError("scan failed")

        pairer = self.make_pairer(
            _Bridge(),
            lambda args, _timeout: _completed(args),
            session=session,
            on_scan_end=lambda: scans.append(True),
        )
        with self.assertRaises(RuntimeError):
            pairer._tick()

        self.assertEqual(scans, [True])

    def test_the_ble_window_keeps_the_bridge_agent_as_default(self) -> None:
        pairer = BluezAutoPairer(
            _Bridge(),
            pairing_window_open=lambda: True,
            which=lambda _command: "/usr/bin/bluetoothctl",
            rfkill_root="/nonexistent/rfkill",
        )
        with mock.patch("wmr_bridge.bluez.scan_and_pair", return_value=[]) as scan:
            pairer._scan_and_pair({0}, 1.0)
        self.assertFalse(scan.call_args.kwargs["claim_agent"])

    def test_without_a_ble_window_bluetoothctl_claims_its_own_agent(self) -> None:
        pairer = BluezAutoPairer(
            _Bridge(),
            which=lambda _command: "/usr/bin/bluetoothctl",
            rfkill_root="/nonexistent/rfkill",
        )
        with mock.patch("wmr_bridge.bluez.scan_and_pair", return_value=[]) as scan:
            pairer._scan_and_pair({0}, 1.0)
        self.assertTrue(scan.call_args.kwargs["claim_agent"])

    def test_pairable_is_cleared_when_a_scan_raises(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            return _completed(args)

        def session(_seconds: float, _should_pair) -> list:
            raise RuntimeError("scan failed")

        pairer = self.make_pairer(_Bridge(), runner, session=session)
        with self.assertRaises(RuntimeError):
            pairer._tick()

        self.assertEqual(
            [call[1:] for call in calls if call[1] == "pairable"],
            [["pairable", "off"]],
        )

    def test_skips_devices_that_are_already_paired_and_connected(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "power":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.LEFT} Motion controller - Left\n")
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: yes\n"
                                        "        Connected: yes\n")
            return _completed(args)

        self.make_pairer(_Bridge(), runner)._tick()

        self.assertFalse([call for call in calls if call[1] in ("pair", "trust", "connect")])

    def test_does_not_scan_when_both_hands_are_connected(self) -> None:
        def runner(args: list[str], timeout: float):
            if args[1] in ("power", "pairable"):
                return _completed(args)
            raise AssertionError(f"unexpected bluetoothctl call: {args}")

        self.assertTrue(self.make_pairer(_Bridge(left=True, right=True), runner)._tick())

    def test_ignores_controllers_outside_allowed_hands(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "power":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.RIGHT} Motion controller - Right\n")
            if args[1] == "info":
                return _completed(args, "        Paired: no\n        Trusted: no\n"
                                        "        Connected: no\n")
            return _completed(args)

        pairer = self.make_pairer(_Bridge(), runner, allowed_hands=(0,))
        self.assertFalse(pairer._tick())

        self.assertFalse([call for call in calls if call[1] in ("pair", "trust", "connect")])
        self.assertFalse([call for call in calls if self.RIGHT in call])

    def test_idle_scans_back_off_between_bursts(self) -> None:
        def runner(args: list[str], timeout: float):
            if args[1] == "power":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.LEFT} Motion controller - Left\n")
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: yes\n"
                                        "        Connected: yes\n")
            return _completed(args)

        pairer = self.make_pairer(_Bridge(), runner)

        self.assertFalse(pairer._tick())
        self.assertEqual(pairer._next_delay(0), 1.0)
        self.assertEqual(pairer._next_delay(1), 1.0)
        self.assertEqual(pairer._next_delay(2), 2.0)
        self.assertEqual(pairer._next_delay(6), 30.0)
        self.assertEqual(pairer._next_delay(100), 30.0)
        # idle grows forever while a hand is missing; 2 ** idle must never
        # reach an int-to-float conversion that raises OverflowError.
        self.assertEqual(pairer._next_delay(10**9), 30.0)

    def test_one_connected_hand_throttles_the_scan_for_the_other(self) -> None:
        scans: list[float] = []

        def runner(args: list[str], timeout: float):
            if args[1] == "power":
                return _completed(args)
            if args[1] == "pairable":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args)
            raise AssertionError(f"unexpected bluetoothctl call: {args}")

        def session(seconds: float, _should_pair) -> list:
            scans.append(seconds)
            return []

        pairer = self.make_pairer(
            _Bridge(left=True), runner, session=session, scan_seconds=10.0
        )
        self.assertFalse(pairer._tick())

        # The inquiry shares the radio with the live ACL link, so it is both
        # shorter and much less frequent than a scan with no controller up.
        self.assertEqual(scans, [2.0])
        self.assertEqual(pairer._next_delay(0), 60.0)
        self.assertEqual(pairer._next_delay(10), 60.0)

    def test_missing_bluetoothctl_disables_pairing_without_raising(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            return _completed(args)

        pairer = self.make_pairer(_Bridge(), runner, which=lambda _command: None)
        with self.assertLogs("wmr_bridge.bluez", level="WARNING") as logs:
            pairer._tick()
            pairer._tick()

        self.assertIn("automatic controller pairing is off", logs.output[0])
        self.assertEqual(calls, [])


class ScanAndPairTests(unittest.TestCase):
    """Drive the real pty/select session with a fake ``bluetoothctl`` process."""

    RIGHT = "AA:BB:CC:DD:EE:01"
    KEYBOARD = "11:22:33:44:55:66"

    @staticmethod
    def _read(fd: int) -> str:
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _run(self, script: bytes, should_pair=None, *, claim_agent: bool = True):
        master_fd, slave_fd = pty.openpty()
        keep_master_fd = os.dup(master_fd)
        commands_fd = os.dup(slave_fd)
        os.set_blocking(commands_fd, False)
        popen_calls: list[list[str]] = []

        class FakeProcess:
            killed = False

            def poll(self) -> int:
                return 0

            def kill(self) -> None:
                self.killed = True

            def wait(self) -> int:
                return 0

        process = FakeProcess()

        def fake_popen(args, **kwargs):
            popen_calls.append(args)
            os.write(kwargs["stdin"], script)
            return process

        pty_stub = types.SimpleNamespace(openpty=lambda: (master_fd, slave_fd))
        subprocess_stub = types.SimpleNamespace(Popen=fake_popen)
        try:
            with mock.patch("wmr_bridge.bluez.pty", pty_stub), mock.patch(
                "wmr_bridge.bluez.subprocess", subprocess_stub
            ):
                result = scan_and_pair(
                    "bluetoothctl",
                    0.05,
                    should_pair or (lambda device: device.hand is not None),
                    claim_agent=claim_agent,
                    settle_seconds=0.01,
                    pair_timeout=0.1,
                )
            commands = self._read(commands_fd)
        finally:
            try:
                os.close(commands_fd)
            finally:
                os.close(keep_master_fd)
        return result, commands, popen_calls, process

    def test_successful_scan_returns_devices_and_pairs_in_one_session(self) -> None:
        script = (
            "Agent registered\n"
            f"[NEW] Device {self.RIGHT} Motion controller - Right\n"
            f"[CHG] Device {self.KEYBOARD} RSSI: -40\n"
            f"Attempting to pair with {self.RIGHT}\n"
            "Pairing successful\n"
        ).encode()

        result, commands, popen_calls, process = self._run(script)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([device.address for device in result], [self.RIGHT])
        self.assertEqual(len(popen_calls), 1)
        self.assertIn("agent NoInputNoOutput", commands)
        self.assertIn("default-agent", commands)
        self.assertIn("pairable on", commands)
        self.assertIn("scan on", commands)
        self.assertIn(f"pair {self.RIGHT}", commands)
        self.assertNotIn(f"pair {self.KEYBOARD}", commands)
        self.assertLess(commands.index("scan on"), commands.index(f"pair {self.RIGHT}"))
        self.assertLess(commands.index(f"pair {self.RIGHT}"), commands.index("quit"))
        self.assertFalse(process.killed)

    def test_existing_default_agent_is_left_in_place(self) -> None:
        script = (
            "Agent registered\n"
            f"[NEW] Device {self.RIGHT} Motion controller - Right\n"
            f"Attempting to pair with {self.RIGHT}\n"
            "Pairing successful\n"
        ).encode()

        result, commands, _popen_calls, _process = self._run(
            script, claim_agent=False
        )

        self.assertIsNotNone(result)
        self.assertIn("agent off", commands)
        self.assertNotIn("agent NoInputNoOutput", commands)
        self.assertNotIn("default-agent", commands)
        self.assertIn("pairable on", commands)
        self.assertIn("scan on", commands)
        self.assertIn(f"pair {self.RIGHT}", commands)

    def test_real_session_registers_agent_and_waits_for_the_pair_reply(self) -> None:
        left = "AA:BB:CC:DD:EE:00"
        fake = """#!{python}
import sys, threading, time
LOG = {log!r}
agent = False
lock = threading.Lock()
replied = threading.Event()


def finish_pair():
    # Real bluetoothctl answers `pair` asynchronously. A session that quits
    # before this reply arrives tears down the agent; model that by suppressing
    # the reply once `quit` has been seen.
    time.sleep(0.05)
    with lock:
        if not replied.is_set():
            replied.set()
            print("Pairing successful", flush=True)


print("Failed to register agent: org.bluez.Error.InvalidArguments", flush=True)
for line in sys.stdin:
    line = line.strip()
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\\n")
    if line.startswith("agent "):
        agent = True
        print("Agent registered", flush=True)
    elif line == "default-agent":
        print("Default agent registered", flush=True)
    elif line == "pairable on":
        print("Changing pairable on succeeded", flush=True)
    elif line == "scan on":
        print("Discovery started", flush=True)
        print("[NEW] Device {address} Motion controller - Left", flush=True)
    elif line.startswith("pair "):
        if not agent:
            print("Failed to pair: org.bluez.Error.AuthenticationFailed", flush=True)
            continue
        print("Attempting to pair with " + line.split()[1], flush=True)
        threading.Thread(target=finish_pair, daemon=True).start()
    elif line == "scan off":
        print("Discovery stopped", flush=True)
    elif line == "quit":
        with lock:
            if not replied.is_set():
                replied.set()  # quit before the reply: no bond result
                break
        break
"""
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "commands.log"
            path = Path(temp) / "fake-bluetoothctl"
            path.write_text(
                fake.format(python=sys.executable, log=str(log), address=left),
                encoding="utf-8",
            )
            path.chmod(0o755)
            with self.assertLogs("wmr_bridge.bluez", level="INFO") as logs:
                result = scan_and_pair(
                    str(path),
                    0.1,
                    lambda device: device.hand == 0,
                    settle_seconds=0.01,
                    pair_timeout=2.0,
                )
            commands = log.read_text(encoding="utf-8").splitlines()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([device.address for device in result], [left])
        self.assertTrue(any(f"paired {left}" in line for line in logs.output))
        self.assertIn("agent NoInputNoOutput", commands)
        self.assertLess(commands.index("agent NoInputNoOutput"), commands.index("default-agent"))
        self.assertLess(commands.index("default-agent"), commands.index("scan on"))
        self.assertLess(commands.index("scan on"), commands.index(f"pair {left}"))
        self.assertLess(commands.index(f"pair {left}"), commands.index("quit"))

    def test_scan_failure_marker_returns_none(self) -> None:
        result, _, popen_calls, _ = self._run(
            b"Agent registered\nNo default controller available\n"
        )

        self.assertIsNone(result)
        self.assertEqual(len(popen_calls), 1)

    def test_returns_none_when_the_pty_cannot_open(self) -> None:
        def broken_openpty():
            raise OSError("cannot allocate pty")

        pty_stub = types.SimpleNamespace(openpty=broken_openpty)
        with mock.patch("wmr_bridge.bluez.pty", pty_stub):
            self.assertIsNone(scan_and_pair("bluetoothctl", 0.05, lambda device: True))

    def test_returns_none_when_the_client_cannot_start(self) -> None:
        master_fd, slave_fd = pty.openpty()

        def broken_popen(_args, **_kwargs):
            raise OSError("exec failed")

        pty_stub = types.SimpleNamespace(openpty=lambda: (master_fd, slave_fd))
        subprocess_stub = types.SimpleNamespace(Popen=broken_popen)
        with mock.patch("wmr_bridge.bluez.pty", pty_stub), mock.patch(
            "wmr_bridge.bluez.subprocess", subprocess_stub
        ):
            self.assertIsNone(scan_and_pair("bluetoothctl", 0.05, lambda device: True))


def controller_frame(
    *,
    buttons: int = 0,
    trigger: int = 0,
    stick_x: int = 0x07FF,
    stick_y: int = 0x07FF,
    gyro: tuple[int, int, int] = (0, 0, 0),
) -> bytes:
    """Build a resting 0x01 frame, overriding only the field under test."""

    payload = bytearray(44)
    payload[0] = buttons
    payload[1] = stick_x & 0xFF
    payload[2] = ((stick_x >> 8) & 0x0F) | ((stick_y & 0x0F) << 4)
    payload[3] = (stick_y >> 4) & 0xFF
    payload[4] = trigger
    for index, value in enumerate(gyro):
        raw = value & 0xFFFFFF
        offset = 19 + index * 3
        payload[offset] = raw & 0xFF
        payload[offset + 1] = (raw >> 8) & 0xFF
        payload[offset + 2] = (raw >> 16) & 0xFF
    return b"\xA1\x01" + bytes(payload)


class DisconnectTests(unittest.TestCase):
    ADDRESS = "AA:BB:CC:DD:EE:00"

    def test_disconnect_asks_bluetoothctl_to_drop_the_acl_link(self) -> None:
        calls: list[tuple[list[str], float]] = []

        def runner(args: list[str], timeout: float):
            calls.append((args, timeout))
            return _completed(args)

        self.assertTrue(disconnect_device(self.ADDRESS, runner=runner))
        self.assertEqual(calls[0][0], ["bluetoothctl", "disconnect", self.ADDRESS])

    def test_disconnect_failure_and_missing_address_return_false(self) -> None:
        def runner(args: list[str], timeout: float):
            return _completed(args, "Failed\n", 1)

        self.assertFalse(disconnect_device(self.ADDRESS, runner=runner))
        self.assertFalse(disconnect_device("", runner=runner))

    def test_disconnect_detects_failed_output_with_zero_exit(self) -> None:
        def runner(args: list[str], timeout: float):
            return _completed(args, "Failed to disconnect: org.bluez.Error.Failed\n", 0)

        self.assertFalse(disconnect_device(self.ADDRESS, runner=runner))

    def test_disconnect_treats_not_connected_as_already_dropped(self) -> None:
        def runner(args: list[str], timeout: float):
            return _completed(
                args, "Failed to disconnect: org.bluez.Error.NotConnected\n", 0
            )

        self.assertTrue(disconnect_device(self.ADDRESS, runner=runner))

    def test_block_and_unblock_ask_bluetoothctl_to_guard_the_device(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            return _completed(args)

        self.assertTrue(set_device_blocked(self.ADDRESS, True, runner=runner))
        self.assertTrue(set_device_blocked(self.ADDRESS, False, runner=runner))
        self.assertEqual(
            calls,
            [
                ["bluetoothctl", "block", self.ADDRESS],
                ["bluetoothctl", "unblock", self.ADDRESS],
            ],
        )

    def test_block_failure_and_missing_address_return_false(self) -> None:
        def runner(args: list[str], timeout: float):
            return _completed(args, "Failed to block\n", 1)

        self.assertFalse(set_device_blocked(self.ADDRESS, True, runner=runner))
        self.assertFalse(set_device_blocked("", True, runner=runner))


class _FakeReader:
    def __init__(self, path: str = "/dev/hidraw0", **_kwargs: object) -> None:
        self.path = path
        self.fd = 42
        self.enables = 0
        self.keep_alives = 0
        self.writes = 0
        self.keep_alive_interval = 60.0
        self.clock = 0.0
        self._last_enable: float | None = None
        self.reads: list[bytes] = []

    def open(self) -> int:
        return self.fd

    def send_enable(self) -> bool:
        self.enables += 1
        self.writes += 1
        self._last_enable = self.clock
        return True

    def keep_alive(self, now: float | None = None) -> bool:
        current = self.clock if now is None else now
        if (
            self._last_enable is not None
            and current - self._last_enable < self.keep_alive_interval
        ):
            return False
        self.keep_alives += 1
        self.writes += 1
        self._last_enable = current
        return True

    def read(self) -> bytes | None:
        return self.reads.pop(0) if self.reads else None

    def close(self) -> None:
        pass


class IdlePolicyTests(unittest.IsolatedAsyncioTestCase):
    ADDRESS = "AA:BB:CC:DD:EE:00"
    START_US = 1_000_000

    def make_manager(self, *, sleep: float = 300.0, poweroff: float = 900.0,
                     poweroff_cb=None, cooldown: float = 60.0,
                     reconnect_guard=None, reference: bytes | None = None):
        bridge = Bridge()
        manager = HidrawManager(
            bridge,
            idle_sleep_timeout=sleep,
            idle_poweroff_timeout=poweroff,
            idle_reconnect_cooldown=cooldown,
            poweroff=poweroff_cb,
            reconnect_guard=(
                (lambda address, blocked: True)
                if reconnect_guard is None
                else reconnect_guard
            ),
        )
        reader = _FakeReader()
        manager._readers[reader.path] = reader  # type: ignore[assignment]
        manager._hand_of[reader.path] = 0
        manager._path_of_hand[0] = reader.path
        manager._address_of_hand[0] = self.ADDRESS
        # Establish the per-hand baseline the way a real connect plus first
        # report does, so tests can move away from a known rest pose.
        bridge.set_hand_connected(0, True, self.ADDRESS)
        bridge.ingest(
            0,
            controller_frame() if reference is None else reference,
            rx_time_us=self.START_US,
        )
        bridge.hands[0].last_activity_us = self.START_US
        return manager, bridge, reader

    async def test_keep_alive_pauses_at_idle_timeout_and_wakes_on_activity(self) -> None:
        manager, _bridge, reader = self.make_manager()

        await manager._idle_tick(self.START_US + 299_000_000)
        self.assertEqual(reader.keep_alives, 1)
        self.assertNotIn(0, manager._asleep)

        await manager._idle_tick(self.START_US + 300_000_000)
        self.assertEqual(reader.keep_alives, 1)
        self.assertIn(0, manager._asleep)

        reader.reads = [controller_frame(buttons=0x01)]
        manager._on_readable(0, reader)  # type: ignore[arg-type]

        self.assertNotIn(0, manager._asleep)
        self.assertEqual(reader.enables, 1)
        # The immediate 0x06 wake counts as the keep-alive; no duplicate write
        # happens on the next tick.
        reader.writes = 0
        await manager._idle_tick(self.START_US + 301_000_000)
        self.assertEqual(reader.enables, 1)
        self.assertEqual(reader.writes, 0)

    async def test_stationary_off_centre_pose_sleeps_and_powers_off(self) -> None:
        calls: list[str] = []

        def poweroff(address: str) -> bool:
            calls.append(address)
            return True

        still = controller_frame(
            buttons=BTN_TRACKPAD_TOUCH,
            trigger=20,
            stick_x=0x07FF - 131,
            stick_y=0x07FF + 303,
            gyro=(9000, -7000, 5000),
        )
        manager, bridge, reader = self.make_manager(
            poweroff_cb=poweroff, reference=still
        )
        reader.reads = [still, still]
        manager._on_readable(0, reader)  # type: ignore[arg-type]

        self.assertEqual(bridge.hands[0].last_activity_us, self.START_US)

        await manager._idle_tick(self.START_US + 300_000_000)
        self.assertIn(0, manager._asleep)
        self.assertTrue(bridge.status()["hands"][0]["idleSleep"])

        reader.reads = [still]
        manager._on_readable(0, reader)  # type: ignore[arg-type]
        self.assertIn(0, manager._asleep)

        await manager._idle_tick(self.START_US + 900_000_000)
        self.assertEqual(calls, [self.ADDRESS])
        self.assertIn(0, manager._powered_off)

    async def test_stationary_frames_do_not_wake_but_movement_does(self) -> None:
        still = controller_frame(
            buttons=BTN_TRACKPAD_TOUCH,
            trigger=20,
            stick_x=0x07FF - 131,
            stick_y=0x07FF + 303,
        )
        manager, bridge, reader = self.make_manager(reference=still)
        reader.reads = [still]
        manager._on_readable(0, reader)  # type: ignore[arg-type]
        await manager._idle_tick(self.START_US + 300_000_000)
        self.assertIn(0, manager._asleep)

        reader.reads = [still, still]
        manager._on_readable(0, reader)  # type: ignore[arg-type]
        self.assertIn(0, manager._asleep)
        self.assertEqual(reader.enables, 0)

        moved = controller_frame(
            buttons=BTN_TRACKPAD_TOUCH,
            trigger=20,
            stick_x=0x07FF - 131,
            stick_y=0x07FF + 303,
            gyro=(9000, 0, 0),
        )
        reader.reads = [moved]
        manager._on_readable(0, reader)  # type: ignore[arg-type]

        self.assertNotIn(0, manager._asleep)
        self.assertFalse(bridge.status()["hands"][0]["idleSleep"])
        self.assertEqual(reader.enables, 1)

    async def test_poweroff_disconnects_once_at_idle_timeout(self) -> None:
        calls: list[str] = []

        def poweroff(address: str) -> bool:
            calls.append(address)
            return True

        manager, _bridge, reader = self.make_manager(poweroff_cb=poweroff)

        await manager._idle_tick(self.START_US + 899_000_000)
        self.assertEqual(calls, [])
        self.assertEqual(reader.keep_alives, 0)

        await manager._idle_tick(self.START_US + 900_000_000)
        self.assertEqual(calls, [self.ADDRESS])
        self.assertIn(0, manager._powered_off)

        await manager._idle_tick(self.START_US + 1_000_000_000)
        self.assertEqual(calls, [self.ADDRESS])

    async def test_failed_poweroff_is_retried_and_keepalives_stay_paused(self) -> None:
        results = [False, True]
        calls: list[str] = []

        def poweroff(address: str) -> bool:
            calls.append(address)
            return results.pop(0) if results else True

        manager, _bridge, reader = self.make_manager(
            poweroff_cb=poweroff, cooldown=0.0
        )

        await manager._idle_tick(self.START_US + 900_000_000)
        self.assertEqual(calls, [self.ADDRESS])
        self.assertNotIn(0, manager._powered_off)
        self.assertIn(0, manager._asleep)
        self.assertEqual(reader.keep_alives, 0)

        await manager._idle_tick(self.START_US + 901_000_000)
        self.assertEqual(calls, [self.ADDRESS, self.ADDRESS])
        self.assertIn(0, manager._powered_off)
        self.assertEqual(reader.keep_alives, 0)

        await manager._idle_tick(self.START_US + 1_000_000_000)
        self.assertEqual(calls, [self.ADDRESS, self.ADDRESS])

    async def test_poweroff_without_address_retries_until_one_is_known(self) -> None:
        calls: list[str] = []
        manager, _bridge, _reader = self.make_manager(
            poweroff_cb=lambda address: calls.append(address) or True
        )
        manager._address_of_hand.pop(0)

        await manager._idle_tick(self.START_US + 900_000_000)
        self.assertEqual(calls, [])
        self.assertNotIn(0, manager._powered_off)
        self.assertIn(0, manager._asleep)

        manager._address_of_hand[0] = self.ADDRESS
        await manager._idle_tick(self.START_US + 901_000_000)
        self.assertEqual(calls, [self.ADDRESS])
        self.assertIn(0, manager._powered_off)

    async def test_activity_during_inflight_poweroff_relatches_the_disconnect(self) -> None:
        manager, bridge, reader = self.make_manager()

        def poweroff(address: str) -> bool:
            bridge.hands[0].last_activity_us = self.START_US + 900_000_000
            manager._note_activity(0, reader)  # type: ignore[arg-type]
            return True

        manager._poweroff = poweroff
        await manager._idle_tick(self.START_US + 900_000_000)

        self.assertIn(0, manager._powered_off)
        self.assertIn(0, manager._asleep)

    async def test_poweroff_blocks_reconnect_until_the_cooldown_ends(self) -> None:
        guard_calls: list[tuple[str, bool]] = []

        def guard(address: str, blocked: bool) -> bool:
            guard_calls.append((address, blocked))
            return True

        manager, _bridge, reader = self.make_manager(
            poweroff_cb=lambda address: True, reconnect_guard=guard
        )

        await manager._idle_tick(self.START_US + 900_000_000)

        self.assertEqual(guard_calls, [(self.ADDRESS, True)])
        self.assertIn(0, manager._powered_off)
        self.assertEqual(
            manager._powered_off_until_us[0], self.START_US + 960_000_000
        )
        self.assertNotIn(reader.path, manager._readers)

        # BlueZ may still deliver a page-in; the manager refuses to open it,
        # and a report already in flight cannot undo the latch.
        manager.left_spec = reader.path
        manager._device_for = lambda path: HidrawDevice(
            path=path, name="", uniq=self.ADDRESS
        )
        self.assertFalse(manager._open(reader.path))
        manager._note_activity(0, reader)  # type: ignore[arg-type]
        self.assertIn(0, manager._powered_off)

        await manager._idle_tick(self.START_US + 960_000_000)

        self.assertEqual(
            guard_calls, [(self.ADDRESS, True), (self.ADDRESS, False)]
        )
        self.assertNotIn(0, manager._powered_off)
        self.assertNotIn(0, manager._powered_off_until_us)

        class _Loop:
            def add_reader(self, *_args: object) -> None:
                pass

        manager._loop = _Loop()  # type: ignore[assignment]
        with mock.patch("wmr_bridge.inputs.HidrawReader", _FakeReader):
            self.assertTrue(manager._open(reader.path))

    async def test_failed_poweroff_lifts_the_block_and_retries(self) -> None:
        guard_calls: list[tuple[str, bool]] = []
        results = [False, True]

        manager, _bridge, _reader = self.make_manager(
            poweroff_cb=lambda address: results.pop(0),
            reconnect_guard=(
                lambda address, blocked: guard_calls.append((address, blocked))
                or False
            ),
        )

        await manager._idle_tick(self.START_US + 900_000_000)
        self.assertEqual(guard_calls, [(self.ADDRESS, True)])
        self.assertNotIn(0, manager._powered_off)
        self.assertNotIn(0, manager._powered_off_until_us)

        await manager._idle_tick(self.START_US + 901_000_000)
        self.assertEqual(
            guard_calls, [(self.ADDRESS, True), (self.ADDRESS, True)]
        )
        self.assertIn(0, manager._powered_off)

    async def test_successful_block_keeps_the_latch_when_disconnect_races_it(self) -> None:
        """BlueZ's block drops the ACL, so a NotConnected disconnect is success."""

        guard_calls: list[tuple[str, bool]] = []

        def guard(address: str, blocked: bool) -> bool:
            guard_calls.append((address, blocked))
            return True

        manager, _bridge, reader = self.make_manager(
            poweroff_cb=lambda address: False, reconnect_guard=guard
        )

        await manager._idle_tick(self.START_US + 900_000_000)

        self.assertEqual(guard_calls, [(self.ADDRESS, True)])
        self.assertIn(0, manager._powered_off)
        self.assertEqual(
            manager._powered_off_until_us[0], self.START_US + 960_000_000
        )
        self.assertNotIn(reader.path, manager._readers)

        await manager._idle_tick(self.START_US + 901_000_000)

        self.assertEqual(guard_calls, [(self.ADDRESS, True)])
        self.assertIn(0, manager._powered_off)

    async def test_zero_cooldown_disables_the_reconnect_guard(self) -> None:
        guard_calls: list[tuple[str, bool]] = []
        manager, _bridge, _reader = self.make_manager(
            poweroff_cb=lambda address: True,
            cooldown=0.0,
            reconnect_guard=(
                lambda address, blocked: guard_calls.append((address, blocked))
                or True
            ),
        )

        await manager._idle_tick(self.START_US + 900_000_000)

        self.assertEqual(guard_calls, [])
        self.assertIn(0, manager._powered_off)
        self.assertNotIn(0, manager._powered_off_until_us)

    async def test_close_lifts_a_remaining_block(self) -> None:
        guard_calls: list[tuple[str, bool]] = []
        manager, _bridge, _reader = self.make_manager(
            poweroff_cb=lambda address: True,
            reconnect_guard=(
                lambda address, blocked: guard_calls.append((address, blocked))
                or True
            ),
        )

        await manager._idle_tick(self.START_US + 900_000_000)
        await manager.close()

        self.assertEqual(guard_calls[-1], (self.ADDRESS, False))

    async def test_zero_timeouts_disable_both_stages(self) -> None:
        calls: list[str] = []
        manager, _bridge, reader = self.make_manager(
            sleep=0.0,
            poweroff=0.0,
            poweroff_cb=lambda address: calls.append(address) or True,
        )

        await manager._idle_tick(self.START_US + 10_000_000_000)

        self.assertEqual(reader.keep_alives, 1)
        self.assertEqual(calls, [])
        self.assertNotIn(0, manager._asleep)

    async def test_poweroff_still_applies_when_sleep_is_disabled(self) -> None:
        calls: list[str] = []
        manager, _bridge, reader = self.make_manager(
            sleep=0.0,
            poweroff=900.0,
            poweroff_cb=lambda address: calls.append(address) or True,
        )

        await manager._idle_tick(self.START_US + 899_000_000)
        self.assertEqual(reader.keep_alives, 1)

        await manager._idle_tick(self.START_US + 900_000_000)
        self.assertEqual(calls, [self.ADDRESS])


if __name__ == "__main__":
    unittest.main()
