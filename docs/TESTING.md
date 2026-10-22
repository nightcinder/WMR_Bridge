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

Needs a C99 compiler on PATH (`gcc`, `clang`, or `zig`). Written and run here
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
| 13 | Small MTU | Enable FF12 while the ATT MTU is below 59 (default 23, before MTU exchange, or a client that will not negotiate) | No FF12 notifications; `mtu_skip` rises on both hands; `drop` and `q` do not | not yet run |
| 14 | Recovery after small MTU | Raise the MTU to 59 or more while subscribed | Records start flowing; the first `sequence` is higher than the last one before (the skipped reports); `mtu_skip` stops rising | not yet run |
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
| D1 | Boots and prints the new lines | Flash, both controllers connected | `vhci_drop=` / `log_errors=` line and a `<HAND> link:` line per hand in each `STREAM` block; no other new output | not yet run |
| D2 | Link health answered | Watch the `link:` lines for 30 s | `rssi`, `fcc`, `lq` show numbers, or `n/a(0xNN)` with a status (record which, per hand) | not yet run |
| D3 | HCI cost | Count commands for 60 s (e.g. with `WMR_CFG_HCI_TRACE` set, or a sniffer) | About 72 Read RSSI / Failed Contact / Link Quality commands per minute (1.2/s), none closer than 100 ms | not yet run |
| D4 | Tick-based estimate vs the analyzer | Same controller, phone capture and the ESP32 `dev_missing` over the same period | `dev_missing` and the analyzer's `device reports missing` roughly agree (the analyzer's window is the whole phase, the ESP32's is the last 256 deltas) | not yet run |
| D5 | Estimate on a clean stream | A controller very close to the ESP32, nothing else on | `dev_missing` stays near 0 and `period` is about 4.99 ms | not yet run |
| D6 | Reconnect | Power-cycle a controller | `link:` shows `n/a (no ACL link)` while down; `dev_missing` and `discont` restart from 0 on reconnect, with no spike | not yet run |
| D7 | Mode changes | Leave a controller idle, then move it | A `LEFT link mode: active -> sniff ...` line appears only if the link really changes mode, once per change; none otherwise | not yet run |
| D8 | VHCI drop counter | Normal streaming for 10 minutes | `vhci_drop` stays 0, or rises, which would be a finding; also try the old tick-based shortfall against it | not yet run |
| D9 | Does the diagnostics build change the rates? | Compare `rate_rx` per hand with the earlier reports (no phone: LEFT 175-181, RIGHT 164-184) | Similar; record any difference | not yet run |
| D10 | Experiments in DECODING.md | No phone / not subscribed / FF11 / FF12; each controller alone; positions swapped | Record `rate_rx`, `dev_missing`, `mode`, `rssi` per case | not yet run |
