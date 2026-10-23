# Run sheet: robustness — the remaining TESTING.md items in one sitting

> **Proposed sheet, not run. No line in this file is a hardware result.** None of the checks
> below has been run on this firmware, and nothing here is a pass/fail claim. Issue: ONY-26
> (M9.1). One bridge, default build (`WMR_CFG_BRIDGE_HANDS=0`).

Purpose: collect the hardware items that have been outstanding in
[TESTING.md](../../TESTING.md) into one ordered bench session, so none of them needs a sitting
of its own. The session also covers the gamepad path's gate: a clean 10-minute run and a
long soak with both hands and the phone subscribed.

Every `STREAM` block in this sheet must show `bridge_hands=0`. A `1` or `2` means a single-hand
image (see [two-bridge-hands.md](two-bridge-hands.md)); that is the wrong image for this sheet.

## What is covered

| TESTING.md item | Section | Short description |
| --- | --- | --- |
| Still-required 2 | §1 | Both controllers + phone subscribed, at least 10 minutes |
| Still-required 3 | §10.2 | Incoming connection: power-cycle each controller |
| Still-required 4 | §10.3 | Outgoing connections `l` and `r`, one at a time |
| Still-required 5 | §10.2 | Controller disconnect/reconnect while the phone is subscribed |
| Still-required 6 | §3 | Phone disconnect/reconnect |
| Still-required 9 | §10.4 | Manual bond reset `k` / `K` |
| Still-required 11 | §10.6 | Long soak (at least 1 hour) |
| FF12 5 | §2 | Sequence continuity, one hand, 60 s |
| FF12 6 | §1 | Sequence continuity, both hands, 10 minutes |
| FF12 7 | §1 | Throughput in the same run as FF12 6 |
| FF12 11 | §5, §8 | CCC read-back in each active-stream state |
| FF12 12 | §5 | Disabling the inactive FF11 stream while FF12 is active |
| FF12 15 | §3 | Phone reconnect with FF12 active |
| B4 | §4 | FF14 adds no phone-side sequence gaps |
| B6 | §6 | MTU 247: at most 4 records per batch |
| B7 | §7 | MTU below 61: FF14 sends nothing, FF12 still works at 59/60 |
| B8 | §10.5 | Partial flush with one controller |
| B10 | §9 | FF12 and FF11 unchanged |
| B11 | §8 | Switching FF12 → FF14 → FF11 |

Not in this sheet: the two-bridge delta (§11 is a placeholder), B9 (latency), and the FF12
checks that are not listed above. They are not silently claimed as done.

## Why this order

The 10-minute FF12 run comes first: it is the baseline every later rate, counter and gap
figure is compared with, and it is the shortest of the two long waits. The phone-side stream,
MTU and switching items follow while the setup is fresh. The 1-hour FF14 soak is started last
and is **left running unattended**; the controller-side reconnect, outgoing and bond-reset
items (§10.2–§10.5) are done during its first part, so none of the fiddly work is waiting
behind the hour. The last action is reading the soak's final block.

Three reboots are deliberate (before §1, before §6, before §10) to give clean counters. There
is **one flash** for the whole session.

## 0. Before the sitting

### 0.1 One flash

Build and flash `main` at or after the commit that merges this sheet. Record the hash. The
clone/copy/build steps are the same as [closeout.md](closeout.md) §0; the short version:

```powershell
$clone = "$env:TEMP\WMR_Bridge-robustness"
if (Test-Path $clone) { Remove-Item $clone -Recurse -Force }
git clone https://github.com/nightcinder/WMR_Bridge $clone
git -C $clone log --oneline -1                      # note the hash
robocopy $clone C:\Users\Nick\btstack-wmr\wmr_bridge /E /XD .git captures __pycache__ .github venv
Copy-Item $clone\integration\main-CMakeLists.txt C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Force
Select-String C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Pattern "wmr_batch|wmr_echo|wmr_discovery|wmr_cfgmgr"   # must print four lines
cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge build
idf.py -B build-wmr-bridge -p COM5 -b 115200 flash monitor
```

Robocopy exit codes below 8 are success. Both controllers should reconnect on their own
(addresses are stored in NVS); if they do not, put them in pairing mode and press `d` once.
Record the firmware hash and the build folder in the results.

