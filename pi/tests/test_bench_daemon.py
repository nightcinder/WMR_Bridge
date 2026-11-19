"""Tests for tools/bench_daemon.py: stage wiring, the 20 % gate, end-to-end run.

The full-window reconciliation evidence (stage sum vs ``/proc/<pid>/stat``)
is recorded by ``bench_daemon.py --rate 400`` runs; ``/proc`` ticks are
10 ms, so a unit-test window is too short for that gate to be stable. These
tests pin the tool's logic instead: the reconciliation formula, that a
no-subscriber replay charges only ``frame copy`` and ``decode``, and that a
real subscribed replay through a forked client delivers every report and
charges all six stages, leaving the production modules restored.
"""

from __future__ import annotations

import asyncio
import unittest

import wmr_bridge.bridge as bridge_mod
from tools.bench_daemon import STAGES, reconciled, run_config
from wmr_bridge import decode as dec

# A minimal, decode-able 46-byte report: A1 01 header plus zero payload.
FRAME = bytes((0xA1, dec.REPORT_ID)) + bytes(dec.FRAME_LENGTH - 2)
RATE = 1000  # 200 reports = 0.2 s of wall time per configuration


class BenchDaemonTests(unittest.TestCase):
    def test_reconciled_gate(self) -> None:
        self.assertTrue(reconciled(100, 100))
        self.assertTrue(reconciled(100, 125))  # exactly 20 % off: the boundary
        self.assertFalse(reconciled(100, 80))  # 25 % off in the other direction
        self.assertFalse(reconciled(100, 79))
        self.assertFalse(reconciled(100, 0))  # a vanished /proc delta must fail

    def test_none_config_charges_only_ingest_stages(self) -> None:
        result = asyncio.run(run_config([FRAME], "none", RATE, 50, 200))
        self.assertEqual(result["reports"], 200)
        self.assertEqual((result["delivered"], result["dropped"]), (0, 0))
        self.assertGreater(result["stage_ns"]["frame copy"], 0)
        self.assertGreater(result["stage_ns"]["decode"], 0)
        for stage in ("record build", "pack", "enqueue", "write"):
            self.assertEqual(result["stage_ns"][stage], 0, stage)
        # The monkeypatches must not leak into the rest of the suite.
        self.assertEqual(bridge_mod.decode_frame.__module__, "wmr_bridge.decode")
        self.assertEqual(bridge_mod.Bridge.ingest.__qualname__, "Bridge.ingest")

    def test_decoded_config_delivers_and_charges_all_stages(self) -> None:
        count = 200
        result = asyncio.run(run_config([FRAME], "decoded", RATE, 50, count))
        self.assertEqual((result["delivered"], result["dropped"]), (count, 0))
        for stage in STAGES:
            self.assertGreater(result["stage_ns"][stage], 0, stage)
        expected = count / RATE
        self.assertLess(abs(result["wall_s"] - expected), 0.3)


if __name__ == "__main__":
    unittest.main()
