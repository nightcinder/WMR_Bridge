# Testing status

Last updated with the refactor commit candidate (2026-10-21). This page
separates what was **actually run** from what is **still required**. Nothing
in the "still required" section has been done; the refactor must not be called
verified until it has.

## Run (and results)

| Test | Result |
| --- | --- |
| Firmware build, ESP-IDF v6.1, target esp32, `EXAMPLE=wmr_bridge` | Passed. No compiler warnings from `wmr_*` sources. `esp32-example.bin` 0x870d0 bytes (36 % of the app partition) |
| `sdkconfig` unchanged by the build | Confirmed by byte comparison |
| Host unit tests: `tests/run_tests.ps1` (built with `zig cc`, C99, `-Wall -Wextra -Wconversion`) | Passed: 6087 checks, 0 failures |

The host tests cover (see `tests/test_protocol.c`):

- notification capacity for MTU 0, 15, 16, 23, 247, 517;
- golden header bytes for both fragments of a 46-byte frame;
- encode-then-reassemble round trip for every capacity 13-244, both hands;
- whole frame in one fragment; 200 fragmented frames in a row;
- LEFT/RIGHT fragments interleaved;
- an incomplete frame followed by a new frame (old one counted, new one intact);
- a continuation with no frame in progress; a lost middle fragment (frame never
  completes, a later fragment does not resurrect it); a sequence change
  mid-frame; a duplicated fragment;
- malformed packets: short, wrong magic, bad hand, length/total/offset/chunk
  inconsistencies, then recovery;
- a malformed fragment abandoning the partial frame;
- queue: FIFO order, full queue rejects the newest, wraparound, clear with
  per-hand purge counts, peak survives clear;
- all eight sample frames in `samples/controller_frames.txt` at every capacity.

These tests exercise the pure-C protocol and queue code only. They do **not**
test BTstack integration, callbacks, timers or radio behavior.

## Run on hardware (ESP32 on COM5, 115200 baud; no controllers or phone present)

| Test | Result |
| --- | --- |
| Flash refactored firmware (`idf.py -B build-wmr-bridge -p COM5 -b 115200 flash`) | Passed, hash verified. The earlier attempt on COM3 failed: wrong port |
| Boot, 30 s serial capture | Passed: stack up, `BLE advertising: WMR Bridge (RAW STREAM FF11)`, console usage with BOND RESET labels, a stats block every 5 s (all zeros, `phone=0`), no HCI trace or hex dumps |

### Session 2: both controllers and phone (user-run, serial log pasted; about 15 s of streaming)

| Observation | Result |
| --- | --- |
| Phone connected and subscribed first | `BLE phone connected`, `notifications enabled`, `mtu=23` then `mtu=517` (negotiated value as seen by the ESP32) |
| Incoming HID from both controllers | Both accepted on their own: LEFT cid=1, RIGHT cid=2, descriptor status 0x00 each. `SET_PROTOCOL handshake status=0x03` appears for each (also present before the refactor; not investigated) |
| Final stats block | LEFT rx=tx=2341, RIGHT rx=tx=1668, `drop=0 offline=0 purged=0`, `q=0/256`, `errors=0` |
| Rates | `rate_tx` equals `rate_rx` on every block; about 174/s LEFT and 154/s RIGHT in the last block (the first block after connect is low because it spans the connection start) |
| Accounting identity | 2341+1668 = 4009 = tx sum = `notify` (4009; one notification per frame at MTU 517), q=0, so it holds |
| Queue | `high=11` (a burst at connect), never grew afterwards |

Not covered by this session: duration beyond about 15 s, controller or phone
disconnect/reconnect, the bond reset, and any phone-side check. The MTU shown
is the ESP32's view; the phone's own `onMtuChanged` value was not recorded.
Required items 2 and 3 are therefore only partly done and 7 is half done.

This covers required test 1 only. Idle counters are all zero, as expected with nothing connected.

## Still required (hardware, not yet run)

Run with the refactored firmware flashed. Record dates and observed numbers here.

1. ~~Boot and advertising~~ - done, see above.
2. **Both controllers, phone subscribed**: LEFT and RIGHT connected, phone subscribed (`sub=1`), at least 10 minutes running.
   - `rate_tx` tracks `rate_rx` for each hand;
   - `drop` stops increasing;
   - `q` does not grow persistently (`high` stays well below 256);
   - the accounting identity in [COUNTERS.md](COUNTERS.md) holds.
