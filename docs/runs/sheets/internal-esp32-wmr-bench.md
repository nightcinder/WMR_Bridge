# Internal bench runbook: ESP32 + WMR controllers + Android phone

**Status: runbook, not a result.** This is the owner's own bench sitting, not a
tester hand-off. It covers what is still open after the 0.2.4 evidence: the
ONY-23 app-vs-probe latency decision, the phone/protocol checks carried since
ONY-439, the ONY-446 RIGHT reconnect, the ONY-441 HID gamepad, the GPIO 4 LED,
and ONY-176 Sitting R (RIGHT asymmetry, phases 1–2, no reflash).

Nothing here is a hardware result until the figures are pasted back. A check
with no figures is `not run`.

Plan: ONY-926. Runbook owner: Nick. Firmware: **WMR_Bridge v0.3.5**. Phone
apps: **Companion 0.3.18** and **GATT probe 0.3.18**.

---

## 0. What this sitting settles

| Block | Check | Gate / decision | Time |
|---|---|---|---|
| **A** | ONY-23 latency-2: app vs native GATT probe | Milestone 5: Stay Flutter / move the receive hot path native / in between. Needs three interleaved blocks in **two sessions** | 75–90 min per session |
| **B** | FF16 live read, FF17 `DebugScreen`, stick calibration | 0.3.5 protocol and app controls | 30–40 min |
| **C** | ONY-446 RIGHT reconnect after discovery | Packaged 0.3.5 fixes the 0.2.4 RIGHT stall | 30 min |
| **D** | ONY-441 HID gamepad | 0.3.5 HID recheck; the only pass so far is on `0.2.4-38` | 20 min |
| **E** | GPIO 4 status LED | red / yellow / blue / green mapping | 5 min |
| **F** | ONY-176 Sitting R, phases 1–2 | Does RIGHT's ~10.8 % alone-loss follow the controller, the hand slot, or the position? | 45–60 min |

**Recommended order:** install and confirm versions, then **B → E → C → D**
on one boot; do **F** next on a fresh boot with one controller powered; do
**A** last, in its own taped-position session, then repeat **A** on another
power-up (ideally another day). A, C, D and F all move or power-cycle the radio,
so do not interleave them with a latency block.

Session 1 (functional) is about 1.5–2 h. The two latency sessions are about
75–90 min each. Two sessions are mandatory for a latency verdict.

---

## 1. Install

### 1.1 Firmware v0.3.5

Use the browser installer or the release image, not a local build:

- Installer: <https://nightcinder.github.io/WMR_Bridge/>
- Release: <https://github.com/nightcinder/WMR_Bridge/releases/tag/v0.3.5>

1. Open the installer in desktop Chrome or Edge with a USB **data** cable.
2. Choose **Both hands** and install `v0.3.5`. If the board will not enter the
   bootloader, hold **BOOT** while the installer retries.
3. Open the serial monitor and confirm before touching the controllers:
   - boot banner prints `WMR Bridge firmware 0.3.5 ...` and
     `serving LEFT and RIGHT`;
   - `LED: WS2812 on GPIO 4 27` (the prebuilt image drives both pins);
   - console `v` prints `0.3.5`.
4. Connect the app and confirm the Debug screen shows `firmware 0.3.5` and
   `FF11+ FF12+ FF13+ FF14+ FF15+ FF16+ FF17+`.

A full factory flash erases the stored controller addresses and the bridge's
phone bond. Pair both controllers again. If the phone still holds an old
`WMR Bridge` bond from a 0.2.x build, forget that entry once in Android
Bluetooth settings before pairing.

If the board already reports `0.3.5` (console `v` and FF16 agree), do not
reflash just for this sitting; record both reads in §2.

### 1.2 Phone APKs

Both artifacts are the `wmr-companion` **v0.3.18** release assets. The release
is cut by ONY-932; the probe APK is due in that release, not in an older one.

- Companion (`Bridge`) app, Android 14+, arm64-v8a:
  <https://github.com/nightcinder/wmr-companion/releases/download/v0.3.18/wmr-companion-0.3.18-unified-arm64-v8a.apk>
