# Nintendo Switch Joy-Con and Pro Controller

Status: **host-tested and firmware-build-tested, never run on a controller.**
The discovery names, output report `0x01` handshake, SPI calibration packing into
FF13 and the 49-byte `0x30` decoder are implemented and covered by
`tests/test_switch.c`. Nothing here has been confirmed against real hardware.

## Discovery

| Bluetooth name | Hand | Model |
| --- | --- | --- |
| `Joy-Con (L)` | LEFT | `WMR_MODEL_SWITCH` |
| `Joy-Con (R)` | RIGHT | `WMR_MODEL_SWITCH` |
| `Pro Controller` | LEFT | `WMR_MODEL_SWITCH` |

Names are matched case-insensitively after trailing NUL bytes and spaces are
removed, like the WMR and PSVR2 names. Two Joy-Con pair as the two hands. A Pro
Controller has no hand in its name, so it pairs as LEFT; its left stick is the
`stick` pair and its right stick is the `trackpad` pair in the decoded record.

The model is stored in the address record (2 bits per hand, value `2`) and used
again after a reconnect. Firmware that predates Switch support reads that value
as an unknown model and rejects the record, so it falls back to discovery.

## Output report `0x01`

The handshake sends Classic BT HID output report `0x01`: report ID, packet
counter, 8 rumble bytes, subcommand ID, subcommand arguments, zero padding to 49
bytes. The rumble bytes are the neutral `00 01 40 40 00 01 40 40`.

The bridge sends these four subcommands in order after `DESCRIPTOR_AVAILABLE`.
Each is spaced `WMR_SWITCH_HS_INTERVAL_MS` (default 50 ms) after the previous
reply, because the controller answers with a `0x21` input report.

| Step | Subcommand | Arguments | Effect |
| --- | --- | --- | --- |
| 1 | `0x40` | `01` | enable the 6-axis sensor |
| 2 | `0x10` | `20 60 00 00 18` | SPI read: 24 bytes at `0x6020` (IMU calibration) |
| 3 | `0x10` | `3D 60 00 00 12` | SPI read: 18 bytes at `0x603D` (stick calibration) |
| 4 | `0x03` | `30` | select 60 Hz full input report mode |

The `0x10` arguments are a 4-byte little-endian SPI address followed by a 1-byte
length.

A NACK (ACK byte with bit 7 clear) or a short SPI reply retries the same step, up
to `WMR_SWITCH_HS_MAX_TRIES` (3). After that the handshake is `FAILED` and the
controller stays connected without decoded calibration; reconnecting retries.
The state machine is `wmr_switch_handshake_*` in `src/wmr_switch.c`.

## Input report `0x21` (subcommand reply)

A captured frame is `A1 21...`. Offsets below count from `A1`:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | `0xA1` HID input header |
| 1 | 1 | report ID `0x21` |
| 2 .. 13 | 12 | timer, battery/connection, buttons, sticks, vibrator |
| 14 | 1 | ACK byte: bit 7 set = ACK; low bits = reply data type (`0x10` = SPI data) |
| 15 | 1 | subcommand ID being answered |
| 16 .. 20 | 5 | `0x10` replies only: echoed SPI address (4-byte little-endian) and size |
| 21 .. | up to 35 | subcommand reply data (the SPI bytes for `0x10`) |

The handshake checks that the echoed address and size match the request and
retries the step otherwise.

These replies never enter the sample stream and never take a sequence number.

## FF13 calibration blob

Once the two SPI reads succeed, the bridge publishes the 42 raw bytes over FF13
through the same per-hand cache the Microsoft config block uses
(`wmr_cfgmgr_set_blob`). The phone sees the normal 16-byte FF13 window header
and a 42-byte blob:

| Blob offset | Size | Source |
| --- | --- | --- |
| 0 .. 23 | 24 | SPI `0x6020`: six int16 little-endian values (acc origin XYZ, acc sensitivity XYZ, gyro origin XYZ, gyro sensitivity XYZ) |
| 24 .. 41 | 18 | SPI `0x603D`: three 3-byte groups per stick (left max-above-centre/centre/min-below-centre, right centre/min/max), two packed 12-bit axes each. The max/min groups are deltas; the parser stores absolute min/max (`centre +/- delta`) |

