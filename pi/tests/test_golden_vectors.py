"""Shared golden vectors: the Pi decoder and packer against wmr_golden_v1.json.

The same file is committed in the companion app
(``packages/wmr_core/test/fixtures/wmr_golden_v1.json``) and read there by
``golden_vectors_test.dart``, so both sides are pinned to identical bytes. See
``tests/vectors/README.md`` for the fields. Standard library only.
"""

from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path

from tools.wifi_probe import DevLossEstimator
from wmr_bridge.ble_gatt import FF15_ECHO, GattApplication
from wmr_bridge.bridge import Bridge
from wmr_bridge.decode import decode_frame, extend_ticks, record_from_frame, record_from_state
from wmr_bridge.protocol import (
    BATCH_VERSION,
    DECODED_SIZE,
    Frame,
    ProtocolError,
    Reassembler,
    encode_batch,
    encode_raw_messages,
    pack_record,
    parse_batch,
    unpack_record,
)

VECTORS = Path(__file__).resolve().parents[2] / "tests" / "vectors" / "wmr_golden_v1.json"
GOLDEN = json.loads(VECTORS.read_text(encoding="utf-8"))
FRAMES = GOLDEN["ff11_frames"]
ECHO_ANSWER = struct.Struct("<BBHQIHH")


def _f32_hex(value: float) -> str:
    return struct.pack("<f", value).hex()


class GoldenFileTests(unittest.TestCase):
    def test_header_and_coverage(self) -> None:
        self.assertEqual(GOLDEN["format"], "wmr-golden")
        self.assertEqual(GOLDEN["version"], 1)
        self.assertGreaterEqual(len(FRAMES), 12)
        names = [case["name"] for case in FRAMES]
        self.assertEqual(len(names), len(set(names)))


