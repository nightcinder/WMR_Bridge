"""Host tests for the FF11/FF12/FF14 payloads, pinned to the C reference."""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from wmr_bridge.protocol import (
    BATCH_VERSION,
    Frame,
    ProtocolError,
    Reassembler,
    DecodedRecord,
    encode_batch,
    encode_fragment,
    encode_raw_messages,
    pack_record,
    parse_batch,
    unpack_record,
)

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"

# Produced by compiling src/wmr_decode.c + src/wmr_decoded.c and running each
# sample frame through wmr_decoded_from_frame(hand=0, sequence=1,
# rx_time_us=123456). These pin bit-exact parity with the firmware.
C_EXPECTED = {
    "sessionA-0": bytes.fromhex(
        "01 00 00 00 01 00 00 00 40 e2 01 00 00 00 00 00 "
        "ca 04 57 03 c5 fe e2 3e 7c ed 49 3f c9 37 db bc "
        "be b9 34 3e 6f c2 f4 40 a8 17 e6 40 81 05 40 00 "
        "21 05 00 00 00 00 00 9c"
    ),
    "sessionB-2": bytes.fromhex(
        "01 00 00 00 01 00 00 00 40 e2 01 00 00 00 00 00 "
        "4a 77 3e 7e e5 7e 87 3c b6 f3 7d 3c bf cf b8 ba "
        "ff c8 07 c1 89 4e 91 40 63 ce 3e 40 d5 0b 40 00 "
        "61 05 00 00 00 00 00 83"
    ),
}

DECODED_GOLDEN = bytes.fromhex(
    "01 01 00 06 04 03 02 01 88 77 66 55 44 33 22 11 "
    "dd cc bb aa 00 00 80 3f 00 00 00 c0 00 00 00 3f "
    "00 00 80 40 00 00 00 c1 00 00 00 00 fe ff ff 7f "
    "01 80 00 00 00 40 ff 7b"
)


def make_frame(hand: int = 1, sequence: int = 0x01020304) -> Frame:
    data = bytes([0xA1, 0x01]) + bytes((i * 7 + hand) & 0xFF for i in range(2, 46))
    return Frame(hand=hand, sequence=sequence, data=data, rx_time_us=123456)


class RawStreamTests(unittest.TestCase):
    def test_golden_fragment(self) -> None:
        frame = make_frame()
        first = encode_fragment(frame, 0, 44)
        self.assertEqual(
            first[:12],
            bytes.fromhex("52 01 04 03 02 01 2e 00 00 00 20 00"),
        )
        self.assertEqual(len(first), 44)
        self.assertEqual(first[12:], frame.data[:32])

        second = encode_fragment(frame, 32, 44)
        self.assertEqual(
            second[:12],
            bytes.fromhex("52 01 04 03 02 01 2e 00 20 00 0e 00"),
        )
        self.assertEqual(len(second), 26)
        self.assertEqual(second[12:], frame.data[32:])

    def test_default_capacity_is_one_fragment(self) -> None:
        frame = make_frame(hand=0)
        messages = encode_raw_messages(frame)
        self.assertEqual(len(messages), 1)
        self.assertEqual(len(messages[0]), 12 + 46)
        self.assertEqual(messages[0][12:], frame.data)

    def test_invalid_arguments(self) -> None:
        frame = make_frame()
        self.assertEqual(encode_fragment(frame, 0, 12), b"")
        self.assertEqual(encode_fragment(frame, 46, 44), b"")
        self.assertEqual(encode_fragment(frame, 100, 44), b"")

    def test_round_trip_every_capacity(self) -> None:
        for capacity in range(13, 245):
            for hand in (0, 1):
                with self.subTest(capacity=capacity, hand=hand):
                    frame = make_frame(hand=hand, sequence=1000 + capacity)
                    reassembler = Reassembler()
                    out = None
                    for message in encode_raw_messages(frame, capacity):
                        out = reassembler.push(message)
                    self.assertIsNotNone(out)
                    self.assertEqual(out.hand, hand)
                    self.assertEqual(out.sequence, frame.sequence)
                    self.assertEqual(out.data, frame.data)
                    self.assertEqual(reassembler.errors, 0)
                    self.assertEqual(reassembler.frames_incomplete[hand], 0)

    def test_reassembler_abandons_partial_frame(self) -> None:
        reassembler = Reassembler()
        frame = make_frame()
        packets = encode_raw_messages(frame, 20)
        self.assertIsNone(reassembler.push(packets[0]))
        # A new frame starting at offset 0 abandons the first one.
        other = make_frame(sequence=99)
        out = None
        for packet in encode_raw_messages(other, 20):
            out = reassembler.push(packet)
        self.assertIsNotNone(out)
        self.assertEqual(out.sequence, 99)
        self.assertEqual(reassembler.frames_incomplete[frame.hand], 1)

    def test_reassembler_rejects_malformed(self) -> None:
        reassembler = Reassembler()
        self.assertIsNone(reassembler.push(b"\x00" * 12))
        self.assertEqual(reassembler.errors, 1)
        bad = bytearray(encode_fragment(make_frame(), 0, 44))
        bad[1] = 2
        self.assertIsNone(reassembler.push(bytes(bad)))
        self.assertEqual(reassembler.errors, 2)

    def test_frame_validation(self) -> None:
        with self.assertRaises(ProtocolError):
            Frame(hand=2, sequence=1, data=b"x")
        with self.assertRaises(ProtocolError):
            Frame(hand=0, sequence=1, data=b"")

    def test_psvr2_79_byte_frame(self) -> None:
        data = bytes([0x31]) + bytes((i * 3) & 0xFF for i in range(1, 79))
        reassembler = Reassembler()
        for capacity in (44, 244):
            with self.subTest(capacity=capacity):
                frame = Frame(hand=1, sequence=42, data=data)
                out = None
                for message in encode_raw_messages(frame, capacity):
                    out = reassembler.push(message)
                self.assertIsNotNone(out)
                self.assertEqual(out.data, data)
                self.assertEqual(reassembler.errors, 0)


