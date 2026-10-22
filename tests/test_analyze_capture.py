"""Offline tests for tools/capture_ble.py (reassembler, file format) and
tools/analyze_capture.py (decoding, rotation analysis) using synthetic data.

No hardware and no bleak needed. Run: python tests/test_analyze_capture.py
These check the SCRIPTS against data built to a known truth; they say nothing
about what a real controller does.
"""

import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import analyze_capture as ac      # noqa: E402
import capture_ble as cb          # noqa: E402

checks = 0
failures = 0


def check(cond, what):
    global checks, failures
    checks += 1
    if not cond:
        failures += 1
        print("  FAIL:", what)


def s24_bytes(v):
    return (v & 0xFFFFFF).to_bytes(3, "little")


def make_frame(accel, gyro, ticks, temp=3000, battery=130):
    """Build a 46-byte frame; accel in m/s^2 on the 49000 scale, gyro in rad/s."""
    p = bytearray(44)
    p[5] = p[6] = 0xFF
    p[7] = battery
    for i in range(3):
        p[8 + 3 * i:11 + 3 * i] = s24_bytes(int(round(accel[i] / ac.ACCEL_SCALE)))
        p[19 + 3 * i:22 + 3 * i] = s24_bytes(int(round(gyro[i] / ac.GYRO_SCALE)))
    p[17:19] = (temp & 0xFFFF).to_bytes(2, "little")
    p[28:32] = (ticks & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes([0xA1, 0x01]) + bytes(p)


RATE = 170.0
DTICKS = int(round(1.0 / RATE / ac.TICK_SECONDS))


class Writer:
    def __init__(self):
        self.lines = ["# wmr-capture v1"]
        self.t = 0.0
        self.ticks = 0xFFFFFFFF - 3_000_000      # wraps early in the first phase
        self.seq = 10

    def phase(self, name, seconds, accel, gyro_fn, hand=1, drop_every=0, dev_drop=None):
        """dev_drop=(period, run): in every `period` reports the last `run` never reach the
        capture AND never get a sequence number (loss before the ESP32), only the ticks show it."""
        self.lines.append("@begin %s t=%.6f" % (name, self.t))
        n = int(seconds * RATE)
        for i in range(n):
            if dev_drop and i % dev_drop[0] >= dev_drop[0] - dev_drop[1]:
                self.t += 1.0 / RATE
                self.ticks += DTICKS
                continue
            self.seq += 1
            if drop_every and i % drop_every == drop_every - 1:
                self.t += 1.0 / RATE
                self.ticks += DTICKS
                continue
            f = make_frame(accel, gyro_fn(i / RATE), self.ticks)
            self.lines.append("F %.6f %d %d %s" % (self.t, hand, self.seq, f.hex()))
            self.t += 1.0 / RATE
            self.ticks += DTICKS
        self.lines.append("@end %s t=%.6f" % (name, self.t))

    def save(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as fp:
            fp.write("\n".join(self.lines) + "\n")
        return path


def turn(sensor_axis, sign, seconds=4.0):
    """Gyro function: one full 360 degree turn on one sensor axis, smooth profile."""
    # w(t) = A*(1 - cos(2 pi t / T)) integrates to A*T over one period = 2 pi.
    amp = 2 * math.pi / seconds

    def fn(t):
        w = [0.0, 0.0, 0.0]
        if t < seconds:             # the turn is over after one period
            w[sensor_axis] = sign * amp * (1 - math.cos(2 * math.pi * t / seconds))
        return w
    return fn


def test_decode():
    print("decode")
    f = make_frame([1.0, -2.0, 3.0], [0.5, -0.25, 0.125], 12345, temp=-7, battery=99)
    d = ac.decode(f)
    check(d is not None, "decodes")
    check(all(abs(a - b) < 1e-3 for a, b in zip(d["accel"], [1.0, -2.0, 3.0])), "accel")
    check(all(abs(a - b) < 1e-5 for a, b in zip(d["gyro"], [0.5, -0.25, 0.125])), "gyro")
    check(d["ticks"] == 12345 and d["temp"] == -7 and d["battery"] == 99, "ticks/temp/battery")
    check(ac.decode(f[:-1]) is None, "short frame rejected")
    check(ac.decode(bytes([0xA2]) + f[1:]) is None, "bad header rejected")

    # Same bytes as a real sample (sessionA-1 from samples/controller_frames.txt).
    real = bytes.fromhex("A1 01 00 03 28 85 00 FF FF 85 EE AF F9 EC 6D 03 83 42 02 BF 0B CA 06 00 42 06 00 07 00 00 C2 2C 1D 0B 00 00 00 00 00 00 03 00 00 00 00 00".replace(" ", ""))
    d = ac.decode(real)
    mag = math.sqrt(sum(x * x for x in d["accel"]))
    check(9.9 < mag < 10.2, "real sample accel magnitude ~ 1 g (got %.2f)" % mag)
    check(d["ticks"] == 186461378 and d["battery"] == 133, "real sample ticks/battery")


def test_analysis():
    print("analysis of a synthetic capture")
    w = Writer()
    # Upright: gravity along sensor +Y; small constant gyro bias on every axis.
    bias = [0.002, -0.001, 0.0005]
    w.phase("still", 6.0, [0.0, 9.81, 0.0], lambda t: bias)
    # Sensor +Z = world +Y (twist; reads +360 * -1 = ... see below), etc.
    # World rotation is -360 about each world axis. Choose:
    #   world +Y = sensor -X  -> sensor reads +360 about X during twist_cw
    #   world +X = sensor +Z  -> sensor reads -360 about Z during flip_away
    #   world +Z = sensor +Y  -> sensor reads -360 about Y during lean_right
    def plus_bias(fn):
        return lambda t: [a + b for a, b in zip(fn(t), bias)]
    w.phase("twist_cw", 5.0, [0, 9.81, 0], plus_bias(turn(0, +1)))
    w.phase("flip_away", 5.0, [0, 9.81, 0], plus_bias(turn(2, -1)))
    w.phase("lean_right", 5.0, [0, 9.81, 0], plus_bias(turn(1, -1)), drop_every=40)
    path = w.save()
    try:
        rep = ac.analyse(path, 1)
        s = rep["still"]
        check(abs(s["tick_seconds_over_wall"] - 1.0) < 0.01, "tick unit check ~1 (%.4f)" % s["tick_seconds_over_wall"])
        check(all(abs(a - b) < 1e-4 for a, b in zip(s["gyro_bias_rad_s"], bias)), "bias recovered")
        check(s["gravity_axis"] == ("Y", 1), "gravity axis")
        check(s["seq_gaps"] == 0, "no gaps in still")
        tw = rep["rotations"]["twist_cw"]
        check(tw["dominant_axis"] == "X" and tw["dominant_sign"] == 1, "twist: sensor +X")
        check(abs(tw["net_angle_deg"][0] - 360) < 4, "twist: ~360 deg (%.1f)" % tw["net_angle_deg"][0])
        check(tw["scale_ratio"] > 0.98 and tw["scale_ratio"] < 1.02, "twist: scale ratio")
        check(tw["implies"] == "sensor -X = world +Y", "twist implies (%s)" % tw["implies"])
        fa = rep["rotations"]["flip_away"]
        check(fa["dominant_axis"] == "Z" and fa["dominant_sign"] == -1, "flip: sensor -Z")
        check(fa["implies"] == "sensor +Z = world +X", "flip implies (%s)" % fa["implies"])
        lr = rep["rotations"]["lean_right"]
        check(lr["dominant_axis"] == "Y" and lr["dominant_sign"] == -1, "lean: sensor -Y")
        check(lr["implies"] == "sensor +Y = world +Z", "lean implies (%s)" % lr["implies"])
        check(lr["seq_gaps"] > 0, "sequence gaps counted")
        check(lr["leakage"] < 0.02, "no cross-axis leakage")
        check(any(line.startswith("sensor-to-world") for line in rep["summary"]), "mapping summary")
        check(any("disagree on which axis is vertical" in line for line in rep["summary"]), "gravity vs twist cross-check flags a mismatch")
        # Ticks wrapped inside the first phase and the analysis still agrees.
        check(rep["frames_other_hand"] == 0, "no other hand")
        check(not rep["problems"], "no parse problems: %s" % rep["problems"][:2])
        # Other hand is ignored.
        rep0 = ac.analyse(path, 0)
        check(rep0["frames_other_hand"] > 0 and rep0["still"].get("error"), "wrong hand yields no data")
    finally:
        os.remove(path)


def test_wrong_scale_is_flagged():
    print("a wrong gyro scale or tick unit is flagged")
    w = Writer()
    w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0])
    half = turn(0, +1)
    w.phase("twist_cw", 5.0, [0, 9.81, 0], lambda t: [x * 0.5 for x in half(t)])  # only 180 deg
    path = w.save()
    try:
        rep = ac.analyse(path, 1)
        check(any("gyro scale" in line for line in rep["summary"]), "flagged: %s" % rep["summary"])
    finally:
        os.remove(path)


