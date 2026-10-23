# Hardware runs and run sheets

Raw logs and run sheets from the 2026-10-22 bench sessions (everything here is user-reported: the project owner ran the hardware, the assistant analysed the logs). Device addresses are replaced by `ADDR-n`. Figures and conclusions are in the app repo's `docs/STATUS.md` and `docs/LATENCY.md`; this folder holds the raw material so the numbers can be re-derived.

## Run sheets (`sheets/`)

| File | Purpose | State |
|---|---|---|
| `ff14-ab.md` | FF14 against FF12, A/B/A | run |
| `left-coex.md` | Auto latency and modem sleep off on LEFT | run, no effect shown |
| `left-baseline-repeat.md` | How far LEFT drifts with nothing changed | long baseline run |
| `single-controller.md` | LEFT alone, RIGHT alone, both | run |
| `contention-2.md` | Staggered connect and Classic link policy variants for issue #18 | run sheet (variants V2, V3 tested) |
| `contention-2-results.md` | Results of contention-2 runs (role check, V2 no-sniff, RTT separation) for issue #18 | **run** (user-reported): V3 no-op (master confirmed), V2 sniff=clear 17.5%/23.2% (no effect shown), ~40 ms RTT is BLE stack bound |
| `closeout.md` | Checks still not run (MTU on the Connect screen, gamepad checks, settings persistence, HID, C7, round-trip rerun) | **not run** |
| `robustness.md` | Remaining TESTING.md items in one sitting: still-required 2–6, 9, 11; FF12 5–7, 11, 12, 15; B4, B6–B8, B10, B11 | **not run** |

## Logs (`logs/`)

Best-effort labels; the file names were assigned when the files were archived.

| File | What it is |
|---|---|
| `ff14-vs-ff12-abba-linklog.txt` | Run 0 (no phone) then FF12/FF14 runs, `LINKLOG` lines and `STREAM` blocks |
| `left-coex-runs-1-4.txt`, `left-coex-runs-5-8.txt` | The LEFT coexistence runs (auto latency, modem sleep), FF14, MTU 517 |
| `long-baseline-monitor.txt` | Long baseline monitor capture (about 25 minutes; controllers fell asleep eventually; the phone subscribed part-way) |
| `single-controller-left-only.txt` | LEFT alone, FF14, MTU 517 (RIGHT asleep), on the PR #21 firmware: LEFT about 203/s, `last5s` mean about 0.5 % |
| `single-controller-right-only.txt` | RIGHT alone: about 182/s, `last5s` mean about 10.8 % |
| `single-controller-both-connected.txt` | Both connected, same session: about 19 % each |
| `invalid-left-only-phone0-monitor.txt` | An invalid attempt (`phone=0`, controllers silent); kept so nobody trusts a half-run again |

## Round trip (`roundtrip/`)

The first FF15 round-trip run (120 s, FF14, MTU 517, 15 ms), exported by the app: summary JSON, per-ping echo CSV, per-sample CSV. 479 of 479 pings answered, round trip p50 40.8 ms, p99 97.1 ms.

## Reading the logs

`LINKLOG_FIELDS` gives the column names of each `LINKLOG,` line (`last5s_pct` is the share of controller reports missing over 5 s, `dev_missing` comes from controller tick deltas). Some files were saved as UTF-16 by PowerShell and have been converted to UTF-8.
