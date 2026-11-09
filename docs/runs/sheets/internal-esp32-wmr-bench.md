# Internal bench runbook: ESP32 + WMR controllers + Android phone

Status: runbook, not a result. A check is `not run` until its figures are pasted back. This sheet replaces `ony-439-android-verification.md`.

Firmware **v0.3.11**; app **Companion 0.3.19+24** (or later). Plan ONY-926.

## 0. Blocks

| Block | Check | Decision | Time |
|---|---|---|---|
| B | FF16, FF17 DebugScreen, stick | 0.3.11 protocol and app controls | 30-40 min |
| C | ONY-446 RIGHT reconnect | 0.3.11 fixes the 0.2.4 stall | 30 min |
| D | ONY-441 HID gamepad | 0.3.11 recheck | 20 min |
| E | GPIO 4 status LED | red / yellow / blue / green | 5 min |
| F | Sitting R phases 1-2 | Does RIGHT's ~10.8 % follow controller, slot or position? | 45-60 min |
| G | ONY-1058 / ONY-1064 idle sleep | PR #191 5 min sleep, 15 min power-off, 60 s cooldown | 25 min |
| H | ONY-1070 BLE streaming | Pixel 11 Pro Companion app direct BLE streaming and link recovery | 15-20 min |

Order: install, then **B -> E -> C -> D -> G -> H** on one boot. Do **F** next on a fresh boot with one controller powered.

