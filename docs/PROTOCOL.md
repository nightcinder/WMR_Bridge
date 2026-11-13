# BLE protocol (raw stream, wire version unchanged from the pre-refactor bridge)

> Transport note. The FF11/FF12/FF14 payloads below are also used by the
> supported Linux/Raspberry Pi host bridge (host-tests-only until the Pi sitting
> runs), which carries them over a WebSocket on Wi-Fi instead of BLE. GATT-specific
> details (UUIDs, CCC descriptors, ATT MTU) do not apply there. See
> [PI_BRIDGE.md](PI_BRIDGE.md) for that transport.

This format is experimental and unversioned. It is exactly what the pre-refactor
firmware sent. The refactor deliberately did not change it. A test
(`tests/test_protocol.c`, "golden packet") pins the byte layout.

## GATT identifiers

| Item | UUID | Properties |
| --- | --- | --- |
| Device name (GAP 0x2A00) | - | read, value `WMR Bridge` |
| Service | `0000FF10-0000-1000-8000-00805F9B34FB` | primary |
| Characteristic | `0000FF11-0000-1000-8000-00805F9B34FB` | read, notify |
| Characteristic | `0000FF12-0000-1000-8000-00805F9B34FB` | notify |
| Characteristic | `0000FF14-0000-1000-8000-00805F9B34FB` | notify |
| Characteristic | `0000FF15-0000-1000-8000-00805F9B34FB` | write without response, notify |
| Characteristic | `0000FF13-0000-1000-8000-00805F9B34FB` | read, write, notify |
| Characteristic | `0000FF16-0000-1000-8000-00805F9B34FB` | read (firmware version) |
| Characteristic | `0000FF17-0000-1000-8000-00805F9B34FB` | read, write, notify (control & status v2) |
| Client Characteristic Configuration (0x2902) | - | read/write |

The rows are in `wmr_bridge.gatt` order. Each characteristic has its own
section below: raw stream and status (this section and the next ones), decoded
stream, batched stream, echo, config block, firmware version, control. Access:
the firmware-version value is the only one readable on an unencrypted link.
Every other value, and every CCC descriptor in the service, needs an encrypted
and bonded link with a 16-byte key (see "Security and pairing").

Advertising: flags `0x06` (LE general discoverable, BR/EDR not supported),
complete local name `WMR Bridge`, complete list of 16-bit service UUIDs `0x1812`
(HID) and `0xFF10`. The scan response adds only the gamepad appearance `0x03C4`.
The HID service is in the primary advertisement so a HOGP host that skips the
scan response still sees a HID device (see [HID_GAMEPAD.md](HID_GAMEPAD.md)).
Interval 100-150 ms (`WMR_CFG_ADV_INTERVAL_MIN/MAX`), all three channels.

The FF10/FF11 UUIDs are experimental and not assigned for production use
(see `wmr_bridge.gatt`). The staged replacement with project-owned 128-bit UUIDs
is in [BLE_MIGRATION.md](BLE_MIGRATION.md).

## Security and pairing (0.3 firmware)

Security is part of the wire contract from 0.3.0, not an app-side option.

| Item | Access without a bond |
| --- | --- |
| GAP device name (0x2A00), appearance (0x2A01) and preferred connection parameters (0x2A04) | readable |
| GATT database hash (0x2B2A) | readable |
| DIS manufacturer, model, firmware revision and PnP ID | readable |
| HID protocol mode, report map, HID information and boot reports | readable; boot reports return zero length |
| HID control point | writable (write without response) |
| HID boot-report CCCs | readable and writable |
| FF16 firmware version | readable |
| FF11, FF12, FF13, FF14, FF15, FF17 values | refused |
| CCC descriptor of every stream/echo/config/control characteristic | refused |

A read or CCC write before pairing fails with ATT `Insufficient Encryption`
(`0x0F`) or `Insufficient Authentication` (`0x05`) and triggers the OS pairing
flow. A client should wait for pairing to finish and retry once. A failed
pairing is reported to the user rather than retried forever.

The bridge:

- accepts only LE Secure Connections pairing (legacy pairing is refused by the
  peripheral) and requires bonding. There is no display or keypad, so the
  association model is Just Works. The link is encrypted but not
  MITM-protected.
- sends a Security Request as soon as a central connects, so the pairing dialog
  appears without an app-specific call.
- serves only a central that is both encrypted and bonded. A failed pairing, a
  pairing that stored no bond, or no bonded link within 30 s
  (`WMR_CFG_BLE_PAIR_TIMEOUT_MS`) results in a disconnect.
- serves one phone at a time. A second central is disconnected as soon as it
  connects.
- stores the bond in NVS. The serial console's `P` key deletes all phone bonds
  (refused while a phone is connected). `k`/`K`/`b` are controller-only resets.

Fragmentation, sequence numbers and all payload layouts are unchanged by
pairing. The encrypted link carries exactly the bytes described below. See
[SECURITY.md](SECURITY.md) for the threat model and
[BLE_MIGRATION.md](BLE_MIGRATION.md) for the UUID cutover and the app rollout.

## Two uses of FF11

- Notifications carry the controller stream (below). Enabled by writing
  `0x0001` to the CCC descriptor.
- A read of FF11 returns a 16-byte status value, not stream data:

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | u8 | Status version, currently 1 |
| 1 | u8 | bit0 LEFT connected, bit1 RIGHT connected |
| 2 | u8 | bit0 LEFT HID descriptor received, bit1 RIGHT |
| 3 | u8 | Reserved, 0 |
| 4 | u32 LE | Seconds since boot (tick counter, not a frame sequence) |
| 8 | u32 LE | LEFT reports received on the current connection |
| 12 | u32 LE | RIGHT reports received on the current connection |

## Firmware version, FF16

