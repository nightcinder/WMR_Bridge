# Decoding controller frames (host side)

`src/wmr_decode.{h,c}` turns one 46-byte frame (`A1 01` + 44 payload bytes) into
buttons, thumbstick, trackpad, trigger, battery, accelerometer, gyroscope and a
device tick counter. It is pure C and is **not** part of the ESP32 firmware, which
remains a raw pipe; it is meant for the phone/host receiver after reassembly.

The layout and scale factors follow Monado's OG WMR controller driver
(`src/xrt/drivers/wmr/wmr_controller_og.c` and `wmr_protocol.h`, BSL-1.0, verified at commits
`f50c796c32c811f5fd371f77fd8a749d2ad7df08` [abbreviated `f50c796c`] and
`aa635fa755a779f8aee717647c44aa7282e6ca45` [abbreviated `aa635fa7`]). See [Attribution](#attribution)
and [Monado reference deep-dive](#monado-reference-deep-dive-investigation-of-unknowns).

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
out of the input stream. **The firmware reads and serves the block (WMR_Bridge #17, with the streaming fix #21). User-reported 2026-10-22: the phone read it in 496-byte windows, LEFT 7147 bytes and RIGHT 7148 bytes, CRC-32 matching the firmware's, and the app decoded gyro and accelerometer calibration.**

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

**Partly done.** The ESP32 reads the config block (FF13) and the app decodes calibration (user-reported, see above). Applying the per-device mix matrix, bias and pose to the orientation fusion is not done.

### First loss-free captures (phone app, reported)

Source: `right-20261021-232124.txt` and `left-20261021-232228.txt`, recorded with the
companion app's Capture screen (wmr-companion PR #4, not merged; not `capture_ble.py`) at
ATT MTU 517 with 0 sequence gaps on both hands, and analysed with `tools/analyze_capture.py`.
**These results were reported to the assistant that wrote this section through the
WMR_Bridge issue #3 (closed); the capture files are not in this repository and the assistant has not
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

**Checked visually in the companion app's 3D view (user-reported), not measured
(sensor-to-world axes):** world X = -sensor Y, world Y = +sensor X, world Z = +sensor Z
(world: X right, Y up, Z toward the user, as in the analyzer). This is a proper rotation
(determinant +1) and gravity, flip and lean all agree with it.

The check: the user ran the companion app (wmr-companion PR #5, FF12 mode, default axis table
= this mapping) on the Pixel against the flashed PR #5 firmware and reported that the decoded
3D debug view "appears to be working with correct orientations". This was relayed through the
the project owner on issue #3 (closed); the assistant did not see it. **What it establishes:** the three
rotations and the gravity direction agree with this table through the app's fusion filter, to
the extent one person looking at a 3D view can tell. **What it does not:** it is not a
numerical sign check, it was not reported separately for each hand, and a fusion filter can
hide an error in a small angle or in one sign pair. This is the second of the two acceptance
criteria set earlier (a clean clockwise `twist_cw` repeat, or a correct 3D view); the first has
not been done.

The one disagreement in the raw captures is `twist_cw`, which came out as a **positive**
rotation about sensor X on both hands; read literally that gives sensor -X = world +Y, which
contradicts gravity (+X = world +Y) and would be a mirror image (determinant -1). It is
explained by the direction the twist was performed in: the user held the controller upright
with the buttons toward him and twisted "to the right", which moved the button side toward his
right, i.e. **counterclockwise** seen from above (the instruction asked for clockwise). The
table that drives a correct view is the gravity-consistent one, which is the one above.

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

- A **numerical** sign check of the axis mapping: it has been checked only visually, in the
  app's 3D view, and not separately for each hand. A clean clockwise `twist_cw` repeat would
  give one.
- **Accelerometer true scale on hardware (9.81 vs 10):** Monado divides raw counts by 49,000
  (`(98000 / 2)`), based on an empirical observation on HP Reverb G1 that 1g is ~490,000 raw counts
  (`wmr_controller_og.c:229`). That produces ~10 m/s^2 at 1g, not 9.80665 m/s^2 (which would divide
  by ~49,966), and Monado flags this with `@todo: Confirm the scale is correct` (`wmr_controller_hp.c:175`).
  Whether the hardware accelerometer internal gain targets 1g = 9.81 m/s^2 or 10 m/s^2 remains unmeasured.
- **Battery byte and temperature word scales:** Monado does **not** implement battery calculation or
  temperature conversion (see [Monado reference deep-dive](#monado-reference-deep-dive-investigation-of-unknowns));
  both are kept as raw integers and exported only as debug variables. A true battery discharge/percentage
  curve and a temperature conversion formula (e.g. thermistor curve or linear conversion) are **not defined
  in Monado** and require hardware bench measurement.
- **Input control ranges in code:** Trigger is 0..255 normalized by `/ 255.0f` to [0, 1]; trackpad is 0..100
  centered at 50 (`0x32`) mapped by `(raw - 50) / 50.0f` to [-1, 1], with `0xFF` indicating untouched
  (mapped to 0); thumbstick is 12-bit unsigned (0..4095) centered at 2047 (`0x07FF`) mapped by
  `(raw - 0x7FF) / 0x7FF` clamped to 1.0. These follow Monado's reference equations (`wmr_controller_og.c:269-294`),
  but physical travel limits and user deadzones on actual hardware remain to be characterized.
- Per-device mix matrix, bias and pose (needs the config block; partly decoded on the phone).
- Whether the tick timer restarts when a controller reconnects, and the 2^32 wrap (about
  7 minutes of controller time) has not been observed on hardware.
- Bytes 32-43 of the payload (neither Monado nor this driver decodes them; labeled device state / unknown).
- Whether the init/quiesce/enable commands are needed to read the config block, and
  whether sending them disturbs the input stream.
- The controller's HID report descriptor (needed to choose the right report type for the
  config commands) and the block sizes on these controllers.
- Where the thumbstick centre is: resting values are not 0 (see below).
- **The cause of the upstream report loss.** The BLE-load hypothesis (reports drop while the
  ESP32 is also notifying on BLE) is **not established**; the reported rates are mixed (see
  "Investigation notes"). First bench data from the diagnostics build (user-reported, single
  runs): the controllers answered the three link-quality commands (`rssi=0 lq=255 fcc=0`,
  RIGHT slightly lower before the MTU change); no mode change was seen; and the tick-based
  missing-report figure rose from about 10 % to about 20 % per hand while FF12 was actually
  streaming (controllers beside the ESP32; at a larger distance the figures were higher again, see
  "Bench runs on the diagnostics build"). That points at BLE notification traffic costing reports but does not establish it;
  `WMR_CFG_FF12_DECIMATE` (below) is the next test. Still unknown: what the link-quality
  values mean for a Classic link, and whether the VHCI drop message wording matches what the
  counter looks for (`vhci_drop=0 log_errors=0` cannot tell "no drops" from "sink not
  receiving").

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

## Monado reference deep-dive: investigation of unknowns

The WMR controller driver in Monado (<https://gitlab.freedesktop.org/monado/monado>) was investigated
across two reference commits:
- Commit `f50c796c32c811f5fd371f77fd8a749d2ad7df08` (`f50c796c32c8`, Nov 2023)
- Commit `aa635fa755a779f8aee717647c44aa7282e6ca45` (`aa635fa755a7`, Oct 2026)

**Comparison between commits:** A complete diff across `src/xrt/drivers/wmr/` between `f50c796c` and
`aa635fa7` shows that all motion controller driver logic (`wmr_controller_og.c`, `wmr_controller_hp.c`,
`wmr_protocol.h`, `wmr_protocol.c`, `wmr_config.h`, `wmr_config.c`) is **100 % identical**. The only commit
in `src/xrt/drivers/wmr` between them was in `wmr_hmd.c` (buffer zero-initialization and size check on config
reads). Consequently, all line citations below apply equally to both commits.

### 1. Battery calculation

In Monado's OG WMR controller driver (`src/xrt/drivers/wmr/wmr_controller_og.c`):
- Line 199: `uint8_t battery;` in `struct wmr_controller_og_input`
- Line 295: `last_input->battery = read8(&p);` in `wmr_controller_og_packet_parse()`
- Line 476: `u_var_add_u8(wcb, &ctrl->last_inputs.battery, "input.battery");` in `wmr_controller_og_create()`

In Monado's HP Reverb G2 driver (`src/xrt/drivers/wmr/wmr_controller_hp.c`):
- Line 144: `uint8_t battery;` in `struct wmr_controller_hp_input`
- Line 241: `last_input->battery = read8(&p);` in `wmr_controller_hp_packet_parse()`
- Line 411: `u_var_add_u8(wcb, &ctrl->last_inputs.battery, "input.battery");` in `wmr_controller_hp_create()`

**Findings:**
1. Monado implements **no battery calculation, scaling, or percentage conversion** for WMR controllers.
2. The byte at payload offset 7 is stored directly as an unscaled `uint8_t`.
3. Neither driver implements the `xrt_device::get_battery_status` interface function, nor sets
   `wcb->base.supported.battery_status = true` (which drivers like Vive, PS Move, and Contact Glove do).
4. The raw byte is only exposed as an inspectable debug variable via Monado's `u_var` debugging system.
5. Any conversion from raw byte (e.g. observed values 131–133 at normal charge, 156 on first frame) to
   millivolts, discharge percentage, or battery level cannot be derived from Monado source code and must
   be characterized by empirical bench measurements against known battery states.

### 2. Temperature word

In Monado's OG WMR controller driver (`src/xrt/drivers/wmr/wmr_controller_og.c`):
- Line 206: `int32_t temperature;` in `struct wmr_controller_og_input.imu`
- Line 314: `last_input->imu.temperature = read16(&p);` in `wmr_controller_og_packet_parse()`
- Line 486: `u_var_add_i32(wcb, &ctrl->last_inputs.imu.temperature, "imu.temperature");` in `wmr_controller_og_create()`

In `src/xrt/drivers/wmr/wmr_protocol.h`:
- Lines 130-137:
  ```c
  static inline int16_t
  read16(const unsigned char **buffer)
  {
      int16_t ret = (*(*buffer + 0) << 0) | //
                    (*(*buffer + 1) << 8);
      *buffer += 2;
      return ret;
  }
  ```

In Monado's HP Reverb G2 driver (`src/xrt/drivers/wmr/wmr_controller_hp.c`):
- Line 151: `int32_t temperature;`
- Line 260: `last_input->imu.temperature = read16(&p);`
- Line 425: `u_var_add_i32(wcb, &ctrl->last_inputs.imu.temperature, "imu.temperature");`

In Monado's configuration parser (`src/xrt/drivers/wmr/wmr_config.h` and `wmr_config.c`):
- `wmr_config.h` lines 114-117:
  ```c
  /* Current bias and mix matrix extracted from
   * the configuration, which provides coefficients
   * for temperature adjustments - but they're always 0,
   * so we just take the constant coefficient */
  struct xrt_vec3 bias_offsets;
  struct xrt_matrix_3x3 mix_matrix;
  ```
- `wmr_config.c` lines 211-243: When parsing `CalibrationInformation.InertialSensors[]`, Monado reads
  `MixingMatrixTemperatureModel` (36 floats) and `BiasTemperatureModel` (12 floats). However, it ignores
  all higher-order polynomial coefficients, indexing `mix_model_values[i * 4]` and
  `bias_model_values[axis * 4]` to retain only the constant offsets.

**Findings:**
1. Monado decodes payload offset 17–18 as a **little-endian signed 16-bit integer** (`int16_t`), sign-extended to `int32_t`.
2. Monado performs **no temperature conversion** to Celsius, Kelvin, or Fahrenheit.
3. The temperature word is **not fed into IMU sensor fusion, bias estimation, or calibration**.
4. The config block's temperature polynomial terms are ignored by Monado because vendor calibration models
   report zero for non-constant coefficients.
5. In sample data, stationary controllers report settled values around 3007–3040 raw counts. Whether this
   represents an internal IMU die temperature (e.g. from an InvenSense or Bosch sensor at ~0.5 °C or 1 °C
   per LSB with an offset) or another thermistor circuit is not established by Monado.

### 3. Accelerometer scale factor

In Monado's OG WMR controller driver (`src/xrt/drivers/wmr/wmr_controller_og.c`):
- Lines 226-234:
  ```c
  static inline void
  vec3_from_wmr_controller_accel(const int32_t sample[3], struct xrt_vec3 *out_vec)
  {
      // Reverb G1 observation: 1g is approximately 490,000.

      out_vec->x = (float)sample[0] / (98000 / 2);
      out_vec->y = (float)sample[1] / (98000 / 2);
      out_vec->z = (float)sample[2] / (98000 / 2);
  }
  ```
- Lines 297-306:
  ```c
  int32_t acc[3];
  acc[0] = read24(&p); // x
  acc[1] = read24(&p); // y
  acc[2] = read24(&p); // z
  vec3_from_wmr_controller_accel(acc, &last_input->imu.acc);
  ```
- Lines 308-311:
  ```c
  U_LOG_IFL_T(wcb->log_level, "Accel [m/s^2] : %f",
              sqrtf(last_input->imu.acc.x * last_input->imu.acc.x +
                    last_input->imu.acc.y * last_input->imu.acc.y +
                    last_input->imu.acc.z * last_input->imu.acc.z));
  ```

In Monado's HP Reverb G2 driver (`src/xrt/drivers/wmr/wmr_controller_hp.c`):
- Lines 171-180:
  ```c
  static inline void
  vec3_from_wmr_controller_accel(const int32_t sample[3], struct xrt_vec3 *out_vec)
  {
      // Reverb G1 observation: 1g is approximately 490,000.
      // @todo: Confirm the scale is correct

      out_vec->x = (float)sample[0] / (98000 / 2);
      out_vec->y = (float)sample[1] / (98000 / 2);
      out_vec->z = (float)sample[2] / (98000 / 2);
  }
  ```

**Findings:**
1. The scale divisor `49000.0f` (`98000 / 2`) is an empirical heuristic originating from bench observations
   on an HP Reverb G1 controller where stationary 1g measured ~490,000 raw counts.
2. Dividing 490,000 raw counts by 49,000 yields an acceleration magnitude of **$10.0\,\text{m/s}^2$**, not standard
   earth gravity $g = 9.80665\,\text{m/s}^2$.
3. If the controller's factory calibration calibrated raw counts against standard gravity $g \approx 9.80665\,\text{m/s}^2$,
   the true divisor would be $490{,}000 / 9.80665 \approx 49{,}966$ (or $50{,}000$ for a rounded $1\,\text{g} = 9.8\,\text{m/s}^2$).
4. Monado developers explicitly noted this uncertainty with `// @todo: Confirm the scale is correct` in
   `wmr_controller_hp.c:175`.
5. This explains why stationary captures in `samples/controller_frames.txt` and bench captures show $|a| \approx 9.92$
   to $10.08\,\text{m/s}^2$: Monado's divisor intentionally produces ~10 m/s^2.

### 4. Trigger, trackpad, and input handling

In Monado's OG WMR controller driver (`src/xrt/drivers/wmr/wmr_controller_og.c`):
- **Trigger (byte 4):**
  - Line 185: `float trigger;`
  - Line 287: `last_input->trigger = (float)read8(&p) / 0xFF;`
  - Line 379: `xrt_inputs[WMR_CONTROLLER_INDEX_TRIGGER_VALUE].value.vec1.x = cur_inputs->trigger;`
  - Line 475: `u_var_add_f32(wcb, &ctrl->last_inputs.trigger, "input.trigger");`
  - Range: `0x00 .. 0xFF` (0 .. 255 unsigned) normalized to `[0.0, 1.0]`. $0x00 \to 0.0$ (released), $0xFF \to 1.0$ (fully pulled).
- **Trackpad (bytes 5, 6):**
  - Lines 192-197: `struct { bool click; bool touch; struct xrt_vec2 values; } trackpad;`
  - Lines 289-294:
    ```c
    // Read trackpad coordinates (0x00 - 0x64. Both are 0xFF when untouched)
    uint8_t trackpad_x = read8(&p);
    uint8_t trackpad_y = read8(&p);
    last_input->trackpad.values.x = (trackpad_x == 0xFF) ? 0.0f : (float)(trackpad_x - 0x32) / 0x32;
    last_input->trackpad.values.y = (trackpad_y == 0xFF) ? 0.0f : (float)(trackpad_y - 0x32) / 0x32;
    ```
  - Lines 382-384: mapped to `WMR_CONTROLLER_INDEX_TRACKPAD_CLICK`, `TRACKPAD_TOUCH`, and `TRACKPAD` (vec2).
  - Lines 480-483: exposed via `u_var_add_bool` and `u_var_add_f32`.
  - Range: `0x00 .. 0x64` (0 .. 100 decimal) per axis when touched; both bytes are `0xFF` when untouched.
  - Normalization: Center is `0x32` (50 decimal).
    - Untouched (`0xFF`): returns `0.0f`.
    - Min (`0x00` = 0): $(0 - 50) / 50 = -1.0f$.
    - Center (`0x32` = 50): $(50 - 50) / 50 = 0.0f$.
    - Max (`0x64` = 100): $(100 - 50) / 50 = +1.0f$.
- **Trackpad touch and click (byte 0):**
  - Line 264: `last_input->trackpad.click = buttons & 0x10;` (bit 4)
  - Line 266: `last_input->trackpad.touch = buttons & 0x40;` (bit 6)
- **Thumbstick (bytes 1-3):**
  - Lines 270-284: two 12-bit unsigned coordinates (0 .. 4095) packed across 3 bytes. Center is `0x07FF` (2047).
  - Normalized via `(float)(stick - 0x07FF) / 0x07FF` and clamped to `1.0f`.
- **Buttons (byte 0):**
  - Lines 259-266: bitmask:
    - `0x01`: thumbstick click
    - `0x02`: home
    - `0x04`: menu
    - `0x08`: squeeze click
    - `0x10`: trackpad click
    - `0x20`: Bluetooth pairing
    - `0x40`: trackpad touch
- **Differences on HP Reverb G2 (`src/xrt/drivers/wmr/wmr_controller_hp.c`):**
  - Lines 230-240: HP controllers removed the trackpad. Byte 4 remains analog trigger (`(float)read8(&p) / 0xFF`).
    Byte 5 is repurposed as analog squeeze (`(float)read8(&p) / 0xFF`), and byte 6 holds digital button bits
    for X/A (bit `0x02`) and Y/B (bit `0x01`).

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
- **Nothing is confirmed by the lossy desktop captures.** What the first loss-free phone
  captures (0 sequence gaps) do and do not confirm is under "Calibration: status" above:
  the tick unit and gyro scale are reported confirmed; the axis mapping is checked visually
  only (user-reported). Everything else under "Unknown" stays **unconfirmed**.

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
| No phone subscribed (FF12 build, `stream=none`), `rate_rx` | 175-181/s | 164-184/s |
| FF11 streaming to the Pixel at MTU 517, `rate_rx` | about 147/s | 165-172/s |
| FF11 at MTU 23, queue full and dropping, `rate_rx` | about 135/s | about 135/s |
| First FF12 interval after subscribing, `rate_rx` | 180/s | 163/s |
| Diagnostics build, FF12 subscribed at MTU 23 (nothing sent), `rate_rx` / `dev_missing` | 175-183/s, 9-11.5 % | 175-184/s, 9-11.5 % |
| Diagnostics build, FF12 streaming at MTU 517, `rate_rx` / `dev_missing` (per interval) | 158-163/s, about 20 % | 158-163/s, about 20 % |

The controllers produce about 200 reports/s (median tick period about 4.99 ms). Every run
is below 200, the shortfall changes from run to run, and **LEFT is not always the worse
hand** (it was the higher of the two in the first bench). So the asymmetry is not a fixed
property of one controller as far as these numbers show; it may depend on position, radio
conditions or timing. The phone saw no sequence gaps, so the reports are lost before the
bridge numbers them.

The `rate_rx` rows (reported from `STREAM` lines, single runs) are mixed and **no conclusion
is drawn**. With no phone subscribed both hands are already 8-18 % below 200, so BLE load
cannot be the whole explanation. With FF11 streaming LEFT is lower (about 147 against about
178) while RIGHT's ranges overlap (165-172 against 164-184), and at MTU 23 with a full queue
both are lower still; whether BLE load adds to the shortfall is **not established**, and the
runs were not repeated.

### Bench runs on the diagnostics build (user-reported; the assistant did not run or see them)

Relayed on WMR_Bridge issue #3 (closed); single runs, the first two partly corrected by the project owner.

**Near the ESP32 (controllers beside it, `rssi=0`, `lq=255`).** Missing device reports per hand
were about 10 % with the phone idle or the stream held at `mtu_skip` (MTU 23), and about 20 %
while FF12 streamed at MTU 517. This is the part of the first run that stands. It is
suggestive only.

**Distance changed partway through the run.** The user moved both controllers away from the
ESP32 during the same FF12 streaming run. The table below therefore shows the readings
changing **with distance increasing, not with time or BLE load**:

| Interval (per hand, 1000 reports expected) | LEFT `rate_rx` | RIGHT `rate_rx` | LEFT `rssi` / `lq` | RIGHT `rssi` / `lq` | LEFT missing | RIGHT missing |
| --- | --- | --- | --- | --- | --- | --- |
| first (before the move) | 160 | 158 | 0 / 255 | 0 / 255 | 208 | n/a |
| middle | 155-157 | 148-151 | -21 / 233 | -30 / 225 | about 237 | about 260 |
| last | 143 | 145 | -23 / 232 | -30 / 225 | 287 | 297 |

Reported flat through the run: `drop`, `purged`, `vhci_drop=0`, `log_errors=0`, `mode=active`,
`fcc=0`, `discont=0`, `q=0` (one `q=6/256` blip), `tx` equal to `rx`, `conn=[15.00ms lat=1 to=5000ms]`.
The cumulative `dev_missing` crept from 16.4 % to 17.7 %, which hides the per-interval rise from
about 21 % to about 29 %. That rise is **confounded by distance** and is not a result about BLE.
The `last5s=` figure in the `link:` line (added with the decimation option) shows this per interval.

**Phone disconnected at the far position (user-reported, no per-interval numbers yet).** The
user said the `dev_missing` percentage "started to fall back down immediately" after the phone
was disconnected, with the controllers left where they were. The cumulative percentage falls
whenever the current loss is below the running average, so this shows the loss dropped, not by
how much. It is consistent with BLE traffic competing with the Classic link; it cannot say
whether the cost comes from the notifications or from the BLE connection itself. **No
conclusion is drawn** until the `STREAM` lines from that period are available.

**A / B / C at one far position (user-reported; the position was chosen by the user and not
measured: `rssi` -27 to -29 LEFT and -36 to -37 RIGHT, `lq` about 227 LEFT and 218-219 RIGHT, so
RIGHT is the weaker link).** Per-interval figures come from the `dev_missing` deltas per 5 s
(about 1000 expected per hand). The runs were **not fixed length**. State A was first reported
as one full interval (LEFT 158.6/s, about 22 % missing; RIGHT 113.4/s, about 45 %) and then
replaced by a longer run of 15 full intervals, computed by the project owner from the pasted `STREAM`
lines. Missing percentages use the nominal period of 4.98 ms (about 200.8 reports/s):

| State | Intervals | LEFT `rate_rx` | LEFT missing | RIGHT `rate_rx` | RIGHT missing |
| --- | --- | --- | --- | --- | --- |
| C: phone disconnected | 1 block (no delta available) | 171.0/s | n/a | 164.4/s | n/a |
| B: phone connected, nothing subscribed (MTU 23, then 517) | 5 full | mean 171.6/s | about 14.6 % | mean 154.5/s | about 23.1 % |
| A: phone connected, FF12 streaming at MTU 517 | 15 full | mean 157.8/s (range 150.0-167.2, sd 4.1) | about 21.4 % (mean of the `dev_missing` deltas 22.5 %) | mean 120.9/s (range 104.6-142.2, sd 10.2) | about 39.8 % (mean of the `dev_missing` deltas 40.7 %) |

Link readings stayed flat during the A run (reported): LEFT `rssi` -28 to -30, `lq` 225-227;
RIGHT `rssi` -35 to -37, `lq` 218-220; `fcc=0`, `mode=active`, `vhci_drop=0`, `drop` and `purged`
constant, `tx` equal to `rx`.

What it suggests, with the caveats below (**not established**): A against B at the same
position, LEFT loses about 7 more points and RIGHT about 17 more, consistently across the run
rather than in one interval, so notification traffic may matter beyond the BLE connection
itself (B costs little by comparison); the weak-link hand (RIGHT) suffers most, in the same
direction as the near result (about 10 % to about 20 %) with a larger effect; C against B is
not resolved from one block. Other limits: the states were run one after another, not
interleaved, so a slow change over time is not excluded; B includes a change of MTU; B and C
are short; **the position is not measured**, so the percentages must not be treated as
characteristic of the hardware until the experiment is repeated at a measured, fixed distance.
Real use is at distance, so the weak-link case is the one that matters.

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
  The no-phone rates reported so far are already below 200, so BLE sharing cannot be the
  whole explanation; whether it adds to it is not established.
- **Three-state protocol at one fixed position (A / B / C).** Pick one position for both
  controllers, state it (distance, orientation, height, which side) and **do not move them
  for the whole experiment**; note `rssi` and `lq` per hand as a proxy, since the position
  itself is not measured. About 2 minutes (24 intervals) per state, in this order:
  - **A**: phone connected, FF12 streaming at MTU 517 (request the MTU first).
  - **B**: phone connected, nothing subscribed (`stream=none`; disable the CCC, keep the connection).
  - **C**: phone disconnected.
  Record per 5 s, per hand: `rate_rx`, the interval figure (`last5s=` in the `link:` line) and
  the cumulative figure (`cum=`), `rssi` and `lq`. **A against B** separates notification
  traffic from the BLE connection; **B against C** separates the connection itself. Because
  slow changes over time are not excluded by one pass, repeat the sequence in reverse order
  (C, B, A) and compare the two passes. (The reversed pass is an addition of this write-up, not
  a request from the project owner.) The decimation option below is the same protocol with state A repeated
  at N = 1, 2 and 4.
- **Diagnostics implemented on the PR #8 branch (approved on WMR_Bridge issue #3 (closed)). None has run
  on hardware.** See [COUNTERS.md](COUNTERS.md) for the output lines.
  - **(a) Missing reports, from the tick deltas, per hand** (`src/wmr_devloss.c`, pure C and
    host-tested). Fed with the 32-bit device tick counter of every received input report (4
    bytes read in `wmr_transport_submit_report`, before queueing). Same definition as the
    analyzer: each delta between consecutive reports implies `round(delta / period) - 1`
    missing reports, where `period` is the median of the deltas not longer than 1.5x the
    10th-percentile delta. Because it runs online: the period comes from a **sliding window
    of the last 256 deltas** (so it cannot drift with run length) and is refreshed every 32
    reports; nothing is counted until 16 deltas exist, which are then counted retroactively;
    a delta over 5 s is a *discontinuity* (counted separately, not as missing); a repeated
    tick value is ignored; the tracker is reset when the Classic link is created or lost.
    Same limit as the analyzer: perfectly uniform loss looks like a slower controller. Cost:
    about 1.1 KB per hand, and one sort of at most 256 values per hand every 32 reports.
  - **(b) Link mode changes.** `HCI_EVENT_MODE_CHANGE` is logged once per change (not per
    report), e.g. `LEFT link mode: active -> sniff (interval 40 slots = 25.00 ms) at t=12.345 s`
    (time since boot). Links are assumed active when created. The hand is found from the
    Bluetooth address in the ACL connection-complete event. The current mode also appears in
    the per-hand link line.
  - **(c) RSSI, failed-contact counter and link quality per Classic link**, polled every 5 s.
    **Extra HCI traffic: 3 commands per connected link per cycle, i.e. 6 commands per 5 s for
    two links = 1.2 commands per second, plus 1.2 Command Complete events per second.** They
    are sent one at a time, at least 100 ms apart (so at most 10 per second, and only for
    about 0.6 s of every 5 s), and an unanswered command is abandoned after 500 ms. Shown as
    `rssi=... fcc=... lq=...`, or `n/a(0xNN)` with the HCI status if the controller refuses.
    Interpretation caveats, unverified here: for BR/EDR, Read RSSI is, as I understand the
    specification, a difference from the "golden receive power range" (0 = within range,
    negative = too weak) rather than dBm; the failed-contact counter is, as I understand it,
    a count of *consecutive* failed contacts, so a poll every 5 s may read 0 almost always;
    link quality is vendor-specific (0-255, higher is better) and may be unsupported.
  - **(d) Silent VHCI receive drops, counted without editing any BTstack file.** A log sink
    registered with `hci_dump_init` (from `src/wmr_linkdiag.c`; this repository's
    `integration/main-CMakeLists.txt` only lists its sources) receives BTstack's error-level
    messages. A message whose format matches the one in `hci_transport_esp32_vhci.c`
    (`transport_recv_pkt_cb ... dropping packet`) increments `vhci_drop`; all error-level
    messages increment `log_errors` (which therefore includes the drops). Packet logging is
    turned off and only the error level reaches the sink, so the cost is one function call per
    error message and one flag test per HCI packet. Limits: it depends on BTstack's wording (a
    different wording would be counted in `log_errors` but not in `vhci_drop`); the callback runs
    on the Bluetooth controller task, so the counters are atomic; and the sink is not installed
    if `CONFIG_BTSTACK_HCI_DUMP_STDOUT` is set, in which case both stay 0.
  - **Test option, off by default: `WMR_CFG_FF12_DECIMATE=N`** (`src/wmr_config.h`).
    Sends only every Nth FF12 record per hand and counts the rest as `decimated` (see
    [TESTING.md](TESTING.md) and [COUNTERS.md](COUNTERS.md)); the STREAM block says
    `DIAGNOSTIC BUILD` when it is on. If `dev_missing` returns toward the idle level at N=2 or
    4, BLE airtime is a cause. **A fair test needs the controllers at one fixed, stated position
    for the whole run** (see the three-state protocol above): moving them changes the loss by
    itself. Not run on hardware.
  - **How to read them:** `dev_missing` per hand with no phone against with a phone shows
    whether BLE traffic changes the upstream loss; `mode` lines show whether a controller enters
    sniff mode when the loss appears; `rssi`/`fcc`/`lq` per hand may show a radio difference
    between LEFT and RIGHT; `vhci_drop` above zero means the ESP32 itself is dropping packets.

### Classic against BLE scheduling: what ESP-IDF v6.1 and the BTstack port expose

**Read from the sources only; nothing here was run or tested, and no firmware was changed.**
Sources: ESP-IDF v6.1 (`components/bt`, `components/esp_coex`), the BTstack ESP32 port (`e38553977`),
and this build's `sdkconfig`. The ESP32 controller itself is the precompiled
`components/bt/controller/lib_esp32/esp32/libbtdm_app.a`; how it shares the radio between BR/EDR
and BLE is **not visible in any source**, only through the options below.

What exists, and what it is documented to do:

| Option | Where | Documented effect | State in this build |
| --- | --- | --- | --- |
| `BTDM_CTRL_AUTO_LATENCY` (`auto_latency` in `esp_bt_controller_config_t`) | `controller/esp32/Kconfig.in`, `esp_bt.h` | "BLE auto latency, used to enhance classic BT performance while classic BT and BLE are enabled at the same time." Dual mode only. The text does not say what it changes; the name suggests it adjusts BLE connection latency, which is a guess | **off** (`# CONFIG_BTDM_CTRL_AUTO_LATENCY is not set`) |
| `BTDM_CTRL_MODEM_SLEEP` (ORIG mode), `esp_bt_sleep_enable/disable()` | same | "bluetooth controller sleeps between BR/EDR frames and BLE events" and wakes on a schedule. Nothing says it affects loss | **on**, ORIG, main-XTAL low-power clock |
| `esp_bredr_tx_power_set(min, max)` | `esp_bt.h` | BR/EDR TX power range, applies to inquiry, page and connection; default minimum `ESP_PWR_LVL_N0`, maximum `ESP_PWR_LVL_P3`. Not a scheduling option; it is the ESP32's transmit power towards the controllers | not called; defaults |
| `esp_coex_preference_set()` (deprecated), `esp_coex_status_bit_set/clear()` | `esp_coex/include/esp_coexist.h` | Arbitrate **Wi-Fi against Bluetooth** (`ESP_COEX_PREFER_WIFI/BT/BALANCE`). The status bits defined for BLE are for BLE Mesh and for BT only A2DP. **No Classic-against-BLE priority exists in this API** | not called |
| `ESP_COEX_SW_COEXIST_ENABLE` | `esp_coex/Kconfig` | Software Wi-Fi/Bluetooth coexistence. Its help says that if only Bluetooth is used, disabling it reduces binary size (no mention of any other effect) | **on** (the Wi-Fi component is compiled in, but the application and the BTstack port source do not call `esp_wifi_*`; whether the Wi-Fi libraries are initialised at run time was not checked) |
| `BTDM_CTRL_BR_EDR_MAX_ACL_CONN`, `BTDM_CTRL_BLE_MAX_CONN` | `Kconfig.in` | Maximum connections; each costs static RAM (1.2 KB and 1 KB). Nothing says they change scheduling | 4 and 3, while 2 and 1 are used |
| `controller_task_prio`, `BTDM_CTRL_PINNED_TO_CORE`, `BTDM_CTRL_HLI` | `esp_bt.h`, `Kconfig.in` | Controller task priority and core, high-level interrupt | core 0, HLI on |

The BTstack ESP32 port (`hci_transport_esp32_vhci.c`) calls `BT_CONTROLLER_INIT_CONFIG_DEFAULT()`
unchanged and then `esp_bt_controller_enable()`: it sets none of the options above, so every one of
them is controlled by `sdkconfig`. BTstack itself has only HCI-level knobs (below), nothing about
Classic-against-BLE priority.

**What I would test (none run), in order of cost, each against the A / B / C protocol at a fixed
position:** (1) `CONFIG_BTDM_CTRL_AUTO_LATENCY=y` (a one-line `sdkconfig.defaults` change; the only
option documented as favouring Classic while both are active); (2) `CONFIG_BTDM_CTRL_MODEM_SLEEP=n`
(rules the controller's sleep schedule in or out; costs power, irrelevant for a mains-powered bench
unit). Neither is documented as reducing loss, so either could do nothing. Both are build-time
changes in the user's BTstack tree, not in this repository, so they need a decision before anything
is changed.

**Other BLE settings that could lower the airtime cost besides batching** (read from BTstack
`src/hci.c` and the configuration; again nothing measured):

- **Connection interval.** The bridge asks for 7.5-15 ms (`WMR_CFG_CONN_INTERVAL_*`) and the phone
  chose 15.00 ms with slave latency 1 in the reported runs (the request asked for latency 0, so the
  phone granted something different). A longer interval means fewer connection events, each with a
  fixed airtime overhead, but only helps if the controller can send several notifications per
  event; a shorter one makes more events. Whether the ESP32 controller sends more than one
  notification per event is **not known** (closed controller; the firmware does not log it).
- **Slave latency.** It only lets the peripheral skip events when it has nothing to send, so it
  does not reduce the cost of streaming. The reported `lat=1` is the phone's choice.
- **Data length.** `ENABLE_LE_DATA_LENGTH_EXTENSION` is defined and BTstack's default is to ask
  for the maximum (`le_set_data_length_max = true`), so a notification of up to MTU-3 bytes can
  be carried in fewer link-layer packets if the controller and the phone accept it. The firmware
  does not log the data-length change event, so whether it took effect is **unknown**.
- **PHY.** Whether this ESP32 controller supports anything but the 1M PHY has now been checked
  (ONY-98) and the answer is a permanent no — 2M PHY is not available on this hardware at any price.
  The three independent checks:
  1. **The firmware never asks for it.** There is no `gap_le_set_phy` / `hci_le_set_phy` call
     anywhere under `src/`. The only BLE connection tuning is the interval/latency request at
     `src/wmr_transport.c:154` (`gap_request_connection_parameter_update`). BTstack does not switch
     PHY on its own — a 2M link needs an explicit LE Set PHY procedure, and the default LE
     connection PHY is 1M.
  2. **The hardware cannot do it.** The bridge is the original Classic ESP32 (README build table:
     ESP32 rev v1.1; `docs/FLASHING.md` calls for a Classic ESP32 because the S3 has no Classic
     Bluetooth). Espressif states the ESP32 supports Bluetooth LE 4.2 only, and 2M PHY is a BLE 5.0
     feature, so a PHY update initiated by the phone would be rejected by the controller. Source:
     <https://docs.espressif.com/projects/esp-faq/en/latest/software-framework/bt/ble.html#does-esp32-support-bluetooth-5-0>
  3. **It is not observable from the app either.** `universal_ble` exposes no PHY API, so the
     companion's `BleConnectionInfo.phy` stays null on every platform. A `"phy": null` in a saved
     latency run means **"not observable"**, not "2M".
  Consequently, there is no `sdkconfig` option, build flag or variant that enables 2M on this board.
  A 2M link would need a Bluetooth 5.0 bridge SoC, and the ESP32-S3/C3/C6 parts have no Classic
  Bluetooth — which the controllers require — so that is a two-radio redesign, not a drop-in part
  swap. The throughput levers that remain on 4.2 are DLE plus the batching/decimation work already
  described in this document.
- **Connection event length.** BTstack's `le_minimum_ce_length` / `le_maximum_ce_length` are
  connection *creation* parameters of a central; the bridge is the peripheral, and the L2CAP
  connection parameter update it sends has no event-length field. The phone decides.
- **Fewer, larger notifications** (the batching sketch under "Proposals" in
  [PROTOCOL.md](PROTOCOL.md)) and the decimation test are the direct handles.

A cheap, one-time observation that would settle two of the unknowns without recurring traffic:
log the LE data-length change event and the controller's LE buffer size (Read Buffer Size) at
connect. Proposed, not made.

### Not explained

- The `SET_PROTOCOL handshake status=0x03` logged for each controller (first noted in the
  README) is unexplained; whether it affects the report rate is unknown.
