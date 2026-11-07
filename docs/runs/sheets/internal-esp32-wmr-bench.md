# Internal bench runbook: ESP32 + WMR controllers + Android phone

Status: runbook, not a result. A check is `not run` until its figures are pasted back. This sheet replaces `ony-439-android-verification.md`.

Firmware **v0.3.11**; apps **Companion 0.3.18** and **GATT probe 0.3.18**. Plan ONY-926.

## 0. Blocks

| Block | Check | Decision | Time |
|---|---|---|---|
| A | ONY-23 latency-2: app vs native probe | Milestone 5: Stay Flutter / move native / in between; two sessions | 75-90 min each |
| B | FF16, FF17 DebugScreen, stick | 0.3.11 protocol and app controls | 30-40 min |
| C | ONY-446 RIGHT reconnect | 0.3.11 fixes the 0.2.4 stall | 30 min |
| D | ONY-441 HID gamepad | 0.3.11 recheck | 20 min |
| E | GPIO 4 status LED | red / yellow / blue / green | 5 min |
| F | Sitting R phases 1-2 | Does RIGHT's ~10.8 % follow controller, slot or position? | 45-60 min |

Order: install, then **B -> E -> C -> D** on one boot. Do **F** next on a fresh boot with one controller powered. Do **A** last in its own taped session, then repeat on another power-up. Do not interleave A with radio moves.

## 1. Install and record
1. Install the **Both hands** `v0.3.11` image from the [installer](https://nightcinder.github.io/WMR_Bridge/) or the release.
2. Confirm the boot banner, console `v` and Debug screen all say `0.3.11`, with all capability bits. If so, do not reflash.
3. Install both APKs from the `wmr-companion` `v0.3.18` release. If the probe will not update, uninstall `com.onyxlens.wmr.gattprobe` once, then install.
4. Record: firmware hash, `bridge_hands=`, app/probe versions, phone and Android, LED wiring (GPIO 4 or `declined`).

A factory flash erases controller addresses and the phone bond. Pair both controllers again; forget an old 0.2.x `WMR Bridge` bond in Android first.

## 2. Common rules
- One phone client at a time (`phone=1`): force-stop the Companion before the probe, and disconnect the probe before the Companion.
- Probes before ONY-973 only scan. While the phone holds the HID gamepad link the bridge stops advertising: remove the `WMR Bridge` bond before the probe, then pair the gamepad again.
- Tape the bridge, phone and controller spots; do not move them inside a block.
- Select the stream, wait **30 s**, confirm `mtu=517`, `phone=1` and live `rate_rx`; a no-stream run shows `stream=none sub=0`. Copy the first and last `STREAM` line per run, including `conn=[...]`, plus RSSI.
- A disconnect, sleeping controller, wrong stream, missing `STREAM`, MTU below 517, interval change or moved rig invalidates the run. Mark it and repeat.

## 3. Block A — ONY-23 latency-2

Canonical protocol: [latency-2](https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/latency-2.md); it wins if they differ. Question: how much of the ~40-45 ms FF15 round trip and more than 50 ms stalls is Flutter/Dart, and how much is radio?

**Verdict, FF14 pair only:**
- Move the receive path native if `app p50 - probe p50 >= 10 ms`, or probe stalls `< 0.5 x` the app's on either hand.
- Stay Flutter only if `abs(d) <= 5 ms` and both hands have `abs(a - p) <= 0.25 x p` (median `stalls_over_50000us`).
- Otherwise "in between". One session is not a verdict.

Setup: phone taped about 1.0 m from the bridge; controllers face down and still about 0.5 m away; fixed board orientation; same phone and builds; battery saver and scanning off; fresh connection per run.

Each run is **120 s** at **250 ms**, in this order. **Skip the Companion `none` runs** (1, 6, 9): that app mode is known broken (ONY-980). The verdict uses the FF14 pair only; probe `none` runs are optional RTT context.

| Block | Runs (client, stream) |
|---|---|
| 1 | ~~App none~~, Probe none, App FF14, Probe FF14 |
| 2 | Probe none, ~~App none~~, Probe FF14, App FF14 |
| 3 | ~~App none~~, Probe none, App FF14, Probe FF14 |

Record client, stream, validity, MTU, `conn`, RSSI, sent/answered/timeouts, RTT min/p50/p95/p99/max, turnaround p50, per-hand stalls, gaps and file names. Discard a pair if RSSI differs by more than 3 dB or link parameters differ.

Interval pass, FF14: App/Probe at **50 ms**, then **250 ms**, then **1000 ms** (6 runs). Compare cluster spacing, not only p50. Repeat the same **15 runs** in a second session; if they disagree, the result is `not shown`.

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

## 8. Results and paste-back

| # | Block | Check | Result |
|---|---|---|---|
| 1-3 | A | Latency sessions 1, 2, verdict | not run |
| 4-6 | B | FF16, FF17, stick | not run |
| 7-9 | C-E | ONY-446, HID, LED | not run |
| 10-11 | F | Sitting R phases 1-2, phase 3 | not run |

Paste: build header; per-run exports and `STREAM`; A verdict; B status/ATT; C monitor lines; D control notes; E colours; F/F2 means and attribution; defects filed. A check with no figures is `not run`.

## 9. Not covered

V1/interval/hold: owner-reported tested on ONY-980; definitions in [`single-esp32-levers.md`](single-esp32-levers.md). Two bridges: [`multi-bridge.md`](multi-bridge.md). Pi: [`pi-bridge.md`](pi-bridge.md). PSVR2/glasses: [`external-test-user.md`](external-test-user.md).
