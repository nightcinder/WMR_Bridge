#!/usr/bin/env python3
"""Triage a WMR external test bundle (`wmr-ext-*.zip`) from the companion app.

Unpacks the zip, reads `manifest.md`, `diagnostics.json` and (when the
companion exported one) `device_environment.json`, dispatches every capture
under `captures/` to `tools/analyze_capture.py`, and prints one report: app,
firmware, MTU and stream facts, per-hand rates and loss, sequence gaps, the
USB/display fingerprint the host reported, per-cell speed-gate verdicts, the
6DoF pose-file inventory, and every missing or inconsistent file.

A bundle without a manifest, without diagnostics, or without any capture or
pose file exits nonzero and names the problem. Everything else is reported and
exits zero. A 6DoF-only bench bundle has no `captures/`, no bridge and no
latency run; its six pose recordings are the analysable data.

Standard library only. Run from anywhere:

    python3 tools/triage_bundle.py wmr-ext-20261030.zip
"""

import argparse
import json
import os
import re
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = "captures/"
POSE_PREFIX = "pose_capture_"
POSE_FORMAT = "wmr-pose-recording"
POSE_NAME = re.compile(
    r"^pose_capture_([A-Za-z0-9]+)_(left|right)_(.+?)(?:_run(\d+))?\.json$")
# The guided bench timer stops at 15 s; accept the boundary with tolerance.
minimumRunSeconds = 14

DEVICE_ENVIRONMENT_FILE = "device_environment.json"
DEVICE_ENVIRONMENT_FORMAT = "wmr-device-environment/1"

# Vendors the companion already recognizes. Any other vendor ID is a new
# fingerprint, not a failure: it is reported so it can be added to the table.
USB_VENDOR_NAMES = {
    0x35CA: "VITURE",
    0x3318: "XREAL",
}

# Base USB class codes from the USB-IF class-code table. An unlisted code is
# still reported verbatim.
USB_CLASS_NAMES = {
    0x00: "per-interface",
    0x01: "audio",
    0x02: "CDC communications",
    0x03: "HID",
    0x05: "physical",
    0x06: "image",
    0x07: "printer",
    0x08: "mass storage",
    0x09: "hub",
    0x0A: "CDC data",
    0x0B: "smart card",
    0x0D: "content security",
    0x0E: "video",
    0x0F: "personal healthcare",
    0x10: "audio/video",
    0x11: "billboard",
    0x12: "USB type-C bridge",
    0x13: "bulk display",
    0xDC: "diagnostic",
    0xE0: "wireless",
    0xEF: "miscellaneous",
    0xFE: "application specific",
    0xFF: "vendor specific",
}

SPEED_GATE_KEYS = ("speed_gates", "pose_speed_gates", "speed-gates")
SPEED_GATE_VERDICTS = ("pass", "warn", "fail", "skipped", "unknown")


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


