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


def build_bundle(destination, drop=(), edit=None, rename=None, extra=None):
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
            rel = (rename or {}).get(rel, rel)
            archive.writestr(rel, data)
        for rel, data in (extra or {}).items():
            archive.writestr(rel, data)
    return destination


def run_main(zip_path):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = triage_bundle.main([zip_path])
    return code, out.getvalue(), err.getvalue()


def pose_span(data, seconds):
    """Return pose JSON bytes whose samples span `seconds`."""
    document = json.loads(data)
    first = document["samples"][0]
    last = document["samples"][-1]
    last["host_ns"] = first["host_ns"] + int(seconds * 1_000_000_000)
    for key in ("left", "right"):
        last[key]["host_time_ns"] = last["host_ns"]
    last["tracking"]["time_ns"] = last["host_ns"]
    return json.dumps(document).encode()


def repeated_pose(data, samples, seconds):
    """Rewrite a pose file with `samples` copies of one tracking frame."""
    document = json.loads(data)
    first = document["samples"][0]
    start = first["host_ns"]
    step = int(seconds * 1_000_000_000) // (samples - 1)
    rewritten = []
    for index in range(samples):
        sample = json.loads(json.dumps(first))
        sample["host_ns"] = start + index * step
        sample["tracking"] = {
            "v": 1,
            "time_ns": start + index * step,
            "host_time_ns": start + index * step,
            "head": {"pose": {"t": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]}},
        }
        rewritten.append(sample)
    document["samples"] = rewritten
    return json.dumps(document).encode()


def set_pose_span(seconds):
    """Rewrite the fixture's pose file so its samples span `seconds`."""
    def edit(rel, data):
        if rel != "pose_capture_androidXr_left_still.json":
            return data
        return pose_span(data, seconds)
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

    def bundle(self, drop=(), edit=None, rename=None):
        return build_bundle(
            os.path.join(self._tmp.name, "wmr-ext-20261030.zip"),
            drop=drop, edit=edit, rename=rename)

    def test_missing_manifest_exits_nonzero_naming_it(self):
        code, out, err = run_main(self.bundle(drop=("manifest.md",)))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("manifest.md", err)

    def test_missing_captures_and_poses_exits_nonzero_naming_them(self):
        code, out, err = run_main(self.bundle(
            drop=("captures", "pose_capture_androidXr_left_still.json")))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("capture", err.lower())
        self.assertIn("pose", err.lower())

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

    def test_run_shorter_than_14_seconds_is_flagged(self):
        code, out, _err = run_main(self.bundle(edit=set_pose_span(13)))
        self.assertEqual(code, 0)
        self.assertIn("run is 13.0 s, shorter than 14 s", out)

    def test_run_at_14_seconds_passes(self):
        code, out, _err = run_main(self.bundle(edit=set_pose_span(14)))
        self.assertEqual(code, 0)
        self.assertNotIn("shorter than 14 s", out)
        self.assertIn("Problems: none", out)

    def test_repeated_tracking_frames_are_flagged(self):
        def repeated(rel, data):
            if rel != "pose_capture_androidXr_left_still.json":
                return data
            return repeated_pose(data, 4, 15)

        code, out, _err = run_main(self.bundle(edit=repeated))
        self.assertEqual(code, 0)
        self.assertIn(
            "4 samples over 15.0 s (1 distinct tracking frame)", out)
        self.assertIn(
            "3 of 4 samples repeat the preceding tracking frame", out)

    def test_one_sample_run_is_flagged_as_short(self):
        def one_sample(rel, data):
            if rel != "pose_capture_androidXr_left_still.json":
                return data
            document = json.loads(data)
            document["samples"] = document["samples"][:1]
            return json.dumps(document).encode()

        code, out, _err = run_main(self.bundle(edit=one_sample))
        self.assertEqual(code, 0)
        self.assertIn("run is 0.0 s, shorter than 14 s", out)
        self.assertNotIn("no readable host_ns timestamps", out)

    def test_occlusion_run_shorter_than_14_seconds_is_not_flagged(self):
        old = "pose_capture_androidXr_left_still.json"
        new = "pose_capture_androidXr_left_occlusion.json"

        def occlusion(rel, data):
            if rel == "diagnostics.json":
                document = json.loads(data)
                document["pose_captures"][0].update(
                    file=new, segment="occlusion")
                return json.dumps(document).encode()
            return set_pose_span(13)(rel, data)

        bundle = self.bundle(edit=occlusion, rename={old: new})
        with zipfile.ZipFile(bundle) as archive:
            samples = json.loads(archive.read(new))["samples"]
        span_ns = samples[-1]["host_ns"] - samples[0]["host_ns"]
        self.assertLess(span_ns, 14_000_000_000)

        code, out, _err = run_main(bundle)
        self.assertEqual(code, 0)
        self.assertNotIn("shorter than 14 s", out)
        self.assertIn("Problems: none", out)


