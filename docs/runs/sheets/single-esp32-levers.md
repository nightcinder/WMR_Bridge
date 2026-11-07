# Run sheet: single-ESP32 contention levers (ONY-176)

Status: proposed, not run. Figures from earlier sittings are user-reported. A lever stays `not run` until the owner pastes back its run blocks.

Question: with the single ESP32 on the bench, which remaining contention lever changes Classic report loss, and which is closed by evidence? Work is ordered cheapest first: V1 stagger, Sitting R phases 1-2, BLE airtime, then core/HCI buffers only if diagnostics show host drops.

## 0. What not to re-measure

| Fact | Evidence |
|---|---|
| Both Classic links are master; V3 is a no-op | [`CONTENTION_LEVERS.md`](../../CONTENTION_LEVERS.md) |
| No-sniff V2: 17.55 % / 23.19 % in one 120 s run, inside baseline | same |
| No-phone baseline 9-13 % per hand; LEFT alone ~0.5 %, RIGHT alone ~10.8 % | [`PREDICTION.md`](../../PREDICTION.md) §1 |
| Phone BLE lost 0 records in the analysed 120 s run | same |
| DLE accepted at 251 octets / 2120 us on 2026-10-22 | `BLE data length changed` line |
| Host forwarding is not dropping: `vhci_drop=0`, `log_errors=0`, queue high-water 41/256 | archived `docs/runs/logs/*.txt` |

## 1. Common setup
- Fixed marked controller positions, face down and still, for the session. Record distance, orientation and phone position; a photo is the record.
- Phone: nRF Connect or the Companion, MTU **517**, priority **High**, FF14 only. Do not switch clients inside a block.
- Reset the ESP32 before each run. Wait for `STREAM phone=1 sub=1 stream=batched mtu=517`, then **30 s settle** and a **2-minute window**.
- Record per run: firmware hash and `build-info.json`, position, the BLE parameter request/complete lines, the DLE line, `STREAM`, both `link:` lines, and all 24 `LINKLOG,` rows.
- Discard a run with a disconnect, sleeping controller, missing `LINKLOG` row, `sub=0`, `mtu=23`, or a moved position.
- Compare paired A/B/A blocks in one session. Do not compare absolute loss across boots (15-26 % spread).
- Effect rule: a lever needs **>= 5 points** improvement in both hands against the lowest REF in every pair. **< 3 points** is "no effect shown". The REF repeats must span <= 3 points.

### One-flash LEVERS image (ONY-476)

The runtime keys avoid a reflash between levers. Build with a fresh `--work` so CMake cannot reuse cached flags:

```bash
EXTRA_CFLAGS="-DWMR_CFG_TEST_LEVERS=1" \
  python3 tools/firmware/build.py --out dist \
  --work .fw-ony476-levers \
  --idf-export ~/esp/esp-idf/export.sh
```

Attached image: `wmr-bridge-factory.bin`, 681792 bytes, SHA-256 `687a1831dd51cbbb0beb9767f292be084f8e83c82e6833940ac5a556c397b35e`, source `08fb896`, version `0.2.4-18-g08fb896`. Verify the hash before flashing, or check for the `LEVERS test image` line.

Console: `x` shows defaults; `g` cycles stagger 0/1/2; `i` toggles the 30 ms interval request; `h` toggles FF14 hold 1/2; `p` cycles TX default/P6/P9; `t` reads TX power and DLE. These keys do not exist in a default build.

## 2. Lever 1 — V1 staggered connect

Build REF and the two V1 images from the same commit, each with its own `--work`:

```bash
python3 tools/firmware/build.py --variant both --work .fw-ony176-ref --out dist-ony176-ref
EXTRA_CFLAGS="-DWMR_CFG_STAGGER_FIRST_HAND=1" \
  python3 tools/firmware/build.py --variant both --work .fw-ony176-v1l --out dist-ony176-v1l
EXTRA_CFLAGS="-DWMR_CFG_STAGGER_FIRST_HAND=2" \
  python3 tools/firmware/build.py --variant both --work .fw-ony176-v1r --out dist-ony176-v1r
```

Check `compile_commands.json` for the define; no define means it is REF.

At one fixed position:

