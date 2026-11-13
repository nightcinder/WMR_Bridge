"""Decoder tests: field extraction, validation and 32-bit tick extension."""

from __future__ import annotations

import dataclasses
import random
import struct
import unittest

from wmr_bridge.decode import (
    BTN_MENU,
    BTN_SQUEEZE,
    ControllerState,
    _decode_frame_reference,
    _record_from_state_reference,
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


def _report(payload: bytes = b"") -> bytearray:
    """A valid 46-byte input report with ``payload`` at report offset 2."""

    frame = bytearray(46)
    frame[0] = 0xA1
    frame[1] = 0x01
    frame[2 : 2 + len(payload)] = payload
    return frame


def _stick_report(raw: int) -> bytes:
    """Put the same 12-bit raw value in both sticks, plus trackpad/trigger bytes."""

    frame = _report()
    frame[2 + 1] = raw & 0xFF
    frame[2 + 2] = ((raw >> 8) & 0x0F) | ((raw & 0x0F) << 4)
    frame[2 + 3] = raw >> 4
    frame[2 + 4] = raw & 0xFF  # trigger
    frame[2 + 5] = raw & 0xFF  # trackpad x
    frame[2 + 6] = (raw >> 4) & 0xFF  # trackpad y
    return bytes(frame)


def _random_reports(seed: int, count: int) -> list[bytes]:
    rng = random.Random(seed)
    reports = []
    for _ in range(count):
        frame = bytearray(rng.randbytes(46))
        frame[0] = 0xA1
        frame[1] = 0x01
        reports.append(bytes(frame))
    return reports


def _float_fields(state: ControllerState) -> list[float]:
    return [
        state.thumbstick_x,
        state.thumbstick_y,
        state.trigger,
        state.trackpad_x,
        state.trackpad_y,
        *state.accel,
        *state.gyro,
    ]


class FastDecodeParityTests(unittest.TestCase):
    """The fast binary32 path must match the Fraction reference bit for bit."""

    def assertSameFrame(self, frame: bytes) -> None:
        fast = decode_frame(frame)
        reference = _decode_frame_reference(frame)
        self.assertEqual(fast, reference)
        if fast is None:
            return
        fast_record = record_from_state(fast, 0, 7, 99)
        reference_record = _record_from_state_reference(reference, 0, 7, 99)
        self.assertEqual(fast_record, reference_record)
        self.assertEqual(pack_record(fast_record), pack_record(reference_record))

    def test_exhaustive_stick_raw_values(self) -> None:
        for raw in range(4096):
            with self.subTest(raw=raw):
                self.assertSameFrame(_stick_report(raw))

    def test_random_reports(self) -> None:
        for frame in _random_reports(1, 3000):
            self.assertSameFrame(frame)

    def test_imu_edge_values(self) -> None:
        edges = [0, 1, -1, 0x7FFFFF, -0x800000, 49000, -49000]
        # Offsets of the three accel and three gyro 24-bit fields in the payload.
        offsets = [8, 11, 14, 19, 22, 25]
        for value in edges:
            encoded = (value & 0xFFFFFF).to_bytes(3, "little")
            for offset in offsets:
                with self.subTest(value=value, offset=offset):
                    frame = _report()
                    frame[2 + offset : 2 + offset + 3] = encoded
                    self.assertSameFrame(bytes(frame))
            with self.subTest(value=value, offset="all"):
                frame = _report()
                for offset in offsets:
                    frame[2 + offset : 2 + offset + 3] = encoded
                self.assertSameFrame(bytes(frame))

    def test_arbitrary_states_match_reference(self) -> None:
        base = decode_frame(bytes(_report()))
        rng = random.Random(2)
        y_edges = [0.5, -0.5, 0.49999997, -0.49999997, 1.5, -1.5]
        multipliers = [1.0, 1.0 + 1e-7, 1.0 - 1e-7]
        for _ in range(20000):
            state = dataclasses.replace(
                base,
                thumbstick_x=rng.uniform(-1.2, 1.2),
                thumbstick_y=rng.choice(y_edges) / 32767 * rng.choice(multipliers),
                trackpad_x=rng.uniform(-1e-4, 1e-4),
                trackpad_y=rng.uniform(-1, 1),
                trigger=rng.uniform(0, 1),
            )
            self.assertEqual(
                record_from_state(state, 1, 3, 5),
                _record_from_state_reference(state, 1, 3, 5),
            )

    def test_explicit_pins(self) -> None:
        full = decode_frame(bytes(_stick_report(4095)))
        self.assertEqual(full.thumbstick_x, 1.0)
        self.assertEqual(record_from_state(full, 0, 0, 0).stick[0], 32767)

        zero = decode_frame(bytes(_stick_report(0)))
        self.assertEqual(zero.thumbstick_x, -1.0)
        self.assertEqual(record_from_state(zero, 0, 0, 0).stick[0], -32767)

        trackpad_off = decode_frame(bytes(_report(bytes([0, 0, 0, 0, 0, 0xFF, 0xFF]))))
        self.assertEqual(trackpad_off.trackpad_x, 0.0)
        self.assertEqual(trackpad_off.trackpad_y, 0.0)

        trigger = decode_frame(bytes(_report(bytes([0, 0, 0, 0, 0x80]))))
        self.assertEqual(record_from_state(trigger, 0, 0, 0).trigger, 0x80)

    def test_outputs_are_binary32_exact(self) -> None:
        for frame in _random_reports(3, 200) + [_stick_report(raw) for raw in range(0, 4096, 37)]:
            state = decode_frame(frame)
            for value in _float_fields(state):
                self.assertEqual(struct.unpack("<f", struct.pack("<f", value))[0], value)


if __name__ == "__main__":
    unittest.main()
