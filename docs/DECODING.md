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
| 28-31 | u32 LE | device timer, 100 ns per tick, wraps (`wmr_decode_extend_ticks`) |
| 32-43 | | not decoded by Monado either (unknown/device state) |

## Checked against hardware data

`tests/test_decode.c` runs on `samples/controller_frames.txt` (stationary
controller): acceleration magnitude is about 10.0 (1 g), gyro is at noise level
(about 0.016 rad/s) and ticks increase within each session. That confirms the
offsets and scales; it does not validate signs or axes.

## Calibration: status

**Not done.** It needs two things that cannot be produced without the controllers:

1. **The firmware config block.** Monado reads it over HID: output report `0x06`
   (64 bytes: `06 02 <block id> <addr u32 LE>`), after a `06 00` and a `06 04 C1 02`
   command, and collects the 78-byte `0x06` input-report responses (`blk_remain`,
   `len`, up to 68 data bytes each) until the block is complete. Then it XORs the
   data after the first two bytes with a fixed key table (`wmr_config_key.h`) and
   parses the JSON: `CalibrationInformation.InertialSensors[]` (per sensor: mix
   matrix, bias offsets, pose) and `ControllerLeds[]`. Monado then multiplies by the
   mix matrix, adds the bias, and rotates into the OpenXR IMU frame
   (X right, Y up, Z backward).
2. **A rotation capture.** Axis and sign checks need someone to rotate the
   controller about each axis in turn while frames are recorded.

Doing this on the ESP32 also changes the firmware's HID path: the 78-byte `0x06`
responses would arrive through the same report callback that now forwards every
report as a 46-byte raw frame (`WMR_CFG_MAX_FRAME` is 64), so the command channel
must be kept out of the raw stream. That change is firmware work and was not made,
because it cannot be tested without hardware.

### Confirmed (from the stationary samples only)

- Accel scale: magnitude 10.0 for a stationary controller (1 g), consistent with
  `raw / 49000`.
- Gyro scale is at least the right order of magnitude (noise about 0.016 rad/s).
- Ticks increase monotonically within each session.

### Unknown

- Gyro and accel **axis directions and signs** (no rotation capture yet).
- Per-device mix matrix, bias and orientation (config block not read).
- Gyro scale in a real rotation (`1e-5` is Monado's value; unconfirmed here).
- Battery and temperature scales, and bytes 32-43 of the payload.

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
distinguish a real startup transient from the controller being picked up; a capture
of the first few seconds after connect, with the controller held still, would. Not
resolved.

### Other caveats

- Thumbstick, trackpad, trigger and buttons were not exercised by the samples (nothing
  held); only the synthetic tests cover them. Capture frames while moving the stick
  and pressing buttons, labelled by hand.
- No pose/orientation fusion (Monado uses `m_imu_3dof`); fusion is planned on the phone.

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
