#!/usr/bin/env python3
"""Record raw controller frames from the WMR Bridge over BLE (characteristic FF11).

Guided: it prompts for each phase of the rotation procedure in docs/DECODING.md,
and writes one text capture file that tools/analyze_capture.py reads.

Needs `pip install bleak` and the real hardware. The pure parts (the fragment
reassembler and the file format) are unit-tested without hardware in
tests/test_analyze_capture.py; the BLE connection itself has NOT been run.

It only uses what the current firmware already serves (FF11 raw fragments), so no
firmware change is needed.
"""

import argparse
import asyncio
import datetime
import os
import sys
import time

DEVICE_NAME = "WMR Bridge"
FF11_UUID = "0000ff11-0000-1000-8000-00805f9b34fb"
HEADER_SIZE = 12
MAGIC = 0x52
MAX_FRAME = 80                 # WMR is 46 bytes, PSVR2 up to 79

# (phase name, instructions). The analysis script keys on these names.
PHASES = [
    ("still",
     "Hold the controller still and upright: ring at the top, trigger facing away\n"
     "from you, as if aiming forward. Do not touch the buttons. Keep still for at\n"
     "least 5 seconds after pressing Enter."),
    ("twist_cw",
     "Start upright and facing away, as above. Twist it about its handle, like\n"
     "tightening a screw: the TRIGGER side (the side facing away from you) swings to\n"
     "your RIGHT and the button side swings to your LEFT, which is clockwise seen from\n"
     "above. ONE full 360-degree turn, slowly (about 4 seconds), ending in the same pose."),
    ("flip_away",
     "Start upright and facing away. Tip the top of the ring away from you and over:\n"
     "a forward somersault of the controller. ONE full 360-degree turn, slowly\n"
     "(about 4 seconds), ending in the same pose."),
    ("lean_right",
     "Start upright and facing away. Tip the top of the ring to YOUR right and over:\n"
     "a cartwheel to the right. ONE full 360-degree turn, slowly (about 4 seconds),\n"
     "ending in the same pose."),
]


class Reassembler:
    """Reassembles FF11 fragments (docs/PROTOCOL.md, 'Receiver rules') per hand.

    push() returns (hand, sequence, frame_bytes) when a frame completes, else None.
    Counts malformed and abandoned fragments in .errors / .incomplete.
    """

    def __init__(self):
        self.slot = [None, None]      # per hand: [sequence, total, bytearray]
        self.errors = 0
        self.incomplete = 0

    def push(self, packet):
        if len(packet) < HEADER_SIZE:
            self.errors += 1
            return None
        magic, hand = packet[0], packet[1]
        sequence = int.from_bytes(packet[2:6], "little")
        total = int.from_bytes(packet[6:8], "little")
        offset = int.from_bytes(packet[8:10], "little")
        chunk = int.from_bytes(packet[10:12], "little")
        if magic != MAGIC or hand > 1:
            self.errors += 1
            if hand <= 1:
                self._abandon(hand)
            return None
        if (chunk != len(packet) - HEADER_SIZE or chunk == 0 or total == 0
                or total > MAX_FRAME or offset >= total or offset + chunk > total):
            self.errors += 1
            self._abandon(hand)
            return None
        data = packet[HEADER_SIZE:]
        if offset == 0:
            self._abandon(hand)
            self.slot[hand] = [sequence, total, bytearray(data)]
        else:
            cur = self.slot[hand]
            if cur is None or cur[0] != sequence or cur[1] != total or len(cur[2]) != offset:
                self._abandon(hand)
                return None
            cur[2] += data
        cur = self.slot[hand]
        if len(cur[2]) == cur[1]:
            self.slot[hand] = None
            return hand, sequence, bytes(cur[2])
        return None

    def _abandon(self, hand):
        if self.slot[hand] is not None:
            self.incomplete += 1
            self.slot[hand] = None


class LinkStats:
    """Per-hand frame and sequence accounting over one window (a phase or the run).

    A sequence gap means frames the controller produced that never reached the
    capture: lost on the way (see docs/DECODING.md, 'Link quality'). Effective Hz is
    what arrived; produced Hz is what the controller generated (arrived + missing).
    """

    def __init__(self):
        self.hands = {0: self._blank(), 1: self._blank()}

    @staticmethod
    def _blank():
        return {"frames": 0, "gap_events": 0, "missing": 0, "out_of_order": 0,
                "first_t": None, "last_t": None, "last_seq": None}

    def add(self, hand, sequence, t):
        h = self.hands[hand]
        if h["last_seq"] is not None:
            step = (sequence - h["last_seq"]) & 0xFFFFFFFF
            if step == 0 or step >= 0x80000000:
                h["out_of_order"] += 1          # duplicate or going backwards: not "missing"
            elif step > 1:
                h["gap_events"] += 1
                h["missing"] += step - 1
        h["frames"] += 1
        h["last_seq"] = sequence
        if h["first_t"] is None:
            h["first_t"] = t
        h["last_t"] = t

    def summary(self, hand):
        h = self.hands[hand]
        span = (h["last_t"] - h["first_t"]) if h["frames"] > 1 else 0.0
        total = h["frames"] + h["missing"]
        return {
            "frames": h["frames"], "gap_events": h["gap_events"], "missing": h["missing"],
            "out_of_order": h["out_of_order"], "seconds": span,
            "loss_fraction": (h["missing"] / total) if total else 0.0,
            "effective_hz": ((h["frames"] - 1) / span) if span > 0 else 0.0,
            "produced_hz": ((total - 1) / span) if span > 0 else 0.0,
        }

    def lines(self, indent="  "):
        out = []
        for hand, name in ((0, "LEFT"), (1, "RIGHT")):
            s = self.summary(hand)
            if not s["frames"]:
                continue
            out.append("%s%-5s frames=%d gaps=%d missing=%d (%.1f%% lost) effective=%.1f Hz produced=%.1f Hz%s"
                       % (indent, name, s["frames"], s["gap_events"], s["missing"],
                          100 * s["loss_fraction"], s["effective_hz"], s["produced_hz"],
                          ("  out-of-order=%d" % s["out_of_order"]) if s["out_of_order"] else ""))
        return out

    def worst_loss(self):
        return max(self.summary(0)["loss_fraction"], self.summary(1)["loss_fraction"])


