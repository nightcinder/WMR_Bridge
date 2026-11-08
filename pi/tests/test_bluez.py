"""Tests for hidraw discovery and report normalisation (no hardware)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from wmr_bridge.bluez import (
    BluezAutoPairer,
    HidrawDevice,
    discover_hidraw,
    hand_from_name,
    hand_from_override,
    normalize_report,
    parse_devices,
    parse_info,
)


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


class BluezAutoPairerTests(unittest.TestCase):
    LEFT = "AA:BB:CC:DD:EE:00"
    RIGHT = "AA:BB:CC:DD:EE:01"

    def make_pairer(self, bridge: _Bridge, runner, *, allowed_hands=(0, 1),
                    which=lambda _command: "/usr/bin/bluetoothctl"):
        return BluezAutoPairer(
            bridge,
            allowed_hands=allowed_hands,
            runner=runner,
            which=which,
            scan_seconds=1.0,
            poll_interval=1.0,
        )

    def test_pairs_trusts_and_connects_matching_controller(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "--timeout":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.LEFT} Motion controller - Left\n"
                                        "Device 11:22:33:44:55:66 Some keyboard\n")
            if args[1] == "info":
                return _completed(args, "        Name: Motion controller - Left\n"
                                        "        Paired: no\n        Trusted: no\n        Connected: no\n")
            return _completed(args)

        pairer = self.make_pairer(_Bridge(), runner)
        self.assertTrue(pairer._tick())

        actions = [call[1:] for call in calls if call[1] in ("pair", "trust", "connect")]
        self.assertEqual(
            actions,
            [["pair", self.LEFT], ["trust", self.LEFT], ["connect", self.LEFT]],
        )

    def test_skips_devices_that_are_already_paired_and_connected(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "--timeout":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.LEFT} Motion controller - Left\n")
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: yes\n        Connected: yes\n")
            return _completed(args)

        self.make_pairer(_Bridge(), runner)._tick()

        self.assertFalse([call for call in calls if call[1] in ("pair", "trust", "connect")])

    def test_does_not_scan_when_both_hands_are_connected(self) -> None:
        def runner(args: list[str], timeout: float):
            raise AssertionError(f"unexpected bluetoothctl call: {args}")

        self.make_pairer(_Bridge(left=True, right=True), runner)._tick()

    def test_ignores_controllers_outside_allowed_hands(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "--timeout":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.RIGHT} Motion controller - Right\n")
            if args[1] == "info":
                return _completed(args, "        Paired: no\n        Trusted: no\n        Connected: no\n")
            return _completed(args)

        pairer = self.make_pairer(_Bridge(), runner, allowed_hands=(0,))
        self.assertFalse(pairer._tick())

        self.assertFalse([call for call in calls if call[1] in ("pair", "trust", "connect")])
        self.assertFalse([call for call in calls if self.RIGHT in call])

    def test_idle_scans_back_off_between_bursts(self) -> None:
        def runner(args: list[str], timeout: float):
            if args[1] == "--timeout":
                return _completed(args)
            if args[1] == "devices":
                return _completed(args, f"Device {self.LEFT} Motion controller - Left\n")
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: yes\n        Connected: yes\n")
            return _completed(args)

        pairer = self.make_pairer(_Bridge(left=True), runner)

        self.assertFalse(pairer._tick())
        self.assertEqual(pairer._next_delay(0), 1.0)
        self.assertEqual(pairer._next_delay(1), 1.0)
        self.assertEqual(pairer._next_delay(2), 2.0)
        self.assertEqual(pairer._next_delay(7), 60.0)
        self.assertEqual(pairer._next_delay(100), 60.0)

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

    def test_scan_failure_still_reconnects_a_paired_controller(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], timeout: float):
            calls.append(args)
            if args[1] == "--timeout":
                return None
            if args[1] == "devices":
                return _completed(args, f"Device {self.LEFT} Motion controller - Left\n")
            if args[1] == "info":
                return _completed(args, "        Paired: yes\n        Trusted: yes\n        Connected: no\n")
            return _completed(args)

        self.make_pairer(_Bridge(), runner)._tick()

        actions = [call[1:] for call in calls if call[1] in ("pair", "trust", "connect")]
        self.assertEqual(actions, [["connect", self.LEFT]])


if __name__ == "__main__":
    unittest.main()
