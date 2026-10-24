# Run sheet: single-ESP32 contention levers (ONY-176)

> **Proposed sheet, not yet run.** Nothing in this file is a hardware result. The
> figures quoted from earlier sittings are user-reported; every pass rule below is
> fixed before any new data. The agent cannot flash the board or operate the
> controllers, so a lever stays **not run** until the owner pastes back its run
> blocks.

Question: with the single ESP32 already on the bench, which remaining contention
lever changes Classic report loss, and which is closed by evidence? This is the
follow-up to ONY-170 ([`PREDICTION.md`](../../PREDICTION.md) §2.5). The default,
V1=1, V1=2, 30 ms interval and hold=2 images compile on this branch
(`idf.py build`, ESP-IDF v6.1); compilation is not a hardware result. Work is
ordered cheapest first:

1. V1 staggered connect (`WMR_CFG_STAGGER_FIRST_HAND=1/2`).
2. RIGHT-asymmetry phases 1-2 (no reflash): controller, hand slot, table position.
3. BLE airtime: DLE confirmation, a longer requested connection interval, and a
   longer FF14 batch hold.
4. Controller task core/priority and HCI buffers, only if the diagnostics show
   host-side drops.

## 0. What is already known (do not re-measure)

| Fact | Evidence |
|---|---|
| Both Classic links are master on the ESP32; V3 is a no-op | [`contention-2-results.md`](contention-2-results.md) §1 |
| No-sniff V2 showed no effect in one 120 s run (17.55 % / 23.19 %) | [`contention-2-results.md`](contention-2-results.md) §2 |
| No-phone baseline is 9-13 %; LEFT alone ~0.5 %, RIGHT alone ~10.8 % | [`single-controller.md`](single-controller.md), [`contention-2.md`](contention-2.md) §0 |
| The phone's BLE link lost 0 records in the analysed 120 s run | [`PREDICTION.md`](../../PREDICTION.md) §1 |
| DLE was accepted at 251 octets / 2120 µs on 2026-10-22 | `BLE data length changed: tx 251 octets / 2120 us, rx 251 octets / 2120 us`, [`../logs/ff14-vs-ff12-abba-linklog.txt`](../logs/ff14-vs-ff12-abba-linklog.txt) line 139 |
| Host-side forwarding is not dropping: `vhci_drop=0`, `log_errors=0`, queue high-water 41/256 over the archived logs | `docs/runs/logs/*.txt`; e.g. `single-controller-both-connected.txt` line 198 |

These bound the possible gain but do not replace the runs below.

## 1. Common setup and recording rules

Every timed run:

- Same marked controller positions, face down and still, for the whole session.
  Note the distance to the ESP32 and phone, orientation and the phone position;
  do not move the board or phone between paired runs. A photo is the position
  record.
- Phone: nRF Connect (reference client) or the companion app, connect to
  `WMR Bridge`, **Request MTU 517**, **connection priority High**, notify on
  **FF14 only**. Do not switch clients inside a block.
- Reset the ESP32 (monitor `Ctrl+T`, `Ctrl+R`) before each run, wait for a live
  `STREAM phone=1 sub=1 stream=batched mtu=517`, then **30 s settle** and a
  **2-minute window**.
- Record per run: firmware hash (`git rev-parse HEAD` and the `build-info.json`),
  position, the `BLE connection parameter update requested` line, the
  `BLE connection parameters complete` line, the `BLE data length changed` line,
  the `STREAM` line, both `LEFT link:` / `RIGHT link:` lines, and all 24
  `LINKLOG,` rows (plus `LINKLOG_FIELDS` once).
- Discard a run with a disconnect, a sleeping controller, a missing `LINKLOG`
  row, `sub=0`, `mtu=23`, or any change to the position.
- Comparisons are paired A/B/A blocks in one session. Absolute loss is not
  compared across boots: the reported between-boot spread is 15-26 %.
- Effect rule (same as the earlier sheets): a lever needs **≥ 5 percentage
  points** improvement in both hands against the lowest REF in every pair; a
  difference **< 3 points** is "no effect shown"; anything else is
  "inconclusive". A verdict needs the REF repeats to span ≤ 3 points.