3. **Incoming connections**: power-cycle each controller; it reconnects by itself and its stream resumes.
4. **Outgoing connections**: `l` and `r`, one at a time.
5. **Controller disconnect/reconnect** while the phone is subscribed: the other hand keeps flowing; the affected hand resumes; sequence numbers continue.
6. **Phone disconnect/reconnect**: advertising restarts; after reconnect the phone must request MTU/High priority and write the CCC again; `purged`/`offline` behave as described in [RECONNECT_AND_QUEUE.md](RECONNECT_AND_QUEUE.md); no duplicate "BLE phone connected" lines.
7. **Large MTU + High priority**: log the *negotiated* MTU on the phone, compare with `mtu=`.
8. **Phone-side sequence validation**: lossless end-to-end delivery has **not** been established by any test. A gap-checking receiver is future work.
9. **Manual bond reset** (`k`/`K`): refused while connected; works while disconnected; controller re-pairs afterwards.
10. **Capture sample frames** labelled LEFT and RIGHT from this build and add them to `samples/` (current samples are from earlier bring-up sessions with the hand not recorded).
11. **Long soak** (hours) watching `errors`, `drop`, heap, and uptime.
12. Comparison against the original single-file bridge under the same conditions (that version is not included in this repository).

## Rerunning the host tests

```powershell
.\tests\run_tests.ps1
```

Needs a C99 compiler on PATH (`gcc`, `clang`, or `zig`); it also builds and runs the FF14 tests
(`tests/test_batch.c`). Written and run here
with `zig cc` from a throwaway Python venv, because no host compiler was
installed.

## FF12 decoded stream: bench-test checklist (hardware)

