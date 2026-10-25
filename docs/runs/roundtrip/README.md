# FF15 round-trip exports

Raw exports from the companion app's latency screen, kept so the figures in
[`wmr-companion` `docs/LATENCY.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/LATENCY.md)
can be re-derived. Everything here is user-reported: the project owner ran the
hardware and exported the files; the assistant only analysed them.

Both runs are 120 s, FF14 batched, 250 ms ping interval, both controllers
connected to one bridge, MTU 517 and a 15 ms connection interval. Each run has
three files: the app's JSON summary, the per-ping echo CSV and the per-record
frame CSV (the app exports the last one as `latency-<stamp>.csv`; it is stored
here as `-samples.csv` to match the 2026-10-22 archive).

| Run | Files | Firmware / app | Round trip p50 / p99 / max | Tick loss LEFT / RIGHT |
|---|---|---|---|---|
| 2026-10-22 14:56:31 | `latency-20261022-145631{,-echo,-samples}.csv`, `.json` | Firmware `203108c`, companion `3df70a4` (pre-`0.1.0` builds; no hash in the export) | 40.8 / 97.1 / 151.9 ms | 25.1 % / 17.8 % |
| 2026-10-24 18:43:35 | `latency-20261024-184335{,-echo,-samples}.csv`, `.json` | WMR_Bridge `v0.1.1`, wmr-companion `0.1.0` (owner-reported; no hash in the export) | 40.5 / 102.5 / 125.3 ms | 21.0 % / 15.2 % |

Notes for re-deriving either run:

- The 2026-10-22 JSON is `wmr-latency` version 2 (one `link` object, no
  `bridge_id` per hand); the 2026-10-24 JSON is version 3 (one `link` entry per
  bridge, `bridge_id` per hand). The schemas are described in the companion
  `docs/LATENCY.md` "Export" section.
- RSSI is in each JSON's `link` block: -69 to -48 dBm on 2026-10-22 and -86 to
  -81 dBm on 2026-10-24. The 2026-10-22 position moved; the 2026-10-24 position
  was not recorded. Neither run may be treated as a fixed-position comparison.
- The echo CSV holds the per-ping figures (write-call time, queue depth the
  answer skipped, ESP32 turnaround, clock-offset estimate); the frame CSV holds
  the per-record arrivals and device ticks. Percentiles in the JSON are
  nearest-rank over the whole run.
