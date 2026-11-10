"""HID gamepad report map and mapping, pinned against the firmware vectors."""

from __future__ import annotations

from pathlib import Path
import unittest

from wmr_bridge.hid_gamepad import (
    DEFAULT_CONFIG,
    NEUTRAL_REPORT,
    REPORT_ID,
    REPORT_MAP,
    REPORT_SIZE,
    HandInput,
    HidConfig,
    HidHistory,
    build_report,
)

VECTORS = Path(__file__).resolve().parents[2] / "tests" / "vectors"
TRACKPAD_NONE = 0
TRACKPAD_DPAD = 1
TRACKPAD_FACE = 2


def _vector_lines(name: str) -> list[str]:
    path = VECTORS / name
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class ReportMapTests(unittest.TestCase):
    def test_report_map_matches_the_firmware_descriptor_byte_for_byte(self) -> None:
        expected = bytes.fromhex("".join(_vector_lines("hid_report_descriptor.txt")))
        self.assertEqual(REPORT_MAP, expected)
        self.assertEqual(REPORT_ID, 0x01)
        self.assertEqual(REPORT_SIZE, 9)

    def test_neutral_report_is_centred_hat_and_zero_axes(self) -> None:
        self.assertEqual(NEUTRAL_REPORT, bytes((0, 0, 15, 0, 0, 0, 0, 0, 0)))
        self.assertEqual(build_report(HandInput(), HandInput()), NEUTRAL_REPORT)


class GamepadVectorTests(unittest.TestCase):
    """The shared vectors are the wire contract with src/wmr_hid.c."""

    def test_every_gamepad_vector_matches(self) -> None:
        rows = _vector_lines("hid_gamepad.txt")
        self.assertGreaterEqual(len(rows), 20)
        for row in rows:
            fields = row.split()
            name = fields[0]
            values = fields[1:]
            self.assertEqual(len(values), 29, name)
            left = HandInput(
                connected=bool(int(values[0])),
                buttons=int(values[1]),
                stick=(float(values[2]), float(values[3])),
                trigger=int(values[4]),
                trackpad=(float(values[5]), float(values[6])),
            )
            right = HandInput(
                connected=bool(int(values[7])),
                buttons=int(values[8]),
                stick=(float(values[9]), float(values[10])),
                trigger=int(values[11]),
                trackpad=(float(values[12]), float(values[13])),
            )
            history = HidHistory(bool(int(values[14])), bool(int(values[15])))
            config = HidConfig(
                invert_stick_y=bool(int(values[16])),
                invert_trackpad_y=bool(int(values[17])),
                left_trackpad=int(values[18]),
                right_trackpad=int(values[19]),
            )
            expected = bytes(int(value) for value in values[20:29])
            with self.subTest(vector=name):
                self.assertEqual(
                    build_report(left, right, config, history), expected
                )

    def test_trigger_history_updates_for_the_next_report(self) -> None:
        history = HidHistory()
        self.assertEqual(
            build_report(
                HandInput(connected=True, trigger=255), HandInput(), history=history
            )[1],
            0x01,
        )
        self.assertTrue(history.l2)
        self.assertFalse(history.r2)

    def test_only_connected_hands_are_mapped(self) -> None:
        left = HandInput(connected=False, buttons=0x08)
        right = HandInput(connected=True, buttons=0x0E)
        report = build_report(left, right, DEFAULT_CONFIG)
        self.assertEqual(report[0], 0x80)  # R1
        self.assertEqual(report[1], 0x18)  # Guide + Start

    def test_unused_trackpad_role_leaves_buttons_empty(self) -> None:
        hand = HandInput(
            connected=True,
            buttons=0x50,  # trackpad click + touch
            trackpad=(0.9, 0.1),
        )
        report = build_report(
            hand, HandInput(), HidConfig(left_trackpad=TRACKPAD_NONE)
        )
        self.assertEqual(report[0], 0x00)
        report = build_report(
            hand, HandInput(), HidConfig(left_trackpad=TRACKPAD_DPAD)
        )
        self.assertEqual(report[0], 0x00)
        self.assertEqual(report[2], 0x02)
        report = build_report(
            hand, HandInput(), HidConfig(left_trackpad=TRACKPAD_FACE)
        )
        self.assertEqual(report[0], 0x02)