LOSSY_THRESHOLD = 0.05


class CaptureFile:
    """Writes the capture text format read by tools/analyze_capture.py.

        # comment / metadata
        @begin <phase> t=<seconds>
        F <seconds> <hand> <sequence> <hex of the frame (up to 79 bytes)>
        @end <phase> t=<seconds>
    """

    def __init__(self, fp):
        self.fp = fp

    def meta(self, text):
        self.fp.write("# %s\n" % text)

    def begin(self, name, t):
        self.fp.write("@begin %s t=%.6f\n" % (name, t))

    def end(self, name, t):
        self.fp.write("@end %s t=%.6f\n" % (name, t))

    def frame(self, t, hand, sequence, frame):
        self.fp.write("F %.6f %d %d %s\n" % (t, hand, sequence, frame.hex()))


async def ainput(prompt):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


async def run(args):
    from bleak import BleakClient, BleakScanner    # imported late: not needed for tests

    print("Scanning for '%s'..." % DEVICE_NAME)
    if args.address:
        target = args.address
    else:
        target = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15)
        if target is None:
            sys.exit("'%s' not found. Is the ESP32 running and not connected to a phone?" % DEVICE_NAME)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    reassembler = Reassembler()
    run_stats = LinkStats()
    phase_stats = LinkStats()
    active = {"name": None}
    t0 = time.perf_counter()

    with open(args.out, "w", newline="\n") as fp:
        cap = CaptureFile(fp)

        def on_notify(_sender, data):
            now = time.perf_counter() - t0
            result = reassembler.push(bytes(data))
            if result and active["name"]:
                hand, seq, frame = result
                run_stats.add(hand, seq, now)
                phase_stats.add(hand, seq, now)
                cap.frame(now, hand, seq, frame)

        async with BleakClient(target) as client:
            mtu = getattr(client, "mtu_size", None)
            print("Connected. ATT MTU as reported by the PC's BLE stack: %s" % (mtu if mtu else "unknown"))
            if mtu is not None and mtu < 59:
                print("  note: an MTU below 59 cannot carry a 56-byte decoded record; the raw stream "
                      "needs one notification per frame only from MTU 61 up.")
            cap.meta("wmr-capture v1")
            cap.meta("source: BLE FF11 raw stream via tools/capture_ble.py")
            cap.meta("hand: %s (as stated by the operator)" % args.hand)
            cap.meta("mtu: %s" % (mtu if mtu else "unknown"))
            cap.meta("started: %s" % datetime.datetime.now().isoformat(timespec="seconds"))
            await client.start_notify(FF11_UUID, on_notify)
            for name, text in PHASES:
                print("\n=== %s ===\n%s" % (name, text))
                await ainput("Press Enter to BEGIN '%s' ... " % name)
                phase_stats = LinkStats()
                active["name"] = name
                cap.begin(name, time.perf_counter() - t0)
                await ainput("Recording. Press Enter when FINISHED ... ")
                cap.end(name, time.perf_counter() - t0)
                active["name"] = None
                fp.flush()
                print("Link during '%s':" % name)
                for line in phase_stats.lines():
                    print(line)
                if phase_stats.worst_loss() > LOSSY_THRESHOLD:
                    print("  WARNING: more than %d%% of frames lost in this phase; the rotation results "
                          "from it will not be valid." % int(LOSSY_THRESHOLD * 100))
            await client.stop_notify(FF11_UUID)

    print("\nWrote %s" % args.out)
    print("Whole run:")
    for line in run_stats.lines():
        print(line)
    print("malformed fragments=%d, abandoned frames=%d" % (reassembler.errors, reassembler.incomplete))
    print("Next: python tools/analyze_capture.py %s --hand %s" % (args.out, args.hand))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hand", required=True, choices=["LEFT", "RIGHT"],
                    help="which controller you are holding (recorded in the file)")
    ap.add_argument("--out", help="output file (default captures/<hand>-<time>.txt)")
    ap.add_argument("--address", help="BLE address of the bridge (skips the name scan)")
    args = ap.parse_args()
    if not args.out:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        args.out = os.path.join("captures", "%s-%s.txt" % (args.hand.lower(), stamp))
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
