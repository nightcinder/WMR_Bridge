# PSVR2 Sense controllers: firmware support

**Status: implementation complete in firmware, host-tested and firmware-build-tested;
never run on a PSVR2 controller.** Tracking issue:
[nightcinder/WMR_Bridge#44](https://github.com/nightcinder/WMR_Bridge/issues/44).

PSVR2 Sense controller support is **on by default** and is compiled into every image.
Both hands search for a WMR name and for a PSVR2 name in the same inquiry; there is no
enable switch. `WMR_CFG_MAX_FRAME` is 80 bytes so the 79-byte `0x31` report fits beside
the 46-byte WMR report.

The bridge started as a WMR-only HID host. This document describes the part of PSVR2
support that is in the firmware: identifying the controllers, storing their model,
carrying their raw frames, and sending the HID handshake that should wake them. Decoding
the PSVR2 report is deliberately left to a later change (and to the phone first).

## What is implemented

| Area | Behaviour |
| --- | --- |
| Discovery (`wmr_discovery.{h,c}`) | `PlayStation VR2 Sense Controller (L)` maps to LEFT and `(R)` to RIGHT, case-insensitive, with the same trailing-space/NUL padding rules as the WMR names. `wmr_discovery_model_for_name` returns `WMR_MODEL_PSVR2`; a run records `found_model[hand]`. Both models are searched on every hand by default. |
| Model storage (`wmr_addr_record_*`) | The 16-byte NVS record keeps version 1 and uses the first reserved byte (offset 14) for two model bits per hand: `00` = WMR, `01` = PSVR2. Model bits are written only for a valid hand. Older firmware ignores that byte, so downgrading keeps the addresses and reads both hands as WMR. An unknown value makes the record invalid instead of guessing. |
| Connection state (`wmr_controller.c`) | The model is copied from discovery (or from the name of an incoming connection) into the hand, saved with its address, restored at boot, and shown by `s` as `model=WMR` / `model=PSVR2`. |
| Raw stream (FF11) | A PSVR2 input report (byte 0 `0xA1`, byte 1 `0x31`, up to 79 bytes) is forwarded byte-for-byte with the usual per-hand sequence number. `WMR_CFG_MAX_FRAME` defaults to 80 for every image. |
| HID wake (`WMR_CFG_PSVR2_WAKE`) | On `descriptor available`, a PSVR2 hand schedules a read of HID Feature Report `0x05` over the HID control channel after `WMR_CFG_PSVR2_WAKE_DELAY_MS` (250 ms). The delay lets BTstack finish the SET_PROTOCOL(report) it queues on incoming/reconnect links; outgoing links do not send SET_PROTOCOL. The handshake status and response length are logged; the payload is hex-dumped when `WMR_CFG_REPORT_HEXDUMP=1`. One attempt per connection. |
| FF13 | The Microsoft config-block read is **not** sent to a PSVR2 hand: it is WMR-specific. The companion app sees FF13 idle for that hand. |
| FF17 status | `left_model` / `right_model` (0 = WMR, 1 = PSVR2) let the phone tell the hands apart before it chooses a stream. |
| Report classification | For a PSVR2 hand only `0x31` frames are forwarded; anything else is counted in `other_reports`. WMR hands keep the existing `0x01` input / `0x02`+`0x06` config split. |

Console examples for a recognised PSVR2 hand:

```
Discovery: LEFT is 1C:... (name in inquiry response, model PSVR2)
LEFT descriptor status=0x00 (model PSVR2)
PSVR2: LEFT wake scheduled in 250 ms
PSVR2: LEFT wake: GET_REPORT feature 0x05 status=0x00
LEFT GET_REPORT response: status=0x00 len=NN
LEFT first report: cid=... frame_len=79 descriptor=available
```

`LEFT address: ... (discovered, stored, model PSVR2)` is printed at boot and by `s`.

## What is not implemented

- **Decoded stream (FF12/FF14).** There is no PSVR2 parser. Those frames are rejected by
  `wmr_decoded_from_frame` and counted in the per-hand `dec_err` counter; the raw stream
  (FF11) is the only way to get PSVR2 data to a phone today. The public report layout is
  documented in the ONY-233 research page; `wmr-companion` needs its own `0x31` parser
  before FF12/FF14 can carry PSVR2 samples.
- **Output reports.** Haptics, adaptive-trigger resistance, and the IR tracking LEDs are
  host-to-controller `0x31` reports with a sequence nibble and a CRC-32. Nothing sends
  them.
- **Calibration.** The response to Feature Report `0x05` is only logged. Whether it is
  one report or several chunks, and how it maps to the IMU, is still open (below).
- **Battery and pairing UI.** Battery is not decoded; pairing uses the same auto-confirmed
  SSP as WMR.

## Open questions for the hardware run

1. Does a single `GET_REPORT` Feature `0x05` wake the controller, or does it need repeated
   reads/chunks (some implementations describe multi-part feature reads)? The log line
   above is the first thing to check.
2. Is the input report exactly 79 bytes (`A1 31` + 77 payload), or can it be shorter or
   longer? `WMR_CFG_MAX_FRAME` is 80 and the predicate accepts any `0x31` frame up to that,
   so a mismatch shows up as `frame_len` in the first-report line rather than a silent drop.
3. Does the controller send reports before the descriptor-available event? If so the
   delayed wake (250 ms) is still harmless, but reports sent before the handshake may be
   lost. Check the first `LEFT first report` line for a gap.
4. Do both hands work simultaneously on one ESP32, or does the PSVR2 link use different
   sniff/role behaviour from WMR? Link diagnostics (`link:`) apply unchanged.

## Reproducing the firmware build and host tests

```bash
# Default firmware: PSVR2 and WMR, MAX_FRAME=80
python3 tools/firmware/build.py --variant both        # needs ESP-IDF on PATH

# Host tests (PSVR2 is part of the default build: 198 discovery checks,
# 6127 protocol checks)
gcc -std=c99 -Wall -Wextra -Wconversion -Werror -Isrc \
    tests/test_discovery.c src/wmr_discovery.c -o /tmp/test_discovery && /tmp/test_discovery
gcc -std=c99 -Wall -Wextra -Wconversion -Werror -Isrc \
    tests/test_protocol.c src/wmr_protocol.c src/wmr_queue.c src/wmr_decoded.c \
    src/wmr_decode.c -o /tmp/test_protocol && /tmp/test_protocol samples/controller_frames.txt
```

`WMR_CFG_MAX_FRAME` defaults to 80 for every image: 96 x 256 queued frames overflowed
`dram0_0_seg` by 568 bytes in the ESP32 image, while 80 builds with enough headroom for
both controller types. If a PSVR2 report turns out to be longer than 80, lower
`WMR_CFG_QUEUE_CAPACITY` or move the queue to a smaller element type before raising the
frame limit again.

## Build switches

| Switch | Default | Effect |
| --- | --- | --- |
| `WMR_CFG_PSVR2_WAKE` | `1` | `0` keeps PSVR2 recognition and raw forwarding but sends no wake request, for diagnostics. |
| `WMR_CFG_PSVR2_WAKE_DELAY_MS` | `250` | Delay between descriptor-available and the Feature Report `0x05` read, so SET_PROTOCOL can complete first. |
| `WMR_CFG_MAX_FRAME` | `80` | Must stay `>= 79`; checked at compile time. |
| `WMR_CFG_REPORT_HEXDUMP` | `0` | Also hex-dumps the Feature Report `0x05` response. |

## Sources

- Bluetooth names, USB IDs and the wake/report facts: ONY-81 report and
  [WMR_Bridge#44](https://github.com/nightcinder/WMR_Bridge/issues/44).
- Names were confirmed against Windows/Linux/Apple host reports in that investigation;
  they have not been captured through this firmware.
- Report layout (for the future decoder): ONY-233 research page,
  <https://app.notion.com/p/404145eff9f581ecaf73dfdeed2c8301>.
