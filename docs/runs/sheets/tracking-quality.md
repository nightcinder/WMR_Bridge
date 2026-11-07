# Run sheet: tracking quality — calibration, 90° turns, yaw drift, #19

Status: proposed, not run. Needs the tracking CSV export and beta slider in the APK. This is a measurement sheet: no pass/fail threshold and no verdict on calibration. That verdict is M8.4.

Question: is orientation stable, does FF13 calibration change what fusion sees, and what are the real #19 ranges? One sitting on `main`, no reflash: static accel/gyro off and on; 90° turns; yaw drift at three beta values; raw trigger, trackpad, battery and temperature.

## 0. Known

| Item | Before this sheet |
|---|---|
| Accel at rest | 9.9-10.2 m/s² wire; divisor 49,000 gives ~10, not 9.81. Scale open |
| Gyro bias | First still second; never compared with calibration on |
| 90° turns | Not measured; vertical-axis sign unverified |
| Yaw | Drifts with no magnetometer; beta effect unmeasured |
| FF13 | LEFT 7147 B `8bc13223`, RIGHT 7148 B `75e8ab85`, behind a switch, not run |
| Trigger | Wire 0..255; full scale unmeasured |
| Trackpad | Wire 0..100, centre 50, `0xFF` untouched; corners unmeasured |
| Battery/temp | Raw integers only; no cold or warm values |

The FF13 path has a mix matrix, bias and rotation but **no scale factor**. Block (a) shows whether calibration distorts magnitude; the 9.81 question comes from the wire value with calibration off.

## 1. Definitions
- **Run:** one CSV + JSON, one block, hand scope, calibration state and beta.
- **Still window:** (a) 5-55 s; (c) 10-295 s.
- **off/on:** per-hand "Apply FF13 calibration", default off, not persisted.
- **Beta:** Madgwick slider 0.01-0.20, default 0.05. Use 0.02, 0.05, 0.10.
- **View frame:** X right, Y up, Z toward viewer; yaw about Y, pitch X, roll Z.
- **Invalid run:** missing column, stream/calibration changed inside it, wrong beta label, > 2° movement in a static run, gap over 0.5 s, restart or reconnect. Repeat and keep the row marked.

Fixed arithmetic:
- `accel_mag = sqrt(x²+y²+z²)`, wire and filter input. Report median, p5, p95, count.
- Wire gyro bias per axis: mean and std of `gyro_wire_*`, norm, estimator bias with `bias_frame`, residual. `bias_calibrated=false` invalidates the bias.
- Turn: `dq = q_end ⊗ conjugate(q_start)`; `theta = 2*acos(clamp(dq.w))`; `signed = theta * dot(axis, e)` with `e` = Y/X/Z; `off_axis = sqrt(max(0, theta²-signed²))`; `error = signed-90°`.
- Drift: unwrap `yaw_deg`, least-squares slope in **°/min**, total change, R², first/last 5 s medians, pitch/roll change. > 2° pitch/roll invalidates.
- Trigger: max, first non-zero, top plateau, whether 255 was reached.
- Trackpad: report x/y; `raw = round(50 + 50*normalized/32767)`. Untouched is `trackpad_touch=0`, not a corner.
- Battery: raw byte first/last/min/max and changes. No percentage.
- Temperature: signed 16-bit; cold, +30 min, end. No °C conversion.

## 2. Prerequisites

Stop if any row is false.