def fragments(hand, seq, frame, chunk):
    out = []
    for off in range(0, len(frame), chunk):
        data = frame[off:off + chunk]
        out.append(bytes([0x52, hand]) + seq.to_bytes(4, "little") + len(frame).to_bytes(2, "little")
                   + off.to_bytes(2, "little") + len(data).to_bytes(2, "little") + data)
    return out


def test_reassembler():
    print("capture reassembler")
    frame = bytes(range(46))
    # Golden headers from docs/PROTOCOL.md: RIGHT, sequence 0x01020304, 32 + 14 bytes.
    frags = fragments(1, 0x01020304, frame, 32)
    check(frags[0][:12].hex() == "520104030201" + "2e00" + "0000" + "2000", "golden header 1")
    check(frags[1][:12].hex() == "520104030201" + "2e00" + "2000" + "0e00", "golden header 2")
    r = cb.Reassembler()
    check(r.push(frags[0]) is None, "partial")
    res = r.push(frags[1])
    check(res == (1, 0x01020304, frame), "complete frame")
    # one-notification frame
    r = cb.Reassembler()
    check(r.push(fragments(0, 7, frame, 58)[0]) == (0, 7, frame), "single fragment")
    # interleaved hands
    r = cb.Reassembler()
    a, b = fragments(0, 1, frame, 20), fragments(1, 2, frame[::-1], 20)
    done = []
    for x, y in zip(a, b):
        for p in (x, y):
            q = r.push(p)
            if q:
                done.append(q)
    check(len(done) == 2 and done[0][0] == 0 and done[1][0] == 1, "interleaved hands")
    # missing middle fragment: abandoned, never emitted
    r = cb.Reassembler()
    f = fragments(0, 9, frame, 10)
    check(r.push(f[0]) is None and r.push(f[2]) is None and r.push(f[3]) is None, "gap is not emitted")
    check(r.incomplete >= 1, "gap counted as abandoned")
    # malformed
    r = cb.Reassembler()
    check(r.push(b"\x00" * 5) is None and r.errors == 1, "short")
    bad = bytearray(frags[0]); bad[0] = 0x00
    check(r.push(bytes(bad)) is None and r.errors == 2, "bad magic")


