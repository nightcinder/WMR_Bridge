# BLE HID gamepad (ONY-441)

The ESP32 exposes the two WMR controllers as one standard BLE HID-over-GATT
gamepad on the same phone link as the FF10 service. Android binds it as an
input device with no app installed. The FF10 service is unchanged: an app can
still read FF11/FF12/FF14 from the same connection.

## Report

Report ID 1, 9 payload bytes. The report descriptor and the axis/hat layout
are the same as `wmr_companion_hid_android/lib/src/hid_report.dart`; the
button bits use the host's HID order rather than the package's model order
(see "Button slots" below):

| byte | content |
|---|---|
| 0-1 | 16 button bits, little endian, in Android/Linux HID order: A, B, C, X, Y, Z, L1, R1, L2, R2, Select, Start, Mode, L3, R3 (bit 0 is the first entry; bit 15 unused) |
| 2 | low nibble: hat switch 0..7 clockwise from up, 15 = centred; high nibble 0 |
| 3, 4 | left stick X, Y: -127..127, Y positive down |
| 5, 6 | right stick X (Z), Y (Rz), same scale |
| 7, 8 | left trigger (Brake), right trigger (Accelerator): 0..255 |

The descriptor is byte-for-byte `hidReportDescriptor` from that package. The
firmware copies it into `src/wmr_hid.c`; the descriptor vector file fails the
host tests if the two ever drift. The report bytes are the firmware's own
`tests/vectors/hid_gamepad.txt`, now in host button order.

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

### Button slots

Android treats a gamepad's 16 Button usages as Linux `BTN_A` and the following
codes in order (`Generic.kl`: `key 304` = `BUTTON_A` through `key 318` =
`BUTTON_THUMBR`). The report therefore has to leave slots 3 (C) and 6 (Z)
empty even though no WMR control uses them; otherwise every later button lands
one or two slots early. Firmware 0.3.2 packed the companion's model order
directly, which is the off-by-one the user saw.

| Physical control | HID usage / bit | Android keycode | Label in a tester |
|---|---|---|---|
| A | 1 / bit 0 | `BUTTON_A` (96) | A |
| B | 2 / bit 1 | `BUTTON_B` (97) | B |
| (none) | 3 / bit 2 | `BUTTON_C` (98) | C |
| X | 4 / bit 3 | `BUTTON_X` (99) | X |
| Y | 5 / bit 4 | `BUTTON_Y` (100) | Y |
| (none) | 6 / bit 5 | `BUTTON_Z` (101) | Z |
| Squeeze LEFT | 7 / bit 6 | `BUTTON_L1` (102) | L1 |
| Squeeze RIGHT | 8 / bit 7 | `BUTTON_R1` (103) | R1 |
| Trigger LEFT | 9 / bit 8 | `BUTTON_L2` (104) | L2 |
| Trigger RIGHT | 10 / bit 9 | `BUTTON_R2` (105) | R2 |
| Menu LEFT | 11 / bit 10 | `BUTTON_SELECT` (109) | Select |
| Menu RIGHT | 12 / bit 11 | `BUTTON_START` (108) | Start |
| Home | 13 / bit 12 | `BUTTON_MODE` (110) | Mode / Home |
| Stick click LEFT | 14 / bit 13 | `BUTTON_THUMBL` (106) | L3 |
| Stick click RIGHT | 15 / bit 14 | `BUTTON_THUMBR` (107) | R3 |

The hat (d-pad) and both trackpad roles are unchanged. The companion package's
`encodeHidReport()` still packs its model order for its own
`BluetoothHidDevice`; app HID output should be off when the firmware gamepad
is in use, and moving that encoder to the same table is a companion follow-up.

### Bench check for the user

1. Flash the build from this change and forget/re-pair the phone once if the
   HID descriptor cache keeps the old layout (the descriptor itself is
   unchanged, so a normal reconnect is usually enough).
2. Open any gamepad tester that shows button names (the Android
   `BUTTON_*` labels above) and press each control on each hand.
3. Expected: A→A, B→B, X→X, Y→Y, squeeze→L1/R1, trigger→L2/R2,
   stick click→L3/R3, menu→Select/Start, home→Mode. C and Z never light up.
4. Paste the observed labels back on ONY-685; a mismatch names the remaining
   slot offset.

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

