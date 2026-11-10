# Testing status

This page separates what has run from what is still required. A check is only
`reported` when the owner supplied figures; a green CI build is not a hardware
result. Detailed procedures are in the run sheets and the internal bench.

## Host tests (run)

```sh
./tests/run_c_tests.sh
```

CI runs this list (`host-tests`): one C99 build and run per test with
`-Wall -Wextra -Wconversion -Werror`. `host-tests-sanitizers` runs the same with
ASan and UBSan.

The codec tests cover notification capacity at MTU 0-517, golden fragments,
encode/reassemble at every capacity, fragmented and interleaved frames,
malformed input and recovery, queue order/full/wraparound/purge, and all sample
frames. `test_secure.c` covers pairing, stale-key recovery, timeout and the
single-central rule. `test_batch.c` covers batch bytes, the MTU rule, flush and
exclusion. `test_echo.c`, `test_discovery.c`, `test_cfg.c`, `test_diag.c` and the
BTstack-side fakes cover their protocols. Pure-C modules also build as
`libwmr_codec.a`:

```sh
cmake -B build && cmake --build build && ctest --test-dir build --output-on-failure
```

The tests exercise host code only. They do not cover BTstack integration,
callbacks, timers or radio.

## Hardware run so far

| Check | Result |
|---|---|
| Firmware build, ESP-IDF v6.1, esp32 | passed, no warnings |
| `sdkconfig` unchanged by build | confirmed |
| Boot, 30 s, no controllers or phone | passed: stack up, advertising, status block |
| Both controllers + phone, about 15 s | passed: MTU 23 then 517, both HID accepted, rates 174/s LEFT and 154/s RIGHT, `drop=0`, queue `high=11` |

Not covered by that session: more than 15 s, reconnects, bond reset, or any
phone-side check.

## Still required (hardware)

Run with the current firmware and record dates and figures.

1. Boot and advertising: done.
2. Both controllers + phone subscribed for at least 10 min: `rate_tx` tracks
   `rate_rx`; `drop` flat; `q` low; accounting holds.
3. Incoming connections: power-cycle each controller; it reconnects and streams.
4. Outgoing `l`/`r`, one hand at a time.
5. Controller disconnect/reconnect while subscribed: the other hand keeps
   flowing and sequence continues.
6. Phone disconnect/reconnect: advertising restarts; MTU/priority/CCC are
   re-established; no duplicate connect line.
7. Large MTU + high priority: log the phone's negotiated MTU and compare.
8. Phone-side sequence gaps: no gap-checking receiver yet; future code.
9. Manual bond reset `k`/`K`: refused while connected, works while
   disconnected, controller re-pairs.
10. Capture labelled LEFT/RIGHT sample frames for `samples/`.
11. Long soak (hours): watch `errors`, `drop`, heap and uptime. Heap is not
    printed by this build.

## FF12 decoded stream

Status of the old bench checklist. `reported` means owner figures; no assistant
radio run.

| # | Check | Status |
|---|---|---|
| 1 | FF12 discoverable | reported OK |
| 2 | Subscribe and `stream=decoded` | reported OK, MTU 517 |
| 3 | 56-byte records, version 1, flags 0 | partial (4 records) |
| 4 | Fields plausible at rest | partial; rest stick not centred |
| 5 | Sequence continuity, one hand | not run |
| 6 | Sequence continuity, both hands, 10 min | not run |
| 7 | Throughput about 360/s | not run |
| 8 | FF11 write refused while FF12 active | partial (refusal logged) |
| 9 | FF12 write refused while FF11 active | reported OK |
| 10 | Switch streams | reported OK |
| 11 | CCC read-back | not run |
| 12 | Disable inactive FF11 | not run |
| 13 | MTU below 59: no FF12, `mtu_skip` rises | reported OK |
| 14 | Recovery after MTU rises | reported OK |
| 15 | Phone reconnect | not run |
| 16 | FF11 unchanged | not run |
| 17 | `rx_time_us` monotonic | partial; arrival is bursty, not sensor time |
| 18 | Tick unit and wrap | partial; 100 ns consistent, 2^32 wrap unseen |

Checks 5-7, 11, 12, 15 and 16 are in the removed robustness sheet (ONY-1157; see git history) §1, §2, §3, §5, §9.

## Link diagnostics

