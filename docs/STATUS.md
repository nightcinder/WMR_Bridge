# WMR Bridge: Project Status

Last updated: 2026-10-23 (contention-2 hardware results; see [docs/runs/sheets/contention-2-results.md](runs/sheets/contention-2-results.md)).
For the companion Flutter app status, 3D viewer, and orientation fusion state, see the companion repository [nightcinder/wmr-companion docs/STATUS.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/STATUS.md).

## Firmware Capabilities & Milestones

| Capability | State | Hardware Check (user-reported) |
|---|---|---|
| Classic BT HID host (dual WMR controllers) | Merged | Yes; controllers connect and stream |
| IMU & input decoding (200 Hz nominal, 100 ns ticks) | Merged | Yes; gyro rad/s, accel magnitude ~10 |
| GATT characteristics FF11 (raw) & FF12 (decoded) | Merged | Yes; mutual exclusion and MTU raise tested |
| FF14 batched notification stream | Merged | Yes; default stream, 9 records/batch, queue drop=0 |
| FF15 echo round-trip turnaround | Merged | Yes; sub-millisecond turnaround (p50 <0.5 ms, 336–472 µs) |
| FF13 controller calibration blob manager & GATT stream | Merged | Yes; both blocks read to READY, parsed and applied in fusion |
| Controller discovery by name & NVS address persistence | Merged | Yes; D1–D11 checks passed |
| Link diagnostics (`link:`, `role:`, `POLICY:`) | Merged | Yes; verified in contention-2 runs |

## Contention-2 Empirical Hardware Findings (Issue #18)

Full run sheet and figures: [`docs/runs/sheets/contention-2-results.md`](runs/sheets/contention-2-results.md).

### 1. Classic ACL Master Role Confirmed (Variant V3 No-Op)
- Instrumented with PR #28 per-link role logging (`HCI_EVENT_ROLE_CHANGE` and `HCI_EVENT_CONNECTION_COMPLETE`).
- **Result:** Both controller links consistently establish and operate as **`master`** (`status=0x00`).
- **Conclusion:** ESP32 is already master; report loss is not caused by slave-role scheduling. Variant V3 (`WMR_CFG_CLASSIC_FORCE_MASTER`) is a no-op and discarded.

### 2. Sniff Mode Ruled Out (Variant V2)
- Tested with `WMR_CFG_CLASSIC_NO_SNIFF=1` (PR #27, #29, #30).
- Confirmed via serial link policy read-back: `status=0x00`, `settings=0x0001` (`sniff=clear`, `role_switch=on`).
- 120-second bench run (FF14 batched, MTU 517, high priority):
  - **LEFT tick loss:** 17.55 %
  - **RIGHT tick loss:** 23.19 %
- **Conclusion:** Loss remains within the baseline spread (LEFT 14–20 %, RIGHT 19–26 %). Disabling sniff mode has no measurable effect on report loss.

### 3. RTT Delay Bound (Stream Congestion Ruled Out)
- Tested with latency screen stream selector (`stream: none` vs `stream: batched`, 60 s each, 250 ms ping interval).
- **Result:** Median RTT was ~59 ms with no stream vs ~41 ms with active stream; ESP32 turnaround was <0.5 ms (336 µs and 472 µs).
- **Conclusion:** The ~40 ms RTT is bound by Android GATT write-request and BLE connection interval round trips, not stream congestion or bridge queue delay.

### 4. Next Step: Multi-Bridge Architecture
Single-controller runs showed near-zero loss on LEFT (0.5 %) and baseline on RIGHT (10.8 %), while both controllers together suffer ~18–25 % loss. Having ruled out slave role and sniff mode, the loss is confirmed to be physical 2.4 GHz RF airtime contention inside the single ESP32 transceiver. Multi-Bridge support (dual ESP32s, one per hand) resolves contention at the physical layer without radio scheduling hacks.