def test_file_roundtrip():
    print("capture file format round trip")
    import io
    buf = io.StringIO()
    cap = cb.CaptureFile(buf)
    cap.meta("wmr-capture v1")
    frame = make_frame([0, 9.81, 0], [0, 0, 0], 1000)
    cap.begin("still", 0.0)
    for i in range(12):
        cap.frame(0.01 * i, 1, 100 + i, make_frame([0, 9.81, 0], [0, 0, 0], 1000 + 58824 * i))
    cap.end("still", 0.2)
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as fp:
        fp.write(buf.getvalue())
    try:
        phases, problems = ac.parse_capture(path)
        check(not problems and len(phases["still"]) == 12, "parsed")
        check(phases["still"][3]["seq"] == 103 and phases["still"][3]["hand"] == 1, "fields")
    finally:
        os.remove(path)
    check(len(frame) == 46, "frame size")


def test_device_loss():
    print("device reports missing (tick deltas)")
    # 3 of every 10 reports lost upstream: sequence numbers stay contiguous, ticks show it.
    w = Writer()
    w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0], dev_drop=(10, 3))
    w.phase("twist_cw", 5.0, [0, 9.81, 0], turn(0, +1), dev_drop=(10, 3))
    path = w.save()
    try:
        rep = ac.analyse(path, 1)
        s = rep["still"]
        check(s["seq_gaps"] == 0 and s["loss_fraction"] == 0.0, "sequence numbers show no loss")
        dl = s["device_loss"]
        check(dl is not None and abs(dl["loss_fraction"] - 0.3) < 0.02, "30%% upstream loss found: %s" % dl)
        check(abs(dl["period_ms"] - 1000.0 / RATE) < 0.05, "period %.3f ms" % dl["period_ms"])
        check(dl["runs"].get("3", 0) >= 100 and set(dl["runs"]) == {"3"}, "all missing runs have length 3: %s" % dl["runs"])
        check(not rep["link_lossy"], "sequence-based banner is not triggered by upstream loss")
        check(any(l.startswith("device reports missing upstream") for l in rep["summary"]), "summary line")
        check("lost before the ESP32" in ac.device_loss_text(dl), "text says where")
    finally:
        os.remove(path)

    # Isolated single losses (one in three): deltas of 1 and 2 periods, so the period is still found.
    w = Writer()
    w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0], dev_drop=(3, 1))
    path = w.save()
    try:
        dl = ac.analyse(path, 1)["still"]["device_loss"]
        check(abs(dl["loss_fraction"] - 1 / 3) < 0.02, "one in three lost: %s" % dl)
        check(set(dl["runs"]) == {"1"}, "isolated losses are 1-runs: %s" % dl["runs"])
        check(abs(dl["period_ms"] - 1000.0 / RATE) < 0.05, "period still found")
    finally:
        os.remove(path)

    # Documented limit: if EVERY other report is lost the deltas are all 2 periods, which is
    # indistinguishable from a controller running at half rate, so nothing is reported missing.
    w = Writer()
    w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0], dev_drop=(2, 1))
    path = w.save()
    try:
        dl = ac.analyse(path, 1)["still"]["device_loss"]
        check(dl["missing"] == 0 and abs(dl["period_ms"] - 2000.0 / RATE) < 0.1, "uniform loss looks like a slower controller")
    finally:
        os.remove(path)

    # A clean capture reports zero, and too few frames reports 'not enough'.
    w = Writer()
    w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0])
    path = w.save()
    try:
        dl = ac.analyse(path, 1)["still"]["device_loss"]
        check(dl["missing"] == 0 and dl["runs"] == {}, "clean capture: %s" % dl)
    finally:
        os.remove(path)
    check(ac.device_loss([]) is None and "not enough" in ac.device_loss_text(None), "too few frames")

    # Tick wrap does not create phantom losses (Writer starts 3,000,000 ticks before 2^32).
    w = Writer()
    w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0])
    path = w.save()
    try:
        check(ac.analyse(path, 1)["still"]["device_loss"]["missing"] == 0, "wrap is not loss")
    finally:
        os.remove(path)


