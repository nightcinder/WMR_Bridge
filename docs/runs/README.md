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

The minimum run is `docs/runs/sheets/bench.md`: a 12-step, about 24 minute
pass/fail checklist on a Raspberry Pi 3B+ and an ESP32 with WMR controllers
and a Pixel 11 Pro. It covers the checks that still gate something real:
ONY-1058 (keep-alive and idle sleep/power-off) and ONY-1117 (v0.3.16
release). Optional expansions are listed at the end of that sheet.

External testers use `docs/runs/sheets/external-test-user.md`.

Before the first sitting: record the firmware hash and boot banner once; install
the companion APK; use one phone client at a time; keep the phone on a charger.
The browser installer needs Chrome or Edge and a served `dist/`; otherwise use
`build.py`. If a sheet's prerequisite is false, stop and report the sitting as
not run, not as a failure.

## Run sheets (`sheets/`)

| File | Purpose | State |
|---|---|---|
| [bench.md](sheets/bench.md) | 12-step Pi 3B+ and ESP32 pass/fail checklist, about 24 min | not run |
| [external-test-user.md](sheets/external-test-user.md) | External tester Tier 0 quickstart with Tier 1 diagnostics and Tier 2 device evidence | template, not run |

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