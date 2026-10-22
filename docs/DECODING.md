# Decoding controller frames (host side)

`src/wmr_decode.{h,c}` turns one 46-byte frame (`A1 01` + 44 payload bytes) into
buttons, thumbstick, trackpad, trigger, battery, accelerometer, gyroscope and a
device tick counter. It is pure C and is **not** part of the ESP32 firmware, which
remains a raw pipe; it is meant for the phone/host receiver after reassembly.

The layout and scale factors follow Monado's OG WMR controller driver
(`src/xrt/drivers/wmr/wmr_controller_og.c` and `wmr_protocol.h`, BSL-1.0, read at commit
`f50c796c32c811f5fd371f77fd8a749d2ad7df08`, abbreviated `f50c796c32c8`). See [Attribution](#attribution).

## Payload layout (offsets from the byte after the report ID)

| Offset | Size | Field |
| --- | --- | --- |
| 0 | u8 | buttons: 0x01 stick click, 0x02 home, 0x04 menu, 0x08 squeeze, 0x10 trackpad click, 0x20 BT pairing, 0x40 trackpad touch |
| 1-3 | 2 x 12 bit | thumbstick X (byte1 + low nibble of byte2), Y (high nibble of byte2 + byte3 << 4); centre 0x7FF |
| 4 | u8 | trigger 0..255 |
| 5, 6 | u8, u8 | trackpad X, Y, 0..0x64, both 0xFF when untouched |
| 7 | u8 | battery (raw) |
| 8-16 | 3 x s24 LE | accelerometer; Monado divides by 49000 (about m/s^2) |
| 17-18 | s16 LE | temperature (raw) |
| 19-27 | 3 x s24 LE | gyroscope; times 1e-5 (rad/s) |
| 28-31 | u32 LE | device timer, 100 ns per tick (from Monado, not yet measured here), wraps at 2^32 (`wmr_decode_extend_ticks`) |
| 32-43 | | not decoded by Monado either (unknown/device state) |

## Checked against hardware data

`tests/test_decode.c` runs on `samples/controller_frames.txt` (stationary
controller): acceleration magnitude is about 10.0 (1 g), gyro is at noise level
(about 0.016 rad/s) and ticks increase within each session. That confirms the
offsets and scales; it does not validate signs or axes.

## Timing: `device_ticks`

- **Unit: 100 ns per tick.** From Monado: `WMR_MOTION_CONTROLLER_NS_PER_TICK` is 100 in
  `wmr_controller_protocol.h`, and `wmr_controller_base_handle_imu_sample` computes
  `sample_time_ns = timestamp_ticks * 100` (commit `f50c796c32c8`). **Confirmed from
  Monado's code only. It has not been measured on these controllers**; the capture
  procedure below measures it (elapsed ticks against wall-clock time, and the gyro
  integral over a known rotation).
- 32 bits, wraps at 2^32 ticks = 429.4967296 s. Use unsigned modulo subtraction for
  `dt` and accumulate in 64 bits (`wmr_decode_extend_ticks` does the equivalent of
  Monado's "low half went down, add 2^32"). The wire rules are in
  [PROTOCOL.md](PROTOCOL.md#timing).
- The sample file only shows ticks increasing within a session; it is too sparse to
  show the per-report spacing (expected: about 58,800 ticks at 170 Hz).

## Calibration: design (done on the phone)

FF12 stays uncalibrated. The phone gets each controller's firmware config block from
the bridge on FF13 ([PROTOCOL.md](PROTOCOL.md#controller-config-block-ff13-proposed-design-only-not-implemented)),
including how the ESP32 reads it over HID, caches it, and keeps the 78-byte responses
out of the input stream. **That is a design only; no firmware for it exists and none of
it has been tried on hardware.**

What the phone does with the blob (all from Monado, `wmr_controller_base.c` and
`wmr_config.c` at commit `f50c796c32c8`):

1. Check length and CRC-32 against the FF13 header.
2. Skip the first 2 bytes. XOR the rest with `wmr_config_key` (1024 bytes, from
   `wmr_config_key.h`): `data[i] ^= key[i % 1024]`. The result is JSON text.
3. Parse JSON `CalibrationInformation.InertialSensors[]`. Each entry has `SensorType`
   (`CALIBRATION_InertialSensorType_Gyro`, `..._Accelerometer`, `..._Magnetometer`) and:
   - `Rt.Rotation` (9 floats, row-major 3x3) and `Rt.Translation` (3 floats): the
     sensor's pose;
   - `MixingMatrixTemperatureModel` (36 floats = 9 elements x 4 temperature
     coefficients): Monado uses only the first coefficient of each element,
     `mix[i] = model[4*i]`;
   - `BiasTemperatureModel` (12 floats = 3 axes x 4): Monado uses `bias = (model[0],
     model[4], model[8])`;
   - `BiasUncertainty` (3 floats) and `Noise` (6 floats), used by Monado's filter.
4. Apply per sample, in Monado's order, to the raw-scaled gyro and accel vectors:
   multiply by the 3x3 mix matrix; **add** the bias offsets; then rotate into the
   OpenXR IMU frame using the pose composed by `wmr_config_precompute_transforms`
   (X right, Y up, Z backward). The pose composition should be taken from that
   function, not re-derived.

Open questions for the phone side: the temperature terms are ignored by Monado (only
the constant coefficients); whether using them improves results here is untested. The
exact key table is Monado's (BSL-1.0); it must carry Monado's notice if copied.

## Hardware session: capture and axis check

This is how the unknowns below get answered. It needs the real controllers.

**Method.** `tools/capture_ble.py` (needs `pip install bleak`) connects to the
bridge from a PC over BLE, subscribes to the **raw** stream FF11, reassembles frames
and records them with the PC's clock and the bridge's per-hand sequence number. It
works with the **current firmware**, no firmware change. It asks the operator to
begin and end each phase with Enter, so the markers in the file are exact and
hand-labelled. `tools/analyze_capture.py` then reports the result. Neither the BLE
connection in `capture_ble.py` nor any real-controller result has been run yet; the
reassembly, file format and analysis are unit-tested on synthetic data
(`python tests/test_analyze_capture.py`, 41 checks, run in CI).

**Procedure** (one controller at a time; the other may be on but is ignored):

1. Connect the controller to the bridge as usual. Make sure no phone is connected to the
   bridge (the bridge serves one phone).
2. Hold the controller **upright**: ring at the top, trigger facing away from you, as if
   aiming forward. Keep the buttons untouched.
3. Run `python tools/capture_ble.py --hand LEFT` (or `RIGHT`) and follow the prompts:

   | Phase | What to do |
   | --- | --- |
   | `still` | Hold still and upright for at least 5 seconds after pressing Enter. Start the recording **right after** the controller connects if you can: that is what settles the `sessionA` first-frame question |
   | `twist_cw` | Start upright, trigger side facing away from you. Twist about the handle like tightening a screw: **the trigger side (the side facing away from you) swings to your RIGHT and the button side (facing you) swings to your LEFT**, which is clockwise seen from above. One full 360 degree turn over about 4 s, ending in the same pose. (The first phone capture was done the opposite way, button side to the right, which is counterclockwise; see 'Calibration: status') |
   | `flip_away` | Tip the top of the ring away from you and over: one forward somersault, about 4 s |
   | `lean_right` | Tip the top of the ring to your right and over: one cartwheel, about 4 s |

   Press Enter to begin and again to end each phase. Do the whole sequence for both
   controllers (and repeat once if the numbers look unclean).
4. Run `python tools/analyze_capture.py captures/<file>.txt --hand LEFT`.

**Capture file format** (text, one frame per line):

```
# wmr-capture v1
# hand: LEFT (as stated by the operator)
@begin still t=0.000000
F 0.013251 0 8231 A1010003...   <seconds> <hand 0/1> <sequence> <46-byte frame hex>
@end still t=6.100000
```

Frames outside a phase are not written. Sequence gaps in the file mean loss before the
BLE stack accepted the frame (see [PROTOCOL.md](PROTOCOL.md)).

**What the analysis reports, and how to read it.** The world frame is right-handed: X
right, Y up, Z toward you. Each rotation is -360 degrees about a world axis (clockwise
seen from above is negative about +Y; a forward somersault is negative about +X; a
rightward cartwheel is negative about +Z).

- Net angle per sensor axis (integrated with `dt` from `device_ticks`, gyro bias taken
  from `still`). One sensor axis should read about +-360 degrees and the others near 0.
  The sign tells whether that sensor axis points along or against the world axis.
- Scale ratio (net / 360): about 1.0 confirms the gyro scale **and** the tick unit
  together. The `still` phase separates the two: ticks-over-wall-clock about 1.0
  confirms the 100 ns unit by itself.
- Cross-axis leakage (other axes over the dominant one): above about 25 % means the
  rotation was not clean and should be repeated.
- Sensor-to-world mapping from the three rotations, and a consistency check against the
  gravity direction from `still` (a stationary accelerometer reads +1 g along world
  +Y; if that disagrees with the twist result, either an axis is inverted or the pose
  was not as instructed).
- Per phase and hand: **device reports missing**, derived from the tick deltas (elapsed ticks
  over the controller's median period, minus frames received), printed next to the
  sequence-gap line, with the lengths of the missing runs. Sequence gaps count only what
  was lost after the ESP32 numbered a report; this counts what was lost before it.
- `still`: sequence gaps, gyro bias and noise, per-second view of the first 8 seconds
  (gyro deviation, temperature word, battery byte) to show whether `sessionA`'s
  first-frame difference is a startup transient.

What this does and does not settle: it fixes the **raw** gyro axis directions and signs,
the gyro scale, and the tick unit. It does not give the per-device mix matrix, bias or
pose; those come from the config block (design above). It uses one pose and three
axes, so it cannot detect small misalignments.

## Calibration: status

**Not done.** The ESP32-side config read (FF13) is a design only. No per-device mix matrix,
bias or pose has been read or applied.

### First loss-free captures (phone app, reported)

Source: `right-20261021-232124.txt` and `left-20261021-232228.txt`, recorded with the
companion app's Capture screen (wmr-companion PR #4, not merged; not `capture_ble.py`) at
ATT MTU 517 with 0 sequence gaps on both hands, and analysed with `tools/analyze_capture.py`.
**These results were reported to the assistant that wrote this section through the
director issue #3; the capture files are not in this repository and the assistant has not
seen them or re-run the analysis.** Each line is "as reported" and is not hardware
verification by the assistant.

**Confirmed by these captures (both hands):**

- **Tick unit is 100 ns.** Ticks over wall-clock: 1.0016 (RIGHT) and 1.0083 (LEFT). Median
  report period 49,896 (RIGHT) and 49,816 (LEFT) ticks, about 4.99 ms, i.e. a nominal
  200 Hz.
- **Gyro scale is rad/s** (`raw * 1e-5`). Net angle for the three nominal 360 degree turns:
  LEFT 353 / 352 / 361, RIGHT 330 / 346 / 351 degrees, i.e. 0.92 to 1.00 of 360. The spread
  is attributed to hand technique, not to the scale.
- **Accel magnitude** 9.92 (RIGHT) and 9.95 (LEFT). Whether the scale is 9.81 or 10 is
  **not resolved**: both are within about 1.5 %.
- **Gravity in the upright pose reads +X** (sensor X) on both hands.
- **Gyro bias at rest:** RIGHT (-0.004, -0.000, +0.007) rad/s, LEFT (+0.024, +0.010,
  -0.004) rad/s. Per-sample noise 0.021 to 0.030 rad/s.
- **From `flip_away` and `lean_right`** (both hands agree): sensor -Y = world +X and
  sensor +Z = world +Z.

**Probable, NOT verified (sensor-to-world axes):** world X = -sensor Y, world Y = +sensor X,
world Z = +sensor Z (world: X right, Y up, Z toward the user, as in the analyzer). This is a
proper rotation (determinant +1) and gravity, flip and lean all agree with it.

The one disagreement is `twist_cw`, which came out as a **positive** rotation about sensor X
on both hands; read literally that gives sensor -X = world +Y, which contradicts gravity
(+X = world +Y) and would be a mirror image (determinant -1). The likely explanation is that
the twist was performed the other way from the instruction: the user held the controller
upright with the buttons toward him and twisted "to the right", which moved the button side
toward his right, i.e. **counterclockwise** seen from above (the instruction asked for
clockwise). The user's wording is ambiguous, so this stays **probable, not verified**. It
becomes confirmed when either (a) a repeat `twist_cw`, clearly clockwise from above, comes
out as a **negative** rotation about sensor +X, or (b) the app's 3D view driven by this
mapping follows all three rotations correctly. **Do not write a final sensor-to-world matrix
and do not remove the "unconfirmed" wording until one of those has happened.**

The twist instruction in the procedure above has been rewritten to say which way the
trigger side moves, so it cannot be misread again. (The companion app's own instruction text
needs the same change.)

### Reports missing upstream of the ESP32 (open problem)

Sequence numbers cannot show this: the bridge numbers a report only when it receives it. The
controller's tick counter can. During `still`, LEFT arrived at 108 Hz and RIGHT at 192.7 Hz
while the median tick delta says the controllers produce about 200 Hz and elapsed ticks match
wall-clock time. For the 5.6 s LEFT `still`, about 1,120 reports were expected and 608
arrived, so roughly half of the LEFT reports never reached the ESP32's queue (reported, not
re-derived). The ESP32's own statistics lines (about 150 reports/s per hand) agree that fewer
than 200 per second arrive.

Since the phone saw 0 sequence gaps, the loss is **before** the bridge numbers a report: in
the Classic Bluetooth link, the ESP32's Bluetooth controller or its host interface, or the
controller itself. `tools/analyze_capture.py` now reports it per phase and hand (see below).
Where it happens is **not established**; see [Investigation notes](#investigation-notes-upstream-report-loss).

### Unknown (do not record as confirmed)

- The **sign of the Y axis mapping** (the `twist_cw` conflict above).
- Per-device mix matrix, bias and pose (needs the config block; not captured).
- Whether the tick timer restarts when a controller reconnects, and the 2^32 wrap (about
  7 minutes of controller time) has not been observed.
- Battery and temperature scales, and bytes 32-43 of the payload.
- Whether the init/quiesce/enable commands are needed to read the config block, and
  whether sending them disturbs the input stream.
- The controller's HID report descriptor (needed to choose the right report type for the
  config commands) and the block sizes on these controllers.
- Where the thumbstick centre is: resting values are not 0 (see below).

### First-frame transient in `sessionA-0`

Compared with the other seven frames, `sessionA-0` has: gyro about (0.44, 0.79, -0.03)
rad/s instead of about 0.016; acceleration (0.2, 7.7, 7.2) m/s^2, magnitude 10.5,
instead of (-8.4, 4.6, 3.0), magnitude 10.07; temperature raw 1409 instead of about
3030; battery 156 instead of 131-133; a tick count of 56,034,506 (about 5.6 s at
100 ns per tick) with the next frame at 186,461,378.

The data show the controller was moving and in a different orientation at that moment,
and that the temperature word was still far from its settled value. The phone captures
show **no** such transient, but they were not started right after the controller connected,
so the question is **unresolved**: a `still` recording begun immediately after connect is
still needed.

### Thumbstick resting values are not zero

In the first FF12 bench check (user-run, reported) the resting stick values were hand 0
(-0.029, 0.014) and hand 1 (-0.064, 0.148). The decoder defines the centre as raw `0x7FF`
(2047 of 0..4095): `value = (raw - 0x7FF) / 0x7FF`, clamped to 1.0, which maps `0x7FF` to
exactly 0 (tested in `tests/test_decode.c`) and is Monado's formula, with no dead zone and
no centre calibration. The resting values therefore correspond to raw readings of about
1988 and 2076 (hand 0) and 1916 and 2350 (hand 1): the **controller reports a resting
position away from 2047**, not a firmware mapping error. Nothing in the firmware or the
decoder is wrong as far as this shows; a dead zone or a learned centre belongs on the
phone. How far the stick travels on each side (the real min and max) has not been measured.

### Other caveats

- Trackpad, trigger and buttons were not exercised by the captures (nothing held); only the
  synthetic tests cover them. Capture frames while moving the stick and pressing buttons,
  labelled by hand.
- No pose/orientation fusion (Monado uses `m_imu_3dof`); fusion runs on the phone.

## Attribution

`src/wmr_decode.*` is derived from Monado
(<https://gitlab.freedesktop.org/monado/monado>), files
`src/xrt/drivers/wmr/wmr_controller_og.c` and `wmr_protocol.h` at commit
`f50c796c32c811f5fd371f77fd8a749d2ad7df08`.

    Copyright 2018, Philipp Zabel.
    Copyright 2020-2021, N Madsen.
    Copyright 2020-2023, Collabora, Ltd.
    Copyright 2020-2023, Jan Schmidt
    SPDX-License-Identifier: BSL-1.0

Monado's license (Boost Software License 1.0), reproduced from its `LICENSE` file:

```
Permission is hereby granted, free of charge, to any person or organization
obtaining a copy of the software and accompanying documentation covered by this
license (the "Software") to use, reproduce, display, distribute, execute, and
transmit the Software, and to prepare derivative works of the Software, and to
permit third-parties to whom the Software is furnished to do so, all subject to
the following:

The copyright notices in the Software and this entire statement, including the
above license grant, this restriction and the following disclaimer, must be
included in all copies of the Software, in whole or in part, and all derivative
works of the Software, unless such copies or derivative works are solely in the
form of machine-executable object code generated by a source language
processor.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE, TITLE AND NON-INFRINGEMENT. IN NO EVENT SHALL
THE COPYRIGHT HOLDERS OR ANYONE DISTRIBUTING THE SOFTWARE BE LIABLE FOR ANY
DAMAGES OR OTHER LIABILITY, WHETHER IN CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

The rest of this repository carries the BlueKitchen BTstack license (see the README).

## Link quality (read this before trusting a rotation capture)

The rotation checks assume most frames arrive. The controllers produce about 150-190
reports/s each; the first LEFT and RIGHT captures delivered only about 10 frames/s per
hand, with 1,000 to 1,500 sequence gaps per phase. Gyro integration, the tick-unit check
and the axis mapping are **not valid** on such a capture, so the tools now say so:

- `tools/capture_ble.py` prints the ATT MTU the PC's BLE stack reports right after
  connecting, writes it into the capture header (`# mtu: N`), and after every phase and
  at the end prints per hand: frames received, sequence-gap events, total missing frames,
  percentage lost, effective Hz (what arrived) and produced Hz (arrived + missing).
- `tools/analyze_capture.py` prints the MTU, the loss per phase, and, when more than
  **5 %** of the produced frames in any phase were lost, a `LINK TOO LOSSY, rotation
  results not valid` banner; it then withholds the sensor-to-world mapping.
- The bridge's own 5-second line shows what it believes: `mtu`, `conn=[interval
  latency timeout]`, queue depth, and per hand `rx` (from the controller), `tx` (handed
  to BLE) and `drop` (queue full). If `rx` is about 190/s but `tx` is about 10/s with
  `drop` rising, the BLE link is the bottleneck; if `tx` is about 190/s but the capture is
  short of frames, the loss is after the ESP32 (the PC's BLE stack or radio). See
  [COUNTERS.md](COUNTERS.md).
- After the phone connects the bridge asks for a **7.5-15 ms connection interval, latency
  0, 4 s timeout** and logs what the phone actually granted
  (`BLE connection parameters at connect: ...` and, if the phone changes them,
  `... updated: ...`). Windows centrals often keep a longer interval (tens of ms), which
  limits how many notifications fit per second whatever the bridge asks; the log shows
  whether the request was honoured. **This has not been tried on hardware.**

**Which captures to trust (reported by the maintainer's bench runs; not reproduced by
the assistant that wrote this):**

- **Desktop captures over Windows with `bleak` lost over 90 % of the frames** (about 10
  frames/s per hand received of about 190 produced, 1,000-1,500 sequence gaps per
  phase). They are not usable for the rotation checks.
- **Phone captures are the reference.** A Pixel running the companion app, with ATT MTU
  517 and high connection priority, received about 4,200 FF11 frames per hand with 0
  sequence gaps, 0 incomplete frames and 0 fragment errors. The bridge's own statistics
  agree: `mtu=517`, `conn=[15.00ms lat=1 to=5000ms]` (the phone kept 15 ms and chose
  latency 1 rather than 0), about 150 frames/s per hand, `drop=0 offline=0 purged=0
  errors=0`, `rx=tx`, queue high-water 15 of 256. So the bridge and its queue keep up,
  and the earlier loss came from the Windows `bleak` link.
- The companion app is getting a capture screen that writes the same file format as
  `capture_ble.py`, so `tools/analyze_capture.py` stays the analyzer. `capture_ble.py`
  remains available for quick checks, but treat a desktop capture as valid only if its own
  loss lines are under 5 %.
- **Nothing is confirmed by the lossy captures.** The gyro scale, the tick unit
  (100 ns per tick), the axis directions and signs, and the other items under "Unknown"
  above stay **unconfirmed** until a clean capture, with a loss line under 5 %, has been
  analysed.

**Repeat capture** (firmware from `main`, which includes the link diagnostics, flashed by
the user; nothing here flashes anything):

```powershell
# 1. Build and flash as in docs/BUILD_WINDOWS.md, then open the monitor and note, after the
#    PC connects: the 'BLE connection parameters ...' lines and the STREAM line.
# 2. In another terminal, with the bridge not connected to a phone:
pip install bleak
python tools/capture_ble.py --hand LEFT
python tools/analyze_capture.py captures/<file>.txt --hand LEFT
# 3. Repeat for --hand RIGHT. Keep the monitor output from step 1 next to each capture.
```

Report with each capture: the printed MTU, the `conn=[...]` text, the per-phase loss lines
from `capture_ble.py`, and the STREAM/`rx`/`tx`/`drop` lines from the monitor during the
`still` phase.

## Investigation notes: upstream report loss

**Status: reading of code, configuration and the reported numbers only. Nothing was
measured by the assistant, no firmware was changed, and no cause is established.** The
statements about hardware are the maintainer's reports; the statements about code are from
this repository and the BTstack ESP32 port it is built with (`e38553977`).

### What the numbers say

| Source (reported) | LEFT | RIGHT |
| --- | --- | --- |
| First bench (about 15 s, both streaming to a phone), reports/s at the ESP32 | about 174 | about 154 |
| PR #6 bench, ESP32 statistics | about 150 | about 150 |
| Phone capture, `still` phase, arrival rate | 108 Hz | 192.7 Hz |
| FF12 first interval after subscribing (`tx` count; interval length not stated) | 377 | 310 |

The controllers produce about 200 reports/s (median tick period about 4.99 ms). Every run
is below 200, the shortfall changes from run to run, and **LEFT is not always the worse
hand** (it was the higher of the two in the first bench). So the asymmetry is not a fixed
property of one controller as far as these numbers show; it may depend on position, radio
conditions or timing. The phone saw no sequence gaps, so the reports are lost before the
bridge numbers them.

### Where a report can be lost, and what would show it

1. **Radio, controller to ESP32.** Classic ACL retransmits, but a controller can flush a
   report that is too late. Would predict: depends on distance, orientation and other
   2.4 GHz traffic; mostly isolated losses and short runs. The firmware collects no signal
   quality data (no RSSI, failed-contact counter or link quality is read).
2. **Radio shared with BLE.** The ESP32 has one radio for two Classic links and the BLE link
   to the phone (connection events every 15 ms as granted in the PR #6 bench, up to about 133
   per second if 7.5 ms were granted). Would predict: loss runs that line up with the BLE
   interval (about three reports per 15 ms), and a higher rate with no phone connected. This
   can be tested with the current firmware (experiments below).
3. **Sniff mode.** `wmr_bridge_main.c` enables sniff mode in the default link policy
   (`LM_LINK_POLICY_ENABLE_SNIFF_MODE | LM_LINK_POLICY_ENABLE_ROLE_SWITCH`), so a
   controller may request it and the ESP32's controller will accept. In sniff mode a device
   transmits only at anchor points. **Mode changes are not logged anywhere**
   (`HCI_EVENT_MODE_CHANGE` is not handled and not in the HCI trace list), so this cannot be
   ruled in or out from the logs.
4. **Packets dropped inside the ESP32 before the HID handler.** In
   `hci_transport_esp32_vhci.c`, `host_recv_pkt_cb` discards an incoming HCI packet when the
   receive ring buffer has no room and only calls `log_error`. In this build
   `ENABLE_LOG_ERROR` is defined but `CONFIG_BTSTACK_HCI_DUMP_STDOUT` is **not set**, so
   such a message is not printed: **a drop here would be silent.** Controller-to-host flow
   control is enabled with 20 buffers (`HCI_HOST_ACL_PACKET_NUM`), so the controller should
   not overrun the host and overflow is not expected, but nothing in the firmware would
   show it. Would predict: loss that follows host load rather than distance. Console output
   is a small contributor at most: a `STREAM` block of about 400 characters at 115200 baud
   takes about 35 ms, about 7 reports per hand per 5 s (0.7 %), if it blocks the BTstack
   task at all (not verified), far less than the shortfalls above.
5. **The controller.** Its own firmware may drop reports under radio contention or buffer
   limits. Not observable from here. (The battery bytes of the two hands in the FF12 check,
   133 and 131, do not suggest a difference.)

### How to tell them apart

- **Run-length histogram, no new capture needed.** `tools/analyze_capture.py` now prints, per
  phase and hand, how many reports are missing according to the tick deltas and how long the
  missing runs are (`1x40, 3x12, ...`). Isolated runs of 1 and no pattern point to 1; runs
  of about three at a steady rhythm point to 2; a few very long runs (tens of ms or more)
  point to stalls (3 or 4). Run it on the existing LEFT and RIGHT captures.
  *Limit:* if every delta is the same multiple of the real period (perfectly uniform loss)
  it looks like a slower controller and nothing is reported.
- **Experiments with the current firmware** (read `rate_rx` per hand from the 5 s `STREAM`
  lines, at least 60 s each, same physical placement):
  1. No phone connected (the `offline` counter counts every report): baseline per-hand rate.
  2. Phone connected, not subscribed (`stream=none`), then FF11, then FF12.
  3. Each controller on its own, then both.
  4. The two controllers swapped in position / right next to the ESP32 antenna.
  If the rate with no phone is already below 200, BLE sharing (2) is not the main cause.
- **Diagnostics that would need a firmware change (not made; for approval):**
  (a) a per-hand count of missing reports from the tick deltas, computed at the ESP32 (it
  reads 4 bytes of the report), shown in the `STREAM` line, so upstream loss is measured
  without a phone; (b) log `HCI_EVENT_MODE_CHANGE`; (c) read RSSI, failed-contact counter and
  link quality for each Classic link every few seconds; (d) make the VHCI ring-buffer drop
  visible (a counter in the port, or enabling the HCI dump, which is very verbose).

### Not explained

- The `SET_PROTOCOL handshake status=0x03` logged for each controller (first noted in the
  README) is unexplained; whether it affects the report rate is unknown.
