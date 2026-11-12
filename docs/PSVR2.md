# PSVR2 Sense controllers: firmware support

Status: implementation complete in firmware, host-tested and firmware-build-tested.
Never run on a PSVR2 controller. Tracking issue:
[nightcinder/WMR_Bridge#44](https://github.com/nightcinder/WMR_Bridge/issues/44).

PSVR2 Sense controller support is on by default and is compiled into every image.
Both hands search for a WMR name and for a PSVR2 name in the same inquiry. There is
no enable switch. `WMR_CFG_MAX_FRAME` is 80 bytes so the 79-byte `0x31` report fits
beside the 46-byte WMR report.

The bridge started as a WMR-only HID host. This document describes the part of
PSVR2 support that is in the firmware: identifying the controllers, storing their
model, carrying their raw frames, sending the HID handshake that should wake
them, and decoding the `0x31` input report into the FF12/FF14 record. The decode
follows Monado's `pssense` driver and is uncalibrated; it has not run on a PSVR2
controller.

## What is implemented

| Area | Behaviour |
| --- | --- |
| Controller dispatch (`wmr_controller_driver.{h,c}`) | One table entry per model owns the Bluetooth names, the HID report predicate, the FF12/FF14 decoder, the wake feature report and whether the model speaks the FF13 config protocol. Discovery, report classification and decoding look the entry up; adding a family is a table entry, not another model branch. |
| Discovery (`wmr_discovery.{h,c}`) | `PlayStation VR2 Sense Controller (L)` maps to LEFT and `(R)` to RIGHT, case-insensitive, with the same trailing-space/NUL padding rules as the WMR names. `wmr_discovery_model_for_name` returns `WMR_MODEL_PSVR2`. A run records `found_model[hand]`. Both models are searched on every hand by default. |
| Model storage (`wmr_addr_record_*`) | The 16-byte NVS record keeps version 1 and uses the first reserved byte (offset 14) for two model bits per hand: `00` = WMR, `01` = PSVR2. Model bits are written only for a valid hand. Older firmware ignores that byte, so downgrading keeps the addresses and reads both hands as WMR. An unknown value makes the record invalid instead of guessing. |
| Connection state (`wmr_controller.c`) | The model is copied from discovery (or from the name of an incoming connection) into the hand, saved with its address, restored at boot, and shown by `s` as `model=WMR` / `model=PSVR2`. |
| Raw stream (FF11) | A PSVR2 input report (byte 0 `0xA1`, byte 1 `0x31`, up to 79 bytes) is forwarded byte-for-byte with the usual per-hand sequence number. `WMR_CFG_MAX_FRAME` defaults to 80 for every image. |
| Decoded stream (FF12/FF14) | A 79-byte `0x31` frame is decoded into the same 56-byte record as a WMR frame (`wmr_decoded_from_frame` dispatches through the driver table). The offsets and scales are below. Frames that are not exactly 79 bytes with the `A1 31` header are counted in `dec_err`. |
| HID wake (`WMR_CFG_PSVR2_WAKE`) | On `descriptor available`, a PSVR2 hand schedules a read of HID Feature Report `0x05` over the HID control channel after `WMR_CFG_PSVR2_WAKE_DELAY_MS` (250 ms). The delay lets BTstack finish the SET_PROTOCOL(report) it queues on incoming/reconnect links. Outgoing links do not send SET_PROTOCOL. The handshake status and response length are logged. The payload is hex-dumped when `WMR_CFG_REPORT_HEXDUMP=1`. One attempt per connection. |
| FF13 | The Microsoft config-block read is not sent to a PSVR2 hand. It is WMR-specific. The companion app sees FF13 idle for that hand. |
| FF17 status | `left_model` / `right_model` (0 = WMR, 1 = PSVR2) let the phone tell the hands apart before it chooses a stream. |
| Report classification | For a PSVR2 hand only `0x31` frames are forwarded. Anything else is counted in `other_reports`. WMR hands keep the existing `0x01` input / `0x02`+`0x06` config split. |

### FF12/FF14 record from a `0x31` frame

Offsets are into the frame as captured (`frame[0] = 0xA1`, `frame[1] = 0x31`);
the payload layout is Monado's `struct pssense_input_report` plus one byte.
All multi-byte fields are little-endian.

| Frame offset | Size | Field | Mapping |
| --- | --- | --- | --- |
| 3, 4 | 2 x u8 | left/right thumbstick | x = `(raw - 128) / 128`, y = `(128 - raw) / 128`, then Q15 |
| 5 | u8 | trigger | `trigger` (0 .. 255) |
| 10 .. 12 | 3 x u8 | buttons | PS -> home, Share/Options -> menu, L1/R1 -> squeeze, L3/R3 -> thumbstick click; face buttons and capacitive touches have no WMR bit |
| 18 .. 23 | 3 x i16 | gyro x, y, z | raw x 180/1024 deg/s, converted to rad/s (`raw * pi / 1024`) |
| 24 .. 29 | 3 x i16 | accel x, y, z | raw x 9.80665/4096 m/s^2 (+-8 g range) |
| 30 .. 33 | u32 | IMU ticks | x 333 ns, stored in `device_ticks` as 100 ns ticks |
| 34 | u8 | temperature | `temperature_raw` |
| 44 | u8 | battery state | raw byte in `battery` (high nibble charge state, low nibble 0 .. 10 level) |

`trackpad` stays 0 (the controller has none), `flags` stays 0 (uncalibrated),
and the record's `sequence`/`rx_time_us` are the bridge's per-hand values, not
the controller's own packet counter. The `0x31` report carries a CRC-32 over
the report body (Monado seeds it with the `0xA1` header and covers the 74 bytes
before the trailing CRC); the bridge does not check it, matching the raw FF11
path, and relies on the BLE link CRC. A corrupt report that still reaches the
decoder therefore produces a sample rather than a `dec_err`.

Console examples for a recognised PSVR2 hand:

```
Discovery: LEFT is 1C: ... (name in inquiry response, model PSVR2)
LEFT descriptor status=0x00 (model PSVR2)
PSVR2: LEFT wake scheduled in 250 ms
PSVR2: LEFT wake: GET_REPORT feature 0x05 status=0x00
LEFT GET_REPORT response: status=0x00 len=NN
LEFT first report: cid= ... frame_len=79 descriptor=available
```

`LEFT address: ... (discovered, stored, model PSVR2)` is printed at boot and by `s`.

## What is not implemented

- Calibrated decode. The FF12/FF14 samples use Monado's fixed gyro/accel
  scales. The response to Feature Report `0x05` is only logged; its calibration
  block is not parsed, so no per-device bias, scale or axis correction is
  applied. Whether it is one report or several chunks, and how it maps to the
  IMU, is still open (below).
- Face buttons and capacitive touch. The WMR button byte has no bits for
  Square/Cross/Circle/Triangle, the trigger click or finger proximity, so only
  PS, Share/Options, L1/R1 and L3/R3 reach a consumer. Extending that needs a
  record/layout change, not just a decoder change.
- Report CRC. The `0x31` CRC-32 is not checked, so a corrupt frame that reaches
  the decoder produces a sample instead of `dec_err`.
- Output reports. Haptics, adaptive-trigger resistance, and the IR tracking LEDs
  are host-to-controller `0x31` reports with a sequence nibble and a CRC-32.
  Nothing sends them.
- Battery and pairing UI. The raw battery-state byte is carried in the record;
  percentage, charging state and a UI are not. Pairing uses the same
  auto-confirmed SSP as WMR.

## Open questions for the hardware run

1. Does a single `GET_REPORT` Feature `0x05` wake the controller, or does it need
   repeated reads/chunks (some implementations describe multi-part feature
   reads)? The log line above is the first thing to check.
2. Is the input report exactly 79 bytes (`A1 31` + 77 payload), or can it be
   shorter or longer? `WMR_CFG_MAX_FRAME` is 80 and the predicate accepts any
   `0x31` frame up to that, so a mismatch shows up as `frame_len` in the
   first-report line rather than a silent drop.
3. Does the controller send reports before the descriptor-available event? If so
   the delayed wake (250 ms) is still harmless, but reports sent before the
   handshake may be lost. Check the first `LEFT first report` line for a gap.
4. Do both hands work simultaneously on one ESP32, or does the PSVR2 link use
   different sniff/role behaviour from WMR? Link diagnostics (`link:`) apply
   unchanged.

## Reproducing the firmware build and host tests

```bash
# Default firmware: PSVR2 and WMR, MAX_FRAME=80
python3 tools/firmware/build.py --variant both # needs ESP-IDF on PATH

# Host tests (PSVR2 is part of the default build: 243 discovery checks,
# 6129 protocol checks)
gcc -std=c99 -Wall -Wextra -Wconversion -Werror -Isrc \
    tests/test_discovery.c src/wmr_discovery.c src/wmr_controller_driver.c \
    src/wmr_decoded.c src/wmr_decode.c -o /tmp/test_discovery && /tmp/test_discovery
gcc -std=c99 -Wall -Wextra -Wconversion -Werror -Isrc \
    tests/test_protocol.c src/wmr_protocol.c src/wmr_queue.c src/wmr_decoded.c \
    src/wmr_decode.c src/wmr_controller_driver.c -o /tmp/test_protocol && \
    /tmp/test_protocol samples/controller_frames.txt
```

`WMR_CFG_MAX_FRAME` defaults to 80 for every image. 96 x 256 queued frames
overflowed `dram0_0_seg` by 568 bytes in the ESP32 image, while 80 builds with
enough headroom for both controller types. If a PSVR2 report turns out to be
longer than 80, lower `WMR_CFG_QUEUE_CAPACITY` or move the queue to a smaller
element type before raising the frame limit again.

## Build switches

| Switch | Default | Effect |
| --- | --- | --- |
| `WMR_CFG_PSVR2_WAKE` | `1` | `0` keeps PSVR2 recognition and raw/decoded forwarding but sends no wake request, for diagnostics. |
| `WMR_CFG_PSVR2_WAKE_DELAY_MS` | `250` | Delay between descriptor-available and the Feature Report `0x05` read, so SET_PROTOCOL can complete first. |
| `WMR_CFG_MAX_FRAME` | `80` | Must stay `>= 79`; checked at compile time. |
| `WMR_CFG_REPORT_HEXDUMP` | `0` | Also hex-dumps the Feature Report `0x05` response. |

## Sources

- Bluetooth names, USB IDs and the wake/report facts: report and
  [WMR_Bridge#44](https://github.com/nightcinder/WMR_Bridge/issues/44).
- Names were confirmed against Windows/Linux/Apple host reports in that
  investigation. They have not been captured through this firmware.
- Report layout and scales: Monado's PSVR2 Sense driver,
  `src/xrt/drivers/pssense/pssense_driver.c` (`struct pssense_input_report`,
  `PSSENSE_GYRO_SCALE_DEG`, `PSSENSE_ACCEL_SCALE`), and the research
  page, <https://app.notion.com/p/404145eff9f581ecaf73dfdeed2c8301>.
- The decoder offsets follow Monado; that source has not been checked against a
  frame from this controller.
