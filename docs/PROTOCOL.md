# BLE protocol (raw stream, wire version unchanged from the pre-refactor bridge)

This format is **experimental and unversioned**. It is exactly what the
pre-refactor firmware sent; the refactor deliberately did not change it. A
test (`tests/test_protocol.c`, "golden packet") pins the byte layout.

## GATT identifiers

| Item | UUID | Properties |
| --- | --- | --- |
| Device name (GAP 0x2A00) | - | read, value `WMR Bridge` |
| Service | `0000FF10-0000-1000-8000-00805F9B34FB` | primary |
| Characteristic | `0000FF11-0000-1000-8000-00805F9B34FB` | read, notify |
| Client Characteristic Configuration (0x2902) | - | read/write |

Advertising: flags `0x02` (LE general discoverable), complete local name
`WMR Bridge`, incomplete list of 16-bit service UUIDs `0xFF10`.
Interval 100-150 ms (`WMR_CFG_ADV_INTERVAL_MIN/MAX`), all three channels.

The FF10/FF11 UUIDs are experimental and not assigned for production use
(see `wmr_bridge.gatt`).

## Two uses of FF11

- **Notifications** carry the controller stream (below). Enabled by writing
  `0x0001` to the CCC descriptor.
- **A read** of FF11 returns a 16-byte **status** value, not stream data:

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | u8 | Status version, currently 1 |
| 1 | u8 | bit0 LEFT connected, bit1 RIGHT connected |
| 2 | u8 | bit0 LEFT HID descriptor received, bit1 RIGHT |
| 3 | u8 | Reserved, 0 |
| 4 | u32 LE | Seconds since boot (tick counter, **not** a frame sequence) |
| 8 | u32 LE | LEFT reports received on the current connection |
| 12 | u32 LE | RIGHT reports received on the current connection |

## Notification format

All multi-byte integers are **little-endian**. Every notification value is one
fragment: a 12-byte header followed by a chunk of one controller frame.

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `magic` | `0x52` (`'R'`) |
| 1 | u8 | `hand` | 0 = LEFT, 1 = RIGHT |
| 2 | u32 | `sequence` | Per-hand frame number (see rules) |
| 6 | u16 | `total_len` | Length of the whole frame in bytes (observed: 46) |
| 8 | u16 | `offset` | Offset of this chunk within the frame |
| 10 | u16 | `chunk_len` | Bytes of frame data in this notification |
| 12 | `chunk_len` | `data` | `frame[offset .. offset+chunk_len)` |

The frame is the controller's HID input report **exactly as BTstack delivers
it**: byte 0 = `0xA1` (HID DATA/input transaction header), byte 1 = report ID
(observed `0x01`), then 44 payload bytes. 46 bytes in total. It is not decoded.

Example (RIGHT, sequence `0x01020304`, 46-byte frame, notification capacity
44 bytes), first fragment header then second fragment header:

```
52 01 04 03 02 01 2E 00 00 00 20 00   | 32 data bytes   (offset 0,  chunk 32)
52 01 04 03 02 01 2E 00 20 00 0E 00   | 14 data bytes   (offset 32, chunk 14)
```

## Fragmentation

- The notification capacity is `min(ATT_MTU - 3, 244)`: 3 bytes are ATT
  opcode + handle, 244 is `WMR_CFG_MAX_NOTIFICATION`.
- Data per notification = capacity - 12. With the default ATT MTU of 23 the
  capacity is 20 and a 46-byte frame takes 6 notifications
  (8,8,8,8,8,6 bytes). With ATT MTU >= 61 it fits in one notification
  (58 bytes).
- If the negotiated MTU is 15 or less, nothing can be sent (no room for a payload byte).
- Fragments of one frame are sent **consecutively and in order** before any
  fragment of the next frame. The two hands are interleaved at frame
  granularity, never within a frame. A conforming receiver should still keep
  separate reassembly state per hand (the reference reassembler does).

## Receiver rules (reference: `wmr_reassembler_push`)

A fragment is **valid** if `magic == 0x52`, `hand < 2`, `chunk_len ==
notification_length - 12`, `chunk_len > 0`, `0 < total_len <= 64`,
`offset < total_len` and `offset + chunk_len <= total_len`.

For a valid fragment of hand *h*:

1. `offset == 0`: start a new frame. If a frame for *h* was in progress it is
   **abandoned** (counted as incomplete, never emitted).
2. `offset > 0`: accept only if a frame for *h* is in progress and `sequence`
   and `total_len` match it and `offset` equals the bytes received so far.
   Otherwise abandon the frame in progress and ignore the fragment (a late or
   duplicated fragment must not resurrect an abandoned frame).
3. The frame is complete when the received bytes reach `total_len`.

An invalid fragment is counted as an error and also abandons the frame in
progress for its hand (if its `hand` field is valid).

Tests: `tests/test_protocol.c` covers fragmented, incomplete, lost,
duplicated, interleaved and malformed fragments.

