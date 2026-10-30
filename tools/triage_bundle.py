#!/usr/bin/env python3
"""Triage a WMR external test bundle (`wmr-ext-*.zip`) from the companion app.

Unpacks the zip, reads `manifest.md` and `diagnostics.json`, dispatches every
capture under `captures/` to `tools/analyze_capture.py`, and prints one report:
app, firmware, MTU and stream facts, per-hand rates and loss, sequence gaps,
the 6DoF pose-file inventory, and every missing or inconsistent file.

A bundle without a manifest, without diagnostics, or without a capture exits
nonzero and names the problem. Everything else is reported and exits zero.

Standard library only. Run from anywhere:

    python3 tools/triage_bundle.py wmr-ext-20261030.zip
"""

import argparse
import json
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = "captures/"
POSE_PREFIX = "pose_capture_"
POSE_FORMAT = "wmr-pose-recording"


class BundleError(Exception):
    """A bundle that cannot be triaged at all."""


def parse_manifest(text):
    """`- Key: value` lines of manifest.md, last one wins."""
    meta = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and ": " in line:
            key, value = line[2:].split(": ", 1)
            meta[key.strip()] = value.strip()
    return meta


def _safe_members(zf, destination):
    root = os.path.realpath(destination)
    members = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        target = os.path.realpath(os.path.join(root, info.filename))
        if target != root and not target.startswith(root + os.sep):
            raise BundleError("zip member escapes the bundle: %s" % info.filename)
        members.append(info)
    return members


def _extract(zip_path, destination):
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(destination, members=_safe_members(zf, destination))


def _load_diagnostics(path):
    try:
        with open(path, "r") as fp:
            data = json.load(fp)
    except (OSError, ValueError) as exc:
        raise BundleError("diagnostics.json is unreadable: %s" % exc)
    if not isinstance(data, dict):
        raise BundleError("diagnostics.json is not a JSON object")
    return data


def _analyser():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import analyze_capture
    return analyze_capture


def _hand_index(meta, name):
    """0 = LEFT, 1 = RIGHT, from the capture header or the file name."""
    recorded = (meta.get("hand") or "").strip().upper()
    for index, label in ((0, "LEFT"), (1, "RIGHT")):
        if recorded.startswith(label):
            return index, label
    lowered = name.lower()
    if "left" in lowered:
        return 0, "LEFT"
    if "right" in lowered:
        return 1, "RIGHT"
    return None, "unknown"


def _firmware_text(firmware):
    if not isinstance(firmware, dict):
        return "not reported"
    if firmware.get("malformed"):
        return "malformed (FF16 present)"
    if firmware.get("version"):
        return str(firmware["version"])
    if firmware.get("unavailable"):
        return "unavailable (discovery failed)"
    if firmware.get("feature_present"):
        return "feature present, no value"
    return "not read"


def _number(value):
    if value is None:
        return "?"
    if isinstance(value, (int, float)):
        return ("%.1f" % value) if isinstance(value, float) else str(value)
    return str(value)


def _hand_streams(stream):
    rates = stream.get("rate_hz") or []
    losses = stream.get("loss") or []
    parts = []
    for index, label in ((0, "LEFT"), (1, "RIGHT")):
        rate = rates[index] if index < len(rates) else None
        loss = losses[index] if index < len(losses) else None
        rate_text = "%s Hz" % _number(rate)
        loss_text = "?" if loss is None else "%.1f%%" % (100.0 * loss)
        parts.append("%s %s, loss %s" % (label, rate_text, loss_text))
    return ", ".join(parts)


