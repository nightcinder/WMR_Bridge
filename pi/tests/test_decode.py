"""Decoder tests: field extraction, validation and 32-bit tick extension."""

from __future__ import annotations

import unittest

from wmr_bridge.decode import (
    BTN_MENU,
    BTN_SQUEEZE,
    ControllerState,
    decode_frame,
    extend_ticks,
    record_from_frame,
    record_from_state,
)
from wmr_bridge.protocol import Frame, pack_record, unpack_record


class DecoderTests(unittest.TestCase):
    def test_from_frame_matches_firmware_test(self) -> None:
        frame = bytearray(46)
        frame[0] = 0xA1
        frame[1] = 0x01
        frame[2] = 0x0C  # menu + squeeze
        frame[2 + 1] = 0xFF
        frame[2 + 2] = 0xF7
        frame[2 + 3] = 0x7F  # stick centred
        frame[2 + 4] = 0x80  # trigger
        frame[2 + 5] = 0xFF
        frame[2 + 6] = 0xFF  # trackpad untouched
        frame[2 + 7] = 0x55  # battery
        frame[2 + 28] = 0x10
        frame[2 + 29] = 0x27  # ticks 10000

        state = decode_frame(bytes(frame))
        self.assertIsNotNone(state)
        self.assertEqual(state.buttons, BTN_MENU | BTN_SQUEEZE)
        self.assertEqual(state.thumbstick_x, 0.0)
        self.assertEqual(state.thumbstick_y, 0.0)
        self.assertEqual(state.trackpad_x, 0.0)
        self.assertEqual(state.trackpad_y, 0.0)
        self.assertAlmostEqual(state.trigger, 128 / 255, places=6)
        self.assertEqual(state.battery, 0x55)
        self.assertEqual(state.ticks, 10000)

        record = record_from_state(state, hand=0, sequence=42, rx_time_us=123456)
        parsed = unpack_record(pack_record(record))
        self.assertEqual(parsed.hand, 0)
        self.assertEqual(parsed.sequence, 42)
        self.assertEqual(parsed.rx_time_us, 123456)
        self.assertEqual(parsed.buttons, BTN_MENU | BTN_SQUEEZE)
        self.assertEqual(parsed.stick, (0, 0))
        self.assertEqual(parsed.trackpad, (0, 0))
        self.assertEqual(parsed.trigger, 0x80)
        self.assertEqual(parsed.battery, 0x55)
        self.assertEqual(parsed.device_ticks, 10000)

    def test_rejects_non_report(self) -> None:
        self.assertIsNone(decode_frame(b""))
        self.assertIsNone(decode_frame(bytes(46)))
        bad = bytearray(46)
        bad[0] = 0xA1
        bad[1] = 0x02  # configuration report, not an input report
        self.assertIsNone(decode_frame(bytes(bad)))
        good = bytearray(46)
        good[0] = 0xA1
        good[1] = 0x01
        self.assertIsNotNone(decode_frame(bytes(good)))
        self.assertIsNone(decode_frame(bytes(good[:-1])))

    def test_record_from_invalid_frame(self) -> None:
        frame = Frame(hand=0, sequence=1, data=b"\xA1\x02" + bytes(44))
        self.assertIsNone(record_from_frame(frame))

    def test_stick_and_trackpad_scaling(self) -> None:
        frame = bytearray(46)
        frame[0] = 0xA1
        frame[1] = 0x01
        p = frame
        # Max/min stick values: 0xFFF and 0.
        p[2 + 1], p[2 + 2], p[2 + 3] = 0xFF, 0xFF, 0xFF
        p[2 + 5], p[2 + 6] = 0x64, 0x00  # trackpad +1.0 and -1.0
        state = decode_frame(bytes(frame))
        self.assertAlmostEqual(state.thumbstick_x, 1.0, places=6)
        self.assertAlmostEqual(state.thumbstick_y, 1.0, places=6)
        self.assertAlmostEqual(state.trackpad_x, 1.0, places=6)
        self.assertAlmostEqual(state.trackpad_y, -1.0, places=6)
        record = record_from_state(state, 0, 1, 0)
        self.assertEqual(record.stick, (32767, 32767))
        self.assertEqual(record.trackpad, (32767, -32767))

    def test_temperature_is_signed(self) -> None:
        frame = bytearray(46)
        frame[0] = 0xA1
        frame[1] = 0x01
        frame[2 + 17] = 0xFE
        frame[2 + 18] = 0xFF
        state = decode_frame(bytes(frame))
        self.assertEqual(state.temperature_raw, -2)

    def test_ticks_extend_across_wrap(self) -> None:
        previous = 0xFFFFFFFF - 5
        self.assertEqual(extend_ticks(previous, 3), 0x1_00000003)
        self.assertEqual(extend_ticks(0x1_00000003, 4), 0x1_00000004)
        # A smaller low word is a wrap, matching the C helper.
        self.assertEqual(extend_ticks(100, 50), 0x1_00000032)

    def test_state_is_frozen(self) -> None:
        state = ControllerState(
            buttons=0,
            thumbstick_x=0.0,
            thumbstick_y=0.0,
            trigger=0.0,
            trackpad_x=0.0,
            trackpad_y=0.0,
            battery=0,
            accel_raw=(0, 0, 0),
            accel=(0.0, 0.0, 0.0),
            temperature_raw=0,
            gyro_raw=(0, 0, 0),
            gyro=(0.0, 0.0, 0.0),
            ticks=0,
        )
        with self.assertRaises(Exception):
            state.ticks = 1  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