def test_link_stats():
    print("link statistics (capture_ble.LinkStats)")
    s = cb.LinkStats()
    # RIGHT: 100 frames at 100 Hz with every 10th one missing; LEFT: no gaps.
    seq = 1000
    t = 0.0
    for i in range(100):
        seq += 2 if i and i % 10 == 0 else 1       # one missing number before every 10th arrival
        s.add(1, seq, t)
        s.add(0, 5000 + i, t)
        t += 0.01
    r = s.summary(1)
    check(r["frames"] == 100 and r["gap_events"] == 9 and r["missing"] == 9, "right gaps: %s" % r)
    check(abs(r["loss_fraction"] - 9 / 109) < 1e-9, "loss fraction")
    check(abs(r["effective_hz"] - 100.0) < 0.5, "effective Hz %.2f" % r["effective_hz"])
    check(abs(r["produced_hz"] - 108.0 / 0.99) < 0.01, "produced Hz %.2f" % r["produced_hz"])   # 109 numbers over 0.99 s
    l = s.summary(0)
    check(l["missing"] == 0 and l["gap_events"] == 0 and l["loss_fraction"] == 0, "left clean")
    # wrap-around of the 32-bit sequence is not a gap; a repeat is not 'missing'
    w = cb.LinkStats()
    w.add(0, 0xFFFFFFFF, 0.0); w.add(0, 0, 0.01); w.add(0, 0, 0.02); w.add(0, 1, 0.03)
    ws = w.summary(0)
    check(ws["missing"] == 0 and ws["out_of_order"] == 1, "wrap and duplicate: %s" % ws)
    check(len(s.lines()) == 2 and "LEFT" in s.lines()[0], "formatted lines")
    check(cb.LinkStats().worst_loss() == 0.0, "empty is clean")