A read returns a UTF-8 string with no terminator: the ESP-IDF `PROJECT_VER`
value embedded in the application descriptor. `tools/firmware/build.py` sets it
to the same version that it writes to `manifest.json` and `build-info.json`, so a
client can compare the running firmware with a release without a serial console.
A build made directly with `idf.py` reports that project's `PROJECT_VER`
(ESP-IDF's default is `1`).

The value is at most 31 bytes (the descriptor field is 32 bytes including the
terminator). This characteristic is independent of the FF11/FF12/FF14 stream
exclusion and of FF13/FF15. For the full compatibility matrix mapping FF16
strings to firmware characteristics and client library packages, see
[docs/COMPATIBILITY.md](COMPATIBILITY.md).

## Notification format

All multi-byte integers are little-endian. Every notification value is one
fragment: a 12-byte header followed by a chunk of one controller frame.

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `magic` | `0x52` (`'R'`) |
| 1 | u8 | `hand` | 0 = LEFT, 1 = RIGHT |
| 2 | u32 | `sequence` | Per-hand frame number (see rules) |
| 6 | u16 | `total_len` | Length of the whole frame in bytes (46 for WMR, 79 for PSVR2, 50 for Switch) |
| 8 | u16 | `offset` | Offset of this chunk within the frame |
| 10 | u16 | `chunk_len` | Bytes of frame data in this notification |
| 12 | `chunk_len` | `data` | `frame[offset .. offset+chunk_len)` |

The frame is the controller's HID input report exactly as BTstack delivers it:
byte 0 = `0xA1` (HID DATA/input transaction header), byte 1 = report ID, then
the payload. A WMR report is report ID `0x01` with 44 payload bytes, 46 bytes
in total. A PSVR2 Sense report is report ID `0x31` with 77 payload bytes,
79 bytes in total. A Switch report is report ID `0x30` with 48 payload bytes,
50 bytes in total (Switch references and `WMR_SWITCH_REPORT_SIZE` count the
49-byte report without the `0xA1` header). The raw stream
carries any frame unchanged; FF12/FF14
apply the model's decoder (see [PSVR2.md](PSVR2.md), [SWITCH.md](SWITCH.md)).
PSVR2 and Switch names are searched for by default alongside the WMR names from
0.2 onward. The 12-byte fragment
header is identical for both.

Byte-count convention. Every frame length in this document (`total_len`, and
the figures 46, 79 and 50) counts the frame as BTstack delivers it: the `0xA1`
header byte, the report ID byte, then the payload. The HID report without the
`0xA1` byte is one byte shorter (45, 78, 49); other references, including
[SWITCH.md](SWITCH.md) in places and the Nintendo notes, use that count. The
Pi host bridge prepends `0xA1` to what hidraw returns so its frames follow the
same convention.

Example (RIGHT, sequence `0x01020304`, 46-byte frame, notification capacity 44
bytes), first fragment header then second fragment header:

```
52 01 04 03 02 01 2E 00 00 00 20 00   | 32 data bytes   (offset 0,  chunk 32)
52 01 04 03 02 01 2E 00 20 00 0E 00   | 14 data bytes   (offset 32, chunk 14)
```

## Fragmentation

- The notification capacity is `min(ATT_MTU - 3, 244)`: 3 bytes are ATT opcode
  + handle, 244 is `WMR_CFG_MAX_NOTIFICATION`.
- Data per notification = capacity - 12. With the default ATT MTU of 23 the
  capacity is 20 and a 46-byte WMR frame takes 6 notifications (8,8,8,8,8,6
  bytes). A 79-byte PSVR2 frame takes 10 notifications at that MTU (8*9 + 7
  bytes). With ATT MTU >= 61 a WMR frame fits in one notification (58 bytes). A
  PSVR2 frame needs ATT MTU >= 94 (capacity >= 91).
- If the negotiated MTU is 15 or less, nothing can be sent (no room for a
  payload byte).
- Fragments of one frame are sent consecutively and in order before any fragment
  of the next frame. The two hands are interleaved at frame granularity, never
  within a frame. A conforming receiver should still keep separate reassembly
  state per hand (the reference reassembler does).

## Receiver rules (reference: `wmr_reassembler_push`)

A fragment is valid if `magic == 0x52`, `hand < 2`, `chunk_len ==
notification_length - 12`, `chunk_len > 0`, `0 < total_len <= 80`,
`offset < total_len` and `offset + chunk_len <= total_len`.

For a valid fragment of hand h:

1. `offset == 0`: start a new frame. If a frame for h was in progress it is
   abandoned (counted as incomplete, never emitted).
2. `offset > 0`: accept only if a frame for h is in progress and `sequence` and
   `total_len` match it and `offset` equals the bytes received so far. Otherwise
   abandon the frame in progress and ignore the fragment (a late or duplicated
   fragment must not resurrect an abandoned frame).
3. The frame is complete when the received bytes reach `total_len`.

An invalid fragment is counted as an error and also abandons the frame in
progress for its hand (if its `hand` field is valid).

The limit 80 is the spec value (`WMR_CFG_MAX_FRAME` in the firmware,
`MAX_FRAME` in `pi/wmr_bridge/protocol.py`). A receiver MUST accept every
`total_len` from 1 to 80. A receiver with a smaller limit drops PSVR2 frames
(79 bytes) as malformed; the companion app's reassembler currently uses 64
(`wmrMaxFrame`), which is a known app-side deviation, not a second spec value.

Tests: `tests/test_protocol.c` covers fragmented, incomplete, lost,
duplicated, interleaved and malformed fragments.

## Sequence numbers

- One counter per hand, starting at 0 at boot. The first frame is numbered 1.
  It increments for every report received from that controller, whether or not it
  is delivered to the phone.
- Consequently the phone sees gaps in `sequence` for frames lost before
  transmission: while no phone was subscribed (`offline`), dropped (`drop`), or
  purged at a queue reset (`purged`). The first sequence number after
  subscribing is therefore generally not 1.
- Sequence numbers do not reset when a controller or the phone reconnects. They
  reset only when the ESP32 reboots. A reboot is visible as a drop back to
  small values.
- The counter is `uint32_t` and wraps. Use modular arithmetic when comparing.
- A gap proves loss before the BLE stack accepted the frame. The absence of a
  gap does not yet prove lossless end-to-end delivery. See
  [COUNTERS.md](COUNTERS.md) and [TESTING.md](TESTING.md).

# Decoded stream, FF12 (version 1; implemented in firmware, run on hardware: user-reported)

Status: approved. Firmware implemented and merged (first on branch
`ff12-decoded-stream`). Host-tested and built. Run on hardware with two WMR
controllers and an Android phone, user-reported (figures in
[STATUS.md](STATUS.md) and [DECODING.md](DECODING.md)); the remaining
checklist items are in [TESTING.md](TESTING.md). PSVR2 and Switch decoding
have never run on a controller.
The reference pack/unpack is `src/wmr_decoded.{h,c}`, pinned by the
"decoded frame (FF12) golden packet" test in `tests/test_protocol.c`, so the
Android app can build and test against exact bytes. The raw FF11 stream above is
unchanged. The 56-byte record from the first proposal is kept as it was. The
decisions below did not require changing it.

## Why a second characteristic

FF11 stays the byte-for-byte raw pipe (its golden-packet test pins it). Decoded
samples go on a new characteristic so a receiver can choose raw or decoded.
Fusion (orientation) and calibration run on the phone. This stream carries IMU
samples, not a quaternion. The app normally uses FF12. FF11 is for a debug mode.

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF12-0000-1000-8000-00805F9B34FB` | notify only (in service `FF10`) |
| CCC (0x2902) | - | read/write |

There is no read value.

## Subscription: FF11, FF12 and FF14 are mutually exclusive

Decoded records go only to a phone subscribed to FF12. Raw fragments go only to a
phone subscribed to FF11. Batches of decoded records
([FF14](#batched-decoded-stream-ff14-version-2-implemented-in-firmware-run-on-hardware-user-reported))
go only to a phone subscribed to FF14. At most one of the three is ever active,
which keeps the BLE budget at one stream's worth.

- Subscribing to a second stream is refused. While one of the three CCC
  descriptors is enabled, a write that would enable another fails with ATT
  application error `0x80` ("other stream active"). The first subscription is
  unaffected. To switch, write `0x0000` to the active CCC first, then enable the
  other. This holds in every order (FF11 then FF12, FF11 then FF14, FF12 then
  FF11, FF12 then FF14, FF14 then FF11, FF14 then FF12). It is one rule in
  `wmr_stream_mode_ccc_write` and is host-tested for all six.
- The CCC state is per connection and resets on disconnect, so there is no stale
  state to clean up after a reconnect.
- Why refuse instead of letting one silently win: a client that thinks it is
  getting both streams would otherwise see one go quiet with no error. Refusing
  makes the mistake visible at the moment it is made. (Review item: the
  alternative is to accept both and give FF12 precedence.)
- Sequence numbers are shared. Each hand has one counter, incremented for every
  input report received from that controller regardless of subscription state or
  which stream is active. Switching streams therefore looks like a gap (the
  frames received while unsubscribed count as `offline`), not a reset.

## Notification format

One notification = one complete sample = exactly 56 bytes (no fragmentation). All
integers are little-endian. Floats are IEEE 754 binary32, little-endian.

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1`. A receiver must ignore records with an unknown version |
| 1 | u8 | `hand` | 0 = LEFT, 1 = RIGHT |
| 2 | u8 | `flags` | bit0 `CALIBRATED`: 0 for WMR and PSVR2 records, set for a Switch record decoded with a valid factory calibration (see below). Other bits 0 today; bits 1-3 are proposed in "Proposals" |
| 3 | u8 | `buttons` | bit0 thumbstick click, bit1 home, bit2 menu, bit3 squeeze click, bit4 trackpad click, bit5 BT pairing, bit6 trackpad touch, bit7 0 |
| 4 | u32 | `sequence` | The same per-hand sequence number as the raw FF11 frame this sample was decoded from. Gaps = loss before transmission |
| 8 | u64 | `rx_time_us` | ESP32 `esp_timer_get_time()` when the HID report was received, microseconds since ESP32 boot. For latency and jitter diagnostics only; not a sensor clock |
| 16 | u32 | `device_ticks` | Primary clock for sensor timing. Controller's own timer, 100 ns per tick, wraps at 2^32 (see "Timing") |
| 20 | 3 x f32 | `gyro` x, y, z | rad/s, uncalibrated, raw sensor axes |
| 32 | 3 x f32 | `accel` x, y, z | m/s^2, uncalibrated scale, raw sensor axes |
| 44 | i16 | `temperature_raw` | Raw sensor word, scale not established |
| 46 | 2 x i16 | `stick` x, y | -32767 .. 32767 = -1.0 .. 1.0, 0 = centre |
| 50 | 2 x i16 | `trackpad` x, y | -32767 .. 32767 = -1.0 .. 1.0; both 0 when `trackpad touch` is clear |
| 54 | u8 | `trigger` | 0 .. 255 |
| 55 | u8 | `battery` | Raw controller byte, scale not established |

### Scales

- Gyro: raw signed 24-bit word times `1e-5` rad/s (Monado's factor).
- Accel: raw signed 24-bit word divided by `49000` (Monado's factor). Monado notes
  1 g is about 490000 raw, so this is about m/s^2 with an uncertainty of a few
  percent (the stationary samples read 10.0-10.1, not 9.81). Not a calibrated
  figure.
- Stick: 12-bit value, `(raw - 0x7FF) / 0x7FF`, clamped to 1.0, then times
  32767.
- Trackpad: `(raw - 0x32) / 0x32`, then times 32767. Raw `0xFF` (untouched)
  gives 0.
- These factors are from Monado and verified only to the extent described in
  [DECODING.md](DECODING.md) (stationary magnitude, noise level). A rotation
  capture is planned to check the gyro scale (a full turn should integrate to
  2 pi).

#### PSVR2 `0x31` frames

A PSVR2 hand decodes through its driver entry (`wmr_controller_driver.c`,
`wmr_decoded_from_psvr2_frame`). The frame must be exactly 79 bytes with the
`A1 31` header. Offsets and scales follow Monado's `pssense` driver: gyro is
raw x `pi/1024` rad/s; accel is raw x `9.80665/4096` m/s^2 (+-8 g); the stick is
0 .. 255 with x `(raw - 128)/128` and y `(128 - raw)/128`; the trigger is 0 .. 255;
`device_ticks` is the controller IMU counter times 333 ns, converted to the
record's 100 ns ticks. Temperature is a raw degrees-C byte and battery is the
raw battery-state byte (high nibble charge state, low nibble level). Only PS,
Share/Options, L1/R1 and L3/R3 map to the WMR button bits; face buttons and
capacitive touches are not represented. `trackpad` stays 0 and these samples
are uncalibrated (`flags` bit 0 clear). The report CRC is not checked. Full
field table: [PSVR2.md](PSVR2.md).

#### Switch `0x30` frames

A Switch hand decodes through its driver entry (`wmr_controller_driver.c`,
`wmr_switch_decode_samples`). The frame must be exactly 50 bytes with the
`A1 30` header. One report carries three 12-byte IMU samples 5 ms apart and one
button/stick snapshot, so it produces three FF12 records (oldest first). All
three carry the raw frame's `sequence`; `rx_time_us` steps back 5 ms per older
sample and `device_ticks` is 0 (the controller has only an 8-bit packet timer).
Accel and gyro use the SPI factory calibration read by the handshake when it is
valid, otherwise the documented default scales; stick axes use the calibrated
min/centre/max when valid. `CALIBRATED` is set only when the IMU calibration is
in use. Home, Minus/Plus, Capture, stick click and ZL/ZR/L/R map to the WMR
button bits; the D-pad and face buttons do not. A Pro Controller pairs as LEFT
and exposes its right stick as the `trackpad` pair. Full field table and open
questions: [SWITCH.md](SWITCH.md).

A constant `device_ticks` of 0 breaks the "Timing" rules below for every
receiver that takes `dt` from the ticks. The replacement, the Switch timing
contract, is specified in [SWITCH.md](SWITCH.md#timing-contract-specified-not-implemented)
and summarised under "Proposals" (specified, not implemented).

### Calibration is done on the phone

For WMR and PSVR2 hands FF12 carries uncalibrated values: `flags` bit 0 is 0.
The one exception today is a Switch hand on the ESP32, whose decoder applies
the SPI factory calibration and sets bit 0 (`wmr_switch.c`, "Switch `0x30`
frames" above). Each WMR sample is in the
controller's raw sensor axes, only scaled to physical units. The phone applies
the per-device mix matrix, bias and pose from the controller's firmware config
block, which the bridge exposes unmodified on FF13 (below). A receiver must
read `flags` per record and not assume: a record with bit 0 set must not get
the FF13 calibration applied a second time. Axis directions and signs of the raw axes are unverified
until the rotation capture in [DECODING.md](DECODING.md) is done.

## Timing

- `device_ticks` is the sensor clock. The app uses the difference between
  successive samples of the same hand as `dt` for its fusion filter.
- Unit: 100 ns per tick (10 MHz). Taken from Monado, where
  `WMR_MOTION_CONTROLLER_NS_PER_TICK` is 100 and
  `wmr_controller_base_handle_imu_sample` computes
  `sample_time_ns = timestamp_ticks * 100`
  (monado commit `f50c796c32c811f5fd371f77fd8a749d2ad7df08`). Measured on
  hardware, user-reported: ticks over wall-clock 1.0016 (RIGHT) and 1.0083
  (LEFT), median report period about 4.99 ms ([DECODING.md](DECODING.md)).
  The capture procedure there checks it two ways: elapsed ticks against
  elapsed wall-clock time, and the gyro integral over a known rotation.
- Wrap. The counter is an unsigned 32-bit value and wraps at 2^32 ticks, which
  is 429.4967296 s (about 7 min 9.5 s) at 100 ns. Compute
  `dt_ticks = (uint32_t)(ticks - prev_ticks)` (unsigned modulo arithmetic,
  correct across a wrap) and accumulate into a 64-bit total. This is valid
  while successive samples of the same hand are less than 2^32 ticks (429.5 s)
  apart, which holds unless the link is lost for longer than that. It is
  equivalent to Monado's "if the low half went down, add 2^32".
- Hands have independent clocks. LEFT and RIGHT `device_ticks` have unrelated
  origins. Never subtract one from the other. Use `rx_time_us` for coarse
  cross-hand alignment only.
- Discontinuities. It is not established whether a controller restarts its timer
  when it reconnects. A receiver should therefore compare `dt_ticks * 100 ns`
  with `delta(rx_time_us) * 1000 ns` for the same two samples. If they differ by
  more than about 100 ms, treat it as a discontinuity and reset the filter for
  that hand. A sequence gap alone is not a discontinuity. Ticks keep counting
  through lost samples, so `dt_ticks` is still the true elapsed time.
- `rx_time_us` (ESP32 receive time) includes Bluetooth scheduling jitter and is
  only for measuring latency and jitter, for example against a phone-side clock.
  On the Pi host bridge it is weaker still: a user-space stamp taken when the
  daemon reads the report from hidraw, so reports read in one burst get nearly
  identical stamps (see "Linux/Pi host bridge differences"). Receivers must
  take sample timing from `device_ticks` on both bridges.
  It answers the one-way part of issue #1 (closed). The round trip is the
  [FF15 echo](#echo-ff15-version-1-approved-implemented-in-firmware-run-on-hardware-user-reported),
  whose answer carries a timestamp on the same clock.

## Delivery rules

- While FF12 notifications are enabled, the ESP32 sends one FF12 notification
  per received input report. It uses the same bounded frame queue as FF11 (only
  one of the two streams is ever active, so there is no contention). A full
  queue drops the newest sample and counts it in `drop`, as for FF11.
- Where decoding happens. The HID callback only copies the report and stamps
  `rx_time_us` (`esp_timer_get_time()`) at arrival. Decoding into the 56-byte
  record happens when the notification pump takes a frame from the queue, and
  only while FF12 is the active stream.
- Minimum MTU: 59 (56 + 3 bytes of ATT opcode and handle). Below that nothing
  is sent on FF12. Each report received while FF12 is active and the MTU is
  below 59 is discarded and counted in the per-hand `mtu_skip` statistic (it is
  not queued). Its sequence number is still consumed, so the phone sees the gap
  once the MTU is raised. A frame that was already queued when the MTU turned out
  to be too small is dropped and counted the same way. This is a deliberate
  difference from FF11, where a tiny MTU leaves frames queued until the queue
  fills and `drop` rises. Request a large MTU before enabling FF12 (the same
  step as for FF11).
- A queued frame that is not a valid input report (wrong length,
  transaction header, or report ID) is discarded and counted in the per-hand
  `dec_err` statistic. Config responses are not expected here. They are
  separated by report ID before this point in the FF13 design (implemented, see
  below). A PSVR2 `0x31` frame is decoded by its driver entry when it is exactly
  79 bytes; a short or malformed `0x31` frame is counted in `dec_err`
  ([PSVR2.md](PSVR2.md)). A Switch `0x30` frame is accepted only at exactly
  50 bytes and produces three FF12 records; a short or malformed `0x30` frame is
  rejected by its input-report check before it is queued, so it increments
  `other_reports`, not `dec_err` ([SWITCH.md](SWITCH.md)).
- Samples of the two hands may interleave freely. Ordering is per hand by
  `sequence`.

## Bench-test items (not blockers)

- Both hands at about 360 notifications/s (56 bytes each) on one connection:
  does the negotiated connection interval sustain it with no drops and bounded
  queue depth? Measured only for the raw stream so far (about 154-174 reports/s
  per hand).
- `device_ticks` unit and wrap behaviour on hardware (see Timing).
- Whether `device_ticks` restarts on controller reconnect.
- Refused-CCC behaviour on the target Android versions (the app must handle a
  failed descriptor write).
- GATT: FF12 is `NOTIFY | DYNAMIC` in `wmr_bridge.gatt`, directly after FF11
  in service `FF10`. FF11 is unchanged.

# Batched decoded stream, FF14 (version 2; implemented in firmware, run on hardware: user-reported)

Status: firmware implemented and merged (first on branch `ff14-batching`).
Host-tested and built. Run on hardware, user-reported: it is the default
stream of the recorded two-controller runs ([STATUS.md](STATUS.md),
[`runs/roundtrip/`](runs/roundtrip/README.md)). Checklist items without a
recorded run stay open in [TESTING.md](TESTING.md). Reference code:
`src/wmr_batch.{h,c}`. FF11 and FF12 are unchanged. FF14 is an additional
notify-only characteristic that carries the same 56-byte FF12 version 1 records,
several per notification.

## Why

Both hands stream about 160 reports/s each (reported), so FF12 sends about 320
notifications/s. A decimation experiment (`WMR_CFG_FF12_DECIMATE`) was run by
the user at one fixed table position with the controllers face down and still,
MTU 517, connection interval 15 ms (user-reported, single runs, one session; the
`last5s` missing-report share per hand):

| Run | LEFT | RIGHT |
| --- | --- | --- |
| no phone (N=4 build) | 9-11 % | 9-11 % |
| N=1, first run | 22-25 % | 36 % |
| N=1, repeat | 19 % | 28-36 % |
| N=2 | 18-20 % | 14-15 % |
| N=4 | 18-20 % | 12-13 % |

Reading, as given by the project owner and not independently re-checked here:
sending fewer notifications cost RIGHT up to about 20 points less loss, and the
effect repeated. LEFT rose from about 10 % to about 19 % as soon as the phone
streamed and stayed there at N=1, 2 and 4, so LEFT's extra loss is not
explained by notification volume, and FF14 is not claimed to fix LEFT. Decimation
drops samples. FF14 sends all of them in fewer notifications. Whether that gives
the same relief is exactly what the FF14 bench runs in TESTING.md are for.
Everything above is one position and one session.

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF14-0000-1000-8000-00805F9B34FB` | notify only (in service `FF10`) |
| CCC (0x2902) | - | read/write |

There is no read value. GATT: `NOTIFY | DYNAMIC` in `wmr_bridge.gatt`, directly
after FF12.

## Notification format

One notification value = a 2-byte header followed by `count` complete FF12
version 1 records, unchanged (each keeps its own `version` = 1, `hand`,
`sequence`, `rx_time_us`, `device_ticks`, ... ):

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `2` |
| 1 | u8 | `count` | Records that follow, 1 to `(MTU - 3 - 2) / 56`, at most 9 |
| 2 | 56 x `count` | records | FF12 version 1 records, oldest first. Records of both hands may share a batch (each record carries its own `hand`). Order is by arrival, and per hand by `sequence` |

Total size = `2 + 56 x count`, never more than `MTU - 3`. The most records per
notification: 4 at MTU 247, 5 at MTU 285, 9 at MTU 517 (the largest ATT MTU).
Pinned by the golden-bytes tests for a batch of 1, 2 and the maximum in
`tests/test_batch.c`.

A receiver reads `version`, rejects anything other than 2, takes `count`, and
requires the value length to be exactly `2 + 56 x count`
(`wmr_batch_parse` is the reference). Records are then parsed as in the FF12
section.

## Minimum MTU: 61

`3 + 2 + 56` = 61 (one record). Below 61 nothing is sent on FF14. As for
FF12, each report received while FF14 is active and the MTU is below 61 is
discarded and counted in the per-hand `mtu_skip` (not queued, sequence number
still consumed). Note that FF12 needs only 59, so an MTU of 59 or 60 works for
FF12 and not for FF14. Request a large MTU before enabling FF14.

## When a batch is sent

Reports go into the same bounded frame queue as FF11 and FF12 (a full queue
drops the newest and counts it in `drop`, exactly as for FF12. Batching drops
nothing itself). A batch is sent from the queue when the ATT stack can send
(`ATT_EVENT_CAN_SEND_NOW`), and only when one of these holds:

- a full notification is queued: `(MTU - 3 - 2) / 56` records (9 at MTU 517)
  or more, counted in `batch_full_flushes`; or
- the oldest queued record has waited one connection interval (the interval the
  phone granted; 15 ms until it is known, `WMR_CFG_BATCH_HOLD_DEFAULT_US`): the
  queue is then flushed as a partial batch. `WMR_CFG_BATCH_HOLD_INTERVALS`
  (default 1) multiplies the hold. 2 is the airtime variant and adds
  about one interval of batch latency.

So a record is held for batching no longer than the configured number of
connection intervals, checked by a timer that is armed for exactly the remaining
time (plus up to one report period, about 5 ms, if a check is delayed). The
decision is the pure function `wmr_batch_flush_reason`, and its bound is
simulated in `tests/test_batch.c`. This bound is on the batching hold. If the
BLE stack cannot send (the previous notification has not been accepted yet, or
the link is busy) records wait in the queue longer, exactly as FF12 records do.
Decimation is not applied on FF14 (a diagnostic setting for FF12 only). Whether
the ESP32 BLE stack really sends one batch per connection event is not
guaranteed and has not been measured.

On a send error the batch stays queued and is sent again (a send error never
discards records). A queued frame that is not a valid input report for its
model is removed and counted in `dec_err`, and takes no slot in the batch.

A Switch `0x30` frame yields three records but a notification holds
`(MTU - 5) / 56` of them, so FF14 needs an ATT MTU of at least 173 for the whole
frame. Below that the newest records that fit are sent and the older samples are
discarded and counted in the per-hand `mtu_skip`; the frame is still popped and
its sent records count in `tx`.

## Counters and the accounting identity

Per-hand counters are those of FF12. On FF14, `tx` counts records (each valid
record in an accepted batch counts once for its hand), not notifications. The
global counters:

| Counter | Meaning |
| --- | --- |
| `batches_tx` | Batches accepted by the BLE stack (FF14 only) |
| `records_in_batches` | Records inside those batches |
| mean batch size | `records_in_batches / batches_tx` |
| `batch_max` | Most records in one batch so far |
| `batch_full_flushes` | Batches sent because a full one was queued. The rest were sent on the hold time |
| `notify` / `notifications` | BLE notifications accepted. Under FF14 this is the number of batches |

Identity (see [COUNTERS.md](COUNTERS.md)): `rx = tx + drop + offline + purged +
mtu_skip + dec_err + decimated + queued` holds per hand as for FF12, with `tx`
in records. Under FF14 `batches_tx` equals the growth of `notify`, and
`records_in_batches` equals the growth of `tx` summed over both hands (both
counted since boot, so they match exactly only if no other stream ran in that
boot).

## Loss pattern and latency

One lost notification now loses up to `count` records (9 at MTU 517) instead of
one. They are still detectable by per-hand `sequence` gaps. A record can wait up
to one connection interval for its batch. A v1 record also waits for its
connection event. `device_ticks` stays the sensor clock and `rx_time_us` is per
record, so fusion timing does not depend on the batch. Latency is not measured.

## An FF12 or FF11 receiver

An app that never subscribes to FF14 never sees a version 2 value. A version 1
parser that were pointed at FF14 would ignore it silently (`version` is byte 0),
which is the reason FF14 is a separate characteristic rather than a mode of
FF12.

## Bench-test items

See [TESTING.md](TESTING.md): FF14 on a phone at MTU 517 and 247, the three
ordered exclusion cases, MTU below 61, and the A/B/A comparison of FF12 and
FF14 at one fixed position. Runs that exist are recorded in
[STATUS.md](STATUS.md) and under [`runs/`](runs/README.md) as user-reported;
an item with no record there has not been run.

# Echo, FF15 (version 1; approved, implemented in firmware, run on hardware: user-reported)

Status: layout approved by the project owner (2026-10-22, issue #1). Firmware
implemented and host-tested. Run on hardware, user-reported: the round-trip
runs of 2026-10-22 and later are in
[`runs/roundtrip/`](runs/roundtrip/README.md). Open checklist items are in
[TESTING.md](TESTING.md). Reference code: `src/wmr_echo.{h,c}`, pinned by the
golden-bytes tests in `tests/test_echo.c`. FF11, FF12 and FF14 are unchanged, and
no existing handle moves (FF15 is appended after FF14 in `wmr_bridge.gatt`).

## Why

The streams carry no phone-side reference, so the phone can measure
inter-arrival jitter but not latency. FF15 gives a BLE round trip that can be
measured while a stream is running. The phone writes a small ping, and the
ESP32 answers on the next send opportunity, ahead of every frame in the stream
queue. The answer also carries the ESP32 receive time on the same clock as
FF12's `rx_time_us`, so the phone can relate the two clocks (see "Using it").

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF15-0000-1000-8000-00805F9B34FB` | write without response, notify (in service `FF10`) |
| CCC (0x2902) | - | read/write |

`FF13` is left for the config block design below. `FF15` is the next free
number. There is no read value. A write with response is refused by the stack
(the characteristic does not declare it).

## Not part of the stream exclusion

FF15 is independent of the FF11/FF12/FF14 rule. Its CCC can be enabled with or
without any of the three, and enabling it never returns `0x80`. Like the others,
its CCC state is per connection and is cleared when the phone connects or
disconnects.

## Request (phone to ESP32): 4 bytes, write without response

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1` |
| 1 | u8 | reserved | Send 0. Ignored by the ESP32 |
| 2 | u16 | `ping_id` | Any value chosen by the phone. Copied into the answer |

A request of any other length or version is ignored and counted (`bad`). Because
it is a write without response, nothing tells the phone that a request was
ignored except the missing answer.

## Answer (ESP32 to phone): 20 bytes, notification

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1`. A receiver must ignore an answer with an unknown version |
| 1 | u8 | reserved | 0 |
| 2 | u16 | `ping_id` | From the request |
| 4 | u64 | `rx_time_us` | ESP32 `esp_timer_get_time()` when the request reached the ATT write callback, microseconds since ESP32 boot. The same clock as FF12 `rx_time_us` |
| 12 | u32 | `turnaround_us` | Time from `rx_time_us` to just before the answer was handed to `att_server_notify`. Saturates at `0xFFFFFFFF` |
| 16 | u16 | `queue_depth` | Stream frames queued when the request arrived (the backlog the answer skipped). 0 with no stream |
| 18 | u16 | reserved | 0 |

All integers little-endian. Golden example (`ping_id` `0x1234`,
`rx_time_us` `0x0011223344556677`, `turnaround_us` 1500, `queue_depth`
`0x0102`):

```
01 00 34 12 77 66 55 44 33 22 11 00 DC 05 00 00 02 01 00 00
```

The answer fits the default ATT MTU of 23 (20 + 3), so FF15 works before the MTU
is raised.

## Rules

- Answer only when subscribed. A request that arrives while FF15 notifications
  are off is ignored (`unsubscribed`). Enable the FF15 CCC first.
- One outstanding echo. A request that arrives while an earlier answer is still
  waiting to be sent is ignored (`busy`). The waiting answer is unchanged. The
  phone should send the next ping only after the previous answer or a timeout
  (1 s is suggested).
- Priority. On each `ATT_EVENT_CAN_SEND_NOW` the pump sends a waiting answer
  before any FF11 fragment, FF12 record or FF14 batch. The answer does not
  enter the frame queue, and FF14 does not hold it for batching. A failed
  `att_server_notify` keeps the answer and retries it on the pump timer
  (`errors` counts the failure). `turnaround_us` is then restamped at the retry.
- What the priority does not bypass. Notifications already handed to
  `att_server_notify` sit in BTstack's and the ESP32 controller's buffers and
  go out first, so does anything the phone's own stack is still delivering. At
  stream load the round trip therefore still includes waiting for those
  in-flight packets (a few notifications, not the queue). How many that is on
  the ESP32 has not been measured.
- Unsubscribing FF15, or a phone connect or disconnect, discards a waiting
  answer (`cancelled`).
- The answer is not counted in `notify` (`notifications`), so the FF14 identity
  (`batches_tx` equals the growth of `notify`) still holds.

## Using it (phone side)

Let `t1` be the phone time just before the write, `t4` the phone arrival time
of the answer, and `t2 = rx_time_us`, `t3 = rx_time_us + turnaround_us`
(ESP32 clock):

- Round trip on the air and in both stacks: `rtt = (t4 - t1) - (t3 - t2)`.
  `t4 - t1` alone is what the app sees end to end.
- Clock offset (optional, an estimate): `offset = ((t2 - t1) + (t3 - t4)) / 2`
  maps ESP32 time to phone time as `phone = esp32 - offset`, with an error of
  up to `rtt / 2` (the write and the notification need not take equally long).
  With it, `phone_arrival - (rx_time_us - offset)` on FF12/FF14 records
  estimates one-way latency to within that error. The ESP32 and phone clocks
  drift, so the offset has to be re-measured. Using it is the app's decision.
- Compare runs with `queue_depth` and the stream that was active. An RTT that
  grows with `queue_depth` would show the priority is not working.

## Counters

`FF15 echo accepted= ... sent= ... pending= ... busy= ... bad= ... unsubscribed= ...
cancelled= ... ` on the periodic line ([COUNTERS.md](COUNTERS.md)). Identity:
`accepted = sent + cancelled + pending`.

## Bench-test items

See [TESTING.md](TESTING.md), "FF15 echo". The round-trip runs that exist are
in [`runs/roundtrip/`](runs/roundtrip/README.md) (user-reported); an item with
no record there has not been run.

# Controller config block, FF13 (APPROVED layout, implemented in firmware, read run on hardware: user-reported)

Status: layout approved by the project owner (Nick, 2026-10-22). Firmware wired
in (WMR_Bridge #17, #21). The wire formats, the block-read state machine and
the FF13 window are a pure-C module (`src/wmr_cfg.c`, `tests/test_cfg.c`,
host-tested). The BTstack side is wired in: `src/wmr_cfgmgr.c` (cache, timers,
sending), report-ID routing in `wmr_controller.c`, the characteristic in
`wmr_transport.c` and `wmr_bridge.gatt`. The UUID and layout below were
approved on 2026-10-22. The block read ran on hardware the same day
(user-reported, see "As implemented" and "Input stream stops after the read"
below); the items under "Still unverified" remain open.

Design basis. The commands below are what Monado does (commit
`f50c796c32c811f5fd371f77fd8a749d2ad7df08`, `wmr_controller_base.c`,
`wmr_controller_protocol.h`). When this design was written they had never
been tried through this bridge; the block read and the enable pair have since
run (user-reported). Every item still marked "unverified" must be checked on
hardware before relying on it.

WMR hands only. The descriptor-available handler sends the block read only to a
`WMR_MODEL_WMR` hand. A PSVR2 hand gets the Feature Report `0x05` wake read
instead and no FF13 command. See [PSVR2.md](PSVR2.md). A Switch hand runs its
own output report `0x01` handshake and publishes the raw 42-byte SPI calibration
blob (24 IMU + 18 stick bytes) through the same FF13 cache
(`wmr_cfgmgr_set_blob`), so the phone reads it with the normal window header;
see [SWITCH.md](SWITCH.md).

Goal: the ESP32 reads each controller's firmware config block once, caches it
per hand, and serves it to the phone byte-for-byte as received. The ESP32 does
not de-obfuscate, parse, or interpret it. The phone de-obfuscates (Monado's
fixed 1024-byte XOR key, applied to the data after the first two bytes), parses
the JSON (`CalibrationInformation.InertialSensors[]`: mix matrix, bias offsets,
pose) and applies it. See [DECODING.md](DECODING.md) for the content.

Why raw and not pre-processed: it keeps the key table (1024 bytes) and a JSON
parser off the ESP32, and it lets the phone check integrity with a CRC over
exactly what the controller sent.

## Reading the block from the controller (ESP32 to controller)

Commands are 64-byte HID reports with report ID `0x06`:

| Byte | Meaning |
| --- | --- |
| 0 | `0x06` (report ID / prefix) |
| 1 | command id |
| 2 | block id |
| 3-6 | `addr`, u32 LE |
| 7-63 | zero |

Block read is command `0x02`. Responses are 78 bytes: `[0]` response report ID
(`0x02` for block reads, `0x06` for the init commands), `[2]` echoed command id,
`[4]` echoed block id, `[5 .. 8]` `blk_remain` u32 LE (bytes of the block not yet
delivered), `[9]` `len` (data bytes in this response, at most 68), `[10 .. 77]`
data.

Reading one block:

1. Send `06 02 <blk> FF FF FF FF`. The response carries the first `len` bytes
   and `blk_remain`. The block size is `blk_remain + len`.
2. While bytes remain, send `06 02 <blk> <remain u32 LE>` where `remain` is the
   number of bytes still to read, and append each response's `len` bytes.
   Monado waits 10 ms between requests and allows 250 ms for each response.

Blocks used by Monado: `0x00` (firmware revision u32 at offset 0x14, config
block size u16 at 0x34), `0x03` (16-byte serial number at offset 0x84), and
`0x02` (the config block: the first 2 bytes are skipped, the rest is
obfuscated JSON).

Monado also sends `06 00` (re-init) and `06 04 C1 02` (quiesce) before
reading, and afterwards enables streaming with `06 03 01 00 02` and
`06 03 02 E1 02`. The bridge already receives input reports with none of these.
Unverified: whether block reads work without the init commands, and whether the
init commands interrupt or stop the input stream. Plan: try the block reads
alone. Only if they fail, send the full Monado sequence including the two enable
commands, and watch the input stream.

Unverified: whether the controller takes these on the HID control or interrupt
channel and as an output or feature report (Monado uses hidapi `hid_write`, an
output report). This must be read from the controller's HID report descriptor,
which the ESP32 already receives. Decoding it is a prerequisite.

The sequence is a non-blocking state machine on BTstack timers (one outstanding
request, 250 ms timeout, a small bounded number of retries, 10 ms spacing),
never blocking the HID callback.

## Separating config responses from input reports

Today the HID report handler (`handle_report` in `wmr_controller.c`) classifies
by report ID, applies the per-model rule below, and passes input reports to
`wmr_transport_submit_report`, which increments the per-hand sequence, counts it
as received, and (when subscribed) copies it into a frame of at most
`WMR_CFG_MAX_FRAME` (80 bytes). A config response is 78 bytes including its
report ID, plus the 1-byte HID transaction header, 79 bytes in all, so without
the rule below it would consume a sequence number and be forwarded as an input
frame.

Rule for the new design, applied in the HID handler before anything is counted
or queued: look at the report ID, which is byte 1 of what BTstack delivers (byte
0 is the `0xA1` DATA/input header).

| Report ID | Handling |
| --- | --- |
| `0x01` | WMR input report: existing path, unchanged (sequence, raw FF11 or decoded FF12) |
| `0x02`, `0x06` | WMR config command response: goes only to the config reader. Not sequence-numbered, not counted in `rx`, not queued, not forwarded on FF11/FF12 |
| `0x31` | PSVR2 input report (PSVR2 hand only): forwarded on FF11 and decoded for FF12/FF14 when the frame is exactly 79 bytes |
| `0x21` | Switch subcommand reply (Switch hand only): consumed by the output report `0x01` handshake, never queued or forwarded |
| `0x30` | Switch input report (Switch hand only): forwarded on FF11 and decoded into three FF12/FF14 records when the frame is exactly 50 bytes |
| anything else | Counted in a separate "other reports" counter, not forwarded |

Classification is by report ID, not by length, so a malformed short response
cannot leak into the sample stream. While a config read is in progress the
controller keeps streaming `0x01` input reports interleaved with the responses.
The reader ignores non-matching IDs exactly as Monado's loop does, and input
handling is unaffected. A PSVR2 hand never gets the FF13 read at all (the
commands are Microsoft-specific), so its `0x02`/`0x06` reports, if any, are
counted as "other". A Switch hand sends its own subcommands and gets no
Microsoft read. `WMR_CFG_MAX_FRAME` is 80 so WMR (46), PSVR2 (79) and Switch
(50) frames all fit. Config responses still never reach that queue. The sequence counter is not
advanced by config traffic, so FF11/FF12 sequence numbers stay contiguous.

## Cache

- RAM cache per hand, filled after the HID descriptor is available. The block
  size is not fixed by anything in Monado (it comes from the controller). Its
  size on these controllers is unknown until measured, so the buffer must be
  sized from the `blk_remain` of the first response, with a hard upper bound
  (proposed 16 KB). A larger block is a FAILED state.
- On a later reconnect, re-read only blocks `0x00` and `0x03` (small) and
  compare the firmware revision and serial against the cache. Re-read block `0x02`
  only if either changed. Persisting the cache in NVS across reboots is an
  optional follow-up.
- The cache survives phone disconnects. It is cleared only if the revision or
  serial differ on reconnect.

## FF13 characteristic (ESP32 to phone)

A GATT attribute value is limited to 512 bytes by the ATT specification (600 on
Android's stack), and the config block is expected to be larger, so a plain
read-only characteristic cannot hold it. FF13 is therefore a read
characteristic with a window cursor, plus a notification when the state changes.
This departs from "read-only" for that reason.

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF13-0000-1000-8000-00805F9B34FB` | read, write (with response), notify |
| CCC (0x2902) | - | read/write |

Write value (5 bytes): `hand` u8, `offset` u32 LE. Sets the read cursor for this
connection (default: hand 0, offset 0). Invalid `hand` fails with ATT error
`0x80`. The blob itself is never written.

Read / notify value: a 16-byte header, then data.

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1` |
| 1 | u8 | `hand` | 0 LEFT, 1 RIGHT (the hand the cursor points at) |
| 2 | u8 | `state` | 0 NOT_READ (controller not connected or not tried), 1 READING, 2 READY, 3 FAILED |
| 3 | u8 | reserved | 0 |
| 4 | u32 | `total_len` | Blob length in bytes. 0 unless READY |
| 8 | u32 | `offset` | Start of this window in the blob |
| 12 | u32 | `crc32` | CRC-32 (IEEE 802.3, reflected, polynomial `0xEDB88320`, init and final XOR `0xFFFFFFFF`) of the whole blob. 0 unless READY |
| 16 | `n` | data | `blob[offset .. offset+n)`, `n = min(total_len - offset, 496)`. Empty unless READY |

A notify carries only the 16-byte header (no data), sent when a hand's `state`
changes, so the app knows when to start reading. The phone reads the blob by
writing `(hand, 0)`, reading (the stack issues read-blob requests for values
longer than the MTU), then writing `(hand, 496)`, `(hand, 992)` and so on until
`total_len` bytes are collected, then checks the CRC-32. If the cursor offset is
beyond `total_len`, the window is empty.

The blob is the controller's block `0x02` including the leading 2 bytes and
still XOR-obfuscated, exactly as read. The phone drops the first two bytes and
XORs the rest with Monado's `wmr_config_key` (1024 bytes; byte `i` of the data
is XORed with `key[i % 1024]`) before parsing the JSON. The key table is
Monado's (`src/xrt/drivers/wmr/wmr_config_key.h`, Copyright 2021 Jan Schmidt,
BSL-1.0). If it is copied into the app, its notice must travel with it.

FF13 is independent of the FF11/FF12 exclusivity and is expected to be used
alongside FF12, with negligible traffic.

## Golden bytes (host-tested)

All integers little-endian. CRC-32 check value: CRC-32 of the ASCII bytes
`123456789` is `0xCBF43926`. Of four zero bytes `0x2144DF1C`.

First block-read request for block `0x02` (64 bytes, rest zero), then a follow-up
for block `0x03` with `addr` `0x01020304`:

```
06 02 02 FF FF FF FF 00 ...            06 02 03 04 03 02 01 00 ...
```

Config response (78 bytes, from the report ID; BTstack delivers it behind the
`A1` header byte): block `0x02`, `blk_remain` `0x1234`, `len` 3, data
`AA BB CC`, rest zero:

```
02 00 02 00 02 34 12 00 00 03 AA BB CC 00 ...
```

FF13 header-only value (notify, or any non-READY read), RIGHT, state `READY`
shown for a 600-byte blob (`total_len` 0x258) at offset 0:
`01 01 02 00 58 02 00 00 00 00 00 00 <crc32>`. The data window follows (496
bytes, then 104 at offset 496). A hand that is not READY reads `total_len` 0,
`crc32` 0 and no data. Cursor write for RIGHT, offset 496: `01 F0 01 00 00`.

## Reader rules fixed by the host tests

- The block size is `blk_remain + len` of the first response. Zero, or more than
  the buffer (hard cap 16384), is FAILED.
- Each later response must satisfy `blk_remain == bytes_expected - bytes_received - len`.
  Any disagreement, or a zero-length response while bytes remain, is FAILED.
- Responses for another block or command are ignored. Three retries after a 250 ms
  timeout, then FAILED. A consumed response resets the retry count.
- Reports are classified by report ID in the HID handler (`0x01` input,
  `0x02`/`0x06` config, else other), never by length.

## As implemented (wiring; read run on hardware, user-reported 2026-10-22)

- The read starts 1 s after a hand's HID descriptor becomes available, once
  per boot per hand. When the cache is READY it is not re-read, and the
  revision/serial comparison from the design above is not implemented (block
  `0x02` only; blocks `0x00` and `0x03` are never read). A FAILED or NOT_READ
  hand is retried on its next connection.
- Commands go out with `hid_host_send_report(cid, 0x06, bytes 1 .. 63)`, i.e. as
  an HID output report on the interrupt channel, without Monado's init commands.
  Whether the controller accepts that is exactly what C1 checks.
- Report routing in the HID handler: `0x01` input (unchanged), `0x02`/`0x06`
  config (only the reader; no sequence number, not in `rx`), anything else
  counted per hand as `other_reports` (printed at disconnect) and not
  forwarded (a change from before, when every report was forwarded).
- FF13 notify (16-byte header, offset 0) is sent only to a phone that enabled
  the CCC, once per state change, behind a waiting FF15 answer and ahead of
  stream data. It does not count in `notifications`. Enabling the CCC sends
  nothing. Read the value to learn the current state.
- The cursor and CCC reset when the phone connects or disconnects. The console
  statistics print `FF13 LEFT/RIGHT state= total= crc32= send_errors=` (state 0
  NOT_READ, 1 READING, 2 READY, 3 FAILED).
- RAM: two 16 KB block buffers.

## Input stream stops after the read (user-reported, 2026-10-22)

After WMR_Bridge #17, on every boot each controller delivered only a few hundred
input reports (LEFT 149-188, RIGHT 190) and then none, with the HID link up
(rssi/lq healthy) and the block READY. The stop lines up with the first config
command (about 1 s after the descriptor, at about 200 reports/s). This is the
unverified item the design named ("whether the init commands interrupt or stop
the input stream"). The cause is not proven. It is either the commands stopping
the controller's streaming, or the new routing dropping reports (neither input
nor config reports are no longer forwarded). The statistics line now shows
`other=` per hand: it stays 0 if routing is not the cause.

Fix (merged as WMR_Bridge #21; user-reported working on hardware, 2026-10-22,
LEFT about 204/s alone): after each read (done or failed) send Monado's two
enable commands, `06 03 01 00 02` then `06 03 02 E1 02` (rest zero, 10 ms
apart). Those bytes come from the design notes, not from a source checked here.
Build with `-DWMR_CFG_FF13_READ=0` (or set it in `wmr_config.h`) to send no
config command at all. Input must then stream as it did before FF13.

The enable pair is also re-sent once whenever a hand whose block is already
READY reconnects (the cached block is not read again), and as a keep-alive
every `WMR_CFG_KEEPALIVE_MS` (default 10000 ms) while the hand stays connected.
Set `WMR_CFG_KEEPALIVE_MS` to 0 to disable the periodic timer.

## Still unverified (needs a controller)

Whether the controller accepts block reads without Monado's init commands.
Control or interrupt channel and output or feature report. That the follow-up
`addr` is "bytes still to read" as written above (taken from this design's
reading of Monado, not checked against Monado's source in this session). Block
`0x02` size on these controllers. That the 78-byte response arrives as one HID
report. Bench items: [TESTING.md](TESTING.md), "FF13 config block".

## Security note

The config block is device-specific data (it may contain identifiers). Since
firmware 0.3.5 FF13 is readable and writable only on an encrypted, bonded link
(`wmr_bridge.gatt`, "Security and pairing" above). Pairing is Just Works, so
the link is not MITM-protected and any central that completes pairing while
the bridge accepts a new bond can read the block. See
[SECURITY.md](SECURITY.md) for the residual risk. (Before 0.3.0 the service
had no encryption and anyone in range could read it. Firmware 0.3.0-0.3.4
declared the encryption flags but did not enforce them; 0.3.5 added the
16-byte key-size enforcement, see CHANGELOG.md.)

## Not decided here

- NVS persistence, and an eviction rule for it.
- Behaviour if a controller never answers (this design: FAILED after the
  bounded retries; a console key could retry).

# Idle sleep and power-off (0x06 keep-alive)

The 0x06 keep-alive pair keeps a connected controller awake and streaming. It
goes out every `WMR_CFG_KEEPALIVE_MS` (default 10000 ms). Without it the
controller sleeps and stops sending reports. The idle policy below lets a
controller sleep and then power off after inactivity.

Two stages. Both measure time since the last active input report.

- After `WMR_CFG_IDLE_SLEEP_MS` (default 300000 ms, 5 min) without activity,
  the bridge stops sending the 0x06 pair. The controller can enter its own
  standby. The Bluetooth link stays up.
- After `WMR_CFG_IDLE_POWEROFF_MS` (default 900000 ms, 15 min) without
  activity, the bridge drops the ACL. The controller powers off.

Set either macro to 0 to disable that stage. `WMR_CFG_IDLE_POWEROFF_MS` must be
0 or no smaller than `WMR_CFG_IDLE_SLEEP_MS`. A report that shows activity
before the power-off threshold wakes the controller. The bridge sends the 0x06
pair again at once.

On the ESP32 firmware, activity means any of these in a decoded report:

- a button is pressed (buttons, thumbstick click, home, menu, squeeze,
  trackpad click or touch, BT pairing).
- trigger value above 0.05;
- thumbstick X or Y above 0.10 in either direction;
- gyro: any axis above 0.08, or the sum of the three axes above 0.15.

A report below all of these thresholds does not count as activity. It does not
reset the idle timer and does not wake a sleeping controller.

After a power-off, inbound reconnects from that hand are declined for
`WMR_CFG_IDLE_RECONNECT_COOLDOWN_MS` (default 60000 ms). This stops a
still-awake controller from reopening the link before its own shutdown timer
runs out. Normal reconnects resume after that time.

On the Pi host bridge the same policy runs in `HidrawManager`. The flags are
`--idle-sleep-timeout` and `--idle-poweroff-timeout` (seconds, default 300 and
900). 0 disables a stage. The power-off timeout must be 0 or at least the
sleep timeout. The bridge pauses the 10 s 0x06 keep-alive after the sleep
timeout and disconnects Bluetooth after the power-off timeout so the
controller powers off. Pass the flags through `WMR_BRIDGE_ARGS` when running
under systemd. On the Pi, activity is relative to a per-hand baseline latched
on connect: a button transition, trigger moved by more than 0.05, thumbstick
moved by more than 0.10 on either axis, or gyro moved by more than 0.08 on any
axis or 0.15 in sum. An active report moves the baseline forward, so an
off-centre resting stick or a static touch bit does not keep the hand awake.
If a controller has no Bluetooth address, the bridge cannot power it off. It
logs a warning and retries on the next tick.

The Pi cooldown is `--idle-reconnect-cooldown` (seconds, default 60, 0
disables it). At the power-off timeout the bridge blocks the controller in
BlueZ, which also drops the ACL, so a page-in from a still-awake controller is
refused, and the hidraw monitor refuses to reopen the hand for the same
window. When the cooldown ends the bridge unblocks the device; a button press
then reconnects normally.

Configuration macros (ESP32):

- `WMR_CFG_KEEPALIVE_MS` (default 10000): keep-alive interval. 0 disables the
  periodic timer.
- `WMR_CFG_IDLE_SLEEP_MS` (default 300000).
- `WMR_CFG_IDLE_POWEROFF_MS` (default 900000).
- `WMR_CFG_IDLE_RECONNECT_COOLDOWN_MS` (default 60000).

Set them in `src/wmr_config.h` or pass `-D` at build time.

# Controller scan and status v2, FF17 (implemented in firmware)

Characteristic `0000FF17-0000-1000-8000-00805F9B34FB`, READ | WRITE | NOTIFY
| DYNAMIC in service `FF10`.

FF17 exposes controller scanning (discovery), manual connect/disconnect, stored
address reset, and bond clearing to companion applications over BLE, mapping
directly onto the ESP32 console actions. It also provides a 20-byte versioned
status (Status v2) that reports connection state, connecting state, HID
descriptor status, address source, controller model, last 3 address bytes,
discovery state and round, and the last executed command and result.

FF17 is independent of the FF11/FF12/FF14 stream exclusion (like FF13 and
FF15). It can be read, written, or subscribed to while any stream mode or no
stream is active.

## Write commands (1-2 bytes)

Writes execute control actions that mirror serial console commands. Writes take
1 or 2 bytes: `opcode` (byte 0) and optional `hand` (byte 1; `0x00` = LEFT,
`0x01` = RIGHT).

| Opcode | Name | Size | Argument | Action / firmware function |
| --- | --- | --- | --- | --- |
| `0x01` | DISCOVER | 1-2 B | hand ignored | `wmr_controller_discover()`: starts bounded inquiry/discovery for unbound hands |
| `0x02` | CONNECT | 2 B | hand (`0x00`=LEFT, `0x01`=RIGHT) | `wmr_controller_connect(hand)`: initiates Classic BT HID connection; if that hand is already open or opening, returns OK without starting a second attempt; if its previous disconnect is still closing, returns BUSY |
| `0x03` | DISCONNECT | 2 B | hand (`0x00`=LEFT, `0x01`=RIGHT) | `wmr_controller_disconnect(hand)`: closes Classic BT HID connection |
| `0x04` | FORGET_ADDRS | 1-2 B | hand ignored | `wmr_controller_forget_addresses()`: clears stored NVS addresses (refused if connected/connecting) |
| `0x05` | FORGET_BOND | 2 B | hand (`0x00`=LEFT, `0x01`=RIGHT) | `wmr_controller_forget_bond(hand)`: clears paired link key (refused if connected/connecting) |

### Result codes and ATT errors

Every write returns an ATT response. On success (`WMR_CTRL_RES_OK` = `0x00`):
ATT status `0x00` (success). On failure / refusal: ATT application error
`0x80 + result_code`.

The result codes are:

| Code | Name | Description |
| --- | --- | --- |
| `0x00` | OK | Action initiated or succeeded |
| `0x01` | BUSY | Controller busy, connection attempt pending, or discovery in progress |
| `0x02` | NO_ADDRESS | Controller address is unknown |
| `0x03` | NOTHING_TO_DO | All required controller addresses are already known |
| `0x04` | NOT_CONNECTED | Controller has no active connection to disconnect |
| `0x05` | NOT_ENABLED | Hand is excluded by this firmware build (`WMR_CFG_BRIDGE_HANDS`) |
| `0x06` | INVALID_PARAM | Unknown opcode, invalid hand index, or incorrect length |

The opcode and result code of the most recent write command are also recorded in
the `last_command` and `last_result` fields of the FF17 status structure and
notified immediately.

## Read and Notify: Status v2 (20 bytes)

Reading FF17 or receiving an FF17 notification returns a 20-byte payload. At 20
bytes, it fits within the default ATT MTU of 23 bytes (20-byte payload +
3-byte ATT header) without requiring MTU exchange.

| Offset | Size | Field | Description |
| --- | --- | --- | --- |
| 0 | u8 | `version` | Format version, `1` |
| 1 | u8 | `left_flags` | Bit 0: connected, Bit 1: connecting, Bit 2: descriptor received, Bits 3 .. 7: reserved (0) |
| 2 | u8 | `left_addr_source` | 0: None, 1: Compile override, 2: Stored in NVS |
| 3 | u8 | `left_model` | 0: WMR, 1: PSVR2, 2: Switch |
| 4 .. 6 | 3 B | `left_addr_tail` | Last 3 bytes of Bluetooth address (device ID: bytes 3, 4, 5). `00 00 00` if no address |
| 7 | u8 | `reserved0` | Reserved, `0` |
| 8 | u8 | `right_flags` | Bit 0: connected, Bit 1: connecting, Bit 2: descriptor received, Bits 3 .. 7: reserved (0) |
| 9 | u8 | `right_addr_source` | 0: None, 1: Compile override, 2: Stored in NVS |
| 10 | u8 | `right_model` | 0: WMR, 1: PSVR2, 2: Switch |
| 11 .. 13 | 3 B | `right_addr_tail` | Last 3 bytes of Bluetooth address (device ID: bytes 3, 4, 5). `00 00 00` if no address |
| 14 | u8 | `reserved1` | Reserved, `0` |
| 15 | u8 | `discovery_state` | 0: Idle, 1: Inquiry, 2: Remote name requests, 3: Connecting discovered |
| 16 | u8 | `discovery_round` | Current discovery round (0 when idle) |
| 17 | u8 | `last_command` | Opcode of the last write command (`0x00` if none) |
| 18 | u8 | `last_result` | Result code of the last write command (`0x00` if none/OK) |
| 19 | u8 | `reserved2` | Reserved, `0` |

### Notifications

When the CCC descriptor is set to `0x0001`, the ESP32 sends a 20-byte
notification whenever:

- A controller's connected, connecting, or descriptor status changes.
- Discovery starts, advances phase/round, or finishes.
- A write command executes (providing immediate asynchronous confirmation of
  opcode and result).

Notifications do not count toward stream transmission counters.

### Radio contention

Bluetooth inquiry (discovery) shares the 2.4 GHz radio and antenna with ongoing
BLE notifications. While discovery is active (`discovery_state != 0`), packet
loss or latency jitter may increase on running controller streams. The client UI
should display a temporary notice (for example "Scanning ... stream may stutter")
while `discovery_state` is non-zero.

# Linux/Pi host bridge differences (implemented in `pi/`)

The Pi host bridge ([PI_BRIDGE.md](PI_BRIDGE.md)) serves the same FF10 service
through BlueZ as a fallback next to its WebSocket. The stream payloads are
byte-identical to the firmware's (`pi/tests/test_protocol.py`,
`pi/tests/test_golden_vectors.py`). The values below differ from the ESP32 and
a client must tell the two bridges apart before it interprets them. This
section describes the code in `pi/wmr_bridge/ble_gatt.py` and
`pi/wmr_bridge/bridge.py` as it is; it has host tests only.

## Firmware-version value: a JSON endpoint document

On the ESP32 this value is a plain version string of at most 31 bytes. On the
Pi it is a UTF-8 JSON object with no whitespace, readable without a bond:

| Key | Type | Meaning |
| --- | --- | --- |
| `type` | string | `"wmr_ble_endpoint"`. A client that sees a value starting with `{` parses JSON and checks this |
| `protocol` | number | `1` |
| `bridge_id` | string | The same identity as `hello.bridge_id` on the WebSocket and the `WMB1` service data in the advertisement |
| `name` | string | Advertised name, default `WMR Bridge` |
| `version` | string | Package version, the string an ESP32 would return bare |
| `hosts` | array of strings | LAN addresses of the TCP listener, preferred uplink first (wired, then Wi-Fi) |
| `port` | number | TCP port, default 8765 |
| `path` | string | WebSocket path, default `/wmr` |
| `authRequired` | boolean | True when the listener requires a token |

It never carries the token. The document is longer than 31 bytes, so a client
must not apply the ESP32 length limit to the whole value, only to `version`.

## Config-block value: session document and calibration windows

The Pi uses the same characteristic for two things, selected by the cursor:

- Before any cursor write on the connection, or after a zero-length write, a
  read returns the session document: a UTF-8 JSON object with `type`
  `"wmr_ble_session"`, `protocol` `1`, `bridge_id`, `version`, `port`, `path`,
  `authRequired`, `pairingOpen` (boolean: the bridge currently accepts a new
  bond) and, only when a token is configured, `token` (the WebSocket session
  token). This is why the value is readable only on an encrypted link. A
  client must never log or display the document.
- After a 5-byte cursor write (`hand` u8, `offset` u32 LE, as on the ESP32) a
  read returns the calibration window with the 16-byte header described in
  "Controller config block". A cursor write of another length, or a hand
  other than 0 or 1, is refused. The Pi does not read the block from the
  controller yet, so a fresh daemon serves header-only `NOT_READ` windows.

An ESP32 never returns JSON here, and its default cursor is `(hand 0,
offset 0)`. A client detects the session document by the first byte `{`; a
calibration window starts with the version byte `0x01`.

## Echo answer: zero fields

The Pi answers a valid 4-byte request at once from the write handler with the
same 20-byte layout, but:

| Field | ESP32 | Pi |
| --- | --- | --- |
| `rx_time_us` | `esp_timer_get_time()` in the ATT write callback | Linux `CLOCK_MONOTONIC` in microseconds when the BlueZ write handler runs in the daemon (user space, after D-Bus delivery) |
| `turnaround_us` | time until the answer is handed to the stack | always 0 |
| `queue_depth` | stream frames queued at arrival | always 0 |

So on a Pi `t3 = t2`, the `rtt` formula of "Using it" degenerates to `t4 - t1`,
and a zero turnaround or depth means "not measured", not "instant" or "empty".
There is no `busy` state. The WebSocket analogue is the JSON `ping`/`pong`
pair with `serverTimeUs`. Golden bytes for both layouts are in
`tests/vectors/wmr_golden_v1.json` (`ff15_echo`).

## `rx_time_us` in records is a user-space read stamp

The Pi stamps a report with `time.monotonic_ns() // 1000` when the daemon
reads it from hidraw (`HidrawManager._on_readable`, `Bridge.ingest`), not when
the Bluetooth controller received it. The reader drains up to 32 reports per
wake-up, so reports that arrived while the event loop was busy get stamps a
few microseconds apart, and the stamp includes kernel and scheduler delay. Its
origin is system boot, unrelated to an ESP32's.

Consequences for a receiver, on both bridges but mandatory on the Pi:

- Take `dt` and all sample timing from `device_ticks`.
- Use `rx_time_us` only for the 100 ms discontinuity check and as a noisy
  anchor for clock alignment (take the minimum delay over a window, never a
  single record).
- Do not compare `rx_time_us` across bridges.

## Batched stream pacing on the Pi's BLE fallback

On the Pi's BLE fallback a full batch is sent at once, and a partial batch is
sent when at least `1 / batch_hz` (default 50 Hz, so 20 ms) has passed since
the previous batch. That is a pacing interval for partial batches, not a cap on
the record rate. On the WebSocket the hold is `--batch-hold-ms` (default 5 ms).

# Proposals (specified, NOT implemented, NOT part of the spec above)

Status of every item in this section: **specified, not implemented.** No
firmware, Pi or app code does any of this at the time of writing. The text is
here so the wire change is reviewed before code is written. Nothing below has
been run on hardware. When an item is implemented, move it into the section it
belongs to and delete it here.

The FF12 version 2 batching sketch that used to be here is the implemented
[FF14](#batched-decoded-stream-ff14-version-2-implemented-in-firmware-run-on-hardware-user-reported)
section above.

## P1. Record flag bits 1-3

Status: specified, not implemented.

The record stays version 1 and 56 bytes. Three more bits of `flags` (offset 2)
get a meaning. Neither the app's parser, the Pi's `unpack_record` nor
`wmr_decoded_unpack` rejects a record for having them set, so no version bump
is needed; a bridge that sets them announces it in `features` (P2).

| Bit | Mask | Name | Set by the bridge when |
| --- | --- | --- | --- |
| 0 | `0x01` | `CALIBRATED` | (existing) sensor values already have calibration applied |
| 1 | `0x02` | `TICKS_SYNTHETIC` | `device_ticks` of this record was not read from the controller's own counter but generated by the bridge from its receive clock (Switch today; any model without a usable tick counter) |
| 2 | `0x04` | `RX_TIME_SYNTHETIC` | `rx_time_us` of this record was not stamped at the receipt of its own report: it was derived (the two older samples of a Switch report), or the bridge knows the stamp is shared with other reports of one burst read |
| 3 | `0x08` | `TICK_RESET` | this is the first record the bridge numbered after the tick epoch of this hand changed: a new Classic connection of the controller, or a restart of the synthetic tick generator |
| 4-7 | | reserved | 0; a receiver ignores them |

Receiver rules:

- `TICKS_SYNTHETIC`: `dt` is still `(ticks - prev) mod 2^32` at 100 ns, and the
  ticks are still monotonic per hand. The receiver must treat the timing as
  bridge-clock quality (it carries the bridge's receive jitter) and must not
  use `ticks` against `rx_time_us` as a clock-rate check, since both come from
  one clock.
- `RX_TIME_SYNTHETIC`: exclude the record from delay, jitter and clock-offset
  estimation. It still takes part in the 100 ms discontinuity rule.
- `TICK_RESET`: start a new epoch for the hand (re-baseline, reset the
  filter's internal state) without waiting for the 100 ms rule. The marked
  record can be lost before delivery, so the 100 ms rule stays in force as the
  fallback and `controller_epoch` (P2) is the authoritative signal.
- A bridge that cannot know (`TICK_RESET` on a bridge that does not see the
  reconnect) leaves the bit clear. Clear never means "no reset happened".

## P2. `boot_id`, `controller_epoch` and `features` in the status

Status: specified, not implemented.

Problem: a receiver cannot tell an ESP32 or daemon restart (sequence numbers
and `rx_time_us` start over) from reordering, and cannot tell a controller
reconnect (ticks may restart) from a long gap, except by heuristics.

Status read, version 2 (24 bytes; the first 16 bytes keep the version 1
layout, so a version 1 parser that accepts a longer value still works; the
app's `BridgeStatus.parse` does):

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | version | `2` |
| 1-15 | | | as version 1 |
| 16 | u32 LE | `boot_id` | Random, non-zero, chosen once per boot (ESP32) or per daemon start (Pi). A change means sequence numbers and `rx_time_us` started over |
| 20 | u8 | `left_controller_epoch` | Starts at 0; incremented (mod 256) each time a new Classic connection of the LEFT controller starts delivering reports |
| 21 | u8 | `right_controller_epoch` | Same for RIGHT |
| 22 | u16 LE | `features` | bit0 `FLAG_BITS` (P1 implemented), bit1 `ECHO_V2` (P3), bit2 `SWITCH_SYNTHETIC_TICKS` (P4), bit3 `SWITCH_BLOB_V1` (P5). Other bits 0 |

The control status (20 bytes) mirrors the two epochs in its reserved bytes so
a subscribed client is notified when one changes: offset 7 `reserved0`
becomes `left_controller_epoch`, offset 14 `reserved1` becomes
`right_controller_epoch`. The version byte stays 1; a client reads the epochs
there only when the status read reported version 2.

WebSocket (Pi): `hello` and `status` gain `"bootId"` (number, same value as
above), `"features"` (array of the names above in lower case) and each entry
of `hands` gains `"controllerEpoch"` (number).

Receiver rules: a changed `boot_id` re-baselines sequence accounting and the
bridge-clock alignment for both hands; a changed `controller_epoch` starts a
new tick epoch for that hand. Both are logged as events by the raw IMU
recorder (wmr-companion `docs/IMU_LOG.md`).

## P3. Echo version 2: transmit time

Status: specified, not implemented.

Version 1 carries `rx_time_us` and a relative `turnaround_us`, and the Pi
fills the second with 0. Version 2 carries an absolute transmit stamp and says
which fields are real.

Request: the same 4 bytes with `version` = `2`. A version 1 bridge ignores it
(`bad`), so a client sends version 2 only when `features` has `ECHO_V2`, and a
client that gets no answer falls back to version 1.

Answer, 28 bytes (needs ATT MTU 31 or more; at a smaller MTU the bridge
answers a version 2 request with a version 1 answer):

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `2` |
| 1 | u8 | `flags` | bit0 `TX_TIME_VALID`, bit1 `QUEUE_DEPTH_VALID`, bit2 `USER_SPACE_CLOCK` (stamps taken in a user-space process, as on the Pi) |
| 2 | u16 | `ping_id` | From the request |
| 4 | u64 | `rx_time_us` | As version 1: bridge clock when the request was received |
| 12 | u64 | `tx_time_us` | Bridge clock just before the answer was handed to the stack (restamped on a retry). Valid only with `TX_TIME_VALID` |
| 20 | u16 | `queue_depth` | As version 1. Valid only with `QUEUE_DEPTH_VALID` |
| 22 | u16 | reserved | 0 |
| 24 | u32 | `boot_id` | As P2, so an offset estimate is never carried across a restart |

Phone side: `t2 = rx_time_us`, `t3 = tx_time_us`; the formulas of "Using it"
are unchanged. The WebSocket `pong` gains `serverRxTimeUs` and keeps
`serverTimeUs` as the transmit stamp.

## P4. Switch timing contract (summary)

Status: specified, not implemented. Secondary priority (after the WMR path).
Full text: [SWITCH.md](SWITCH.md#timing-contract-specified-not-implemented).

- `device_ticks` MUST advance for every record of a hand; the constant 0 the
  decoder sends today is replaced by synthetic ticks generated from the
  frame's receive time, with `TICKS_SYNTHETIC` set.
- Sample interval within a report: `clamp((rx - prev_rx) / 3, 1 ms, 5 ms)`,
  5 ms for the first report of an epoch.
- The two older samples carry `RX_TIME_SYNTHETIC`.

## P5. Switch calibration blob, 44 bytes (summary)

Status: specified, not implemented. Secondary priority. The firmware
publishes 42 bytes today and the app's `SwitchCalibration.parse` accepts only
the 44-byte layout, so the two do not interoperate yet. The 44-byte layout is
the contract; full text in
[SWITCH.md](SWITCH.md#calibration-blob-version-1-44-bytes-specified-not-implemented).