## 1. Install and record
1. Install the **Both hands** `v0.3.11` image from the [installer](https://nightcinder.github.io/WMR_Bridge/) or the release.
2. Confirm the boot banner, console `v` and Debug screen all say `0.3.11`, with all capability bits. If so, do not reflash.
3. Install the Companion APK from the `wmr-companion` `v0.3.19+24` release (or later).
4. Record: firmware hash, `bridge_hands=`, app version, phone and Android, LED wiring (GPIO 4 or `declined`).

A factory flash erases controller addresses and the phone bond. Pair both controllers again; forget an old 0.2.x `WMR Bridge` bond in Android first.

## 2. Common rules
- Tape the bridge, phone and controller spots; do not move them inside a block.
- Select the stream, wait **30 s**, confirm `mtu=517`, `phone=1` and live `rate_rx`; a no-stream run shows `stream=none sub=0`. Copy the first and last `STREAM` line per run, including `conn=[...]`, plus RSSI.
- A disconnect, sleeping controller, wrong stream, missing `STREAM`, MTU below 517, interval change or moved rig invalidates the run. Mark it and repeat.

## 3. Block A — ONY-23 latency-2 (closed)

**Closed — Stay Flutter** (ONY-23). ONY-1010 amended the rule: the test
condition is a normal warm/stable power state, and the Stay Flutter bound is
one-sided `d = app p50 - probe p50 <= +5 ms`; the >50 ms stall count must still
be within 25 % of the probe's (`abs(a - p) <= 0.25 x p`) on both hands. Move the
receive path native if `d >= +10 ms` or probe stalls are `< 0.5 x` the app's on
either hand; otherwise it is in between — stay Flutter and file the
`universal_ble` gaps upstream. The pre-amendment
[latency-2](https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/latency-2.md)
rule is two-sided `abs(d) <= 5 ms` with the same stall bound.

Evidence: 2026-10-28 `d` = +3.7 ms, one unpaired run per client, with stalls
inside 25 % on both hands; 2026-11-07 `d` = -16.3 / -2.6 / -9.9 ms at 250 / 50 /
1000 ms, with probe stalls 610/698 against app 490/440. The 250 ms pair is app
11:43 against a probe re-run at 15:18 (app RSSI not recorded), and its RIGHT
stalls are 37 % apart (440 vs 698), so it misses the Stay branch under either
bound and lands in between: Stay Flutter, gaps filed upstream. The 50 and
1000 ms runs received no stream frames. The Dart client is not the bottleneck;
the round trip is radio-bound. The native probe is retired. ONY-1010 records the
amendment; the rule and protocol are in `latency-2`; the 2026-11-07 figures are
archived in [`docs/runs/roundtrip/README.md`](../roundtrip/README.md). Do not run
this block.

## 4. Block B — FF16, FF17, stick

**FF16:** read the app Debug screen and console `v`; repeat after reconnect with
`none` and `FF14`. Pass: `firmware 0.3.11`, `FF11+` through `FF17+`, console matches `build-info.json`, string stable. Fail: `n/a`, `malformed`, different version or stream-dependent string.

**FF17:** both hands awake, FF14. `0x02` CONNECT, `0x03` DISCONNECT, `0x05`
FORGET_BOND, `0x04` FORGET_ADDRS. ATT `0x84` = NOT_CONNECTED, `0x81` = BUSY.

| Step | Do | Pass |
|---|---|---|
| 1 | Read with both connected | connected + HID ready, distinct address tails |
| 2 | Disconnect LEFT | clears, `last_command` 0x03, `last_result` 0x00 |
| 3 | Connect LEFT | connecting, then connected + HID ready; stream resumes |
| 4 | Disconnect the disconnected hand | ATT 0x84, state unchanged |
| 5 | Forget bond on a connected hand | ATT 0x81, bond kept |
| 6 | Forget addresses with one hand connected | ATT 0x81, addresses kept |

Do not run step 5 disconnected: it clears the key and forces a re-pair. Record the 20-byte status before step 2 and after every write, plus ATT status.

**Stick:** both still, FF14, fusion off.

| Step | Do | Pass |
|---|---|---|
| 1 | Touch neither stick 30 s | raw shows rest offset (LEFT ~`-0.023, 0.009`, RIGHT ~`-0.112, 0.082`); normalized 0.000 |
| 2 | Learning off, centre 0,0, dead zone 0 | normalized = raw - centre, offset shows |
| 3 | Restore centre and 0.05; push every end stop | normalized reaches ±1 and returns to 0 |
| 4 | Hold a stick ~0.1 off centre 2 s | record learning; Re-centre works |
| 5 | Learning off, read `stick_settings.json` | centre stays; file says `learn false` |

## 5. Blocks C-E

**C, ONY-446 RIGHT reconnect.** Both hands paired, monitor open, FF14.

| Step | Do | Pass |
|---|---|---|
| 1 | Disconnect both; `0x04`; `addr_source=0`; with LEFT asleep run `d` | RIGHT re-pairs and streams alone |
| 2 | Reboot with RIGHT awake and LEFT pairing | RIGHT encrypts within ~750 ms, HID within 4 s, reports resume; LEFT streams |
| 3 | Power-cycle RIGHT while both stream | neutral report, then streams again within ~5 s |

Console `F` is equivalent to `0x04` and is refused while a hand is connected.

**D, ONY-441 HID.** Advertising only, both hands connected, phone ready. Forget
an old 0.2.x bond first. Leave the app's own HID output off.

| Step | Do | Pass |
|---|---|---|
| 1 | Pair `WMR Bridge` in Android | one gamepad entry; `hid=on` |
| 2 | Press every control in a gamepad tester | all register and release |
| 3 | Stream FF14 with the tester open | both continue; `HID reports` rises, `errors=0` |
| 4 | Power-cycle one controller | neutral report, input resumes, nothing sticks |
| 5 | Reboot the bridge | phone reconnects to the bond; `hid=on` |

**E, LED.** One WS2812 on GPIO 4 (the prebuilt image also drives GPIO 27).
**red = none, yellow = LEFT, blue = RIGHT, green = both.** Pass: red at boot,
correct colour per hand, back to red within ~1 s. If no LED, record `declined`.

## 6. Block F — Sitting R phases 1-2 (no reflash)

Does RIGHT's ~10.8 % alone follow the physical controller, the table position, or the firmware RIGHT slot? Detail also in [`single-esp32-levers.md`](single-esp32-levers.md) §3.

Isolation: one controller connected. The other has no battery or is off; its link reads `n/a (no ACL link)` and `rate_rx=0.0/s`.

Phone: Companion or nRF Connect, MTU **517**, priority **High**, FF14. Reset the ESP32 before each run, wait **30 s**, record a **5-minute window** (60 `LINKLOG` rows). Go/no-go: `phone=1 sub=1 stream=batched mtu=517`; tested hand `rate_rx` 170-205/s, `lq >= 220`; other hand no ACL link; interval 15.00 ms.

| Phase | Runs |
|---|---|
| 1 | A1 LEFT at Pos 1; B1 RIGHT at Pos 2; A2/B2 repeats |
| 2 | P1 LEFT at Pos 2; P2 RIGHT at Pos 1; P3 repeat P2 |

Do not re-pair, reflash or move the bridge/phone between runs. Moving the controllers between marks is the phase-2 variable.

| Result | Reading |
|---|---|
| Loss follows physical RIGHT across positions and slots | controller hardware / RF front-end |
| Loss follows Position 2 for either controller | table geometry / antenna orientation |
| Loss follows the RIGHT slot | firmware RIGHT path |
| A1/A2 or B1/B2 span > 3 points | session drifted; discard |

Record per run: battery and temperature raw start/end, ambient °C, `STREAM`, active `link:`, the other hand's no-ACL line, mean `last5s_pct`, and the current build's DLE line.

## 7. Block F2 — phase 3 hand-slot swap (one reflash)

Only if phases 1-2 do not separate controller from position. Put physical RIGHT (`2C:4D:79:C8:F9:A9`) in the LEFT slot and physical LEFT (`2C:4D:79:C8:C9:CC`) in the RIGHT slot.
1. Power off the controller not under test.
2. Console `L`/`R` to disconnect; `k`/`K` to clear keys; `F` to clear addresses and discover. `F` is refused while a hand is connected.
3. To force the swap, build with the overrides:

   ```powershell
   $env:EXTRA_CFLAGS = '-DWMR_CFG_LEFT_ADDRESS=\"2C:4D:79:C8:F9:A9\" -DWMR_CFG_RIGHT_ADDRESS=\"2C:4D:79:C8:C9:CC\"'
   idf.py -B build-wmr-asym-swap build
   idf.py -B build-wmr-asym-swap -p COM5 flash monitor
   Remove-Item Env:EXTRA_CFLAGS
   ```

   Press `s`; both addresses must say `compile-time override`.
4. Run S1 (RIGHT in LEFT slot, Pos 1), S2 (LEFT in RIGHT slot, Pos 2), then repeats S3/S4, using the phase-1 window and isolation rules.
5. Attribution: loss follows the controller into the LEFT slot -> controller; stays with the RIGHT slot -> firmware path.

Restore the normal both-hands image afterwards.

## 8. Block G: ONY-1058 and ONY-1064 controller idle sleep, power-off, and cooldown (PR #191)

Firmware PR #191 implements two-stage idle power management in `src/wmr_cfgmgr.c` and `src/wmr_controller.c`:
- `WMR_CFG_IDLE_SLEEP_MS` (default 300000 ms, 5 min): pauses the 0x06 keep-alive pair so the controller enters standby. The ACL link remains connected.
- `WMR_CFG_IDLE_POWEROFF_MS` (default 900000 ms, 15 min): drops the Bluetooth ACL link so the controller powers off.
- `WMR_CFG_IDLE_RECONNECT_COOLDOWN_MS` (default 60000 ms, 1 min): declines incoming reconnects to prevent a link bounce while the controller shuts down.

Prerequisites:
- ESP32 flashed with firmware containing PR #191.
- ESP32 connected over USB to monitor serial output:

  ```bash
  idf.py -p COM5 monitor
  ```

  (or `/dev/ttyUSB0` on Linux).
- Both WMR controllers paired and connected.
- Pixel 11 Pro connected to `WMR Bridge` with `wmr_companion_app` and subscribed to FF14 `batched`.

Procedure:

1. Pair and stream both controllers. Keep the serial monitor open.
2. Place both controllers untouched on their fixed desk marks at timestamp `T0`.
3. Wait 300 s (5 minutes) without touching either controller.
4. At `T0 + 300 s` (within one 10 s keep-alive tick, up to `T0 + 310 s`), make sure that the serial monitor prints:
   `FF13 LEFT: idle for <N> ms; keep-alive paused, controller may sleep`
   `FF13 RIGHT: idle for <N> ms; keep-alive paused, controller may sleep`
   where `<N>` is the measured idle time in milliseconds and is at least 300000.
5. Make sure that both controllers enter standby mode (LEDs dim or pulse slowly). The Bluetooth ACL link stays connected. On the Pixel 11 Pro, make sure that `wmr_companion_app` still reports connected.
6. At `T0 + 400 s`, test the activity wake on LEFT: move the thumbstick or press the trigger.
7. Make sure that the serial monitor prints:
   `FF13 LEFT: activity after idle sleep; resuming the keep-alive`
8. Make sure that LEFT resumes full streaming on the Pixel 11 Pro in under 1.0 s without an ACL reconnect.
9. Leave RIGHT untouched until `T0 + 900 s` (15 minutes).
10. At `T0 + 900 s` (up to `T0 + 910 s`), make sure that the serial monitor prints:
    `FF13 RIGHT: idle for <N> ms; disconnecting so the controller powers off`
    `pair: RIGHT idle; disconnecting so it powers off, reconnects declined for 60000 ms`
    where `<N>` is at least 900000.
11. Make sure that the RIGHT controller turns off completely (ring LEDs extinguish).
12. Test the 60-second reconnect cooldown: immediately press the Windows power button on RIGHT within the 60 s window.
13. Make sure that the serial monitor prints:
    `pair: RIGHT reconnected during its idle power-off cooldown; dropping the link`
    or:
    `pair: RIGHT is in its idle power-off cooldown; not reconnecting yet`
    The reconnect must be declined.
14. Wait for the 60 s cooldown to expire. Turn on RIGHT.
15. Make sure that RIGHT reconnects and streams cleanly.

Pass: keep-alive pauses at 300 s to 310 s (allowing up to one 10 s keep-alive tick). Motion wakes the controller in under 1.0 s. Power-off disconnect occurs at 900 s to 910 s. Reconnects during the 60 s cooldown are refused, and reconnect after 60 s succeeds cleanly.

## 9. Block H: ONY-1070 BLE GATT two-controller streaming on Pixel 11 Pro

The ESP32 bridge streams over BLE GATT directly. `wmr_companion_app` (0.3.19+24 or later) on the Pixel 11 Pro connects through its direct Bluetooth transport. This block verifies two-controller BLE GATT streaming and link recovery directly on the Pixel 11 Pro. If you evaluate automated composite TCP-to-BLE failover jitter, refer to `docs/runs/sheets/pi-bridge.md` §P12 with a library host.

Prerequisites:
- ESP32 running both-hands firmware and advertising `WMR Bridge`.
- Both WMR controllers paired and streaming to the ESP32.
- Pixel 11 Pro running `wmr_companion_app` (0.3.19+24 or later).

Procedure:

1. Open `wmr_companion_app` on the Pixel 11 Pro.
2. In the Connect screen, select the Bluetooth transport mode.
3. Tap Scan. Select the BLE device `WMR Bridge`.
4. Tap Connect.
5. Make sure that the status indicator shows connected.
6. Open the Stream panel. Select the `batched` stream.
7. Make sure that both controllers stream simultaneously at 150-180 reports/s per hand.
8. Open the Latency panel. Measure the round-trip time and the frame arrival intervals.
9. Power cycle the bridge or toggle Bluetooth on the phone to test link recovery.
10. Make sure that the stream reconnects within 5 s and sequence numbers continue without error.
11. Tap Export Test Bundle to save the session bundle on the phone.

Pass: clean BLE GATT connection. Two-controller batched streaming at 150-180 reports/s per hand. RTT p50 < 45 ms. Clean stream recovery within 5 s.

## 10. Results and paste-back

| # | Block | Check | Result |
|---|---|---|---|
| 1-3 | B | FF16, FF17, stick | not run |
| 4-6 | C-E | ONY-446, HID, LED | not run |
| 7-8 | F | Sitting R phases 1-2, phase 3 | not run |
| 9 | G | ONY-1058 / ONY-1064 idle sleep, power-off, cooldown | not run |
| 10 | H | ONY-1070 BLE streaming on Pixel 11 Pro | not run |

Paste: build header; per-run exports and `STREAM`; B status/ATT; C monitor lines; D control notes; E colours; F/F2 means and attribution; G monitor timestamps and wake logs; H stream rates and bundle export; defects filed. A check with no figures is `not run`.

### FF13 evidence (0.2.4)

| Recording | Span | Attitude | LEFT | RIGHT |
|---|---|---|---|---|
| `pose-20261028-191217.json` | 6.6 s | both still, face down | `+0.00°` | `−0.03°` (pitch `+88.8°`) |
| `pose-20261028-192352.json` | 14.4 s | RIGHT held at pitch `+24.8°`, turned | `−0.01°` | `−25.33°` (`−1.74 °/s`) |

Both are fused poses (`source=orientationOnly`); only the first is a rest take. Neither is labelled with the FF13 switch state (in-memory only), so no before/after pair and no 90° error. The owner's prose pair, RIGHT `83.5` / `42.7`, has no export or switch label, so neither figure is assigned. Needs two 60 s face-down, untouched takes, switch off then on.

## 11. Not covered

V1/interval/hold: owner-reported tested on ONY-980; definitions in [`single-esp32-levers.md`](single-esp32-levers.md). Two bridges: [`multi-bridge.md`](multi-bridge.md). Pi: [`pi-bridge.md`](pi-bridge.md). PSVR2/glasses: [`external-test-user.md`](external-test-user.md).
