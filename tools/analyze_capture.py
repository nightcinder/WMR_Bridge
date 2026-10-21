#!/usr/bin/env python3
"""Analyse a rotation capture (see docs/DECODING.md) and report gyro axes and signs.

Input: a capture file from tools/capture_ble.py with the phases
still, twist_cw, flip_away, lean_right.

Reports, for the chosen hand:
  * still: rate, sequence gaps, gyro bias/noise, gravity vector in sensor axes,
    tick unit check (ticks vs wall clock), and a per-second view of the start of
    the phase (the first-frame transient).
  * each rotation: net angle per sensor axis (a full turn should be +-360 degrees),
    the dominant sensor axis and sign, cross-axis leakage, and what that implies
    for the sensor axes in a right-handed world frame (X right, Y up, Z toward you).

Decoding follows src/wmr_decode.c (layout and scales from Monado, see
docs/DECODING.md). Standard library only.
"""

import argparse
import json
import math
import sys

TICK_SECONDS = 100e-9          # Monado: WMR_MOTION_CONTROLLER_NS_PER_TICK = 100
GYRO_SCALE = 1e-5              # rad/s per raw count
ACCEL_SCALE = 1.0 / 49000.0    # m/s^2 per raw count
FRAME_LENGTH = 46
AXES = "XYZ"

# phase -> (world axis letter, expected net angle in degrees about that +axis)
# World frame is right-handed: X right, Y up, Z toward the user.
#   twist_cw   : clockwise seen from above      = -360 about +Y
#   flip_away  : top of ring moves away, over   = -360 about +X
#   lean_right : top of ring moves to your right = -360 about +Z
EXPECTED = {
    "twist_cw": ("Y", -360.0),
    "flip_away": ("X", -360.0),
    "lean_right": ("Z", -360.0),
}


def s24(b0, b1, b2):
    v = b0 | (b1 << 8) | (b2 << 16)
    return v - (1 << 24) if v & 0x800000 else v


def decode(frame):
    """Decode one 46-byte frame (A1 01 + 44 bytes). Returns a dict or None."""
    if len(frame) != FRAME_LENGTH or frame[0] != 0xA1 or frame[1] != 0x01:
        return None
    p = frame[2:]
    accel = [s24(p[8 + 3 * i], p[9 + 3 * i], p[10 + 3 * i]) * ACCEL_SCALE for i in range(3)]
    gyro = [s24(p[19 + 3 * i], p[20 + 3 * i], p[21 + 3 * i]) * GYRO_SCALE for i in range(3)]
    temp = p[17] | (p[18] << 8)
    if temp & 0x8000:
        temp -= 1 << 16
    ticks = p[28] | (p[29] << 8) | (p[30] << 16) | (p[31] << 24)
    return {"accel": accel, "gyro": gyro, "temp": temp, "battery": p[7], "ticks": ticks,
            "buttons": p[0]}