Firmware: FF12 as merged to `main` (PR #5). **Results marked "Reported" or "PARTIAL" were
run by the user (Pixel with nRF Connect, a pasted ESP32 log and one screenshot) and relayed through
director issue #3; the assistant did not run or see them, and they are not claimed as verified by
it.** Rows still saying "not yet run" have no report at all. What the assistant ran is host-only: the pure CCC-exclusion rule, the minimum-MTU rule and the
raw-frame-to-record path (`tests/test_stream_mode.c`, in CI) and a full ESP-IDF build
(the command and result are in the pull request). Beyond the rows below, nothing over the
air has been reported.

Use any BLE client that can write descriptors and subscribe (nRF Connect, or a script).
Record the date, the build, the phone/client and the observed numbers in the Result column.

| # | Check | How | Expect | Result |
| --- | --- | --- | --- | --- |
| 1 | FF12 is discoverable | Connect, discover service `FF10` | Characteristics `FF11` (read, notify) and `FF12` (notify only); `FF12` has a CCC | Reported OK (user, Pixel with nRF Connect): FF12 found and subscribed. Whether the FF11 properties were inspected was not reported. |
| 2 | Subscribe to FF12 | Request MTU 247 or more, write `0x0001` to the FF12 CCC | Write succeeds; log shows `BLE decoded notifications enabled`; periodic `STREAM` line shows `stream=decoded` together with `mtu=` and `conn=[interval lat= to=]` | Reported OK: MTU 517; log `BLE decoded notifications enabled`; STREAM line `stream=decoded mtu=517 conn=[15.00ms lat=1 to=5000ms]`. Before subscribing: `stream=none`, `tx=0`, every frame counted `offline`. First interval after subscribing: `notify=687` (LEFT tx 377 + RIGHT tx 310), `drop=0`, `mtu_skip=0`, `dec_err=0`, queue high-water 15. |
| 3 | Record size and version | Capture 100 notifications | Every value is exactly 56 bytes; byte 0 is `0x01` (version); byte 2 (`flags`) is `0x00`; byte 1 matches the hand | PARTIAL (4 records, not 100): all exactly 56 bytes, version 1, flags 0. The hand byte was not reported as checked against the controller. |
| 4 | Fields plausible | Hold a controller still | `accel` magnitude about 10 m/s^2, `gyro` near 0 (about 0.02 rad/s), `device_ticks` increasing; compare with a rotation capture from `tools/capture_ble.py` | PARTIAL (controllers sitting still, a few records): hand 0 gyro about 0.02 rad/s, accel magnitude 10.07; hand 1 gyro 0.010-0.011, accel 10.16-10.17; `temperature_raw` 2926 (hand 0) and 2983-2984 (hand 1); battery 133 and 131; trigger, trackpad and buttons 0. Resting stick was not centred: hand 0 (-0.029, 0.014), hand 1 (-0.064, 0.148); see DECODING.md, "Thumbstick resting values are not zero". Not compared with a rotation capture. |
| 5 | Sequence continuity, one hand | One controller streaming for 60 s | `sequence` increases by 1 per record with no gaps (a gap means loss before transmission); note any | not yet run |
| 6 | Sequence continuity, both hands | Both controllers streaming for 10 minutes | Per-hand `sequence` contiguous; per-hand rate about 150-180/s; `drop` stays flat; `q` stays low; `rx` = `tx` + `drop` + `offline` + `purged` + `mtu_skip` + `dec_err` + `q` (accounting identity in COUNTERS.md); `errors=0` | not yet run |
| 7 | Throughput | Same run as 6 | Record whether about 360 notifications/s sustains with no drops at the negotiated connection interval (bench item from PROTOCOL.md) | not yet run |
| 8 | Refusal | While FF12 is subscribed, write `0x0001` to the FF11 CCC | The write fails with ATT error `0x80`; FF12 keeps streaming; log shows `BLE raw notifications refused` | PARTIAL: refusal logged as `BLE raw notifications refused: decoded stream is active (ATT error 0x80)`. Not yet shown: FF12 still streaming afterwards (the follow-up STREAM line was not pasted). |
| 9 | Refusal, other direction | Subscribe to FF11 first, then try the FF12 CCC | ATT error `0x80`; FF11 keeps streaming | Reported OK: `BLE decoded notifications refused: raw stream is active (ATT error 0x80)`. FF11 still streaming afterwards: not reported. |
| 10 | Switching | Write `0x0000` to the active CCC, then enable the other | Both writes succeed; the other stream starts; the first stops; no records of the old format after the switch | Reported OK: `BLE decoded notifications disabled` then `BLE raw notifications enabled` worked. Absence of records in the old format after the switch: not reported. |
| 11 | CCC read-back | Read both CCCs in each state | Only the active one reads `0x0001` | not yet run |
| 12 | Disabling the inactive stream | While FF12 is active, write `0x0000` to the FF11 CCC | Succeeds; FF12 unaffected | not yet run |
| 13 | Small MTU | Enable FF12 while the ATT MTU is below 59 (default 23, before MTU exchange, or a client that will not negotiate) | No FF12 notifications; `mtu_skip` rises on both hands; `drop` and `q` do not | Reported OK (user, Pixel with nRF Connect, PR #8 diagnostics build; relayed on issue #3): at MTU 23 with FF12 subscribed, `mtu_skip` equals `rx` minus `offline` on both hands, `drop=0`, `q=0`, `notify=0`. |
| 14 | Recovery after small MTU | Raise the MTU to 59 or more while subscribed | Records start flowing; the first `sequence` is higher than the last one before (the skipped reports); `mtu_skip` stops rising | Reported OK (same run), except the sequence part: after requesting MTU 517 `mtu_skip` stopped rising (final 16936 / 17041), `rate_tx` equals `rate_rx` at about 160/s per hand, one notification per record (notify delta 1615 per 5 s = LEFT tx delta 800 + RIGHT 815), queue empty, no drops since the transition. The sequence numbers seen by the phone were not reported. |
| 15 | Reconnect | Disconnect and reconnect the phone | Subscription is not remembered (`stream=none` until the CCC is written again); no stale records | not yet run |
| 16 | FF11 unchanged | Subscribe to FF11 instead | Fragmented frames as before; the golden-packet format in PROTOCOL.md; `stream=raw`; same rates as the earlier bench run | not yet run |
| 17 | `rx_time_us` | Compare record timestamps | Monotonic per hand across a controller reconnect; spacing matches `device_ticks` x 100 ns within jitter | PARTIAL: `rx_time_us` monotonic, but bursty: three consecutive hand-1 records arrived 2,437 us and 155 us apart while their `device_ticks` are 4.990 ms apart, so arrival time is not the sensor clock (as specified). Across a controller reconnect: not run. |
| 18 | Tick unit and wrap | Use `tools/analyze_capture.py` on an FF11 capture (or compute from FF12) | ticks/wall about 1.0 confirms 100 ns per tick; unconfirmed until run | PARTIAL (from FF12): three consecutive hand-1 records have sequence +1 and `device_ticks` +49,896 each (4.990 ms), the same median as the capture analysis, consistent with 100 ns per tick. The 2^32 wrap (about 7 minutes of controller time) has not been observed. Phone FF11 captures (reported): ticks/wall 1.0016 (RIGHT), 1.0083 (LEFT). |

Do not flash from this repository's CI or from an assistant session; flashing and the
runs above are done by hand.

## Link diagnostics: bench-test checklist (hardware, **not yet run**)

Firmware: the PR #8 branch. Added to find where controller reports are lost (director issue
#3). Everything below is **not yet run**; what the assistant ran is host-only (`tests/test_diag.c`
in CI: the missing-report estimator, log classification, mode tracking and reply parsing) and a
full ESP-IDF build.

| # | Check | How | Expect | Result |
| --- | --- | --- | --- | --- |
| D1 | Boots and prints the new lines | Flash, both controllers connected | `vhci_drop=` / `log_errors=` line and a `<HAND> link:` line per hand in each `STREAM` block; no other new output | Reported OK (user): the `vhci_drop`, `log_errors` and per-hand `link:` fields were read from the `STREAM` blocks. Whether any other new output appeared was not reported. |
| D2 | Link health answered | Watch the `link:` lines for 30 s | `rssi`, `fcc`, `lq` show numbers, or `n/a(0xNN)` with a status (record which, per hand) | Reported: the controllers answered all three commands. In all runs `rssi=0 lq=255 fcc=0`; RIGHT showed `rssi` -3 to -7 and `lq` 248 to 251 before the MTU change. No refusal reported. What the values mean for a Classic link is still the unverified reading in DECODING.md. |
| D3 | HCI cost | Count commands for 60 s (e.g. with `WMR_CFG_HCI_TRACE` set, or a sniffer) | About 72 Read RSSI / Failed Contact / Link Quality commands per minute (1.2/s), none closer than 100 ms | not yet run |
| D4 | Tick-based estimate vs the analyzer | Same controller, phone capture and the ESP32 `dev_missing` over the same period | `dev_missing` and the analyzer's `device reports missing` roughly agree (the analyzer's window is the whole phase, the ESP32's is the last 256 deltas) | not yet run |
| D5 | Estimate on a clean stream | A controller very close to the ESP32, nothing else on | `dev_missing` stays near 0 and `period` is about 4.99 ms | Reported, **expectation not met**: with both controllers right next to the ESP32, one per side (a phone connected and FF12 subscribed, nothing sent), `dev_missing` was 9 to 11.5 % of expected per hand, not near 0; `period` 4.98 ms. So a nearby controller does not by itself give a clean stream. |
| D6 | Reconnect | Power-cycle a controller | `link:` shows `n/a (no ACL link)` while down; `dev_missing` and `discont` restart from 0 on reconnect, with no spike | not yet run |
| D7 | Mode changes | Leave a controller idle, then move it | A `LEFT link mode: active -> sniff ...` line appears only if the link really changes mode, once per change; none otherwise | PARTIAL (reported): no mode change was seen in any run and `mode=active` throughout. The idle-then-move case specifically was not reported, and a controller that never enters sniff in these runs does not show the log line works. |
| D8 | VHCI drop counter | Normal streaming for 10 minutes | `vhci_drop` stays 0, or rises, which would be a finding; also try the old tick-based shortfall against it | not yet run |
| D9 | Does the diagnostics build change the rates? | Compare `rate_rx` per hand with the earlier reports (no phone: LEFT 175-181, RIGHT 164-184) | Similar; record any difference | Reported: with FF12 subscribed at MTU 23 and nothing sent, `rate_rx` was LEFT 175 to 183, RIGHT 175 to 184, the same as the earlier no-phone figures. No difference is claimed from single runs. |
| D10 | Experiments in DECODING.md | No phone / not subscribed / FF11 / FF12; each controller alone; positions swapped | Record `rate_rx`, `dev_missing`, `mode`, `rssi` per case | not yet run |

## PR #8 diagnostics build: first bench data (user-reported; the assistant did not run or see it)

Relayed on director issue #3. Setup as reported: both controllers right next to the ESP32, one
per side; Pixel with nRF Connect; FF12 subscribed; the MTU stayed at 23 for the first part,
then 517 was requested. Single runs, not repeated. Rows D1, D2, D5, D7, D9 and checks 13 and
14 above are filled in from this report.

| Observation (reported) | Value |
| --- | --- |
| At MTU 23, FF12 subscribed, nothing sent: `rate_rx` | LEFT 175 to 183/s, RIGHT 175 to 184/s |
| At MTU 23: `dev_missing` | 9 to 11.5 % per hand |
| After MTU 517, FF12 streaming: `rate_rx` / `rate_tx` | 158 to 163/s per hand, `rate_tx` equal to `rate_rx` |
| After MTU 517: `dev_missing` | about 20 % per hand, per interval |
| Cumulative `dev_missing` | creeps from 13.5 % to 14.3 % (it includes the idle period) |
| Link health, all runs | `rssi=0 lq=255 fcc=0 mode=active`; `vhci_drop=0 log_errors=0 discont=0`; `period=4.98 ms` |

**The reported finding** (from the part of the run **before the controllers were moved**:
controllers beside the ESP32, `rssi=0`, `lq=255`): missing device reports per hand went from
about 10 % (idle or `mtu_skip`) to about 20 % while FF12 actually streams. It fits `rate_rx` falling from 175-183 to
158-163 against a nominal of about 200/s (a 200/s controller at 160/s is 20 % missing).
With the earlier FF11 and phone-capture figures it suggests that BLE notification traffic
costs reports on the Classic link, but the earlier, larger losses had the controllers in the
user's hands, so other factors are likely too. **Not established.** The decimation option below
is the next test.

**Per-interval figure.** The first report's "about 45 missing per hand" was corrected by the
director to about 45 per **second**, i.e. about 225 per 5 s or about 22 %, which agrees with
`rate_rx` 158 to 163 against about 200 and with the later per-interval table (about 208 missing
per 1000).

**Later in the same run the user moved both controllers away from the ESP32** (to simulate real
distance). The loss and link-quality readings that followed changed with the distance and are
**not** a time-dependent or BLE effect; they are in [DECODING.md](DECODING.md), "Bench runs on the
diagnostics build". A fair decimation test needs the controllers at one fixed, stated
position for the whole run.

**Reading `vhci_drop=0 log_errors=0`.** It is consistent with no drops. It cannot distinguish
that from the log sink not receiving BTstack's message (the sink depends on BTstack's wording),
because no error-level message has appeared at all to show the sink works.