class SixRunPoseTest(unittest.TestCase):
    """Option A: six runs, one per motion, alternating hands."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wmr-triage-test-")
        self.addCleanup(self._tmp.cleanup)
        self.zip_path = os.path.join(self._tmp.name, "wmr-ext-20261030.zip")

    def bundle(self):
        source_rel = "pose_capture_androidXr_left_still.json"
        data = (FIXTURE / source_rel).read_bytes()
        runs = (
            ("left", "still", 15),
            ("right", "drifting", 15),
            ("left", "slow_pan", 18),
            ("right", "medium_pan", 15),
            ("left", "fast_flick", 20),
            ("right", "occlusion", 15),
        )
        captures, extra = [], {}
        for hand, segment, seconds in runs:
            name = "pose_capture_androidXr_%s_%s.json" % (hand, segment)
            if name != source_rel:
                extra[name] = pose_span(data, seconds)
            captures.append({
                "file": name,
                "runtime": "androidXr",
                "hand": hand,
                "segment": segment,
                "run": 1,
            })

        def edit(rel, content):
            if rel == source_rel:
                return pose_span(content, 15)
            if rel != "diagnostics.json":
                return content
            document = json.loads(content)
            document["pose_captures"] = captures
            return json.dumps(document).encode()

        return build_bundle(self.zip_path, edit=edit, extra=extra)

    def test_six_single_runs_are_accepted(self):
        code, out, err = run_main(self.bundle())
        self.assertEqual(code, 0, err)
        self.assertIn(
            "Pose captures: 6 listed in diagnostics.json, 6 file(s)", out)
        self.assertIn("pose_capture_androidXr_left_slow_pan.json", out)
        self.assertIn("2 samples over 18.0 s", out)
        self.assertIn("2 samples over 20.0 s", out)
        self.assertNotIn("does not match runtime/hand/segment", out)
        self.assertNotIn("listed more than once", out)
        self.assertNotIn("missing from the bundle", out)
        self.assertNotIn("shorter than 14 s", out)
        self.assertIn("Problems: none", out)


class SixDofOnlyBundleTest(unittest.TestCase):
    """A 6DoF-only bench has poses, but no captures, bridge or latency run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wmr-triage-test-")
        self.addCleanup(self._tmp.cleanup)
        self.zip_path = os.path.join(self._tmp.name, "wmr-ext-20261030.zip")

    def bundle(self, drop=("captures", "latency")):
        steps = [
            {"id": "device_summary", "status": "done"},
            {"id": "connect", "status": "skipped"},
        ]

        def edit(rel, data):
            if rel == "manifest.md":
                return (data
                        .replace(b"- Bridges: 1", b"- Bridges: 0")
                        .replace(b"- Bench mode: 3dof_only",
                                 b"- Bench mode: 6dof_only"))
            if rel == "diagnostics.json":
                document = json.loads(data)
                document["bridges"] = []
                document["bench_mode"] = "6dof_only"
                document["bench"] = {"mode": "6dof_only", "steps": steps}
                return json.dumps(document).encode()
            return set_pose_span(15)(rel, data)

        return build_bundle(self.zip_path, drop=drop, edit=edit)

    def test_pose_only_bundle_is_accepted(self):
        code, out, err = run_main(self.bundle())
        self.assertEqual(code, 0, err)
        self.assertNotIn("nothing to analyse", err)
        for expected in (
            "Bench: 6dof_only, runtime androidXr",
            "Bench steps: 1 done, 1 skipped, 0 failed (2)",
            "Bridges: 0",
            "Captures: 0",
            "pose_capture_androidXr_left_still.json  androidXr/left/still",
            "Problems: none",
        ):
            self.assertIn(expected, out)
        self.assertNotIn("lists no bridge", out)
        self.assertNotIn("no latency/", out)

    def test_pose_only_bundle_without_poses_is_rejected(self):
        bundle = self.bundle(
            drop=("captures", "latency",
                  "pose_capture_androidXr_left_still.json"))
        code, out, err = run_main(bundle)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no pose captures", err)


class MultiRunPoseTest(unittest.TestCase):
    """Run 1 keeps the short name; runs 2..4 append a `_runN` suffix."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wmr-triage-test-")
        self.addCleanup(self._tmp.cleanup)
        self.zip_path = os.path.join(self._tmp.name, "wmr-ext-20261030.zip")

    def bundle(self, runs):
        source_rel = "pose_capture_androidXr_left_still.json"
        data = (FIXTURE / source_rel).read_bytes()
        extra = {}
        captures = []
        for run in runs:
            name = "pose_capture_androidXr_left_still%s.json" % (
                "_run%d" % run if run > 1 else "")
            if run > 1:
                extra[name] = data
            captures.append({
                "file": name,
                "runtime": "androidXr",
                "hand": "left",
                "segment": "still",
                "run": run,
            })

        def edit(rel, content):
            if rel != "diagnostics.json":
                return content
            document = json.loads(content)
            document["pose_captures"] = captures
            return json.dumps(document).encode()

        return build_bundle(self.zip_path, edit=edit, extra=extra)

    def test_runs_two_to_four_are_not_filename_mismatches(self):
        code, out, err = run_main(self.bundle((1, 2, 3, 4)))
        self.assertEqual(code, 0, err)
        self.assertNotIn("does not match runtime/hand/segment", out)
        for run in (2, 3, 4):
            self.assertIn(
                "pose_capture_androidXr_left_still_run%d.json  "
                "androidXr/left/still" % run, out)
        self.assertIn("Problems: none", out)

    def test_pose_segment_strips_run_suffix(self):
        self.assertEqual(
            triage_bundle._pose_segment(
                "pose_capture_viture_left_slow_pan_run2.json"), "slow_pan")
        self.assertEqual(
            triage_bundle._pose_segment(
                "pose_capture_xreal_right_occlusion_run4.json"), "occlusion")
        self.assertIsNone(triage_bundle._pose_segment("not-a-pose-capture.txt"))


if __name__ == "__main__":
    unittest.main()
