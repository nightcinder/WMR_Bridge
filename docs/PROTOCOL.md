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

# Decoded stream, FF12 (PROPOSED, version 1, not implemented in firmware)

**Status: proposal for review.** Nothing in `src/` sends this yet. The reference
pack/unpack is `src/wmr_decoded.{h,c}`, pinned by the "decoded frame (FF12)
golden packet" test in `tests/test_protocol.c`, so the Android app can build and
test against exact bytes. The raw FF11 stream above is **unchanged**.

## Why a second characteristic

FF11 stays the byte-for-byte raw pipe (its golden-packet test pins it). Decoded
samples go on a new characteristic so a receiver can choose raw, decoded or both,
and so neither format constrains the other. Fusion (orientation) runs on the phone;
this stream carries IMU *samples*, not a quaternion.

| Item | UUID | Properties |
| --- | --- | --- |
| Characteristic | `0000FF12-0000-1000-8000-00805F9B34FB` | notify only (in service `FF10`) |
| CCC (0x2902) | - | read/write |

There is no read value. Enabling notifications on FF12 and on FF11 are independent.

## Notification format

One notification = one complete sample = exactly **56 bytes** (no fragmentation).
All integers are **little-endian**; floats are IEEE 754 binary32, little-endian.

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | u8 | `version` | `1`. A receiver must ignore records with an unknown version |
| 1 | u8 | `hand` | 0 = LEFT, 1 = RIGHT |
| 2 | u8 | `flags` | bit0 `CALIBRATED`: sensor values have calibration applied. Other bits 0 |
| 3 | u8 | `buttons` | bit0 thumbstick click, bit1 home, bit2 menu, bit3 squeeze click, bit4 trackpad click, bit5 BT pairing, bit6 trackpad touch, bit7 0 |
| 4 | u32 | `sequence` | The **same per-hand sequence number** as the raw FF11 frame this sample was decoded from (rules under "Sequence numbers" apply, gaps = loss before transmission) |
| 8 | u64 | `rx_time_us` | ESP32 `esp_timer_get_time()` when the HID report was received, microseconds since ESP32 boot |
| 16 | u32 | `device_ticks` | Controller's own timer, **100 ns per tick**, wraps at 2^32 (about 429 s). Use for IMU `dt`; extend to 64 bits on the phone |
| 20 | 3 x f32 | `gyro` x, y, z | **rad/s** |
| 32 | 3 x f32 | `accel` x, y, z | **m/s^2**, uncalibrated scale (a stationary controller read 10.0-10.1 total in the samples) |
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
- Both scale factors are from Monado and verified only to the extent described in
  [DECODING.md](DECODING.md) (stationary magnitude, noise level).

### Axes and calibration

- `CALIBRATED` clear: gyro/accel are the controller's **raw sensor axes**, scaled
  to physical units only. This is all that can be produced today.
- `CALIBRATED` set (future): the per-device mix matrix and bias from the controller's
  firmware config block are applied, and the values are in the **OpenXR IMU frame**
  (X right, Y up, Z backward), as Monado does. Axis directions and signs are
  **unverified** until a rotation capture is done ([DECODING.md](DECODING.md)).
- A receiver must read `flags` and not assume either.

## Delivery rules

- The ESP32 sends one FF12 notification per received HID report while FF12
  notifications are enabled. It is a **separate queue path** from FF11; a full
  queue drops the newest sample and counts it, as for FF11.
- Requires an ATT MTU of at least **59** (56 + 3). Below that nothing is sent on
  FF12. Request a large MTU before enabling FF12 (the same step as for FF11).
- Samples of the two hands may interleave freely. Ordering is per hand by `sequence`.

## Throughput, and questions for review

- About 150-180 reports/s per hand measured on the raw stream, so about 360
  notifications/s for both hands at 56 bytes. Whether the BLE connection interval
  sustains that **alongside FF11** is untested; the expectation is that a phone
  subscribes to FF12 *or* FF11 for normal use, not both.
- Q1: Is `rx_time_us` (ESP32 receive time) the right timestamp, or should the
  controller's `device_ticks` be the primary clock (receive time carries Bluetooth
  jitter; ticks do not)? Both are included. This overlaps issue #1 (latency
  measurement), which is **not** addressed here.
- Q2: Should buttons/stick/trackpad/trigger be sent in every sample (current,
  simplest) or only on change?
- Q3: Calibrated vs uncalibrated: the proposal sends whatever the ESP32 has and sets
  `CALIBRATED`. Alternative: always send raw-scaled values and the config block
  once on a separate read, and calibrate on the phone.
- Q4: `temperature_raw` and `battery` are raw because their scales are unknown.
- GATT database (`wmr_bridge.gatt`) is not changed until this is approved.