## 2. Lever 1 — V1 staggered connect

The code exists in `src/wmr_controller.c`; it defaults off and does not change
the default build. Build REF and the two V1 images from the same commit. Use a
separate `--work` directory per image so ESP-IDF cannot reuse a previous
`EXTRA_CFLAGS` configuration.

```bash
# REF (default, flag off)
python3 tools/firmware/build.py --variant both --work .fw-ony176-ref --out dist-ony176-ref

# V1 LEFT first
EXTRA_CFLAGS="-DWMR_CFG_STAGGER_FIRST_HAND=1" \
  python3 tools/firmware/build.py --variant both --work .fw-ony176-v1l --out dist-ony176-v1l

# V1 RIGHT first
EXTRA_CFLAGS="-DWMR_CFG_STAGGER_FIRST_HAND=2" \
  python3 tools/firmware/build.py --variant both --work .fw-ony176-v1r --out dist-ony176-v1r
```

Windows PowerShell: set `$env:EXTRA_CFLAGS = "-DWMR_CFG_STAGGER_FIRST_HAND=1"`
before the build and `Remove-Item Env:EXTRA_CFLAGS` after it. Check
`compile_commands.json` in the build folder contains the define; a build without
it is the REF image.

Flash order and windows, at one fixed position:

| Order | Image | Window |
|---|---|---|
| A1 | REF | both hands, 2 min |
| B1 | V1 LEFT first | first hand alone 2 min (after 30 s settle), then wake the second hand at the mark; wait for `STAGGER: ... released`, 30 s settle, both hands 2 min |
| A2 | REF | both hands, 2 min |
| B2 | V1 RIGHT first | as B1, with the other first hand |
| A3 | REF | both hands, 2 min |

Use [`contention-2.md`](contention-2.md) §3-§4 for the gate checks. The metric is
the mean `last5s_pct` of the **both-hands** window; the alone window is the
control. A pass needs the both-hands window at least 5 points below the lowest
REF in both hands, without the alone window rising. Only then is a second
session justified.

## 3. Lever 2 — RIGHT asymmetry, phases 1-2 only

No reflash. Run [`right-asymmetry.md`](right-asymmetry.md) §4 **Phase 1 (A1, B1,
A2, B2)** and **Phase 2 (P1, P2, P3)**, under its §1 isolation rule (only one
controller connected at a time; the other has no battery or is powered off).
Skip Phase 3 unless phases 1-2 do not separate the controller from the table
position; only Phase 3 needs the address-swap build.

Attribution uses the table already fixed in `right-asymmetry.md` §5:

| Result | Reading |
|---|---|
| Loss follows physical RIGHT across positions and hand slots | controller hardware/RF front-end |
| Loss follows Position 2 for either controller | table geometry / antenna orientation |
| Loss follows the RIGHT hand slot | firmware RIGHT path |
| A1/A2 or B1/B2 span > 3 points | session drifted; discard |

Phase 1-2 answers the product blocker without a flash.

## 4. Lever 3 — BLE airtime

DLE is already logged by `wmr_transport.c`. `WMR_CFG_CONN_INTERVAL_MIN/MAX`
already request a longer interval. ONY-176 adds one knob,
`WMR_CFG_BATCH_HOLD_INTERVALS` (default 1; value 2 is the variant), so the
default FF14 hold stays one connection interval.

Build three images from the same commit:

```bash
# REF: request 7.5-15 ms, hold 1 interval
python3 tools/firmware/build.py --variant both --work .fw-ony176-ref --out dist-ony176-ref

# B1: request 30 ms (24 x 1.25 ms), hold 1 interval
EXTRA_CFLAGS="-DWMR_CFG_CONN_INTERVAL_MIN=24 -DWMR_CFG_CONN_INTERVAL_MAX=24" \
  python3 tools/firmware/build.py --variant both --work .fw-ony176-i30 --out dist-ony176-i30

# B2: default interval request, hold 2 intervals
EXTRA_CFLAGS="-DWMR_CFG_BATCH_HOLD_INTERVALS=2" \
  python3 tools/firmware/build.py --variant both --work .fw-ony176-h2 --out dist-ony176-h2
```

