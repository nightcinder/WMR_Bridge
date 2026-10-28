# WMR Bridge: Firmware & Bridge Status
Last updated: 2026-10-26 (current tagged release v0.2.4 — ONY-307 discovery/ACL-slot fixes and the ONY-313 logging cleanup; ESP32-over-BLE is the default supported target and the Linux/Raspberry Pi Wi-Fi host bridge is the second supported first-class target; Pi is host-tests-only until the Pi sitting runs — see [`docs/PI_BRIDGE.md`](PI_BRIDGE.md) and [`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md)). Firmware and Pi hardware runs remain unverified unless a run sheet proves otherwise.

This document tracks **firmware and host-bridge capabilities and empirical findings** in this repository. Two supported first-class bridge targets are maintained here:
- **ESP32-over-BLE (`src/`, default):** BTstack Classic Bluetooth HID host forwarding over BLE GATT (`FF10` service; `FF11`–`FF16`). Bench-tested with two WMR controllers and an Android phone (user-reported figures below).
- **Linux / Raspberry Pi-over-Wi-Fi (`pi/`, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md)):** BlueZ Classic Bluetooth HID host forwarding the same `FF11`/`FF12`/`FF14` payloads over a 5 GHz Wi-Fi WebSocket (`wmr-wifi-v1`). **Second supported target, but host-tests-only until the Pi sitting runs** ([`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md)).