def test_lossy_link_warning():
    print("lossy link warning (analyze_capture)")
    # The reported failure: about 190 frames/s produced, about 10 delivered (95% lost).
    w = Writer()
    w.lines.append("# mtu: 247")
    w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0], drop_every=2)
    w.phase("twist_cw", 5.0, [0, 9.81, 0], turn(0, +1), drop_every=2)
    w.phase("flip_away", 5.0, [0, 9.81, 0], turn(2, -1), drop_every=2)
    w.phase("lean_right", 5.0, [0, 9.81, 0], turn(1, -1), drop_every=2)
    path = w.save()
    try:
        rep = ac.analyse(path, 1)
        check(rep["link_lossy"], "flagged lossy")
        check(rep["mtu"] == "247", "mtu parsed from header: %s" % rep["mtu"])
        check(rep["summary"][0].startswith("LINK TOO LOSSY, rotation results not valid"), "banner first")
        check(not any(line.startswith("sensor-to-world (raw axes)") for line in rep["summary"]), "no mapping when lossy")
        check(any("NOT shown" in line for line in rep["summary"]), "says why")
        check(ac.lossy_banner(rep) is not None, "banner text")
    finally:
        os.remove(path)

    # Just under and just over the 5% limit.
    for every, lossy in ((25, False), (15, True)):       # 1/25 = 4%, 1/15 = 6.7%
        w = Writer()
        w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0], drop_every=every)
        w.phase("twist_cw", 5.0, [0, 9.81, 0], turn(0, +1), drop_every=every)
        path = w.save()
        try:
            rep = ac.analyse(path, 1)
            check(rep["link_lossy"] == lossy, "drop 1/%d lossy=%s (got %s)" % (every, lossy, rep["link_lossy"]))
        finally:
            os.remove(path)

    # A clean capture has no warning and says the MTU was not recorded when there is no header.
    w = Writer()
    w.phase("still", 6.0, [0, 9.81, 0], lambda t: [0, 0, 0])
    w.phase("twist_cw", 5.0, [0, 9.81, 0], turn(0, +1))
    path = w.save()
    try:
        rep = ac.analyse(path, 1)
        check(not rep["link_lossy"] and ac.lossy_banner(rep) is None, "clean capture not flagged")
        check(rep["mtu"] == "not recorded", "mtu missing -> 'not recorded'")
    finally:
        os.remove(path)


if __name__ == "__main__":
    test_device_loss()
    test_link_stats()
    test_lossy_link_warning()
    test_decode()
    test_analysis()
    test_wrong_scale_is_flagged()
    test_reassembler()
    test_file_roundtrip()
    print("\n%d checks, %d failures" % (checks, failures))
    sys.exit(1 if failures else 0)
