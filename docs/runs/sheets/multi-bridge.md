# Run sheet: multi-bridge — one ESP32 per controller (issue #18)

Status: proposed, not run. Nothing here is a result. This tests the topology, not a firmware change: two bridges, one hand each, against one bridge carrying both hands.

Question: does one ESP32 per controller remove the extra loss of the two-controller setup, and does RIGHT's own ~10.8 % follow the controller, the position or the board?

Flags: `WMR_CFG_BRIDGE_HANDS` = 0 (both), 1 (LEFT), 2 (RIGHT); `WMR_CFG_BLE_NAME` gives `WMR Bridge`, `WMR Bridge L`, `WMR Bridge R`.

## 0. What the existing runs say

All figures are FF14, MTU 517, phone streaming, `last5s_pct` after 30 s.

| Setup | LEFT | RIGHT |
|---|---|---|
| Both controllers, no phone | 9-13 % | 9-13 % |
| LEFT alone, phone streaming | ~0.5 % | - |
| RIGHT alone, phone streaming | - | ~10.8 % |
| Both connected, same build/position | ~19.6 % | ~19.1 % |
| Both connected, long baseline | 18-19 % | 18-20 % |

LEFT is almost clean alone and loses ~19 % when RIGHT is connected. RIGHT has ~10.8 % of its own and gains ~8 points with LEFT connected. So the sheet tests two things: whether splitting the hands removes the extra pair loss, and where RIGHT's own loss belongs. Multi-bridge cannot be "zero loss": RIGHT's own loss stays unless the controller or position is the cause.

## 1. Prepare two boards

| Label | What it runs |
|---|---|
| **Board A** | REF image in every A run; L image in every B run; R image after S1 |
| **Board B** | R image in every B run; powered **off** in every A run; L image after S1 |

Use two USB ports and two monitors, e.g. COM5 for A and COM6 for B. Both logs are needed at the same time.

### 1.1 Clear the old state

Do one board at a time, with the other powered off:
1. Boot the monitor.
2. `L`/`R` to disconnect any controller.
3. `k`/`K` to delete stored link keys (refused while connected).
4. Put only the controller this board should serve into pairing mode.
5. `F` to forget both addresses and start discovery. `F` is refused while a controller is connected or connecting.
6. Wait for `Discovery:`, `Addresses saved`, `Connecting`, `descriptor status=0x00`, `STREAM ... bridge_hands=1` (A) or `2` (B), and a live `rate_rx` of about 150-200/s.

### 1.2 Board A holds both hands (REF)

Power B off. Flash A with REF (NVS survives). Disconnect (`L`/`R`), clear keys (`k`/`K`), put **both** controllers in pairing mode, press `F`. Wait for both descriptors and `STREAM ... bridge_hands=0`.

### 1.3 Re-pair a controller bonded to the other host

Both halves of the bond matter: the ESP32 key (`k`/`K`) and the controller's own bond (pairing mode clears it). Keep the other board off.
- **Case A, intended board has the address (usual REF↔VAR switch):** power off the old board; on the intended board disconnect the hand and press `k`/`K`; put the controller in pairing mode; press `l`/`r`; wait for descriptor and a live `rate_rx`.
- **Case B, intended board has no address (fresh board or S1 for LEFT):** power off the other board; put the controller in pairing mode; disconnect both hands and press `F`; discovery finds it; pair the other hand again if needed.

Never run discovery on both boards at once. Never press `F` while a controller is connected. If the wrong bridge logs `... excluded ... declining`, that is expected; wake the controller again.

### 1.4 Go / no-go

| Check | Go |
|---|---|
| Board A, B run | `WMR Bridge L`, `bridge_hands=1` |
| Board B, B run | `WMR Bridge R`, `bridge_hands=2` |
| Board A, A run | `WMR Bridge`, `bridge_hands=0`, both hands |
| Excluded hand | `n/a (no ACL link)`, no stream |
| Phone | one row in A runs, two rows in B runs; each `mtu=517`, FF14 |
| `STREAM` per active bridge | `phone=1 sub=1 stream=batched mtu=517` |

Do not time until every row passes.

## 2. Build and flash

All three images come from one commit. `tools/firmware/build.py --variant` sets the flags and gives each variant its own build directory.

```powershell
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1
cd <repo>
git checkout <implementation sha>
$dist = "dist-multi"; $portA = "COM5"; $portB = "COM6"
python tools\firmware\build.py --variant both  --out $dist
python tools\firmware\build.py --variant left  --out $dist
python tools\firmware\build.py --variant right --out $dist
```

Check each `build-info*.json` for variant, hands and name. Check `compile_commands.json` in each build folder for the defines. If a check does not match, stop; that is not the variant.

Flash (NVS is kept, so pairing survives):

```powershell
python tools\firmware\build.py --variant both  --out $dist --flash $portA
python tools\firmware\build.py --variant left  --out $dist --flash $portA
python tools\firmware\build.py --variant right --out $dist --flash $portB
```

After S1 swap the images: `left` to B, `right` to A. Do not write the merged factory image at `0x0` (it erases NVS), and do not run `erase-flash` between runs.

