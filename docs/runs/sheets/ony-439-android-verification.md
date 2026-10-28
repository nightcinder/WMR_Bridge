# Run sheet: ONY-439 Android verification campaign

> **Proposed sheet, not yet run.** Nothing here is a hardware result. It becomes one only when
> the owner pastes back the block in §6 for each check; a green CI build is not a result.

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
| 5 | Write `0x01` (DISCOVER) with a hand already connected | ATT success; `discovery_state` leaves 0, `discovery_round` increments, then returns to 0 | Status never leaves idle, or the app crashes |
| 6 | Write `0x05 0x01` (FORGET_BOND RIGHT) while RIGHT is connected | ATT error `0x80 + 0x01` (BUSY), no bond removed | Bond cleared while connected |
| 7 | Write an invalid opcode, e.g. `0x7f` | ATT error `0x80 + 0x06` (INVALID_PARAM) | Any success code |

Do **not** run step 6 with a hand disconnected: it clears the link key and forces a re-pair.

Paste back: the decoded 20-byte status before step 2, after each write, and the ATT status byte
the app reports for steps 2–7; plus the `FF17`/discovery lines from the monitor if it prints them.

## 3. Stick dead zone (phone only)

Spec: `wmr-companion` `docs/PROTOCOL.md` "Stick centre" — `StickNormalizer` subtracts the learned
or set centre, rescales each half-axis so the end stop reaches ±1, clamps, then applies a radial
dead zone (default 0.05, slider 0–0.3) and rescales so the zone edge is 0. The only figure so far
is one user-reported RIGHT y ≈ 0.148 at rest.

Controllers face down and still, stream `FF14`, fusion off is fine — this is a raw-stick check.

| Step | Do | Pass | Fail |
|---|---|---|---|
| 1 | Do not touch either stick; watch the Debug screen raw and normalized values for 30 s | Raw shows the rest offset (expect ~0.148 on RIGHT y); normalized stays 0.000 on both axes, both hands, for the whole 30 s | Normalized drifts off 0 at rest, or jitters |
| 2 | Set the dead zone slider to 0 | Normalized now shows the rest offset, matching the raw figure | Normalized stays 0 with the zone off (the offset is being hidden elsewhere) |
| 3 | Back to 0.05; push each stick to its end stop in all four directions | Normalized reaches ±1 on each axis at the stop, and returns to 0 when released | A direction cannot reach ±1, or does not return to 0 |
| 4 | Hold a stick slightly off centre (about 0.1) and still for 2 s | Learning either leaves the centre alone or adopts the held position — record which; "Re-centre stick" then sets the current reading as centre | Learning adopts the held position with no way to undo it |
| 5 | Switch learning off, release the stick, read the centre | Centre stays at the value shown before the switch | Centre keeps changing with learning off |

Paste back: the raw and normalized pairs at rest per hand, the highest normalized value reached
per direction, whether learning adopted a held offset in step 4, and the dead-zone value used.

## 4. Checks with a procedure elsewhere

| Check | Procedure | Result lands in |
|---|---|---|
| Latency stream ladder (`none`, `FF11`, `FF12`, `FF14`, repeats) | `wmr-companion` `docs/runs/sheets/latency-2.md` §3–§5 | That sheet §7; `wmr-companion` `docs/STATUS.md` milestone 5 |
| App-vs-native probe decision | Same sheet, decision rule in §"The decision rule" and arithmetic in §6 | Same sheet; milestone 5 verdict |
| FF13 calibration applied in fusion | `wmr-companion` `docs/PROTOCOL.md` "FF13 config block in the app"; per-hand switch, default off | `wmr-companion` `docs/STATUS.md` FF13 row |

Record the same device/build header for these three, and paste the exported JSON/CSV names, so the
ladder and the probe run can be tied to the build hash in §0.

## 5. Results

One row per check, filled in only from a pasted-back block. `not run` is the honest default; a
check the owner declines is recorded as `declined`, not as a pass.

| # | Check | Result | Device | Build | Command | Figures |
|---|---|---|---|---|---|---|
| 1 | FF16 live read | partial — app cannot show it (wmr-companion #107); console `v` not yet pasted | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f | §1 | see §7 |
| 2 | FF17 scan/connect | not run | | | §2 | |
| 3 | Stick dead zone | partial — rest half only; no dead-zone-0 or end-stop reading | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f; app 0.2.1 | §3 | see §7 |
| 4 | Latency stream ladder | not run | | | latency-2 §3–§5 | |
| 5 | App-vs-native probe decision | not run | | | latency-2 §6 | |
| 6 | FF13 calibration in fusion | partial — two pose recordings, switch state not labelled; no 90° error | Pixel 11 Pro, Android 17 | 0.2.4 / 41a9e5f; app 0.2.1 | companion PROTOCOL | see §7 |

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

FF13 blocks on the same sitting read to READY: LEFT 7147 bytes `8bc13223`, RIGHT 7148 bytes
`75e8ab85`, `send_errors=0` both hands.

### FF16 (§1) — app gap

The library parses FF16 into `WmrProtocolVersion` and publishes it on the link snapshot
(`packages/wmr_companion/lib/src/companion.dart`), but `apps/wmr_companion_app/lib` never reads
`.protocol` and has no About page. §1 step 1 therefore cannot pass on this build. Filed as
wmr-companion issue #107; the console half (§1 step 2) is still open.

### Stick dead zone (§3) — step 1 half

Debug screen at rest, both sticks untouched, FF14 stream: normalized `out` = `+0.0000, +0.0000`
on both hands (screenshot). Persisted store (`app_flutter/stick_settings.json`):

```
deadZone 0.05, learn true
centres[LEFT ] = (-0.022653, 0.008952)
centres[RIGHT] = (-0.111924, 0.081852)
```

The screenshot sequence shows the RIGHT centre moving from about `-0.1112, +0.0835` toward the
persisted value while its sample count rose 303 → 318, so learning was still converging during
the run; LEFT moved from `-0.0239, +0.0092`. Still needed: dead zone 0 reading, end stops, held
offset and learning-off steps.

### FF13 in fusion (§6) — two pose recordings

Pulled with `adb exec-out run-as com.onyxlens.wmr_companion cat app_flutter/<name>` (files live in
the app documents directory, not on `/sdcard`). Both are `wmr-pose-recording` v1 files written by
the Debug screen recorder, i.e. the fused poses the viewer shows; every sample is
`source=orientationOnly`, `confidence=none` (no positional tracking in these takes). Yaw is the
Y-axis heading of the recorded quaternion, unwrapped; drift is the endpoint difference over the
recording.

| Recording | Samples | Span | LEFT yaw drift | RIGHT yaw drift |
|---|---|---|---|---|
| `pose-20261028-191217.json` | 201 | 6.6 s | +0.00° (0.00 °/s) | −0.02° (−0.003 °/s) |
| `pose-20261028-192352.json` | 437 | 14.4 s | +0.01° (0.00 °/s) | +3.50° (+0.243 °/s) |

Both takes are short, and neither is labelled with the FF13 switch state (the switch is in-memory
only), so they cannot yet be entered as the before/after pair. The 90° hand-turn error in §6 was
not recorded.

### Link figures that are not ladder results

`dt p50 4.98 ms`, `last5s` loss 21.1 % LEFT / 17.3 % RIGHT, `cum` 19.4 % / 16.3 % are the
firmware's link tick statistics, not the app's stream latency. Check 4 still needs the latency
screen from `latency-2.md`.