def _int_value(value):
    """An integer from a JSON number or a hexadecimal ID string."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip(), 16)
        except ValueError:
            return None
    return None


def _hex_id(value):
    return "unknown" if value is None else "%04x" % value


def _byte_hex(value):
    return "??" if value is None else "%02x" % value


def _usb_id_text(vendor, product):
    return "%s:%s" % (_hex_id(vendor), _hex_id(product))


def _usb_names(device):
    """`(manufacturer product)` text for one USB device, without duplicates."""
    parts = []
    for key in ("manufacturer", "product"):
        value = device.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return " ".join(parts)


def _device_environment_section(work, problems):
    """USB, display and XR fingerprint from `device_environment.json`."""
    path = os.path.join(work, DEVICE_ENVIRONMENT_FILE)
    if not os.path.isfile(path):
        problems.append("%s is missing from the bundle; no USB or display "
                        "fingerprint was captured" % DEVICE_ENVIRONMENT_FILE)
        return ["Device environment: not exported"]
    try:
        with open(path, "r") as fp:
            data = json.load(fp)
    except (OSError, ValueError) as exc:
        problems.append("%s is unreadable: %s" % (DEVICE_ENVIRONMENT_FILE, exc))
        return ["Device environment: unreadable"]
    if not isinstance(data, dict):
        problems.append("%s is not a JSON object" % DEVICE_ENVIRONMENT_FILE)
        return ["Device environment: unreadable"]

    status = str(data.get("status") or "ok")
    lines = ["Device environment: %s, status %s, captured %s"
             % (data.get("format", "unknown"), status,
                data.get("captured_at", "unknown"))]
    if status != "ok":
        detail = data.get("detail") or data.get("reason")
        problems.append("device environment probe %s%s"
                        % (status, ": %s" % detail if detail else ""))

    usb = data.get("usb") if isinstance(data.get("usb"), dict) else {}
    devices = [device for device in usb.get("devices") or []
               if isinstance(device, dict)]
    available = usb.get("host_available") is True
    lines.append("  USB: host %s, %d device(s)"
                 % ("available" if available else "not available", len(devices)))
    if not available:
        problems.append("device environment: USB host is not available, so no "
                        "VID/PID fingerprint was captured")
    for device in devices:
        vendor = _int_value(device.get("vendor_id"))
        product = _int_value(device.get("product_id"))
        usb_id = _usb_id_text(vendor, product)
        vendor_name = USB_VENDOR_NAMES.get(vendor)
        names = _usb_names(device)
        lines.append("    %s  %s%s"
                     % (usb_id, vendor_name or "unknown vendor",
                        "  %s" % names if names else ""))
        if vendor not in USB_VENDOR_NAMES:
            problems.append(
                "device environment: unrecognized USB device %s%s; add its "
                "vendor ID to the glasses table once the model is known"
                % (usb_id, " (%s)" % names if names else ""))
        for interface in device.get("interfaces") or []:
            if not isinstance(interface, dict):
                continue
            base = _int_value(interface.get("class"))
            sub = _int_value(interface.get("subclass"))
            protocol = _int_value(interface.get("protocol"))
            lines.append("      interface %s/%s/%s %s"
                         % (_byte_hex(base), _byte_hex(sub),
                            _byte_hex(protocol),
                            USB_CLASS_NAMES.get(base, "unknown class")))
            if base not in USB_CLASS_NAMES:
                problems.append(
                    "device environment: unrecognized USB interface class %s "
                    "on %s" % (_byte_hex(base), usb_id))

    displays = data.get("displays")
    declared = None
    if isinstance(displays, dict):
        declared = displays.get("external_count")
        items = [item for item in displays.get("items") or []
                 if isinstance(item, dict)]
    else:
        items = [item for item in displays or [] if isinstance(item, dict)]
    external = [item for item in items
                if item.get("external") is True or item.get("type") == "external"]
    lines.append("  Displays: %d external of %d"
                 % (len(external), len(items)))
    if isinstance(declared, int) and declared != len(external):
        problems.append("device environment: external_count %d disagrees with "
                        "%d external display item(s)"
                        % (declared, len(external)))
    for item in items:
        size = ""
        if item.get("width") is not None and item.get("height") is not None:
            size = " %sx%s" % (item["width"], item["height"])
        refresh = item.get("refresh_rate_hz")
        rate = " @ %s Hz" % _number(refresh) if refresh is not None else ""
        lines.append("    display %s %s%s%s%s"
                     % (item.get("id", "?"), item.get("type", "unknown"),
                        size, rate,
                        "  %s" % item["name"] if item.get("name") else ""))

    features = [feature for feature in data.get("xr_features") or []
                if isinstance(feature, dict)]
    if features:
        present = [str(feature.get("name", "unknown")) for feature in features
                   if feature.get("present") is True]
        absent = [str(feature.get("name", "unknown")) for feature in features
                  if feature.get("present") is not True]
        lines.append("  XR features: %d present, %d absent"
                     % (len(present), len(absent)))
        for name in present:
            lines.append("    + %s" % name)
        for name in absent:
            lines.append("    - %s" % name)
    return lines


def _gate_verdict(value):
    """`(verdict, detail)` from a manifest or diagnostics speed-gate value."""
    if isinstance(value, bool):
        return ("pass" if value else "fail"), ""
    detail = ""
    raw = value
    if isinstance(value, dict):
        for key in ("detail", "reason", "note", "speed", "speed_cm_s"):
            if value.get(key) not in (None, ""):
                detail = str(value[key])
                break
        raw = ""
        for key in ("verdict", "status", "result", "passed", "pass"):
            if key in value:
                raw = value[key]
                break
    if isinstance(raw, bool):
        return ("pass" if raw else "fail"), detail
    text = str(raw).strip()
    lowered = text.lower()
    if "fail" in lowered or "out of band" in lowered or "out-of-band" in lowered:
        verdict = "fail"
    elif "warn" in lowered:
        verdict = "warn"
    elif "pass" in lowered or lowered in ("ok", "true"):
        verdict = "pass"
    elif "skip" in lowered or lowered in ("", "none", "-", "—", "unknown"):
        verdict = "skipped"
    else:
        verdict = "unknown"
    if not detail:
        detail = re.sub(
            r"^(passed|pass|warning|warn|failed|fail|skipped|skip|ok)\b"
            r"[\s:,-]*", "", text, flags=re.IGNORECASE).strip()
    return verdict, detail


def _speed_gate_rows(manifest_text):
    """Rows from any markdown table that has a Speed gate column."""
    rows = []
    header = None
    for line in manifest_text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            header = None
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if header is None:
            header = [cell.lower() for cell in cells]
            continue
        if all(cell and set(cell) <= set("-: ") for cell in cells):
            continue
        gate_index = None
        for index, name in enumerate(header):
            if "speed" in name and "gate" in name:
                gate_index = index
                break
        if gate_index is None or gate_index >= len(cells):
            continue
        values = dict(zip(header, cells))
        cell = ""
        for key in ("cell", "name", "file"):
            if values.get(key) not in (None, "", "-", "—"):
                cell = os.path.basename(values[key])
                break
        if not cell:
            parts = [values[key] for key in ("hand", "segment", "run")
                     if values.get(key) not in (None, "", "-", "—")]
            cell = " ".join(parts) or "row %d" % (len(rows) + 1)
        rows.append((cell, cells[gate_index]))
    return rows


def _speed_gate_bullets(manifest_text):
    """`cell: value` bullets under a heading that names speed gates."""
    rows = []
    in_section = False
    for line in manifest_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            in_section = "speed" in heading and "gate" in heading
            continue
        if not in_section:
            continue
        match = re.match(r"^[-*]\s+([^:]+):\s*(.+)$", stripped)
        if not match:
            continue
        cell = match.group(1).strip()
        if re.match(r"speed[- ]gate\b", cell, re.IGNORECASE):
            # The explicit `- Speed gate <cell>: value` form owns these.
            continue
        rows.append((cell, match.group(2)))
    return rows


def _speed_gate_entries(manifest_text, diagnostics):
    """Cell -> `(verdict, detail)` from the manifest, then diagnostics.json."""
    entries = {}

    def add(cell, value):
        cell = str(cell).strip()
        if not cell or cell in entries:
            return
        verdict, detail = _gate_verdict(value)
        entries[cell] = (verdict, detail)

    for cell, value in _speed_gate_rows(manifest_text):
        add(cell, value)
    for cell, value in _speed_gate_bullets(manifest_text):
        add(cell, value)
    for line in manifest_text.splitlines():
        match = re.match(r"^\s*-\s*speed[- ]gate\s*:\s*(.+)$", line,
                         re.IGNORECASE)
        if match:
            add("", match.group(1))
            continue
        match = re.match(r"^\s*-\s*speed[- ]gate\s+(.+?)\s*:\s+(.+)$",
                         line, re.IGNORECASE)
        if match:
            add(match.group(1), match.group(2))
    for key in SPEED_GATE_KEYS:
        value = diagnostics.get(key)
        if isinstance(value, dict):
            for cell, raw in value.items():
                add(cell, raw)
        elif isinstance(value, list):
            for index, raw in enumerate(value, 1):
                if not isinstance(raw, dict):
                    continue
                cell = None
                for name in ("cell", "id", "name", "segment", "file"):
                    if raw.get(name) not in (None, ""):
                        cell = os.path.basename(str(raw[name]))
                        break
                add(cell or "cell %d" % index, raw)
    return entries


def _speed_gate_section(manifest_text, diagnostics, problems):
    """One line per cell plus a verdict summary; warn and fail are problems."""
    entries = _speed_gate_entries(manifest_text, diagnostics)
    if not entries:
        return ["Speed gates: not recorded"]
    counts = {verdict: 0 for verdict in SPEED_GATE_VERDICTS}
    for verdict, _detail in entries.values():
        counts[verdict] = counts.get(verdict, 0) + 1
    lines = ["Speed gates: %d cell(s), %d pass, %d warn, %d fail, %d skipped, "
             "%d unknown"
             % (len(entries), counts["pass"], counts["warn"], counts["fail"],
                counts["skipped"], counts["unknown"])]
    for cell, (verdict, detail) in entries.items():
        lines.append("  %s: %s%s"
                     % (cell, verdict, " — %s" % detail if detail else ""))
        if verdict in ("warn", "fail"):
            problems.append("speed gate %s: %s%s"
                            % (cell, verdict,
                               " — %s" % detail if detail else ""))
    return lines


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
        run = entry.get("run", 1)
        run_suffix = "_run%d" % run if isinstance(run, int) and run > 1 else ""
        expected = "%s%s_%s_%s%s.json" % (
            POSE_PREFIX, runtime, hand, segment, run_suffix)
        if base != expected:
            problems.append("%s: name does not match runtime/hand/segment "
                            "(expected %s)" % (base, expected))
        path = os.path.join(directory, base)
        if not os.path.isfile(path):
            problems.append("%s: listed in diagnostics.json but missing from the "
                            "bundle" % base)
            continue
        lines.append("  %s  %s/%s/%s  %s"
                     % (base, runtime, hand, segment,
                        _pose_file_note(path, problems, segment)))
    for base in on_disk:
        if base in by_file:
            continue
        problems.append("%s: present in the bundle but not listed in "
                        "diagnostics.json" % base)
        lines.append("  %s  (not listed)  %s" % (base, _pose_file_note(
            os.path.join(directory, base), problems, _pose_segment(base))))
    return lines


def _pose_segment(name):
    """Motion segment from `pose_capture_<runtime>_<hand>_<segment>[_runN].json`."""
    match = POSE_NAME.match(name)
    return match.group(3) if match else None


def _pose_run_seconds(samples):
    """Seconds between the first and last sample, or None if unreadable."""
    if not samples:
        return None
    try:
        first = samples[0]["host_ns"]
        last = samples[-1]["host_ns"]
    except (KeyError, TypeError):
        return None
    if not isinstance(first, int) or not isinstance(last, int):
        return None
    return (last - first) / 1e9


def _distinct_tracking_frames(samples, hand=None):
    """Count runs of identical tracking content, ignoring timestamps.

    Only the recorded hand is compared: the other hand still moves and would
    hide a repeated anchor frame; when the recorded hand is absent, the head
    frame is the fallback. Returns None when the samples carry no hand or head
    content, so a synthetic fixture without a real tracking frame is not
    flagged.
    """
    def strip(value):
        if isinstance(value, dict):
            return {key: strip(item) for key, item in value.items()
                    if key not in ("time_ns", "host_time_ns")}
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    last = object()
    distinct = 0
    for sample in samples:
        tracking = sample.get("tracking") if isinstance(sample, dict) else None
        if not isinstance(tracking, dict):
            return None
        content = strip(tracking)
        hands = content.get("hands") if isinstance(content, dict) else None
        head = content.get("head") if isinstance(content, dict) else None
        payload = None
        if isinstance(hands, dict):
            if hand:
                recorded = hands.get(hand)
                if recorded is not None:
                    payload = {"hand": recorded}
            else:
                payload = hands
        if payload is None and head:
            payload = {"head": head}
        if payload is None:
            return None
        signature = json.dumps(payload, sort_keys=True)
        if signature != last:
            distinct += 1
            last = signature
    return distinct


def _pose_file_note(path, problems, segment=None):
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
        return "0 samples"
    seconds = _pose_run_seconds(samples)
    if seconds is None:
        problems.append("%s: samples have no readable host_ns timestamps"
                        % os.path.basename(path))
        return "%d samples" % len(samples)
    if seconds < minimumRunSeconds and segment != "occlusion":
        problems.append("%s: run is %.1f s, shorter than %d s"
                        % (os.path.basename(path), seconds, minimumRunSeconds))
    note = "%d samples over %.1f s" % (len(samples), seconds)
    match = POSE_NAME.match(os.path.basename(path))
    distinct = _distinct_tracking_frames(samples, match.group(2) if match else None)
    if distinct is not None and distinct < len(samples):
        note += " (%d distinct tracking frame%s)" % (
            distinct, "" if distinct == 1 else "s")
        if distinct * 2 <= len(samples):
            problems.append(
                "%s: %d of %d samples repeat the preceding tracking frame; "
                "the declared anchor rate is overstated"
                % (os.path.basename(path), len(samples) - distinct, len(samples)))
    return note


def _bench_mode(meta, diagnostics):
    """Bench mode from diagnostics.json, falling back to the manifest."""
    return (diagnostics.get("bench_mode") or meta.get("Bench mode") or "").strip()


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
    if not bridges and _bench_mode(meta, diagnostics) != "6dof_only":
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
                manifest_text = fp.read()
            meta = parse_manifest(manifest_text)
            if not meta:
                raise BundleError("manifest.md has no '- key: value' lines")
            diagnostics = _load_diagnostics(diagnostics_path)

            captures = sorted(
                name for name in os.listdir(os.path.join(work, "captures"))
                if name.endswith(".txt")
            ) if os.path.isdir(os.path.join(work, "captures")) else []
            poses = sorted(
                name for name in os.listdir(work)
                if name.startswith(POSE_PREFIX) and name.endswith(".json"))
            if not captures and not poses:
                raise BundleError("no capture files under captures/ and no "
                                  "pose captures in the bundle (nothing to "
                                  "analyse)")

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
            lines.extend(_device_environment_section(work, problems))
            if meta:
                lines.append("Manifest: %d field(s), date %s"
                             % (len(meta), meta.get("Date", "unknown")))
            bench_mode = _bench_mode(meta, diagnostics)
            if bench_mode:
                runtime = diagnostics.get("tracking_runtime")
                lines.append("Bench: %s%s"
                             % (bench_mode,
                                ", runtime %s" % runtime if runtime else ""))
            steps = (diagnostics.get("bench") or {}).get("steps")
            if isinstance(steps, list) and steps:
                statuses = [step.get("status") for step in steps
                            if isinstance(step, dict)]
                lines.append("Bench steps: %d done, %d skipped, %d failed (%d)"
                             % (statuses.count("done"),
                                statuses.count("skipped"),
                                statuses.count("failed"), len(steps)))
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
            lines.append("")
            lines.extend(_speed_gate_section(
                manifest_text, diagnostics, problems))

            if not os.path.isfile(os.path.join(work, "README.md")):
                problems.append("README.md is missing from the bundle")
            if not os.path.isdir(os.path.join(work, "latency")) and (
                    bench_mode != "6dof_only"):
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