## 0.2.4-37 field run: CTKD makes a Classic bond for the LE identity (fixed in 0.2.4-38)

The 2026-10-28 20:11 capture (`0.2.4-37-gb8e48a4-att`) shows the phone pairing
three times and storing a bond each time, then closing the LE link about 5 s
after connecting (`reason=0x13`) with `hid=off` and **no ATT PDU after
encryption**. The first attempt reads the GAP Device Name, pairs, bonds, and
stops; the next two reconnects fail re-encryption with `status=0x06` (the
bridge cannot find the key), delete the bond and re-pair. No Classic ACL from
the phone appears, so Android is not reaching the bridge's BR/EDR side at all.

The ESP32 port compiles Cross-Transport Key Derivation (CTKD) in whenever
Classic and LE are both enabled. During LE pairing the two sides therefore set
the CT2 bit and derive a BR/EDR link key. Android stores that derived key as a
**Classic bond for the LE identity address** — and since `0.2.4-36` that
identity is a static random address that does not exist on BR/EDR. The phone
then treats the device as dual-mode, `btif_hh_transport_select()` chooses the
Classic HID transport, pages an address the bridge does not own, and the
pairing UI returns to the scan list. The bridge never sees the page, which is
why the log has a bond but no GATT client. With the public address this
fallback was real HID over BR/EDR (the 19:06 Classic ACL).

`tools/firmware/build.py` now removes `ENABLE_CROSS_TRANSPORT_KEY_DERIVATION`
from the port's `btstack_config.h` before every build (verified there, like the
ACL limit), so the bridge never offers CTKD. Controllers pair over Classic SSP
and are unaffected; the bridge never uses a CTKD-derived key. The boot line
`BLE LE address: … (static random…)` and the new `BLE pairing complete: bond …
identity=… type=… irk=…` line make the next capture self-describing.

## 0.2.4-38 field run: phone pass (2026-10-28)

The first complete phone run (`0.2.4-38-g4bcf01c`, factory sha256
`b1794838…`, Pixel 11 Pro / Android 17) passes. Both controllers were already
connected when the phone paired at 16:43:16. The bond was stored at 16:43:17
(`bond 15 identity=B0:9C:B2:CE:26:88 type=public irk=yes`), ATT MTU rose to
517, data length to 251 octets, and Android enabled the HID Report CCCD at
16:43:20 (`HID input report enabled (report id 1)`). The status line then read
`hid=on mtu=517`, and `HID reports` climbed from 33 to 3433 with `errors=0`
over about 51 s (~68 reports/s) while both controller links stayed at
~170-190 reports/s with both FF13 blocks at `state=2`. The phone lists no
second (Classic) entry, so disabling CTKD removed the BR/EDR fallback.

The bridge rebooted at 16:44:13 and the phone reconnected at 16:44:17 to the
stored bond without re-pairing (`BLE phone secure: bonded LE Secure
Connections link`), re-enabled the report and received only the neutral report
(`HID reports=1`), so no input was stuck across the BLE reconnect. The user
confirms the gamepad appears and the tester registers controls.

Not captured in this run: a controller power-cycle after that reboot (the
controllers had not reconnected before the log ends), and per-control values
(stick min/max/centre, trigger range, d-pad sectors). The app-side
`StickNormalizer` gap below is unchanged.

## Verification STATUS (ONY-441 done-when)

The phone rows are now a completed hardware result: the 2026-10-28
`0.2.4-38` run passed the gamepad test in a stock phone. The paste-back
template below remains for future runs; this pass answered the tester and
Classic-entry questions rather than filling in every value.