def parse_capture(path):
    """Returns {phase: [ {t, hand, seq, **decoded} ... ]} and a list of problems."""
    phases = {}
    problems = []
    current = None
    with open(path, "r") as fp:
        for n, line in enumerate(fp, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts[0] == "@begin":
                current = parts[1]
                phases.setdefault(current, [])
            elif parts[0] == "@end":
                current = None
            elif parts[0] == "F" and len(parts) == 5:
                if current is None:
                    problems.append("line %d: frame outside a phase" % n)
                    continue
                try:
                    frame = bytes.fromhex(parts[4])
                except ValueError:
                    problems.append("line %d: bad hex" % n)
                    continue
                d = decode(frame)
                if d is None:
                    problems.append("line %d: not a 46-byte A1 01 input frame" % n)
                    continue
                d.update(t=float(parts[1]), hand=int(parts[2]), seq=int(parts[3]))
                phases[current].append(d)
            else:
                problems.append("line %d: unrecognised: %s" % (n, line[:40]))
    return phases, problems


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def tick_deltas(frames):
    """Per-sample elapsed ticks, modulo 2^32 (correct across a wrap)."""
    return [(b["ticks"] - a["ticks"]) & 0xFFFFFFFF for a, b in zip(frames, frames[1:])]


def seq_gaps(frames):
    """Number of missing sequence numbers (modulo 2^32)."""
    missing = 0
    for a, b in zip(frames, frames[1:]):
        step = (b["seq"] - a["seq"]) & 0xFFFFFFFF
        if step > 1:
            missing += step - 1
    return missing


def analyse_still(frames):
    r = {"frames": len(frames)}
    if len(frames) < 10:
        r["error"] = "fewer than 10 frames"
        return r
    wall = frames[-1]["t"] - frames[0]["t"]
    dts = tick_deltas(frames)
    tick_total = sum(dts) * TICK_SECONDS
    r["wall_seconds"] = wall
    r["rate_hz"] = (len(frames) - 1) / wall if wall > 0 else None
    r["seq_gaps"] = seq_gaps(frames)
    r["median_dticks"] = median(dts)
    r["tick_seconds_over_wall"] = tick_total / wall if wall > 0 else None

    # Skip the first second for the settled statistics so a start transient does not
    # inflate them; the transient itself is shown separately.
    settled = [f for f in frames if f["t"] - frames[0]["t"] >= 1.0] or frames
    r["gyro_bias_rad_s"] = [mean([f["gyro"][i] for f in settled]) for i in range(3)]
    r["gyro_noise_rad_s"] = [stdev([f["gyro"][i] for f in settled]) for i in range(3)]
    g = [mean([f["accel"][i] for f in settled]) for i in range(3)]
    r["accel_mean"] = g
    r["accel_magnitude"] = math.sqrt(sum(x * x for x in g))
    big = max(range(3), key=lambda i: abs(g[i]))
    r["gravity_axis"] = (AXES[big], 1 if g[big] > 0 else -1)

    # Per-second view of the start: the first-frame transient.
    t0 = frames[0]["t"]
    rows = []
    for sec in range(0, min(8, int(wall) + 1)):
        w = [f for f in frames if sec <= f["t"] - t0 < sec + 1]
        if not w:
            continue
        gm = mean([math.sqrt(sum((f["gyro"][i] - r["gyro_bias_rad_s"][i]) ** 2 for i in range(3))) for f in w])
        rows.append({"second": sec, "frames": len(w), "gyro_dev_rad_s": gm,
                     "temp_raw": mean([f["temp"] for f in w]),
                     "battery": median([f["battery"] for f in w])})
    r["start_view"] = rows
    f0 = frames[0]
    r["first_frame"] = {"temp_raw": f0["temp"], "battery": f0["battery"],
                        "gyro": f0["gyro"], "accel": f0["accel"]}
    return r


def analyse_rotation(name, frames, bias):
    r = {"phase": name, "frames": len(frames)}
    if len(frames) < 10:
        r["error"] = "fewer than 10 frames"
        return r
    angle = [0.0, 0.0, 0.0]
    peak = [0.0, 0.0, 0.0]
    gaps = 0
    for a, b in zip(frames, frames[1:]):
        dt = ((b["ticks"] - a["ticks"]) & 0xFFFFFFFF) * TICK_SECONDS
        if dt > 0.1:
            gaps += 1
        for i in range(3):
            ga = a["gyro"][i] - bias[i]
            gb = b["gyro"][i] - bias[i]
            angle[i] += 0.5 * (ga + gb) * dt
            peak[i] = max(peak[i], abs(gb))
    deg = [math.degrees(x) for x in angle]
    k = max(range(3), key=lambda i: abs(deg[i]))
    others = [abs(deg[i]) for i in range(3) if i != k]
    r["net_angle_deg"] = deg
    r["dominant_axis"] = AXES[k]
    r["dominant_sign"] = 1 if deg[k] > 0 else -1
    r["leakage"] = max(others) / abs(deg[k]) if deg[k] else None
    r["gaps_over_100ms"] = gaps
    r["seq_gaps"] = seq_gaps(frames)
    r["duration_s"] = frames[-1]["t"] - frames[0]["t"]
    exp = EXPECTED.get(name)
    if exp:
        world, expected_deg = exp
        r["expected"] = "%s about world +%s" % (expected_deg, world)
        r["scale_ratio"] = abs(deg[k]) / abs(expected_deg)
        # Sensor axis k = sigma * world axis, and the world rotation was expected_deg
        # about the world axis, so the sensor reads expected_deg * sigma.
        sigma = 1 if (deg[k] > 0) == (expected_deg > 0) else -1
        r["implies"] = "sensor %s%s = world %s" % ("+" if sigma > 0 else "-", AXES[k], "+" + world)
        r["world_axis"] = world
        r["sigma"] = sigma
    return r


def verdict(report):
    """Plain-language summary lines. Never claims more than the data shows."""
    lines = []
    s = report.get("still", {})
    if "error" in s:
        lines.append("still: %s" % s["error"])
    elif s:
        ratio = s.get("tick_seconds_over_wall")
        if ratio is not None:
            ok = 0.98 <= ratio <= 1.02
            lines.append("tick unit (100 ns): ticks/wall = %.4f -> %s"
                         % (ratio, "consistent with 100 ns" if ok else "NOT consistent with 100 ns; check"))
        a = s["accel_magnitude"]
        lines.append("accel magnitude %.2f (about 10 expected on Monado's scale, 9.81 if exact)" % a)
        axis, sign = s["gravity_axis"]
        lines.append("gravity (upright pose): accel reads %s%s" % ("+" if sign > 0 else "-", axis))
    mapping = {}
    for name, (world, _) in EXPECTED.items():
        rot = report["rotations"].get(name)
        if not rot or "error" in rot:
            lines.append("%s: %s" % (name, rot["error"] if rot else "missing"))
            continue
        sr = rot["scale_ratio"]
        net = rot["net_angle_deg"][AXES.index(rot["dominant_axis"])]
        lines.append("%s: dominant sensor %s%s, net %.0f deg (%.2f x expected), leakage %.0f%%, %s"
                     % (name, "+" if rot["dominant_sign"] > 0 else "-", rot["dominant_axis"],
                        net, sr, 100 * (rot["leakage"] or 0), rot["implies"]))
        if not 0.9 <= sr <= 1.1:
            lines.append("  note: net angle is %.0f%% of 360: gyro scale, tick unit, or an incomplete turn" % (100 * sr))
        if (rot["leakage"] or 0) > 0.25:
            lines.append("  note: large cross-axis motion; the rotation axis was not clean")
        mapping[rot["world_axis"]] = (rot["dominant_axis"], rot["sigma"])
    if "Y" in mapping and s and "gravity_axis" in s:
        # Upright pose: a stationary accelerometer reads +1 g along world +Y
        # (it measures the reaction to gravity). Compare with the twist result.
        g_axis, g_sign = s["gravity_axis"]
        t_axis, t_sigma = mapping["Y"]
        if g_axis == t_axis and g_sign == t_sigma:
            lines.append("accel gravity direction agrees with the twist rotation (both: world +Y = sensor %s%s)"
                         % ("+" if g_sign > 0 else "-", g_axis))
        elif g_axis == t_axis:
            lines.append("accel and gyro disagree in SIGN on the vertical axis (accel: world +Y = sensor %s%s, "
                         "gyro: %s%s): one of them is inverted, or the pose/direction was not as instructed"
                         % ("+" if g_sign > 0 else "-", g_axis, "+" if t_sigma > 0 else "-", t_axis))
        else:
            lines.append("accel (vertical = sensor %s) and gyro (twist = sensor %s) disagree on which axis is vertical"
                         % (g_axis, t_axis))
    if len(mapping) == 3:
        used = {m[0] for m in mapping.values()}
        if len(used) == 3:
            lines.append("sensor-to-world (raw axes): " + ", ".join(
                "world +%s = sensor %s%s" % (w, "+" if m[1] > 0 else "-", m[0])
                for w, m in sorted(mapping.items())))
        else:
            lines.append("two rotations map to the same sensor axis; repeat the capture")
    return lines


def analyse(path, hand):
    phases, problems = parse_capture(path)
    mine = {name: [f for f in frames if f["hand"] == hand] for name, frames in phases.items()}
    other = sum(1 for frames in phases.values() for f in frames if f["hand"] != hand)
    report = {"file": path, "hand": hand, "problems": problems,
              "frames_other_hand": other, "rotations": {}}
    still = analyse_still(mine["still"]) if "still" in mine else {}
    report["still"] = still
    bias = still.get("gyro_bias_rad_s", [0.0, 0.0, 0.0])
    for name in EXPECTED:
        if name in mine:
            report["rotations"][name] = analyse_rotation(name, mine[name], bias)
    report["summary"] = verdict(report)
    return report


def print_report(rep):
    print("Capture: %s   hand: %s%s" % (rep["file"], "LEFT" if rep["hand"] == 0 else "RIGHT",
          ("   (ignored %d frames from the other hand)" % rep["frames_other_hand"]) if rep["frames_other_hand"] else ""))
    for p in rep["problems"][:10]:
        print("  warning:", p)
    s = rep["still"]
    if s and "error" not in s:
        print("\nstill: %d frames over %.1f s (%.1f Hz), %d sequence gaps, median %.0f ticks/frame"
              % (s["frames"], s["wall_seconds"], s["rate_hz"] or 0, s["seq_gaps"], s["median_dticks"]))
        print("  gyro bias  [rad/s]: %s" % " ".join("%+.4f" % x for x in s["gyro_bias_rad_s"]))
        print("  gyro noise [rad/s]: %s" % " ".join("%.4f" % x for x in s["gyro_noise_rad_s"]))
        print("  accel mean        : %s |a|=%.2f" % (" ".join("%+.2f" % x for x in s["accel_mean"]), s["accel_magnitude"]))
        ff = s["first_frame"]
        print("  first frame: temp=%d battery=%d gyro=%s" % (ff["temp_raw"], ff["battery"], " ".join("%+.3f" % x for x in ff["gyro"])))
        print("  start of capture, per second (gyro deviation from the settled bias):")
        for row in s["start_view"]:
            print("    t=%ds  frames=%3d  |gyro-bias|=%.4f rad/s  temp=%.0f  battery=%.0f"
                  % (row["second"], row["frames"], row["gyro_dev_rad_s"], row["temp_raw"], row["battery"]))
    for name, r in rep["rotations"].items():
        if "error" in r:
            print("\n%s: %s" % (name, r["error"]))
            continue
        print("\n%s: %d frames over %.1f s, %d sequence gaps, %d gaps >100 ms"
              % (name, r["frames"], r["duration_s"], r["seq_gaps"], r["gaps_over_100ms"]))
        print("  net angle [deg] X/Y/Z: %s  (expected %s)" % (" ".join("%+.0f" % x for x in r["net_angle_deg"]), r["expected"]))
    print("\nSummary")
    for line in rep["summary"]:
        print("  " + line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("capture")
    ap.add_argument("--hand", required=True, choices=["LEFT", "RIGHT"])
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args()
    rep = analyse(args.capture, 0 if args.hand == "LEFT" else 1)
    if args.json:
        json.dump(rep, sys.stdout, indent=2)
        print()
    else:
        print_report(rep)


if __name__ == "__main__":
    main()
