# Run sheet: robustness — remaining TESTING.md items in one sitting

Status: proposed, not run. No line here is a result. One bridge, default build (`WMR_CFG_BRIDGE_HANDS=0`). Every `STREAM` block must show `bridge_hands=0`.

This sheet folds in the old `closeout.md` checks; §11 is that list.

## What is covered

Still-required 2-6, 9, 11; FF12 5-7, 11, 12, 15; B4, B6-B8, B10, B11; the Connect-screen MTU, gamepad mapping/persistence, HID, C7 (FF13 read off), the fixed-position round-trip rerun and bench housekeeping.

## 0. Before the sitting

**Flash once**, `main` at or after the commit that merges this sheet. Record the
hash:

```powershell
$clone = "$env:TEMP\WMR_Bridge-robustness"
if (Test-Path $clone) { Remove-Item $clone -Recurse -Force }
git clone https://github.com/nightcinder/WMR_Bridge $clone
git -C $clone log --oneline -1
robocopy $clone C:\Users\Nick\btstack-wmr\wmr_bridge /E /XD .git captures __pycache__ .github venv
Copy-Item $clone\integration\main-CMakeLists.txt C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Force
Select-String C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Pattern "wmr_batch|wmr_echo|wmr_discovery|wmr_cfgmgr"
cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge build
idf.py -B build-wmr-bridge -p COM5 -b 115200 flash monitor
```

Robocopy exit codes below 8 are success. Controllers reconnect from NVS; if not, pair them and press `d` once.
- App: latest CI debug APK from `wmr-companion` `main`; record its commit.
- Also install nRF Connect for the MTU and CCC checks.
- One client at a time. Keep the phone on a charger and the app in front; do not lock the screen during a timed run.
- Fixed position: phone and ESP32 about 1 m apart, controllers on marks, awake. A photo is the record.
- Keep one monitor log for the session and note the wall-clock at each section boundary.
- Reset the ESP32 before each section so counters start at boot. Record the first full `STREAM` block as the baseline. If `bridge_hands` is not 0, stop: wrong image.

## 1. Still-required 2 + FF12 6-7 (10 min, both hands)

Do: app -> Latency test, **FF12**, echo off, 250 ms, length Until stop. Run 10:00, then Export. Record the serial block at start and end and five middle `LINKLOG,` lines.

Pass: both hands `rate_rx` 150-180/s after the first block; `rate_tx` within 2/s; `drop`, `offline`, `mtu_skip`, `dec_err`, `decimated`, `errors` deltas 0; `q` 0 (a single 1-2 is tolerated); `high` stops rising and stays below 64. App JSON: per hand `sequence_gaps`, `sequence_loss`, `out_of_order`, `rows_dropped` all 0. Total notification rate ~300-360/s. Accounting identity holds.

If the phone sleeps, reconnects or `offline` rises, restart the section.

## 2. FF12 5 (one hand, 60 s)

Leave one controller connected; the other must show `no ACL link`. Run FF12 60 s. Pass: gaps, loss, out-of-order and rows-dropped all 0; `rate_rx` 150-200/s; `rate_tx` tracks it; `drop`/`errors` deltas 0.

## 3. Still-required 6 + FF12 15 (phone reconnect)

With FF12 streaming, note the last sequence per hand. Turn phone Bluetooth off for about 10 s, then reconnect.

Pass: on disconnect `phone=0 sub=0 stream=none mtu=0`; reports count `offline`, not queued. On reconnect exactly one `BLE phone connected`; CCC written again; `mtu=517`; `sub=1`; `notify` rises. No stale record: the first sequence after subscribe is greater than the last before disconnect. FF12 15: the subscription is not remembered until the CCC write.

## 4. B4 (FF14 adds no gaps)

Run FF12 120 s, then FF14 120 s without moving anything. Pass: FF14 per hand `sequence_gaps` and `out_of_order` 0; gaps not greater than FF12; no `seq_delta` <= 0; records/rate in the FF12 range; same MTU and interval.

## 5. FF12 11-12 (CCC read-back, inactive stream)

nRF Connect, MTU 517, both controllers streaming. Enable FF12; read the FF11, FF12 and FF14 CCCs. Pass: FF12 `0x0001`, FF11 and FF14 `0x0000`. Write `0x0000` to FF11 while FF12 is active: write succeeds, `BLE raw notifications disabled`, FF12 unchanged, `purged` does not rise. Repeat the CCC read with FF11 active later; FF12 11 wants both states.

## 6. B6 (MTU 247)

Reboot for fresh counters. Request MTU **247**, enable FF14, both hands, 60 s. Pass: `mtu=247`; FF14 `max` <= 4; values at most 226 bytes and start `02 NN` with `1 <= NN <= 4`; length `2 + 56*NN`; `batches_tx` rises; `records/batch` at most 4; `drop`, `errors`, `mtu_skip` deltas 0. If `max` is above 4, a 517 run has leaked into the counters; repeat after a reboot.

## 7. B7 (MTU below 61)

Same session. Reconnect at MTU **23** without requesting one; enable FF14; both hands; 30 s. If the phone allows, repeat at **59** and **60**.

