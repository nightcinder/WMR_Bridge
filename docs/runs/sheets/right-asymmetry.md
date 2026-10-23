# Run sheet: the RIGHT-alone asymmetry — one-board controller, hand-slot, and position separation

> **Proposed sheet, not yet run.** Nothing here is a hardware result. Figures quoted from earlier runs are user-reported; the pass criteria are fixed before any data. This sheet runs on a single ESP32 on `origin/main` (`41771f8`), with no purchase of a second board required.

Question: why does RIGHT lose ~10.8 % on its own while LEFT loses only ~0.5 % alone, and does that ~10.8 % follow:
1. **The controller** — physical RIGHT has a worse radio RF front-end, antenna, battery level, or thermal characteristics?
2. **The hand slot** — something in the firmware's RIGHT path (connect order, role handling, stored address, FF13 config read, buffer routing)?
3. **The position** — where that controller sits on the table relative to the ESP32 and phone (local RF geometry, multipath, orientation)?

---

## 0. Context & Figures to Compare Against

From `docs/runs/sheets/contention-2-results.md` (lines 14–16, 132) and `docs/runs/sheets/single-controller.md` (user-reported bench figures, FF14 batched notifications, MTU 517, high connection priority 15 ms, `last5s_pct` after a 30 s settle):

| Setup | LEFT Loss | RIGHT Loss | Source |
|---|---|---|---|
| **LEFT alone** | **~0.5 %** (rate ~203/s) | — (`no ACL link`) | `docs/runs/logs/single-controller-left-only.txt` |
| **RIGHT alone** | — (`no ACL link`) | **~10.8 %** (rate ~182/s) | `docs/runs/logs/single-controller-right-only.txt` |
| **Both connected** | **~18–20 %** | **~18–26 %** | `docs/runs/sheets/contention-2-results.md` |

### Why this must be resolved before a second board is commissioned
Multi-bridge architecture (`docs/runs/sheets/multi-bridge.md`) splits the two controllers onto two dedicated ESP32s to eliminate mutual 2.4 GHz radio airtime contention. However, multi-bridge's theoretical best-case performance is strictly bounded by single-controller performance: even with zero contention from the other controller, a second board buys a clean LEFT (~0.5 %) and a bad RIGHT (~10.8 %). RIGHT's own ~10.8 % is the primary product blocker for 6DoF tracking stability.

`multi-bridge.md` §0 poses the question of whether RIGHT's loss moves with the board (S1), the position (S2), or the controller, but requires two boards and reflashing. The sitting designed here isolates the three candidate causes **today on the single board already on the bench**, with no second board and no hardware purchase.

---

## 1. Setup & Hardware Configuration