### The `drop=1200 / purged=12,7 / high=256` counters after the MTU change

Reported: after the MTU change `drop` is 1200 / 1213, `purged` 12 / 7 and `high` 256, constant
since, and FF12 below MTU 59 should not queue anything. What the code allows (read from
`src/wmr_transport.c`, nothing measured):

1. **FF12 cannot have filled the queue at MTU 23.** `wmr_transport_submit_report` returns
   (counting `mtu_skip`) before it pushes anything while the stream is FF12 and the MTU is
   below 59, and `pump_send_decoded` removes and counts any frame that was queued before the
   MTU was known to be too small. The MTU cannot go down inside a connection, and the queue is
   emptied when the phone connects and on every subscription change. So the check-13 phase
   itself did not queue.
2. **The counters are since boot, not since connection or subscription.** `drop`, `purged` and
   `notifications` are free-running and are reset only by a reboot ([COUNTERS.md](COUNTERS.md)),
   and `high` is the peak since boot and never decreases. So `drop=1200`, `purged=12/7` and
   `high=256` can have been set at any time since the last reboot, including before this run,
   and "constant since the MTU change" only says nothing was added after it.
3. **The most likely source is an earlier FF11 phase in the same boot at MTU 23** (not proven).
   FF11 has no MTU rule: at MTU 23 it queues, the queue fills to 256 and the newest reports
   are dropped. The earlier "MTU 23, queue full and dropping, about 135/s each" data point is
   exactly that situation. Switching FF11 off then purges whatever is queued (`purged`). About
   1200 drops in total is roughly 600 per hand, a few seconds at 135/s per hand, which fits a
   short test, but the number alone does not prove it.