Pass at 23: no FF14 notifications, `batches_tx` delta 0, `mtu_skip` rises on both hands, `drop` 0, `q=0`. At 59/60: FF14 still sends nothing and `mtu_skip` rises; FF12 works at the same MTU (`stream=decoded`, `notify` rises, `mtu_skip` stops rising). If the phone refuses 59/60, record it; do not claim that half.

## 8. B11 (switch FF12 -> FF14 -> FF11)

nRF Connect, MTU 517, both controllers. Enable FF12, then disable, enable FF14, disable, enable FF11. Wait for a block between switches.

Pass: each CCC write logs the expected enabled/disabled line; `stream` goes `decoded -> none -> batched -> none -> raw`; each switch starts fresh (`purged` counts the old queue; no stale batch). First bytes: FF12 `01 <hand> 00`, FF14 `02 NN`, FF11 `52`. `errors` and `drop` deltas 0.

## 9. B10 (FF12 and FF11 unchanged)

X1: `decimated=0`, no diagnostic line, FF12 rates/identity as in §1. Let FF11 run 60 s: `stream=raw`, first byte `52`, header matches PROTOCOL.md, `notify` tracks `tx` (one fragment per frame at MTU 517, six at MTU 23), FF14 `batches_tx` delta 0.

## 10. Still-required 11 (1-hour soak) + 3-5, 9, B8

Reboot for clean counters. Connect the app, select **FF14**, MTU 517. Confirm `phone=1 sub=1 stream=batched mtu=517 bridge_hands=0`. Start a 1-hour timer. Leave it running; do the tests below during it.
- **Incoming/reconnect (3, 5):** power-cycle LEFT, then RIGHT, one at a time. Pass: `Incoming HID`, accept, descriptor, first report; `rate_rx` returns to 150-200/s; `rx` continues; `drop` does not grow; other hand keeps streaming.
- **Outgoing (4):** press `L`/`R`, then `l`/`r`, one hand at a time. Pass: connecting, descriptor, first report, `rate_rx` resumes, counters kept.
- **Bond reset (9):** with a hand connected, `k`/`K` is refused. Disconnect, press `k`/`K`, put the controller in pairing mode, press `l`/`r`. Pass: key removed, re-pair and stream.
- **B8 partial flush:** disconnect one hand for 60 s. Pass: delta mean 1-5 records/batch; `batch_full_flushes` delta 0 or a few; `drop`/`errors` 0; no phone gaps.

After at least 1 hour, record the last block. Pass: `errors`/`drop` deltas 0 after the first minute; `q=0`; `high` stable; `vhci_drop=0`, `log_errors=0`; `t_ms` monotonic and span >= 1 hour; no reboot. Heap is not printed by this build: record "not observable".

## 11. Close-out checks (folded from closeout.md)
- **Connect-screen MTU:** pass 517, or >= 61 with FF14 still chosen. Fail 23 or 247 when the phone supports 517. Paste the number, phone and ESP32 `mtu=`.
- **Gamepad:** each button lights only its lamp; stick up gives +y; triggers rise smoothly (record full pull); trackpad corners map LEFT Up/Down/Left/Right and RIGHT Y/A/X/B. Note any inversion.
- **Settings persist:** change two settings, wait 1 s, swipe the app away and reopen. Both values must remain. Stick centre and HID state are not saved.
- **HID on a phone and host (optional, PC):** follow [`GAMEPAD.md`](https://github.com/nightcinder/wmr-companion/blob/main/docs/GAMEPAD.md); record the prompt, status, mappings, inversion and any stall.
- **C7, FF13 read off (one reflash):** set `WMR_CFG_FF13_READ=0`, build and flash. Run 3 min with both hands, FF14, MTU 517. Pass: no `FF13` lines, `FF13 state 0`, `other=0`, `rate_rx` ~165-200/s. Paste the log, first `STREAM`, 10 `LINKLOG,` lines and commit. Restore and reflash afterwards.
- **Fixed-position round trip:** tape phone and ESP32. FF14, MTU 517, 15 ms, 120 s. Pass: 100 % answered; compare p50/p99 with 40.8/97.1 ms. Paste exports, RSSI start/end and image.
- **Housekeeping:** remove `wmr-companion-ff12`, `wmr-apk*` and `%TEMP%\wmr-key-test`; delete merged `claude/project-thread-*` branches.
- FF14 B9 needs the app to log both streams; it is not covered here.

## 12. Results log

| Item | Section | Pass? | Pasted back |
|---|---|---|---|
| Still-required 2 / FF12 6-7 | §1 | | |
| FF12 5 | §2 | | |
| Still-required 6 / FF12 15 | §3 | | |
| B4 | §4 | | |
| FF12 11-12 | §5, §8 | | |
| B6 | §6 | | |
| B7 | §7 | | |
| B11 | §8 | | |
| B10 | §9 | | |
| Still-required 3, 4, 5, 9, 11, B8 | §10 | | |
| Close-out checks | §11 | | |

Session: firmware `<hash>`, app `<commit>`, phone `<model, Android>`, board `<model, COM>`, position `<distance, orientation, photo>`, date/time.