def _capture_section(path, name, problems):
    """Analyse one capture. Returns report lines."""
    analyser = _analyser()
    meta = analyser.read_meta(path)
    hand, label = _hand_index(meta, name)
    lines = ["  %s  hand %s  mtu %s"
             % (name, label, meta.get("mtu", "not recorded"))]
    if hand is None:
        problems.append("%s: hand is not recorded in the header or file name; "
                        "not analysed" % name)
        return lines
    try:
        report = analyser.analyse(path, hand)
    except Exception as exc:                     # noqa: BLE001 - report, do not crash
        problems.append("%s: analysis failed: %s" % (name, exc))
        return lines
    for problem in report.get("problems", [])[:3]:
        problems.append("%s: %s" % (name, problem))
    still = report.get("still") or {}
    if "error" in still:
        lines.append("    still: %s" % still["error"])
    elif still:
        lines.append(
            "    still: %d frames over %.2f s (%.1f Hz), %d sequence gaps "
            "(%.1f%% of produced frames lost)"
            % (still["frames"], still["wall_seconds"], still.get("rate_hz") or 0,
               still.get("seq_gaps", 0), 100.0 * still.get("loss_fraction", 0.0)))
    else:
        lines.append("    still: phase missing")
    for rotation, data in report.get("rotations", {}).items():
        if "error" in data:
            lines.append("    %s: %s" % (rotation, data["error"]))
        else:
            lines.append(
                "    %s: %d frames, %d gaps >100 ms, %d sequence gaps "
                "(%.1f%% lost)"
                % (rotation, data["frames"], data.get("gaps_over_100ms", 0),
                   data.get("seq_gaps", 0), 100.0 * data.get("loss_fraction", 0.0)))
    return lines


def _pose_section(directory, diagnostics, problems):
    """Rows for every pose file listed or present, plus named inconsistencies."""
    listed = diagnostics.get("pose_captures") or []
    listed = [entry for entry in listed if isinstance(entry, dict)]
    on_disk = sorted(
        name for name in os.listdir(directory)
        if name.startswith(POSE_PREFIX) and name.endswith(".json"))
    lines = ["Pose captures: %d listed in diagnostics.json, %d file(s) in the bundle"
             % (len(listed), len(on_disk))]
    by_file = {}
    for entry in listed:
        base = os.path.basename(str(entry.get("file", "")))
        if not base:
            problems.append("pose_captures entry has no file name: %r" % entry)
            continue
        if base in by_file:
            problems.append("%s: listed more than once in diagnostics.json" % base)
            continue
        by_file[base] = entry
        runtime = entry.get("runtime")
        hand = entry.get("hand")
        segment = entry.get("segment")
        expected = "%s%s_%s_%s.json" % (POSE_PREFIX, runtime, hand, segment)
        if base != expected:
            problems.append("%s: name does not match runtime/hand/segment "
                            "(expected %s)" % (base, expected))
        path = os.path.join(directory, base)
        if not os.path.isfile(path):
            problems.append("%s: listed in diagnostics.json but missing from the "
                            "bundle" % base)
            continue
        lines.append("  %s  %s/%s/%s  %s"
                     % (base, runtime, hand, segment, _pose_file_note(path, problems)))
    for base in on_disk:
        if base in by_file:
            continue
        problems.append("%s: present in the bundle but not listed in "
                        "diagnostics.json" % base)
        lines.append("  %s  (not listed)  %s" % (base, _pose_file_note(
            os.path.join(directory, base), problems)))
    return lines


def _pose_file_note(path, problems):
    try:
        with open(path, "r") as fp:
            data = json.load(fp)
    except (OSError, ValueError) as exc:
        problems.append("%s: pose file is unreadable: %s"
                        % (os.path.basename(path), exc))
        return "unreadable"
    if not isinstance(data, dict) or data.get("format") != POSE_FORMAT:
        problems.append("%s: not a %s document"
                        % (os.path.basename(path), POSE_FORMAT))
        return "bad format"
    samples = data.get("samples")
    if not isinstance(samples, list):
        problems.append("%s: samples is not a list" % os.path.basename(path))
        return "bad samples"
    if not samples:
        problems.append("%s: no pose samples" % os.path.basename(path))
    return "%d samples" % len(samples)


def _manifest_checks(meta, diagnostics, problems):
    app = diagnostics.get("app") or {}
    version = app.get("version")
    manifest_app = meta.get("App")
    if version and manifest_app and not manifest_app.endswith(str(version)):
        problems.append("manifest App %r disagrees with diagnostics.json "
                        "version %r" % (manifest_app, version))
    bridges = diagnostics.get("bridges") or []
    manifest_bridges = meta.get("Bridges")
    if manifest_bridges and manifest_bridges != str(len(bridges)):
        problems.append("manifest says %s bridge(s); diagnostics.json has %d"
                        % (manifest_bridges, len(bridges)))
    if not bridges:
        problems.append("diagnostics.json lists no bridge (nothing was connected)"
                        " at export")