- Native GATT probe:
  <https://github.com/nightcinder/wmr-companion/releases/download/v0.3.18/wmr-gatt-probe-0.3.18-debug.apk>

Release page: <https://github.com/nightcinder/wmr-companion/releases/tag/v0.3.18>

The release is cut by ONY-932. If the page or either asset is missing, stop and
report it there; do not substitute a CI artifact or an older APK. Install both
while the phone is on Wi-Fi:

```bash
adb devices                       # the phone must appear as "device", not "unauthorized"
adb install -r wmr-companion-0.3.18-unified-arm64-v8a.apk
adb install -r wmr-gatt-probe-0.3.18-debug.apk
```

If the probe install stops with **"App not installed"** / the generic
**"Something went wrong"** dialog, or `adb` says
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the phone already holds a probe signed
with a different debug key. The `v0.3.18` probe is debug-signed by the CI
runner, and every runner mints a new key, so an earlier probe from a CI
artifact, a local build or ONY-195/467 cannot be updated in place. Remove it
once, then install:

```bash
adb uninstall com.onyxlens.wmr.gattprobe   # ignore "Unknown package" on a fresh phone
adb install wmr-gatt-probe-0.3.18-debug.apk
adb shell pm list packages | grep com.onyxlens.wmr.gattprobe
```

`adb push` only copies the APK to the device; it does not install it. Do not
push to `/sdcard` and tap the file as a workaround — use `adb install`. From
0.3.19 the probe is signed with the project release key, so later probes update
in place; installing the first release-signed probe over the debug-signed
`0.3.18` build also needs the one-time `adb uninstall`.

The probe is the bare-Kotlin client: no Flutter, no plugin, no platform channel.

### 1.3 Record the build header once

Paste this at the top of the result block; every block inherits it.

| Field | Where from | Expected |
|---|---|---|
| Bridge firmware + hash | Console `v`; `git describe --tags --always` of the flashed commit; release `build-info.json` `version` | `0.3.5` |
| `bridge_hands=` | `STREAM` line (console `s` or the periodic stats) | `0` (both hands) unless the block says otherwise |
| App version + commit | Debug screen → About | `0.3.18` |
| Probe version + commit | APK filename / build output | `0.3.18` |
| Phone model + Android | Settings → About phone | record |
| LED wiring | Boot line + physical inspection | GPIO 4, or `declined` if no LED |

---

## 2. Common setup and recording rules

These apply to every block unless the block says otherwise.

- **One phone client at a time.** The firmware serves one central (`phone=1`).
  Force-stop the Companion before running the probe, and disconnect the probe
  before opening the Companion.
- **The gamepad link hides the probe.** The bridge stops advertising while the
  phone holds its BLE HID gamepad link, so a scan cannot see it even though the
  link is up. Probes before ONY-973 (including the `0.3.18` APK pinned in §1.2)
  only scan: remove the `WMR Bridge` bond in Android Bluetooth settings
  (Settings → Bluetooth → `WMR Bridge` → Forget) before the probe block. That
  drops the HID link and the bridge advertises again; the probe pairs on
  connect. Pair the gamepad again after the probe block. Probes from `main`
  after ONY-973 also fall back to the bonded Bridge, so the gamepad can stay
  connected.
- **Fixed position.** Mark the bridge, phone stand and controller spots with
  tape before the first timed run. Do not move them inside a block. A photo at
  the start and end is the position record.
- **Stream and settle.** Select the stream before Start. After connecting or
  changing stream, wait **30 s** and confirm the bridge's `STREAM` line before
  timing. Expect `mtu=517`, `phone=1`, and a live `rate_rx` for FF14; a
  no-stream run must show `stream=none` and `sub=0` *during* the run.
- **Record the link.** For every timed run, copy the first and last `STREAM`
  line exactly, including `conn=[…ms lat=… to=…]`. Record RSSI at start and
  end.
- **Invalid runs.** A disconnect, sleeping controller, wrong stream, missing
  `STREAM`, MTU below 517, interval change inside a pair, or a moved rig makes
  the run invalid. Mark it invalid and repeat it; do not silently drop it.