| # | Check | Status |
|---|---|---|
| D1 | `vhci_drop`/`log_errors` and per-hand `link:` lines print | reported OK |
| D2 | Link health answers | reported; `rssi=0 lq=255 fcc=0` in all runs, RIGHT varied before MTU change |
| D3 | HCI command cost | not run |
| D4 | Tick estimate vs analyzer | not run |
| D5 | Clean stream near the board | reported, expectation not met: `dev_missing` 9-11.5 % |
| D6 | Reconnect | not run |
| D7 | Mode changes | partial; no mode change seen |
| D8 | VHCI drop counter over 10 min | not run |
| D9 | Diagnostic build rate change | reported: same rates |
| D10 | DECODING experiments | not run |

The reported finding: missing reports rose from about 10 % idle to about 20 %
while FF12 streamed, with `rate_rx` falling from 175-183 to 158-163/s. It fits
BLE airtime costing Classic reports, but other factors were present and it is
not established.

## FF14 batched stream

| # | Check | Status |
|---|---|---|
| B1 | FF14 exists | not run |
| B2 | Exclusion, all six orders | not run |
| B3 | Batches at MTU 517 | reported: `02 NN`, mean 6.4-6.7, max 9, about 48 notif/s |
| B4 | Nothing lost to batching | not run; in robustness §4 |
| B5 | Accounting identity | reported: `rx = tx + q`, `drop=0` |
| B6 | MTU 247: at most 4 records | not run; robustness §6 |
| B7 | MTU below 61: FF14 sends nothing | not run; robustness §7 |
| B8 | Partial flush, one controller | not run; robustness §10 |
| B9 | Record latency | not run |
| B10 | FF12 and FF11 unchanged | not run; robustness §9 |
| B11 | Switch FF12/FF14/FF11 | not run; robustness §8 |
| B12 | `LINKLOG` fields | reported: 19-field header parses |

The FF12 vs FF14 batching comparison is in the removed robustness sheet (ONY-1157; see git history) §4.

## FF15 echo

E1-E10 were reported passed on 2026-10-22 in one line, with no output pasted.
No figures are recorded. Host tests cover the request/answer bytes, rejection,
busy/unsubscribed/cancelled states and saturation.

## Discovery by name

D1-D11 were reported passed on 2026-10-22 in one line, with no output pasted.
Host tests cover name matching, stored-address bytes, parsing and the incoming
rule. Not tested anywhere: inquiry, remote name requests, NVS, held incoming
connections, and pairing after discovery.

## FF13 config block

Host tests cover formats and the reader only.

| # | Check | Status |
|---|---|---|
| C1 | Block `0x02` read with no init commands | reported OK for `0x02` |
| C2 | Follow-up `addr` = bytes remaining | implied by READY; no trace |
| C3 | Size and CRC-32 | reported: LEFT 7147 `8bc13223`, RIGHT 7148 `75e8ab85`; stability not tested |
| C4 | Input stream during read | inconclusive: no stream was on |
| C5 | Phone reads FF13 in 496-byte windows | reported OK, CRCs match |
| C6 | Input keeps streaming after READY | reported: continued; `other=` not reported |
| C7 | `WMR_CFG_FF13_READ=0` | partly reported; LEFT and the image hash unconfirmed. Folded into the removed robustness sheet (ONY-1157; see git history) §11 |

## FF12 decimation diagnostic

`WMR_CFG_FF12_DECIMATE` sends every Nth report on FF12 and counts the rest as
`decimated`. X1 (default unchanged), X2 (N=2), X3 (N=4) and X4 (the fixed-position
comparison) have not run. If `dev_missing` falls toward the idle level at N=2/4,
airtime is a cause; if not, it is not the only cause.

## Where the procedures live

- Internal bench (latency-2, FF16/FF17/stick, ONY-446, HID, LED, Sitting R):
  the removed internal-ESP32 bench sheet (ONY-1157; see git history). ESP32
  pass/fail steps: `docs/runs/sheets/bench.md`.
- Remaining protocol and robustness checks, plus close-out:
  `the removed robustness sheet (ONY-1157; see git history)`.
- External tester tiers:
  [`runs/sheets/external-test-user.md`](runs/sheets/external-test-user.md).
- Contention results: [`CONTENTION_LEVERS.md`](CONTENTION_LEVERS.md).