class GoldenFrameTests(unittest.TestCase):
    def test_decode_frame_matches_expected_state(self) -> None:
        for case in FRAMES:
            with self.subTest(case=case["name"]):
                frame = bytes.fromhex(case["frame_hex"])
                self.assertEqual(len(frame), 46)
                state = decode_frame(frame)
                self.assertIsNotNone(state)
                want = case["state"]
                self.assertEqual(state.buttons, want["buttons"])
                # Floats are exact binary32 values, so equality is exact.
                self.assertEqual(state.thumbstick_x, want["thumbstick_x"])
                self.assertEqual(state.thumbstick_y, want["thumbstick_y"])
                self.assertEqual(state.trigger, want["trigger"])
                self.assertEqual(state.trackpad_x, want["trackpad_x"])
                self.assertEqual(state.trackpad_y, want["trackpad_y"])
                self.assertEqual(state.battery, want["battery"])
                self.assertEqual(list(state.accel_raw), want["accel_raw"])
                self.assertEqual(list(state.accel), want["accel"])
                self.assertEqual(state.temperature_raw, want["temperature_raw"])
                self.assertEqual(list(state.gyro_raw), want["gyro_raw"])
                self.assertEqual(list(state.gyro), want["gyro"])
                self.assertEqual(state.ticks, want["ticks"])

    def test_record_bytes_match(self) -> None:
        for case in FRAMES:
            with self.subTest(case=case["name"]):
                state = decode_frame(bytes.fromhex(case["frame_hex"]))
                record = record_from_state(
                    state, case["hand"], case["sequence"], case["rx_time_us"]
                )
                wire = pack_record(record)
                self.assertEqual(len(wire), DECODED_SIZE)
                self.assertEqual(wire.hex(), case["record_hex"])

    def test_record_from_frame_matches(self) -> None:
        for case in FRAMES:
            with self.subTest(case=case["name"]):
                frame = Frame(
                    hand=case["hand"],
                    sequence=case["sequence"],
                    data=bytes.fromhex(case["frame_hex"]),
                    rx_time_us=case["rx_time_us"],
                )
                record = record_from_frame(frame)
                self.assertIsNotNone(record)
                self.assertEqual(pack_record(record).hex(), case["record_hex"])

    def test_unpack_record_matches_expected_fields(self) -> None:
        for case in FRAMES:
            with self.subTest(case=case["name"]):
                wire = bytes.fromhex(case["record_hex"])
                want = case["record"]
                record = unpack_record(wire)
                self.assertEqual(wire[0], want["version"])
                self.assertEqual(record.hand, want["hand"])
                self.assertEqual(record.flags, want["flags"])
                self.assertEqual(record.buttons, want["buttons"])
                self.assertEqual(record.sequence, want["sequence"])
                self.assertEqual(record.rx_time_us, want["rx_time_us"])
                self.assertEqual(record.device_ticks, want["device_ticks"])
                self.assertEqual(list(record.gyro), want["gyro"])
                self.assertEqual(list(record.accel), want["accel"])
                self.assertEqual([_f32_hex(v) for v in record.gyro], want["gyro_f32_hex"])
                self.assertEqual([_f32_hex(v) for v in record.accel], want["accel_f32_hex"])
                self.assertEqual(record.temperature_raw, want["temperature_raw"])
                self.assertEqual(list(record.stick), want["stick"])
                self.assertEqual(list(record.trackpad), want["trackpad"])
                self.assertEqual(record.trigger, want["trigger"])
                self.assertEqual(record.battery, want["battery"])
                self.assertEqual(pack_record(record), wire)

    def test_invalid_frames_are_refused(self) -> None:
        for case in GOLDEN["ff11_invalid_frames"]:
            with self.subTest(case=case["name"]):
                self.assertIsNone(decode_frame(bytes.fromhex(case["frame_hex"])))

    def test_invalid_records_are_refused(self) -> None:
        for case in GOLDEN["ff12_invalid_records"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(ProtocolError):
                    unpack_record(bytes.fromhex(case["hex"]))


class GoldenFragmentTests(unittest.TestCase):
    def test_fragments_encode_and_reassemble(self) -> None:
        spec = GOLDEN["ff11_fragments"]
        case = FRAMES[spec["frame_index"]]
        frame = Frame(
            hand=case["hand"], sequence=case["sequence"], data=bytes.fromhex(case["frame_hex"])
        )
        for key, capacity in (("capacity_244", 244), ("capacity_44", 44)):
            with self.subTest(capacity=capacity):
                messages = encode_raw_messages(frame, capacity)
                self.assertEqual([m.hex() for m in messages], spec[key])
                reassembler = Reassembler()
                results = [reassembler.push(bytes.fromhex(m)) for m in spec[key]]
                self.assertTrue(all(r is None for r in results[:-1]))
                self.assertEqual(results[-1].data, frame.data)
                self.assertEqual(results[-1].hand, frame.hand)
                self.assertEqual(results[-1].sequence, frame.sequence)
                self.assertEqual(reassembler.errors, 0)


class GoldenBatchTests(unittest.TestCase):
    def test_batch_encodes_and_parses(self) -> None:
        spec = GOLDEN["ff14_batch"]
        records = [bytes.fromhex(FRAMES[i]["record_hex"]) for i in spec["record_indices"]]
        batch = encode_batch(records)
        self.assertEqual(batch.hex(), spec["batch_hex"])
        self.assertEqual(batch[0], BATCH_VERSION)
        self.assertEqual(batch[0], spec["version"])
        self.assertEqual(batch[1], spec["count"])
        self.assertEqual(len(batch), spec["length"])
        self.assertEqual(parse_batch(bytes.fromhex(spec["batch_hex"])), records)
        hands = [unpack_record(r).hand for r in parse_batch(batch)]
        self.assertEqual(hands, [FRAMES[i]["hand"] for i in spec["record_indices"]])

    def test_invalid_batches_are_refused(self) -> None:
        for case in GOLDEN["ff14_batch"]["invalid"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(ProtocolError):
                    parse_batch(bytes.fromhex(case["hex"]))


class GoldenEchoTests(unittest.TestCase):
    def test_layout_of_every_example(self) -> None:
        spec = GOLDEN["ff15_echo"]
        for case in spec["examples"]:
            with self.subTest(case=case["name"]):
                request = bytes.fromhex(case["request_hex"])
                answer = bytes.fromhex(case["answer_hex"])
                self.assertEqual(len(request), spec["request_size"])
                self.assertEqual(len(answer), spec["answer_size"])
                self.assertEqual(request, struct.pack("<BBH", 1, 0, case["ping_id"]))
                version, reserved, ping_id, rx, turnaround, depth, tail = ECHO_ANSWER.unpack(answer)
                self.assertEqual((version, reserved, tail), (1, 0, 0))
                self.assertEqual(ping_id, case["ping_id"])
                self.assertEqual(rx, case["rx_time_us"])
                self.assertEqual(turnaround, case["turnaround_us"])
                self.assertEqual(depth, case["queue_depth"])

    def test_pi_handler_produces_the_pi_example(self) -> None:
        case = next(c for c in GOLDEN["ff15_echo"]["examples"] if c["name"] == "pi_zero_fields")
        bridge = Bridge()
        bridge.now_us = lambda: case["rx_time_us"]  # type: ignore[method-assign]
        application = GattApplication(bridge, clock=lambda: 100.0)
        sent: list[tuple[str, bytes]] = []
        application._notify = lambda uuid, value: sent.append((uuid, bytes(value)))  # type: ignore[method-assign]
        application.start_notify(FF15_ECHO)
        self.assertEqual(application.write_value(FF15_ECHO, bytes.fromhex(case["request_hex"])), 0)
        answers = [value for uuid, value in sent if uuid == FF15_ECHO]
        self.assertEqual([a.hex() for a in answers], [case["answer_hex"]])


def _classify(samples: list[dict], tolerance_us: int) -> list[tuple[str, int]]:
    """PROTOCOL.md "Timing": modular tick dt and the 100 ms discontinuity rule."""

    out: list[tuple[str, int]] = []
    previous: dict | None = None
    for sample in samples:
        if previous is None:
            out.append(("baseline", 0))
        else:
            dt_ticks = (sample["device_ticks"] - previous["device_ticks"]) & 0xFFFFFFFF
            rx_ns = (sample["rx_time_us"] - previous["rx_time_us"]) * 1000
            if rx_ns < 0 or abs(dt_ticks * 100 - rx_ns) > tolerance_us * 1000:
                out.append(("discontinuity", 0))
            else:
                out.append(("step", dt_ticks))
        previous = sample
    return out


class GoldenTimingTests(unittest.TestCase):
    def test_step_kinds_and_dt(self) -> None:
        timing = GOLDEN["timing"]
        for sequence in timing["sequences"]:
            with self.subTest(sequence=sequence["name"]):
                got = _classify(sequence["samples"], timing["discontinuity_tolerance_us"])
                want = [(s["kind"], s["dt_ticks"]) for s in sequence["expected_steps"]]
                self.assertEqual(got, want)
                for step in sequence["expected_steps"]:
                    self.assertEqual(step["dt_us"], step["dt_ticks"] * timing["tick_ns"] / 1000)
                totals = sequence["expected_totals"]
                self.assertEqual(sum(k == "step" for k, _ in got), totals["steps"])
                self.assertEqual(
                    sum(k == "discontinuity" for k, _ in got), totals["discontinuities"]
                )

    def test_extend_ticks_matches_unwrapped_values(self) -> None:
        checked = 0
        for sequence in GOLDEN["timing"]["sequences"]:
            if "expected_ticks64" not in sequence:
                continue
            with self.subTest(sequence=sequence["name"]):
                ticks = [s["device_ticks"] for s in sequence["samples"]]
                acc = ticks[0]
                got = [acc]
                for value in ticks[1:]:
                    acc = extend_ticks(acc, value)
                    got.append(acc)
                self.assertEqual(got, sequence["expected_ticks64"])
                checked += 1
        self.assertGreater(checked, 0)

    def test_missing_count_matches_devloss_estimator(self) -> None:
        # Sequences without an rx-time discontinuity follow src/wmr_devloss.c
        # exactly; the Pi probe carries an independent port of that file.
        checked = 0
        for sequence in GOLDEN["timing"]["sequences"]:
            totals = sequence["expected_totals"]
            if totals["discontinuities"]:
                continue
            with self.subTest(sequence=sequence["name"]):
                estimator = DevLossEstimator()
                for sample in sequence["samples"]:
                    estimator.add(sample["device_ticks"])
                self.assertEqual(estimator.missing, totals["missing"])
                self.assertEqual(estimator.period_ticks or None, totals["period_ticks"])
                self.assertEqual(estimator.discontinuities, totals["long_gaps"])
                checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