4. **A stall inside FF12 after the MTU rose is the alternative that cannot be ruled out from
   the counters.** If the can-send-now event was delayed during the MTU exchange, FF12 records
   would queue (about 320/s, so 256 in under a second) and then drop until sending resumed. A
   stall of about 4 to 5 s would be needed for 1200 drops. `errors` would rise if
   `att_server_notify` failed (its value was not reported); a delayed event would not raise it.
5. **What separates the two:** the `STREAM` block printed **just before** the MTU request
   (if `drop` is already 1200 and `high` 256 there, it predates the transition, which points to
   item 3), the value of `errors`, and a run from a fresh reboot with FF12 only. A firmware
   change that would remove the ambiguity (log the first drop of each episode with the stream
   mode, MTU and queue depth) is **proposed, not made**.

## Diagnostic build option: FF12 decimation (`WMR_CFG_FF12_DECIMATE`; not yet run on hardware)

Default `1` = off, no behaviour change. Set `N` in `src/wmr_config.h` (or before including it)
to send only every Nth report of each hand on FF12; the rest are counted per hand as
`decimated` and the STREAM block prints `DIAGNOSTIC BUILD: FF12 sends every Nth report`. The
phone's `sequence` then jumps by N (the skipped reports keep their numbers, as with
`mtu_skip`). FF11 is not decimated. It tests whether BLE airtime costs controller reports:
**if `dev_missing` falls back toward the idle level (about 10 %) at N=2 or 4, airtime is a
cause; if it stays at about 20 %, it is not (or not only).** Build and CI: see the pull request.

| # | Check | How | Expect | Result |
| --- | --- | --- | --- | --- |
| X1 | Default build unchanged | Flash the PR build with `N=1` | `decimated=0` on both hands; no DIAGNOSTIC line; same behaviour as before | not yet run |
| X2 | N=2 | Rebuild with `WMR_CFG_FF12_DECIMATE=2`, FF12 at MTU 517, 60 s | `DIAGNOSTIC BUILD` line; `tx` about half of `rx`; `decimated` about half of `rx`; identity holds; phone `sequence` steps of 2 | not yet run |
| X3 | N=4 | Same with 4 | `tx` about a quarter of `rx`; steps of 4 | not yet run |
| X4 | The test itself | At N=1, 2, 4 and with no phone, **controllers at one fixed, stated position for every run (state it, and note `rssi`/`lq`; do not move them)**: `rate_rx`, `last5s` and `cum` per hand, 2 minutes each | Record all four; compare with the idle level | not yet run |

## New log lines (PR #9; hardware, **not yet run**)

Logging only; no new HCI traffic. Lines are described in [COUNTERS.md](COUNTERS.md).

