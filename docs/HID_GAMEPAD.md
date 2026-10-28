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
- **Advertising:** the primary advertising data carries the name, flags `0x06`
  (LE general discoverable, BR/EDR not supported) and the complete 16-bit UUID
  list with the HID service `0x1812` and the FF10 service. The scan response
  adds Appearance `0x03C4` (Gamepad). `0x1812` belongs in the primary packet, not
  only in the scan response: a host that misses the scan response otherwise sees
  a plain vendor device and will not treat it as a HID gamepad.
- **No input before subscribe:** the bridge sends HID input reports only after
  the phone enables the Report characteristic's CCCD (`HID input report enabled`
  in the console; `hid=on` in the status line). A neutral report is sent
  immediately after the subscription is enabled. `0.2.4-24` logged
  `HID reports=0` and the phone still dropped after nine seconds, so the
  reports sent during discovery in earlier builds were not the cause of the
  drop; the ATT trace below located it in the GAP service.
- **Connectable state is logged:** every status line prints `adv=on` or
  `adv=off`, the HCI result of LE Set Advertising Enable is reported if the
  controller refuses it, and advertising is re-asserted after controller
  discovery (`BLE advertising: on (discovery finished)`) and after a phone
  disconnect. A phone that cannot connect while `adv=off`, or a log with no
  `BLE phone connected` line, separates "bridge not advertising" from "phone did
  not try".
- **Phone transport is LE only:** the bridge is not Classic-discoverable
  (`WMR_CFG_CLASSIC_DISCOVERABLE=0`), and page scan stays on for controller
  reconnects. Advertising uses a **static random LE address** derived from the
  BT MAC (`0.2.4-36`), not the public BR/EDR address. A phone that knows the
  BR/EDR address can otherwise merge the two and open Classic HID, where the
  bridge is a HID host with no profile to serve. The 2026-10-28 captures show
  exactly that: `B0:9C:B2:CE:26:88`, a Google OUI and the Pixel's BR/EDR
  address, came up within a second of the LE drop in both the 16:05 and 19:06
  runs. The static random address is stable across reboots, so the phone bond
  survives; it does change once from the public-address build, so forget the
  old entry before pairing. The BR/EDR Class of Device is Miscellaneous
  (`0x000000`, `WMR_CFG_CLASS_OF_DEVICE`) for the same reason; the LE
  Appearance `0x03C4` still gives the gamepad icon.
- **GATT:** `wmr_bridge.gatt` imports BTstack's `hids.gatt`; `hids_device`
  serves the Report Map, the input report (ID 1), HID Information, Protocol
  Mode and HID Control Point. Report characteristics require an encrypted
  link (`ENCRYPTION_KEY_SIZE_16`), so Android pairs before subscribing.
- **Device Information Service:** `wmr_bridge.gatt` exposes DIS `0x180A` with
  manufacturer, model, firmware revision (the `PROJECT_VER` string) and a
  7-byte PnP ID. Android reads the PnP ID while it opens a HOGP device. The
  `0.2.4-25` trace's last read-by-type was first taken for that lookup, but the
  UUID-printing `0.2.4-29` trace showed it was `0x2A04`, not `0x2A50`. DIS is
  harmless metadata; it was not the cause of the drop.
- **Peripheral preferred connection parameters:** the `0.2.4-29` trace ends
  with `read-by-type 0x0001..0xffff type=0x2a04 uuid_len=2`, answered
  `attribute not found`. Android's GAP client reads `0x2A04` after service
  discovery; a failed read takes its client-complete path to
  `GATT_Disconnect()` on the link. That is the five-second drop every phone run
  showed: discovery completed, no HID read or Report CCCD write happened, and
  the phone closed the ACL itself (`reason=0x13`). `wmr_bridge.gatt` now
  exposes `0x2A04` (min/max 15 ms, latency 0, timeout 4 s).

## ATT trace

