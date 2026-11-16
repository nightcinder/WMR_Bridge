#!/usr/bin/env python3
"""Benchmark ``Bridge.ingest`` with unsubscribed and subscribed BLE GATT sinks.

ONY-1459 gates the FF12/FF14 record build and the HID sample path behind
their CCCDs. The legacy sink below reproduces the pre-gating ``deliver()``:
it still re-decodes every frame for the HID gamepad while no central has
subscribed. Run the same 3600 frames (9 s at 400 Hz) through both and exit
non-zero unless the gated path is at least ``--threshold`` faster.

The subscribed stage enables the HID report CCCD and advances a simulated
monotonic clock at the capture's 400 Hz, so the 10 ms HID pacing floor and its
D-Bus notification count are exercised at the rate a phone would see. Every
stage reports the median elapsed time and the median is what the pass/fail
check uses.

Examples::

    python3 tools/bench_ingest.py
    python3 tools/bench_ingest.py --frames 3600 --repeat 7 --threshold 0.20
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wmr_bridge import decode as dec  # noqa: E402
from wmr_bridge.ble_gatt import (  # noqa: E402
    FF11_RAW,
    FF12_DECODED,
    FF14_BATCHED,
    HID_REPORT,
    GattApplication,
)
from wmr_bridge.bridge import Bridge  # noqa: E402
from wmr_bridge.inputs import read_capture  # noqa: E402
from wmr_bridge.protocol import Frame, encode_raw_messages, pack_record  # noqa: E402

DEFAULT_SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
DEFAULT_FRAMES = 3600
RANDOM_FALLBACK_COUNT = 256
REPORTS_PER_SECOND = 400
FRAME_PERIOD_S = 1.0 / REPORTS_PER_SECOND


def _load_frames(path: Path) -> tuple[list[bytes], str]:
    frames: list[bytes] = []
    if path.is_file():
        for _label, data in read_capture(path):
            if dec.decode_frame(data) is not None:
                frames.append(data)
        if frames:
            return frames, str(path)
    rng = random.Random(1)
    for _ in range(RANDOM_FALLBACK_COUNT):
        frame = bytearray(rng.randbytes(dec.FRAME_LENGTH))
        frame[0] = 0xA1
        frame[1] = dec.REPORT_ID
        frames.append(bytes(frame))
    return frames, f"seeded random ({RANDOM_FALLBACK_COUNT} frames; {path} not usable)"


class _LegacySink(GattApplication):
    """The pre-ONY-1459 deliver(): every frame reached the HID path."""

    @property
    def wants_frames(self) -> bool:
        # Before the gate, Bridge.ingest handed every frame to every sink.
        return True

    def deliver(self, frame: Frame, record=None) -> None:  # type: ignore[override]
        if self.is_notifying(FF11_RAW):
            for fragment in encode_raw_messages(frame, self.fragment_size):
                self._notify(FF11_RAW, fragment)
        if record is not None:
            packed = pack_record(record)
            if self.is_notifying(FF12_DECODED):
                if len(packed) <= self.notification_limit:
                    self._notify(FF12_DECODED, packed)
                else:
                    self.dropped_records += 1
            if self.is_notifying(FF14_BATCHED):
                if self.batch_max_count:
                    self._queue_batch(packed)
                else:
                    self.dropped_records += 1
        self._deliver_hid(frame, record)


def _bench(
    frames: list[bytes], repeat: int, legacy: bool, subscribed_hid: bool = False
) -> tuple[int, list[int]]:
    """Return the median elapsed ns and the per-run HID notification counts."""

    samples: list[int] = []
    notifications: list[int] = []
    for _ in range(repeat):
        # A phone subscribed at the capture's 400 Hz: advance the clock with the
        # frames so the HID pacing floor sees the same time as a live stream.
        clock_now = [0.0]

        def clock() -> float:
            return clock_now[0]

        bridge = Bridge()
        app = _LegacySink(bridge, hosts=[]) if legacy else GattApplication(
            bridge, hosts=[], clock=clock
        )
        bridge.add_sink(app)
        sent = [0]
        if subscribed_hid:
            app.set_emitter(lambda _uuid, _value: sent.__setitem__(0, sent[0] + 1))
            app.start_notify(HID_REPORT)
        # A real bridge streams while the controller is connected; that is the
        # state where the old HID path decoded every frame a second time.
        bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        start = time.perf_counter_ns()
        for data in frames:
            bridge.ingest(0, data)
            clock_now[0] += FRAME_PERIOD_S
        elapsed = time.perf_counter_ns() - start
        samples.append(elapsed)
        notifications.append(sent[0])
    return int(statistics.median(samples)), notifications


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--frames", type=int, default=DEFAULT_FRAMES, help="ingested frames per run"
    )
    parser.add_argument("--repeat", type=int, default=5, help="timed runs; median is kept")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.20,
        help="required elapsed-time drop for the gated path (default 0.20)",
    )
    parser.add_argument(
        "--samples",
        type=Path,
        default=DEFAULT_SAMPLES,
        help="capture of 'label hex bytes' lines (default: samples/controller_frames.txt)",
    )
    args = parser.parse_args(argv)
    if args.frames < 1 or args.repeat < 1 or not 0.0 <= args.threshold < 1.0:
        parser.error("--frames and --repeat must be at least 1; --threshold in [0, 1)")

    source_frames, source = _load_frames(args.samples)
    dataset = [source_frames[i % len(source_frames)] for i in range(args.frames)]
    print(f"frames: {args.frames} from {len(source_frames)} captured, {source}")

    legacy_ns, _ = _bench(dataset, args.repeat, True)
    gated_ns, _ = _bench(dataset, args.repeat, False)
    hid_ns, hid_counts = _bench(dataset, args.repeat, False, subscribed_hid=True)
    drop = 1.0 - gated_ns / legacy_ns if legacy_ns else 0.0
    hid_rate = statistics.median(hid_counts) / (args.frames * FRAME_PERIOD_S)
    print(f"legacy: {legacy_ns / 1e6:.3f} ms  gated: {gated_ns / 1e6:.3f} ms")
    print(f"drop: {drop * 100:.1f}% (threshold {args.threshold * 100:.0f}%)")
    print(
        f"subscribed HID: {hid_ns / 1e6:.3f} ms  "
        f"{hid_ns / args.frames / 1000:.2f} us/frame  "
        f"{statistics.median(hid_counts)} notifications ({hid_rate:.1f} Hz)"
    )
    if drop < args.threshold:
        print("FAIL: gated ingest is not fast enough", file=sys.stderr)
        return 1
    if statistics.median(hid_counts) == 0:
        print("FAIL: subscribed HID emitted no notifications", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
