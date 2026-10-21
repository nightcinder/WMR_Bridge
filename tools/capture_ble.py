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
MAX_FRAME = 64

# (phase name, instructions). The analysis script keys on these names.
PHASES = [
    ("still",
     "Hold the controller still and upright: ring at the top, trigger facing away\n"
     "from you, as if aiming forward. Do not touch the buttons. Keep still for at\n"
     "least 5 seconds after pressing Enter."),
    ("twist_cw",
     "Start upright and facing away, as above. Twist it about its handle, like\n"
     "tightening a screw: clockwise as seen from above. ONE full 360-degree turn,\n"
     "slowly (about 4 seconds), ending in the same pose."),
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


class CaptureFile:
    """Writes the capture text format read by tools/analyze_capture.py.

        # comment / metadata
        @begin <phase> t=<seconds>
        F <seconds> <hand> <sequence> <hex of the 46-byte frame>
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
    counts = {0: 0, 1: 0}
    active = {"name": None, "writer": None}
    t0 = time.perf_counter()

    with open(args.out, "w", newline="\n") as fp:
        cap = CaptureFile(fp)
        cap.meta("wmr-capture v1")
        cap.meta("source: BLE FF11 raw stream via tools/capture_ble.py")
        cap.meta("hand: %s (as stated by the operator)" % args.hand)
        cap.meta("started: %s" % datetime.datetime.now().isoformat(timespec="seconds"))
        active["writer"] = cap

        def on_notify(_sender, data):
            now = time.perf_counter() - t0
            result = reassembler.push(bytes(data))
            if result and active["name"]:
                hand, seq, frame = result
                counts[hand] += 1
                cap.frame(now, hand, seq, frame)

        async with BleakClient(target) as client:
            print("Connected. Subscribing to FF11 (raw stream)...")
            await client.start_notify(FF11_UUID, on_notify)
            for name, text in PHASES:
                print("\n=== %s ===\n%s" % (name, text))
                await ainput("Press Enter to BEGIN '%s' ... " % name)
                active["name"] = name
                cap.begin(name, time.perf_counter() - t0)
                await ainput("Recording. Press Enter when FINISHED ... ")
                cap.end(name, time.perf_counter() - t0)
                active["name"] = None
                fp.flush()
            await client.stop_notify(FF11_UUID)

    print("\nWrote %s" % args.out)
    print("frames: LEFT=%d RIGHT=%d, malformed fragments=%d, abandoned frames=%d"
          % (counts[0], counts[1], reassembler.errors, reassembler.incomplete))
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
