# Run sheet: ONY-439 Android verification campaign

> **Procedure plus partial evidence, not a verdict.** A check scores only from figures the owner
> supplies in §6 or that are recorded in §7; a green CI build is not a result.

Scope: the checks that need only the owner's phone and one ESP32 — no headset, no second bridge,
no iOS device, no Pi. One sitting on the image already flashed (`v0.2.1` or newer, or the CI APK
of the same commit); **no reflash and no firmware build**.

Three checks have no procedure anywhere yet and are specified in full below: FF16 live read (§1),
FF17 scan/connect (§2), stick dead zone (§3). The other three already own a procedure elsewhere
and this sheet only points at them and collects the result: the latency stream ladder and the
app-vs-native probe decision are in `wmr-companion` [`docs/runs/sheets/latency-2.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/latency-2.md),
and FF13 calibration applied in fusion is `wmr-companion` `docs/STATUS.md` (FF13 row) with the
switch described in `wmr-companion` `docs/PROTOCOL.md`.

## 0. Record before touching anything

Paste these once at the top of the result block; every check below inherits them.

| Field | Where from |
|---|---|
| Bridge firmware version and hash | Console `v` and `git describe --tags --always` of the flashed commit; `build-info.json` `version` if a packaged image was flashed |
| `bridge_hands=` boot banner | Serial monitor first line |
| App version and commit | Debug screen "About", or `git -C wmr-companion log --oneline -1` |
| Phone model and Android version | Settings → About phone |
| Stream mode in use | App stream selector (`none`, `FF11`, `FF12`, `FF14`) |

If the bridge reports a version the app cannot parse (FF16 empty or `1`), stop: the board is not
running a packaged build and the FF16 check cannot pass on it.

## 1. FF16 live read (phone only, no stream needed)

Spec: `docs/PROTOCOL.md` "Firmware version, FF16" — read-only UTF-8, no terminator, at most
31 bytes, independent of the FF11/FF12/FF14 stream exclusion.

1. Connect the app to `WMR Bridge` and open the Debug screen. Read the firmware version the app
   shows and whether it marks FF16 as present.
2. Read the same version over the console: send `v` on the serial monitor.
3. Read the same value a second time after a disconnect/reconnect, to show it is stable and does
   not depend on the stream being active.

| Step | Pass | Fail |
|---|---|---|
| 1 | App shows the same string the console prints, and enables the FF16 feature | App shows nothing, `unknown`, or a different string |
| 2 | Console `v` prints one line matching `build-info.json` `version` (e.g. `0.2.1`) | `1` or an empty value from an `idf.py` build |
| 3 | Same string after reconnect, with stream `none` and with `FF14` | String changes or disappears when the stream changes |

Paste back: the app's string, the console `v` line, `build-info.json` `version`, and the stream
mode used for step 3.

## 2. FF17 scan and connect (phone only, one ESP32)

Spec: `docs/PROTOCOL.md` "Controller scan and status v2, FF17". Writes are 1–2 bytes
(`opcode`, optional `hand`; `0x00` LEFT, `0x01` RIGHT). Status is a 20-byte payload:
`version`=1, per-hand `flags` (bit 0 connected, bit 1 connecting, bit 2 descriptor received),
`addr_source`, `model`, `addr_tail`, then `discovery_state`, `discovery_round`,
`last_command`, `last_result`.

Do this with both controllers awake. Leave the stream on `FF14` for the whole section: FF17 is
independent of the stream exclusion, and running it under a stream is the interesting case.

| Step | Do | Pass | Fail |
|---|---|---|---|
| 1 | Read FF17 with both hands connected | `version` 1, both `flags` have bit 0 and bit 2 set, `addr_source` 2 (NVS), `addr_tail` non-zero and different per hand | A hand shows no address, or `flags` lacks the descriptor bit |
| 2 | Write `0x03 0x00` (DISCONNECT LEFT) | ATT success, LEFT bit 0 clears, `last_command` 0x03 and `last_result` 0x00 within the same notification | No state change, or a stale `last_command` |
| 3 | Write `0x02 0x00` (CONNECT LEFT) | ATT success, LEFT bit 1 (connecting) then bit 0+2 set, stream resumes for LEFT | Left stays disconnected, or the stream does not resume |
| 4 | Write `0x03 0x00` then `0x03 0x00` again (DISCONNECT twice) | Second write returns ATT error `0x80 + 0x04` (NOT_CONNECTED) and `last_result` 0x04 | Second write reports success |
| 5 | Write `0x01` (DISCOVER) with both addresses already stored (step 1 requires `addr_source` 2 for both) | ATT error `0x80 + 0x03` (NOTHING_TO_DO), `last_command` 0x01 and `last_result` 0x03, `discovery_state` stays 0 | ATT success and a discovery round, or a different error code |
| 6 | Write `0x05 0x01` (FORGET_BOND RIGHT) while RIGHT is connected | ATT error `0x80 + 0x01` (BUSY), no bond removed | Bond cleared while connected |
| 7 | Write an invalid opcode, e.g. `0x7f` | ATT error `0x80 + 0x06` (INVALID_PARAM) | Any success code |

Do **not** run step 6 with a hand disconnected: it clears the link key and forces a re-pair.

Each write is its own value in nRF Connect — there is no multi-command queue, so steps 2–7 are
seven separate writes to FF17, and the app's log shows the ATT status (`0x00` for success) next to
each. Reading the characteristic before and after each write gives the 20-byte status.

Step 5's code is deliberate: `wmr_controller_discover()` returns `WMR_DISCOVERY_NOTHING_TO_DO`
when `known_hands()` reports both addresses stored (`src/wmr_controller.c`), and
`ctrl_write` maps that to `WMR_CTRL_RES_NOTHING_TO_DO` (`src/wmr_transport.c`), i.e. ATT
`0x80 + 0x03` and `last_result` 0x03. A real discovery round only runs with at least one address
missing, so exercise it by disconnecting both hands, forgetting both addresses (`0x04 0x00`,
`0x04 0x01`) and re-pairing. `wmr_controller_forget_addresses()` returns `WMR_FORGET_BUSY`
(ATT `0x81`) while either hand has a cid or is connected (`src/wmr_controller.c`), so send
`0x03 0x00` and `0x03 0x01` first (RIGHT may need a second write) and confirm bit 0 is clear for
both in the status read before the forget writes. That also changes the `addr_source` step 1
reads, so run step 1 before it.

Paste back: the decoded 20-byte status before step 2, after each write, and the ATT status byte
the app reports for steps 2–7; plus the `FF17`/discovery lines from the monitor if it prints them.

## 3. Stick dead zone (phone only)

Spec: `wmr-companion` `docs/PROTOCOL.md` "Stick centre" — `StickNormalizer` subtracts the learned
or set centre, rescales each half-axis so the end stop reaches ±1, clamps, then applies a radial
dead zone (default 0.05, slider 0–0.3) and rescales so the zone edge is 0. The learned centre on
this unit is LEFT `(-0.0227, 0.0090)`, RIGHT `(-0.1119, 0.0819)`, so rest reads near zero on each
axis before the zone is applied at all.

Controllers face down and still, stream `FF14`, fusion off is fine — this is a raw-stick check.

| Step | Do | Pass | Fail |
|---|---|---|---|
| 1 | Do not touch either stick; watch the Debug screen raw and normalized values for 30 s | Raw shows the rest offset (about `-0.023, 0.009` on LEFT and `-0.112, 0.082` on RIGHT on this unit); normalized stays 0.000 on both axes, both hands, for the whole 30 s | Normalized drifts off 0 at rest, or jitters |
| 2 | Switch learning off, zero the centre (set it to `0,0`), and set the dead zone to 0 for this step | Normalized equals raw minus the set centre, so the raw rest offset shows on both axes: about `-0.023, 0.009` on LEFT and `-0.112, 0.082` on RIGHT on this unit | Normalized still reads 0,0 with the centre zeroed and the dead zone at 0 — the offset is being hidden before the zone |
| 3 | Back to the learned centre and 0.05; push each stick to its end stop in all four directions | Normalized reaches ±1 on each axis at the stop, and returns to 0 when released | A direction cannot reach ±1, or does not return to 0 |
| 4 | Hold a stick slightly off centre (about 0.1) and still for 2 s | Learning either leaves the centre alone or adopts the held position — record which; "Re-centre stick" then sets the current reading as centre | Learning adopts the held position with no way to undo it |
| 5 | Switch learning off, release the stick, read the centre, then export or read `stick_settings.json` | Centre stays at the value shown before the switch **and** the saved file reads `learn false` | Centre keeps changing with learning off, or the file still says `learn true` with the switch off |

Paste back: the raw and normalized pairs at rest per hand at dead zone 0.05, then with the centre
zeroed and the dead zone at 0; the highest normalized value reached per direction, whether learning
adopted a held offset in step 4, the dead-zone value used, and the `learn` flag and centre values
from `stick_settings.json` after step 5.

## 4. Checks with a procedure elsewhere

| Check | Procedure | Result lands in |
|---|---|---|
| Latency stream ladder (`none`, `FF11`, `FF12`, `FF14`, repeats) | `wmr-companion` `docs/runs/sheets/latency-2.md` §3–§5 | That sheet §7; `wmr-companion` `docs/STATUS.md` milestone 5 |
| App-vs-native probe decision | Same sheet, decision rule in §"The decision rule" and arithmetic in §6 | Same sheet; milestone 5 verdict |
| FF13 calibration applied in fusion | `wmr-companion` `docs/PROTOCOL.md` "FF13 config block in the app"; per-hand switch, default off | `wmr-companion` `docs/STATUS.md` FF13 row |

Record the same device/build header for these three, and paste the exported JSON/CSV names, so the
ladder and the probe run can be tied to the build hash in §0.

## 5. Results

One row per check, filled in only from evidence the owner supplies — the §6 paste-back block, or
the exports and recordings collected in §7. `not run` is the honest default; a check the owner
declines is recorded as `declined`, not as a pass.

| # | Check | Result | Device | Build | Command | Figures |
|---|---|---|---|---|---|---|
| 1 | FF16 live read | partial — console `v` matches the packaged image; app cannot show it (wmr-companion #107); reconnect half not run | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f | §1 | see §7 |
| 2 | FF17 scan/connect | `partial` — fourth attempt captured the 20-byte status v2 from FF17 notifications: version 1, both hands `addr_source` 2 (NVS) with distinct tails, both `flags` 0 (disconnected), discovery idle / round 4. DISCOVER `0x01` confirmed by `last_command 0x01` / `last_result 0x03` NOTHING_TO_DO; a `0x05` write arrived and was recorded as `last_result 0x06` INVALID_PARAM instead of BUSY/OK. `error 134` is ambiguous (Android `GATT_CMD_STARTED` or ATT `0x86`). Connect/disconnect, the double disconnect and `0x7f` remain unrun; no literal ATT status byte was displayed | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f; app 0.2.1 | §2 | see §7 |
| 3 | Stick dead zone | `partial` — three sittings of centre lines: RIGHT stable at ~`−0.112, +0.085` (a real bias), LEFT moved `−0.030, −0.011` → `−0.008, +0.021` → `+0.017, +0.025`, still learning. Dead zone 0 was set, but saved settings read `learn true` after the switch was reported off (toggle persistence unconfirmed), so no centre-zeroed raw/normalized pair and no end-stop reading | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f; app 0.2.1 | §3 | see §7 |
| 4 | Latency stream ladder | `partial` — one 120 s run per stream, one session: p50 none 63.4 / 62.3, FF11 34.3, FF12 34.5, FF14 39.3 ms; 479/479 answered each. No ladder verdict: `latency-2` §3.3 needs three runs per cell and a second session | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f; app 0.2.1 | latency-2 §3–§5 | see §7 |
| 5 | App-vs-native probe decision | `partial` — app FF14 p50 39.3 ms vs probe 35.6 ms (`d` = +3.7 ms); stalls app 235/317 vs probe 274/365, inside the 25 % Stay window on both hands, but one unpaired run per client, so `latency-2` §6 forbids a verdict | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f; app 0.2.1 | latency-2 §6 | see §7 |
| 6 | FF13 calibration in fusion | partial — two pose recordings, switch state not labelled; prose rest pair 83.5 / 42.7 unlabelled for off/on; no 90° error | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f; app 0.2.1 | companion PROTOCOL | see §7 |

## 6. Paste-back block

```
Build: firmware <version> / <hash> / build-info version <...>; app <version> / <commit>
Phone: <model>, Android <version>; stream <mode>; bridge_hands=<0|1>
1 FF16: app="<...>" console v="<...>" reconnect="<...>"  -> PASS/FAIL
2 FF17: status before=<hex> after 0x03 0x00=<hex> ... att=<0x..> each  -> PASS/FAIL
3 Dead zone: raw L=(x,y) R=(x,y); normalized rest L=(x,y) R=(x,y); max per direction=...  -> PASS/FAIL
4 Ladder: none p50=.. FF11 p50=.. FF12 p50=.. FF14 p50=..; stalls=..; exports=<names>  -> PASS/FAIL
5 Probe: app p50=.. probe p50=.. stalls app=.. probe=..; verdict=<stay|native|upstream>  -> PASS/FAIL
6 FF13 fusion: switch=<on|off>; before/after yaw drift=..; 90 deg error=..  -> PASS/FAIL
Defects filed: <issue ids or none>
```

A check with no figures is `not run`, even if the phone "seemed fine".

## 7. Evidence received (2026-10-28 sitting)

Owner-supplied, not yet a verdict: the rows above stay `partial` until the missing half of each
check arrives. Re-test from the same phone and bridge; no reflash.

### Header (§0, settled)

| Field | Value | Source |
|---|---|---|
| Bridge firmware | `0.2.4`, commit `41a9e5f`, variant `both`, built 2026-10-26 23:38Z | packaged image `build-info.json`, matched against the owner's `0.2.4` + `bridge_hands=0` stream banner |
| Stream banner | `bridge_hands=0`, `stream=batched` (FF14), `mtu=517`, `sub=1` | serial log 19:10:32 |
| App | `0.2.1` (versionCode 3) | `dumpsys package com.onyxlens.wmr_companion` |
| Phone | Pixel 11 Pro (grizzly), Android 17 | `adb devices -l`, `ro.build.version.release` |
| Console `v` | `WMR Bridge firmware 0.2.4 (ESP-IDF v6.1, built Oct 26 2026 23:37:46)` | serial monitor, 20:06:47 |

The `v` line matches the packaged image (`0.2.4`, built 23:38Z) and the boot banner
`serving LEFT and RIGHT ... discovery autostart on` matches `bridge_hands=0`, so the §0 gate is met
and no reflash was needed.

FF13 blocks on the same sitting read to READY: LEFT 7147 bytes `8bc13223`, RIGHT 7148 bytes
`75e8ab85`, `send_errors=0` both hands.

### FF16 (§1) — app gap

The library parses FF16 into `WmrProtocolVersion` and publishes it on the link snapshot
(`packages/wmr_companion/lib/src/companion.dart`), but `apps/wmr_companion_app/lib` never reads
`.protocol` and has no About page. §1 step 1 therefore cannot pass on this build. Filed as
wmr-companion issue #107.

Step 2 passes: console `v` printed `WMR Bridge firmware 0.2.4 (ESP-IDF v6.1, built Oct 26 2026
23:37:46)`, the same version as `build-info.json`, not `1` and not empty. Step 3 (disconnect,
reconnect, read again under `none` and `FF14`) was not run.

### FF17 (§2) — no ATT byte observed; earlier “134 is a pass” withdrawn

nRF Connect on the phone, bridge `0.2.4`/`41a9e5f`. The first three attempts captured only the
monitor lines; the fourth (below) also captured the 20-byte status v2 from FF17 notifications.

The `error 134` / `error 131` values nRF Connect showed cannot be read as ATT bytes. `131` =
`0x83` = `GATT_DB_FULL` is unambiguously client-side, but `134` = `0x86` is **also exactly the ATT
error `ctrl_write` returns for INVALID_PARAM** (`0x80 + 0x06`, `src/wmr_ctrl.c` /
`src/wmr_transport.c`), so 134 on its own cannot say whether a write reached the bridge. The
earlier reading that 134 *was* ATT `0x86` is not proven either; it is ambiguous until the 20-byte
status carries the opcode. Steps 6 and 7 stay **unscored**.

| Step | Write | Expected | Observed | Result |
|---|---|---|---|---|
| 5 | `0x01` DISCOVER, twice | ATT `0x83` NOTHING_TO_DO | console at 20:27:31, 20:27:38 and again at 20:55:14: `LEFT and RIGHT addresses are known; nothing to discover (F forgets the stored addresses)`; fourth attempt also shows `last_command` `0x01` / `last_result` `0x03` in the FF17 notification | pass at firmware level — the `NOTHING_TO_DO` path; ATT byte not captured |
| 7 | `0x7f` invalid opcode | ATT `0x86` INVALID_PARAM | nRF Connect `error 134`; no ATT response was shown and no status read carries `0x7f` | not run — 134 is ambiguous here |
| 2–3 | `0x03 0x00` / `0x03 0x01`, then reconnect | disconnect then connect per hand | at 20:27:03 LEFT `disconnected after 759639 input report(s)` and RIGHT at 20:27:04 `after 531466`, each followed within a second by `incoming HID connection`, descriptor, `ready: HID descriptor received (model WMR)` | behaviour matches, but the status read and ATT bytes are missing, so not scored |
| 6 | `0x05 0x01` FORGET_BOND RIGHT | ATT `0x81` BUSY while RIGHT is connected | fourth attempt: the notification after the `0x05` write reads `last_command` `0x05` / `last_result` `0x06` INVALID_PARAM, so the write reached the bridge but was rejected before the BUSY/OK path | not run — unexpected INVALID_PARAM; see below |
| 1, 4 | status read, disconnect twice | — | fourth attempt: 20-byte status v2 captured from notifications, but both `flags` are 0, so it was taken with both hands disconnected; the double disconnect was never sent | status format and NVS tails pass, connected-state half not run |

Third attempt, 20:55:08–20:55:14 (owner comment, 2026-10-29 00:57): the stream was `none`
(`stream=none`, `rate_tx=0.0/s`, `notify=452526`), not FF14, and nothing in the monitor answered a
write. `0x05 0x01` "did nothing" again after an initial `error 131` `GATT_DB_FULL`; the only new
firmware line is the same DISCOVER `nothing to discover` at 20:55:14. At that point the writes were
assumed to be stopping in the client stack.

Fourth attempt, 21:17–21:20 (screenshot `Screenshot_20261028-212024.png`, attached 2026-10-29
01:21): Bluetooth was cycled, nRF Connect reconnected, and this time FF17 notifications arrived, so
the 20-byte status v2 was captured. Two notifications, decoded against §2:

| # | Payload | Decoded |
|---|---|---|
| 1 | `01 00 02 00 C8 C9 CC 00 00 02 00 C8 F9 A9 00 00 04 01 03 00` | version 1; LEFT flags 0, source 2 NVS, model WMR, tail `C8:C9:CC`; RIGHT flags 0, source 2 NVS, model WMR, tail `C8:F9:A9`; discovery idle, round 4; `last_command` `0x01` DISCOVER, `last_result` `0x03` NOTHING_TO_DO |
| 2 | `01 00 02 00 C8 C9 CC 00 00 02 00 C8 F9 A9 00 00 04 05 06 00` | same hand fields; `last_command` `0x05`, `last_result` `0x06` INVALID_PARAM |

The monitor's `LEFT and RIGHT addresses are known; nothing to discover` line at 21:18:53 is the
same DISCOVER result the first notification records.

So:

- **Step 5 is confirmed by the status, not just the console.** The notification after `0x01` carries
  `last_command` `0x01` / `last_result` `0x03` and `discovery_state` stays 0. The literal ATT byte
  is still not displayed, but the firmware's own result field is captured.
- **The format half of step 1 passes; the connected half does not.** `version` 1, `addr_source` 2
  for both hands, distinct non-zero tails and model WMR all match; both `flags` are 0, so this read
  was taken with both hands disconnected. No status with bit 0 and bit 2 set has been captured.
- **`error 134` was the bridge answering after the `0x05` write.** The notification records
  `last_result` `0x06`, i.e. ATT `0x86` = 134: at least that write did leave the phone, so "the
  client stack is where the writes are stopping" is withdrawn. Whether the earlier `0x7f` write
  also arrived is still unknown.
- **The `0x05` rejection is itself unexplained.** A 2-byte `05 01` is accepted by
  `wmr_ctrl_parse_command` (tests/test_ctrl.c:65), and a disconnected RIGHT should reach
  `wmr_controller_forget_bond()` and return OK or NO_ADDRESS, never INVALID_PARAM, which only comes
  from the length/hand parse check. Either nRF Connect sent a value that was not two bytes, or the
  write path truncated it. One retry with the exact bytes confirmed is needed before calling this a
  firmware defect.

The app's Debug → Controller control panel keeps one status row and replaces it on each
notification, so "only one notification" there is not by itself a defect; no app issue was filed
for it.

`wmr_controller_forget_bond()` returns `WMR_BOND_RESET_BUSY` when `connect_pending` is set or the
hand has a `cid`/`connected` (`src/wmr_controller.c`), and `ctrl_write` maps that to ATT `0x81`
(`src/wmr_transport.c`); a `0x00` there would be a real defect. The invalid-opcode result follows
the same path: `default: res = WMR_CTRL_RES_INVALID_PARAM` → `0x80 + 0x06`.

The reconnect pair also printed `W pair HID SET_PROTOCOL failed: unsupported request (0x03)` after
each hand — the WMR controller does not implement the HID boot protocol; it is noise here, not a
defect of this check.

### Stick dead zone (§3) — rest half only

Debug screen at rest, both sticks untouched, FF14 stream: normalized `out` = `+0.0000, +0.0000`
on both hands (screenshot). Persisted store (`app_flutter/stick_settings.json`, first sitting):

```
deadZone 0.05, learn true
centres[LEFT ] = (-0.022653, 0.008952)
centres[RIGHT] = (-0.111924, 0.081852)
```

The screenshot sequence shows the RIGHT centre moving from about `-0.1112, +0.0835` toward the
persisted value while its sample count rose 303 → 318, so learning was still converging during
the run; LEFT moved from `-0.0239, +0.0092`. The later Debug line printed `-0.1106, +0.0831` and
`learned/set 168x` for **both** hands; the stored file has two different centres, so the LEFT line
is suspect — treat the stored values above as the record and re-read LEFT on the next sitting.

Second sitting, dead zone set to 0: the Debug screen then printed the centre line for each hand
(`x`, `y` are the centre, `out` the normalized value):

| Hand | Centre at first read | Centre after sitting | `out` after sitting |
|---|---|---|---|
| LEFT | `-0.0303, -0.0107` (1x) | `-0.0083, +0.0211` (128x) | `+0.0049, -0.0006` |
| RIGHT | `-0.1093, +0.0829` (1x) | `-0.1123, +0.0861` (180x) | `+0.0008, -0.0028` |

Third sitting, dead zone still 0 (`deadZone 0.0`), learning claimed off: centre lines read LEFT
`+0.0166, +0.0254` and RIGHT `−0.1120, +0.0857`. That pair makes the split clear: RIGHT is stable
to ±0.003 across all three sittings (`−0.1093`, `−0.1123`, `−0.1120` on x; `+0.0829`, `+0.0861`,
`+0.0857` on y), a real ~0.14 mechanical bias; LEFT went `−0.0303, −0.0107` → `−0.0083, +0.0211`
→ `+0.0166, +0.0254`, flipping sign on both axes, i.e. the learner still moving. Neither pair
proves learning-off, because the centre is still changing, and no `out` pair was reported for the
third sitting.

The owner reported learning switched off for that pair, but the app's saved settings read

```
deadZone 0.0, learn true
centres[LEFT ] = (-0.007763, 0.021720)
centres[RIGHT] = (-0.111539, 0.086905)
```

i.e. learning was still on and the persisted centres match the "after sitting" row. So the two
near-zero `out` pairs show the learner tracking the centre, not a raw offset. `StickNormalizer`
only moves the centre from `_learn()` when `config.learn` is true (`packages/wmr_core/lib/domain/stick_centre.dart`),
so `out ≈ 0` with a moving centre is expected while learning runs.

Why the saved file still says `learn true` is **unresolved**, not explained away: the Debug
`SwitchListTile` calls `setLearn`, which writes the whole `StickSettings` after a short debounce
(`apps/wmr_companion_app/lib/ui/debug/stream_settings.dart`), and both the real and fake services
listen to `stickSettingsProvider.config` and apply it, so the code path looks right. The evidence
does not distinguish between the switch never being moved, it being switched back on, and the save
not landing. Step 5's file read settles it; if the file still says `learn true` with the switch
visibly off, that is an app defect and gets filed rather than noted.

Still needed: step 2 with learning actually off and the centre zeroed (raw vs normalized), step 3
end stops, steps 4–5 held offset and learning-off (centre frozen), and the `stick_settings.json`
read after switching learning off in step 5 to settle the persistence question above.

### Latency stream ladder (§4) — five app exports

All five are app `wmr-latency` v3 exports from one sitting, 120.0 s each, ping interval 250 ms,
`mtu=517`, bridge `8c:94:df:12:b9:b6`, 479 pings sent and 479 answered. Stream labels are the app's
own (`raw` = FF11, `decoded` = FF12, `batched` = FF14, `none` = no stream).

| Export (`latency-20261028-…`) | Stream | RTT p50 | p95 | max | RSSI start → end | Stalls >50 ms L / R | Seq gaps L / R | Tick loss L / R |
|---|---|---|---|---|---|---|---|---|
| `200130` | `batched` (FF14) | 39.3 ms | 92.0 | 126.6 | −59 → −65 dBm | 235 / 317 | 0 / 0 | 14.5 / 20.7 % |
| `200358` | `decoded` (FF12) | 34.5 ms | 100.9 | 149.4 | −64 → −62 dBm | 133 / 140 | 0 / 0 | 15.1 / 24.1 % |
| `200617` | `raw` (FF11) | 34.3 ms | 98.5 | 126.2 | −62 → −60 dBm | 158 / 143 | 0 / 0 | not reported |
| `200916` | `none` | 63.4 ms | 85.5 | 131.9 | −62 → −60 dBm | — | — | — |
| `201148` | `none` | 62.3 ms | 80.2 | 138.8 | −57 → −59 dBm | — | — | — |

All five report `mtu=517`, `connection_interval_us=15000` and `high_priority_requested=true`. The
owner confirms the sitting ran backwards — FF14, FF12, FF11, `none`, `none` — so the labels above
are the app's own and the timestamps agree. After each `none` run the screen switches back to
FF11 raw: the latency screen remembers the mode that was active when `none` was picked and restores
it when the run stops (`_restoreMode` in `apps/wmr_companion_app/lib/ui/latency/latency_providers.dart`),
which is why the second `none` run also ended on FF11. That is by design and happens after the run
ends, so neither export is contaminated; it only means the stream has to be picked again for the
next no-stream run.

What the exports cannot supply is the bridge `STREAM` line for each run, so `lat` and `sub` are
unrecorded, and `200130` (FF14) drifted 6 dB start to end, past the 3 dB note threshold in
`latency-2` §2 — that run is the weakest of the five and is the one to repeat first.

The ladder is complete: all four streams, with `none` run twice. The `none` frame CSVs are
98 bytes — header only — which is correct for a run with no decoded samples; the JSON for each
still carries all 479 pings. The earlier reading that those two exports were empty was wrong.

Direction matches the 2026-10-22/24 runs: `none` costs about 25 ms over the streamed modes, and
FF11/FF12 sit ~5 ms under FF14. This is one run per cell in one session, so `latency-2` §3.3 does
not allow a ladder verdict, and check 5's paired cells (three app and three probe runs at FF14,
interleaved, plus a second session) are still outstanding. On the single FF14 pair the arithmetic
would be `d` = 39.3 − 35.6 = **+3.7 ms** and stalls 235 vs 274 (LEFT), 317 vs 365 (RIGHT), i.e.
inside both Stay bounds; that is a shape, not a verdict.

### FF13 in fusion (§6) — two pose recordings

Pulled with `adb exec-out run-as com.onyxlens.wmr_companion cat app_flutter/<name>` (files live in
the app documents directory, not on `/sdcard`). Both are `wmr-pose-recording` v1 files written by
the Debug screen recorder, i.e. the fused poses the viewer shows; every sample is
`source=orientationOnly`, `confidence=none` (no positional tracking in these takes).

Yaw is measured as the heading change about world Y: the relative rotation from the first sample,
projected onto the Y axis (swing-twist). The Debug screen's own Euler yaw is **not** usable at rest
on a face-down controller: in the 6.6 s take the RIGHT hand sits at pitch `+88.8°` (gimbal lock)
and its Euler yaw wanders `-6.4°` then `+4.4°` while the actual heading changes by `-0.03°`.

| Recording | Samples | Span | Attitude | LEFT heading | RIGHT heading |
|---|---|---|---|---|---|
| `pose-20261028-191217.json` | 201 | 6.6 s | both hands still | +0.00° | −0.03° (pitch +88.8°, face down) |
| `pose-20261028-192352.json` | 437 | 14.4 s | RIGHT hand held at pitch +24.8° and turned | −0.01° | −25.33° (−1.74 °/s) |

Only the first take is a rest measurement, and 6.6 s is short. The second is a hand-held turn, not
drift. Neither take is labelled with the FF13 switch state (the switch is in-memory only), so there
is no before/after pair yet, and the 90° hand-turn error in §6 was not recorded. What check 6 needs
is two 60 s recordings with both controllers face down and untouched, switch off then on.

The owner also reported a rest pair in prose — LEFT "not moving", and two RIGHT figures, `83.5`
and `42.7` — with no export and no switch label. Which number is switch-off and which is
switch-on is not stated, so neither can be assigned; if `42.7` is the on state it would agree with
"FF13 on helped a lot", but that reading must not be recorded until it is labelled.

### Latency (§4, §5) — one native probe run

The owner attached the GATT probe's export from 2026-10-28 19:25:45–19:27:46 local:
`latency-20261028-192746.json` (`wmr-latency` v2, `source` `wmr-gatt-probe (native Android)`),
`-echo.csv` (473 pings) and `-samples.csv` (39 642 frames, not needed for the verdict).

| Field | Value |
|---|---|
| Run | 120.4 s, FF14 `batched`, ping interval 250 ms, MTU 517, RSSI −67 → −66 dBm |
| Round trip | 473 sent / 473 answered, 0 timeouts, 0 failed writes; min 18.6 / p50 **35.6** / p95 90.6 / max 129.8 ms (echo CSV agrees: p50 35.609 ms) |
| End-to-end / turnaround | p50 35.9 ms / p50 0.343 ms (p99 8.3 ms) |
| Max queue depth | 28 |
| Stalls > 50 ms | LEFT 274, RIGHT 365; sequence gaps 0 / 0; tick loss 15.1 % / 20.6 % |
| Missing | the bridge `STREAM` line for the run (interval, `lat`), so the `conn=`/`lat` gate in `latency-2` §4 is unrecorded |

That is the probe half of one FF14 pair. There is no app run from the same session and position to
compare it with, and no app ladder across `none`/`FF11`/`FF12`/`FF14`, so checks 4 and 5 stay
`not run`: the rule in `latency-2` §6 is applied to paired cells from the same session, at least
three runs each, repeated in a second session.


### Link figures that are not ladder results

`dt p50 4.98 ms`, `last5s` loss 21.1 % LEFT / 17.3 % RIGHT, `cum` 19.4 % / 16.3 % are the
firmware's link tick statistics, not the app's stream latency. Check 4 still needs the latency
screen from `latency-2.md`.