| # | Check | How | Expect | Result |
| --- | --- | --- | --- | --- |
| L1 | `LINKLOG` lines | Run with both controllers; capture the serial output | One `LINKLOG_FIELDS,...` header, then one `LINKLOG,...` line per hand per 5 s; every line has as many fields as the header; the numbers match the `link:` line printed just before | not yet run |
| L2 | `LINKLOG` in a script | Paste a capture into a CSV reader, filtering on the prefix `LINKLOG,` | Empty fields (not `n/a`) where a value is unknown, for example `rssi` while a controller is down | not yet run |
| L3 | First-drop line | At MTU 23 subscribe to FF11 until the queue fills | Exactly one `<HAND> queue full: first drop of this episode, stream=raw mtu=23 q=256/256 errors=N` line per episode, not one per dropped report; a second line only after the queue has drained and filled again | not yet run |
| L4 | LE buffer size | Read the boot log | One `HCI LE buffers: ACL packet length N bytes, M packets` line during start-up (or none, which would mean the event was not seen by the handler) | not yet run |
| L5 | Data length | Connect the phone | A `BLE data length changed: tx ... rx ...` line, or `BLE data length: no change event seen since connect` about 0.5 s after connect | not yet run |

## FF14 batched stream (branch `ff14-batching`; hardware, **not yet run**)

What was run (host only, by the assistant): `tests/test_batch.c` (golden bytes for a batch of 1, 2 and
the maximum of 9; the MTU rule at 0, 23, 59, 60, 61, 116, 117, 247, 285, 517; the flush rule
including a partial flush on the hold time and a simulated 200 reports/s arrival that never held a
record longer than the hold time; all six orderings of the FF11/FF12/FF14 exclusion; queue-full and
wraparound behaviour; a failed send keeping its records), plus the extended `LINKLOG` strings in
`tests/test_diag.c`. These ran locally with `zig cc` (0 failures); the CI result is on the pull request. The ESP-IDF build
(`EXAMPLE=wmr_bridge`) is clean with 0 warnings. **Not tested anywhere:** `pump_send_batch`,
the hold timer and `att_server_notify` in `wmr_transport.c` (they need BTstack and a radio), and
everything below. Nothing about FF14 is verified on hardware.

**One BTstack-side file changes with this branch:** `integration/main-CMakeLists.txt` lists
`wmr_batch.c` as a new source. Copy it over `port\esp32\main\CMakeLists.txt` again (as in
[BUILD_WINDOWS.md](BUILD_WINDOWS.md)) before building, or the link fails with undefined
`wmr_batch_*` references.

### Checks

| # | Check | How | Expect | Result |
| --- | --- | --- | --- | --- |
| B1 | FF14 exists | Connect, discover service FF10 | A notify-only `0000FF14-...` characteristic with a CCC, after FF12; FF11 and FF12 still present and unchanged | not yet run |
| B2 | Exclusion, all six orders | With FF11 / FF12 / FF14 in turn enabled, try to enable each of the other two (six pairs) | Every second enable fails with ATT error `0x80` and the first stream keeps running; after writing `0x0000` to the active CCC the other enables | not yet run |
| B3 | Batches at MTU 517 | Subscribe to FF14, both controllers streaming, 60 s | Values start `02 NN`; `NN` between 1 and 9; value length `2 + 56 x NN`; STREAM `FF14` line shows `batches_tx` rising, `mean` between 1 and 9, `max` up to 9; **`notify` rises by `batches_tx`, much slower than FF12's ~320/s** (the expectation is of the order of 70 to 100 per second at a 15 ms interval; not measured) | not yet run |
| B4 | Nothing lost to batching | Same run; on the phone, per hand, `sequence` gaps | Gaps equal the FF12 gaps for the same run (missing reports from the controllers show as gaps, batching adds none); no repeated or reordered `sequence` within a hand | not yet run |
| B5 | Identity | STREAM and `LEFT`/`RIGHT` lines at the end of B3 | `rx = tx + drop + offline + purged + mtu_skip + dec_err + decimated + q` per hand, with `tx` in records; `tx` (both hands) grew by `records_in_batches`; `notify` grew by `batches_tx` | not yet run |
| B6 | MTU 247 | Cap the phone's MTU at 247 | `max` is at most 4; values at most 226 bytes | not yet run |
| B7 | MTU below 61 | MTU 23, then (if the phone allows) 59 or 60 | Nothing arrives on FF14; `mtu_skip` rises for both hands; `batches_tx` stays 0. At 59/60 FF12 still works and FF14 does not | not yet run |
| B8 | Partial flush | Only one controller connected (about 160 to 200 reports/s) | Batches smaller than full: `mean` about the reports that arrive in one connection interval (roughly 3 at 15 ms, not measured); `batch_full_flushes` stays near 0; the phone still sees every record | not yet run |
| B9 | Latency of a record | Use `rx_time_us` against the phone's arrival time for a few seconds of FF12, then FF14 (needs the app to log both) | FF14 arrival-minus-`rx_time_us` is larger by at most about one connection interval; **not measured so far** | not yet run |
| B10 | FF12 and FF11 unchanged | Re-run X1 (FF12 at `N=1`) and the FF11 checks | Identical to before; `FF14` line stays at zero | not yet run |
| B11 | Switching | FF12, then `0x0000`, then FF14, then `0x0000`, then FF11 | Each switch starts a fresh stream (the queue is cleared, `purged` counts what was dropped), no stale batch | not yet run |
| B12 | `LINKLOG` | Capture a B3 run | Header and lines have the same number of fields; the last five are `batches_tx,records_in_batches,batch_mean,batch_max,batch_full_flushes`; the first fourteen are unchanged | not yet run |

