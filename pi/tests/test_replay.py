"""Capture parsing and replay-source tests."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from wmr_bridge.bridge import Bridge
from wmr_bridge.inputs import ReplaySource, read_capture

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"


class CaptureTests(unittest.TestCase):
    def test_reads_repository_samples(self) -> None:
        entries = read_capture(SAMPLES)
        self.assertEqual(len(entries), 8)
        self.assertEqual(entries[0][0], "sessionA-0")
        self.assertEqual(len(entries[0][1]), 46)
        self.assertEqual(entries[0][1][:2], b"\xA1\x01")

    def test_accepts_hidraw_shapes(self) -> None:
        with NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("# comment\n")
            handle.write("hid-0 01 " + " ".join(["00"] * 44) + "\n")
            handle.write("plain " + " ".join(["A1", "01"] + ["11"] * 44) + "\n")
            path = handle.name
        try:
            entries = read_capture(path)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][1][:2], b"\xA1\x01")
        self.assertEqual(entries[1][1][2], 0x11)

    def test_rejects_bad_input(self) -> None:
        with NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("bad zz\n")
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                read_capture(path)
        finally:
            Path(path).unlink(missing_ok=True)

        with NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("short 01 02\n")
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                read_capture(path)
        finally:
            Path(path).unlink(missing_ok=True)


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_feeds_both_hands(self) -> None:
        entries = read_capture(SAMPLES)
        source = ReplaySource(entries, rate=10_000, hand="auto", loop=False)
        bridge = Bridge()
        await source.run(bridge)
        self.assertEqual(sum(hand.reports for hand in bridge.hands), len(entries))
        self.assertEqual([hand.reports for hand in bridge.hands], [5, 3])

    async def test_replay_alternate_hands(self) -> None:
        entries = read_capture(SAMPLES)[:3]
        source = ReplaySource(entries, rate=10_000, hand="alternate", loop=False)
        bridge = Bridge()
        await source.run(bridge)
        self.assertEqual([hand.reports for hand in bridge.hands], [2, 1])

    async def test_replay_one_hand(self) -> None:
        entries = read_capture(SAMPLES)[:3]
        source = ReplaySource(entries, rate=10_000, hand="right", loop=False)
        bridge = Bridge()
        await source.run(bridge)
        self.assertEqual([hand.reports for hand in bridge.hands], [0, 3])


if __name__ == "__main__":
    unittest.main()