The blob is not the Microsoft JSON block and carries no extra version byte; the
FF13 header already identifies the window format. A Switch hand never runs the
Microsoft block read.

This 42-byte blob is what the firmware publishes today
(`WMR_SWITCH_CAL_SIZE`). The companion app's `SwitchCalibration.parse`
accepts only a 44-byte blob that starts with a version and a device-type
byte, so today's firmware and today's app do not interoperate on Switch
calibration. The 44-byte layout is the contract going forward; see
"Calibration blob version 1, 44 bytes" below.

## Input report `0x30`

A captured frame is `A1 30...` and is exactly 50 bytes (49-byte report plus the
HID header). The report carries one button/stick snapshot and three 12-byte IMU
samples, oldest first, 5 ms apart.

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | `0xA1` |
| 1 | 1 | report ID `0x30` |
| 2 | 1 | timer (8-bit) |
| 3 | 1 | battery high nibble, connection low nibble |
| 4 .. 6 | 3 | buttons: right, shared, left |
| 7 .. 9 | 3 | left stick: two packed 12-bit axes |
| 10 .. 12 | 3 | right stick: two packed 12-bit axes |
| 13 | 1 | vibrator feedback |
| 14 .. 25 | 12 | IMU sample 1: accel XYZ, gyro XYZ (int16 LE) |
| 26 .. 37 | 12 | IMU sample 2 |
| 38 .. 49 | 12 | IMU sample 3 |

Every report produces three FF12 records (and up to three records in an FF14
batch). All three carry the raw frame's `sequence`; `rx_time_us` steps back
5 ms per older sample. The current decoder sends `device_ticks = 0` because the
controller exposes only an 8-bit packet timer, not a usable tick counter. That
is the behaviour of the code today; it is not conforming and is replaced by
the "Timing contract" below (specified, not implemented).

An FF14 notification needs an ATT MTU of 173 to carry all three records at once
(`3` ATT bytes + `2` batch header + `3 x 56` record bytes). Below that the newest
records that fit are sent and the older samples of the frame are discarded and
counted in the per-hand `mtu_skip`; FF12 needs only 59 because it carries one
record per notification.

Buttons map to the WMR bits as follows: Home -> `HOME`, Minus/Plus -> `MENU`,
Capture -> `TRACKPAD_CLICK`, either stick click -> `THUMBSTICK_CLICK`, and any of
ZL/ZR/L/R -> `SQUEEZE` (and a full `trigger`). The D-pad and face buttons are not
represented in the shared button byte.

### Calibration and scales

With the SPI calibration loaded, the decoded record sets `CALIBRATED` and uses
the documented factory formulas:

- accel: `raw * 4 / (acc_sensitivity - acc_origin)` g, then `* 9.80665` m/s^2;
- gyro: `(raw - gyro_origin) * 816 / (gyro_sensitivity - gyro_origin)` degrees/s,
  then `* pi / 180` rad/s;
- sticks: linear from the calibrated centre to the min/max on each side, mapped
  to `-1 .. 1`.

Without calibration the decoder uses the documented default scales
(`0.000244` g per accel count and `0.06103` degrees/s per gyro count) and leaves
`CALIBRATED` clear. Values that fail the sanity checks (sensitivity not greater
than origin, min >= centre >= max) are treated as absent rather than guessed.

## Timing contract (specified, not implemented)

Status: **specified, not implemented.** Approved doc-first on 2026-11-13; the
firmware and app change together in one flag-gated revision, held out of the
rc.2 cuts. Nothing here has been run on a controller.
Protocol summary: [PROTOCOL.md](PROTOCOL.md), "Proposals", P1 and P4.

Why. Receivers take `dt` from `device_ticks` (PROTOCOL.md, "Timing"). With
`device_ticks` fixed at 0 a receiver gets `dt = 0` for every record. The app
works around it by spacing the three records of a report itself
(`SwitchBurstTimestamps`), but each new report then starts again from tick 0,
so its tick clock runs backwards once per report and the 100 ms discontinuity
rule fires (read from the app's `switch_burst.dart` and `tick_timer.dart`,
not run). The bridge has the only usable clock, so the bridge generates the
ticks.