### The comparison the director asked for (user to run; the assistant has not)

Question: does sending the same samples in far fewer notifications reduce the missing-report
share the way decimation did? Use the default build (decimation off, `N=1`), the **same fixed,
stated table position** for every run (controllers face down and still, as in the decimation
runs), MTU 517, interval as granted (note it). Four 2-minute runs, in this order:

1. **No phone** (baseline; the idle level).
2. **A: FF12**, `N=1`.
3. **B: FF14**.
4. **A again: FF12**, `N=1` (A/B/A: if the second A differs from the first, the position or the
   session drifted and the comparison is weaker).

Per run, record per hand `rate_rx`, `last5s` and `cum`, `rssi`/`lq`, the granted interval and
`mtu`, and for FF14 `mean`, `max`, `batches_tx` (the `LINKLOG` lines carry all of it; paste the
capture).

How to read it, so the conclusion is not chosen after the fact: in the decimation runs RIGHT fell
from 28 to 36 % at N=1 to 12 to 15 % at N=2 and 4, and LEFT stayed at about 18 to 20 % at every N
(director-reported, one position, one session). If FF14 brings RIGHT down toward that range,
notification count is part of RIGHT's loss and FF14 helps it without dropping samples. If RIGHT
stays near its FF12 level, batching does not help at this position. **LEFT is not expected to
change and a change would be a new finding; do not claim FF14 fixes LEFT.** One session at one
position cannot say more than that.

## FF15 echo (issue #1; hardware: checklist reported passed by the user, no output pasted)

What was run (host only, by the assistant): `tests/test_echo.c` (golden bytes of the request and
the answer; rejected lengths and versions; one outstanding echo, `busy`, `unsubscribed`,
`cancelled`, a retry restamping the turnaround, saturation). The firmware sources were also
syntax-checked with gcc against current BTstack `master` headers and a `wmr_bridge.gatt` compiled
with BTstack's `compile_gatt.py` (FF15 value handle 0x0012, CCC 0x0013; all earlier handles
unchanged). The assistant did not build with ESP-IDF, flash or run it. On 2026-10-22 the user
reported that E1-E10 all passed (below); the host tests above are all the assistant ran.

**One BTstack-side file changes:** `integration/main-CMakeLists.txt` lists `wmr_echo.c`. Copy it over
`port\esp32\main\CMakeLists.txt` again before building, or the link fails with undefined
`wmr_echo_*` references.

| # | Check | How | Expect | Result |
| --- | --- | --- | --- | --- |
| E1 | FF15 exists | Connect, discover service FF10 | `0000FF15-...` with write-without-response and notify, and a CCC, after FF14; FF11, FF12, FF14 unchanged | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| E2 | Echo, no stream | Enable FF15 only, write `01 00 01 00` | One 20-byte answer starting `01 00 01 00`; `queue_depth` 0; `FF15 echo accepted=1 sent=1` | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| E3 | Not subscribed | FF15 CCC off, write a ping | No answer; `unsubscribed` rises | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| E4 | Bad request | Write 3 bytes, and a version 2 request | No answer; `bad` rises by 2 | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| E5 | Independent of the exclusion | With FF12 (then FF14, then FF11) active, enable FF15 | Accepted, never `0x80`; the stream keeps running | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| E6 | RTT at load | FF12 at MTU 517 with both controllers, one ping every 100 ms for 60 s (needs the app or a script to log `t1`, `t4`, the answer) | RTT distribution; `turnaround_us` small (expected well under 1 ms, not measured); RTT does not grow with `queue_depth` | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded. The row asks for an RTT distribution; none was reported |
| E7 | RTT idle vs FF14 | Repeat E6 with no stream, then FF14 | The three distributions side by side; FF14 is not expected to add a batch hold to the echo | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded. The row asks for three RTT distributions; none was reported |
| E8 | Busy | Send two pings back to back | One answer for the first `ping_id`, `busy` rises for the second (only if the second arrives before the first answer is sent; may not reproduce) | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded. The row says it may not reproduce; not reported whether it did |
| E9 | Counters and identity | `s` after E2-E8 | `accepted = sent + cancelled + pending`; `notify` did not grow from echo answers | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| E10 | Loss unchanged | Compare `last5s` with and without pings during FF12 | No visible change at 10 pings/s (an echo is 20 bytes on air per 100 ms) | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |

## Discovery by name (issue #2; hardware: checklist reported passed by the user, no output pasted)