| Order | Image | Window |
|---|---|---|
| A1 | REF | both hands, 2 min |
| B1 | V1 LEFT first | first hand alone 2 min after settle, then wake the second, wait for `STAGGER: ... released`, 30 s, both hands 2 min |
| A2 | REF | both hands, 2 min |
| B2 | V1 RIGHT first | as B1 with the other first hand |
| A3 | REF | both hands, 2 min |

Metric: mean `last5s_pct` of the both-hands window; the alone window is the control. Pass needs the both-hands window 5 points below the lowest REF in both hands, without the alone window rising.

## 3. Lever 2 — Sitting R phases 1-2

No reflash. Run [`internal-esp32-wmr-bench.md`](internal-esp32-wmr-bench.md) §6: A1, B1, A2, B2, then P1-P3, with only one controller connected.

| Result | Reading |
|---|---|
| Loss follows physical RIGHT across positions and slots | controller hardware / RF front-end |
| Loss follows Position 2 for either controller | table geometry / antenna orientation |
| Loss follows the RIGHT slot | firmware RIGHT path |
| A1/A2 or B1/B2 span > 3 points | session drifted; discard |

Phase 3 (hand-slot swap) is in the bench sheet §7 and only runs if 1-2 do not separate controller from position.

## 4. Lever 3 — BLE airtime

Build three images:

```bash
# REF: 7.5-15 ms request, hold 1 interval
python3 tools/firmware/build.py --variant both --work .fw-ony176-ref --out dist-ony176-ref
# B1: request 30 ms, hold 1
EXTRA_CFLAGS="-DWMR_CFG_CONN_INTERVAL_MIN=24 -DWMR_CFG_CONN_INTERVAL_MAX=24" \
  python3 tools/firmware/build.py --variant both --work .fw-ony176-i30 --out dist-ony176-i30
# B2: default interval, hold 2
EXTRA_CFLAGS="-DWMR_CFG_BATCH_HOLD_INTERVALS=2" \
  python3 tools/firmware/build.py --variant both --work .fw-ony176-h2 --out dist-ony176-h2
```

Run A1 REF, B1 30 ms, B2 hold 2, A2 REF at the same position, 30 s settle and 2 minutes each. Record the phone's granted interval from `BLE connection parameters complete`; a refused request is not a variant result.

Capture per run: DLE line, parameter line, `notify`/`batches_tx` growth and records per batch, both hands' `last5s_pct`, `rate_rx`, RSSI, LQ, and the `LINKLOG` rows.

Reading: better loss on B1 means connection-event airtime contributes; better on B2 means notification frequency does. If neither moves 5 points, BLE airtime is not a lever at this position. B2 adds about one interval of batching delay; record that cost.

## 5. Lever 4 — core/priority and HCI buffers

Do not run unless diagnostics show host drops. Archived runs have `vhci_drop=0`, `log_errors=0`, queue high-water 41/256 (16 %), and `tx` trails `rx` only by queued records. If a future timed window shows `vhci_drop > 0` or `log_errors > 0`, record that line first, then design the variant.

## 6. Results

| Lever | Status |
|---|---|
| V1 staggered connect | not run; needs the two V1 images and A/B/A block |
| Sitting R phases 1-2 | not run; no reflash |
| DLE, current build | not run; capture the line (2026-10-22 showed 251/2120) |
| 30 ms interval | not run; needs B1 and the phone grant |
| FF14 hold 2 | not run; needs B2 |
| Core/priority, HCI buffers | ruled out by diagnostics |

No lever has a new hardware result, so the best measured configuration is unchanged: default both-hands FF14, MTU 517, granted 15 ms, one-interval hold, 18-25 % dual-hand loss. Next architecture step: multi-bridge or the Pi bridge.

## 7. Paste back per run

```text
RUN ID / LEVER / IMAGE / FIRMWARE HASH / POSITION / PHONE CLIENT / WINDOW
BLE parameter request, parameter complete, data length lines
STREAM line; last LEFT and RIGHT link lines
LEFT and RIGHT last5s mean, min-max, rate_rx, rssi, lq
notify/batches_tx delta, mean records/batch
LINKLOG_FIELDS, then all 24 LINKLOG rows
```