A build with `WMR_CFG_ATT_TRACE=1` (`EXTRA_CFLAGS=-DWMR_CFG_ATT_TRACE=1`
`python3 tools/firmware/build.py ...`) logs `ATT trace on: ...` at start-up,
prints one line per ATT or SMP PDU on the phone link, and adds
`ATT trace=on rx= tx= writes= two_byte_writes= smp= le_sig=` to the status
block. `tx` is phone→bridge (requests) and `rx` is bridge→phone (responses).
Read By Type and Find By Type lines carry `type=0x…` and `uuid_len=`, the
characteristic UUID the host is looking for and its encoded width (2 or 16
bytes). The trace shows how far a
HOGP host gets: service discovery (`read-by-group`, `read-by-type`), the
Report Map read (`read` 0x2A4B then `read-blob` at offset 22), the Protocol
Mode write (0x2A4E), and the Report CCCD write (value `0x0001`). `smp` counts pairing PDUs; a connection with SMP traffic but no ATT
lines is a pairing-only link, not a HOGP host. A trace that stops before the
CCCD write names the request that failed. Incoming Classic ACL connections are
logged too (`att incoming ACL link from <addr>`): a phone that decides the
bridge is a BR/EDR HID device pages it there instead of opening GATT.
The `BLE phone connected` line carries `fw=<version>`, so a capture that starts
mid-run still identifies the image. Off by default because a HID host produces
dozens of lines per connection.

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
| Source commit | `d298202` (`0.2.4-33-gd298202`, GAP `0x2A04` + BR/EDR ACL limit 3 + unknown-link drop) |
| BTstack | `e38553977a25fb0b55b383c72c289be0975f422c` |
| ESP-IDF | v6.1 |
| Factory image SHA-256 | `c5da5eb9e8d24aad5e5531183551b47ea323e4a2d6daa9c06db82afb16972eef` |

The first phone attempt could not pair: it ran the pre-ONY-438 image
(`0.2.4-7-g90c6c83`) and the bridge showed two names for one address. Both are
fixed in the builds above. A phone link now needs ONY-438's LE Secure
Connections pairing with bonding; a build without it is not a valid test.