What was run (host only, by the assistant): `tests/test_discovery.c` (name matching incl. case,
padding and near misses; golden bytes of the stored address record and what it rejects; address
parsing; a discovery run with names in the inquiry response, with remote name requests, with one
hand already known, with a second controller of the same hand, with a full pending list; the
incoming-connection rule). The firmware sources were syntax-checked with gcc against BTstack
`e38553977a25` (the tested commit) and current `master` headers. **Not built with ESP-IDF, not
flashed, not run.** Not tested anywhere: inquiry, remote name requests, NVS storage, the held
incoming connection, connecting and pairing after discovery, and everything below.

**Before testing:** this change removes the compiled-in addresses, so the first boot has none
stored and starts discovery. Re-copy `integration/main-CMakeLists.txt` (adds `wmr_discovery.c`).
Already-paired controllers keep their link keys on the ESP32; whether they reconnect inwards
before discovery has found them is check D6.

| # | Check | How | Expect | Result |
| --- | --- | --- | --- | --- |
| D1 | Fresh boot, nothing stored | Flash, boot, controllers off | `LEFT address: unknown (not discovered yet)`, same for RIGHT; about 3 s later `Discovery round 1/6 ...`; after 6 rounds `not found ... press d` and `Discovery finished` | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| D2 | Names | Put LEFT in pairing mode, press `d` | `Discovery: LEFT is <addr> (name in inquiry response)` or `(remote name)`. If neither appears, the real name differs from `Motion controller - Left`: record it with `WMR_CFG_HCI_TRACE` | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| D3 | Pair and connect | Continue D2 | `Addresses saved (LEFT stored, ...)`, `Connecting LEFT`, SSP accept, `descriptor status=0x00`, reports flow | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| D4 | Both hands | `F` (after `L`/`R`), then both controllers in pairing mode | Both found in one or two rounds, connected one after the other | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| D5 | Persistence | Reboot the ESP32, power-cycle the controllers | Addresses printed as `discovered, stored`; no discovery round; controllers reconnect on their own (`Incoming HID ... accepting`) | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| D6 | Unknown incoming, hand missing | `F` while both are disconnected, then power on a paired controller (not pairing mode) | Either `Incoming unknown device: requesting its name` then accepted and stored, or it does not try to connect (record which) | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| D7 | Unknown incoming, both known | A different paired HID device connects in | `not LEFT or RIGHT; declining` | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| D8 | Two of a hand | Two LEFT controllers in pairing mode | One taken, `another device with a controller name ... ignored` for the other | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded. The row needs a second LEFT controller; not reported what setup was used |
| D9 | Refusals | `F` while connected; `l` while discovery runs; `k` with no address | `FORGET refused ...`; `Discovery is running`; `has no address yet` | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |
| D10 | Loss during discovery | One controller streaming to the phone (FF12), the other missing, run `d` | Note `last5s` of the streaming hand during the inquiry rounds vs. before; stays connected | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded. The row asks for `last5s` before and during the inquiry rounds; no figures reported |
| D11 | Override | Build with `-DWMR_CFG_LEFT_ADDRESS=\"..\"` | LEFT `compile-time override`; discovery looks only for RIGHT; `F` keeps LEFT | user-reported (2026-10-22): passed; one-line report for the whole checklist, no output pasted, so no figures are recorded |


## FF13 config block (APPROVED layout; wired but not built; none run)

Host tests (`tests/test_cfg.c`, CI) cover formats and the reader only. The checks below need
the ESP32 build with the new wiring and a controller. **Not yet run.** Watch the console for
`FF13 LEFT: ...` lines and the statistics `FF13` lines.

| # | Check | Expected | Result |
| --- | --- | --- | --- |
| C1 | Controller accepts a block-read request with no init commands (the firmware reads block `0x02`, not block 0) | Response report ID `02`, `len` > 0 | user-reported (2026-10-22): both hands reached READY, so yes for block `0x02` via `hid_host_send_report`; block 0 not tried |
| C2 | Follow-up `addr` = bytes still to read | Responses continue; `blk_remain` falls by `len` | user-reported (2026-10-22): implied by READY (the reader fails on any `blk_remain` mismatch); no raw trace seen |
| C3 | Block `0x02` size and CRC-32 | Size below 16384; CRC stable across two reads | user-reported (2026-10-22), one read each: LEFT 7147 bytes crc32 `8bc13223`, RIGHT 7148 bytes crc32 `75e8ab85`. Stability across two reads not yet run |
| C4 | Input stream during the read | FF11/FF12 sequence numbers stay contiguous | inconclusive: no phone stream was on (`sub=0`). The first link lines after the read show `dev_missing` LEFT 78 of 227 (cum 34.4 %), RIGHT 42 of 232 (18.1 %), above the usual 9-11 % no-phone baseline. Not comparable (fresh connection, read in progress) and not shown to be caused by the read; needs a run with the read disabled or repeated after READY |
| C5 | Phone reads FF13 in 496-byte windows | Reassembled blob CRC matches header | not yet run |
