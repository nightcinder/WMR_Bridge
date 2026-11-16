#!/usr/bin/env python3
"""Benchmark the 400 Hz WebSocket transport: ingest fan-out plus ``_send_frame``.

ONY-1473 gave ``WebSocket._send_frame`` a fast path: when the transport reports
no queued write bytes the frame cannot block, so the send skips the per-frame
``asyncio.wait_for(drain(), write_timeout)`` timer. This tool drives the real
bridge server: a client subscribes to the ``raw`` stream, 400 Hz frames are
ingested through ``Bridge.ingest`` and the client reads every message. Legacy
mode restores the pre-change ``_send_frame`` and both modes run in the same
process, so the comparison is unaffected by machine noise.

Fast-path and baseline use the same pacing (one event-loop turn per frame, no
wall-clock sleeps) and each run builds a fresh bridge and connection, so the
reported figure is CPU time per frame. Exit status is non-zero unless the
median of ``--repeat`` runs is at least ``--threshold`` faster than the baseline.

Examples::

    python3 tools/bench_ws.py
    python3 tools/bench_ws.py --frames 4000 --repeat 7 --threshold 0.15
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wmr_bridge import decode as dec  # noqa: E402
from wmr_bridge import ws as wsmod  # noqa: E402
from wmr_bridge.bridge import Bridge  # noqa: E402
from wmr_bridge.inputs import read_capture  # noqa: E402
from wmr_bridge.server import BridgeServer  # noqa: E402

DEFAULT_SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
DEFAULT_FRAMES = 1600
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


async def _legacy_send_frame(
    self: wsmod.WebSocket, opcode: int, payload: bytes = b"", fin: bool = True
) -> None:
    """The pre-ONY-1473 send: every frame arms and cancels a write timeout."""

    if self._closed:
        raise wsmod.WebSocketClosed()
    first = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = bytes((first, length))
    elif length <= 0xFFFF:
        header = bytes((first, 126)) + length.to_bytes(2, "big")
    else:
        header = bytes((first, 127)) + length.to_bytes(8, "big")
    if self.is_client:
        mask = os.urandom(4)
        header = bytes((header[0], header[1] | 0x80)) + header[2:] + mask
        payload = bytes(byte ^ mask[index & 3] for index, byte in enumerate(payload))
    async with self._send_lock:
        self.writer.write(header + payload)
        try:
            if self.write_timeout is None:
                await self.writer.drain()
            else:
                await asyncio.wait_for(self.writer.drain(), self.write_timeout)
        except asyncio.TimeoutError as exc:
            self._closed = True
            self.writer.close()
            raise wsmod.WebSocketClosed(wsmod.CLOSE_GOING_AWAY, "write timed out") from exc


async def _run_pipeline(frames: list[bytes], *, legacy: bool) -> tuple[int, int]:
    """Stream ``frames`` to one raw subscriber; return (CPU ns, wall ns)."""

    bridge = Bridge()
    bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
    server = BridgeServer(bridge, host="127.0.0.1", port=0)
    original: Any = wsmod.WebSocket._send_frame
    if legacy:
        wsmod.WebSocket._send_frame = _legacy_send_frame
    try:
        await server.start()
        client = await wsmod.connect("127.0.0.1", server.port, "/wmr", timeout=5)
        try:
            await client.recv_json()  # hello
            await client.send_json({"type": "subscribe", "stream": "raw"})
            reply = await client.recv_json()
            if reply.get("type") != "subscribed":
                raise RuntimeError(f"subscribe failed: {reply!r}")
            received = 0
            done = asyncio.Event()

            async def read_all() -> None:
                nonlocal received
                while received < len(frames):
                    await client.recv_binary()
                    received += 1
                done.set()

            reader = asyncio.create_task(read_all())
            await asyncio.sleep(0)
            cpu_ns = time.process_time_ns()
            wall_ns = time.perf_counter_ns()
            for data in frames:
                bridge.ingest(0, data)
                # One loop turn per frame, like a 400 Hz BLE callback: the
                # sender and the reader run between ingests.
                await asyncio.sleep(0)
            await asyncio.wait_for(done.wait(), 30)
            wall_ns = time.perf_counter_ns() - wall_ns
            cpu_ns = time.process_time_ns() - cpu_ns
            dropped = sum(subscriber.dropped for subscriber in bridge.subscribers)
            if dropped:
                raise RuntimeError(f"dropped {dropped} frames; the run is not comparable")
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        finally:
            await client.close()
    finally:
        wsmod.WebSocket._send_frame = original
        await server.close()
    return cpu_ns, wall_ns


async def _bench(frames: list[bytes], repeat: int, *, legacy: bool) -> tuple[float, float]:
    cpu: list[float] = []
    wall: list[float] = []
    for _ in range(repeat):
        cpu_ns, wall_ns = await _run_pipeline(frames, legacy=legacy)
        cpu.append(cpu_ns / len(frames) / 1000.0)
        wall.append(wall_ns / len(frames) / 1000.0)
    return statistics.median(cpu), statistics.median(wall)


async def _main(args: argparse.Namespace) -> int:
    source_frames, source = _load_frames(args.samples)
    dataset = [source_frames[i % len(source_frames)] for i in range(args.frames)]
    print(f"frames: {args.frames} from {len(source_frames)} captured, {source}")

    baseline_cpu, baseline_wall = await _bench(dataset, args.repeat, legacy=True)
    fast_cpu, fast_wall = await _bench(dataset, args.repeat, legacy=False)
    drop = 1.0 - fast_cpu / baseline_cpu if baseline_cpu else 0.0
    print(
        f"baseline: {baseline_cpu:.3f} us/frame CPU ({baseline_wall:.3f} wall)  "
        f"fast: {fast_cpu:.3f} us/frame CPU ({fast_wall:.3f} wall)"
    )
    print(
        f"drop: {drop * 100:.1f}% median CPU over {args.repeat} runs "
        f"(threshold {args.threshold * 100:.0f}%)"
    )
    if drop < args.threshold:
        print("FAIL: the fast send path is not fast enough", file=sys.stderr)
        return 1
    print("PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--frames", type=int, default=DEFAULT_FRAMES, help="ingested frames per run"
    )
    parser.add_argument("--repeat", type=int, default=5, help="timed runs; the median is kept")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.10,
        help="required median CPU drop for the fast path (default 0.10)",
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
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
