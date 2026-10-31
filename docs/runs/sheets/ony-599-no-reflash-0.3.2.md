# Run sheet: 0.3.2 no-reflash bench (ONY-599 sitting)

One sitting, no reflash. Firmware **0.3.2** is already flashed on the ESP32; this sheet only
confirms the version. Companion app: current release (record the build; 0.3.4+9 or newer). Merges
the checks that were split across `ony-439-android-verification.md`, `right-asymmetry.md` and
`TESTING.md`, plus the 0.3.x release pass and the #113 pose-filter corpus.

Source sheets (do not re-run their full procedures unless noted):
- `docs/runs/sheets/ony-439-android-verification.md` (FF16, FF17, stick, ONY-446)
- `docs/runs/sheets/right-asymmetry.md` (Sitting R, phases 1-2 only; phase 3 needs a reflash)
- `docs/TESTING.md` (FF12 rate, HID power-cycle, link diagnostics D/L rows)
- `docs/runs/sheets/pose-filter.md` (#113 corpus)

## 0. Record before touching anything

Paste once at the top of the result block; every check inherits these.

| Field | Source | Expected / measured |
|---|---|---|
| Bridge firmware version + hash | Console `v`; `git describe --tags --always`; `build-info.json` `version` | `0.3.2` |
| `bridge_hands=` boot banner | Serial monitor first line | record |
| App version + commit | Debug screen "About", or `git -C wmr-companion log --oneline -1` | current release (0.3.4+9 or newer) |
| Phone model + Android version | Settings -> About phone | record |
| Stream mode in use | App stream selector (`none`, `FF11`, `FF12`, `FF14`) | record |

If the bridge reports a version the app cannot parse (FF16 empty or `1`), stop: the board is not
running a packaged build and the FF16 check cannot pass on it.

## 1. FF16 live read (version confirm)

Spec: `docs/PROTOCOL.md` "Firmware version, FF16".

- [ ] Step 1 — Connect the app to `WMR Bridge`, open the Debug screen, read firmware version + capability bits.  
  Expected: app shows `firmware 0.3.2` and `FF11+ FF12+ FF13+ FF14+ FF15+ FF16+` (companion does not display FF17).  
  Measured: ____________________
- [ ] Step 2 — Read the same version over the console: send `v`.  
  Expected: one line matching `build-info.json` `version` (`0.3.2`), not `1` or empty.  
  Measured: ____________________
- [ ] Step 3 — Read again after a disconnect/reconnect, stream `none` then `FF14`.  
  Expected: same string both times.  
  Measured: ____________________

## 2. FF17 scan and connect (connected-state rows)

Spec: `docs/PROTOCOL.md` "Controller scan and status v2, FF17". Both controllers awake; stream `FF14`
for the whole section.

- [ ] Step 1 — Read FF17 with both hands connected.  
  Expected: `version` 1, both `flags` have bit 0 + bit 2, `addr_source` 2 (NVS), distinct non-zero `addr_tail` per hand.  
  Measured: ____________________
- [ ] Step 2 — Write `0x03 0x00` (DISCONNECT LEFT).  
  Expected: ATT success, LEFT bit 0 clears, `last_command` 0x03 / `last_result` 0x00.  
  Measured: ____________________
- [ ] Step 3 — Write `0x02 0x00` (CONNECT LEFT).  
  Expected: LEFT bit 1 then bit 0+2 set, stream resumes for LEFT.  
  Measured: ____________________
- [ ] Steps 4-7 — Double disconnect, DISCOVER with both stored, FORGET_BOND while connected, invalid opcode.  
  Expected: ATT `0x84` (NOT_CONNECTED), `0x83` (NOTHING_TO_DO), `0x81` (BUSY), `0x86` (INVALID_PARAM).  
  Measured: ____________________

(Full FF17 procedure, including the forget-addresses re-pair path, is in `ony-439-android-verification.md` §2.)

## 3. Stick dead zone (phone only)

Spec: companion `docs/PROTOCOL.md` "Stick centre"; `StickNormalizer`.

Controllers face down and still, stream `FF14`, fusion off.

- [ ] Step 1 — No touch, 30 s; watch raw + normalized.  
  Expected: raw rest offset ~LEFT (-0.023, 0.009), RIGHT (-0.112, 0.082); normalized stays 0.000.  
  Measured: ____________________
- [ ] Step 2 — Learning off, centre zeroed (0,0), dead zone 0.  
  Expected: normalized = raw - centre, so the rest offset shows on both axes.  
  Measured: ____________________
- [ ] Step 3 — Learned centre + 0.05; push each stick to end stop in all four directions.  
  Expected: normalized reaches +/-1 at the stop and returns to 0 when released.  
  Measured: ____________________
- [ ] Step 4 — Hold a stick slightly off centre (~0.1) for 2 s.  
  Expected: learning either leaves the centre alone or adopts the held position; record which; "Re-centre stick" sets current reading as centre.  
  Measured: ____________________
- [ ] Step 5 — Learning off, release, read centre, export/read `stick_settings.json`.  
  Expected: centre frozen at the value shown; file reads `learn false`.  
  Measured: ____________________

## 4. ONY-446: RIGHT reconnects after discovery

Both hands paired and stored, bridge booted, serial monitor open, stream `FF14`.

- [ ] Step 1 — Disconnect both hands (`0x03 0x00`, `0x03 0x01`), then `0x04` (FORGET_ADDRS); confirm both `addr_source` 0. With LEFT asleep, run discovery (`d`) so RIGHT re-pairs and streams; leave LEFT unpaired.  
  Expected: both disconnect, `0x04` OK, both `addr_source` 0, then RIGHT alone re-pairs and streams.  
  Measured: ____________________
- [ ] Step 2 — Reboot bridge with RIGHT awake and LEFT in pairing mode; wait for `discovery finished` and LEFT to pair.  
  Expected: RIGHT ACL encrypts within ~750 ms (`RIGHT link encrypted`), `incoming HID connection` within 4 s, RIGHT reports resume; LEFT streams.  
  Measured: ____________________
- [ ] Step 3 — Power-cycle RIGHT once while both hands stream.  
  Expected: neutral report, then RIGHT encrypts and streams again within ~5 s.  
  Measured: ____________________

## 5. Status LED colours (board with LED wired)

LED on GPIO 4: red = none, yellow = LEFT only, blue = RIGHT only, green = both. If no LED, record `declined`.

- [ ] Step 1 — Boot, no controller connected. Expected: red. Measured: ____________________
- [ ] Step 2 — Connect LEFT only. Expected: yellow. Measured: ____________________
- [ ] Step 3 — Disconnect LEFT, connect RIGHT only. Expected: blue. Measured: ____________________
- [ ] Step 4 — Connect both. Expected: green. Measured: ____________________
- [ ] Step 5 — Disconnect both. Expected: back to red within ~1 s. Measured: ____________________

## 6. Android HID gamepad, no app

Spec: `docs/HID_GAMEPAD.md`. Forget a `WMR Bridge` entry left by 0.2.x once before this section.

- [ ] Step 1 — Bridge advertising (`adv=on`, no discovery round); pair `WMR Bridge` in Android Bluetooth settings.  
  Expected: one entry, gamepad icon, pairing completes; bridge logs `BLE phone connected` and `hid=on`.  
  Measured: ____________________
- [ ] Step 2 — Open a gamepad tester; press every control on both hands.  
  Expected: every control registers and releases; no stuck input.  
  Measured: ____________________
- [ ] Step 3 — Stream `FF14` in companion with both hands connected, tester open.  
  Expected: both hands keep streaming; HID continues; status shows `HID reports` rising, `errors=0` (~68/s).  
  Measured: ____________________
- [ ] Step 4 — Power-cycle one controller while the phone stays connected.  
  Expected: neutral report, then input resumes; no button stays pressed.  
  Measured: ____________________
- [ ] Step 5 — Reboot the bridge with the phone still bonded.  
  Expected: phone reconnects to the bond without re-pairing; `hid=on` again.  
  Measured: ____________________

## 7. Link diagnostics (0.3.2 firmware change: `src/wmr_linkdiag.c`)

Procedure and expected values: `docs/TESTING.md` "Link diagnostics: bench-test checklist" (D1-D10, L1-L5)
and "FF12 decoded stream" (checks 5, 6, 13, 14).

- [ ] D1 — Boots and prints the new lines (`vhci_drop=`, `log_errors=`, per-hand `link:`). Measured: ____________________
- [ ] D2 — Link health answered for 30 s (`rssi`, `fcc`, `lq` or `n/a(0xNN)`). Measured: ____________________
- [ ] D4 — Tick-based estimate vs analyzer agrees. Measured: ____________________
- [ ] D5 — Estimate on a clean stream (nearby controller): `dev_missing` near 0, `period` ~4.99 ms. Measured: ____________________
- [ ] D6 — Reconnect: `link:` shows no ACL while down; counters restart on reconnect. Measured: ____________________
- [ ] D7 — Mode changes log only on a real change. Measured: ____________________
- [ ] D8 — VHCI drop counter stays 0 (or a finding). Measured: ____________________
- [ ] D9 — Diagnostics build does not change rates. Measured: ____________________
- [ ] D10 — DECODING.md experiments (no phone / not subscribed / FF11 / FF12; each hand alone; positions swapped). Measured: ____________________
- [ ] L1 — `LINKLOG` lines: one header, one per hand per 5 s, field counts match. Measured: ____________________
- [ ] L2 — `LINKLOG` parses in a CSV reader; empty fields where unknown. Measured: ____________________
- [ ] L3 — First-drop line appears once per episode. Measured: ____________________
- [ ] L4 — LE buffer size line at boot. Measured: ____________________
- [ ] L5 — Data length line (or "no change event") after connect. Measured: ____________________
- [ ] FF12-5/6/13/14 — Sequence continuity (one hand 60 s; both hands 10 min), small MTU, recovery. Measured: ____________________

## 8. Sitting R, phases 1-2 (no reflash)

From `right-asymmetry.md` §§3-4, phases 1-2 only (phase 3 needs a reflash with the address-override
build, so it is excluded from this no-reflash sitting). Attribution question: does RIGHT's ~10.8 %
loss follow the controller, the table position, or the firmware hand slot?

Phase 1 (baseline, standard pairing/positions):
- [ ] A1 — Physical LEFT at Pos 1 alone. Expected ~0.5 %. Measured: ____________________
- [ ] B1 — Physical RIGHT at Pos 2 alone. Expected ~10.8 %. Measured: ____________________
- [ ] A2 — Repeat LEFT at Pos 1 (drift check, <=3.0 pts from A1). Measured: ____________________
- [ ] B2 — Repeat RIGHT at Pos 2 (drift check, <=3.0 pts from B1). Measured: ____________________

Phase 2 (swap desk positions, no re-pair, no reflash):
- [ ] P1 — Physical LEFT at Pos 2 alone. Measured: ____________________
- [ ] P2 — Physical RIGHT at Pos 1 alone. Measured: ____________________
- [ ] P3 — Repeat/drift check. Measured: ____________________

Record per run: battery raw start/end, temperature raw start/end, ambient C, uptime window, STREAM
line, active link line, non-tested hand "no ACL link", and the §6 paste-back block.

## 9. #113 pose-filter corpus

Procedure exists: `docs/runs/sheets/pose-filter.md` (guided bench "Capture 6DoF pose" step; filter-spec
rev 4 §8 segments). No new procedure invented here.

- [ ] Run the guided bench wizard through the "Capture 6DoF pose" step on a tracking-capable source
  (VITURE / XREAL / Android XR). If no such source is available, record `unavailable: no 6DoF
  hand-tracking source` and mark skipped (does not block bundle export).  
  Measured: ____________________
- [ ] Verify the bundle contains one `pose_capture_<runtime>_<hand>_<segment>.json` per hand per
  segment (Still 10 s, Held drifting 10 s, Slow pan, Medium pan, Fast flick) plus manifest entries
  with runtime, segment, app commit and firmware SHA.  
  Measured: ____________________
- [ ] Skipped/failed segments recorded with reason; all-five-skipped still yields a complete bundle.  
  Measured: ____________________

## 10. Results

One row per check; `not run` is the honest default; declined checks recorded as `declined`.

| # | Check | Gate | Result | Device | Build | Figures |
|---|---|---|---|---|---|---|
| 1 | FF16 on companion (current) | 0.3.2 | not run | | | §1 |
| 2 | FF17 scan/connect | carried | not run | | | §2 |
| 3 | Stick dead zone | carried | not run | | | §3 |
| 4 | ONY-446 RIGHT reconnect | 0.3.2 | not run | | | §4 |
| 5 | Status LED colours | 0.3.2 | not run | | | §5 |
| 6 | Android HID gamepad | 0.3.2 | not run | | | §6 |
| 7 | Link diagnostics | 0.3.2 | not run | | | §7 |
| 8 | Sitting R phases 1-2 | carried | not run | | | §8 |
| 9 | #113 pose-filter corpus | carried | not run | | | §9 |

## 11. Paste-back block

```
Build: firmware 0.3.2 / <hash> / build-info version <...>; app <version> / <commit>
Phone: <model>, Android <version>; stream <mode>; bridge_hands=<0|1>
1 FF16: app="<...>" bits="<...>" console v="<...>" reconnect="<...>"  -> PASS/FAIL
2 FF17: status before=<hex> after writes=<hex> att=<0x..> each  -> PASS/FAIL
3 Dead zone: raw L=(x,y) R=(x,y); normalized rest L=(x,y) R=(x,y); max per direction=...  -> PASS/FAIL
4 ONY-446: RIGHT ACL=<...> encrypted=<yes|no> hid=<yes|no> reports=<yes|no> reset=<yes|no>  -> PASS/FAIL
5 LED: none=<...> left=<...> right=<...> both=<...> back to none=<...>  -> PASS/FAIL
6 HID: pair=<...> controls=<...> FF14=<...> power-cycle=<...> reboot=<...>  -> PASS/FAIL
7 Link diag: D1-D10, L1-L5, FF12-5/6/13/14 figures  -> PASS/FAIL
8 Sitting R: A1/B1/A2/B2/P1/P2/P3 last5s means  -> PASS/FAIL/ATTRIBUTION
9 #113 corpus: files present=<...> segments=<...> manifest=<...>  -> PASS/FAIL
Defects filed: <issue ids or none>
```

A check with no figures is `not run`, even if it "seemed fine".