Contract, per Switch hand:

1. `device_ticks` MUST advance from every record to the next one of the same
   hand, by at least 1 ms (10000 ticks), in units of 100 ns, modulo 2^32. It
   is never constant and never goes backwards within one boot of the bridge.
   This is the per-hand sample clock every model must carry: a model whose
   controller has a usable counter passes it through unmarked (PROTOCOL.md,
   "Timing"), and Switch is the model that needs the bridge to generate it.
2. The ticks are synthetic: they are derived from the bridge's receive clock,
   the one `rx_time_us` uses. Every Switch record sets `SYNTHETIC_TICKS`
   (`flags` bit 1).
3. Let `rx` be the receive time of the report in microseconds, `prev_rx` the
   receive time of the previous report of this hand, and `last` the synthetic
   time of the newest sample already emitted for this hand.
   - `interval = 5000` for the first report after the controller connects;
     otherwise `interval = clamp((rx - prev_rx) / 3, 1000, 5000)` (integer
     division, microseconds: 1 ms to 5 ms).
   - `newest = rx` for the first report; otherwise
     `newest = max(rx, last + 1000 + 2 * interval)`.
   - Sample `k` (0 = oldest, 2 = newest) has synthetic time
     `t_k = newest - (2 - k) * interval` and
     `device_ticks = (t_k * 10) mod 2^32`.
   - Then `last = newest`, `prev_rx = rx`.
4. `rx_time_us` of sample 2 is `rx`, the real stamp. Samples 0 and 1 carry
   `rx - (2 - k) * interval` (not below 0) and set `RX_TIME_SYNTHETIC`
   (`flags` bit 2). The fixed 5 ms step of today's decoder is replaced by
   `interval`.
5. A controller reconnect does not restart the synthetic clock, because it is
   the bridge's clock. `TICK_RESET` is therefore not set for a Switch hand;
   `controller_epoch` still increments. A bridge restart changes `boot_id`.
6. All three records keep the raw frame's `sequence`, as today.
7. A long gap between reports (lost reports) needs no special case: the
   interval clamps at 5 ms and the tick difference across the gap is the real
   elapsed time on the bridge clock.

The `max` in step 3 only matters when two reports are delivered less than
3 ms apart (a radio stall released them together). The samples of the late
report are then placed 1 ms after the previous ones instead of on top of
them, and the offset is gone at the next normally spaced report.

Worked example (`interval`, times in microseconds; the fourth report arrives
1 ms after the third, the sixth 8 ms after the fifth):

| `rx` | `interval` | `k` | `t_k` | `device_ticks` | `rx_time_us` | `flags` (no calibration) |
| --- | --- | --- | --- | --- | --- | --- |
| 1000000 | 5000 | 0 | 990000 | 9900000 | 990000 | `0x06` |
| 1000000 | 5000 | 1 | 995000 | 9950000 | 995000 | `0x06` |
| 1000000 | 5000 | 2 | 1000000 | 10000000 | 1000000 | `0x02` |
| 1015000 | 5000 | 0 | 1005000 | 10050000 | 1005000 | `0x06` |
| 1015000 | 5000 | 1 | 1010000 | 10100000 | 1010000 | `0x06` |
| 1015000 | 5000 | 2 | 1015000 | 10150000 | 1015000 | `0x02` |
| 1030000 | 5000 | 0 | 1020000 | 10200000 | 1020000 | `0x06` |
| 1030000 | 5000 | 1 | 1025000 | 10250000 | 1025000 | `0x06` |
| 1030000 | 5000 | 2 | 1030000 | 10300000 | 1030000 | `0x02` |
| 1031000 | 1000 | 0 | 1031000 | 10310000 | 1029000 | `0x06` |
| 1031000 | 1000 | 1 | 1032000 | 10320000 | 1030000 | `0x06` |
| 1031000 | 1000 | 2 | 1033000 | 10330000 | 1031000 | `0x02` |
| 1046000 | 5000 | 0 | 1036000 | 10360000 | 1036000 | `0x06` |
| 1046000 | 5000 | 1 | 1041000 | 10410000 | 1041000 | `0x06` |
| 1046000 | 5000 | 2 | 1046000 | 10460000 | 1046000 | `0x02` |
| 1054000 | 2666 | 0 | 1048668 | 10486680 | 1048668 | `0x06` |
| 1054000 | 2666 | 1 | 1051334 | 10513340 | 1051334 | `0x06` |
| 1054000 | 2666 | 2 | 1054000 | 10540000 | 1054000 | `0x02` |