def triage(zip_path):
    """Return (report_lines, problems). Raises BundleError for a fatal bundle."""
    if not os.path.isfile(zip_path):
        raise BundleError("%s does not exist" % zip_path)
    try:
        with tempfile.TemporaryDirectory(prefix="wmr-triage-") as work:
            try:
                _extract(zip_path, work)
            except zipfile.BadZipFile as exc:
                raise BundleError("%s is not a zip: %s" % (zip_path, exc))

            manifest_path = os.path.join(work, "manifest.md")
            diagnostics_path = os.path.join(work, "diagnostics.json")
            if not os.path.isfile(manifest_path):
                raise BundleError("manifest.md is missing from the bundle")
            if not os.path.isfile(diagnostics_path):
                raise BundleError("diagnostics.json is missing from the bundle")

            with open(manifest_path, "r") as fp:
                meta = parse_manifest(fp.read())
            if not meta:
                raise BundleError("manifest.md has no '- key: value' lines")
            diagnostics = _load_diagnostics(diagnostics_path)

            captures = sorted(
                name for name in os.listdir(os.path.join(work, "captures"))
                if name.endswith(".txt")
            ) if os.path.isdir(os.path.join(work, "captures")) else []
            if not captures:
                raise BundleError("no capture files under captures/ "
                                  "(nothing to analyse)")

            problems = []
            lines = ["Bundle: %s" % zip_path,
                     "Diagnostics: %s, generated %s"
                     % (diagnostics.get("format", "unknown"),
                        diagnostics.get("generated_at", "unknown"))]
            app = diagnostics.get("app") or {}
            lines.append("App: %s %s (%s)"
                         % (app.get("name", "unknown"), app.get("version", "unknown"),
                            app.get("commit", "unknown")))
            device = diagnostics.get("device") or {}
            lines.append("Device: %s / %s"
                         % (device.get("phone", "unknown"),
                            device.get("android", "unknown")))
            if meta:
                lines.append("Manifest: %d field(s), date %s"
                             % (len(meta), meta.get("Date", "unknown")))
            _manifest_checks(meta, diagnostics, problems)

            bridges = [b for b in diagnostics.get("bridges") or []
                       if isinstance(b, dict)]
            lines.append("")
            lines.append("Bridges: %d" % len(bridges))
            mtu_values = []
            for index, bridge in enumerate(bridges, 1):
                link = bridge.get("link") or {}
                stream = bridge.get("stream") or {}
                mtu = bridge.get("mtu")
                if mtu is not None:
                    mtu_values.append(mtu)
                lines.append("  %s %s" % (bridge.get("id", "unknown"),
                                          "(%s)" % bridge["transport"]
                                          if bridge.get("transport") else ""))
                lines.append("    firmware %s, MTU %s, link %s, rssi %s dBm"
                             % (_firmware_text(bridge.get("firmware")),
                                _number(mtu), link.get("state", "unknown"),
                                _number(link.get("rssi_dbm"))))
                lines.append("    stream %s: %s"
                             % (stream.get("mode", "none"),
                                _hand_streams(stream)))

            lines.append("")
            lines.append("Captures: %d" % len(captures))
            for name in captures:
                path = os.path.join(work, "captures", name)
                lines.extend(_capture_section(
                    path, CAPTURE_DIR + name, problems))
                analyser = _analyser()
                capture_mtu = analyser.read_meta(path).get("mtu")
                if (len(mtu_values) == 1 and capture_mtu
                        and capture_mtu.isdigit() and capture_mtu != str(mtu_values[0])):
                    problems.append("%s: capture MTU %s disagrees with "
                                    "diagnostics.json MTU %s"
                                    % (CAPTURE_DIR + name, capture_mtu, mtu_values[0]))

            lines.append("")
            lines.extend(_pose_section(work, diagnostics, problems))

            if not os.path.isfile(os.path.join(work, "README.md")):
                problems.append("README.md is missing from the bundle")
            if not os.path.isdir(os.path.join(work, "latency")):
                problems.append("no latency/ files (no latency run was saved)")

            lines.append("")
            if problems:
                lines.append("Problems: %d" % len(problems))
                for problem in problems:
                    lines.append("  - %s" % problem)
            else:
                lines.append("Problems: none")
            return lines, problems
    except OSError as exc:
        raise BundleError("cannot read the bundle: %s" % exc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bundle", help="wmr-ext-YYYYMMDD.zip from the companion app")
    args = ap.parse_args(argv)
    try:
        lines, _problems = triage(args.bundle)
    except BundleError as exc:
        print("triage_bundle: %s" % exc, file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