## Sequence numbers

- One counter **per hand**, starting at 0 at boot; the first frame is
  numbered **1**. It increments for **every** report received from that
  controller, *whether or not it is delivered* to the phone.
- Consequently the phone sees gaps in `sequence` for frames lost before
  transmission: while no phone was subscribed (`offline`), dropped (`drop`),
  or purged at a queue reset (`purged`). The first sequence number after
  subscribing is therefore generally not 1.
- Sequence numbers **do not reset** when a controller or the phone
  reconnects; they reset only when the ESP32 reboots. A reboot is visible as a
  drop back to small values.
- The counter is `uint32_t` and wraps; use modular arithmetic when comparing.
- A gap proves loss *before* the BLE stack accepted the frame. The absence of
  a gap does **not** yet prove lossless end-to-end delivery: see
  [COUNTERS.md](COUNTERS.md) and [TESTING.md](TESTING.md).

# Decoded stream, FF12 (version 1; implemented in firmware, NOT yet run on hardware)

**Status: approved; firmware implemented on branch `ff12-decoded-stream`; host-tested
and built, but never run on hardware** (checklist in [TESTING.md](TESTING.md)). The
reference pack/unpack is `src/wmr_decoded.{h,c}`, pinned by the
"decoded frame (FF12) golden packet" test in `tests/test_protocol.c`, so the Android
app can build and test against exact bytes. The raw FF11 stream above is
**unchanged**. The 56-byte record from the first proposal is kept as it was; the
decisions below did not require changing it.

## Why a second characteristic

FF11 stays the byte-for-byte raw pipe (its golden-packet test pins it). Decoded
samples go on a new characteristic so a receiver can choose raw or decoded. Fusion
(orientation) and calibration run on the phone; this stream carries IMU *samples*,
not a quaternion. The app normally uses FF12; FF11 is for a debug mode.

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF12-0000-1000-8000-00805F9B34FB` | notify only (in service `FF10`) |
| CCC (0x2902) | - | read/write |

There is no read value.

## Subscription: FF11, FF12 and FF14 are mutually exclusive

Decoded records go only to a phone subscribed to FF12; raw fragments go only to a
phone subscribed to FF11; batches of decoded records ([FF14](#batched-decoded-stream-ff14-version-2-implemented-in-firmware-not-yet-run-on-hardware))
go only to a phone subscribed to FF14. At most one of the three is ever active, which
keeps the BLE budget at one stream's worth.

- **Subscribing to a second stream is refused.** While one of the three CCC descriptors
  is enabled, a write that would enable another fails with ATT application error
  `0x80` ("other stream active"). The first subscription is unaffected. To switch,
  write `0x0000` to the active CCC first, then enable the other. This holds in every
  order (FF11 then FF12, FF11 then FF14, FF12 then FF11, FF12 then FF14, FF14 then
  FF11, FF14 then FF12); it is one rule in `wmr_stream_mode_ccc_write` and is
  host-tested for all six.
- The CCC state is per connection and resets on disconnect, so there is no stale
  state to clean up after a reconnect.
- Why refuse instead of letting one silently win: a client that thinks it is getting
  both streams would otherwise see one go quiet with no error. Refusing makes the
  mistake visible at the moment it is made. (Review item: the alternative is to
  accept both and give FF12 precedence.)
- **Sequence numbers are shared.** Each hand has one counter, incremented for every
  input report received from that controller regardless of subscription state or
  which stream is active. Switching streams therefore looks like a gap (the frames
  received while unsubscribed count as `offline`), not a reset.

## Notification format

One notification = one complete sample = exactly **56 bytes** (no fragmentation).
All integers are **little-endian**; floats are IEEE 754 binary32, little-endian.

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1`. A receiver must ignore records with an unknown version |
| 1 | u8 | `hand` | 0 = LEFT, 1 = RIGHT |
| 2 | u8 | `flags` | bit0 `CALIBRATED`. **Always 0 in version 1** (see below). Other bits 0 |
| 3 | u8 | `buttons` | bit0 thumbstick click, bit1 home, bit2 menu, bit3 squeeze click, bit4 trackpad click, bit5 BT pairing, bit6 trackpad touch, bit7 0 |
| 4 | u32 | `sequence` | The **same per-hand sequence number** as the raw FF11 frame this sample was decoded from. Gaps = loss before transmission |
| 8 | u64 | `rx_time_us` | ESP32 `esp_timer_get_time()` when the HID report was received, microseconds since ESP32 boot. **For latency and jitter diagnostics only; not a sensor clock** |
| 16 | u32 | `device_ticks` | **Primary clock for sensor timing.** Controller's own timer, 100 ns per tick, wraps at 2^32 (see "Timing") |
| 20 | 3 x f32 | `gyro` x, y, z | **rad/s**, uncalibrated, raw sensor axes |
| 32 | 3 x f32 | `accel` x, y, z | **m/s^2**, uncalibrated scale, raw sensor axes |
| 44 | i16 | `temperature_raw` | Raw sensor word, scale not established |
| 46 | 2 x i16 | `stick` x, y | -32767..32767 = -1.0..1.0, 0 = centre |
| 50 | 2 x i16 | `trackpad` x, y | -32767..32767 = -1.0..1.0; both 0 when `trackpad touch` is clear |
| 54 | u8 | `trigger` | 0..255 |
| 55 | u8 | `battery` | Raw controller byte, scale not established |

