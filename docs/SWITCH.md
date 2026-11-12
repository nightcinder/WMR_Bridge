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

A captured frame is `A1 21 ... `. Offsets below count from `A1`:

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

## Input report `0x30`

A captured frame is `A1 30 ... ` and is exactly 50 bytes (49-byte report plus the
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
5 ms per older sample. `device_ticks` is 0 because the controller exposes only an
8-bit packet timer, not a usable tick counter.

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

## What is not verified

- The exact output report length and the counter/rumble byte order on this
  BTstack HID host.
- Whether the controller accepts the four-step order, or wants vibration
  enabled (`0x48 0x01`) or player lights first.
- The axis signs. The Joy-Con pair has one IMU axis reversed; the decoder does
  not apply a per-hand axis transform yet.
- Button and stick direction after the WMR mapping, and the Pro Controller's
  right-stick-as-trackpad choice.
- The FF13 blob offsets against the companion app; the app currently expects the
  Microsoft JSON block.

These are the first items for the hardware sheet.
