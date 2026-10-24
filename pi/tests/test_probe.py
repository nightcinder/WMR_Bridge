"""Tests for tools/wifi_probe.py: DevLossEstimator C-parity, RTT stats, and end-to-end probe run."""

from __future__ import annotations

import argparse
import io
import json
import struct
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.wifi_probe import Counters, DevLossEstimator, run as run_probe, summarize_rtt
from wmr_bridge.bridge import Bridge
from wmr_bridge.inputs import read_capture
from wmr_bridge.server import BridgeServer

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
SAMPLE_DATA = read_capture(SAMPLES)[0][1]
P = 50_000  # 5 ms at 100 ns per tick (200 Hz controller)


def _frame_with_ticks(ticks: int) -> bytes:
    buf = bytearray(SAMPLE_DATA)
    struct.pack_into("<I", buf, 2 + 28, ticks & 0xFFFFFFFF)
    return bytes(buf)


class DevLossTests(unittest.TestCase):
    def test_clean_stream(self) -> None:
        t = DevLossEstimator()
        ticks = 1000
        for _ in range(300):
            t.add(ticks)
            ticks += P
        self.assertEqual(t.missing, 0)
        self.assertEqual(t.discontinuities, 0)
        self.assertEqual(t.period_ticks, P)
        self.assertEqual(t.reports, 300)
        self.assertEqual(t.loss_pct, 0.0)
        self.assertEqual(t.period_ms, 5.0)

    def test_warmup_does_not_count_early(self) -> None:
        t = DevLossEstimator()
        ticks = 0
        for i in range(t.warmup):
            t.add(ticks)
            ticks += 3 * P if i % 3 == 2 else P
        self.assertEqual(t.period_ticks, 0)
        self.assertEqual(t.missing, 0)

    def test_known_loss_pattern_matches_c(self) -> None:
        t = DevLossEstimator()
        for slot in range(1000):
            if slot % 10 < 7:
                t.add(123456 + slot * P)
        self.assertEqual(t.period_ticks, P)
        self.assertEqual(t.reports, 700)
        self.assertEqual(t.missing, 99 * 3)
        self.assertEqual(t.discontinuities, 0)
        self.assertAlmostEqual(t.loss_pct, round(297 * 100.0 / 997, 2))

    def test_isolated_loss(self) -> None:
        t = DevLossEstimator()
        for slot in range(900):
            if slot % 3 != 2:
                t.add(slot * P)
        self.assertEqual(t.period_ticks, P)
        self.assertEqual(t.missing, 299)

    def test_jitter_is_not_loss(self) -> None:
        t = DevLossEstimator()
        ticks = 0
        for i in range(500):
            t.add(ticks)
            ticks += 51_000 if (i & 1) else 49_000
        self.assertEqual(t.missing, 0)

    def test_32bit_wrap(self) -> None:
        t = DevLossEstimator()
        ticks = (0xFFFFFFFF - 100 * P) & 0xFFFFFFFF
        for _ in range(400):
            t.add(ticks)
            ticks = (ticks + P) & 0xFFFFFFFF
        self.assertEqual(t.missing, 0)
        self.assertEqual(t.discontinuities, 0)
        self.assertEqual(t.period_ticks, P)

    def test_discontinuity_and_duplicates(self) -> None:
        t = DevLossEstimator()
        ticks = 0
        for _ in range(100):
            t.add(ticks)
            ticks += P
        ticks += 60_000_000  # 6 s gap (> 5 s discontinuity threshold)
        for _ in range(100):
            t.add(ticks)
            ticks += P
        self.assertEqual(t.discontinuities, 1)
        self.assertEqual(t.missing, 0)
        before = t.reports
        t.add(ticks - P)  # duplicate tick
        self.assertEqual(t.reports, before + 1)
        self.assertEqual(t.missing, 0)
        self.assertEqual(t.discontinuities, 1)


class RttAndCountersTests(unittest.TestCase):
    def test_summarize_rtt_includes_min_p50_p95_max(self) -> None:
        samples = [float(i) for i in range(1, 21)]
        stats = summarize_rtt(samples)
        self.assertEqual(stats["samples"], 20)
        self.assertEqual(stats["min"], 1.0)
        self.assertEqual(stats["p50"], 11.0)
        self.assertEqual(stats["p95"], 19.0)
        self.assertEqual(stats["max"], 20.0)

    def test_counters_snapshot_maps_p5_p6_fields(self) -> None:
        counters = Counters()
        for slot in range(30):
            if slot != 20:  # 1 missing report on LEFT
                counters.sequence(0, slot + 1 if slot < 20 else slot, slot * P)
            counters.sequence(1, slot + 1, slot * P)
        snap = counters.snapshot(elapsed_s=2.0)
        self.assertEqual(snap["missingFromTicks"], [1, 0])
        self.assertEqual(snap["hands"][0]["hand"], "LEFT")
        self.assertEqual(snap["hands"][0]["records"], 29)
        self.assertEqual(snap["hands"][0]["missingFromTicks"], 1)
        self.assertEqual(snap["hands"][0]["lossPct"], round(100.0 / 30, 2))
        self.assertEqual(snap["hands"][1]["hand"], "RIGHT")
        self.assertEqual(snap["hands"][1]["records"], 30)
        self.assertEqual(snap["hands"][1]["missingFromTicks"], 0)
        self.assertEqual(snap["hands"][1]["lossPct"], 0.0)
        self.assertEqual(
            snap["bleFields"],
            {
                "rssi": "n/a",
                "phy": "n/a",
                "mtu": "n/a",
                "interval": "n/a",
                "esp_timer": "n/a",
            },
        )


class ProbeEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_reports_tick_loss_and_rtt_percentiles(self) -> None:
        bridge = Bridge(batch_max=4, batch_hold_ms=5)
        server = BridgeServer(bridge, host="127.0.0.1", port=0)
        await server.start()
        try:
            args = argparse.Namespace(
                host="127.0.0.1",
                port=server.port,
                path="/wmr",
                stream="batched",
                token=None,
                token_file=None,
                duration=0.35,
                report_interval=10.0,
                ping_interval=0.05,
            )
            buf = io.StringIO()

            async def feed() -> None:
                import asyncio

                await asyncio.sleep(0.05)
                for slot in range(25):
                    if slot != 18:
                        bridge.ingest(0, _frame_with_ticks(slot * P))
                    bridge.ingest(1, _frame_with_ticks(slot * P))
                await asyncio.sleep(0.05)

            import asyncio

            feeder = asyncio.create_task(feed())
            with redirect_stdout(buf):
                rc = await run_probe(args)
            await feeder
            self.assertEqual(rc, 0)
            output = buf.getvalue()
            summary = json.loads(output[output.rfind("\n{") + 1 :])
            self.assertEqual(summary["missingFromTicks"], [1, 0])
            self.assertEqual(summary["hands"][0]["records"], 24)
            self.assertEqual(summary["hands"][0]["missingFromTicks"], 1)
            self.assertEqual(summary["hands"][1]["records"], 25)
            self.assertEqual(summary["hands"][1]["missingFromTicks"], 0)
            self.assertIn("rttMs", summary)
            for key in ("samples", "min", "p50", "p95", "max"):
                self.assertIn(key, summary["rttMs"])
            self.assertEqual(summary["dropped"], 0)
            self.assertEqual(summary["bleFields"]["rssi"], "n/a")
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