The second attempt (`0.2.4-8-gba1a8bb`) paired after several resets but showed
a phone icon and then would not connect once the phone entry was forgotten.
BTstack's default Class of Device is `0x007a020c` ("Smartphone"), and a
dual-mode phone uses the BR/EDR class instead of the LE appearance for that
icon and connect path. `0.2.4-10` set `0x000508` (Peripheral / Gamepad) and
exposed the GAP Appearance characteristic `0x03C4`, matching the scan response.
That made the icon right but still left a Classic gamepad for Android to open;
`0.2.4-36` returns the Class of Device to Miscellaneous (`0x000000`) and keeps
only the LE appearance. The controller log's `HID SET_PROTOCOL failed:
unsupported request` line is the controller declining the optional
report-protocol request; it still streams 46-byte report-protocol input, so it
is not the pairing fault.

### Device name and class on both transports

The bridge still answers Classic pages for the controllers, so the same
address exists on both transports. `WMR_CFG_BLE_NAME` sets the BR/EDR local
name too (`gap_set_local_name`), not just the LE advertising name; before that
the classic side kept BTstack's default `BTstack <bd_addr>` and the phone's
entry flipped between the two names every few seconds. Since `0.2.4-36` the LE
advertisement uses a separate static random address, so the LE HID device and
the BR/EDR side are different identities; the BR/EDR Class of Device no longer
claims a HID device.

Inquiry scan is off, so a phone does not see the Classic side at all and pairs
the LE HID device. `gap_connectable_control(1)` is explicit: controllers page
the bridge on reconnect, and that needs page scan, not inquiry scan.

The third attempt (`0.2.4-10-g084e2f3`) showed the gamepad icon but the phone
reported `cannot communicate with <address>`; the attached bridge log shows no
phone link on either transport during the attempt (`phone=0`, no LE connection,
no Classic link from the phone), while the bridge was running controller
discovery, which refuses incoming Classic links. The phone had been paired
Classically in the second attempt, and the bridge had no HID-over-BR/EDR to
serve; a dual-mode entry like that always ends in that error. That is why the
Classic side is now hidden from phones.

The fourth attempt (`0.2.4-16`, the CI merge build of this branch and `main`)
still ended with a gamepad entry that would not connect, and the new bridge log
again has `phone=0` for the whole capture: no LE connection, and no Classic link
from the phone. The attempt fell inside controller discovery (inquiry rounds 1-6
and the connect phase, which refuses incoming Classic links), before the "wait
for `discovery finished`, then pair" step; after the manual reboot at 21 s the
bridge had both addresses stored, ran no discovery and streamed both hands, but
the log ends without another phone attempt. The build now logs `adv=on/off` in
every status line, reports a controller refusal of LE advertising, re-asserts
advertising when discovery finishes, and carries `0x1812` in the primary
advertisement. Retest with the console open on this build and paste the lines
around the attempt.

The fifth attempt (`0.2.4-16-g15b6805`, the CI merge build before `eab749b`;
its status lines still lack `adv=`) got further: the phone offered WMR Bridge
as a gamepad, paired and connected, and the bridge counted HID reports
(`HID reports=520 errors=0`). That count came from the unconditional send path,
not from a phone subscription: `0.2.4-24` gated sends on the Report CCCD and
logged `hid=off sub=0` with `HID reports=0`, so the phone had not subscribed in
that build either. After the phone entry was forgotten, the next connection
failed in security, not in advertising: the bridge still held the old key, so
`BLE re-encryption failed: status=0x06` (PIN_OR_KEY_MISSING) was followed by
`BLE phone refused: no bonded LE Secure Connections link; disconnecting`, which
cut off the Just Works exchange the phone had already started (`BLE pairing
failed: status=0x13`, then disconnect `reason=0x16`). The policy now deletes the
stale bond and keeps the link up for that exchange; every other failed
re-encryption still disconnects.

The `0.2.4-29-g748e928-att` trace (`ATT trace=on`, one line per ATT PDU, UUID
column) shows the phone pairing, bonding and discovering every service,
characteristic and descriptor. It then sent **no read and no write at all**, and
dropped the link (reason `0x13`) five seconds later. The last request was
`read-by-type 0x0001..0xffff type=0x2a04` (Peripheral Preferred Connection
Parameters), answered `attribute not found`. Android's GAP client reads that
characteristic after discovery; the failed read takes its client-complete path
to `GATT_Disconnect()`, so the phone closed the ACL before the HID profile
could start. DIS (`0.2.4-26`) was a correct addition but not the drop.
`0.2.4-31-g13c7d9f` adds GAP `0x2A04`; the next trace should show the HID
Information and Report Map reads and the Report CCCD write. The phone had
connected before that, so the remaining work is the full tester paste-back.

### 0.2.4-31 field run: phone connects, controllers hit the ACL limit

The 2026-10-28 16:05 capture is the first one where the phone stays connected
(user report). The ATT trace confirms the `0x2A04` fix: the
`read-by-type 0x2A04` is answered with 12 bytes instead of `attribute not
found`. The new failure is on the controller side:

```
connecting LEFT at 2C:4D:79:C8:C9:CC
LEFT HID connection failed: connection limit exceeded (no spare ACL slot) (0x09)
```

RIGHT was streaming at the time, and the capture also has an unknown Classic
link from `B0:9C:B2:CE:26:88` — not LEFT or RIGHT — that came up and never
opened a HID channel. The ESP32 controller defaults to two BR/EDR ACL links, so
the idle unknown link held the second slot and LEFT could never be paged.

Two changes fix that:

- `tools/firmware/build.py` sets `CONFIG_BTDM_CTRL_BR_EDR_MAX_ACL_CONN=3` in the
  BTstack ESP32 port's `sdkconfig` and fails the build unless the generated
  `sdkconfig.h` contains `CONFIG_BTDM_CTRL_BR_EDR_MAX_ACL_CONN_EFF 3`. The
  spare link means one stray Classic device cannot starve a controller.
- `src/wmr_controller.c` tracks an incoming Classic ACL from an address that is
  not LEFT or RIGHT. When both hands have stored addresses it disconnects the
  link immediately; while a hand is still unpaired it allows 5 s for a HID
  channel and then disconnects it. Before this, such a link stayed up forever.
  Declining an unknown HID channel also disconnects the underlying ACL.

The firmware logs `unknown Classic link <addr> up ... waiting for a HID
channel` or `incoming device <addr> is not LEFT or RIGHT; disconnecting`, so
the next capture names the device that used to hold the slot.

## 0.2.4-33 field run: connect stall and the reserved-radio hold

The 2026-10-28 18:54 capture (user: "worse than before") has no HCI `0x09`:
the third ACL slot removed that failure. It shows two boot-time problems
instead.

1. `connecting LEFT` completed its ACL (`LEFT link up ...`) and then sat for
   20 s with no encryption, no SSP confirmation, and no HID channel. The
   discovery connect phase waited for HID events that never came, so the
   single-hand connect never finished; the board was reset.
2. After the reset, RIGHT paired, encrypted, received its descriptor and
   streamed. At the start of inquiry round 4 the radio reservation
   disconnected it "for inquiry round 4", and its reconnect attempts were
   refused for the rest of the capture while the absent LEFT never answered.

The fix has two parts:

- A connect watchdog (`WMR_CFG_PAIR_ENCRYPT_MS`, `WMR_CFG_PAIR_CONNECT_TIMEOUT_MS`):
  an ACL that does not encrypt within 1.5 s gets an explicit encryption request
  when it has a stored key, and an ACL that produces no HID channel within 5 s
  is dropped so the attempt is retried.
- The radio reservation is bounded to `WMR_CFG_DISCOVERY_RESERVE_ROUNDS` (3)
  rounds per run. After that a connected hand may stay or reconnect while the
  remaining rounds look for the missing one.

The phone is absent from the whole 18:54 capture (`phone=0`), so the phone row
is unchanged by this run.

## 0.2.4-33 field run: the phone's BR/EDR fallback (fixed in 0.2.4-36)

The 2026-10-28 19:06 capture (`0.2.4-33`) shows the ACL fix working: LEFT and
RIGHT stream at ~180 reports/s, both FF13 blocks read (`state=2`), and the phone
connects, bonds and runs the full GATT discovery. It still never enables the
Report CCCD (`hid=off sub=0`, `HID reports=0`) and drops nine seconds after
connecting (`reason=0x13`).

The drop is preceded by an incoming Classic ACL from `B0:9C:B2:CE:26:88`. That
address is a **Google OUI**, i.e. the Pixel's BR/EDR side, not another gadget.
The 16:05 capture (`0.2.4-31`) has the same address at the same point: the LE
link closes with `reason=0x13` at 12:04:34 and the Classic ACL comes up at
12:04:35. There the bridge kept the unknown ACL (the drop-on-unknown rule came
later), and the phone showed "Connected" for the rest of the run: Android was
connected over BR/EDR, where the bridge has no HID device profile. That is also
the earlier "cannot communicate with <mac>" error.

`0.2.4-36` removes the Classic address from the phone's view of the device:

- LE advertising uses a static random address derived from the BT MAC
  (`esp_read_mac(ESP_MAC_BT)`, top two bits set), so the LE HID device and the
  bridge's BR/EDR address are no longer the same identity. Controller links are
  unaffected (they use the BR/EDR address).
- The BR/EDR Class of Device is `0x000000` (Miscellaneous), so the bridge is
  not a Classic gamepad; the LE Appearance still shows the gamepad icon.

The bond is keyed to the LE address, so this build needs one fresh pairing:
forget every "WMR Bridge" entry on the phone (there may be an LE entry and an
older Classic one), flash, then pair once.

## Verification STATUS (ONY-441 done-when)

Nothing in this table is a completed hardware result yet. A row moves off
`not run` only when the paste-back below is filled in; the pairing row records
what the console traces show so far.

| Check | Result | Phone / Android | Build | Evidence |
|---|---|---|---|---|
| Stock phone pairs and shows an input device, no app | PARTIAL: pairs and bonds; GAP `0x2A04` reads successfully, but the phone never enables the Report CCCD (`hid=off sub=0`) and drops after 6-9 s. The 16:05 and 19:06 captures show its BR/EDR side (`B0:9C:B2…`, Google OUI) coming up at the drop; `0.2.4-36` gives LE a static random address and a non-HID Class of Device. Retest pending | Pixel 11 Pro / Android 17 | `0.2.4-33-gd298202` (diagnosis), `0.2.4-36` (fix) | ATT trace, 16:05/19:06 captures, above |
| Both controllers connect while the phone is connected | PASS (controllers): 19:06 has LEFT and RIGHT streaming (~180 reports/s each, both FF13 blocks `state=2`) before and during the phone connection; the phone side still drops | Pixel 11 Pro / Android 17 | `0.2.4-33-gd298202` | 19:06 capture, above |
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

- **No completed phone run.** A Pixel 11 Pro / Android 17 paired and connected
  on `0.2.4-16-g15b6805` (`HID reports=520`, `errors=0`) and re-pairing after
  Forget is fixed by the stale-bond repair, but the gamepad-tester block was
  never filled in. Earlier attempts on `0.2.4-8`/`-10`/`-16` stopped at the icon or the
  connection. The next attempt needs the bridge console open, the phone entry
  forgotten first, `discovery finished` (and `adv=on`) before pairing, then the
  paste-back block.
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
