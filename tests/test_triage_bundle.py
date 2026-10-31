"""Tests for tools/triage_bundle.py against a synthetic tester bundle.

The fixture mirrors the tree the companion app writes (`manifest.md`,
`diagnostics.json`, `captures/`, `latency/` and a pose file) and its capture
frames come from `samples/controller_frames.txt`. No app, hardware or network.

Run: python3 -m unittest
"""

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURE = HERE / "fixtures" / "wmr-ext-20261030"
SAMPLES = ROOT / "samples" / "controller_frames.txt"
sys.path.insert(0, str(ROOT / "tools"))

import triage_bundle  # noqa: E402


def build_bundle(destination, drop=(), edit=None):
    """Zip the fixture, optionally dropping paths or rewriting a file."""
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(FIXTURE.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(FIXTURE).as_posix()
            if any(rel == name or rel.startswith(name.rstrip("/") + "/")
                   for name in drop):
                continue
            data = path.read_bytes()
            if edit is not None:
                data = edit(rel, data)
            archive.writestr(rel, data)
    return destination


def run_main(zip_path):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = triage_bundle.main([zip_path])
    return code, out.getvalue(), err.getvalue()


def set_pose_span(seconds):
    """Rewrite the fixture's pose file so its samples span `seconds`."""
    def edit(rel, data):
        if rel != "pose_capture_androidXr_left_still.json":
            return data
        document = json.loads(data)
        first = document["samples"][0]
        last = document["samples"][-1]
        last["host_ns"] = first["host_ns"] + int(seconds * 1_000_000_000)
        for key in ("left", "right"):
            last[key]["host_time_ns"] = last["host_ns"]
        last["tracking"]["time_ns"] = last["host_ns"]
        return json.dumps(document).encode()
    return edit


class FixtureReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wmr-triage-test-")
        self.addCleanup(self._tmp.cleanup)
        self.zip_path = build_bundle(
            os.path.join(self._tmp.name, "wmr-ext-20261030.zip"))

    def test_report_has_app_firmware_mtu_stream_and_captures(self):
        code, out, err = run_main(self.zip_path)
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        for expected in (
            "App: wmr_companion_app 0.2.2+4 (c0ffee1)",
            "firmware 1.2.3, MTU 247",
            "stream raw: LEFT 99.8 Hz, loss 0.4%, RIGHT 99.6 Hz, loss 1.1%",
            "captures/left-20261030-120000.txt  hand LEFT  mtu 247",
            "12 frames over 0.06 s (170.0 Hz), 0 sequence gaps",
            "pose_capture_androidXr_left_still.json  androidXr/left/still",
            "Problems: none",
        ):
            self.assertIn(expected, out)

    def test_fixture_frames_are_verbatim_samples(self):
        samples = {}
        for line in SAMPLES.read_text().splitlines():
            parts = line.split()
            if line.startswith("session"):
                samples[parts[0]] = bytes.fromhex("".join(parts[1:])).hex()
        captures = sorted((FIXTURE / "captures").glob("*.txt"))
        self.assertEqual([p.name for p in captures],
                         ["left-20261030-120000.txt", "right-20261030-120000.txt"])
        for path, sample in zip(captures, ("sessionA-0", "sessionB-0")):
            frames = [line.split()[4] for line in path.read_text().splitlines()
                      if line.startswith("F ")]
            self.assertEqual(len(frames), 12)
            self.assertEqual(set(frames), {samples[sample]})


class FailingBundleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wmr-triage-test-")
        self.addCleanup(self._tmp.cleanup)

    def bundle(self, drop=(), edit=None):
        return build_bundle(
            os.path.join(self._tmp.name, "wmr-ext-20261030.zip"),
            drop=drop, edit=edit)

    def test_missing_manifest_exits_nonzero_naming_it(self):
        code, out, err = run_main(self.bundle(drop=("manifest.md",)))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("manifest.md", err)

    def test_missing_capture_exits_nonzero_naming_it(self):
        code, out, err = run_main(self.bundle(drop=("captures",)))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("capture", err.lower())

    def test_missing_diagnostics_exits_nonzero_naming_it(self):
        code, out, err = run_main(self.bundle(drop=("diagnostics.json",)))
        self.assertEqual(code, 1)
        self.assertIn("diagnostics.json", err)

    def test_listed_pose_file_missing_is_reported(self):
        code, out, _err = run_main(
            self.bundle(drop=("pose_capture_androidXr_left_still.json",)))
        self.assertEqual(code, 0)
        self.assertIn("listed in diagnostics.json but missing", out)

    def test_unlisted_pose_file_is_reported(self):
        def drop_pose_list(_rel, data):
            document = json.loads(data)
            document.pop("pose_captures")
            return json.dumps(document).encode()

        code, out, _err = run_main(
            self.bundle(edit=lambda rel, data: drop_pose_list(rel, data)
                        if rel == "diagnostics.json" else data))
        self.assertEqual(code, 0)
        self.assertIn("present in the bundle but not listed", out)

    def test_manifest_bridge_count_mismatch_is_reported(self):
        def wrong_count(rel, data):
            if rel != "manifest.md":
                return data
            return data.replace(b"- Bridges: 1", b"- Bridges: 2")

        _code, out, _err = run_main(self.bundle(edit=wrong_count))
        self.assertIn("manifest says 2 bridge(s); diagnostics.json has 1", out)

    def test_run_shorter_than_30_seconds_is_flagged(self):
        code, out, _err = run_main(self.bundle(edit=set_pose_span(29)))
        self.assertEqual(code, 0)
        self.assertIn("run is 29.0 s, shorter than 30 s", out)

    def test_run_at_30_seconds_passes(self):
        code, out, _err = run_main(self.bundle(edit=set_pose_span(30)))
        self.assertEqual(code, 0)
        self.assertNotIn("shorter than 30 s", out)
        self.assertIn("Problems: none", out)


if __name__ == "__main__":
    unittest.main()
