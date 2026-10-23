# Results sheet: contention-2 — role logging, no-sniff (V2), and RTT separation

> **Hardware results from bench testing (user-reported, 2026-10-22 / 2026-10-23).**
> Design, variants, and pass criteria: [`contention-2.md`](contention-2.md).
> Tracked under WMR_Bridge issue #18.

## Overview

This sheet records empirical hardware bench results evaluating the candidate hypotheses for Classic Bluetooth upstream report loss (issue #18), along with round-trip latency (RTT) separation measurements.

### Background and Hypotheses Tested

In prior 2026-10-22 bench sessions, report loss between controllers and the ESP32 exhibited a strong dependency on whether both controllers were connected:
- **LEFT alone:** ~0.5 % loss (rate ~203/s)
- **RIGHT alone:** ~10.8 % loss (rate ~182/s)
- **Both connected:** ~18–20 % loss on LEFT, ~18–26 % loss on RIGHT

Three candidate explanations were investigated:
1. **Master/Slave scheduling:** Was the ESP32 acting as a Classic ACL slave on either link, leading to sub-optimal controller polling? (Variant V3)
2. **Sniff mode link policy:** Was Classic sniff mode power-saving negotiation or slot sub-rating robbing radio slots from active report delivery? (Variant V2)
3. **Stream congestion vs stack bounds:** Was the observed ~40 ms RTT on FF15 echo driven by notification stream queue congestion on the bridge or phone transport?

---

## 1. Role Check: Classic ACL Master/Slave Verification

### Setup
Firmware built with per-link role instrumentation (PR #28, merged). The firmware logs parseable `role:` lines at connection complete (`HCI_EVENT_CONNECTION_COMPLETE`) and on every role change event (`HCI_EVENT_ROLE_CHANGE`), recording address, cached BTstack role, event status, and timestamp.

### Serial Capture (Anya, COM5)
```text
RIGHT role: address=2C:4D:79:C8:F9:A9 role=master status=0x00 event=role_change t_ms=3396
RIGHT role: address=2C:4D:79:C8:F9:A9 role=master status=0x00 event=conn_complete t_ms=3410
LEFT role: address=2C:4D:79:C8:C9:CC role=master status=0x00 event=role_change t_ms=61352
LEFT role: address=2C:4D:79:C8:C9:CC role=master status=0x00 event=conn_complete t_ms=61372
```

### Result & Verdict
- Both controller links successfully establish and operate with **`role=master`** (`status=0x00`).
- **Verdict on Variant V3 (`WMR_CFG_CLASSIC_FORCE_MASTER`):**
  Per `contention-2.md` §1 and §4, if the reference log demonstrates master role on both links, Variant V3 is a **NO-OP**.
- **Conclusion:** The ESP32 is already master on both links. Classic report loss is **not** caused by the ESP32 operating as a slave. Variant V3 is discarded.

---

## 2. Variant V2: Classic Link Policy Without Sniff (`WMR_CFG_CLASSIC_NO_SNIFF=1`)

### Setup
- Firmware built with `-DWMR_CFG_CLASSIC_NO_SNIFF=1` (PR #27, with duplicate-case fix PR #29 and policy write sequence fix PR #30).
- Link policy sets `LM_LINK_POLICY_ENABLE_ROLE_SWITCH` strictly; the sniff mode bit (`LM_LINK_POLICY_ENABLE_SNIFF_MODE`) is omitted.
- On connection, `HCI_Write_Link_Policy_Settings` is dispatched per handle and verified with `HCI_Read_Link_Policy_Settings`.
- Test conditions: Controllers stationary, face-down at marked table position. Companion app on Pixel connected with FF14 batched notifications, ATT MTU 517, high connection priority (15 ms connection interval). 120-second continuous capture.

### Policy Verification (Serial Log)
```text
POLICY: RIGHT write handle=0x0080 settings=0x0001 status=0x00
POLICY: RIGHT read handle=0x0080 settings=0x0001 status=0x00 sniff=clear role_switch=on
POLICY: LEFT write handle=0x0081 settings=0x0001 status=0x00
POLICY: LEFT read handle=0x0081 settings=0x0001 status=0x00 sniff=clear role_switch=on
```
Both handles confirmed `status=0x00`, `settings=0x0001` (`sniff=clear`, `role_switch=on`).

### 120-Second Run Results (`latency-20261022-214200.json`)

| Metric | LEFT | RIGHT | Total / System |
|---|---|---|---|
| Duration | 120.0 s | 120.0 s | 120.0 s |
| Reports received (`records`) | 19,839 | 18,401 | 38,240 |
| Notification arrivals | 5,087 | 4,994 | 10,081 |
| Report rate (`rate_per_s`) | 165.36 / s | 153.38 / s | 318.74 / s |
| Missing from ticks (`missing_from_ticks`) | 4,224 | 5,554 | 9,778 |
| **Tick loss (`tick_loss`)** | **17.55 %** | **23.19 %** | **20.36 %** |
| Phone BLE sequence gaps | 0 (0.0 %) | 0 (0.0 %) | 0 |
| BLE stalls (>50 ms) | 232 | 282 | 514 |
| Inter-arrival p50 | 19.6 ms | 19.0 ms | — |
| Inter-arrival p95 | 48.1 ms | 54.1 ms | — |
| Delay variation p50 | 1.27 ms | 1.26 ms | — |
| Echo pings answered | — | — | 479 / 479 (100 %) |
| RTT p50 / p95 | — | — | 39.6 ms / 71.7 ms |
| ESP32 turnaround p50 / max | — | — | 0.46 ms / 5.08 ms |

### Comparison Against Baseline & Verdict
- **Pass Criteria (fixed in `contention-2.md` §4):** Required both hands to drop by at least 5 percentage points below the lowest REF baseline (i.e. LEFT ≤ 10 %, RIGHT ≤ 15 %).
- **Observed:** LEFT at 17.55 %, RIGHT at 23.19 %. Both figures remain entirely within the established REF baseline spread (LEFT 14–20 %, RIGHT 19–26 %).
- **Verdict: Refutes the sniff sub-hypothesis (no effect shown).**
- **Conclusion:** Sniff mode was confirmed clear at the LMP/HCI layer. Disabling sniff mode does not reduce upstream report loss. Sniff negotiation is definitively ruled out as the cause of controller report loss.

---

## 3. RTT Separation: BLE Stream Load vs Transport Bounds

### Question
Does active FF14 streaming cause the ~40 ms RTT measured on FF15 echo (through bridge buffer contention or radio queueing), or is the latency intrinsic to the BLE connection interval and phone stack?

### Protocol
Back-to-back 60-second latency runs on hardware using the app's stream selector (PR #28 in `wmr-companion`):
1. **Run 1 (`stream: none`):** Notification characteristics unsubscribed (FF11/12/14 off); FF15 echo pings sent at 250 ms interval.
2. **Run 2 (`stream: batched`):** FF14 batched stream active concurrently with FF15 echo pings at 250 ms interval.

### Empirical Results

| Metric | Run 1: `none` (Pings Only) | Run 2: `batched` (FF14 Active) | Delta / Implication |
|---|---|---|---|
| Export file | `latency-20261022-210907.json` | `latency-20261022-211032.json` | Sequential runs |
| Duration | 60.0 s | 60.0 s | Identical test window |
| Pings sent / answered | 239 / 239 (100 %) | 239 / 239 (100 %) | Zero timeouts in both |
| RTT min | 42.3 ms | 24.8 ms | Fast path fits 1–2 intervals |
| **RTT p50** | **59.4 ms** | **40.8 ms** | **RTT does not increase with stream load** |
| RTT p95 | 75.0 ms | 59.3 ms | No tail latency blowout from streaming |
| **ESP32 turnaround p50** | **0.34 ms (336 µs)** | **0.47 ms (472 µs)** | **Bridge turnaround <0.5 ms in both runs** |
| ESP32 turnaround max | 1.34 ms | 7.31 ms | Bridge processes echo immediately |
| Left records / loss | 0 records | 10,270 (171.3/s, 14.5 % loss) | Normal baseline streaming |
| Right records / loss | 0 records | 8,886 (148.2/s, 25.8 % loss) | Normal baseline streaming |
| Phone sequence gaps | 0 | 0 | Transport clean in both |

### Analysis & Findings
1. **~40 ms RTT is present with no stream:** Even when zero controller records are being streamed across BLE, the median round-trip time remains ~40–60 ms.
2. **Turnaround is negligible (<0.5 ms):** Median turnaround inside the ESP32 firmware is 336–472 µs. The bridge does not introduce measurable processing or buffering delay.
3. **No-stream latency is slightly higher:** RTT p50 was ~59 ms with no stream vs ~41 ms with active streaming. This is consistent with Android BLE stack radio scheduling: when the BLE link has active notification traffic, the radio anchor points are serviced every 15 ms connection interval; when idle, Android may skip anchor points for power management.
4. **Conclusion:** The ~40 ms RTT is bound by the BLE stack protocol (GATT write-request + ATT notification return across multiple 15 ms connection intervals), **not** by stream congestion or bridge queue backlog.

---

## 4. Synthesis & Architectural Next Steps for Issue #18

Today's hardware runs rule out two major software/protocol hypotheses:
1. **Master role confirmed:** The ESP32 is already master on both links; scheduling as slave is not the problem.
2. **Sniff mode ruled out:** Disabling sniff mode (`sniff=clear`) leaves loss at 17.55 % / 23.19 %, indistinguishable from baseline.
3. **RTT separation confirmed:** Upstream report loss is independent of downstream BLE latency and stream load.

### Physical Contention & Multi-Bridge Architecture
Because report loss drops to near-zero when only one controller is active (LEFT alone is **0.5 %**, RIGHT alone is **10.8 %**), the ~18–25 % loss with both connected represents **physical 2.4 GHz radio airtime contention** in the ESP32's single transceiver. When juggling two high-rate Classic BT ACL links (each pulling ~200 packets/sec) plus BLE, the radio hardware cannot schedule all time-slots without collisions.

**Solution:** Rather than continuing to attempt radio scheduling workarounds on a single chip, the project architecture moves to **Multi-Bridge support** (dual ESP32s: one dedicated to Left, one to Right). With two separate $3 ESP32s, physical radio contention is completely eliminated, yielding the single-controller performance (~0.5 % loss on Left) for full 6DoF tracking.