- **Exports.** Keep the JSON and echo CSV from the app and the probe. Name each
  file by run number in the results table. The frame CSV is not needed for the
  decision.

---

## 3. Block A — ONY-23 latency-2 (app vs native GATT probe)

Canonical protocol and empty result tables:
[`wmr-companion/docs/runs/sheets/latency-2.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/latency-2.md).
This block is the short form; the canonical sheet wins if they differ.

**Question:** how much of the ~40–45 ms FF15 round trip and the >50 ms
inter-arrival stalls is Flutter/plugin/Dart, and how much is the radio and the
connection parameters?

**Verdict rule, fixed before any data.** Apply it to the **FF14 pair** only:

- **Move the receive hot path native** if `d = app p50 − probe p50 ≥ 10 ms`,
  or if either hand has `p < 0.5 × a` (probe stalls less than half the app's).
- **Stay Flutter** only if `abs(d) ≤ 5 ms` **and** both hands have
  `abs(a − p) ≤ 0.25 × p`, where `a`/`p` are the app/probe median
  `stalls_over_50000us` for the same hand.
- **In between** for everything else, including 5 ms < `d` < 10 ms.
- The no-stream pair is context for RTT and the `lat` hypothesis; it has no
  per-hand stalls by construction and cannot satisfy the Stay branch.
- **One session is not a verdict.** The same outcome must hold in a second
  session on another power-up, with at least three valid runs per cell.

### 3.1 Setup (once per session)

- Phone in a stand, taped, screen up, about **1.0 m** from the bridge.
- Both controllers face down and still on marked spots about **0.5 m** from the
  bridge, connected for the whole session. Wake a sleeping controller with a
  button press without lifting it.
- Bridge orientation fixed; USB cable dressed so it cannot tug the board.
- Same phone, Android version, bridge commit, app APK and probe APK for every
  run in the session.
- Bluetooth scanning off, battery saver off, phone not charging during a run,
  no other BLE app running.
- Fresh connection per run; one client connected at a time.

### 3.2 Main blocks — 12 runs (9 with the Companion `none` runs skipped)

Each run is **120 s** at **250 ms** ping interval. Do not run all app runs and
then all probe runs; follow this order.

**Skip the Companion `none` runs.** The app's `none` latency mode is known
broken (ONY-980): runs 1, 6 and 9 do not record properly, so skip them. The
verdict uses the FF14 pair only; the probe `none` runs stay as optional RTT
context.

| Block | # | Client | Stream |
|---|---|---|---|
| 1 | 1 | App | none |
| 1 | 2 | Probe | none |
| 1 | 3 | App | FF14 |
| 1 | 4 | Probe | FF14 |
| 2 | 5 | Probe | none |
| 2 | 6 | App | none |
| 2 | 7 | Probe | FF14 |
| 2 | 8 | App | FF14 |
| 3 | 9 | App | none |
| 3 | 10 | Probe | none |
| 3 | 11 | App | FF14 |
| 3 | 12 | Probe | FF14 |

Block 2 reverses the client order so a slow drift cannot favour one client.
Record for every run: client, stream, valid/invalid, MTU, `conn=[…]`, RSSI
start → end, sent/answered/timeouts, RTT min/p50/p95/p99/max, end-to-end p50,
turnaround p50, per-hand `stalls_over_50000us`, sequence gaps, and the export
filenames.

**RSSI gate:** if start RSSI of the two runs in a pair differs by more than
**3 dB**, discard the pair and repeat it. **Link gate:** a pair is comparable
only at the same MTU and the same `conn` interval/`lat`; if they differ,
discard the pair and record what the phone granted.

### 3.3 Interval pass — 6 runs, FF14 only

Same position, immediately after the three blocks.

| # | Client | Ping interval |
|---|---|---|
| 13 | App | 50 ms |
| 14 | Probe | 50 ms |
| 15 | App | 250 ms |
| 16 | Probe | 250 ms |
| 17 | App | 1000 ms |
| 18 | Probe | 1000 ms |

Purpose: does RTT quantise to the 15 ms connection interval, and does the app
show the same ladder as the probe? Compare cluster spacing, not only p50. At
50 ms the tail is under-sampled (one ping outstanding); at 1000 ms the
percentiles are coarse. That is expected, not a failure.

### 3.4 Session 2

Repeat the same **15 runs** on another power-up (ideally another day), at the
same taped position, with the same bridge/app/probe builds — the 18-run list
minus Companion `none` runs 1, 6 and 9. If the rig is rebuilt,
that is a new position and the sessions cannot be paired. If the two sessions
disagree, the result is **not shown**; repeat rather than choosing a session.

### 3.5 Paste back

- Per run: the probe/app JSON and echo CSV, plus the two `STREAM` lines.
- Per session: app APK commit, probe APK commit, bridge commit and boot banner,
  phone/Android, setup photo, run-number → filename mapping.
- One line for anything that moved, slept, disconnected or was repeated.

---

## 4. Block B — FF16, FF17 `DebugScreen`, stick calibration

### 4.1 FF16 live read

1. Connect the Companion to `WMR Bridge`, open **Debug**, read the firmware
   string and the capability bits.
2. Read the same value over the serial console: send `v`.
3. Disconnect/reconnect and read again with stream `none`, then with `FF14`.

| Step | Pass | Fail |
|---|---|---|
| 1 | `firmware 0.3.5` and `FF11+ FF12+ FF13+ FF14+ FF15+ FF16+ FF17+` | `n/a (no FF16)`, `malformed`, or a different version |
| 2 | One line matching the release `build-info.json` `version` (`0.3.5`), not `1` or empty | `1` or empty |
| 3 | Same string after reconnect, with `none` and `FF14` | String changes or disappears when the stream changes |

Record: app string, bit row, console line, `build-info.json` version, stream used.

### 4.2 FF17 `DebugScreen` controls

The 0.3.18 Debug screen has a **Controller control** panel. Every button maps to
one FF17 opcode; the bridge's result code is shown, never swallowed. Both
controllers awake, stream `FF14`.

`0x02` = CONNECT, `0x03` = DISCONNECT. `0x84` and `0x81` are **ATT application
errors** (`0x80 + result`): `0x84` = NOT_CONNECTED, `0x81` = BUSY. They are the
expected refusals, not separate commands.

| Step | Do | Pass | Fail |
|---|---|---|---|
| 1 | Read the panel with both hands connected | Both hands show connected + `HID ready`, distinct non-zero address tails | A hand shows no address, or no HID descriptor |
| 2 | Tap **Disconnect LEFT** (`0x03`) | LEFT clears, `last_command` `0x03`, `last_result` `0x00`, stream stops for LEFT | No state change or a stale last command |
| 3 | Tap **Connect LEFT** (`0x02`) | LEFT goes connecting, then connected + HID ready; LEFT stream resumes | LEFT stays down or the stream does not resume |
| 4 | Tap **Disconnect** again on the already-disconnected hand | ATT `0x84` NOT_CONNECTED in the panel error line; state unchanged | A success code |
| 5 | With a hand connected, tap **Forget bond** on that hand (`0x05`) | ATT `0x81` BUSY; no bond removed | Bond cleared while connected |
| 6 | Optional but cheap: **Forget stored addresses** (`0x04`) with one hand connected | ATT `0x81` BUSY; no address cleared | Address cleared while connected |

Do **not** run step 5 with the hand disconnected: it clears the link key and
forces a re-pair. If it happens, re-pair that hand before continuing.

Record: the 20-byte status before step 2 and after every write, plus the ATT
status the panel reports.

### 4.3 Stick calibration / dead zone

Spec: companion `docs/PROTOCOL.md` "Stick centre" (`StickNormalizer`). Controllers
face down and still, stream `FF14`, fusion off. This is a raw-stick check.

| Step | Do | Pass | Fail |
|---|---|---|---|
| 1 | Do not touch either stick; watch raw and normalized for 30 s | Raw shows the rest offset (about LEFT `-0.023, 0.009`, RIGHT `-0.112, 0.082` on this unit); normalized stays `0.000` on both axes for the whole 30 s | Normalized drifts or jitters at rest |
| 2 | Turn learning off, set centre to `0,0`, dead zone to `0` | Normalized = raw − centre, so the rest offset appears on both axes | Normalized still reads `0,0` with centre and zone zeroed |
| 3 | Restore learned centre and zone `0.05`; push each stick to its end stop in all four directions | Normalized reaches ±1 at the stop and returns to 0 on release | A direction cannot reach ±1, or does not return |
| 4 | Hold a stick about `0.1` off centre and still for 2 s | Record whether learning leaves the centre alone or adopts the held position; **Re-centre stick** then sets the current reading | Learning adopts the offset with no way to undo it |
| 5 | Turn learning off, release, read the centre, then read/export `stick_settings.json` | Centre stays at the value shown before the switch; file reads `learn false` | Centre keeps changing, or the file still reads `learn true` |

Record: raw and normalized pairs at rest per hand (zone 0.05), the same with
centre `0,0` and zone `0`, the highest normalized value per direction, step 4
outcome, and the `learn`/centre values from the file.

---

## 5. Block C — ONY-446 RIGHT reconnect after discovery

Spec: [`docs/DISCOVERY.md`](../../DISCOVERY.md). The 0.2.4 failure: RIGHT's ACL
came up about 2 s after `discovery finished`, nothing encrypted it, and neither
hand produced a report for 18 s until reset. 0.3.5 encrypts that ACL after
750 ms and drops it after 4 s without a HID channel so the controller pages
again.

Setup: both hands paired and stored, bridge booted, serial monitor open, stream
`FF14`.

| Step | Do | Pass | Fail |
|---|---|---|---|
| 1 | Disconnect both hands. Send `0x04` (FORGET_ADDRS) and confirm both `addr_source` read `0`. With LEFT asleep, run discovery (`d`) so RIGHT re-pairs and streams; leave LEFT unpaired | Both disconnect, `0x04` returns OK, both sources `0`, RIGHT alone re-pairs and streams | A hand stays connected, `0x04` refused, or RIGHT does not re-pair |
| 2 | Reboot the bridge with RIGHT awake and LEFT in pairing mode; wait for `discovery finished` and LEFT to pair | RIGHT's ACL encrypts within about 750 ms (`RIGHT link encrypted` / `HCI_Set_Connection_Encryption`), `incoming HID connection` within 4 s, RIGHT reports resume; LEFT also streams | No encryption/HID line for 18 s, RIGHT never resumes, or a reset is needed |
| 3 | Power-cycle RIGHT once while both hands stream | A neutral report, then RIGHT encrypts and streams again within about 5 s | RIGHT sticks on its last input or needs a reset |

The console `F` command is equivalent to `0x04` (refused while a hand is
connected). Record the monitor lines from RIGHT ACL up to its first report, and
the power-cycle lines.

---

## 6. Block D — ONY-441 HID gamepad

Spec: [`docs/HID_GAMEPAD.md`](../../HID_GAMEPAD.md). The only pass so far is on
the pre-0.3.0 build `0.2.4-38-g4bcf01c`; this block is the v0.3.5 recheck.

Setup: bridge booted and advertising (`adv=on`, no discovery round), both hands
connected, phone ready. If the phone held a 0.2.x bond, forget `WMR Bridge`
once before pairing.

| Step | Do | Pass | Fail |
|---|---|---|---|
| 1 | Pair `WMR Bridge` in Android Bluetooth settings | One entry, gamepad icon, pairing completes; bridge logs `BLE phone connected` and `hid=on` | No entry, phone icon, or a second Classic entry |
| 2 | Open a gamepad tester; press every control on both hands: buttons, d-pad, face buttons, sticks (including click), triggers, menu/home | Every control registers and releases; no stuck input | A control is missing, mapped wrong, or sticks |
| 3 | Stream `FF14` in the Companion with both hands connected and the tester open | Both hands keep streaming; HID continues; `HID reports` rises with `errors=0` (about 68/s) | Stream stalls or HID stops when FF14 starts |
| 4 | Power-cycle one controller while the phone stays connected | Neutral report, then input resumes; no button stays pressed | Last input sticks until reconnect |
| 5 | Reboot the bridge with the phone still bonded | Phone reconnects to the bond without re-pairing; `hid=on` again | Phone asks to pair again, or gets no gamepad |

Do **not** enable the app's own HID output (`BluetoothHidDevice`) while bridge
HOGP is active: registering it disables Android's HID Host service and adds
radio contention. Leave the app's HID output off.

Record: pair result, per-control notes, the status line during step 3, and what
the phone showed after steps 4 and 5.

---

## 7. Block E — GPIO 4 status LED

One WS2812/NeoPixel on **GPIO 4** (the prebuilt image also drives GPIO 27 for an
M5Stack ATOM Lite). **red = none, yellow = LEFT only, blue = RIGHT only,
green = both.** If no LED is wired, record `declined`; do not score it from a
photo of another board.

| Step | Do | Pass | Fail |
|---|---|---|---|
| 1 | Boot with no controller connected | Red | Any other colour, or dark |
| 2 | Connect LEFT only | Yellow | Red, blue or green |
| 3 | Disconnect LEFT, connect RIGHT only | Blue | Red, yellow or green |
| 4 | Connect both | Green | Yellow or blue |
| 5 | Disconnect both | Back to red within about 1 s | Stays green/yellow/blue |

Record: colour at each step, time to change, and the boot line
`LED: WS2812 on GPIO 4 27`.

---

## 8. Block F — ONY-176 Sitting R, phases 1–2 (no reflash)

Canonical sheet: [`right-asymmetry.md`](right-asymmetry.md) §1, §4 and §5.
Run sheet context: [`single-esp32-levers.md`](single-esp32-levers.md) §3.

**Question:** does RIGHT's ~10.8 % loss alone follow the physical controller,
the table position, or the firmware RIGHT hand slot? This block needs no
reflash. Phase 3 (address swap) is **not needed for this sitting**; run it only
if phases 1–2 do not separate the controller from the position — the condition
in [`single-esp32-levers.md`](single-esp32-levers.md) §3 and
[`right-asymmetry.md`](right-asymmetry.md) §5. The V1 stagger, 30 ms interval
and two-interval FF14 hold were tested by the owner (ONY-980) and are **out of
scope** here.

### 8.1 Isolation rule

Only one controller connected at a time. The other has no battery or is powered
off, and its link must read `n/a (no ACL link)` with `rate_rx=0.0/s`. Measure in
radio isolation, never against the dual-link contention floor.

Phone: Companion or nRF Connect, connect to `WMR Bridge`, request MTU **517**,
connection priority **High**, stream `FF14` only. Wait **30 s** after connect,
then record a continuous **5-minute window** (60 `LINKLOG` rows at 5 s).
Reset the ESP32 before each run so uptime starts at zero.

Go/no-go before every timed window: `phone=1 sub=1 stream=batched mtu=517`;
tested hand `rate_rx` about 170–205/s, `lq ≥ 220`, `rx` advancing; non-tested
hand `n/a (no ACL link)`; interval `15.00 ms`.

### 8.2 Runs

| Phase | Run | Controller / slot | Position |
|---|---|---|---|
| 1 | A1 | Physical LEFT as LEFT | Pos 1 |
| 1 | B1 | Physical RIGHT as RIGHT | Pos 2 |
| 1 | A2 | Physical LEFT as LEFT (drift) | Pos 1 |
| 1 | B2 | Physical RIGHT as RIGHT (drift) | Pos 2 |
| 2 | P1 | Physical LEFT as LEFT | Pos 2 |
| 2 | P2 | Physical RIGHT as RIGHT | Pos 1 |
| 2 | P3 | Repeat / drift | as P2 |

Do not re-pair, reflash or move the bridge/phone between runs. Swapping the
controllers between the two marked spots is the phase-2 variable.

### 8.3 Attribution

| Result | Reading |
|---|---|
| Loss follows physical RIGHT across positions and hand slots | controller hardware / RF front-end |
| Loss follows Position 2 for either controller | table geometry / antenna orientation |
| Loss follows the RIGHT hand slot | firmware RIGHT path |
| A1/A2 or B1/B2 span > 3 points | session drifted; discard |

Record per run: battery raw start/end, temperature raw start/end, ambient °C,
the `STREAM` line, active `link:` line, non-tested hand `no ACL link`, and the
mean `last5s_pct` over the 60 `LINKLOG` rows. Also capture the current build's
DLE line (`BLE data length changed: tx … octets / … us, rx …`) for the ONY-176
table; 2026-10-22 showed 251 octets / 2120 µs, but that line has not been
recorded on the current build.

---

## 9. Results

One row per check. Fill only from §10 figures; `not run` is the honest default.

| # | Block | Check | Result | Build | Figures |
|---|---|---|---|---|---|
| 1 | A | Latency-2 session 1 | not run | | §3.5 |
| 2 | A | Latency-2 session 2 | not run | | §3.5 |
| 3 | A | Latency-2 verdict (after both sessions) | not run | | §3 |
| 4 | B | FF16 live read | not run | | §4.1 |
| 5 | B | FF17 `DebugScreen` (`0x02`/`0x03`/`0x84`/`0x81`) | not run | | §4.2 |
| 6 | B | Stick calibration / dead zone | not run | | §4.3 |
| 7 | C | ONY-446 RIGHT reconnect | not run | | §5 |
| 8 | D | ONY-441 HID gamepad | not run | | §6 |
| 9 | E | GPIO 4 status LED | not run | | §7 |
| 10 | F | ONY-176 Sitting R phases 1–2 | not run | | §8 |

## 10. Paste-back block

```text
Build: firmware 0.3.5 / <hash> / build-info version <...>;
       app 0.3.18 / <commit>; probe 0.3.18 / <commit>
Phone: <model>, Android <version>; STREAM bridge_hands=<0|1>; position <distance/photo>

A latency-2 session <1|2>: position=<...> photo=<...>
  run 1 App none:  mtu=  conn=  rssi=  p50=  stalls L/R=  file=
  run 2 Probe none: ...
  ... 15 rows (App `none` rows marked skipped) ...
  session outcome: <Stay / Move / In between / not shown>

B1 FF16: app="<...>" bits="<...>" console v="<...>" reconnect="<...>"  -> PASS/FAIL
B2 FF17: status before=<hex> after=<hex>; ATT 0x03/0x02/0x84/0x81=<...>  -> PASS/FAIL
B3 Stick: raw L=(x,y) R=(x,y); normalized rest L=(x,y) R=(x,y);
          max per direction=<...>; learn=<true|false>; centre L/R=<...>  -> PASS/FAIL

C ONY-446: RIGHT encrypted=<yes|no> hid=<yes|no> reports=<yes|no> reset=<no|yes>;
           power-cycle=<...>  -> PASS/FAIL
D HID: pair=<...> controls=<...> FF14=<...> power-cycle=<...> reboot=<...>  -> PASS/FAIL
E LED: none=<...> left=<...> right=<...> both=<...> back to none=<...>  -> PASS/FAIL
F Sitting R: A1/B1/A2/B2/P1/P2/P3 last5s means=<...>;
             attribution=<controller|position|hand slot|inconclusive>  -> PASS/FAIL
DLE line: <...>

Defects filed: <issue ids or none>
```

A check with no figures is `not run`, even if it "seemed fine".

---

## 11. What this runbook deliberately does not cover

- **Phase 3 of Sitting R** (physical RIGHT in the LEFT hand slot) is not needed
  for this sitting; it is the conditional hand-slot swap in
  [`right-asymmetry.md`](right-asymmetry.md) §5 and is skipped unless phases 1–2
  do not separate the controller from the position.
- **V1 stagger, 30 ms interval request and the two-interval FF14 hold** were
  tested by the owner (report on ONY-980, 2026-11-07) and are not re-benched
  here; see [`single-esp32-levers.md`](single-esp32-levers.md) §2 and §4 for the
  lever definitions. The lever sheet §6, `STATUS.md` and `PREDICTION.md` still
  list them as not run and carry no figures, so this sheet makes no measurement
  claim for them.
- **The Pi Wi-Fi bridge** (Sitting P) needs a Raspberry Pi and is a separate
  sheet.
- **PSVR2 and glasses runtimes** are external-tester coverage; this sitting is
  phone + ESP32 + WMR controllers only.
