# FF15 round-trip exports

Raw exports from the companion app's latency screen, kept so the figures in
[`wmr-companion` `docs/LATENCY.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/LATENCY.md)
can be re-derived. Everything here is user-reported: the project owner ran the
hardware and exported the files; the assistant only analysed them.

Five runs are archived: two FF14 runs (2026-10-22 and 2026-10-24) and the
2026-10-24 ladder pass (none, FF11 raw, FF12 decoded). All are 120 s, 250 ms
ping interval, both controllers on one bridge, MTU 517 and a 15 ms connection
interval. Each run has three files: the app's JSON summary, the per-ping echo
CSV and the per-record frame CSV (the app exports the last one as
`latency-<stamp>.csv`; it is stored here as `-samples.csv` to match the
2026-10-22 archive).

| Run | Stream | Files | Round trip p50 / p99 / max | Pings over 50 ms | Tick loss LEFT / RIGHT | RSSI start → end |
|---|---|---|---|---|---|---|
| 2026-10-22 14:56:31 | FF14 batched | `latency-20261022-145631{,-echo,-samples}.csv`, `.json` | 40.8 / 97.1 / 151.9 ms | 117 / 479 | 25.1 % / 17.8 % | −69 → −48 dBm |
| 2026-10-24 18:43:35 | FF14 batched | `latency-20261024-184335{,-echo,-samples}.csv`, `.json` | 40.5 / 102.5 / 125.3 ms | 121 / 479 | 21.0 % / 15.2 % | −86 → −81 dBm |
| 2026-10-24 20:11:49 | none | `latency-20261024-201149{,-echo,-samples}.csv`, `.json` | 61.6 / 106.4 / 151.8 ms | 438 / 479 | — (no stream) | −80 → −80 dBm |
| 2026-10-24 20:14:40 | FF11 raw | `latency-20261024-201440{,-echo,-samples}.csv`, `.json` | 44.8 / 119.9 / 150.0 ms | 191 / 479 | — (raw frames) | −84 → −86 dBm |
| 2026-10-24 20:16:56 | FF12 decoded | `latency-20261024-201656{,-echo,-samples}.csv`, `.json` | 44.3 / 124.7 / 195.3 ms | 194 / 479 | 30.7 % / 17.6 % | −88 → −87 dBm |

The firmware/app pair is in the last column of the table in the companion
`docs/LATENCY.md`: the 2026-10-22 run predates the `0.1.0` builds (firmware
`203108c`, companion `3df70a4`); every 2026-10-24 run is WMR_Bridge `v0.1.1`
with the wmr-companion `0.1.0` app, reported by the owner. No export records a
commit hash.

Notes for re-deriving the runs:

- The 2026-10-22 JSON is `wmr-latency` version 2 (one `link` object, no
  `bridge_id` per hand); every 2026-10-24 JSON is version 3 (one `link` entry
  per bridge, `bridge_id` per hand). The schemas are described in the companion
  `docs/LATENCY.md` "Export" section.
- The four 2026-10-24 runs are one sitting. The owner states the phone, bridge
  and controllers were not moved. RSSI is not a position record: the app reads
  it twice per run (`rssi_start_dbm` at Start, `rssi_end_dbm` at the automatic
  stop), each a single HCI reading at 1 dB resolution, so several dB of spread
  at an unchanged position is expected. The 2026-10-22 position did move
  (21 dB); none of the runs is a taped-position baseline.
- The none run has no stream records: its frame CSV is the header only and both
  hand summaries are zeros. Its rtt floor is 45.0 ms against 15.3–18.6 ms in
  the three streamed runs, and 438 of 479 pings took more than 50 ms against
  121–194. That reproduces the direction of the old ~59 ms no-stream figure
  (61.6 ms here) and fits the slave-latency explanation, but it is one run.
- The FF11 run's RIGHT records start 35.1 s in (an 85 s span); its RIGHT
  stalls, gaps and rate are therefore not full-run figures. Raw frames carry no
  device ticks, so FF11 has no tick loss.
- The FF11 and FF12 medians sit 3.8–4.3 ms above FF14 in this sitting, but the
  runs are sequential rather than interleaved and the RSSI samples differ by up
  to 8 dB. That is not a clean stream effect and is not reported as one.
- The echo CSV holds the per-ping figures (write-call time, queue depth the
  answer skipped, ESP32 turnaround, clock-offset estimate); the frame CSV holds
  the per-record arrivals and device ticks. Percentiles in the JSON are
  nearest-rank over the whole run.