- **Bridge:** Single ESP32 board on `COM5` (Anya / Nick's bench), running `origin/main` (`41771f8`).
- **Controllers:** Two physical WMR motion controllers:
  - Physical LEFT: known MAC **`2C:4D:79:C8:C9:CC`** (from `contention-2-results.md` line 34)
  - Physical RIGHT: known MAC **`2C:4D:79:C8:F9:A9`** (from `contention-2-results.md` line 32)
- **Table Positions:** Two marked spots on the bench:
  - **Position 1 (Pos L):** The standard left spot, stationary, face-down.
  - **Position 2 (Pos R):** The standard right spot, stationary, face-down.
  - Note orientation and distance to ESP32 and phone. Do not touch or move the ESP32 or phone during timing.
- **Phone:** nRF Connect (or companion app), connect to `WMR Bridge`, Request MTU **517**, connection priority **High** (15 ms), notify on **FF14 only** (FF11, FF12, FF13, and FF15 unsubscribed).
- **Isolation Rule (Single Controller Alone):**
  **Only one controller connected at a time**, exactly as in `single-controller.md` §2. The controller not under test must show `n/a (no ACL link)` with `rate_rx=0.0/s`. Measure the asymmetry in complete radio isolation, never against the dual-link contention floor. Remove the battery or keep the inactive controller powered off.
- **Live #19 Unknowns (Battery & Temperature):**
  Per-controller battery depletion and internal heating are live unknowns (issue #19) and plausible physical confounds for RF performance differences.
  - Record raw `battery` byte (0..255) and signed 16-bit `temperature_raw` word (from the companion app export, spot check, or GATT read of decoded sample offset 44/55) before and after each run.
  - Record ambient room temperature in °C. Do not convert the controller readings to °C or percentage (scale not established, Monado/house rule).

---

## 2. PowerShell Monitor & Run Execution

ESP-IDF PowerShell profile on Anya (`C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1`). **Always append to `$env:Path`, never replace it.**

```powershell
# Load ESP-IDF. This profile appends the tool paths; never replace $env:Path.
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1

cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"

# Monitor with Tee-Object to a named log per run.
# Reset ESP32 before each run (Ctrl+T then Ctrl+R in monitor) so uptime starts at zero.
idf.py -B build-wmr-bridge -p COM5 monitor | Tee-Object -FilePath C:\Users\Nick\btstack-wmr\right-asym-<run-tag>.txt
```

---

## 3. Go / No-Go Gates Before Timing

Half-runs with unsubscribed streams, default MTU, or background link activity cannot be trusted (as documented by `invalid-left-only-phone0-monitor.txt` in `docs/runs/README.md`).

Before starting any timing window, check the latest `STREAM` line and `link:` status in the console:

| Check | Go (Proceed) | No-Go (Stop and Fix Before Timing) |
|---|---|---|
| **`STREAM` line** | `phone=1 sub=1 stream=batched mtu=517` | `phone=0`, `sub=0`, `stream=none`, or `mtu=23` |
| **Tested hand** | `rate_rx` ~170–205/s, `lq` ≥ 220, `rx` advancing steadily | `rate_rx=0.0/s`, `rx` halted, or `lq` < 200 |
| **Non-tested hand** | `n/a (no ACL link)`, `rate_rx=0.0/s`, `rx=0` | Any ACL link present: disconnect (`L`/`R`), remove battery, reset ESP32 |
| **Phone connection** | MTU 517 confirmed, interval 15.00 ms (`conn=[15.00ms...]`) | Latency update pending, interval > 15 ms, or disconnect |

**Timing protocol:** Once all four checks pass, wait **30 seconds settle time**, then record a continuous **5-minute window** (60 `LINKLOG,` rows at 5 s interval). Reset ESP32 (`Ctrl+T`, `Ctrl+R`) between runs so uptime starts at zero.

---

## 4. Run Protocol & A/B/A Ordering

Between-boot baseline loss spreads from 15 % to 26 % across sessions. A single run cannot distinguish a genuine 5–10 point effect from boot variance. All comparisons use **A/B/A interleaved blocks** within the same sitting, with a fixed 5-minute window per run.

The sitting consists of three sequential phases:

```
Phase 1: Baseline Verification (Standard Pairing, Standard Positions)
  A1: Physical LEFT at Pos 1 (Hand slot LEFT)
  B1: Physical RIGHT at Pos 2 (Hand slot RIGHT)
  A2: Physical LEFT at Pos 1 (Drift check)
  B2: Physical RIGHT at Pos 2 (Drift check)

Phase 2: Position Control (Swap Desk Positions, No Re-pairing, No Reflash)
  P1: Physical LEFT at Pos 2 (Hand slot LEFT)
  P2: Physical RIGHT at Pos 1 (Hand slot RIGHT)
  P3: Repeat / drift check

Phase 3: Controller / Hand-Slot Swap (Physical RIGHT as LEFT, Physical LEFT as RIGHT)
  S1: Physical RIGHT at Pos 1 (Hand slot LEFT)
  S2: Physical LEFT at Pos 2 (Hand slot RIGHT)
  S3: Physical RIGHT at Pos 1 (Drift check)
  S4: Physical LEFT at Pos 2 (Drift check)
```

### Phase 1: Baseline Verification
Confirm that the bench setup reproduces the established alone figures:
- Run A1: Physical LEFT (`2C:4D:79:C8:C9:CC`) at Position 1 alone. Expect ~0.5 %.
- Run B1: Physical RIGHT (`2C:4D:79:C8:F9:A9`) at Position 2 alone. Expect ~10.8 %.
- Run A2: Repeat Physical LEFT at Position 1 alone.
- Run B2: Repeat Physical RIGHT at Position 2 alone.

### Phase 2: Position Control (Desk Geometry)
Swap the physical locations of the two controllers without re-pairing or modifying firmware:
- Physical LEFT is placed at **Position 2 (Pos R)**.
- Physical RIGHT is placed at **Position 1 (Pos L)**.
- Firmware pairing remains standard (Physical LEFT is assigned to LEFT hand slot, Physical RIGHT is assigned to RIGHT hand slot).
- Run P1: Physical LEFT at Position 2 alone.
- Run P2: Physical RIGHT at Position 1 alone.
- Run P3: Repeat to verify stability.

### Phase 3: Controller Hand-Slot Swap
The discriminating manoeuvre: assign the physical RIGHT controller (`2C:4D:79:C8:F9:A9`) to the firmware's **LEFT** hand slot, and physical LEFT (`2C:4D:79:C8:C9:CC`) to the **RIGHT** hand slot.

#### Re-pairing Procedure (Reusing `multi-bridge.md` §1.1 and §Case B)
To ensure controllers do not reconnect to their old slots mid-run:
1. Power off or remove the battery from the controller not under test.
2. In the ESP32 console, disconnect any connected controller: press `L` and `R`.
3. Clear stored link keys: press `k` (LEFT) and `K` (RIGHT). This deletes the ESP32's stored bonding keys so stale authentication state is dropped.
4. Clear stored addresses: press `F`. This deletes the NVS address record (`WMRA` tag) and starts discovery.

#### Firmware Hand-Slot Binding (Console Discovery vs Compile-Time Override)
In standard firmware (`src/wmr_discovery.c`), discovery matches device names explicitly:
- Name `"Motion controller - Left"` maps to `WMR_HAND_LEFT` (slot 0).
- Name `"Motion controller - Right"` maps to `WMR_HAND_RIGHT` (slot 1).

Because the physical controllers advertise their factory Bluetooth names, running console discovery (`d`) will re-match `Motion controller - Right` to the RIGHT slot.
- **Go/No-Go Gate on Hand Swap:** Inspect the console output on discovery. If the bridge reports:
  `Discovery: LEFT is 2C:4D:79:C8:F9:A9 (by name)`
  then the swap succeeded via console.
- **Enforcing the Swap via Existing Config Override:** If console discovery matches the factory name to the RIGHT slot, enforce the swap using the built-in compile-time address override (`EXTRA_CFLAGS` in `src/wmr_config.h`, check D11):
  ```powershell
  # Build with swapped address overrides (no source code edits required)
  $env:EXTRA_CFLAGS = '-DWMR_CFG_LEFT_ADDRESS=\"2C:4D:79:C8:F9:A9\" -DWMR_CFG_RIGHT_ADDRESS=\"2C:4D:79:C8:C9:CC\"'
  idf.py -B build-wmr-asym-swap build
  idf.py -B build-wmr-asym-swap -p COM5 flash monitor
  Remove-Item Env:EXTRA_CFLAGS
  ```
  On boot, press `s` to verify:
  `LEFT address: 2C:4D:79:C8:F9:A9 (compile-time override)`
  `RIGHT address: 2C:4D:79:C8:C9:CC (compile-time override)`

#### Swapped Runs
With the physical RIGHT controller operating in the LEFT slot (and vice versa), run at standard positions (Pos 1 = Left spot, Pos 2 = Right spot):
- Run S1: Physical RIGHT (operating in LEFT slot) at Position 1 alone.
- Run S2: Physical LEFT (operating in RIGHT slot) at Position 2 alone.
- Run S3: Repeat Physical RIGHT (in LEFT slot) at Position 1 alone.
- Run S4: Repeat Physical LEFT (in RIGHT slot) at Position 2 alone.

---

## 5. Pass / Fail & Attribution Criteria (Written Before the Run)

All metrics are evaluated using the mean `last5s_pct` across the 60 rows of each 5-minute window after the 30 s settle.

| Candidate Cause | Observation in Phase 2 (Position Control) | Observation in Phase 3 (Hand Swap) | Attribution Verdict |
|---|---|---|---|
| **1. The Controller Hardware**<br>(Antenna, radio RF, battery) | ~10.8 % loss follows Physical RIGHT to Position 1;<br>Physical LEFT stays ≤ 2 % at Position 2. | ~10.8 % loss follows Physical RIGHT into the LEFT hand slot;<br>Physical LEFT stays ≤ 2 % in the RIGHT hand slot. | **Confirmed Controller Hardware:** The loss belongs to MAC `2C:4D:79:C8:F9:A9`. Check battery byte and `temperature_raw` for hardware correlation. |
| **2. The Table Position**<br>(RF geometry, multipath, orientation) | ~10.8 % loss stays at Position 2 (Pos R):<br>Physical LEFT rises to ~10.8 %;<br>Physical RIGHT at Pos 1 drops to ≤ 2 %. | Loss follows table position regardless of hand slot. | **Confirmed Table Position:** The asymmetry is environmental geometry or antenna orientation relative to the bridge/phone. |
| **3. The Firmware Hand Slot**<br>(Connection order, role, FF13 config) | ~10.8 % loss follows Physical RIGHT (still in RIGHT slot) to Position 1. | ~10.8 % loss stays with the **RIGHT hand slot**:<br>Physical LEFT (now in RIGHT slot) exhibits ~10.8 %;<br>Physical RIGHT (now in LEFT slot) drops to ≤ 2 %. | **Confirmed Firmware Hand Slot:** The loss is caused by firmware handling of the RIGHT link path. |

### Session Validity & Inconclusive Rules
- **Drift Gate:** In Phase 1, the repeated baseline runs (A1 vs A2, and B1 vs B2) must span ≤ 3.0 percentage points. If repeat baselines differ by > 3.0 points, the session drifted; discard all runs and re-run the session.
- **Detection Floor:** Differences smaller than 3.0 percentage points are treated as noise/spread ("no effect shown"). A definitive attribution requires an effect ≥ 5.0 percentage points between conditions.
- **Isolation Breach:** If the non-tested hand shows any ACL connection, received packets (`rx > 0`), or an active link line during an alone run, the run is void.
- **Thermal / Battery Drift:** If `battery` drops by > 15 raw counts or `temperature_raw` changes abruptly between runs, note the values and verify whether loss tracks the thermal/battery change.

---

## 6. Copy-Paste-Back Template Per Run

Copy and paste this exact block back into the results record for every run (A1–B2, P1–P3, S1–S4):

```text
================================================================================
RUN ID:            [e.g. A1 / B1 / P1 / S1]
LOG FILE:          [e.g. right-asym-a1.txt]
CONDITION:         [Baseline / Position Swap / Hand Slot Swap]
PHYSICAL MAC:      [2C:4D:79:C8:C9:CC (LEFT) or 2C:4D:79:C8:F9:A9 (RIGHT)]
HAND SLOT:         [LEFT / RIGHT]
TABLE POSITION:    [Position 1 (Pos L) / Position 2 (Pos R)]
BATTERY (raw):     Start: [   ]   End: [   ]
TEMPERATURE (raw): Start: [   ]   End: [   ]   Ambient (°C): [   ]
UPTIME WINDOW:     Start (settle end): [   ] ms   Stop: [   ] ms

STREAM BLOCK:
[Paste the STREAM line here, e.g.]
STREAM phone=1 sub=1 stream=batched mtu=517 conn=[15.00ms lat=0 to=4000ms] q=0/256 high=... notify=... errors=0

LINK LINES:
[Paste active hand link line, e.g.]
  LEFT link: rssi=-25 fcc=0 lq=230 mode=active rate_rx=202.8/s dev_missing=... last5s=... cum=...
[Paste non-tested hand link line, confirming no link, e.g.]
  RIGHT link: n/a (no ACL link) mode=active rate_rx=0.0/s dev_missing=0 last5s=n/a cum=0.0%

SUMMARY METRICS (over 5-minute window):
- rate_rx mean:        [       ] /s
- dev_missing delta:   [       ]
- last5s_pct mean:     [       ] %
- last5s_pct min-max:  [       ] %
- rssi mean:           [       ] dBm
- lq mean:             [       ] (0..255)

LINKLOG DATA:
[Paste LINKLOG_FIELDS header once and all 60 LINKLOG, lines from the window]
================================================================================
```

---

## 7. Results Table Template (Empty)

| # | Condition | Physical Controller MAC | Hand Slot | Table Pos | Battery (start/end) | Temp raw (start/end) | rate_rx mean | dev_missing delta | last5s_pct mean | min–max | rssi | lq | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **A1** | Baseline | `2C:4D:79:C8:C9:CC` (Phys L) | LEFT | Pos 1 | / | / | | | | | | | |
| **B1** | Baseline | `2C:4D:79:C8:F9:A9` (Phys R) | RIGHT | Pos 2 | / | / | | | | | | | |
| **A2** | Baseline (repeat) | `2C:4D:79:C8:C9:CC` (Phys L) | LEFT | Pos 1 | / | / | | | | | | | |
| **B2** | Baseline (repeat) | `2C:4D:79:C8:F9:A9` (Phys R) | RIGHT | Pos 2 | / | / | | | | | | | |
| **P1** | Position swap | `2C:4D:79:C8:C9:CC` (Phys L) | LEFT | Pos 2 | / | / | | | | | | | |
| **P2** | Position swap | `2C:4D:79:C8:F9:A9` (Phys R) | RIGHT | Pos 1 | / | / | | | | | | | |
| **P3** | Position swap (rep) | `2C:4D:79:C8:F9:A9` (Phys R) | RIGHT | Pos 1 | / | / | | | | | | | |
| **S1** | Hand slot swap | `2C:4D:79:C8:F9:A9` (Phys R) | LEFT | Pos 1 | / | / | | | | | | | |
| **S2** | Hand slot swap | `2C:4D:79:C8:C9:CC` (Phys L) | RIGHT | Pos 2 | / | / | | | | | | | |
| **S3** | Hand slot swap (rep) | `2C:4D:79:C8:F9:A9` (Phys R) | LEFT | Pos 1 | / | / | | | | | | | |
| **S4** | Hand slot swap (rep) | `2C:4D:79:C8:C9:CC` (Phys L) | RIGHT | Pos 2 | / | / | | | | | | | |