**One-board route (M12.3'):** flash one board with `--variant left` and use only
the LEFT rows. It validates single-hand mode, not the two-bridge comparison.

## 3. Run protocol

### 3.1 Setup
- One fixed marked position: controllers face down and still. Record distance, orientation and phone position; a photo is the record.
- In B runs the bridges are about **1 m apart**, each near its controller.
- Phone: FF14 batched, MTU 517, high priority. Record the app build.
- Confirm `phone=1 sub=1 stream=batched mtu=517` and live `rate_rx`.
- **30 s settle** after the last connection, then a **2-minute window**.
- Metric: `last5s_pct` mean over the 24 `LINKLOG` rows per hand, with min-max. Also record `cum_pct`, `rate_rx`, `dev_missing`, `rssi`, `lq`, `fcc`.
- Reset the active board(s) before each run; NVS survives. Wait for reconnect.
- Discard and repeat a run if a controller or phone disconnects, a `LINKLOG` row is missing, or a field is empty. Say it was discarded.
- Do not run discovery inside a window.

### 3.2 A/B/A/B block

A = one bridge, both hands (REF). B = two bridges, one hand each (VAR). One block is A/B/A/B; two blocks make four pairs.

| Order | Setup | Board A | Board B | Re-pair |
|---|---|---|---|---|
| A1 | REF | REF, on | off | RIGHT -> A if needed; LEFT stays |
| B1 | VAR | L, on | R, on | RIGHT -> B; LEFT stays |
| A2 | REF | REF, on | off | RIGHT -> A |
| B2 | VAR | L, on | R, on | RIGHT -> B |
| A3 | REF | REF, on | off | RIGHT -> A |
| B3 | VAR | L, on | R, on | RIGHT -> B |
| A4 | REF | REF, on | off | RIGHT -> A |
| B4 | VAR | L, on | R, on | RIGHT -> B |
| S1 | swap boards | R, on | L, on | RIGHT -> A; LEFT -> B |
| S2 | swap marked spots | R, on | L, on | none; move controllers |
| X | L bridge off | R, on | L then off | none |

At each A↔B switch, try the power-up first and wait 30 s. Re-pair only if a hand lands on the wrong bridge.

### 3.3 Per run

Reset the board(s); re-pair if needed; open a monitor per active port; connect the phone, select FF14, confirm `STREAM`; wait 30 s; run 2 minutes. In B runs take LEFT rows from A and RIGHT rows from B.

After each B window, run a **120 s latency export** on the first-connected bridge (B1/B3 L first, B2/B4 R first). It belongs to that bridge only. Then do the reconnect check on each bridge: sleep its controller, confirm `rate_rx=0`, wake it, confirm HID accept, descriptor and `rate_rx` resume for 10 s.

S1: flash A with R and B with L. With B alone, clear it and discover LEFT (Case B). With A alone, `K`, then `r` (Case A). Confirm the two bridge rows. S2: swap the controllers' marked spots; no re-pair expected. X: run a normal VAR window, then switch off the L bridge and run a RIGHT-only window. A fall of 3 points or more means some RIGHT loss is shared air or phone work; no change means it is local.

### 3.4 Repeats

The four REF means must span **3 points or less**, or the session drifted. Effects under 3 points are "not shown"; 5 points is the smallest this design detects. A pass needs a second session on another power-up with the same direction. One sitting is about 2 h.

## 4. Exit criteria (fixed before data)
- **Pass:** every VAR window has LEFT <= 3 % and >= 10 points below the lowest REF LEFT, RIGHT <= 13 % and >= 5 points below the lowest REF RIGHT; the four REF means span <= 3 points; the same direction holds in a second session; both reconnect checks pass; phone sequence gaps = 0 on both bridges.
- **Refute:** LEFT on its own bridge stays >= 10 % while the other bridge runs; then the loss is in the air or on the phone side.
- **RIGHT's own loss:** follows the board -> board/antenna; follows the spot -> position; stays with the controller -> controller.

## 5. Paste back

**Once per session:** build record table, boot banners, `bridge_hands`, pairing
lines (say Case A or B), photos of marks and board labels.

**Per run:** the whole monitor file, `LINKLOG_FIELDS`, all 24 `LINKLOG,` rows
per hand, `link:` lines, `STREAM` at start and end, reconnect lines, wall-clock and `t_ms` span.

**Per B run:** the three latency files, which bridge was connected first, the
JSON RTT/link fields and per-hand sequence gaps, the Connect screen rows, and the reconnect record.

**S1/S2/X:** the same plus where each board and controller was.

Results table: one row per hand per window, with image, board, `bridge_hands`, mean, min-max, `cum_pct`, rate, `dev_missing`, RSSI, LQ, FCC, gaps, mode.

## 6. Not covered
- V1 stagger, sniff, role or QoS: [`single-esp32-levers.md`](single-esp32-levers.md) and [`CONTENTION_LEVERS.md`](../../CONTENTION_LEVERS.md).
- RIGHT's own loss is not settled by one session; a second is required.
- The app does not measure per-bridge RTT in one export.
