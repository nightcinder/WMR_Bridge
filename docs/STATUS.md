# WMR Bridge: Firmware Status

Last updated: 2026-10-24 (experimental Linux/Pi Wi-Fi host bridge; see [`docs/PI_BRIDGE.md`](PI_BRIDGE.md)).

This document tracks **firmware capabilities and firmware-side empirical findings** in this repository.
The overall project status, milestones, roadmap, and companion Flutter app state (including 3D viewer, orientation fusion, and host latency tracking) are maintained at the project's single entry point: [nightcinder/wmr-companion docs/STATUS.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/STATUS.md).

## Firmware Capabilities & Milestones

| Capability | State | Hardware Check (user-reported) |
|---|---|---|
| Classic BT HID host (dual WMR controllers) | Merged | Yes; controllers connect and stream |
| IMU & input decoding (200 Hz nominal, 100 ns ticks) | Merged | Yes; gyro rad/s, accel magnitude ~10 |
| GATT characteristics FF11 (raw) & FF12 (decoded) | Merged | Yes; mutual exclusion and MTU raise tested |
| FF14 batched notification stream | Merged | Yes; default stream, 9 records/batch, queue drop=0 |
| FF15 echo round-trip turnaround | Merged | Yes; sub-millisecond turnaround (p50 <0.5 ms, 336–472 µs) |
| FF13 controller calibration blob manager & GATT stream | Merged | Yes; both blocks read to READY (app-side parsing/fusion tracked in companion docs/STATUS.md) |
| Controller discovery by name & NVS address persistence | Merged | Yes; D1–D11 checks passed |
| Link diagnostics (`link:`, `role:`, `POLICY:`) | Merged | Yes; verified in contention-2 runs |
| Single-hand bridge mode `WMR_CFG_BRIDGE_HANDS` / `WMR_CFG_BLE_NAME` (PR #33) | Merged | Host tests only, **not run on hardware** |
| Linux / Raspberry Pi Wi-Fi host bridge (`pi/`, WebSocket replaces BLE) | Experimental | **Host tests only, not run on hardware** |
| One-command build/package/flash framework and the browser installer (PR #37, [`docs/FLASHING.md`](FLASHING.md)) | Merged | CI-built; flashing a packaged image has **not** been reported |
| Packaged firmware version over FF16 and the console `v` command (PR #38) | Merged | Host tests only, **not run on hardware** |

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

### 4. Next Step: Multi-Bridge Architecture
Single-controller runs showed near-zero loss on LEFT (~0.5 %) and baseline on RIGHT (~10.8 %), while both controllers together suffer ~18–25 % loss (user-reported).

However:
- The no-sniff result is **one** 120 s run (LEFT 17.55 %, RIGHT 23.19 %), inside the baseline spread — no effect shown, not "ruled out definitively".
- Radio contention is the **working hypothesis, not proven**. The master-role and sniff variants are no-ops/null results; that narrows candidates, it does not confirm the cause.
- Multi-bridge cannot deliver zero loss: RIGHT alone was ~10.8 % with one controller and one phone (user-reported). The best it can do on today's evidence is LEFT ~0.5 % / RIGHT ~10.8 %.
- **No two-bridge run has happened.** The sitting and its fixed pass criteria are in [`docs/runs/sheets/multi-bridge.md`](runs/sheets/multi-bridge.md); LEFT ≤ 3 % on its own bridge is the pass bar.