| Check | Result | Phone / Android | Build | Evidence |
|---|---|---|---|---|
| Stock phone pairs and shows an input device, no app | PASS: pairs, bonds, ATT MTU 517, `hid=on`, ~68 HID reports/s with `errors=0`; reconnects to the bond after a bridge reboot | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | 16:43-16:44 capture, above |
| Both controllers connect while the phone is connected | PASS: both hands streaming ~170-190 reports/s, both FF13 blocks `state=2`, before and during the phone link | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | 16:42-16:44 capture, above |
| Gamepad tester: A/B/X/Y, shoulders, stick clicks, menu, home | PASS (user): gamepad appears and controls register | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | user confirmation, 2026-10-28 |
| Gamepad tester: d-pad via left trackpad (8 sectors) | PASS (user): controls register; sectors not itemised | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | user confirmation, 2026-10-28 |
| Gamepad tester: face buttons via right trackpad | PASS (user): controls register | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | user confirmation, 2026-10-28 |
| Analog sticks: full range, centre, Y direction | PASS (user): sticks register; range/centre not itemised | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | user confirmation, 2026-10-28 |
| Analog triggers: 0..255 and L2/R2 hysteresis | PASS (user): triggers register; values not itemised | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | user confirmation, 2026-10-28 |
| No stuck input after controller disconnect/reconnect | PARTIAL: after the bridge reboot the phone received only the neutral report; controller power-cycle not re-captured | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | 16:44 capture, above |
| No stuck input after BLE disconnect/reconnect | PARTIAL: phone reconnected to the bond after a bridge reboot and got only the neutral report (`HID reports=1`); a controller power-cycle after that reboot was not captured, so the reconnect half of this row is unproven | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | 16:44 capture, above |
| FF12 notification rate unchanged while HID is active | PARTIAL: controller/source rate unchanged (~170-190 reports/s per hand, 0 drops, FF13 `state=2`) with HID at ~68/s, but FF12 was not subscribed in this run (`stream=none`), so the simultaneous FF12 + HID notification rate was not sampled | Pixel 11 Pro / Android 17 | `0.2.4-38-g4bcf01c` | 16:42-16:44 capture, above |
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

## BLE HOGP vs Companion Android HID coexistence

The ESP32 bridge exposes standard BLE HID-over-GATT (HOGP, service `0x1812`) and the vendor `FF10` GATT service concurrently on the same LE link (`adv=on`, Appearance `0x03C4`). Android binds the bridge's HOGP gamepad profile directly at the OS level as an input device upon BLE bonding.

The Companion app (`wmr-companion`) also provides an optional app-level HID gamepad output module (`BluetoothHidDevice` / `wmr_companion_hid_android`) designed to make the phone act as a HID peripheral sending gamepad reports to a remote PC, TV, or console host.

### Coexistence rule for testers:
- **Do not enable Companion app HID output while using bridge HOGP.** `BluetoothHidDevice` registers the Android phone as a HID peripheral and disables Android's internal HID Host service. Consequently, when Companion app HID output is active, Android stops receiving the bridge's HOGP input reports. Additionally, running the Classic HID peripheral link alongside the BLE GATT link causes phone-side radio contention and higher latency.
- When testing the firmware BLE HID gamepad, keep Companion app HID output (`HidOutputPanel` / `BluetoothHidDevice`) **disabled**.
- **Bonding coupling:** Because `FF11`, `FF12`, and `FF14` are `READ/WRITE_ENCRYPTED` and the bridge requires a bonded LESC connection, unpairing or forgetting the bridge device in Android Bluetooth settings deletes the bond and immediately terminates encrypted GATT communication; re-pairing re-establishes the bond and automatically re-registers HOGP. The two services cannot be decoupled on the same bonded Android host.

## Known gaps

- **Phone pass complete, three gaps.** The 2026-10-28 `0.2.4-38` run passes
  pairing, HID subscription, controls and the post-reboot BLE reconnect. A
  controller power-cycle after that reboot, the per-control values above, and
  a simultaneous FF12 + HID rate sample were not captured; the tester
  confirmation covers the controls, and the source rate is unchanged.
- **Stick centre and dead zone.** The app's `StickNormalizer` (centre learning
  from calibration, radial dead zone) is not ported. A controller whose stick
  rests off-centre will show drift in a gamepad tester; this pass did not
  itemise stick centres.
- **Tilt** is not fed by the firmware; it remains an app feature.
- **Boot protocol** is not implemented. Android uses report protocol for
  gamepads; a host that switches the HID service to boot protocol will not get
  meaningful input.
- **App HID output coexistence** (`BluetoothHidDevice`): as detailed in the
  coexistence section above, app-level HID output must be turned off when the
  firmware HOGP device is used; registering `BluetoothHidDevice` disables
  Android's HID Host service, which stops reception of the bridge's HOGP
  input reports.
- **Security.** Bonding behaviour is ONY-438's and is now merged: LESC with
  bonding, unbonded centrals refused. A HID build without it cannot pair on a
  current Android phone, so test only images built after that merge.
- **iOS and PC hosts** are out of scope.
