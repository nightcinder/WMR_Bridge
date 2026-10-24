#!/usr/bin/env python3
"""Bench client for the Wi-Fi bridge.

Connects to ``ws://host:8765/wmr``, subscribes, parses the binary payloads with
the same reference parsers as the bridge, and prints throughput, per-hand
sequence gaps and echo RTT. Useful before the companion app grows a Wi-Fi
transport, and for measuring a Pi's Wi-Fi link.

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
from wmr_bridge.protocol import (  # noqa: E402
    Reassembler,
    parse_batch,
    unpack_record,
)


class Counters:
    def __init__(self) -> None:
        self.records = 0
        self.messages = 0
        self.batches = 0
        self.gaps = [0, 0]
        self.last_sequence: list[int | None] = [None, None]
        self.raw_frames = 0
        self.raw_incomplete = 0

    def sequence(self, hand: int, sequence: int) -> None:
        previous = self.last_sequence[hand]
        if previous is not None:
            expected = (previous + 1) & 0xFFFFFFFF
            if sequence != expected:
                self.gaps[hand] += 1
        self.last_sequence[hand] = sequence
        self.records += 1

    def snapshot(self) -> dict:
        return {
            "records": self.records,
            "messages": self.messages,
            "batches": self.batches,
            "gaps": list(self.gaps),
            "rawFrames": self.raw_frames,
            "rawIncomplete": self.raw_incomplete,
            "lastSequence": list(self.last_sequence),
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
                    if frame is not None:
                        counters.raw_frames += 1
                        counters.records += 1
                        counters.sequence(frame.hand, frame.sequence)
                elif args.stream == "decoded":
                    record = unpack_record(payload)
                    counters.sequence(record.hand, record.sequence)
                else:
                    records = parse_batch(payload)
                    counters.batches += 1
                    for packed in records:
                        record = unpack_record(packed)
                        counters.sequence(record.hand, record.sequence)
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
                print(
                    f"{elapsed:6.1f}s  {rate:7.1f} rec/s  "
                    f"left={counters.last_sequence[0]} right={counters.last_sequence[1]}  "
                    f"gaps=({counters.gaps[0]},{counters.gaps[1]})  "
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
    summary = counters.snapshot()
    summary["elapsedS"] = round(elapsed, 2)
    summary["recordsPerSecond"] = round(counters.records / elapsed, 1) if elapsed else 0
    if ping_times:
        ordered = sorted(ping_times)
        summary["rttMs"] = {
            "samples": len(ordered),
            "p50": round(ordered[len(ordered) // 2], 2),
            "max": round(ordered[-1], 2),
        }
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