| Item | Required |
|---|---|
| Firmware | `main` after the FF13 streaming fix (#21); record banner and hash; no reflash during the sitting |
| App | Debug APK after the beta slider and M8.2 export; record commit |
| FF13 key | Bundled in the APK; "Read config" succeeds for both hands before any calibration-on block |
| Controllers | Both charged, connected, awake; note sleep/reconnect and repeat |
| Phone | Same model/Android; battery saver and scanning off; not charging |
| Rig | Table, tape, a box/book for the 90° reference, a fixed prop for static poses |
| Export | Full path per CSV/JSON; serial monitor saved |

The app does not persist beta or the switches. After a restart beta is 0.05 and both switches off; read config again. Confirm before **every** run.

## 3. Setup once
1. Tape the bridge, phone stand and one mark per controller. Record distance and a photo.
2. Start the serial monitor. Copy the `STREAM` block at each run; confirm `bridge_hands=0`.
3. Connect, select **Batched FF14** (or Decoded FF12 for the whole session). Do not mix streams in a block.
4. Read config for both hands; paste length, CRC-32 and READY. If a read fails, skip calibration-on blocks and say which hand failed.
5. Madgwick, beta 0.05, both switches off. Recalibrate gyro bias on each hand; wait 5 s after calibrated.
6. Leave the axis table at defaults. If it changes, record it and do not mix.
7. Take the cold 5 s spot export (§8.3) before moving anything.

Static/drift pose: both upright the same way against a fixed block; tape the block, not the controller. If they cannot stand, lay them flat and record the pose once. Drift is comparable only within the sitting.

## 4. Sharing with latency-2

One flash and APK install, separate sessions. Order: cold spot; tracking (a)-(d); latency-2 after re-taping. Tracking takes 60-75 min. If latency-2 goes first, take the cold spot before it and +30 min at the next block boundary. Do not interleave.

## 5. Block (a) — static, 60 s
1. Both controllers on their marks; touch nothing.
2. Switches **off**. Recalibrate both, wait 10 s, run **60 s**. This is **a-off**.
3. Switches **on**. The estimator restarts; wait 10 s, run 60 s. This is **a-on**.
4. Confirm Madgwick, beta 0.05, same stream and switch state.

Paste both CSV+JSON, `STREAM` start/end, `link:` lines, run-log rows. Screen numbers are only a cross-check.

Report per hand/state: wire and filter accel median/p5/p95; wire gyro bias x/y/z and norm with std; estimator bias with frame; residual; samples; calibrated fraction. No verdict.

## 6. Block (b) — 90° turns

Use a table edge and a box held square. Table top and box face are the stops for pitch/roll; table edge and box perpendicular edge align yaw. Tape the box. Beta 0.05, Madgwick. Run once with switches off, then on. Move one hand at a time; the other stays still as control.

| Axis | Start | End (90° away) |
|---|---|---|
| Yaw (Y) | flat, handle-to-ring line parallel to table edge | same flat pose, parallel to box edge |
| Pitch (X) | upright, ring up, trigger side against rear face | tip toward you until flat |
| Roll (Z) | upright, right side against right face | tip left until flat |

One cycle: hold start 3 s, turn 5 s, hold end 3 s, turn back 5 s, hold start 3 s. Do **3 cycles** per axis (2 minimum). The return is a -90° measure; keep it.

Export one file per hand, axis and state with a **10 s pre-roll**: 3x2x2 = **12 runs**, about 40 s each. Name each row with axis, hand, state, order. If a turn slips, repeat that axis and keep the invalid file marked.

Paste 12 CSV+JSON, `STREAM` at state boundaries, `link:` lines, run log with turn order and slips. Report per hand/axis/state: signed and absolute error, total angle, off-axis, accepted turns, +90/-90 split. No verdict.

## 7. Block (c) — yaw drift, 5 min
1. Controllers back on static marks; touch nothing.
2. Madgwick, beta **0.02**, switches **off**.
3. Reset orientation on both (yaw only); wait 10 s.
4. Run **300 s**. This is **c-beta-0.02**.
5. Repeat at **0.05** and **0.10**, same pose and settle. Optional: 0.05 repeat and one 0.05 with calibration on, labelled separately.

Beta is global, not per hand, and not persisted; check the label before each run.

Paste one CSV+JSON per run with beta and order, `STREAM` start/end, run log and actual duration. Report per beta/hand: yaw slope, total change, R², first/last 5 s medians, pitch/roll change, samples, gaps. No beta recommendation.

## 8. Block (d) — #19 unknowns

**Trigger:** per hand, other still: pull fully 3 s, hold 3 s, release 3 s;
3 pulls. Report max, first non-zero, plateau, whether 255 was reached.

**Trackpad:** per hand hold top-left, top-right, bottom-left, bottom-right,
centre, then untouched, 3 s each; two passes. Report x/y on 0..100, min/max, whether corners reach 0/100, direction, untouched state.

**Battery/temp:** 5 s spot export at cold start (controllers off 10 min),
+30 min streaming, and end. Record `battery`, `temperature_raw`, ambient °C, time. In a shared sitting take +30 min at the next boundary, not mid-run.

Paste trigger/trackpad CSV+JSON, three spots, `STREAM` start/end, the cold/+30/end table, trackpad point order, and any Gamepad-screen disagreement (CSV is the record).

## 9. Export fields

CSV: `t_us`, `hand`, `seq`, `dt_us`, `q_w/q_x/q_y/q_z`, `yaw_deg`, `pitch_deg`, `roll_deg`, `accel_wire_*`, `accel_wire_mag`, `accel_*`, `accel_mag`, `gyro_wire_*`, `gyro_*`, `gyro_bias_*`, `bias_frame`, `bias_calibrated`, `filter`, `beta`, `calibration`, `trigger`, `trackpad_x/y`, optional `trackpad_x/y_raw`, `trackpad_touch`, `battery`, `temperature_raw`, `buttons`.

Do not downsample silently. Use at least **50 Hz per hand** and record the rate.

JSON: `export_version`, `run_id`, `started_at`, `duration_s`, `app_commit`, `fw_commit`, `bridge_name`, `phone`, `android`, `mtu`, `stream`, `hand_scope`, `beta`, `filter`, `calibration_left/right`, `block`, `axis`, `pose`, `notes`, sample rate.

## 10. Paste back
1. Every CSV+JSON from (a)-(d) with the run log.
2. The serial log, or `STREAM` start/end of every run and the `LINKLOG`/`link:` lines between.
3. The cold/+30/end table with ambient and elapsed times.
4. One photo of the setup, box and marks.
5. Every invalid run and reason, kept.

No number comes from the debug screen. If a file or column is missing, say so.

## 11. Results

| Block | Fill |
|---|---|
| a | Per hand/state: accel median/p5/p95; bias x/y/z, norm, std; estimator bias/frame; residual; samples; calibrated |
| b | Per hand/axis/state: signed error +90/-90, absolute error, total angle, off-axis, accepted turns |
| c | Per hand/beta: yaw slope, total change, R², pitch/roll change, samples, gaps |
| d | Per hand: trigger max/first/255; trackpad corners/centre/untouched; battery and temp cold/+30/end |

Invalid runs: id, block, reason, repeated as.

## 12. Limits

Cannot show absolute orientation against a surveyed reference, long-term or temperature drift, whether calibration helps in use (M8.4), a magnetometer yaw fix, full HID mapping, thermal/battery models, a firmware comparison, or whether calibration changes the 9.81-vs-10 scale (no scale factor).
