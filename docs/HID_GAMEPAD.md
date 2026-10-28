# BLE HID gamepad (ONY-441)

The ESP32 exposes the two WMR controllers as one standard BLE HID-over-GATT
gamepad on the same phone link as the FF10 service. Android binds it as an
input device with no app installed. The FF10 service is unchanged: an app can
still read FF11/FF12/FF14 from the same connection.

## Report

Report ID 1, 9 payload bytes, the same layout as
`wmr_companion_hid_android/lib/src/hid_report.dart`:

| byte | content |
|---|---|
| 0-1 | 16 button bits, little endian, `hidButtonOrder`: A, B, X, Y, L1, R1, L2, R2, L3, R3, Select, Start, Guide |
| 2 | low nibble: hat switch 0..7 clockwise from up, 15 = centred; high nibble 0 |
| 3, 4 | left stick X, Y: -127..127, Y positive down |
| 5, 6 | right stick X (Z), Y (Rz), same scale |
| 7, 8 | left trigger (Brake), right trigger (Accelerator): 0..255 |

The descriptor is byte-for-byte `hidReportDescriptor` from that package. The
firmware copies it into `src/wmr_hid.c`; the vector files below fail the host
tests if the two ever drift.

## Mapping

`wmr_hid_map()` is a C port of `mapGamepad()` from
`wmr_core/lib/domain/gamepad.dart`:

- per hand: squeeze → L1/R1, stick click → L3/R3, menu → Select (LEFT) /
  Start (RIGHT), home → Guide;
- trigger: dead zone, rescale, analog Brake/Accelerator, and L2/R2 with the
  same press/release hysteresis as the app (`previous` state);
- trackpad click + touch → d-pad (left) or face buttons (right), using the
  same sector rule and centre radius;
- sticks: -1..1 with Y negated for HID; the `invertStickY` and
  `invertTrackpadY` settings are honoured;
- tilt: the pure mapper accepts roll/pitch and the same tilt config, but the
  firmware has no orientation fusion, so it always passes no tilt. Tilt stays
  an app-side feature.

The firmware feeds the mapper the decoded record's raw stick/trackpad divided
by 32767, so there is no learned stick centre and no radial dead zone in the
firmware. The app's `StickNormalizer` (centre learning and dead zone) is not
ported; a stick that rests off-centre will show that offset in a gamepad
tester. See "Known gaps".

## Link behaviour

- **No app needed:** HID is generated from every controller report, before and
  independently of the FF10 stream mode. A phone with no FF10 subscription
  still gets input.
- **Coalescing:** samples between pump ticks are collapsed. At most one HID
  report is sent per 10 ms, an unchanged report is not resent, and while a
  FF10 stream frame waits the pump alternates HID and stream sends.
- **Stuck input:** a hand with no sample for 250 ms is released, and a neutral
  report is sent once. The report history and both hands are cleared on BLE
  connect and disconnect, so neither a controller reconnect nor a phone
  reconnect can inherit the previous state.
- **Advertising:** the advertising data still carries the FF10 UUID and the
  name. The scan response adds Appearance `0x03C4` (Gamepad) and the HID
  service UUID `0x1812`, which Android uses to classify the device as a
  gamepad.
- **GATT:** `wmr_bridge.gatt` imports BTstack's `hids.gatt`; `hids_device`
  serves the Report Map, the input report (ID 1), HID Information, Protocol
  Mode and HID Control Point. Report characteristics require an encrypted
  link (`ENCRYPTION_KEY_SIZE_16`), so Android pairs before subscribing.

## Tests

Host tests compare both implementations against one vector file:

- `tests/test_hid.c` — parses `tests/vectors/hid_gamepad.txt` and
  `tests/vectors/hid_report_descriptor.txt`, checks every report byte and the
  descriptor, then checks the stale/coalescing state machine.
- Companion package `wmr_companion_hid_android` — the same two vector files
  are checked against `mapGamepad()` + `encodeHidReport()` (see
  `test/hid_gamepad_vectors_test.dart` in the companion PR).

Run the C side:

```bash
gcc -std=c99 -Wall -Wextra -Wconversion -Werror -Isrc \
  tests/test_hid.c src/wmr_hid.c src/wmr_decoded.c src/wmr_decode.c -lm -o test_hid
./test_hid tests/vectors/hid_gamepad.txt tests/vectors/hid_report_descriptor.txt
```

Firmware build (inside the BTstack ESP32 port):

```bash
python3 tools/firmware/build.py --variant both --out dist
```

Local build evidence for this change:

