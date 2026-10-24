# WMR Bridge: Firmware & Bridge Status
Last updated: 2026-10-24 (ESP32-over-BLE is the default supported target and the Linux/Raspberry Pi Wi-Fi host bridge is the second supported first-class target; Pi is host-tests-only until the Pi sitting runs — see [`docs/PI_BRIDGE.md`](PI_BRIDGE.md) and [`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md)).

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
| FF15 echo round-trip turnaround | Merged | Yes; sub-millisecond turnaround (p50 <0.5 ms, 336–472 µs) |
| FF13 controller calibration blob manager & GATT stream | Merged | Yes; both blocks read to READY (app-side parsing/fusion tracked in companion docs/STATUS.md) |
| Controller discovery by name & NVS address persistence | Merged | Yes; D1–D11 checks passed |
| Link diagnostics (`link:`, `role:`, `POLICY:`) | Merged | Yes; verified in contention-2 runs |
| Single-hand bridge mode `WMR_CFG_BRIDGE_HANDS` / `WMR_CFG_BLE_NAME` (PR #33) | Merged | Host tests only, **not run on hardware** |
| Linux / Raspberry Pi Wi-Fi host bridge (`pi/`, WebSocket replaces BLE; PR #47, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md), [`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md)) | Merged (second supported target) | **Host tests only (94 tests + loopback replay), never run on Pi hardware or with controllers** |
| One-command build/package/flash framework and the browser installer (PR #37, [`docs/FLASHING.md`](FLASHING.md)) | Merged | CI-built; flashing a packaged image has **not** been reported |
| Packaged firmware version over FF16 and the console `v` command (PR #38, [`docs/COMPATIBILITY.md`](COMPATIBILITY.md)) | Merged | Host tests only, **not run on hardware** |
| PSVR2 Sense controller framework: opt-in (`WMR_CFG_PSVR2_SUPPORT=0` by default; `-DWMR_CFG_PSVR2_SUPPORT=1` to enable), name/model discovery, model persistence, 79-byte raw frames, Feature Report `0x05` wake (PR #46, [`docs/PSVR2.md`](PSVR2.md)) | Merged (opt-in, off by default) | Host tests and firmware build only, **never run on a PSVR2 controller**; off by default to preserve DRAM headroom; no PSVR2 decoder (FF11 raw only) |
| M5Stack ATOM Lite + Atomic Battery Base battery life investigation ([`docs/ATOM_LITE_BATTERY.md`](ATOM_LITE_BATTERY.md)) | Documented | Electrical and efficiency model: ~45–52 min dual-controller, ~58–65 min single-controller; not recommended for session VR |
| Packet loss and pose prediction investigation ([`docs/PREDICTION.md`](PREDICTION.md)) | Documented | Re-analysis of the 2026-10-22 120 s run: 18–25 % missing on the Classic uplinks, 0 sequence gaps on BLE; recommends a capped, opt-in predictor in the shared library, not on the ESP32 or in the app. No code or hardware run |

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

### 4. Next Step: Multi-Bridge Architecture & Pi Wi-Fi Host Bridge
Single-controller runs showed near-zero loss on LEFT (~0.5 %) and baseline on RIGHT (~10.8 %), while both controllers together suffer ~18–25 % loss (user-reported).

However:
- The no-sniff result is **one** 120 s run (LEFT 17.55 %, RIGHT 23.19 %), inside the baseline spread — no effect shown, not "ruled out definitively".
- Radio contention is the **working hypothesis, not proven**. The master-role and sniff variants are no-ops/null results; that narrows candidates, it does not confirm the cause.
- Multi-bridge cannot deliver zero loss: RIGHT alone was ~10.8 % with one controller and one phone (user-reported). The best it can do on today's evidence is LEFT ~0.5 % / RIGHT ~10.8 %.
- **No two-bridge run has happened.** The sitting and its fixed pass criteria are in [`docs/runs/sheets/multi-bridge.md`](runs/sheets/multi-bridge.md); LEFT ≤ 3 % on its own bridge is the pass bar.
- **No Raspberry Pi Wi-Fi bridge hardware run has happened.** The second supported target (`pi/`, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md)) moves the phone link off 2.4 GHz BLE onto 5 GHz Wi-Fi WebSocket; it is host-tests-only until Sitting P in [`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md) runs.