Run A1 (REF), B1 (30 ms request), B2 (hold 2), A2 (REF) at the same position,
each with the same phone and 2-minute window after a 30 s settle. The phone may
refuse or grant its own interval: record the granted value from
`BLE connection parameters complete` and do not treat a refused request as a
variant result.

For each run capture and compare:

- `BLE data length changed: tx ... octets / ... us, rx ...` (DLE accepted or not);
- `BLE connection parameters complete: ...` (granted interval, latency, timeout);
- total `notify`/`batches_tx` growth per window and mean records per batch;
- both hands' `last5s_pct`, `rate_rx`, `rssi`, `lq`;
- the full `LINKLOG,` rows.

Reading: better loss with B1 means BLE connection-event airtime contributes; better
loss with B2 means notification frequency does. If neither moves by 5 points, BLE
airtime is not a single-ESP32 lever at this position. B2 adds about one interval
(15 ms at the granted 15 ms) of batching delay; record that cost rather than
calling it free.

## 5. Lever 4 — controller task core/priority and HCI buffers

**Do not run unless the diagnostics show host-side drops.** The archived runs
show `vhci_drop=0` and `log_errors=0` throughout, with `tx == rx` and a queue
high-water of 41/256 (16 %) in the worst archived run. That rules out host-side
queue/HCI-buffer loss as the cause of the missing reports, so core pinning,
priority changes and larger HCI buffers have no measured prerequisite and are
**not run**. If a future run shows `vhci_drop > 0` or `log_errors > 0` in a
timed window, record that line first and only then design the core/priority
variant.

## 6. Results

| Lever | Status | Result / prerequisite |
|---|---|---|
| V1 staggered connect | **Not run** | Needs the two V1 images and the A/B/A block in §2; no new code needed |
| RIGHT asymmetry phases 1-2 | **Not run** | Needs one unflashed session under `right-asymmetry.md` §4; phase 3 only if 1-2 do not separate |
| DLE confirmation (current build) | **Not run** | Earlier log shows 251/2120 accepted; needs the line captured again on this build |
| Longer requested interval (30 ms) | **Not run** | Needs the B1 image and phone grant |
| Longer FF14 hold (2 intervals) | **Not run** | Needs the B2 image; knob added in ONY-176 |
| Controller core/priority, HCI buffers | **Ruled out by diagnostics** | `vhci_drop=0`, `log_errors=0`, queue high-water 41/256; no run |

Best measured configuration: **unchanged by ONY-176** because no lever has a new
hardware result. The best measured single-ESP32 configuration remains the
default both-hands FF14 stream at MTU 517 with the granted 15 ms interval and a
one-interval batch hold; its measured dual-hand loss is 18-25 % per hand
([`PREDICTION.md`](../../PREDICTION.md) §1), and no tested variant has beaten it.
Next architecture step: one bridge per hand (multi-bridge) and/or the Pi Wi-Fi
host bridge to remove the BLE/phone link from the ESP32 radio; neither has run.

## 7. Paste-back block (one per run)

```text
================================================================================
RUN ID:            [A1 / B1 / ...]
LEVER:             [V1 / RIGHT-asymmetry / interval / batch-hold]
IMAGE:             [REF / V1-left / V1-right / i30 / h2]
FIRMWARE HASH:     [git rev-parse HEAD + build-info.json variant]
POSITION:          [marked spot, distances to ESP32 and phone, orientation]
PHONE/CLIENT:      [nRF Connect or app version]
WINDOW:            [settle end ms .. stop ms; 24 LINKLOG rows]

BLE LINES:
BLE connection parameter update requested: [...]
BLE connection parameters complete: [...]
BLE data length changed: [...]

STREAM:
[last STREAM line]

LINKS:
[last LEFT link: and RIGHT link: lines]

SUMMARY:
LEFT  last5s mean [ ] %  min-max [ ] %  rate_rx [ ]/s  rssi [ ]  lq [ ]
RIGHT last5s mean [ ] %  min-max [ ] %  rate_rx [ ]/s  rssi [ ]  lq [ ]
notify/batches_tx delta [ ]   mean records/batch [ ]

LINKLOG_FIELDS
[header once]
LINKLOG,...
[all 24 rows]
================================================================================
```
