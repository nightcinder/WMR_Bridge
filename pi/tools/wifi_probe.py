#!/usr/bin/env python3
"""Bench client for the Wi-Fi bridge.

Connects to ``ws://host:8765/wmr``, subscribes, parses the binary payloads with
the same reference parsers as the bridge, and prints throughput, per-hand
sequence gaps, tick-based missing reports (matching ``src/wmr_devloss.c``) and
echo RTT (``min``, ``p50``, ``p95``, ``max``). Useful before the companion app
grows a Wi-Fi transport, and for completing the P5/P6 checks in
``docs/runs/sheets/pi-bridge.md``.

Examples::

    python3 tools/wifi_probe.py --host wmr-pi.local --stream batched
    python3 tools/wifi_probe.py --host 192.168.1.10 --token-file /etc/wmr-bridge/token --duration 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wmr_bridge import __version__, ws as wsmod  # noqa: E402
from wmr_bridge.bridge import HAND_NAMES  # noqa: E402
from wmr_bridge.decode import decode_frame  # noqa: E402
from wmr_bridge.protocol import (  # noqa: E402
    Reassembler,
    parse_batch,
    unpack_record,
)

DEVLOSS_WINDOW = 256
DEVLOSS_WARMUP = 16
DEVLOSS_REFRESH = 32
DEVLOSS_DISCONTINUITY_TICKS = 50_000_000  # 5 s at 100 ns per tick
TICK_MS = 1e-4  # 100 ns in ms


class DevLossEstimator:
    """Port of ``src/wmr_devloss.c`` (and Dart ``TickTimer`` missing-report rule).

    Estimates controller reports lost before reaching the bridge from the
    controller's own 32-bit 100 ns ``device_ticks`` clock. Each delta between
    consecutive reports implies ``round(delta / period) - 1`` missing reports,
    where ``period`` is the median of the deltas at most 1.5x the 10th-percentile
    delta over a sliding window of the last 256 deltas, refreshed every 32
    deltas after a 16-delta warm-up (counted retroactively).
    """

    def __init__(
        self,
        *,
        window: int = DEVLOSS_WINDOW,
        warmup: int = DEVLOSS_WARMUP,
        refresh: int = DEVLOSS_REFRESH,
        discontinuity_ticks: int = DEVLOSS_DISCONTINUITY_TICKS,
    ) -> None:
        self.window_size = window
        self.warmup = warmup
        self.refresh = refresh
        self.discontinuity_ticks = discontinuity_ticks
        self._window: list[int] = [0] * window
        self.count = 0
        self._head = 0
        self._since_refresh = 0
        self.prev_ticks: int | None = None
        self.period_ticks = 0
        self.reports = 0
        self.missing = 0
        self.discontinuities = 0

    def _estimate_period(self) -> int:
        if self.count == 0:
            return 0
        sorted_deltas = sorted(self._window[: self.count])
        ref = sorted_deltas[self.count // 10]
        if ref == 0:
            return 0
        limit = ref + ref // 2
        m = 0
        while m < self.count and sorted_deltas[m] <= limit:
            m += 1
        if m == 0:
            return 0
        if m & 1:
            return sorted_deltas[m // 2]
        return (sorted_deltas[m // 2 - 1] + sorted_deltas[m // 2]) // 2

    @staticmethod
    def _implied_missing(delta: int, period: int) -> int:
        k = (delta + period // 2) // period
        return k - 1 if k > 1 else 0

    def add(self, ticks: int) -> None:
        ticks &= 0xFFFFFFFF
        self.reports += 1
        if self.prev_ticks is None:
            self.prev_ticks = ticks
            return
        delta = (ticks - self.prev_ticks) & 0xFFFFFFFF
        self.prev_ticks = ticks
        if delta == 0:
            return
        if delta > self.discontinuity_ticks:
            self.discontinuities += 1
            return
        self._window[self._head] = delta
        self._head = (self._head + 1) % self.window_size
        if self.count < self.window_size:
            self.count += 1
        self._since_refresh += 1

        if self.period_ticks == 0:
            if self.count >= self.warmup:
                self.period_ticks = self._estimate_period()
                self._since_refresh = 0
                if self.period_ticks:
                    for i in range(self.count):
                        self.missing += self._implied_missing(self._window[i], self.period_ticks)
            return
        self.missing += self._implied_missing(delta, self.period_ticks)
        if self._since_refresh >= self.refresh:
            period = self._estimate_period()
            if period:
                self.period_ticks = period
            self._since_refresh = 0

    @property
    def loss_pct(self) -> float:
        total = self.reports + self.missing
        if not total:
            return 0.0
        return round(self.missing * 100.0 / total, 2)

    @property
    def period_ms(self) -> float | None:
        if not self.period_ticks:
            return None
        return round(self.period_ticks * TICK_MS, 2)


def summarize_rtt(samples_ms: list[float]) -> dict[str, int | float]:
    """Compute RTT summary stats (``samples``, ``min``, ``p50``, ``p95``, ``max``)."""

    ordered = sorted(samples_ms)
    n = len(ordered)

    def _at(q: float) -> float:
        index = min(n - 1, max(0, int(round(q * (n - 1)))))
        return round(ordered[index], 2)

    return {
        "samples": n,
        "min": round(ordered[0], 2),
        "p50": round(ordered[n // 2], 2),
        "p95": _at(0.95),
        "max": round(ordered[-1], 2),
    }


class Counters:
    def __init__(self) -> None:
        self.records = 0
        self.hand_records = [0, 0]
        self.messages = 0
        self.batches = 0
        self.gaps = [0, 0]
        self.last_sequence: list[int | None] = [None, None]
        self.devloss = [DevLossEstimator(), DevLossEstimator()]
        self.raw_frames = 0
        self.raw_incomplete = 0

    def sequence(self, hand: int, sequence: int, device_ticks: int | None = None) -> None:
        previous = self.last_sequence[hand]
        if previous is not None:
            expected = (previous + 1) & 0xFFFFFFFF
            if sequence != expected:
                self.gaps[hand] += (sequence - expected) & 0xFFFFFFFF
        self.last_sequence[hand] = sequence
        self.records += 1
        self.hand_records[hand] += 1
        if device_ticks is not None:
            self.devloss[hand].add(device_ticks)

    def snapshot(self, elapsed_s: float | None = None) -> dict:
        hands = []
        for index, name in enumerate(HAND_NAMES):
            rec = self.hand_records[index]
            dl = self.devloss[index]
            missing = dl.missing
            total = rec + missing
            loss_pct = round(missing * 100.0 / total, 2) if total else 0.0
            hands.append(
                {
                    "hand": name,
                    "records": rec,
                    "recordsPerSecond": round(rec / elapsed_s, 1) if elapsed_s else 0.0,
                    "sequenceGaps": self.gaps[index],
                    "lastSequence": self.last_sequence[index],
                    "missingFromTicks": missing,
                    "lossPct": loss_pct,
                    "periodTicks": dl.period_ticks or None,
                    "periodMs": dl.period_ms,
                    "tickDiscontinuities": dl.discontinuities,
                }
            )
        return {
            "records": self.records,
            "messages": self.messages,
            "batches": self.batches,
            "gaps": list(self.gaps),
            "missingFromTicks": [dl.missing for dl in self.devloss],
            "lossPct": [h["lossPct"] for h in hands],
            "rawFrames": self.raw_frames,
            "rawIncomplete": self.raw_incomplete,
            "lastSequence": list(self.last_sequence),
            "hands": hands,
            "bleFields": {
                "rssi": "n/a",
                "phy": "n/a",
                "mtu": "n/a",
                "interval": "n/a",
                "esp_timer": "n/a",
            },
        }


async def run(args: argparse.Namespace) -> int:
    token = args.token
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").splitlines()[0]
    client = await wsmod.connect(args.host, args.port, args.path, timeout=10)
    print(f"connected to ws://{args.host}:{args.port}{args.path} as {__version__}")
    hello = await asyncio.wait_for(client.recv_json(), 5)
    print("hello:", json.dumps(hello, indent=2))
    if hello.get("authRequired"):
        if not token:
            print("server requires a token; pass --token or --token-file", file=sys.stderr)
            await client.close(4401, "token missing")
            return 2
        await client.send_json({"type": "auth", "token": token})
        auth = await asyncio.wait_for(client.recv_json(), 5)
        print("auth:", auth)
        if auth.get("type") != "auth_ok":
            await client.close(4401, "authentication failed")
            return 2
    await client.send_json({"type": "subscribe", "stream": args.stream})
    reply = await asyncio.wait_for(client.recv_json(), 5)
    print("subscribe:", reply)
    if reply.get("type") != "subscribed":
        await client.close()
        return 2

    counters = Counters()
    reassembler = Reassembler()
    started = time.monotonic()
    next_report = started + args.report_interval
    next_ping = started
    ping_times: list[float] = []
    pending_pings: dict[int, float] = {}
    ping_id = 0
    stop = asyncio.Event()

    async def ping_loop() -> None:
        nonlocal ping_id, next_ping
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_ping and not client.closed:
                ping_id += 1
                pending_pings[ping_id] = now
                await client.send_json({"type": "ping", "id": ping_id, "t": int(now * 1000)})
                next_ping = now + args.ping_interval
            await asyncio.sleep(0.05)

    ping_task = asyncio.create_task(ping_loop())
    try:
        while True:
            if args.duration and time.monotonic() - started >= args.duration:
                break
            try:
                opcode, payload = await asyncio.wait_for(client.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                opcode = None
                payload = b""
            now = time.monotonic()
            if opcode == wsmod.OP_BINARY:
                counters.messages += 1
                if args.stream == "raw":
                    frame = reassembler.push(payload)
                    counters.raw_incomplete = sum(reassembler.frames_incomplete)
                    if frame is not None:
                        counters.raw_frames += 1
                        state = decode_frame(frame.data)
                        counters.sequence(
                            frame.hand,
                            frame.sequence,
                            state.ticks if state is not None else None,
                        )
                elif args.stream == "decoded":
                    record = unpack_record(payload)
                    counters.sequence(record.hand, record.sequence, record.device_ticks)
                else:
                    records = parse_batch(payload)
                    counters.batches += 1
                    for packed in records:
                        record = unpack_record(packed)
                        counters.sequence(record.hand, record.sequence, record.device_ticks)
            elif opcode == wsmod.OP_TEXT:
                message = json.loads(payload)
                if message.get("type") == "pong":
                    sent = pending_pings.pop(message.get("id"), None)
                    if sent is not None:
                        ping_times.append((now - sent) * 1000.0)
                elif message.get("type") == "error":
                    print("server error:", message, file=sys.stderr)
            if now >= next_report:
                elapsed = now - started
                rate = counters.records / elapsed if elapsed else 0
                rtt = f"{ping_times[-1]:.1f}ms" if ping_times else "-"
                loss_l = counters.devloss[0].loss_pct
                loss_r = counters.devloss[1].loss_pct
                miss_l = counters.devloss[0].missing
                miss_r = counters.devloss[1].missing
                print(
                    f"{elapsed:6.1f}s  {rate:7.1f} rec/s  "
                    f"left={counters.last_sequence[0]} right={counters.last_sequence[1]}  "
                    f"gaps=({counters.gaps[0]},{counters.gaps[1]})  "
                    f"missing=({miss_l},{miss_r}) loss=({loss_l:.1f}%,{loss_r:.1f}%)  "
                    f"batches={counters.batches}  rtt={rtt}"
                )
                next_report = now + args.report_interval
    finally:
        stop.set()
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass
        try:
            await client.send_json({"type": "status"})
            status = await asyncio.wait_for(client.recv_json(), 2)
        except (asyncio.TimeoutError, wsmod.WebSocketClosed, wsmod.WebSocketError):
            status = None
        await client.close()

    elapsed = time.monotonic() - started
    summary = counters.snapshot(elapsed)
    summary["elapsedS"] = round(elapsed, 2)
    summary["recordsPerSecond"] = round(counters.records / elapsed, 1) if elapsed else 0
    if ping_times:
        summary["rttMs"] = summarize_rtt(ping_times)
    summary["dropped"] = status.get("dropped") if isinstance(status, dict) else None
    summary["serverStatus"] = status
    print(json.dumps(summary, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default="/wmr")
    parser.add_argument("--stream", choices=("raw", "decoded", "batched"), default="batched")
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--duration", type=float, default=10.0, help="seconds to run (0 = forever)")
    parser.add_argument("--report-interval", type=float, default=2.0)
    parser.add_argument("--ping-interval", type=float, default=2.0)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
