"""Tests for the wifi_probe discovery, JSON output and Sitting P gates."""

from __future__ import annotations

import argparse
import io
import json
import socket
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.wifi_probe import discover, evaluate_gates, run as run_probe
from wmr_bridge.bridge import Bridge
from wmr_bridge.discovery import DiscoveryResponder
from wmr_bridge.inputs import read_capture
from wmr_bridge.server import BridgeServer

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
SAMPLE_DATA = read_capture(SAMPLES)[0][1]
P = 50_000  # 5 ms at 100 ns per tick (200 Hz controller)


def _frame_with_ticks(ticks: int) -> bytes:
    buf = bytearray(SAMPLE_DATA)
    struct.pack_into("<I", buf, 2 + 28, ticks & 0xFFFFFFFF)
    return bytes(buf)


def _summary(**overrides) -> dict:
    summary = {
        "rttMs": {"samples": 10, "min": 1.0, "p50": 8.0, "p95": 9.0, "max": 10.0},
        "hands": [
            {"hand": "LEFT", "lossPct": 0.0},
            {"hand": "RIGHT", "lossPct": 3.0},
        ],
        "gaps": [0, 0],
    }
    summary.update(overrides)
    return summary


class GateTests(unittest.TestCase):
    def test_all_gates_pass_at_the_limits(self) -> None:
        gates = evaluate_gates(_summary())
        self.assertTrue(gates["passed"])
        names = [check["name"] for check in gates["checks"]]
        self.assertEqual(names, ["rtt_p50_ms", "devloss_pct", "devloss_pct", "seq_gaps"])

    def test_rtt_boundary_is_strict(self) -> None:
        self.assertFalse(evaluate_gates(_summary(rttMs={"p50": 15.0}))["passed"])
        self.assertTrue(evaluate_gates(_summary(rttMs={"p50": 14.99}))["passed"])

    def test_missing_rtt_fails(self) -> None:
        gates = evaluate_gates(_summary(rttMs=None))
        self.assertFalse(gates["passed"])
        self.assertEqual(gates["checks"][0]["name"], "rtt_p50_ms")
        self.assertFalse(gates["checks"][0]["passed"])

    def test_per_hand_loss_over_three_percent_fails(self) -> None:
        gates = evaluate_gates(
            _summary(
                hands=[
                    {"hand": "LEFT", "lossPct": 0.0},
                    {"hand": "RIGHT", "lossPct": 3.01},
                ]
            )
        )
        self.assertFalse(gates["passed"])
        failed = [check for check in gates["checks"] if not check["passed"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["hand"], "RIGHT")

    def test_any_sequence_gap_fails(self) -> None:
        gates = evaluate_gates(_summary(gaps=[0, 2]))
        self.assertFalse(gates["passed"])
        self.assertFalse(gates["checks"][-1]["passed"])


class DiscoveryTests(unittest.TestCase):
    async def _responder(self) -> DiscoveryResponder:
        responder = DiscoveryResponder(
            bridge_id="wmr-pi-test",
            name="wmr-pi",
            port=9000,
            hands=["LEFT", "RIGHT"],
            listen_host="127.0.0.1",
            listen_port=0,
            reply_interval_s=0,
        )
        await responder.start()
        return responder

    def test_discover_returns_reply_matched_by_nonce(self) -> None:
        import asyncio

        async def probe() -> dict | None:
            responder = await self._responder()
            try:
                return await asyncio.to_thread(
                    discover, "127.0.0.1", responder.bound_port, timeout=2.0
                )
            finally:
                await responder.close()

        reply = asyncio.run(probe())
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply["type"], "wmr_bridge")
        self.assertEqual(reply["bridge_id"], "wmr-pi-test")
        self.assertEqual(reply["port"], 9000)
        self.assertNotIn("token", reply)

    def test_discover_times_out_without_a_responder(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.assertIsNone(discover("127.0.0.1", port, timeout=0.2))


class JsonOutAndGateRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_writes_summary_and_passes_clean_gates(self) -> None:
        bridge = Bridge(batch_max=4, batch_hold_ms=5)
        server = BridgeServer(bridge, host="127.0.0.1", port=0)
        await server.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                json_out = Path(tmp) / "sitting-p.json"
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
                    discover=False,
                    discovery_port=8765,
                    discovery_timeout=0.1,
                    json_out=str(json_out),
                    assert_gates=True,
                )

                async def feed() -> None:
                    import asyncio

                    await asyncio.sleep(0.05)
                    for slot in range(30):
                        bridge.ingest(0, _frame_with_ticks(slot * P))
                        bridge.ingest(1, _frame_with_ticks(slot * P))
                    await asyncio.sleep(0.05)

                import asyncio

                feeder = asyncio.create_task(feed())
                with redirect_stdout(io.StringIO()):
                    rc = await run_probe(args)
                await feeder

                self.assertEqual(rc, 0)
                written = json.loads(json_out.read_text(encoding="utf-8"))
                self.assertTrue(written["gates"]["passed"])
                self.assertIn("uplink", written)
                self.assertEqual(written["gaps"], [0, 0])
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
