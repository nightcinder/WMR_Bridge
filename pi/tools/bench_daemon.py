#!/usr/bin/env python3
"""End-to-end daemon CPU attribution bench (ONY-1534).

Replays the sample capture at ``--rate`` reports/s into a real Bridge, with an
optional real subscriber (``--stream raw|decoded|batched``, default none); the
forked child plays the client, so the client's CPU is excluded and the replay
producer stays in the measured pid. Nested stopwatches wrap the production
functions into six stages; ``frame copy`` is the ingest residual: report copy
plus sequence/stats/idle bookkeeping that cannot be split without a production
change. The stage sum must reconcile with this pid's CPU delta from
``/proc/<pid>/stat`` (exit 1 beyond 20 %); prints µs/report, % of one core.
"""

from __future__ import annotations

import argparse, asyncio, contextlib, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import wmr_bridge.bridge as bridge_mod  # noqa: E402
from wmr_bridge import decode as dec, ws as wsmod  # noqa: E402
from wmr_bridge.inputs import read_capture  # noqa: E402
from wmr_bridge.server import BridgeServer  # noqa: E402

STAGES = ("frame copy", "decode", "record build", "pack", "enqueue", "write")
DEFAULT_SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"


def process_cpu_ns(pid: int = os.getpid()) -> int:
    """utime+stime of ``pid`` from ``/proc/<pid>/stat``, in nanoseconds."""
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) * (1_000_000_000 // os.sysconf("SC_CLK_TCK"))

def reconciled(total_ns: int, cpu_ns: int) -> bool:
    """The 20 % reconciliation gate the bench must pass."""
    return abs(1.0 - total_ns / max(cpu_ns, 1)) <= 0.20

class StageTimers:
    """Stage counters patched over the production pipeline, restorable."""
    def __init__(self) -> None:
        self.ns: dict[str, int] = dict.fromkeys(STAGES, 0)
        self._ingest_ns = 0
        self._saved: list[tuple[object, str, object]] = []
        self.wrap(bridge_mod.Bridge, "ingest", None)
        self.wrap(bridge_mod, "decode_frame", "decode")
        self.wrap(bridge_mod, "record_from_state", "record build")
        self.wrap(bridge_mod, "pack_record", "pack")
        self.wrap(bridge_mod, "encode_raw_messages", "pack")
        self.wrap(bridge_mod, "encode_batch", "pack")
        self.wrap(bridge_mod.Subscriber, "_enqueue", "enqueue")
        self.wrap(wsmod.WebSocket, "send_binary", "write")
    def wrap(self, owner: object, name: str, stage: str | None) -> None:
        original = getattr(owner, name)
        self._saved.append((owner, name, original))
        # A send suspends inside its window, so only its CPU time counts; sync stages take the cheap clock.
        clock = time.process_time_ns if asyncio.iscoroutinefunction(original) else time.perf_counter_ns

        async def tail(start: int, awaitable: object) -> object:
            try:
                return await awaitable
            finally:
                self.ns[stage] += clock() - start

        def timed(*args: object, **kwargs: object) -> object:
            start, result = clock(), original(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return tail(start, result)
            if stage is None: self._ingest_ns += clock() - start  # ingest window
            else: self.ns[stage] += clock() - start
            return result
        setattr(owner, name, timed)
    def restore(self) -> None:
        for owner, name, original in reversed(self._saved):
            setattr(owner, name, original)
        self._saved.clear()
        # Nested windows ran inside ingest; the remainder is "frame copy".
        self.ns["frame copy"] = self._ingest_ns - sum(self.ns[s] for s in STAGES[1:5])

async def run_config(frames: list[bytes], stream: str, rate: float, burst: int, count: int) -> dict:
    bridge = bridge_mod.Bridge(ping_interval_s=0)
    bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
    stages = StageTimers()
    async def drain(port: int) -> None:  # runs only in the forked child
        client = await wsmod.connect("127.0.0.1", port, "/wmr", timeout=5)
        await client.recv_json()  # hello
        await client.send_json({"type": "subscribe", "stream": stream})
        with contextlib.suppress(Exception):
            while True:
                await client.recv()  # ends when the server closes
    server, pid = None, 0
    try:
        if stream != "none":
            server = BridgeServer(bridge, "127.0.0.1", 0)
            await server.start()
            pid = os.fork()
            if pid == 0:  # the subscriber child's CPU stays out of this pid
                asyncio.run(drain(server.port))
                os._exit(0)
            deadline = time.monotonic() + 10
            while not bridge.stream_client_active:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"no {stream} subscriber connected")
                await asyncio.sleep(0.05)
        cpu0, wall0, sent, next_t, period = process_cpu_ns(), time.monotonic(), 0, time.monotonic(), 1.0 / rate
        while sent < count:  # bursts of `burst` frames, deadline-paced at rate/s
            chunk = min(burst, count - sent)
            for _ in range(chunk):
                bridge.ingest(0, frames[sent % len(frames)])
                sent += 1
            next_t += chunk * period
            if (pause := next_t - time.monotonic()) > 0 and sent < count:
                await asyncio.sleep(pause)
        await asyncio.sleep(0.02)  # let the writer task and a batch-hold flush land
        return {"reports": count, "wall_s": time.monotonic() - wall0,
                "cpu_ns": process_cpu_ns() - cpu0, "stage_ns": stages.ns,
                "delivered": bridge.hands[0].delivered, "dropped": bridge.hands[0].dropped}
    finally:
        stages.restore()
        if server is not None:
            await server.close()
        if pid > 0:
            await asyncio.sleep(0.05)  # reap the child once it saw the close
            os.waitpid(pid, os.WNOHANG)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rate", type=float, default=400.0, help="reports per second")
    parser.add_argument("--stream", choices=("none", "raw", "decoded", "batched"), default="none")
    parser.add_argument("--seconds", type=float, default=20.0, help="replay window")
    args = parser.parse_args(argv)
    if args.rate <= 0 or args.seconds <= 0: parser.error("--rate and --seconds must be positive")
    frames = [data for _l, data in read_capture(DEFAULT_SAMPLES) if dec.decode_frame(data)]
    if not frames: raise SystemExit(f"no decodable frames in {DEFAULT_SAMPLES}")
    count = int(args.rate * args.seconds)
    result = asyncio.run(run_config(frames, args.stream, args.rate, 100, count))
    reports, wall = result["reports"], result["wall_s"]
    total = sum(result["stage_ns"].values())
    print(f"stream={args.stream} rate={args.rate:.0f}/s reports={reports} wall={wall:.1f}s delivered={result['delivered']} dropped={result['dropped']}")
    print(f"{'stage':13s} {'us/report':>10s} {'% of one core':>14s}")
    for stage, ns in [*result["stage_ns"].items(), ("total", total), ("process cpu", result["cpu_ns"])]:
        print(f"{stage:13s} {ns / reports / 1000:10.2f} {ns / wall / 1e7:14.3f}")
    ok = reconciled(total, result["cpu_ns"])
    print(f"stage sum is {total / max(result['cpu_ns'], 1) * 100:.1f}% of process cpu ({'PASS' if ok else 'FAIL'}, tolerance 20%)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
