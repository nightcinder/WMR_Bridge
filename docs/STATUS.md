# WMR Bridge: Firmware & Bridge Status

Last updated: 2026-11-10 (current release 0.3.19: the Pi package in this tree is
`0.3.19` and marks its `LEAdvertisement1` object discoverable, so BlueZ adds the
LE General Discoverable flag (`0x06`) to the advertising data and a phone lists
the bridge in a Bluetooth scan (ONY-1197). It keeps the 0.3.18 FF10
`org.bluez.GattService1` export that makes `RegisterApplication` succeed
(ONY-1186) and the idle power-off reconnect cooldown (ONY-1185), the 0.3.17
dbus-next GATT start annotations and the 0.3.16 uplink order — Ethernet over
5 GHz over 2.4 GHz Wi-Fi via route metrics — plus `AF_NETLINK` in the systemd
unit and token access for `wifi_probe.py` (ONY-1114, ONY-1115, ONY-1144). The
ESP32 firmware last changed
in 0.3.16, which added controller idle sleep and power-off (ONY-1128) on top of
the 0.3.15 change that re-sends the FF13 streaming-enable pair on reconnect and
every 10 s so a connected controller stays awake (ONY-1062). The firmware's
controller families are unchanged from
0.3.14: one driver table per model with PSVR2 FF12/FF14 decoding and Nintendo
Switch Joy-Con/Pro handshake and `0x30` decoding; Switch and PSVR2 support remain
host-tested and firmware-build-tested only and have never run on a controller,
with the Switch hardware check tracked in GitHub #145. The Pi pairs controllers
through BlueZ automatically (the `v0.3.12` release shipped `0.2.4`) and does not
yet read FF13 calibration from controllers. FF10–FF17 payloads otherwise match
0.3.15, so companions at 0.2.2 or later continue to work. BLE advertising has
not run on hardware; the three-state jitter check is ONY-1070. ESP32-over-BLE is
the default supported target and the Linux/Raspberry Pi Wi-Fi host bridge is the
second supported first-class target; Pi is host-tests-only until the Pi sitting
runs, see [`docs/PI_BRIDGE.md`](PI_BRIDGE.md) and
`docs/PI_BRIDGE.md`). Firmware and Pi
hardware runs remain unverified unless a run sheet proves otherwise.

This document tracks firmware and host-bridge capabilities and empirical
findings in this repository. Two supported first-class bridge targets are
maintained here:

- ESP32-over-BLE (`src/`, default): BTstack Classic Bluetooth HID host
  forwarding over BLE GATT (`FF10` service; `FF11`–`FF16`). Bench-tested with
  two WMR controllers and an Android phone (user-reported figures below).
- Linux / Raspberry Pi-over-Wi-Fi (`pi/`, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md)):
  BlueZ Classic Bluetooth HID host forwarding the same `FF11`/`FF12`/`FF14`
  payloads over a 5 GHz Wi-Fi WebSocket (`wmr-wifi-v1`). Second supported
  target, but host-tests-only until the Pi sitting runs
  (`docs/PI_BRIDGE.md`).

The overall project status, milestones, roadmap, and companion Flutter app state
(including 3D viewer, orientation fusion, and host latency tracking) are
maintained at the project's single entry point:
[nightcinder/wmr-companion docs/STATUS.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/STATUS.md).

## Bridge Capabilities & Milestones