The table was computed from the rules above with a script, not taken from a
device. It is also the shared golden-vector case `switch_synthetic_ticks`:
`tests/vectors/wmr_golden_v1.json` in this repository and
`packages/wmr_core/test/fixtures/wmr_golden_v1.json` in wmr-companion, whose
README describes the format. The 15 ms and 8 ms inputs are the last two reports
in the table (`rx` 1046000 and 1054000): `interval` 5000 and 2666, strictly
increasing ticks, and within-report deltas of 5 ms and 2.666 ms. The case and
its tests land with the implementation, not with this spec change, because the
tests are part of the same flag-gated revision.

Receiver side. A bridge that implements this sets `SWITCH_SYNTHETIC_TICKS` in
`features` (PROTOCOL.md P2). The revision is flag-gated: the firmware and app
change together, and a companion without the feature bit keeps its current
behaviour, so older companions see no change. The app's `SwitchBurstTimestamps`
already keeps the spacing a bridge supplies ("the supplied offset wins"), so it
needs no change for such a bridge; against a bridge without the feature bit it
keeps its own spacing and should also keep the tick clock from running backwards
between reports. The Pi host bridge has no Switch support (`pi/` decodes only
the 46-byte WMR report), so this contract applies to the ESP32 firmware until
that changes.

## Calibration blob version 1, 44 bytes (specified, not implemented)

Status: **specified, not implemented** in the firmware (the app parser for it
exists). Secondary priority. Not run on a controller.

The blob gains a 2-byte prefix so the phone can tell the layout and the
controller type without the Bluetooth name. The 42 bytes that follow are the
bytes the firmware publishes today, unchanged and in the same order:

| Blob offset | Size | Content |
| --- | --- | --- |
| 0 | 1 | version, `1` |
| 1 | 1 | device type: `1` Joy-Con (L), `2` Joy-Con (R), `3` Pro Controller |
| 2 | 24 | SPI `0x6020`: 6-axis factory calibration, 12 x int16 LE (accel origin XYZ, accel sensitivity XYZ, gyro origin XYZ, gyro sensitivity XYZ) |
| 26 | 9 | SPI `0x603D`: left stick factory calibration |
| 35 | 9 | SPI `0x6046`: right stick factory calibration |

The single 18-byte read at `0x603D` in the handshake covers both stick blocks
(`0x603D + 9 = 0x6046`), so no extra SPI read is needed. The FF13 window
header is unchanged: `total_len` is 44 and `crc32` covers the 44 bytes. A
phone identifies a Switch blob by `total_len == 44`, `blob[0] == 1` and
`blob[1]` in 1 .. 3, and falls back to the Microsoft JSON path otherwise. A
bridge that serves this layout sets `SWITCH_BLOB_V1` in `features`
(PROTOCOL.md P2).

Open point to settle before either side's calibrated values are trusted: the
firmware's calibrated gyro formula uses the factor 816
(`src/wmr_switch.c`), the app's `SwitchCalibration` uses 936
(`switch_calibration.dart`). Which one matches a controller has not been
checked here.

## What is not verified

- The exact output report length and the counter/rumble byte order on this
  BTstack HID host.
- Whether the controller accepts the four-step order, or wants vibration
  enabled (`0x48 0x01`) or player lights first.
- The axis signs. The Joy-Con pair has one IMU axis reversed; the decoder does
  not apply a per-hand axis transform yet.
- Button and stick direction after the WMR mapping, and the Pro Controller's
  right-stick-as-trackpad choice.
- The FF13 blob against the companion app. The app has a Switch parser, but
  for the 44-byte layout above, not for the 42 bytes sent today.
- Everything in "Timing contract" and "Calibration blob version 1" (specified,
  not implemented).

These are the first items for the hardware sheet.