### 0.2 Phone and clients

- Install the latest CI debug APK from `wmr-companion` `main`; record its commit. The app is
  the only client used here that counts per-hand `sequence` numbers and exports the run as
  JSON.
- Install nRF Connect as well. The MTU and CCC items (§5–§9) need a client that can request a
  specific MTU and read/write the CCC descriptors; the app always requests 517.
- **One client at a time.** The bridge serves a single phone; disconnect one before opening
  the other.
- Keep the phone on a charger and the app in the foreground. Do not lock the screen during a
  timed run: on some phones a locked screen or Doze pauses callbacks, which would show up as
  gaps and would be a phone finding, not a bridge result.

### 0.3 Position and monitor

- Phone and ESP32 fixed about 1 m apart; both controllers on marked spots on the table, awake,
  and not moved between the runs of one section. Note the distance, orientation and phone
  model. A photo is the record.
- Keep one monitor log for the whole session and note the wall-clock time at the start and end
  of each section, so the blocks can be found again:

```powershell
cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge -p COM5 -b 115200 monitor | Tee-Object -FilePath C:\Users\Nick\btstack-wmr\robustness.txt
```

### 0.4 Baseline block

Reset the ESP32 so the counters start at boot. Connect the phone in the app and let it
subscribe; wake both controllers. The first full block should read (values illustrative):

```
STREAM phone=1 sub=1 stream=decoded mtu=517 conn=[15.00ms lat=1 to=5000ms] q=0/256 high=3 notify=1234 errors=0 bridge_hands=0
  FF14 batches_tx=0 records_in_batches=0 mean=n/a max=0 batch_full_flushes=0
  FF15 echo ...
  LEFT  rx=... tx=... drop=0 offline=0 purged=0 mtu_skip=0 dec_err=0 decimated=0 ...
  LEFT link: ... mode=active rate_rx=.../s dev_missing=... last5s=...% cum=...% period=4.99ms discont=0
  RIGHT ...
```

Record this block as the counter baseline. All counters are **since boot**; a pass below is
about the change within the section, not about absolute zeros (unless the section says it
follows a fresh reboot). If `bridge_hands` is not `0`, stop: wrong image.

## 1. Still-required 2 + FF12 6–7 — ten minutes, FF12, both hands

| | |
| --- | --- |
| Do | App → Latency test: stream **FF12**, echo **off**, interval 250 ms, length **Until stop**. Start, note the time, leave the rig alone for 10:00, then Stop and Export. |
| | Record the serial block at the start (the first full block after Start) and at 10:00. |
| | Copy five consecutive `LINKLOG,` lines from the middle of the window. |
| Pass (log terms) | Both hands `rate_rx` 150–180/s in every block after the first; `rate_tx` within 2/s of `rate_rx` (FF12: one record per notification, so `tx` = records). |
| | `drop` delta 0; `offline` 0 throughout (the phone stays subscribed); `mtu_skip` 0; `dec_err` 0; `decimated` 0; `errors` 0. |
| | `q` 0 in every block (a single 1–2 is tolerated); `high` stops rising after the first minute and stays below 64. |
| | Accounting identity at the last block: `rx(LEFT)+rx(RIGHT) = tx+drop+offline+purged+mtu_skip+dec_err+decimated` summed over hands, `+ q`. |
| | FF12 6, app JSON per hand: `sequence_gaps` 0, `sequence_loss` 0, `out_of_order` 0, `rows_dropped` 0, `rate_per_s` 150–180. |
| | FF12 7: total phone notification rate (sum of the two `rate_per_s`) about 300–360/s, sustained over the middle five minutes, with `drop` not growing; record the negotiated `conn=` interval and `mtu=`. |
| Paste back | The exported `latency-*.json`; the first and last full `STREAM` blocks (all lines, including FF14/FF15/LEFT/RIGHT/link); five consecutive `LINKLOG,` lines from the middle; the firmware hash, app commit, phone model/Android and position. |

If the phone sleeps, the app loses rows, the phone reconnects, or `offline` rises, the ten
minutes are broken: restart this section. Do not patch the window.

## 2. FF12 5 — one hand, 60 s