| Capability | State | Hardware Check (user-reported) |
|---|---|---|
| Classic BT HID host (dual WMR controllers, ESP32-over-BLE default target) | Merged | Yes; controllers connect and stream |
| IMU & input decoding (200 Hz nominal, 100 ns ticks) | Merged | Yes; gyro rad/s, accel magnitude ~10 |
| GATT characteristics FF11 (raw) & FF12 (decoded) | Merged | Yes; mutual exclusion and MTU raise tested |
| FF14 batched notification stream | Merged | Yes; default stream, 9 records/batch, queue drop=0 |
| FF15 echo round-trip turnaround | Merged | Yes; p50 0.31–1.81 ms across the 2026-10-24 ladder. FF14 402 µs, none 309 µs, FF11 1.25 ms, FF12 1.81 ms: in the raw/decoded modes the answer waits behind stream notifications. Worst answer 63.5 ms (FF14) and about 43 ms (FF11, FF12), against 7.5 ms on 2026-10-22. Four runs, one position ([`docs/runs/roundtrip/`](runs/roundtrip/README.md)) |
| FF13 controller calibration blob manager & GATT stream | Merged | Yes; both blocks read to READY (app-side parsing/fusion tracked in companion docs/STATUS.md) |
| Controller discovery by name & NVS address persistence | Merged | Yes; D1–D11 checks passed |
| Link diagnostics (`link:`, `role:`, `POLICY:`) | Merged | Yes; verified in contention-2 runs |
| Firmware/Pi logging cleanup (single log format, human-readable pairing diagnostics, Pi hand/client logs) (ONY-313) | Merged (v0.2.1) | Host tests only; not run on hardware |
| Discovery pairing fixes: retry failed pairing connects, reserve the Classic radio while a hand is missing, free the ACL slot before each discovery page (ONY-307, PRs #77 and #80–#82) | Merged (v0.2.1–v0.2.4) | Field logs on 0.2.2/0.2.3 only; the packaged v0.3.11 image has not been run on hardware (internal bench runbook block C) |
| BLE phone-link hardening: LE Secure Connections with bonding, encrypted data characteristics, stale-bond recovery (ONY-438, #88, [`docs/SECURITY.md`](SECURITY.md)) | Merged (v0.3.0) | Pairing, bonding and reconnect were exercised by the 0.2.4-38 phone runs; the packaged v0.3.11 pairing and encryption run is not yet done (internal bench runbook block B) |
| Single-hand bridge mode `WMR_CFG_BRIDGE_HANDS` / `WMR_CFG_BLE_NAME` (PR #33) | Merged | Host tests only, not run on hardware |
| Linux / Raspberry Pi Wi-Fi host bridge (`pi/`, WebSocket replaces BLE; PR #47, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md), `docs/PI_BRIDGE.md`) | Merged (second supported target) | Host tests only (312 tests + loopback replay), the real BlueZ auto-pair path has not been run on Pi hardware or with controllers; FF13 blobs are cached and forwarded, but the controller read over hidraw is not implemented |
| One-command build/package/flash framework and the browser installer (PR #37, [`docs/FLASHING.md`](FLASHING.md)) | Merged | CI-built; flashing a packaged image has not been reported |
| Packaged firmware version over FF16 and the console `v` command (PR #38, [`docs/COMPATIBILITY.md`](COMPATIBILITY.md)) | Merged | Console `v` read on the 0.2.4 hardware run (`0.2.4`, ESP-IDF v6.1); the live FF16 phone read on v0.3.11 is open (internal bench runbook block B) |
| PSVR2 Sense controller support: name/model discovery searched by default beside WMR, model persistence, 79-byte raw frames, decoded FF12/FF14 samples and Feature Report `0x05` wake (PR #46, [`docs/PSVR2.md`](PSVR2.md)) | Merged (always compiled in; 0.2 default); decoded stream added by ONY-945 (v0.3.11) | Host tests and firmware build only, never run on a PSVR2 controller |
| Switch Joy-Con/Pro controller support: discovery names, output report `0x01` subcommand handshake, SPI calibration packed into FF13, three-sample `0x30` decode into FF12/FF14 (ONY-946, [`docs/SWITCH.md`](SWITCH.md)) | Merged (v0.3.11) | Host tests (`tests/test_switch.c`) and firmware build only, never run on a Switch controller; hardware verification tracked in GitHub #145 |
| Controller ACL that stalls after discovery: encrypt it from the bridge, then drop it after 4 s so the controller pages again (ONY-446, #92, [`docs/DISCOVERY.md`](DISCOVERY.md)) | Merged (v0.3.0) | Host-tested (`tests/test_linkwait.c`); field logs on 0.2.4 only, packaged v0.3.11 not run on hardware (internal bench runbook block C) |
| Addressable status LED: red none, yellow LEFT, blue RIGHT, green both (#96) | Merged (v0.3.0) | Host-tested (`tests/test_status_led.c`); the GPIO 4 colour check has not run on hardware (internal bench runbook block E) |
| Runtime contention toggles (`l`/`v`/`t`/`q` LEVERS commands, ONY-430, #86/#97, [`docs/CONTENTION_LEVERS.md`](CONTENTION_LEVERS.md)) | Merged (v0.3.0) | Host-tested (`tests/test_levers.c`); bench run still open |
| Pi host-bridge packaging: `build_package.sh`, `verify_package.sh` and a no-hardware `dry_run.sh` (ONY-440, #87, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md)) | Merged (v0.3.0) | Packaging tests and dry run pass; never run on Pi hardware |
| M5Stack ATOM Lite + Atomic Battery Base battery life investigation ([`docs/ATOM_LITE_BATTERY.md`](ATOM_LITE_BATTERY.md)) | Documented | Electrical and efficiency model: about 45–52 min dual-controller, about 58–65 min single-controller; not recommended for session VR |
| BLE HID-over-GATT gamepad (ONY-441, merged for v0.3.0) | Implemented and passes the 2026-10-28 phone run on the pre-0.3.0 branch build `0.2.4-38-g4bcf01c`: it pairs, bonds, raises ATT MTU to 517, enables the HID Report CCCD (`hid=on`) and streams HID reports (about 68/s, `errors=0`) with both controllers connected; the phone reconnects to the bond after a bridge reboot without re-pairing. CTKD was the last blocker: `0.2.4-38` disables it, so Android keeps the LE HID transport and shows no second Classic entry | PASS (phone + mapping) on `0.2.4-38-g4bcf01c`: Pixel 11 Pro / Android 17; gamepad appears and controls register in the tester. Controller power-cycle after the reboot was not re-captured. The v0.3.11 recheck is open (internal bench runbook block D). See [`docs/HID_GAMEPAD.md`](HID_GAMEPAD.md) |
| Packet loss and pose prediction investigation ([`docs/PREDICTION.md`](PREDICTION.md)) | Documented; ONY-176 Sitting R run-ready | Re-analysis of the 2026-10-22 120 s run: 18–25 % missing on the Classic uplinks, 0 sequence gaps on BLE. The 2026-10-24 run on the `v0.1.1` bridge and `0.1.0` app measured 21.0 % (LEFT) and 15.2 % (RIGHT) tick loss, inside that spread, and the later FF12 run in the same sitting 30.7 % / 17.6 %. One ESP32 is adequate for the 3DoF demo but not VR-grade dual-controller tracking, and 40.5–44.8 ms is the current round-trip median with any stream against 61.6 ms with none ([`docs/runs/roundtrip/`](runs/roundtrip/README.md)). ONY-176 adds the run-ready order for V1 stagger, RIGHT-asymmetry phases 1-2, DLE/interval/FF14 hold and the diagnostics rule-out for host buffers (`the removed single-ESP32 levers sheet (ONY-1157; see git history)`); it also adds the `WMR_CFG_BATCH_HOLD_INTERVALS` knob (default 1). ONY-23 (M7 latency-2) is closed: **Stay Flutter** under the ONY-1010 amendment (warm/stable start; one-sided `d <= +5 ms`, stalls within 25 %). The 2026-11-07 pair misses the Stay branch on RIGHT stalls (440 vs 698, 37 % apart) and lands in between: stay Flutter, gaps filed upstream. 2026-10-28 `d` = +3.7 ms (one unpaired run per client); 2026-11-07 `d` = -16.3 / -2.6 / -9.9 ms at 250 / 50 / 1000 ms (rule in [latency-2](https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/latency-2.md); figures in [`docs/runs/roundtrip/`](runs/roundtrip/README.md)) |

## BLE HID gamepad (ONY-441)

Scope: the same phone link also exposes one standard BLE HID-over-GATT gamepad,
so Android sees the controllers with no app. The FF10 service is unchanged.
Implementation and procedure: [`HID_GAMEPAD.md`](HID_GAMEPAD.md).

| Check | Result | Phone / Android / build | Where |
|---|---|---|---|
| Stock phone pairs and shows a gamepad, no app | PASS: `0.2.4-38` pairs and bonds, ATT MTU 517, `hid=on`, HID reports about 68/s with `errors=0`, and the phone reconnects to the bond after a bridge reboot | Pixel 11 Pro / Android 17, `0.2.4-38-g4bcf01c` | `HID_GAMEPAD.md` §0.2.4-38 field run |
| Both controllers connect while the phone is connected | PASS: both hands stream about 170-190 reports/s with both FF13 blocks `state=2` while the phone is bonded and receiving HID | Pixel 11 Pro / Android 17, `0.2.4-38-g4bcf01c` | same |
| Every control in a gamepad tester (buttons, d-pad, face buttons, sticks, triggers) | PASS (user): gamepad appears and controls register; per-control values not itemised | Pixel 11 Pro / Android 17, `0.2.4-38-g4bcf01c` | same |
| No stuck input after controller or BLE reconnect | PARTIAL: after the 16:44 bridge reboot the phone reconnected to the bond and received only the neutral report (`HID reports=1`); controller power-cycle after that reboot not captured | Pixel 11 Pro / Android 17, `0.2.4-38-g4bcf01c` | same |
| FF12 rate unchanged while HID is active | PARTIAL: controller/source rate about 170-190 reports/s per hand, 0 drops, FF13 `state=2`, HID about 68/s; FF12 itself was not subscribed (`stream=none`), so the simultaneous FF12 + HID rate was not sampled | Pixel 11 Pro / Android 17, `0.2.4-38-g4bcf01c` | same |
| Shared input→report vectors, firmware side | passing (host) | `tests/test_hid.c` | `tests/vectors/hid_gamepad.txt` |
| Shared input→report vectors, companion side | passing (host) | `wmr_companion_hid_android` | companion PR |

The firmware build (ESP-IDF v6.1, BTstack `e38553977`, both-hands variant)
passes and the host vector tests pass. A Pixel 11 Pro / Android 17 retest on
`0.2.4-8-gba1a8bb` paired after several bridge resets, but the phone showed a
phone icon and stopped connecting after the entry was forgotten. The cause is
BTstack's default Class of Device `0x007a020c` ("Smartphone"), which a dual-mode
phone prefers over the LE appearance. `0.2.4-10-g084e2f3` set Peripheral /
Gamepad (`0x000508`) and added the GAP Appearance characteristic (`0x03C4`).
That made the icon right but left a Classic gamepad. `0.2.4-36` sets the Class
of Device back to Miscellaneous (`0x000000`). The `0.2.4-10-g084e2f3` run then
showed the gamepad icon but ended in `cannot communicate with <address>`; the
bridge log had no phone link at all on either transport, and the phone still held
a Classic bond from the earlier attempt. A Classically discoverable bridge is
paired over BR/EDR by Android, where this HID host has no profile, so the
bridge now keeps page scan but turns inquiry scan off and advertises `BR/EDR not
supported` (flags `0x06`). That was not enough: the phone still had the public
BR/EDR address and paged it at every LE drop. `0.2.4-36` advertises LE from a
static random address derived from the BT MAC and sets the BR/EDR Class of
Device to Miscellaneous (`0x000000`), so the phone has no Classic gamepad to
fall back to. GAP Appearance `0x03C4` keeps the gamepad icon. Forgetting the old
entry once is part of the retest.

The `0.2.4-16` retest (CI merge build of this branch and `main`) still showed a
gamepad entry that would not connect, and the attached bridge log again had
`phone=0` with no LE connection and no Classic link from the phone for the whole
capture. The attempt ran while controller discovery held the Classic radio
(inquiry rounds 1-6) and before the phone retry that the instructions ask for
after `discovery finished`. The log also shows the bridge refusing incoming
Classic links in that phase. The HID service UUID `0x1812` now sits in the
primary advertisement (not only in the scan response), advertising is
re-asserted after discovery and after a phone disconnect, and every status line
prints `adv=on`/`adv=off` so the next log says whether the phone could connect
at that moment. Every row above that needs a phone stays `not run` until the
paste-back block in `HID_GAMEPAD.md` is filled in.

The `0.2.4-29-g748e928-att` trace shows where the phone stops. It paired,
bonded, and discovered every service, characteristic and descriptor; then it
sent no ATT read and no ATT write at all, and dropped the link (reason `0x13`)
five seconds later. The last request was
`read-by-type 0x0001..0xFFFF type=0x2A04`, a lookup of Peripheral Preferred
Connection Parameters, which the bridge did not expose, so it was answered
`attribute not found`. Android's GAP client reads `0x2A04` after discovery and
disconnects the link when that read fails, which is why the HID profile never
started. DIS `0x180A` with the PnP ID (`0.2.4-26`) was a correct addition but
not the cause. `0.2.4-31-g13c7d9f` adds `0x2A04` (min/max 15 ms, latency 0,
timeout 4 s). Phone rows stay `not run` until the next paste.

The 2026-10-28 16:05 run on `0.2.4-31` reports the phone connected and stayed,
and the ATT trace shows the `0x2A04` read now returning 12 bytes. The same
capture exposes the next failure on the Classic side: RIGHT streams, an unknown
Classic link at `B0:9C:B2:CE:26:88` comes up and never opens a HID channel, and
`connecting LEFT` then answers `connection limit exceeded (no spare ACL slot)
(0x09)`. The ESP32 controller was configured for two BR/EDR ACL links, so the
idle unknown link took the one LEFT needed. `tools/firmware/build.py` now sets
`CONFIG_BTDM_CTRL_BR_EDR_MAX_ACL_CONN=3` and fails the build unless the generated
header shows the effective value. `src/wmr_controller.c` drops a Classic ACL
from an address that is not LEFT/RIGHT once both hands have stored addresses, and
gives an unknown link 5 s to open a HID channel while a hand is still unpaired.
Both controllers with the phone connected remain `not run`.

The 2026-10-28 16:43 run on `0.2.4-38-g4bcf01c` closes that gap. Both
controllers stream (about 170-190 reports/s, FF13 `state=2`), the phone bonds,
ATT MTU reaches 517, Android enables the Report CCCD (`hid=on`), and HID reports
run at about 68/s with `errors=0`. The phone reconnects to the bond after a
bridge reboot and no Classic entry appears. The user confirms the tester
registers controls. Details in `HID_GAMEPAD.md` §0.2.4-38 field run.

## Android verification campaign (ONY-439, 0.3.11 pass)

Scope: phone + one ESP32 only. Sheet:
the removed internal-ESP32 bench sheet (ONY-1157; see git history).
The 0.3.11 bench pass has four gate checks: FF16 on Companion `0.2.2`, ONY-446
RIGHT reconnect, status LED colours, and the Android HID gamepad. All four are
`not run`. The rows below are the 0.2.4 sitting. A row leaves `not run`
only when that check's figures arrive, from the removed internal-ESP32 bench
sheet's results and paste-back block (ONY-1157; see git history). Rows that name a
device and build do
so because a run exists. `, ` means nothing has run yet.

| Check | Result | Device / build | Where the procedure lives |
|---|---|---|---|
| FF16 live read | `partial`, app 0.2.1 had no FF16 display (wmr-companion #107); Companion 0.2.2 adds it and the 0.3.11 recheck is open. Console `v` settles the 0.2.4 build half (`0.2.4`, `ESP-IDF v6.1`); reconnect half not run | Pixel 11 Pro, Android 17; bridge 0.2.4 / `41a9e5f`, app 0.2.1 | the removed internal-ESP32 bench sheet (ONY-1157; see git history) |
| FF17 scan/connect | `partial`, status v2 captured from FF17 notifications: version 1, both hands `addr_source` 2 (NVS) with distinct tails, discovery idle / round 4, `flags` 0 on both (taken disconnected). DISCOVER `0x01` → `last_result` `0x03` NOTHING_TO_DO; `0x05 0x01` FORGET_BOND RIGHT → `last_result` `0x00` OK with RIGHT disconnected, which explains the earlier `05/06` as a >2-byte write and makes a `134` consistent with ATT `0x86`. Connected-state status, connect/disconnect, double disconnect, `0x7f` and step 6 as written (BUSY while RIGHT connected) unrun; RIGHT link key cleared, so it must be re-paired | Pixel 11 Pro, Android 17; bridge 0.2.4 / `41a9e5f`, app 0.2.1 | the removed internal-ESP32 bench sheet (ONY-1157; see git history) |
| Stick dead zone | `partial`, normalized rest `0.0000, 0.0000` both hands at dead zone 0.05, centres LEFT `-0.0227, 0.0090` / RIGHT `-0.1119, 0.0819`; three sittings: RIGHT stable at about `-0.112, +0.085` (real bias), LEFT moved `-0.030, -0.011` → `-0.008, +0.021` → `+0.017, +0.025`, still learning. Dead zone 0 set but saved settings read `learn true` after the switch was reported off (toggle persistence unconfirmed), so no centre-zeroed raw/normalized pair; end stops, held offset and a confirmed learning-off run missing | Pixel 11 Pro, Android 17; bridge 0.2.4 / `41a9e5f`, app 0.2.1 | the removed internal-ESP32 bench sheet (ONY-1157; see git history) |
| Latency stream ladder | `partial`, one 120 s run per stream: p50 `none` 63.4 / 62.3, FF11 34.3, FF12 34.5, FF14 39.3 ms, 479/479 answered; one session, so no ladder verdict (`latency-2` §3.3) | Pixel 11 Pro, Android 17; bridge 0.2.4 / `41a9e5f`, app 0.2.1 | `wmr-companion` `docs/runs/sheets/latency-2.md` §3–§5 |
| App-vs-native probe decision | `pass`, ONY-23 closed: **Stay Flutter** under the ONY-1010 amendment (warm/stable start; one-sided `d <= +5 ms`, stalls within 25 %). The 2026-11-07 pair misses the Stay branch on RIGHT stalls (440 vs 698, 37 % apart) and lands in between: stay Flutter, gaps filed upstream. 2026-10-28 app 39.3 vs probe 35.6 ms (`d` = +3.7, one unpaired run per client); 2026-11-07 `d` = -16.3 / -2.6 / -9.9 ms at 250 / 50 / 1000 ms, probe stalls 610/698 vs app 490/440 | Pixel 11 Pro, Android 17; 2026-11-07 builds in `docs/runs/roundtrip/README.md` | `wmr-companion` `docs/runs/sheets/latency-2.md` (rule); `docs/runs/roundtrip/README.md` (figures); ONY-1010 (amendment) |
| FF13 calibration applied in fusion | `partial`, pose recordings: 6.6 s face-down rest LEFT `+0.00°`, RIGHT `−0.03°`; a 14.4 s hand-held take turned RIGHT `−25.3°`. Prose rest pair 83.5 / 42.7 unlabelled for off/on, no 90° error | Pixel 11 Pro, Android 17; bridge 0.2.4 / `41a9e5f`, app 0.2.1 | `wmr-companion` `docs/PROTOCOL.md` "FF13 config block in the app"; the removed internal-ESP32 bench sheet (ONY-1157; see git history) |

ONY-23 closed on 2026-11-07. The 2026-10-28 pair gave `d` = +3.7 ms (one
unpaired run per client) and the 2026-11-07 session gave `d` = -16.3 ms at
250 ms, -2.6 ms at 50 ms and -9.9 ms at 1000 ms, with probe stalls 610/698
against app 490/440. The 250 ms pair is app 11:43 against a probe re-run at
15:18 (app RSSI not recorded), and the 50 and 1000 ms runs received no stream
frames. Under the ONY-1010 amendment (warm/stable start; one-sided
`d <= +5 ms`, stalls within 25 %) the 250 ms pair misses the Stay branch on
RIGHT stalls (440 vs 698, 37 % apart) and lands in between: **Stay Flutter**,
with the `universal_ble` gaps filed upstream. The Dart client is not the
bottleneck. The native probe is retired; the rule and protocol remain in
`wmr-companion/docs/runs/sheets/latency-2.md` and the 2026-11-07 figures are
archived in `docs/runs/roundtrip/README.md`.

## Contention-2 Empirical Hardware Findings (Issue #18)

Full run sheet and figures:
[`docs/CONTENTION_LEVERS.md`](CONTENTION_LEVERS.md).

### 1. Classic ACL Master Role Confirmed (Variant V3 No-Op)

- Instrumented with PR #28 per-link role logging (`HCI_EVENT_ROLE_CHANGE` and
  `HCI_EVENT_CONNECTION_COMPLETE`).
- Result: both controller links consistently establish and operate as master
  (`status=0x00`).
- Conclusion: ESP32 is already master. Report loss is not caused by slave-role
  scheduling. Variant V3 (`WMR_CFG_CLASSIC_FORCE_MASTER`) is a no-op and
  discarded.

### 2. Sniff Mode Variant (Variant V2)

- Tested with `WMR_CFG_CLASSIC_NO_SNIFF=1` (PR #27, #29, #30).
- Confirmed via serial link policy read-back: `status=0x00`, `settings=0x0001`
  (`sniff=clear`, `role_switch=on`).
- 120-second bench run (FF14 batched, MTU 517, high priority):
  - LEFT tick loss: 17.55 %
  - RIGHT tick loss: 23.19 %
- Conclusion: loss remains within the baseline spread (LEFT 14–20 %, RIGHT
  19–26 %). Disabling sniff mode has no measurable effect on report loss in
  this single 120 s run. It is not ruled out definitively.

### 3. RTT Delay Bound (Stream Congestion Ruled Out)

- Tested with latency screen stream selector (`stream: none` vs `stream: batched`,
  60 s each, 250 ms ping interval; app side tracked in companion `docs/STATUS.md`).
- Result: median RTT was about 59 ms with no stream vs about 41 ms with active
  stream. ESP32 turnaround was below 0.5 ms (336 µs and 472 µs).
- Conclusion: the about 40 ms RTT is bound by Android GATT write-request and
  BLE connection interval round trips, not stream congestion or bridge queue
  delay.
- Freshness: the about 40 ms figure reproduced on the current release pair. Four
  120 s runs with both controllers on 2026-10-24 (WMR_Bridge `v0.1.1`,
  wmr-companion `0.1.0`) measured rtt p50 40.5 ms FF14, 44.8 ms FF11, 44.3 ms
  FF12 and 61.6 ms with no stream, with 479 of 479 pings answered in each and
  p99 102.5–124.7 ms ([`docs/runs/roundtrip/`](runs/roundtrip/README.md)). The
  client refactor after 2026-10-22 (latency modes, `wmr_core` extraction,
  multi-bridge, export v3) did not move the streamed median, and the no-stream
  penalty reproduced (about 59 ms then, 61.6 ms now), which fits the
  slave-latency explanation. That is one pass of the ladder: the none and FF14
  repeats are not run. Latency-2 closed ONY-23 as **Stay Flutter** under the
  ONY-1010 amendment (warm/stable start; one-sided `d <= +5 ms`, stalls within
  25 %); the 2026-11-07 pair lands in between (stay Flutter, gaps filed
  upstream), so the Dart client is not the bottleneck and the split lies in the
  plugin and radio. See
  [`docs/PREDICTION.md`](PREDICTION.md) section 5.

### 4. Next Step: Multi-Bridge Architecture & Pi Wi-Fi Host Bridge

Single-controller runs showed near-zero loss on LEFT (about 0.5 %) and baseline
on RIGHT (about 10.8 %), while both controllers together suffer about 18–25 %
loss (user-reported).

However:

- The no-sniff result is one 120 s run (LEFT 17.55 %, RIGHT 23.19 %), inside
  the baseline spread. No effect shown, not "ruled out definitively".
- Radio contention is the working hypothesis, not proven. The master-role and
  sniff variants are no-ops/null results. That narrows candidates, it does not
  confirm the cause.
- Multi-bridge cannot deliver zero loss. RIGHT alone was about 10.8 % with one
  controller and one phone (user-reported). The best it can do on today's
  evidence is LEFT about 0.5 % / RIGHT about 10.8 %.
- No two-bridge run has happened. The sitting procedure is in the removed
  multi-bridge sheet (ONY-1157; see git history). LEFT at
  most 3 % on its own bridge is the pass bar.
- No Raspberry Pi Wi-Fi bridge hardware run has happened. The second supported
  target (`pi/`, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md)) moves the phone link off
  2.4 GHz BLE onto 5 GHz Wi-Fi WebSocket. It is host-tests-only until Sitting P
  in `docs/PI_BRIDGE.md` runs.
  `pi/tools/wifi_probe.py` sends a UDP discovery probe, prints the `hello`
  `uplink` object, writes `--json-out`, and enforces the Sitting P criteria with
  `--assert-gates`; that is host-tested tooling, not a hardware result.

## 5. Single-ESP32 Contention Levers (ONY-176)

Follow-up to ONY-170. Run sheet:
`the removed single-ESP32 levers sheet (ONY-1157; see git history)`.
No lever here has a new hardware result. This table is the run-ready state, not
evidence.

| Lever | Status | Result / prerequisite |
|---|---|---|
| V1 staggered connect (`WMR_CFG_STAGGER_FIRST_HAND=1/2`) | Not run | Code on `main`. Needs the two V1 images and the A/B/A block at one fixed position |
| RIGHT asymmetry, phases 1-2 | Not run | No reflash. Power one controller at a time (see the removed single-ESP32 levers sheet, ONY-1157) |
| DLE confirmation, current build | Not run on the current build; 2026-10-22 log shows 251/2120 accepted | `tx 251 octets / 2120 us, rx 251 octets / 2120 us`. Capture the line again on this build |
| Longer BLE interval (30 ms request) | Not run | Existing `WMR_CFG_CONN_INTERVAL_MIN/MAX`. Phone must grant it and the granted value must be recorded |
| Longer FF14 hold (2 intervals) | Not run | New `WMR_CFG_BATCH_HOLD_INTERVALS=2`. Default 1 leaves current behaviour unchanged |
| Controller task core/priority, HCI buffers | Ruled out by diagnostics | Archived runs: `vhci_drop=0`, `log_errors=0`, queue high-water 41/256 (sample `q=2/256 high=41`), `tx` trailing `rx` only by queued records. No run unless a future window shows a nonzero drop/error counter |

Best measured configuration is unchanged by ONY-176: the default both-hands FF14
stream at MTU 517 with the granted 15 ms interval and a one-interval batch hold.
Its measured dual-hand loss is 18–25 % per hand
([`docs/PREDICTION.md`](PREDICTION.md) section 1), and no tested single-ESP32
variant has beaten it. Next architecture step: one bridge per hand (multi-bridge)
and/or the Pi Wi-Fi host bridge, to remove the BLE/phone link from the ESP32
radio. Neither has run.
