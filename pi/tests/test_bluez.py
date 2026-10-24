"""Tests for hidraw discovery and report normalisation (no hardware)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from wmr_bridge.bluez import (
    HidrawDevice,
    discover_hidraw,
    hand_from_name,
    hand_from_override,
    normalize_report,
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


if __name__ == "__main__":
    unittest.main()
