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
   | `twist_cw` | Twist about the handle like tightening a screw, clockwise seen from above, one full 360 degree turn over about 4 s, ending in the same pose |
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
- `still`: sequence gaps, gyro bias and noise, per-second view of the first 8 seconds
  (gyro deviation, temperature word, battery byte) to show whether `sessionA`'s
  first-frame difference is a startup transient.

What this does and does not settle: it fixes the **raw** gyro axis directions and signs,
the gyro scale, and the tick unit. It does not give the per-device mix matrix, bias or
pose; those come from the config block (design above). It uses one pose and three
axes, so it cannot detect small misalignments.

## Calibration: status

**Not done.** The ESP32-side config read (FF13) is a design, and no capture has been
taken with the new tools.

### Confirmed (from the stationary samples only)

- Accel scale: magnitude 10.0 for a stationary controller (1 g), consistent with
  `raw / 49000`.
- Gyro scale is at least the right order of magnitude (noise about 0.016 rad/s).
- Ticks increase monotonically within each session.
- The tick unit (100 ns) is from Monado's code, **not** measured here.

### Unknown (to be answered by the hardware session above unless noted)

- Gyro and accel **axis directions and signs** of the raw sensor axes.
- Gyro scale in a real rotation (`1e-5` is Monado's value; unconfirmed here).
- Tick unit on these controllers, and whether the timer restarts on reconnect.
- Per-device mix matrix, bias and pose (needs the config block; not captured).
- Battery and temperature scales, and bytes 32-43 of the payload.
- Whether the init/quiesce/enable commands are needed to read the config block, and
  whether sending them disturbs the input stream.
- The controller's HID report descriptor (needed to choose the right report type for the
  config commands).
- Block sizes on these controllers.

### First-frame transient in `sessionA-0`

Compared with the other seven frames, `sessionA-0` has: gyro about (0.44, 0.79, -0.03)
rad/s instead of about 0.016; acceleration (0.2, 7.7, 7.2) m/s^2, magnitude 10.5,
instead of (-8.4, 4.6, 3.0), magnitude 10.07; temperature raw 1409 instead of about
3030; battery 156 instead of 131-133; a tick count of 56,034,506 (about 5.6 s at
100 ns per tick) with the next frame at 186,461,378.

The data show the controller was moving and in a different orientation at that
moment, and that the temperature word was still far from its settled value. Frame
spacing in the sample file is sparse, so this cannot say whether the temperature or
battery word settles over seconds or is a startup artefact. The data cannot
distinguish a real startup transient from the controller being picked up. **Not
resolved**; the `still` phase, started right after connect, is meant to settle it.

### Other caveats

- Thumbstick, trackpad, trigger and buttons were not exercised by the samples (nothing
  held); only the synthetic tests cover them. Capture frames while moving the stick
  and pressing buttons, labelled by hand.
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

**Repeat capture** (the user flashes the firmware from this branch first; nothing here
flashes anything):

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
