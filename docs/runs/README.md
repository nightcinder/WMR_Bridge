# Hardware runs and run sheets

Raw logs and run sheets from the 2026-10-22 bench sessions. Everything here is
user-reported: the owner ran the hardware, the assistant analysed the logs.
Device addresses are replaced by `ADDR-n`. Figures and conclusions are in
`docs/STATUS.md` and `docs/PREDICTION.md`. This folder keeps the raw material
so the numbers can be re-derived.

## Verification campaign, start here

Status: none of these sittings has run. This is a running order, not evidence.
A sitting stays not run until the owner pastes back its figures. A green CI
build is not a hardware result.

The order is reflash cost, cheapest first. Sittings that run on the current
image come first, then one-reflash sittings, then the close-out. Sitting P needs
no reflash but needs a Raspberry Pi the owner does not yet have. Sitting B needs
a second Classic ESP32 and is parked.

| Sitting | What it settles | Build | Paste back | Write-up |
|---|---|---|---|---|
| A, latency v2 + tracking quality<br>[latency-2 (companion)](https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/latency-2.md) + [tracking-quality.md](sheets/tracking-quality.md) | M7 Flutter-vs-native decision (closed: Stay Flutter under the ONY-1010 amendment — warm/stable start, one-sided `d <= +5 ms`, stalls within 25 %; 2026-11-07 figures in [roundtrip/README.md](roundtrip/README.md)); M8 fusion defaults (calibration, 90° turns, yaw drift); #19 raw trigger, trackpad, battery, temperature. Does not test #18 | No reflash. Both-hands `main` after the FF13 streaming fix, `bridge_hands=0`. Companion debug APK | latency-2 rule/protocol; tracking-quality §10; iOS v1 files from companion `LATENCY.md` | Companion `STATUS.md` milestone 5; tracking → this sheet §11, then `wmr-companion/docs/STATUS.md` and `DECODING.md` |
| P, Raspberry Pi host bridge<br>[pi-bridge.md](sheets/pi-bridge.md) | Classic BT vs ESP32 upstream loss (P6); Wi-Fi streaming, dual controllers, 5 GHz quality, reconnects, fan-out, soak | No firmware flash. Pi OS 64-bit, package, Python 3.10+, BlueZ `input`, `hidp`; companion Wi-Fi entry or `wifi_probe.py` | pi-bridge §5: host metadata, P1-P11, P6 loss per hand | `pi-bridge.md` §5; `PI_BRIDGE.md`; `STATUS.md` |
| L, single-ESP32 levers<br>one board<br>[single-esp32-levers.md](sheets/single-esp32-levers.md) | Whether V1 stagger, Sitting R phases 1-2, DLE/interval/hold, or host buffers move loss. Core/priority is ruled out by diagnostics | V1 needs two images; interval/hold needs B1/B2; Sitting R needs no reflash | §7 per-run block, all 24 `LINKLOG,` rows | `single-esp32-levers.md` §6; `STATUS.md`; `PREDICTION.md` §2.5 |
| R, RIGHT-alone asymmetry<br>one board, no purchase<br>[internal-esp32-wmr-bench.md](sheets/internal-esp32-wmr-bench.md) §6-§7 | Does RIGHT's ~10.8 % follow the controller, the table position or the firmware hand slot? Phase 3 is the address-swap reflash | Phases 1-2: no reflash. Phase 3: address-override `EXTRA_CFLAGS` build | Bench §8: A1-B2/P1-P3 and S1-S4 means, attribution | Bench §6-§7; `STATUS.md` #18 |
| B′, one board, `--variant left`<br>[multi-bridge.md §2](sheets/multi-bridge.md) | Does single-hand mode boot, advertise `WMR Bridge L`, and stream? One-board smoke test only; #18 stays open | One reflash: `build.py --variant left --flash COM5` | multi-bridge §5 build record and one-board checks | `STATUS.md` single-hand row; `multi-bridge.md` |
| C, robustness + close-out<br>[robustness.md](sheets/robustness.md) + [GAMEPAD.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/GAMEPAD.md) | Remaining TESTING.md items (10 min FF12, 1-hour FF14 soak, reconnects, bond reset, MTU/CCC/switching) plus MTU screen, gamepad mapping/persistence, HID, C7 (FF13 off), round-trip rerun | One both-hands flash; C7 needs one extra flash and a restore | robustness §12 and each section's paste-back | `robustness.md` §12; `STATUS.md`; companion `STATUS.md` |
| B, two bridges<br>parked, not run<br>[multi-bridge.md](sheets/multi-bridge.md) | #18: does one bridge per controller remove the extra pair loss, and what RIGHT's own loss follows? Needs a second Classic ESP32; an S3 cannot substitute | Three images from one commit, two boards, two ports | multi-bridge §5 | `multi-bridge.md`; `STATUS.md` #18 |