class DecodedRecordTests(unittest.TestCase):
    def test_pack_matches_golden(self) -> None:
        record = DecodedRecord(
            hand=1,
            sequence=0x01020304,
            rx_time_us=0x1122334455667788,
            device_ticks=0xAABBCCDD,
            gyro=(1.0, -2.0, 0.5),
            accel=(4.0, -8.0, 0.0),
            temperature_raw=-2,
            stick=(32767, -32767),
            trackpad=(0, 16384),
            trigger=0xFF,
            battery=0x7B,
            buttons=0x06,
            flags=0,
        )
        self.assertEqual(pack_record(record), DECODED_GOLDEN)
        parsed = unpack_record(DECODED_GOLDEN)
        self.assertEqual(parsed.hand, 1)
        self.assertEqual(parsed.buttons, 0x06)
        self.assertEqual(parsed.sequence, 0x01020304)
        self.assertEqual(parsed.rx_time_us, 0x1122334455667788)
        self.assertEqual(parsed.device_ticks, 0xAABBCCDD)
        self.assertEqual(parsed.gyro, (1.0, -2.0, 0.5))
        self.assertEqual(parsed.accel, (4.0, -8.0, 0.0))
        self.assertEqual(parsed.temperature_raw, -2)
        self.assertEqual(parsed.stick, (32767, -32767))
        self.assertEqual(parsed.trackpad, (0, 16384))
        self.assertEqual((parsed.trigger, parsed.battery), (0xFF, 0x7B))

    def test_unpack_rejects(self) -> None:
        for bad in (
            DECODED_GOLDEN[:-1],
            DECODED_GOLDEN + b"\x00",
            bytes((2,)) + DECODED_GOLDEN[1:],
            DECODED_GOLDEN[:1] + bytes((2,)) + DECODED_GOLDEN[2:],
        ):
            with self.subTest(bad=bad[:8].hex()):
                with self.assertRaises(ProtocolError):
                    unpack_record(bad)


class BatchTests(unittest.TestCase):
    def records(self, count: int) -> list[bytes]:
        return [bytes((index,)) + bytes(55) for index in range(count)]

    def test_batch_layout(self) -> None:
        records = self.records(2)
        packet = encode_batch(records)
        self.assertEqual(packet[:2], bytes((BATCH_VERSION, 2)))
        self.assertEqual(len(packet), 2 + 2 * 56)
        self.assertEqual(parse_batch(packet), records)

    def test_batch_limits(self) -> None:
        for count in (1, 2, 9):
            with self.subTest(count=count):
                packet = encode_batch(self.records(count))
                self.assertEqual(len(parse_batch(packet)), count)
        with self.assertRaises(ProtocolError):
            encode_batch([])
        with self.assertRaises(ProtocolError):
            encode_batch(self.records(10))
        with self.assertRaises(ProtocolError):
            encode_batch([b"too short"])

    def test_parse_rejects(self) -> None:
        packet = bytearray(encode_batch(self.records(2)))
        with self.assertRaises(ProtocolError):
            parse_batch(bytes(packet[:-1]))
        with self.assertRaises(ProtocolError):
            parse_batch(bytes((3, 2)) + bytes(packet[2:]))
        with self.assertRaises(ProtocolError):
            parse_batch(bytes((BATCH_VERSION, 0)) + bytes(56))


class SampleParityTests(unittest.TestCase):
    """The Python decoder must produce the C reference's exact bytes."""

    @classmethod
    def setUpClass(cls) -> None:
        from wmr_bridge.inputs import read_capture

        if not SAMPLES.exists():
            raise unittest.SkipTest("samples/controller_frames.txt is missing")
        cls.entries = dict(read_capture(SAMPLES))

    def test_sample_count(self) -> None:
        self.assertEqual(len(self.entries), len(C_EXPECTED) + 6)

    def test_c_parity(self) -> None:
        from wmr_bridge.decode import record_from_frame

        for label, expected in C_EXPECTED.items():
            with self.subTest(label=label):
                frame = Frame(
                    hand=0,
                    sequence=1,
                    data=self.entries[label],
                    rx_time_us=123456,
                )
                record = record_from_frame(frame)
                self.assertIsNotNone(record)
                self.assertEqual(pack_record(record), expected)


if __name__ == "__main__":
    unittest.main()