The overall project status, milestones, roadmap, and companion Flutter app state (including 3D viewer, orientation fusion, and host latency tracking) are maintained at the project's single entry point: [nightcinder/wmr-companion docs/STATUS.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/STATUS.md).

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
| Firmware/Pi logging cleanup (single log format, human-readable pairing diagnostics, Pi hand/client logs) (ONY-313) | Merged (v0.2.1) | Host tests only; **not run on hardware** |
| Discovery pairing fixes: retry failed pairing connects, reserve the Classic radio while a hand is missing, free the ACL slot before each discovery page (ONY-307, PRs #77 and #80–#82) | Merged (v0.2.1–v0.2.4) | Field logs on 0.2.2/0.2.3 only; packaged v0.2.4 remains **not run on hardware** |
| Single-hand bridge mode `WMR_CFG_BRIDGE_HANDS` / `WMR_CFG_BLE_NAME` (PR #33) | Merged | Host tests only, **not run on hardware** |
| Linux / Raspberry Pi Wi-Fi host bridge (`pi/`, WebSocket replaces BLE; PR #47, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md), [`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md)) | Merged (second supported target) | **Host tests only (94 tests + loopback replay), never run on Pi hardware or with controllers** |
| One-command build/package/flash framework and the browser installer (PR #37, [`docs/FLASHING.md`](FLASHING.md)) | Merged | CI-built; flashing a packaged image has **not** been reported |
| Packaged firmware version over FF16 and the console `v` command (PR #38, [`docs/COMPATIBILITY.md`](COMPATIBILITY.md)) | Merged | Host tests only, **not run on hardware** |
| PSVR2 Sense controller support: name/model discovery searched by default beside WMR, model persistence, 79-byte raw frames, Feature Report `0x05` wake (PR #46, [`docs/PSVR2.md`](PSVR2.md)) | Merged (always compiled in; 0.2 default) | Host tests and firmware build only, **never run on a PSVR2 controller**; no PSVR2 decoder (FF11 raw only) |
| M5Stack ATOM Lite + Atomic Battery Base battery life investigation ([`docs/ATOM_LITE_BATTERY.md`](ATOM_LITE_BATTERY.md)) | Documented | Electrical and efficiency model: ~45–52 min dual-controller, ~58–65 min single-controller; not recommended for session VR |
| BLE HID-over-GATT gamepad (ONY-441, draft) | Firmware build + host vector tests; phone offers a gamepad, pairs and receives HID input on `0.2.4-15`; re-pair after Forget fixed by the stale-bond repair | **No completed phone run** (no tester paste-back); HID UUID in the primary advertisement, `adv=on/off` in the status line, and a stale phone bond is deleted on `PIN_OR_KEY_MISSING` so the same connection re-pairs; see [`docs/HID_GAMEPAD.md`](HID_GAMEPAD.md) |
| Packet loss and pose prediction investigation ([`docs/PREDICTION.md`](PREDICTION.md)) | Documented; ONY-176 levers run-ready | Re-analysis of the 2026-10-22 120 s run: 18–25 % missing on the Classic uplinks, 0 sequence gaps on BLE; the 2026-10-24 run on the `v0.1.1` bridge and `0.1.0` app measured 21.0 % (LEFT) and 15.2 % (RIGHT) tick loss, inside that spread, and the later FF12 run in the same sitting 30.7 % / 17.6 %. One ESP32 is adequate for the 3DoF demo but not VR-grade dual-controller tracking, and 40.5–44.8 ms is the current round-trip median with any stream against 61.6 ms with none ([`docs/runs/roundtrip/`](runs/roundtrip/README.md)). ONY-176 adds the run-ready order for V1 stagger, RIGHT-asymmetry phases 1-2, DLE/interval/FF14 hold and the diagnostics rule-out for host buffers ([`docs/runs/sheets/single-esp32-levers.md`](runs/sheets/single-esp32-levers.md)); it also adds the `WMR_CFG_BATCH_HOLD_INTERVALS` knob (default 1). No new hardware run beyond the latency runs |

## BLE HID gamepad (ONY-441)

Scope: the same phone link also exposes one standard BLE HID-over-GATT
gamepad, so Android sees the controllers with no app. The FF10 service is
unchanged. Implementation and procedure: [`HID_GAMEPAD.md`](HID_GAMEPAD.md).

| Check | Result | Phone / Android / build | Where |
|---|---|---|---|
| Stock phone pairs and shows a gamepad, no app | PARTIAL: gamepad offered, paired and connected, `HID reports=520 errors=0`; tester not opened | Pixel 11 Pro / Android 17, `0.2.4-15` | `HID_GAMEPAD.md` §Verification |
| Every control in a gamepad tester (buttons, d-pad, face buttons, sticks, triggers) | not run | — | same |
| No stuck input after controller or BLE reconnect | not run | — | same |
| FF12 rate unchanged while HID is active | not run | — | same |
| Shared input→report vectors, firmware side | passing (host) | `tests/test_hid.c` | `tests/vectors/hid_gamepad.txt` |
| Shared input→report vectors, companion side | passing (host) | `wmr_companion_hid_android` | companion PR |

The firmware build (ESP-IDF v6.1, BTstack `e38553977`, both-hands variant)
passes and the host vector tests pass. A Pixel 11 Pro / Android 17 retest on
`0.2.4-8-gba1a8bb` paired after several bridge resets, but the phone showed a
phone icon and stopped connecting after the entry was forgotten. The cause is
BTstack's default Class of Device `0x007a020c` ("Smartphone"), which a dual-mode
phone prefers over the LE appearance; `0.2.4-10-g084e2f3` sets Peripheral /
Gamepad (`0x000508`) and adds the GAP Appearance characteristic (`0x03C4`). The
`0.2.4-10-g084e2f3` run then showed the gamepad icon but ended in
`cannot communicate with <address>`; the bridge log had no phone link at all on
either transport, and the phone still held a Classic bond from the earlier
attempt. A Classically discoverable bridge is paired over BR/EDR by Android,
where this HID host has no profile, so the bridge now keeps page scan but turns
inquiry scan off and advertises `BR/EDR not supported` (flags `0x06`); the phone
only sees the LE HID device.

The `0.2.4-16` retest (CI merge build of this branch and `main`) still showed a
gamepad entry that would not connect, and the attached bridge log again had
`phone=0` with no LE connection and no Classic link from the phone for the whole
capture. The attempt ran while controller discovery held the Classic radio
(inquiry rounds 1-6) and before the phone retry that the instructions ask for
after `discovery finished`; the log also shows the bridge refusing incoming
Classic links in that phase. The HID service UUID `0x1812` now sits in the
primary advertisement (not only in the scan response), advertising is re-asserted
after discovery and after a phone disconnect, and every status line prints
`adv=on`/`adv=off` so the next log says whether the phone could connect at that
moment. Every row above that needs a phone stays `not run` until the paste-back
block in `HID_GAMEPAD.md` is filled in.

## Contention-2 Empirical Hardware Findings (Issue #18)

Full run sheet and figures: [`docs/runs/sheets/contention-2-results.md`](runs/sheets/contention-2-results.md).

### 1. Classic ACL Master Role Confirmed (Variant V3 No-Op)
- Instrumented with PR #28 per-link role logging (`HCI_EVENT_ROLE_CHANGE` and `HCI_EVENT_CONNECTION_COMPLETE`).
- **Result:** Both controller links consistently establish and operate as **`master`** (`status=0x00`).
- **Conclusion:** ESP32 is already master; report loss is not caused by slave-role scheduling. Variant V3 (`WMR_CFG_CLASSIC_FORCE_MASTER`) is a no-op and discarded.

### 2. Sniff Mode Variant (Variant V2)
- Tested with `WMR_CFG_CLASSIC_NO_SNIFF=1` (PR #27, #29, #30).
- Confirmed via serial link policy read-back: `status=0x00`, `settings=0x0001` (`sniff=clear`, `role_switch=on`).
- 120-second bench run (FF14 batched, MTU 517, high priority):
  - **LEFT tick loss:** 17.55 %
  - **RIGHT tick loss:** 23.19 %
- **Conclusion:** Loss remains within the baseline spread (LEFT 14–20 %, RIGHT 19–26 %). Disabling sniff mode has no measurable effect on report loss in this single 120 s run; it is not ruled out definitively.

### 3. RTT Delay Bound (Stream Congestion Ruled Out)
- Tested with latency screen stream selector (`stream: none` vs `stream: batched`, 60 s each, 250 ms ping interval; app side tracked in companion `docs/STATUS.md`).
- **Result:** Median RTT was ~59 ms with no stream vs ~41 ms with active stream; ESP32 turnaround was <0.5 ms (336 µs and 472 µs).
- **Conclusion:** The ~40 ms RTT is bound by Android GATT write-request and BLE connection interval round trips, not stream congestion or bridge queue delay.
- **Freshness:** the ~40 ms figure reproduced on the current release pair. Four 120 s runs with both controllers on 2026-10-24 (WMR_Bridge `v0.1.1`, wmr-companion `0.1.0`) measured rtt p50 40.5 ms FF14, 44.8 ms FF11, 44.3 ms FF12 and 61.6 ms with no stream, with 479 of 479 pings answered in each and p99 102.5–124.7 ms ([`docs/runs/roundtrip/`](runs/roundtrip/README.md)). The client refactor after 2026-10-22 (latency modes, `wmr_core` extraction, multi-bridge, export v3) did not move the streamed median, and the no-stream penalty reproduced (about 59 ms then, 61.6 ms now), which fits the slave-latency explanation. That is one pass of the ladder: the none and FF14 repeats are not run, and latency-2 (app against the native GATT probe) is still open, so the split between app, plugin and radio is still unknown; see [`docs/PREDICTION.md`](PREDICTION.md) §5.

### 4. Next Step: Multi-Bridge Architecture & Pi Wi-Fi Host Bridge
Single-controller runs showed near-zero loss on LEFT (~0.5 %) and baseline on RIGHT (~10.8 %), while both controllers together suffer ~18–25 % loss (user-reported).

However:
- The no-sniff result is **one** 120 s run (LEFT 17.55 %, RIGHT 23.19 %), inside the baseline spread — no effect shown, not "ruled out definitively".
- Radio contention is the **working hypothesis, not proven**. The master-role and sniff variants are no-ops/null results; that narrows candidates, it does not confirm the cause.
- Multi-bridge cannot deliver zero loss: RIGHT alone was ~10.8 % with one controller and one phone (user-reported). The best it can do on today's evidence is LEFT ~0.5 % / RIGHT ~10.8 %.
- **No two-bridge run has happened.** The sitting and its fixed pass criteria are in [`docs/runs/sheets/multi-bridge.md`](runs/sheets/multi-bridge.md); LEFT ≤ 3 % on its own bridge is the pass bar.
- **No Raspberry Pi Wi-Fi bridge hardware run has happened.** The second supported target (`pi/`, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md)) moves the phone link off 2.4 GHz BLE onto 5 GHz Wi-Fi WebSocket; it is host-tests-only until Sitting P in [`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md) runs.

## 5. Single-ESP32 Contention Levers (ONY-176)

Follow-up to ONY-170. Run sheet: [`docs/runs/sheets/single-esp32-levers.md`](runs/sheets/single-esp32-levers.md). **No lever here has a new hardware result.** This table is the run-ready state, not evidence.

| Lever | Status | Result / prerequisite |
|---|---|---|
| V1 staggered connect (`WMR_CFG_STAGGER_FIRST_HAND=1/2`) | **Not run** | Code on `main`; needs the two V1 images and the A/B/A block at one fixed position |
| RIGHT asymmetry, phases 1-2 | **Not run** | No reflash; use [`docs/runs/sheets/right-asymmetry.md`](runs/sheets/right-asymmetry.md) §4 with one controller powered at a time |
| DLE confirmation, current build | **Not run on the current build; 2026-10-22 log shows 251/2120 accepted** | `tx 251 octets / 2120 us, rx 251 octets / 2120 us`; capture the line again on this build |
| Longer BLE interval (30 ms request) | **Not run** | Existing `WMR_CFG_CONN_INTERVAL_MIN/MAX`; phone must grant it and the granted value must be recorded |
| Longer FF14 hold (2 intervals) | **Not run** | New `WMR_CFG_BATCH_HOLD_INTERVALS=2`; default 1 leaves current behaviour unchanged |
| Controller task core/priority, HCI buffers | **Ruled out by diagnostics** | Archived runs: `vhci_drop=0`, `log_errors=0`, queue high-water 41/256 (sample `q=2/256 high=41`), `tx` trailing `rx` only by queued records; no run unless a future window shows a nonzero drop/error counter |

**Best measured configuration** is unchanged by ONY-176: the default both-hands FF14 stream at MTU 517 with the granted 15 ms interval and a one-interval batch hold. Its measured dual-hand loss is 18–25 % per hand ([`docs/PREDICTION.md`](PREDICTION.md) §1), and no tested single-ESP32 variant has beaten it. **Next architecture step:** one bridge per hand (multi-bridge) and/or the Pi Wi-Fi host bridge, to remove the BLE/phone link from the ESP32 radio; neither has run.