Before the first sitting: record the firmware hash and boot banner once; install
the companion APK; use one phone client at a time; keep the phone on a charger.
The browser installer needs Chrome or Edge and a served `dist/`; otherwise use
`build.py`. If a sheet's prerequisite is false, stop and report the sitting as
not run, not as a failure.

The campaign closes at M12.5: every pass criterion answered with a figure or
explicitly declined, then the #18 disposition, M8 defaults and `STATUS.md` in
both repos written up.

## Run sheets (`sheets/`)

| File | Purpose | State |
|---|---|---|
| [internal-esp32-wmr-bench.md](sheets/internal-esp32-wmr-bench.md) | Internal bench: FF16/FF17/stick, ONY-446, HID, LED, Sitting R phases 1-3 | runbook, not run |
| [external-test-user.md](sheets/external-test-user.md) | External tester Tier 0 quickstart with Tier 1 diagnostics and Tier 2 device evidence | template, not run |
| [multi-bridge.md](sheets/multi-bridge.md) | One ESP32 per controller, A/B/A/B, S1/S2/X | not run; parked |
| [pi-bridge.md](sheets/pi-bridge.md) | Linux / Raspberry Pi Wi-Fi host bridge P1-P11 | not run |
| [robustness.md](sheets/robustness.md) | Remaining TESTING.md items plus the folded close-out checks | not run |
| [single-esp32-levers.md](sheets/single-esp32-levers.md) | V1 stagger, Sitting R phases 1-2, BLE interval/hold | not run |
| [tracking-quality.md](sheets/tracking-quality.md) | Static calibration, 90° turns, yaw drift, #19 unknowns | not run |
| [android-xr-6dof-20261107.md](sheets/android-xr-6dof-20261107.md) | External Android XR 6DoF-only bench bundle: triage, pose inventory, repeated anchor frames, and why it is not a tuning corpus | run (external bundle) |

## Logs (`logs/`)

Best-effort labels; names were assigned when the files were archived.

| File | What it is |
|---|---|
| `ff14-vs-ff12-abba-linklog.txt` | Run 0 (no phone), then FF12/FF14 runs |
| `left-coex-runs-1-4.txt`, `left-coex-runs-5-8.txt` | LEFT auto-latency / modem-sleep runs, FF14, MTU 517 |
| `long-baseline-monitor.txt` | About 25 min with nothing changed |
| `single-controller-left-only.txt` | LEFT alone: about 203/s, `last5s` about 0.5 % |
| `single-controller-right-only.txt` | RIGHT alone: about 182/s, about 10.8 % |
| `single-controller-both-connected.txt` | Both connected: about 19 % each |
| `invalid-left-only-phone0-monitor.txt` | Invalid attempt (`phone=0`); kept as a warning |

## Round trip (`roundtrip/`)

Five FF15 round-trip runs (120 s, MTU 517, 15 ms, both controllers). 2026-10-22,
FF14: 479/479 answered, p50 40.8 ms, p99 97.1 ms. 2026-10-24 on WMR_Bridge
`v0.1.1`: FF14 479/479, p50 40.5 ms, p99 102.5 ms; FF11 p50 44.8 ms; FF12 p50
44.3 ms; none p50 61.6 ms. File inventory and caveats:
[`roundtrip/README.md`](roundtrip/README.md).

## Reading the logs

`LINKLOG_FIELDS` gives the columns of each `LINKLOG,` line (`last5s_pct` is the
share of controller reports missing over 5 s, `dev_missing` comes from tick
deltas). Some files were saved as UTF-16 by PowerShell and converted to UTF-8.