| Item | Value |
|---|---|
| Source commit | `084e2f3` (`0.2.4-10-g084e2f3`; includes ONY-438) |
| BTstack | `e38553977a25fb0b55b383c72c289be0975f422c` |
| ESP-IDF | v6.1 |
| Factory image SHA-256 | `e7553bc097bea0a560b8a94a1c4f3d54b89972097ea1fb83b7bcfd2e1454b764` |

The first phone attempt could not pair: it ran the pre-ONY-438 image
(`0.2.4-7-g90c6c83`) and the bridge showed two names for one address. Both are
fixed in the builds above. A phone link now needs ONY-438's LE Secure
Connections pairing with bonding; a build without it is not a valid test.

The second attempt (`0.2.4-8-gba1a8bb`) paired after several resets but showed
a phone icon and then would not connect once the phone entry was forgotten.
BTstack's default Class of Device is `0x007a020c` ("Smartphone"), and a
dual-mode phone uses the BR/EDR class instead of the LE appearance for that
icon and connect path. The bridge now sets `0x000508` (Peripheral / Gamepad)
and exposes the GAP Appearance characteristic `0x03C4`, matching the scan
response. The controller log's `HID SET_PROTOCOL failed: unsupported request`
line is the controller declining the optional report-protocol request; it still
streams 46-byte report-protocol input, so it is not the pairing fault.

### Device name and class on both transports

The bridge stays Classically discoverable so the controllers can be paired, so
a dual-mode phone sees the same address twice. `WMR_CFG_BLE_NAME` now sets the
BR/EDR local name too (`gap_set_local_name`), not just the LE advertising name;
before that the classic side kept BTstack's default `BTstack <bd_addr>` and the
phone's entry flipped between the two names every few seconds. The Class of
Device is now a gamepad rather than BTstack's smartphone default, so both
transports agree with the `0x03C4` appearance.

## Verification STATUS (ONY-441 done-when)

Nothing in this table is a hardware result yet. A row moves off `not run` only
when the paste-back below is filled in.

| Check | Result | Phone / Android | Build | Evidence |
|---|---|---|---|---|
| Stock phone pairs and shows an input device, no app | not run | — | — | — |
| Gamepad tester: A/B/X/Y, shoulders, stick clicks, menu, home | not run | — | — | — |
| Gamepad tester: d-pad via left trackpad (8 sectors) | not run | — | — | — |
| Gamepad tester: face buttons via right trackpad | not run | — | — | — |
| Analog sticks: full range, centre, Y direction | not run | — | — | — |
| Analog triggers: 0..255 and L2/R2 hysteresis | not run | — | — | — |
| No stuck input after controller disconnect/reconnect | not run | — | — | — |
| No stuck input after BLE disconnect/reconnect | not run | — | — | — |
| FF12 notification rate unchanged while HID is active | not run | — | — | — |
| Shared vectors pass on the companion side | passing (host) | n/a | companion PR | `hid_gamepad_vectors_test.dart` |

### Paste-back block

```
phone model:
Android version / build:
firmware build hash (sha256 of factory image):
app used for the tester (name, version):
A/B/X/Y, L1/R1, L2/R2, L3/R3, Select/Start/Guide:
d-pad sectors (all 8):
right trackpad face buttons:
stick X/Y min/max/centre, both hands:
trigger min/max, L2/R2 press and release:
controller disconnect -> all controls neutral:
BLE disconnect/reconnect -> all controls neutral:
FF12 rate before / with HID (reports/s):
notes:
```

## Known gaps

- **No completed phone run.** The Pixel 11 Pro / Android 17 passed pairing once
  on `0.2.4-8-gba1a8bb` but stopped connecting after the entry was forgotten,
  and the gamepad-tester block was not filled in. The table above is deliberately
  empty until the retest on `0.2.4-10-g084e2f3` produces that block.
- **Stick centre and dead zone.** The app's `StickNormalizer` (centre learning
  from calibration, radial dead zone) is not ported. A controller whose stick
  rests off-centre will show drift in a gamepad tester. This is the most
  likely "stick is not centred" finding in the phone pass.
- **Tilt** is not fed by the firmware; it remains an app feature.
- **Boot protocol** is not implemented. Android uses report protocol for
  gamepads; a host that switches the HID service to boot protocol will not get
  meaningful input.
- **App HID output** (`BluetoothHidDevice`) must be turned off when the
  firmware HID device is used, otherwise Android sees two gamepads. That is
  app UI work and is out of scope here.
- **Security.** Bonding behaviour is ONY-438's and is now merged: LESC with
  bonding, unbonded centrals refused. A HID build without it cannot pair on a
  current Android phone, so test only images built after that merge.
- **iOS and PC hosts** are out of scope.
