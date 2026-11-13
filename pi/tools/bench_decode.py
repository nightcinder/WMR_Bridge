#!/usr/bin/env python3
"""Micro-benchmark for the controller decoder and FF12 record build.

Times the fast path (``decode_frame``, ``record_from_state``, ``pack_record``)
on the sample capture, or on seeded random reports if the capture is missing.
Every loaded frame is also checked against the exact-rational reference; any
difference makes the script exit non-zero.

Examples::

    python3 tools/bench_decode.py
    python3 tools/bench_decode.py --count 50000 --repeat 9 --reference
    python3 tools/bench_decode.py --samples /path/to/controller_frames.txt
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wmr_bridge import decode as dec  # noqa: E402
from wmr_bridge.inputs import read_capture  # noqa: E402
from wmr_bridge.protocol import pack_record  # noqa: E402

DEFAULT_SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
REPORTS_PER_SECOND = 400
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


def _best_ns(fn, items: list, repeat: int) -> int:
    best = None
    for _ in range(repeat):
        start = time.perf_counter_ns()
        for item in items:
            fn(item)
        elapsed = time.perf_counter_ns() - start
        best = elapsed if best is None else min(best, elapsed)
    return best or 0


def _decode_record(frame: bytes):
    return dec.record_from_state(dec.decode_frame(frame), 0, 1, 0)


def _decode_record_pack(frame: bytes):
    return pack_record(dec.record_from_state(dec.decode_frame(frame), 0, 1, 0))


def _reference_record(frame: bytes):
    return dec._record_from_state_reference(dec._decode_frame_reference(frame), 0, 1, 0)


def _check_parity(frames: list[bytes]) -> list[str]:
    failures = []
    for index, frame in enumerate(frames):
        fast_state = dec.decode_frame(frame)
        ref_state = dec._decode_frame_reference(frame)
        if fast_state != ref_state:
            failures.append(f"frame {index}: decode_frame differs from reference")
            continue
        fast_bytes = pack_record(dec.record_from_state(fast_state, 0, 1, 0))
        ref_bytes = pack_record(dec._record_from_state_reference(ref_state, 0, 1, 0))
        if fast_bytes != ref_bytes:
            failures.append(f"frame {index}: FF12 record bytes differ from reference")
    return failures


def _percent_of_core(us_per_report: float) -> float:
    return us_per_report * REPORTS_PER_SECOND / 1_000_000 * 100


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--count", type=int, default=20000, help="decodes per timed run")
    parser.add_argument("--repeat", type=int, default=5, help="timed runs; best is kept")
    parser.add_argument(
        "--samples",
        type=Path,
        default=DEFAULT_SAMPLES,
        help="capture of 'label hex bytes' lines (default: samples/controller_frames.txt)",
    )
    parser.add_argument(
        "--reference",
        action="store_true",
        help="also time the Fraction reference (count // 50 decodes)",
    )
    args = parser.parse_args(argv)
    if args.count < 1 or args.repeat < 1:
        parser.error("--count and --repeat must be at least 1")

    frames, source = _load_frames(args.samples)
    print(f"frames: {len(frames)} from {source}")

    failures = _check_parity(frames)
    for line in failures[:10]:
        print(f"PARITY FAIL {line}")
    if failures:
        print(f"parity: {len(failures)} mismatch(es) against the reference")
        return 1
    print("parity: fast path matches reference on all loaded frames")

    items = [frames[i % len(frames)] for i in range(args.count)]

    stages = [
        ("decode", dec.decode_frame),
        ("decode+record", _decode_record),
        ("decode+record+pack", _decode_record_pack),
    ]
    results = {}
    for label, fn in stages:
        ns = _best_ns(fn, items, args.repeat)
        results[label] = ns / args.count / 1000
    for label, _fn in stages:
        us = results[label]
        print(f"{label}: {us:.2f} us/report")
    print(
        "% of one core at 400 reports/s: "
        + ", ".join(f"{label} {_percent_of_core(results[label]):.3f}%" for label, _ in stages)
    )

    if args.reference:
        ref_count = max(1, args.count // 50)
        ref_items = [frames[i % len(frames)] for i in range(ref_count)]
        ref_decode = _best_ns(dec._decode_frame_reference, ref_items, args.repeat) / ref_count / 1000
        ref_record = _best_ns(_reference_record, ref_items, args.repeat) / ref_count / 1000
        print(f"reference decode: {ref_decode:.2f} us/report ({ref_count} decodes)")
        print(f"reference decode+record: {ref_record:.2f} us/report ({ref_count} decodes)")
        if results["decode"] > 0:
            print(f"speed-up, decode: {ref_decode / results['decode']:.1f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