| | |
| --- | --- |
| Do | Leave only one controller connected (put the other to sleep or take its battery out; confirm `no ACL link` on the other hand). App → Latency test: **FF12**, echo off, 60 s. Start, Stop, Export. |
| Pass (log terms) | The connected hand: `sequence_gaps` 0, `sequence_loss` 0, `out_of_order` 0, `rows_dropped` 0. |
| | Serial: `rate_rx` 150–200/s for that hand, `rate_tx` tracks it, `drop`/`errors` deltas 0. The other hand shows `no ACL link` and does not stream. |
| Paste back | The JSON; which hand was connected; the `STREAM` and `link:` blocks either side of the run; MTU/interval. |

Reconnect the second controller afterwards (power it on or press `l`/`r` as appropriate).

## 3. Still-required 6 + FF12 15 — phone disconnect and reconnect

| | |
| --- | --- |
| Do | With FF12 streaming both hands, note the last `sequence` per hand from the app's Debug screen. |
| | Turn the phone's Bluetooth off (or disconnect in the app) and leave it off about 10 s. Note the console lines and the `STREAM`/`s` block. |
| | Turn Bluetooth on and reconnect in the app. The app requests MTU 517 and High priority and writes the CCC again. Note the console lines and the block after subscribe. |
| | Note the first `sequence` per hand after subscribe, and the `offline`/`purged` deltas. |
| Pass (log terms) | On disconnect: `BLE phone disconnected, reason=0x..`; `phone=0 sub=0 stream=none mtu=0`; incoming reports are counted `offline` (delta > 0) and not queued. Advertising restarts, but there is no log line for it: the evidence is that the bridge is visible again and the phone can reconnect. |
| | On reconnect: exactly **one** `BLE phone connected, handle=0x....` line (no duplicate); `BLE decoded notifications enabled` only after the CCC write; `mtu=517` (negotiated; compare with the app's Connect screen `MTU (negotiated)`); `stream=decoded`; `sub=1`; `notify` rises again. |
| | `purged` counts whatever was queued at connect/subscribe; **no stale record**: the first `sequence` per hand after subscribe is greater than the last one before the disconnect, and no record with a sequence at or below that last value arrives. |
| | FF12 15: the subscription is not remembered (`stream=none` until the CCC is written again); after the write the stream starts fresh. |
| Paste back | The console lines from disconnect to the first streaming block; the `STREAM`/`s` blocks before and after; first and last `sequence` per hand; `offline` and `purged` deltas; the app's negotiated MTU. |

## 4. B4 — FF14 adds no phone-side sequence gaps (paired FF12/FF14)

| | |
| --- | --- |
| Do | Both controllers, position unchanged. App → Latency test: **FF12**, echo off, 120 s; Export. |
| | Without moving anything, select **FF14**, 120 s; Export. |
| Pass (log terms) | FF14, per hand: `sequence_gaps` 0 and `out_of_order` 0; `rows_dropped` 0. |
| | FF14 gaps are not greater than the FF12 gaps for the same hand: batching adds none. If FF12 itself has a gap, record it; B4 fails only if FF14 adds more. |
| | No repeated or reordered `sequence` within a hand: in the CSV there is no `seq_delta` ≤ 0 (the app counts those in `out_of_order`); within a batch, sequences increase by 1. |
| | `records` and `rate_per_s` in FF14 are in the same range as FF12 (record both); MTU 517 and the same interval for both runs. |
| Paste back | Both JSONs (or at least per-hand `sequence_gaps`, `out_of_order`, `records`, `rate_per_s`); the two `STREAM` blocks; MTU/interval; the position note. |

## 5. FF12 11–12 — CCC read-back and the inactive stream

nRF Connect, MTU 517, both controllers streaming.

| | |
| --- | --- |
| Do | Enable FF12. Read the FF11 CCC, the FF12 CCC and (for completeness) the FF14 CCC. |
| | While FF12 is active, write `0x0000` to the FF11 CCC. |
| | Confirm FF12 is still streaming, and note the next block. |
| Pass (log terms) | FF12 active: the FF12 CCC reads `0x0001`; the FF11 (and FF14) CCCs read `0x0000`. |
| | The `0x0000` write to FF11 succeeds; the console logs `BLE raw notifications disabled`; `stream=decoded` is unchanged; `notify` keeps rising; `purged` does not increase (no queue reset). |
| Paste back | The CCC value read for each characteristic; the write result; the console line; `STREAM` blocks before and after. |

When FF11 is active in §8, read both CCCs again and add that state here: FF11 `0x0001`, FF12
`0x0000` (and FF14 `0x0000`). FF12 11 wants both states, not only the FF12-active one.

## 6. B6 — MTU 247

| | |
| --- | --- |
| Do | **Reboot the ESP32** (fresh counters: `max` and `mean` are since boot). Wait for both controllers to reconnect from their stored addresses, then reconnect nRF Connect. |
| | Request MTU **247**; enable FF14; both hands; 60 s. |
| Pass (log terms) | `STREAM ... mtu=247` (the negotiated value; record nRF Connect's own readout too). |
| | FF14 `max` ≤ 4; every notification value is at most 226 bytes and starts `02 NN`, with `1 ≤ NN ≤ 4`; value length `2 + 56*NN`. |
| | `batches_tx` rises; `records_in_batches / batches_tx` is at most 4 and close to it in this two-hand run (record the value); `drop` delta 0; `errors` 0; `mtu_skip` 0. |
| Paste back | The `STREAM` and `FF14` lines at start and end; a few notification value lengths and first bytes; `max`; the negotiated MTU; the duration. |

If `max` reads more than 4, check that no MTU 517 FF14 run happened after the reboot; `max`
never decreases, so a carried-over value invalidates this check.

## 7. B7 — MTU below 61

Same nRF Connect session, after §6.

| | |
| --- | --- |
| Do | Reconnect at the default MTU **23** (do not request an MTU); enable FF14; both hands; 30 s. Note the FF14 line at the start and end. |
| | If the phone allows, reconnect and request MTU **59**; enable FF14; 30 s. Repeat at **60**. |
| | At 59 and 60, write `0x0000` to the FF14 CCC, enable FF12, and run 30 s. |
| Pass (log terms) | MTU 23: `mtu=23`; no FF14 notifications; `batches_tx` delta 0; `mtu_skip` rises on **both** hands; `drop` delta 0; `q=0`. |
| | MTU 59 and 60: the same for FF14 (`batches_tx` delta 0, `mtu_skip` keeps rising). With FF12 enabled at the same MTU: `stream=decoded`, `notify` rises, `mtu_skip` stops rising. |
| | If the phone refuses 59 or 60 (the negotiated value stays 23 or 247), record that; the MTU 23 half still counts, but do not claim the 59/60 half. |
| Paste back | The negotiated MTU per phase; the `STREAM`/`FF14` lines; `mtu_skip` and `batches_tx` deltas; notification counts; any refusal. |

## 8. B11 — switching FF12 → FF14 → FF11

nRF Connect, MTU 517, both controllers. One switch at a time; wait for a block between.

| | |
| --- | --- |
| Do | Enable FF12; let it stream; note `notify` and a few sequences. |
| | Write `0x0000` to the FF12 CCC; enable FF14; note the first batch bytes and the FF14 line. |
| | Write `0x0000` to the FF14 CCC; enable FF11; note the first notification bytes. |
| Pass (log terms) | Each CCC write logs the expected `BLE ... notifications enabled/disabled`; `stream` goes `decoded` → `none` → `batched` → `none` → `raw`. |
| | Each switch starts a fresh stream: `purged` counts what was queued; no stale batch — the first FF14 batch contains no record numbered before the FF12 switch, and the first FF11 fragment follows the last FF14 record. |
| | No old-format values after a switch: FF12 starts `01 <hand> 00`, FF14 starts `02 NN`, FF11 starts `52`. `errors` 0; `drop` delta 0. |
| Paste back | The switch lines; a `STREAM` block after each; `purged` deltas; the first notification bytes for each stream; the last FF12 and first FF14 `sequence` around the switch. |

## 9. B10 — FF12 and FF11 unchanged

Same nRF Connect session; FF11 is active from §8.

| | |
| --- | --- |
| Do | X1: confirm `decimated=0` on both hands, no `DIAGNOSTIC BUILD` line, and FF12 rates/identity as in §1. |
| | Let FF11 run 60 s; note the rates and the first fragment. |
| Pass (log terms) | X1: `decimated=0`; no diagnostic line; FF12 `stream=decoded` with rates and identity in the same range as §1. |
| | FF11: `stream=raw`; notifications start `52` (magic) and the 12-byte header matches PROTOCOL.md (`total_len=46`, `offset`, `chunk_len`); `notify` ≈ the sum of `tx` (one fragment per frame at MTU 517; six fragments per frame at MTU 23); `FF14 batches_tx` delta 0; rates comparable to the earlier bench run (record them; there is no fixed threshold). |
| Paste back | The `STREAM` blocks; the first fragment hex; the rates; the X1 evidence. |

At 517 an FF11 frame is one 58-byte notification, so `notify` and `tx` should grow together.
At MTU 23 the same frame takes six notifications; record which MTU the FF11 run used.

## 10. Still-required 11 — the 1-hour soak, with Still-required 3–5, 9 and B8 done during it

### 10.1 Start the soak

- **Reboot the ESP32** for a clean counter baseline (`t_ms`, `drop`, `errors`, `high` and
  `offline` start at 0). Wait for both controllers, connect the app, select **FF14**, MTU 517.
- Confirm `STREAM phone=1 sub=1 stream=batched mtu=517 q=0/256 bridge_hands=0`, both hands
  streaming, and note the wall-clock start plus the first `STREAM`, `FF14` and `LINKLOG` lines.
- Start a 1-hour timer. Leave the app in the foreground; do not stop the stream until §10.6.
  The soak is not a Latency export: use the Debug screen for the running `seq gaps` and
  packet rate.

### 10.2 Still-required 3 and 5 — incoming connection and controller reconnect

Do one hand at a time, with the phone subscribed and the other hand streaming.

1. Power-cycle LEFT (off, then on). Expected: `Incoming HID: address=... cid=... status=0x00`,
   `LEFT accepting incoming HID connection, cid=...`, `LEFT connected, cid=...; waiting for
   descriptor`, `LEFT descriptor status=0x00`, `LEFT first report: ...`. RIGHT keeps
   streaming throughout.
2. Repeat for RIGHT.

| | |
| --- | --- |
| Pass (log terms) | Each hand reconnects by itself and its `rate_rx` returns to about 150–200/s. `rx` continues from where it was (not reset), and `drop` does not grow. |
| | The other hand's `rate_rx` stays in its normal range in every block during the outage (record the lowest block). |
| | Sequence continues across the reconnect: the app's per-hand `seq gaps` does not increase across the window (read it before and after), and no `out_of_order` appears. `dev_missing` resets on the reconnect and `last5s` is `n/a` for the first block — expected, not a failure. |
| Paste back | The reconnect lines for each hand; two `STREAM` blocks during the outage and two after; the Debug screen's per-hand `seq gaps` before and after. |

### 10.3 Still-required 4 — outgoing `l` and `r`

1. Press `L` (or `R`) to disconnect one hand, then `l` (or `r`) to connect it again by its
   stored address. One hand and one attempt at a time. If the controller wins the race and
   reconnects inward before `l`/`r`, record that and repeat; `l`/`r` is only exercised while
   the hand is disconnected.
2. Repeat for the other hand.

| | |
| --- | --- |
| Pass (log terms) | `Connecting LEFT <addr>`, `LEFT connected, cid=...; waiting for descriptor`, `LEFT descriptor status=0x00`, `LEFT first report: ...`, then `rate_rx` resumes. Counters are not reset. The console refuses a second attempt while one is pending (`Another connection/discovery is pending`). |
| Paste back | The console lines for each command; `s` before and after; the next `STREAM` block. |

### 10.4 Still-required 9 — manual bond reset `k` / `K`

Do one hand at a time.

1. With LEFT connected, press `k`. Expected: `BOND RESET refused: wait until LEFT is
   disconnected and no connection attempt is pending`.
2. Press `L`, then `k`. Expected: `BOND RESET: removed ESP32's stored LEFT link key only. Put
   LEFT into pairing mode, then press l.`
3. Put LEFT in pairing mode and press `l`. Expected: `Connecting LEFT ...`, the SSP accept
   line, `LEFT descriptor status=0x00`, and reports flow.
4. Repeat for RIGHT with `K` / `R` / `r`.

| | |
| --- | --- |
| Pass (log terms) | Refused while the hand is connected; the key is deleted while it is disconnected; the hand re-pairs and streams again afterwards. The other hand keeps flowing throughout. |
| Paste back | The four lines per hand (refused, removed, connecting/SSP, descriptor); `s` after re-pairing; the next `STREAM` block. |

### 10.5 B8 — partial flush with one controller

1. During the soak, disconnect one hand and keep the other streaming. Note the `FF14` line.
2. Run 60 s with one hand. Note the `FF14` line again, and the Debug screen's `seq gaps`.
3. Reconnect the hand; the soak continues.

`mean` and `max` are cumulative since boot, so use **deltas** between the two `FF14` lines.

| | |
| --- | --- |
| Pass (log terms) | Δmean = Δ`records_in_batches` / Δ`batches_tx` between 1 and about 5 (record it; at 15 ms and one hand it should be roughly the reports per interval, about 2–4). |
| | `batch_full_flushes` delta 0 or a few; `drop` delta 0; `errors` 0. |
| | The phone sees every record: `seq gaps` on the Debug screen does not increase; no `out_of_order`. |
| Paste back | The `FF14` lines before and after (so the deltas can be checked); the Debug `seq gaps`; the interval and MTU. |

### 10.6 Finish the soak

After **at least 1 hour** (longer is better), note the wall-clock end and the last `STREAM`,
`FF14` and `LINKLOG` lines, then stop.

| | |
| --- | --- |
| Pass (log terms) | `errors` delta 0; `drop` delta 0 after the first minute; `q=0` in blocks; `high` does not keep rising after §10.2–§10.5; `vhci_drop=0`; `log_errors=0`. |
| | `t_ms` in `LINKLOG` increases monotonically; end − start ≥ 1 hour; no reboot mid-run (no second `BLE advertising`/boot banner, and `t_ms` never drops). |
| | Both hands recovered after the deliberate tests and streamed again. Controller sleep events are recorded, not treated as failures. |
| | **Heap: not printed by this build.** There is no heap line in the current firmware output, so record “not observable in this build” and do not claim a heap figure. A heap reading needs a firmware logging change, which is not part of this sheet. |
| Paste back | The first, middle and last `STREAM` blocks; the first and last `LINKLOG` lines; the `t_ms` span; wall-clock start and end; any disconnect/sleep/reboot lines; the stream and MTU. |

## 11. Two-bridge delta — PLACEHOLDER (do not run)

> **Placeholder. Deliberately empty.** This sheet is for one bridge, default build. The
> two-bridge setup (one hand per bridge, `WMR_CFG_BRIDGE_HANDS=1/2`) and its hand-exclusion
> checks are in [two-bridge-hands.md](two-bridge-hands.md). Which items above become
> per-bridge versus once per rig, how the two bridges' logs are labelled, and what changes for
> the phone when it connects to two bridges are to be added after the M6 decision. No
> two-bridge steps are drafted here, and nothing in this section is a run instruction.

## 12. Results log (empty)

Fill one row per item. “Pass?” is for Nick's observation; this sheet itself makes no claim.

| Item | Section | Pass? | Pasted back |
| --- | --- | --- | --- |
| Still-required 2 | §1 | | |
| Still-required 3 | §10.2 | | |
| Still-required 4 | §10.3 | | |
| Still-required 5 | §10.2 | | |
| Still-required 6 | §3 | | |
| Still-required 9 | §10.4 | | |
| Still-required 11 | §10.6 | | |
| FF12 5 | §2 | | |
| FF12 6 | §1 | | |
| FF12 7 | §1 | | |
| FF12 11 | §5, §8 | | |
| FF12 12 | §5 | | |
| FF12 15 | §3 | | |
| B4 | §4 | | |
| B6 | §6 | | |
| B7 | §7 | | |
| B8 | §10.5 | | |
| B10 | §9 | | |
| B11 | §8 | | |

Session record: firmware `<hash>`, app `<commit>`, phone `<model, Android>`, ESP32 `<board,
COM>`, position `<distance, orientation, photo>`, date/time.