### Scales

- Gyro: raw signed 24-bit word times `1e-5` rad/s (Monado's factor).
- Accel: raw signed 24-bit word divided by `49000` (Monado's factor). Monado notes
  1 g is about 490000 raw, so this is about m/s^2 with an uncertainty of a few percent
  (the stationary samples read 10.0-10.1, not 9.81). Not a calibrated figure.
- Stick: 12-bit value, `(raw - 0x7FF) / 0x7FF`, clamped to 1.0, then times 32767.
- Trackpad: `(raw - 0x32) / 0x32`, then times 32767; raw `0xFF` (untouched) gives 0.
- These factors are from Monado and verified only to the extent described in
  [DECODING.md](DECODING.md) (stationary magnitude, noise level). A rotation capture
  is planned to check the gyro scale (a full turn should integrate to 2 pi).

### Calibration is done on the phone

FF12 carries **uncalibrated** values: `flags` bit 0 is 0. Each sample is in the
controller's raw sensor axes, only scaled to physical units. The phone applies the
per-device mix matrix, bias and pose from the controller's firmware config block,
which the bridge exposes unmodified on FF13 (below). Bit 0 is kept so a later
version can set it without changing the layout; a receiver must read `flags` and
not assume. Axis directions and signs of the raw axes are **unverified** until the
rotation capture in [DECODING.md](DECODING.md) is done.

## Timing

- **`device_ticks` is the sensor clock.** The app uses the difference between
  successive samples of the same hand as `dt` for its fusion filter.
- **Unit: 100 ns per tick (10 MHz).** Taken from Monado, where
  `WMR_MOTION_CONTROLLER_NS_PER_TICK` is 100 and `wmr_controller_base_handle_imu_sample`
  computes `sample_time_ns = timestamp_ticks * 100`
  (monado commit `f50c796c32c811f5fd371f77fd8a749d2ad7df08`). **Confirmed from
  Monado's code only; not yet measured on hardware.** The capture procedure in
  [DECODING.md](DECODING.md) checks it two ways: elapsed ticks against elapsed
  wall-clock time, and the gyro integral over a known rotation.
- **Wrap.** The counter is an unsigned 32-bit value and wraps at 2^32 ticks, which
  is 429.4967296 s (about 7 min 9.5 s) at 100 ns. Compute
  `dt_ticks = (uint32_t)(ticks - prev_ticks)` (unsigned modulo arithmetic, correct
  across a wrap) and accumulate into a 64-bit total. This is valid while successive
  samples of the same hand are less than 2^32 ticks (429.5 s) apart, which holds
  unless the link is lost for longer than that, and is equivalent to Monado's
  "if the low half went down, add 2^32".
- **Hands have independent clocks.** LEFT and RIGHT `device_ticks` have unrelated
  origins; never subtract one from the other. Use `rx_time_us` for coarse
  cross-hand alignment only.
- **Discontinuities.** It is not established whether a controller restarts its timer
  when it reconnects. A receiver should therefore compare
  `dt_ticks * 100 ns` with `delta(rx_time_us) * 1000 ns` for the same two samples;
  if they differ by more than about 100 ms, treat it as a discontinuity and reset
  the filter for that hand. A sequence gap alone is not a discontinuity: ticks keep
  counting through lost samples, so `dt_ticks` is still the true elapsed time.
- **`rx_time_us`** (ESP32 receive time) includes Bluetooth scheduling jitter and
  is only for measuring latency and jitter, for example against a phone-side
  clock. It answers the one-way part of issue #1; the round trip is the
  [FF15 echo](#echo-ff15-version-1-approved-implemented-in-firmware-not-yet-run-on-hardware),
  whose answer carries a timestamp on the same clock.

## Delivery rules

- While FF12 notifications are enabled, the ESP32 sends one FF12 notification per
  received input report. It uses the **same bounded frame queue** as FF11 (only one of
  the two streams is ever active, so there is no contention); a full queue drops the
  newest sample and counts it in `drop`, as for FF11.
- **Where decoding happens.** The HID callback only copies the report and stamps
  `rx_time_us` (`esp_timer_get_time()`) at arrival. Decoding into the 56-byte record
  happens when the notification pump takes a frame from the queue, and only while FF12
  is the active stream.
- **Minimum MTU: 59** (56 + 3 bytes of ATT opcode and handle). Below that nothing is
  sent on FF12. Each report received while FF12 is active and the MTU is below 59 is
  discarded and counted in the per-hand `mtu_skip` statistic (it is not queued); its
  sequence number is still consumed, so the phone sees the gap once the MTU is raised.
  A frame that was already queued when the MTU turned out to be too small is dropped
  and counted the same way. This is a deliberate difference from FF11, where a tiny MTU
  leaves frames queued until the queue fills and `drop` rises. Request a large MTU
  before enabling FF12 (the same step as for FF11).
- A queued frame that is not a valid 46-byte input report (wrong length, transaction
  header, or report ID) is discarded and counted in the per-hand `dec_err` statistic.
  Config responses are not expected here: they are separated by report ID before this
  point in the FF13 design, which is not implemented.
- Samples of the two hands may interleave freely. Ordering is per hand by `sequence`.

## Bench-test items (not blockers)

- Both hands at about 360 notifications/s (56 bytes each) on one connection: does the
  negotiated connection interval sustain it with no drops and bounded queue depth?
  Measured only for the raw stream so far (about 154-174 reports/s per hand).
- `device_ticks` unit and wrap behaviour on hardware (see Timing).
- Whether `device_ticks` restarts on controller reconnect.
- Refused-CCC behaviour on the target Android versions (the app must handle a failed
  descriptor write).
- GATT: FF12 is `NOTIFY | DYNAMIC` in `wmr_bridge.gatt`, directly after FF11 in
  service `FF10`; FF11 is unchanged.

# Batched decoded stream, FF14 (version 2; implemented in firmware, NOT yet run on hardware)

**Status: firmware implemented on branch `ff14-batching`, host-tested and built, never run
on hardware** (checklist in [TESTING.md](TESTING.md)). Reference code:
`src/wmr_batch.{h,c}`. FF11 and FF12 are **unchanged**; FF14 is an additional
notify-only characteristic that carries the same 56-byte FF12 version 1 records, several
per notification.

## Why

Both hands stream about 160 reports/s each (reported), so FF12 sends about 320
notifications/s. A decimation experiment (`WMR_CFG_FF12_DECIMATE`) was run by the user at
one fixed table position with the controllers face down and still, MTU 517, connection
interval 15 ms (user-reported, single runs, one session; the `last5s` missing-report
share per hand):

| Run | LEFT | RIGHT |
| --- | --- | --- |
| no phone (N=4 build) | 9-11 % | 9-11 % |
| N=1, first run | 22-25 % | 36 % |
| N=1, repeat | 19 % | 28-36 % |
| N=2 | 18-20 % | 14-15 % |
| N=4 | 18-20 % | 12-13 % |

Reading, as given by the director and not independently re-checked here: sending fewer
notifications cost RIGHT up to about 20 points less loss, and the effect repeated;
LEFT rose from about 10 % to about 19 % as soon as the phone streamed and stayed there at
N=1, 2 and 4, so LEFT's extra loss is **not** explained by notification volume, and
**FF14 is not claimed to fix LEFT**. Decimation drops samples; FF14 sends all of them in
fewer notifications. Whether that gives the same relief is exactly what the FF14 bench
runs in TESTING.md are for. Everything above is one position and one session.

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF14-0000-1000-8000-00805F9B34FB` | notify only (in service `FF10`) |
| CCC (0x2902) | - | read/write |

There is no read value. GATT: `NOTIFY | DYNAMIC` in `wmr_bridge.gatt`, directly after FF12.

## Notification format

One notification value = a 2-byte header followed by `count` complete FF12 version 1
records, **unchanged** (each keeps its own `version` = 1, `hand`, `sequence`,
`rx_time_us`, `device_ticks`, ...):

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `2` |
| 1 | u8 | `count` | Records that follow, 1 to `(MTU - 3 - 2) / 56`, at most 9 |
| 2 | 56 x `count` | records | FF12 version 1 records, oldest first. Records of both hands may share a batch (each record carries its own `hand`); order is by arrival, and per hand by `sequence` |

Total size = `2 + 56 x count`, never more than `MTU - 3`. The most records per
notification: 4 at MTU 247, 5 at MTU 285, 9 at MTU 517 (the largest ATT MTU). Pinned by the
golden-bytes tests for a batch of 1, 2 and the maximum in `tests/test_batch.c`.

A receiver reads `version`, rejects anything other than 2, takes `count`, and requires the
value length to be exactly `2 + 56 x count` (`wmr_batch_parse` is the reference). Records
are then parsed as in the FF12 section.

## Minimum MTU: 61

`3 + 2 + 56` = 61 (one record). Below 61 **nothing is sent** on FF14. As for FF12, each
report received while FF14 is active and the MTU is below 61 is discarded and counted in
the per-hand `mtu_skip` (not queued, sequence number still consumed). Note that FF12 needs
only 59, so an MTU of 59 or 60 works for FF12 and not for FF14. Request a large MTU before
enabling FF14.

## When a batch is sent

Reports go into the same bounded frame queue as FF11 and FF12 (a full queue drops the newest
and counts it in `drop`, exactly as for FF12; batching drops nothing itself). A batch is
sent from the queue when the ATT stack can send (`ATT_EVENT_CAN_SEND_NOW`), and only when
one of these holds:

- **a full notification is queued**: `(MTU - 3 - 2) / 56` records (9 at MTU 517) or more,
  counted in `batch_full_flushes`; or
- **the oldest queued record has waited one connection interval** (the interval the phone
  granted; 15 ms until it is known, `WMR_CFG_BATCH_HOLD_DEFAULT_US`): the queue is then
  flushed as a partial batch.

So a record is held for batching no longer than one connection interval, checked by a
timer that is armed for exactly the remaining time (plus up to one report period, about 5 ms,
if a check is delayed). The decision is the pure function `wmr_batch_flush_reason`, and its
bound is simulated in `tests/test_batch.c`. This bound is on the batching **hold**: if the BLE
stack cannot send (the previous notification has not been accepted yet, or the link is
busy) records wait in the queue longer, exactly as FF12 records do. Decimation is not
applied on FF14 (a diagnostic setting for FF12 only). Whether the ESP32 BLE stack really
sends one batch per connection event is not guaranteed and has not been measured.

On a send error the batch stays queued and is sent again (a send error never discards
records). A queued frame that is not a valid 46-byte input report is removed and counted in
`dec_err`, and takes no slot in the batch.

## Counters and the accounting identity

Per-hand counters are those of FF12. On FF14, `tx` counts **records** (each valid record in
an accepted batch counts once for its hand), not notifications. The global counters:

| Counter | Meaning |
| --- | --- |
| `batches_tx` | Batches accepted by the BLE stack (FF14 only) |
| `records_in_batches` | Records inside those batches |
| mean batch size | `records_in_batches / batches_tx` |
| `batch_max` | Most records in one batch so far |
| `batch_full_flushes` | Batches sent because a full one was queued; the rest were sent on the hold time |
| `notify` / `notifications` | BLE notifications accepted; under FF14 this is the number of batches |

Identity (see [COUNTERS.md](COUNTERS.md)): `rx = tx + drop + offline + purged + mtu_skip
+ dec_err + decimated + queued` holds per hand as for FF12, with `tx` in records.
Under FF14 `batches_tx` equals the growth of `notify`, and `records_in_batches` equals the
growth of `tx` summed over both hands (both counted since boot, so they match exactly only
if no other stream ran in that boot).

## Loss pattern and latency

One lost notification now loses up to `count` records (9 at MTU 517) instead of one; they are
still detectable by per-hand `sequence` gaps. A record can wait up to one connection
interval for its batch; a v1 record also waits for its connection event. `device_ticks`
stays the sensor clock and `rx_time_us` is per record, so fusion timing does not depend on
the batch. Latency is not measured.

## An FF12 or FF11 receiver

An app that never subscribes to FF14 never sees a version 2 value. A version 1 parser that
were pointed at FF14 would ignore it silently (`version` is byte 0), which is the reason FF14
is a separate characteristic rather than a mode of FF12.

## Bench-test items

See [TESTING.md](TESTING.md): FF14 on a phone at MTU 517 and 247, the three ordered
exclusion cases, MTU below 61, and the A/B/A comparison of FF12 and FF14 at one fixed
position. None has been run.

# Echo, FF15 (version 1; approved, implemented in firmware, NOT yet run on hardware)

**Status: layout approved by the project owner (2026-10-22, issue #1); firmware implemented and
host-tested, never run on hardware** (checklist in [TESTING.md](TESTING.md)). Reference code:
`src/wmr_echo.{h,c}`, pinned
by the golden-bytes tests in `tests/test_echo.c`. FF11, FF12 and FF14 are **unchanged**, and no
existing handle moves (FF15 is appended after FF14 in `wmr_bridge.gatt`).

## Why

The streams carry no phone-side reference, so the phone can measure inter-arrival jitter but not
latency. FF15 gives a BLE round trip that can be measured **while a stream is running**: the phone
writes a small ping, and the ESP32 answers on the next send opportunity, **ahead of every frame in
the stream queue**. The answer also carries the ESP32 receive time on the same clock as FF12's
`rx_time_us`, so the phone can relate the two clocks (see "Using it").

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF15-0000-1000-8000-00805F9B34FB` | write without response, notify (in service `FF10`) |
| CCC (0x2902) | - | read/write |

`FF13` is left for the config block design below; `FF15` is the next free number. There is no
read value. A write **with** response is refused by the stack (the characteristic does not
declare it).

## Not part of the stream exclusion

FF15 is independent of the FF11/FF12/FF14 rule: its CCC can be enabled with or without any of the
three, and enabling it never returns `0x80`. Like the others, its CCC state is per connection and
is cleared when the phone connects or disconnects.

## Request (phone to ESP32): 4 bytes, write without response

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1` |
| 1 | u8 | reserved | Send 0; ignored by the ESP32 |
| 2 | u16 | `ping_id` | Any value chosen by the phone; copied into the answer |

A request of any other length or version is ignored and counted (`bad`). Because it is a write
without response, nothing tells the phone that a request was ignored except the missing answer.

## Answer (ESP32 to phone): 20 bytes, notification

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1`. A receiver must ignore an answer with an unknown version |
| 1 | u8 | reserved | 0 |
| 2 | u16 | `ping_id` | From the request |
| 4 | u64 | `rx_time_us` | ESP32 `esp_timer_get_time()` when the request reached the ATT write callback, microseconds since ESP32 boot. **The same clock as FF12 `rx_time_us`** |
| 12 | u32 | `turnaround_us` | Time from `rx_time_us` to just before the answer was handed to `att_server_notify`; saturates at `0xFFFFFFFF` |
| 16 | u16 | `queue_depth` | Stream frames queued when the request arrived (the backlog the answer skipped); 0 with no stream |
| 18 | u16 | reserved | 0 |

All integers little-endian. Golden example (`ping_id` `0x1234`, `rx_time_us`
`0x0011223344556677`, `turnaround_us` 1500, `queue_depth` `0x0102`):

```
01 00 34 12 77 66 55 44 33 22 11 00 DC 05 00 00 02 01 00 00
```

The answer fits the default ATT MTU of 23 (20 + 3), so FF15 works before the MTU is raised.

## Rules

- **Answer only when subscribed.** A request that arrives while FF15 notifications are off is
  ignored (`unsubscribed`). Enable the FF15 CCC first.
- **One outstanding echo.** A request that arrives while an earlier answer is still waiting to be
  sent is ignored (`busy`); the waiting answer is unchanged. The phone should send the next ping
  only after the previous answer or a timeout (1 s is suggested).
- **Priority.** On each `ATT_EVENT_CAN_SEND_NOW` the pump sends a waiting answer **before** any
  FF11 fragment, FF12 record or FF14 batch; the answer does not enter the frame queue, and FF14 does
  not hold it for batching. A failed `att_server_notify` keeps the answer and retries it on the pump
  timer (`errors` counts the failure); `turnaround_us` is then restamped at the retry.
- **What the priority does not bypass.** Notifications already handed to `att_server_notify` sit in
  BTstack's and the ESP32 controller's buffers and go out first; so does anything the phone's own
  stack is still delivering. At stream load the round trip therefore still includes waiting for
  those in-flight packets (a few notifications, not the queue). How many that is on the ESP32 has
  not been measured.
- Unsubscribing FF15, or a phone connect or disconnect, discards a waiting answer (`cancelled`).
- The answer is **not** counted in `notify` (`notifications`), so the FF14 identity
  (`batches_tx` equals the growth of `notify`) still holds.

## Using it (phone side)

Let `t1` be the phone time just before the write, `t4` the phone arrival time of the answer, and
`t2 = rx_time_us`, `t3 = rx_time_us + turnaround_us` (ESP32 clock):

- **Round trip on the air and in both stacks:** `rtt = (t4 - t1) - (t3 - t2)`. `t4 - t1` alone is
  what the app sees end to end.
- **Clock offset (optional, an estimate):** `offset = ((t2 - t1) + (t3 - t4)) / 2` maps ESP32 time
  to phone time as `phone = esp32 - offset`, with an error of up to `rtt / 2` (the write and the
  notification need not take equally long). With it, `phone_arrival - (rx_time_us - offset)` on
  FF12/FF14 records estimates one-way latency to within that error. The ESP32 and phone clocks
  drift, so the offset has to be re-measured; using it is the app's decision.
- Compare runs with `queue_depth` and the stream that was active: an RTT that grows with
  `queue_depth` would show the priority is not working.

## Counters

`FF15 echo accepted=... sent=... pending=... busy=... bad=... unsubscribed=... cancelled=...` on the
periodic line ([COUNTERS.md](COUNTERS.md)). Identity: `accepted = sent + cancelled + pending`.

## Bench-test items

See [TESTING.md](TESTING.md), "FF15 echo". None has been run.

# Controller config block, FF13 (APPROVED layout, host-tested formats only, not wired, not yet run)

**Status: layout approved by the project owner (Nick, 2026-10-22); firmware wiring still to do.** The wire formats, the block-read state
machine and the FF13 window are implemented as a pure-C module (`src/wmr_cfg.c`,
`tests/test_cfg.c`) and host-tested. It is **not** wired into the HID handler or the GATT
database, `FF13` is **not** in `wmr_bridge.gatt`, and nothing has run on an ESP32, a
controller or a phone. The UUID and layout below were approved on 2026-10-22; they are still
unverified on hardware.

**Design basis.** The commands below are what Monado does
(commit `f50c796c32c811f5fd371f77fd8a749d2ad7df08`, `wmr_controller_base.c`,
`wmr_controller_protocol.h`). They have **never been tried through this bridge**;
every item marked "unverified" must be checked on hardware before relying on it.

Goal: the ESP32 reads each controller's firmware config block once, caches it per
hand, and serves it to the phone **byte-for-byte as received**. The ESP32 does not
de-obfuscate, parse, or interpret it. The phone de-obfuscates (Monado's fixed
1024-byte XOR key, applied to the data after the first two bytes), parses the JSON
(`CalibrationInformation.InertialSensors[]`: mix matrix, bias offsets, pose) and
applies it. See [DECODING.md](DECODING.md) for the content.

Why raw and not pre-processed: it keeps the key table (1024 bytes) and a JSON parser
off the ESP32, and it lets the phone check integrity with a CRC over exactly what the
controller sent.

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
`[4]` echoed block id, `[5..8]` `blk_remain` u32 LE (bytes of the block not yet
delivered), `[9]` `len` (data bytes in this response, at most 68), `[10..77]` data.

Reading one block:

1. Send `06 02 <blk> FF FF FF FF`. The response carries the first `len` bytes and
   `blk_remain`; the block size is `blk_remain + len`.
2. While bytes remain, send `06 02 <blk> <remain u32 LE>` where `remain` is the
   number of bytes still to read, and append each response's `len` bytes.
   Monado waits 10 ms between requests and allows 250 ms for each response.

Blocks used by Monado: `0x00` (firmware revision u32 at offset 0x14, config block
size u16 at 0x34), `0x03` (16-byte serial number at offset 0x84), and `0x02` (the
config block: the first 2 bytes are skipped, the rest is obfuscated JSON).

Monado also sends `06 00` (re-init) and `06 04 C1 02` (quiesce) before reading, and
afterwards enables streaming with `06 03 01 00 02` and `06 03 02 E1 02`. The bridge
already receives input reports with none of these. **Unverified:** whether block
reads work without the init commands, and whether the init commands interrupt or
stop the input stream. Plan: try the block reads alone; only if they fail, send the
full Monado sequence including the two enable commands, and watch the input stream.

**Unverified:** whether the controller takes these on the HID control or interrupt
channel and as an output or feature report (Monado uses hidapi `hid_write`, an
output report). This must be read from the controller's HID report descriptor,
which the ESP32 already receives; decoding it is a prerequisite.

The sequence is a non-blocking state machine on BTstack timers (one outstanding
request, 250 ms timeout, a small bounded number of retries, 10 ms spacing), never
blocking the HID callback.

## Separating config responses from input reports

Today the HID report handler (`handle_report` in `wmr_controller.c`) passes **every**
report to `wmr_transport_submit_report`, which increments the per-hand sequence,
counts it as received, and (when subscribed) copies it into a frame of at most 64
bytes. A config response is 78 bytes including its report ID, plus the 1-byte HID
transaction header, 79 bytes in all, so today it would consume a sequence number and
be dropped as oversize.

Rule for the new design, applied **in the HID handler before anything is counted or
queued**: look at the report ID, which is byte 1 of what BTstack delivers (byte 0 is
the `0xA1` DATA/input header).

| Report ID | Handling |
| --- | --- |
| `0x01` | Input report: existing path, unchanged (sequence, raw FF11 or decoded FF12) |
| `0x02`, `0x06` | Config command response: goes only to the config reader; **not** sequence-numbered, not counted in `rx`, not queued, not forwarded on FF11/FF12 |
| anything else | Counted in a separate "other reports" counter, not forwarded |

Classification is by report ID, not by length, so a malformed short response cannot
leak into the sample stream. While a config read is in progress the controller keeps
streaming `0x01` input reports interleaved with the responses; the reader ignores
non-matching IDs exactly as Monado's loop does, and input handling is unaffected.
`WMR_CFG_MAX_FRAME` (64) stays as it is, since config responses never reach that
queue. The sequence counter is not advanced by config traffic, so FF11/FF12 sequence
numbers stay contiguous.

## Cache

- RAM cache per hand, filled after the HID descriptor is available. The block size is
  not fixed by anything in Monado (it comes from the controller); its size on these
  controllers is **unknown** until measured, so the buffer must be sized from the
  `blk_remain` of the first response, with a hard upper bound (proposed 16 KB), and
  a larger block is a FAILED state.
- On a later reconnect, re-read only blocks `0x00` and `0x03` (small) and compare the
  firmware revision and serial against the cache; re-read block `0x02` only if either
  changed. Persisting the cache in NVS across reboots is an optional follow-up.
- The cache survives phone disconnects. It is cleared only if the revision or serial
  differ on reconnect.

## FF13 characteristic (ESP32 to phone)

A GATT attribute value is limited to 512 bytes by the ATT specification (600 on
Android's stack), and the config block is expected to be larger, so a plain read-only
characteristic cannot hold it. FF13 is therefore a read characteristic with a
**window cursor**, plus a notification when the state changes. This departs from
"read-only" for that reason.

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF13-0000-1000-8000-00805F9B34FB` | read, write (with response), notify |
| CCC (0x2902) | - | read/write |

Write value (5 bytes): `hand` u8, `offset` u32 LE. Sets the read cursor for this
connection (default: hand 0, offset 0). Invalid `hand` fails with ATT error `0x80`.
The blob itself is never written.

Read / notify value: a 16-byte header, then data.

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1` |
| 1 | u8 | `hand` | 0 LEFT, 1 RIGHT (the hand the cursor points at) |
| 2 | u8 | `state` | 0 NOT_READ (controller not connected or not tried), 1 READING, 2 READY, 3 FAILED |
| 3 | u8 | reserved | 0 |
| 4 | u32 | `total_len` | Blob length in bytes; 0 unless READY |
| 8 | u32 | `offset` | Start of this window in the blob |
| 12 | u32 | `crc32` | CRC-32 (IEEE 802.3, reflected, polynomial `0xEDB88320`, init and final XOR `0xFFFFFFFF`) of the **whole** blob; 0 unless READY |
| 16 | `n` | data | `blob[offset .. offset+n)`, `n = min(total_len - offset, 496)`; empty unless READY |

A **notify** carries only the 16-byte header (no data), sent when a hand's `state`
changes, so the app knows when to start reading. The phone reads the blob by writing
`(hand, 0)`, reading (the stack issues read-blob requests for values longer than the
MTU), then writing `(hand, 496)`, `(hand, 992)` and so on until `total_len` bytes are
collected, then checks the CRC-32. If the cursor offset is beyond `total_len`, the
window is empty.

The blob is the controller's block `0x02` **including the leading 2 bytes and still
XOR-obfuscated**, exactly as read. The phone drops the first two bytes and XORs the
rest with Monado's `wmr_config_key` (1024 bytes; byte `i` of the data is XORed with
`key[i % 1024]`) before parsing the JSON. The key table is Monado's
(`src/xrt/drivers/wmr/wmr_config_key.h`, Copyright 2021 Jan Schmidt, BSL-1.0); if it is
copied into the app, its notice must travel with it.

FF13 is independent of the FF11/FF12 exclusivity and is expected to be used alongside
FF12, with negligible traffic.

## Golden bytes (host-tested; not yet run on hardware)

All integers little-endian. CRC-32 check value: CRC-32 of the ASCII bytes `123456789` is
`0xCBF43926`; of four zero bytes `0x2144DF1C`.

First block-read request for block `0x02` (64 bytes, rest zero), then a follow-up for block
`0x03` with `addr` `0x01020304`:

```
06 02 02 FF FF FF FF 00 ...            06 02 03 04 03 02 01 00 ...
```

Config response (78 bytes, from the report ID; BTstack delivers it behind the `A1` header
byte): block `0x02`, `blk_remain` `0x1234`, `len` 3, data `AA BB CC`, rest zero:

```
02 00 02 00 02 34 12 00 00 03 AA BB CC 00 ...
```

FF13 header-only value (notify, or any non-READY read), RIGHT, state `READY` shown for a
600-byte blob (`total_len` 0x258) at offset 0: `01 01 02 00 58 02 00 00 00 00 00 00 <crc32>`;
the data window follows (496 bytes, then 104 at offset 496). A hand that is not READY reads
`total_len` 0, `crc32` 0 and no data. Cursor write for RIGHT, offset 496: `01 F0 01 00 00`.

## Reader rules fixed by the host tests

- The block size is `blk_remain + len` of the first response. Zero, or more than the buffer
  (hard cap 16384), is FAILED.
- Each later response must satisfy `blk_remain == bytes_expected - bytes_received - len`;
  any disagreement, or a zero-length response while bytes remain, is FAILED.
- Responses for another block or command are ignored. Three retries after a 250 ms timeout,
  then FAILED. A consumed response resets the retry count.
- Reports are classified by report ID in the HID handler (`0x01` input, `0x02`/`0x06` config,
  else other), never by length.

## Still unverified (needs a controller)

Whether the controller accepts block reads without Monado's init commands; control or
interrupt channel and output or feature report; that the follow-up `addr` is "bytes still to
read" as written above (taken from this design's reading of Monado, not checked against
Monado's source in this session); block `0x02` size on these controllers; that the 78-byte
response arrives as one HID report. Bench items: [TESTING.md](TESTING.md), "FF13 config
block".

## Security note

The config block is device-specific data (it may contain identifiers). Like the rest
of the BLE service, FF13 has **no authentication or encryption** (see
[SECURITY.md](SECURITY.md)): anyone in range can read it.

## Not decided here

- NVS persistence, and an eviction rule for it.
- Behaviour if a controller never answers (this design: FAILED after the bounded
  retries; a console key could retry).

# Proposals (NOT decided, NOT implemented, NOT part of the spec above)

None at present. The FF12 version 2 batching sketch that was here is now the implemented
[FF14](#batched-decoded-stream-ff14-version-2-implemented-in-firmware-not-yet-run-on-hardware)
section above. FF13 (controller config block) is a proposal: formats host-tested, firmware not wired, not yet run.
