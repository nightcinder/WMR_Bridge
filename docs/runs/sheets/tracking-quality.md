# Run sheet: tracking quality — calibration, 90° turns, yaw drift and the #19 unknowns

> **Proposed sheet, not yet run.** The tracking CSV export (M8.2) and the beta sliders (wmr-companion PR #33) must be in the APK before this is run. Nothing here is a hardware result; every figure quoted from an earlier run is user-reported.

Question: is the orientation correct and stable enough to use, does the FF13 calibration change what the fusion sees, and what are the real ranges behind the #19 unknowns? This sheet measures four things in one sitting on `main`, with no reflash: static accel and gyro bias with calibration off and on; the error of a 90° turn about each view axis; yaw drift over 5 minutes at three Madgwick beta values; and the raw trigger, trackpad, battery and temperature values.

It is deliberately a **measurement** sheet. It contains no pass/fail threshold and no verdict on whether calibration helps. That verdict is M8.4, after the numbers exist.

Companion documents:

- WMR_Bridge [`docs/DECODING.md`](../../DECODING.md) — the “Unknown” list this sheet closes or narrows, the accel/gyro scales, and the #19 Monado findings.
- WMR_Bridge [`docs/PROTOCOL.md`](../../PROTOCOL.md) — FF12/FF14 fields, the `temperature_raw` word and the trigger/trackpad ranges.
- WMR_Bridge [`docs/COUNTERS.md`](../../COUNTERS.md) — the `STREAM`, `LINKLOG` and per-hand link lines quoted below.
- WMR_Bridge issue [#19](https://github.com/nightcinder/WMR_Bridge/issues/19) — the battery, temperature, accel-scale and input-range unknowns that (a) and (d) measure.
- App [`docs/STATUS.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/STATUS.md) — what has already been checked on hardware (accel at rest about 9.9–10.2; gyro bias from the first stationary second; FF13 calibration applied behind a switch, not yet run).
- App [`docs/PROTOCOL.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/PROTOCOL.md) — “Applying the calibration”, including the fact that the FF13 path has **no scale factor**.
- App [`docs/GAMEPAD.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/GAMEPAD.md) — trigger full scale and trackpad range settings that (d) is meant to replace guesses for.
- App [`docs/LATENCY.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/LATENCY.md) and the latency-2 run sheet (wmr-companion [PR #35](https://github.com/nightcinder/wmr-companion/pull/35)) — the sitting this one can share.
- The M8 milestone in the ONY-19 plan: 8.1 is this sheet, 8.2 is the export it assumes, 8.3 is the sitting, 8.4 is the analysis.

## 0. What is already known, and what is not

| Item | State before this sheet | Source |
|---|---|---|
| Accel magnitude at rest | About 9.9–10.2 m/s² on the wire, so the Monado divisor 49,000 gives about 10, not 9.81. The true hardware scale is open | App `docs/STATUS.md`; WMR_Bridge `docs/DECODING.md` “Unknown” |
| Gyro bias | Estimated from the first stationary second by the app; not measured against a reference, and not compared with calibration on | App `docs/PROTOCOL.md`; `lib/domain/gyro_bias.dart` |
| 90° turn error | Not measured. The visual 3D check says the axis mapping is broadly right; the vertical-axis sign was never checked numerically | `docs/DECODING.md` “Unknown” |
| Yaw behaviour | Drifts with no magnetometer. The effect of Madgwick beta on drift has not been measured | App `docs/STATUS.md`; PR #33 |
| FF13 calibration | Blob read and decoded (LEFT 7147 bytes `8bc13223`, RIGHT 7148 bytes `75e8ab85`), applied behind a per-hand switch, default off, **not yet run on hardware** | App `docs/STATUS.md`, `docs/PROTOCOL.md` |
| Trigger | Wire range 0..255 by the decoder, physical travel unmeasured; gamepad full scale defaults to 255 | `docs/DECODING.md`; app `docs/GAMEPAD.md` |
| Trackpad | Wire 0..100, centre 50, `0xFF` when untouched; physical corner values and dead zones unmeasured | `docs/DECODING.md`; app `docs/GAMEPAD.md` |
| Battery and temperature | Raw integers only. Monado defines no scale, curve or conversion; no cold-start or warm values on these controllers | `docs/DECODING.md` “Unknown” |

One consequence worth stating before the data: the app’s FF13 path is a mix matrix, a bias and a rotation, but **no scale factor** (app `docs/PROTOCOL.md`, “Applying the calibration”). Block (a) therefore cannot turn 10 into 9.81 by itself: with calibration on it measures whether the calibration **distorts** the magnitude, and the 9.81 question is read from the wire magnitude with calibration off. If the magnitude changes materially with calibration on, record it; because the path has no scale factor, the change cannot be a deliberate 10→9.81 rescale. This is a property of the implementation, not a result.

## 1. Definitions and fixed arithmetic

The calculations below are fixed before any data exists, so the same numbers are not re-derived after seeing them. They are descriptive: no thresholds, no “pass”, no default is changed by this sheet.

| Term | Meaning here |
|---|---|
| Run | One export from the app: one CSV plus its JSON sidecar. A run has one block, one hand scope, one calibration state and one beta |
| Still window | Block (a): 5–55 s of the 60 s run. Block (c): 10–295 s of the 5 min run. The first seconds are excluded so the filter and bias estimate have settled |
| `off` / `on` | The per-hand **“Apply FF13 calibration”** switch in the debug screen. Off is the default. The switch is not persisted |
| Beta | The **“Madgwick Beta”** slider in Debug settings (PR #33): 0.01–0.20 in 0.01 steps, default 0.05. The three values used here are 0.02, 0.05 and 0.10; all are exactly on the slider grid |
| View frame | X right, Y up, Z toward the viewer, the frame of the debug 3D view and of `q_*`. Euler order yaw about Y, then pitch about X, then roll about Z (app `lib/domain/orientation.dart`) |
| Wire value | The FF12 field before the app’s FF13 calibration: uncalibrated scale, raw sensor axes |
| Filter input | What the fusion filter consumes after FF13 (if on), the axis map, and the runtime bias subtraction |
| Angle error | For a turn, the signed rotation about the intended view axis minus 90°; the total rotation angle is reported separately so a badly aligned turn cannot hide as a small axis error |
| Invalid run | A run with a missing required column, a stream or calibration state that changed inside it, a beta label that does not match the run log, a controller moved during a static run (pitch or roll change > 2°), a sequence gap over 0.5 s, an app restart or a reconnect. Repeat it and keep the invalid row marked |

### 1.1 Accel magnitude

For each sample, `accel_mag = sqrt(accel_x² + accel_y² + accel_z²)` (and the same for `accel_wire_mag`). Report, over the still window and per hand: median, 5th percentile, 95th percentile, and the number of samples. Report the wire and the filter-input magnitude separately.

### 1.2 Gyro bias per axis

The app’s runtime estimator subtracts a bias in the frame shown by `bias_frame`. For a like-for-like per-axis number across calibration states, use the **wire** gyro:

```text
bias_x = mean(gyro_wire_x)   (same for y, z)
bias_norm = sqrt(bias_x² + bias_y² + bias_z²)
bias_std_x = standard deviation(gyro_wire_x)   (same for y, z)
```

Also report `mean(gyro_bias_x/y/z)` from the estimator, with `bias_frame`, and the residual `mean(gyro_x/y/z)` (the filter input, which should be near zero). Do not compare the estimator’s x/y/z across calibration states: with calibration off the estimator is in the sensor frame, with it on it is in the view frame. Compare the norm and the wire values instead. If `gyro_wire_*` is not in the export, use the estimator’s `gyro_bias_*` and state `bias_frame`; do not compare x/y/z across calibration states in that case. A run where `bias_calibrated` is false for the still window is invalid for the bias number.

### 1.3 A 90° turn

Take `q_start` as the sample at the midpoint of the 3 s start hold and `q_end` as the sample at the midpoint of the 3 s end hold. With Hamilton product `⊗`:

```text
dq = q_end ⊗ conjugate(q_start)
if dq.w < 0: dq = -dq                      # shortest arc
theta = 2 * acos(clamp(dq.w, -1, 1))       # total rotation, 0..180°
axis = (dq.x, dq.y, dq.z) / sin(theta/2)   # undefined when theta is ~0
signed = theta * dot(axis, e)              # e = Y for yaw, X for pitch, Z for roll
off_axis = sqrt(max(0, theta² - signed²))  # rotation not about the intended axis
error = signed - 90°
```

Report, per hand, axis and calibration state: median signed error, median absolute error, median total angle, median absolute off-axis component, and the number of accepted turns. Analyse the +90 (start → end) and −90 (end → start) directions separately, then report both; do not average them before taking the absolute value. If every sign in a set is flipped, report the set as flipped once; do not flip individual runs.

### 1.4 Yaw drift

Use `yaw_deg`, unwrapped to a continuous sequence. Fit a straight line to the still window by least squares and report the slope in **degrees per minute**, the total change, and R². Also report the median of the first and last 5 s of the window. Report the pitch and roll change over the same window as a setup check; a static run that changes pitch or roll by more than 2° is invalid for drift. If the unwrapped yaw range exceeds 180°, say so and use the rotation about view Y from `q(t) ⊗ conjugate(q(0))` instead of the Euler angle.

### 1.5 Trigger, trackpad, battery and temperature

- Trigger: per hand, the maximum `trigger` over the full pull, the first non-zero value, and the length of the plateau at the top. The wire is 0..255; say whether 255 was reached.
- Trackpad: report `trackpad_x`/`trackpad_y` at each held point and convert to the wire scale where needed:
  `raw = round(50 + 50 * normalized / 32767)`, which recovers the 0..100 byte from the FF12 value within ±1. A point with `trackpad_touch = 0` is untouched; the documented wire sentinel there is `0xFF` (255), not a corner. If the export writes `trackpad_x_raw`/`trackpad_y_raw`, use those directly and say so.
- Battery: raw byte, first/last/min/max over the session spot runs, and every change with its timestamp. Do not convert to a percentage or a voltage.
- Temperature: `temperature_raw` as a **signed 16-bit** integer. Report cold start, the point after 30 min of streaming, and the end, plus min/max per run. Do not convert to °C; the ambient reading is recorded separately so a scale can be considered later.

## 2. Prerequisites and builds

Stop before starting if any row in the “Required” column is not true. Do not substitute numbers read off the screen for a missing export.

| Item | Required |
|---|---|
| Firmware | `main`, at or after the FF13 streaming fix (#21). Record the boot banner and `git -C WMR_Bridge rev-parse --short HEAD`. **No reflash during this sitting**; if the board is not already on such a build, flash once before the sitting and record the hash |
| App | A debug APK from wmr-companion `main` **after** PR #33 (Madgwick Beta slider) **and** after the M8.2 tracking export. Record the app commit shown by the build or About screen |
| FF13 key | Bundled in the APK (the CI debug APK carries it; app `docs/PROTOCOL.md`). “Read config” must complete for both hands before any calibration-on block |
| Controllers | Both charged, both connected, both awake for the whole block. Note any sleep/reconnect and repeat the affected run |
| Phone | Same phone and Android version for the whole sitting. Battery saver off, Bluetooth scanning off, no other BLE app, not charging during a run |
| Rig | A table, tape for the controller positions, and a box or book whose vertical face and the table top make the 90° reference for block (b). A stand or fixed prop for the static and drift poses |
| Export | The export screen shows the full path of each CSV/JSON; use its Share action or `adb pull` on that path. The serial monitor is captured to a text file for the whole sitting |

The app does not persist filter tuning or the calibration switches (app `docs/PROTOCOL.md`; PR #33). After any app restart, beta returns to 0.05, both calibration switches return to off, and the decoded config has to be read again before the switches can be enabled. Confirm them before **every** run and write the values in the run log.

Required export fields are in §9. If the M8.2 header uses different names, paste the header row and map it; do not silently accept a header missing `q_*`, `accel_mag`, `gyro_bias_*`, `beta`, `calibration` or `trigger`. Do not run the sheet against a build without the export: the numbers in (a)–(d) come from the files, not from the debug panel.

## 3. Setup, once per sitting

1. Put the bridge in its usual place, phone in its stand, both controllers on the table. Tape the bridge, the phone stand and one mark per controller so the position can be restored. Record the distance and a photo.
2. Start the serial monitor and save it. At the start of every run, copy the `STREAM ...` block; the `mtu=`, `conn=[...]` and `bridge_hands=` fields are part of the run record. Confirm `bridge_hands=0` (both hands); if it is 1 or 2, this is a single-hand build and the both-hands blocks below cannot run as written.
3. Open the debug screen, connect to the bridge, and select **Batched FF14** (the app default). If the M8.2 export only supports FF12, use **Decoded FF12** for the whole tracking session instead; do not mix streams inside a block. Confirm `STREAM` shows the matching `stream=` and a live `rate_rx`.
4. Read config for both hands: select LEFT and tap **“Read config (LEFT)”**, then select RIGHT and tap **“Read config (RIGHT)”**. Paste the result lines (length, CRC-32, READY) and confirm no error. If a read fails, continue with the calibration-off blocks and (d), skip every calibration-on block, and say which read failed.
5. Set **Madgwick**, beta **0.05**, both calibration switches **off**. Select LEFT in the hand selector and press **“Recalibrate gyro bias”**; select RIGHT and press it again. Wait until the debug panel says each hand is calibrated, then wait another 5 s.
6. Leave the axis table at its default (the Flip X/Y/Z and Gyro X/Y/Z chips untouched). If it has to change, record the change and do not mix runs from before and after it.
7. Record the cold-start battery and temperature with a 5 s spot export before moving anything (§8.3). If this sitting is shared with latency-2, do this before the latency session starts.

Pose for the static and drift runs: both controllers upright in the same pose (ring top up, trigger away from you), propped against a fixed block; tape the block, not the controller. If they cannot be held upright, lay them flat and record that pose once; the drift number is comparable only within the sitting because the pose changes which gyro axes see gravity. The other hand is a still control while one hand is moved.

## 4. Sharing the sitting with latency-2

The point of sharing is one flash and one APK install, not one continuous tape layout. The two sheets need different things from the table, so they are separate sessions inside the same sitting.

Recommended order:

1. **Cold start:** with the controllers off for at least 10 min, record the cold battery/temperature spot export (§8.3).
2. **Tracking quality (this sheet),** blocks (a) → (b) → (c) → (d). It needs the controllers moved for (b), so it cannot preserve latency-2’s taped position.
3. **Latency-2 (wmr-companion PR #35),** after re-taping the phone and controllers to its fixed position. Its 12 main runs plus interval pass take about 75–90 min.

Plan about 60–75 min for the tracking blocks and their setup, so the shared sitting is about 2.5 h before breaks. If latency-2 must go first, do the cold spot before it, then treat the 30 min temperature point as the first tracking run after the latency session and record the actual elapsed time. Do not interleave the two sheets: a 90° turn during a latency pair invalidates that pair.

Do not change firmware, app, phone, stream mode or calibration state in the middle of either session. If a latency run has to be repeated, finish it before starting a tracking block.

## 5. Block (a) — static, 60 s per hand, calibration off then on

Both hands are recorded at the same time; that gives 60 s of data per hand per state.

1. Place both controllers on their marks in the static pose. Do not touch them, the table or the phone.
2. With both calibration switches **off**, select LEFT and press **“Recalibrate gyro bias”**, then select RIGHT and press it. Wait 10 s, then start a **60 s** export. Stop it at 60 s. This is run **a-off**.
3. Select LEFT, turn **“Apply FF13 calibration”** on, select RIGHT and do the same. The bias estimator restarts automatically when the calibration changes; wait 10 s. Start a second **60 s** export. Stop at 60 s. This is run **a-on**.
4. Confirm in the run log: filter Madgwick, beta 0.05, stream mode unchanged, both switches in the stated state.

**Paste back**

- `a-off` and `a-on` CSV + JSON (or the export’s own stem, with the block and state in the run log).
- The `STREAM ...` block at the start and end of each run, and the `LEFT link:` / `RIGHT link:` lines in between.
- The run-log rows, including any movement or reconnect.
- Any screen numbers are only a cross-check; the values reported from this block come from the CSV.

**Reported after analysis:** per hand and state, `accel_wire_mag` and `accel_mag` median/p5/p95; wire gyro bias x/y/z and norm with standard deviations; estimator bias x/y/z with `bias_frame`; residual filter-input gyro; sample count and `bias_calibrated` fraction. No verdict.

## 6. Block (b) — 90° turns against a table edge, about each axis

Use a **table edge** as the straight reference and a box or book held square to it: the table top and the box’s vertical face are the 90° stop for pitch and roll; the table edge and the box’s perpendicular edge are the yaw alignment. Tape the box down and use the same rig for every turn.

Keep beta at 0.05 and the filter on Madgwick. Do the whole block once with both calibration switches **off**, then again with both **on**. The moved hand is one at a time; the other hand stays on its mark and is the still control.

### 6.1 Poses

| Axis (view) | Start pose | End pose (90° away) | How to keep it pure |
|---|---|---|---|
| Yaw (about Y, vertical) | Controller flat on the table, the line from the handle to the top of the ring parallel to the table edge | Same flat pose, that line parallel to the box’s perpendicular edge | Keep the controller flat; do not lift or tilt it |
| Pitch (about X, left–right) | Controller upright, ring top up, trigger side resting against the vertical face behind it | Ring tipped toward you until the controller lies flat on the table, ring pointing toward you | Rotate about the left–right line; the ring goes from up to horizontal, the trigger side leaves the rear face |
| Roll (about Z, toward you) | Controller upright, ring top up, right side resting against the vertical face to its right | Ring tipped to your left until the controller lies flat on the table | Rotate about the line toward you; the ring goes from up to horizontal to the left, the right side leaves the side face |

The view frame is the app’s: X right, Y up, Z toward the viewer. The vertical-axis sign is itself unverified (the debug screen says so); if the pitch or roll signs come out consistently inverted, record that and do not flip signs inside the sitting.

### 6.2 Cycles

One cycle is: hold the start pose for **3 s**, turn to the end pose over about **5 s**, hold the end pose for **3 s**, turn back to the start over about **5 s**, hold the start pose for **3 s**. Do **3 cycles** per axis (2 is the minimum if time is short). The return is a −90° measurement; keep it, it is not a failed +90.

Export one file per hand, axis and calibration state, with a **10 s** pre-roll before the first turn. That is 3 axes × 2 hands × 2 states = **12 runs**, each about 40 s. If the export records both hands in each file, that is fine: the still hand is the control, and the run log names the moved hand. Name the run log row with the axis, hand, state and order. One export per segment is deliberate: segmenting a single long file by time is the easiest way to mislabel a turn.

**Paste back**

- The 12 CSV + JSON files, each identified by hand, axis and calibration state in the run log.
- The `STREAM ...` block at the start and end of each calibration state, and the `LEFT link:` / `RIGHT link:` lines.
- The run-log rows with the turn order, any slipped start pose, and any cycle that was not 90° by eye.
- If a turn went wrong (slipped, hit the box, returned to a different pose), say which cycle and repeat the whole axis for that hand and state; keep the invalid file marked.

**Reported after analysis:** per hand/axis/state, median signed error, median absolute error, median total angle, median off-axis component, accepted turn count, and the +90/−90 split. No verdict.

## 7. Block (c) — yaw drift over 5 min at beta 0.02 / 0.05 / 0.10

1. Put both controllers back on their static marks in the same pose as block (a). Do not touch them for the whole run.
2. Select Madgwick. Set **“Madgwick Beta”** to **0.02**; the label must read `Madgwick Beta 0.02`. Confirm both calibration switches are **off**.
3. Select LEFT and press **“Reset orientation”**, then select RIGHT and press it. This resets yaw only; the tilt stays seeded. Wait **10 s**.
4. Start the export and run for **5 min** (300 s). Stop it. Do not pick up the phone or touch the controllers. This is run **c-beta-0.02**.
5. Repeat at **0.05** and **0.10**, in that order, with the same pose and the same 10 s settle. If time allows, repeat **0.05** at the end as the session-drift check; label it `c-beta-0.05-repeat`. If time allows and the calibration-on read succeeded, add one **0.05 with calibration on** run; label it separately and do not mix it into the three required rows.

The beta value is one global tuning value applied to both hands (PR #33); it is not per hand. It is also not persisted, so check the label before each run.

**Paste back**

- One CSV + JSON per beta run, with the beta label and the run order in the run log.
- The `STREAM ...` block at the start and end of each run.
- The run-log rows, any touch or movement, and the actual duration if it was not 300 s.

**Reported after analysis:** per beta and hand, yaw slope in °/min, total yaw change, R², first/last 5 s medians, pitch/roll change, sample count and sequence gaps. The three beta rows are descriptive; no beta is recommended here.

## 8. Block (d) — the #19 unknowns

### 8.1 Full trigger pull

For each hand, with the other hand still: pull the trigger from fully released to fully pulled over about **3 s**, hold at the top for **3 s**, release over about **3 s**. Do **3 pulls** per hand. Keep the controller in the same pose so the IMU is not the variable.

**Reported:** per hand, maximum `trigger`, first non-zero value, plateau length at the top, and whether the value reached 255. This is the number the gamepad “Trigger full scale (raw)” default of 255 is guessing at (app `docs/GAMEPAD.md`).

### 8.2 Trackpad corners

For each hand: touch and hold the trackpad at **top-left, top-right, bottom-left, bottom-right, centre**, and leave it **untouched**, for **3 s** each. Do two passes. Keep the finger still at each point; do not slide between points.

**Reported:** per hand, `trackpad_x`/`trackpad_y` at each point, converted to the 0..100 wire scale (§1.5), the min/max over the pass, whether the corners reach 0/100 or stop short, and whether the x/y directions match app `docs/GAMEPAD.md` (“To check on hardware”, step 4). Untouched is `trackpad_touch = 0`; do not report it as a corner.

### 8.3 Battery over the session, and temperature at cold start and after 30 min

Use one **5 s spot export** at each point; it covers both hands. Keep the controllers still and the app connected:

| Point | When | Record |
|---|---|---|
| Cold start | Controllers off for at least 10 min, before any streaming in this sitting | `battery`, `temperature_raw`, ambient °C, local time |
| +30 min | The first quiet moment after 30 min of streaming (a block boundary; do not stop a block mid-run) | same, plus the actual elapsed time |
| End | After the last run in the sitting (including latency-2 if it is shared), before disconnecting | same |

The spot exports are `d-spot-cold`, `d-spot-30min` and `d-spot-end`. The 30 min point may fall during the latency-2 session if that is run first; record the elapsed time and take the reading at the next break rather than interrupting a latency run.

**Reported:** raw `battery` and signed `temperature_raw` at the three points, min/max per run, every change with its timestamp, and any controller sleep or reconnect. No percentage, voltage or °C conversion.

**Paste back for block (d)**

- The trigger and trackpad CSV + JSON runs, and the three spot exports.
- The `STREAM ...` block at the start and end of the block.
- The cold/+30/end table with timestamps and ambient.
- For trackpad, the point order you used (a photo or a one-line sketch is enough).
- Any point where the Gamepad screen and the CSV disagree; the CSV is the record.

## 9. Export fields this sheet needs (M8.2)

The export is assumed to exist (M8.2) and is the source of every number in (a)–(d). One CSV per run, one JSON sidecar with the same stem. Suggested names, following the latency export: `tracking-YYYYMMDD-HHMMSS.csv` and `.json`.

### 9.1 CSV, one row per decoded sample

| Column | Meaning |
|---|---|
| `t_us` | Phone monotonic time of arrival, microseconds since the export started |
| `hand` | `LEFT` or `RIGHT` |
| `seq` | Per-hand wire sequence number; gaps show lost rows |
| `dt_us` | Device-tick step used by fusion; empty on a baseline or discontinuity |
| `q_w`, `q_x`, `q_y`, `q_z` | View-frame orientation after fusion (X right, Y up, Z toward viewer) |
| `yaw_deg`, `pitch_deg`, `roll_deg` | The same orientation as the debug screen shows |
| `accel_wire_x`, `accel_wire_y`, `accel_wire_z` | Wire accel before FF13, m/s² (Monado divisor 49,000) |
| `accel_wire_mag` | Norm of the wire accel |
| `accel_x`, `accel_y`, `accel_z` | Filter input accel after FF13 (if on) and the axis map, m/s² |
| `accel_mag` | Norm of the filter-input accel |
| `gyro_wire_x`, `gyro_wire_y`, `gyro_wire_z` | Wire gyro before FF13, rad/s |
| `gyro_x`, `gyro_y`, `gyro_z` | Filter input gyro: after FF13 and the axis map, runtime bias removed |
| `gyro_bias_x`, `gyro_bias_y`, `gyro_bias_z` | Runtime bias estimate currently subtracted |
| `bias_frame` | `sensor` when calibration is off, `view` when it is on |
| `bias_calibrated` | Whether the estimator has accepted its stationary window |
| `filter` | `madgwick` (or `mahony` if a run is deliberately labelled so) |
| `beta` | Beta applied to this sample; `0.00` for Mahony |
| `calibration` | Per-hand FF13 switch state for this sample, 0/1 |
| `trigger` | Wire trigger, 0..255 |
| `trackpad_x`, `trackpad_y` | FF12 normalized trackpad, −32767..32767; both 0 when `trackpad_touch` is 0 |
| `trackpad_x_raw`, `trackpad_y_raw` | Raw 0..255 if the export has the raw frame; 255 when untouched; empty if it only has FF12 |
| `trackpad_touch` | Buttons bit 6 |
| `battery` | Raw battery byte, 0..255 |
| `temperature_raw` | Signed 16-bit temperature word |
| `buttons` | Raw button byte |

The export must not downsample silently. If it has a rate option, use at least **50 Hz per hand** and record the actual rate in the JSON; 200 Hz per hand is fine and gives a cleaner mean for the analysis.

### 9.2 JSON sidecar

`export_version`, `run_id`, `started_at`, `duration_s`, `app_commit`, `fw_commit`, `bridge_name`, `phone`, `android`, `mtu`, `stream`, `hand_scope` (`both` or `LEFT`/`RIGHT`), `beta`, `filter`, `calibration_left`, `calibration_right`, `block`, `axis`, `pose`, `notes`, and the sample rate. The run log can carry the rest.

## 10. Run log and what to paste back, in one place

Fill one row per export. The JSON is the primary record; this table is for order, validity and anything the files cannot show. If the export auto-names files by timestamp, put the block/run ID in this table; renaming the file is optional, but the CSV and JSON must keep the same stem.

| run_id | block | hand / axis | calibration | beta | start (local) | duration | file stem | valid? | notes |
|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |  |

At the end of the sitting, paste back:

1. Every CSV + JSON from (a), (b), (c) and (d), with the run log above.
2. The serial-monitor log, or at least the `STREAM ...` block at the start and end of every run and the `LINKLOG`/`link:` lines in between. Do not paste screenshots of these lines; text is searchable.
3. The cold/+30/end table with the ambient reading and the actual elapsed times.
4. One photo of the setup, including the 90° box and the controller marks.
5. Any invalid run, with the reason, kept rather than deleted.

No number in (a)–(d) should be pasted from the screen. If a file is missing or a required column is absent, say so; do not reconstruct it from the debug panel.

## 11. Results (to fill after the sitting; no verdict here)

### 11.1 Block (a) — static

| Hand | Calibration | accel_wire_mag median / p5 / p95 (m/s²) | accel_mag median / p5 / p95 (m/s²) | bias x / y / z (wire, rad/s) | bias norm (rad/s) | bias std x / y / z (rad/s) | estimator bias x / y / z (frame, rad/s) | residual gyro x / y / z (rad/s) | samples | bias_calibrated |
|---|---|---|---|---|---|---|---|---|---|---|
| LEFT | off |  |  |  |  |  |  |  |  |  |
| LEFT | on |  |  |  |  |  |  |  |  |  |
| RIGHT | off |  |  |  |  |  |  |  |  |  |
| RIGHT | on |  |  |  |  |  |  |  |  |  |

### 11.2 Block (b) — 90° turns

| Hand | Axis | Calibration | Signed error median (+90 / −90, °) | Absolute error median (°) | Total angle median (°) | Off-axis median (°) | Accepted turns |
|---|---|---|---|---|---|---|---|
| LEFT | yaw | off |  |  |  |  |  |
| LEFT | yaw | on |  |  |  |  |  |
| LEFT | pitch | off |  |  |  |  |  |
| LEFT | pitch | on |  |  |  |  |  |
| LEFT | roll | off |  |  |  |  |  |
| LEFT | roll | on |  |  |  |  |  |
| RIGHT | yaw | off |  |  |  |  |  |
| RIGHT | yaw | on |  |  |  |  |  |
| RIGHT | pitch | off |  |  |  |  |  |
| RIGHT | pitch | on |  |  |  |  |  |
| RIGHT | roll | off |  |  |  |  |  |
| RIGHT | roll | on |  |  |  |  |  |

### 11.3 Block (c) — yaw drift

| Hand | Beta | Calibration | Yaw slope (°/min) | Total yaw change (°) | R² | Pitch change (°) | Roll change (°) | Samples | Gaps |
|---|---|---|---|---|---|---|---|---|---|
| LEFT | 0.02 | off |  |  |  |  |  |  |  |
| LEFT | 0.05 | off |  |  |  |  |  |  |  |
| LEFT | 0.10 | off |  |  |  |  |  |  |  |
| RIGHT | 0.02 | off |  |  |  |  |  |  |  |
| RIGHT | 0.05 | off |  |  |  |  |  |  |  |
| RIGHT | 0.10 | off |  |  |  |  |  |  |  |

Optional rows: `0.05` repeat, and `0.05` with calibration on, labelled separately.

### 11.4 Block (d) — #19 unknowns

| Hand | Trigger max | Trigger first non-zero | Reached 255? | Trackpad corners (TL / TR / BL / BR, raw) | Trackpad centre | Trackpad untouched | Battery cold / +30 / end | Temp cold / +30 / end (raw signed) |
|---|---|---|---|---|---|---|---|---|
| LEFT |  |  |  |  |  |  |  |  |
| RIGHT |  |  |  |  |  |  |  |  |

### 11.5 Invalid runs

| run_id | Block | Reason | Repeated as |
|---|---|---|---|
|  |  |  |  |

## 12. What this sheet cannot show

- Absolute orientation accuracy against an independent reference. The 90° box is a repeatability check, not a surveyed reference; a systematic scale error in the gyro appears as a consistent turn error.
- Long-term drift, temperature-dependent drift, or behaviour across a controller sleep/reconnect beyond the runs recorded here. All of these are one sitting, one phone, one bridge, one pair of controllers.
- Whether calibration “helps” in use. That is a verdict from the numbers, and it is M8.4’s job, not this sheet’s.
- A magnetometer-free yaw fix. Yaw is being measured, not corrected; that is out of scope by design.
- The gamepad mapping as a whole. Block (d) measures the raw ranges behind the trigger and trackpad settings; it does not check the HID output or the host mapping.
- Thermal or battery models. Raw words only; no conversion is implied by a successful run.
- A firmware comparison. No reflash means one build; the sheet cannot separate build effects from run-to-run variation.
- Whether the FF13 calibration changes the 9.81-vs-10 scale. The app’s calibration has no scale factor; (a) can only show whether it distorts the magnitude, and the wire value answers the scale question.
