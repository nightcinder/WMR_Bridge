#!/usr/bin/env python3
"""Benchmark ``Bridge.ingest`` with an unsubscribed BLE GATT sink.

ONY-1459 gates the FF12/FF14 record build and the HID sample path behind
their CCCDs. The legacy sink below reproduces the pre-gating ``deliver()``:
it still re-decodes every frame for the HID gamepad while no central has
subscribed. Run the same 3600 frames (9 s at 400 Hz) through both and exit
non-zero unless the gated path is at least ``--threshold`` faster.

Examples::

    python3 tools/bench_ingest.py
    python3 tools/bench_ingest.py --frames 3600 --repeat 7 --threshold 0.20
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wmr_bridge import decode as dec  # noqa: E402
from wmr_bridge.ble_gatt import (  # noqa: E402
    FF11_RAW,
    FF12_DECODED,
    FF14_BATCHED,
    GattApplication,
)
from wmr_bridge.bridge import Bridge  # noqa: E402
from wmr_bridge.inputs import read_capture  # noqa: E402
from wmr_bridge.protocol import Frame, encode_raw_messages, pack_record  # noqa: E402

DEFAULT_SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
DEFAULT_FRAMES = 3600
RANDOM_FALLBACK_COUNT = 256


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


def _bench(frames: list[bytes], repeat: int, legacy: bool) -> int:
    best = None
    for _ in range(repeat):
        bridge = Bridge()
        app = _LegacySink(bridge, hosts=[]) if legacy else GattApplication(bridge, hosts=[])
        bridge.add_sink(app)
        # A real bridge streams while the controller is connected; that is the
        # state where the old HID path decoded every frame a second time.
        bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        start = time.perf_counter_ns()
        for data in frames:
            bridge.ingest(0, data)
        elapsed = time.perf_counter_ns() - start
        best = elapsed if best is None else min(best, elapsed)
    return best or 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--frames", type=int, default=DEFAULT_FRAMES, help="ingested frames per run"
    )
    parser.add_argument("--repeat", type=int, default=5, help="timed runs; best is kept")
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

    legacy_ns = _bench(dataset, args.repeat, True)
    gated_ns = _bench(dataset, args.repeat, False)
    drop = 1.0 - gated_ns / legacy_ns if legacy_ns else 0.0
    print(f"legacy: {legacy_ns / 1e6:.3f} ms  gated: {gated_ns / 1e6:.3f} ms")
    print(f"drop: {drop * 100:.1f}% (threshold {args.threshold * 100:.0f}%)")
    if drop < args.threshold:
        print("FAIL: gated ingest is not fast enough", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
