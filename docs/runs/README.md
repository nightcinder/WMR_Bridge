# Hardware runs and run sheets

Raw logs and run sheets from the 2026-10-22 bench sessions (everything here is user-reported: the project owner ran the hardware, the assistant analysed the logs). Device addresses are replaced by `ADDR-n`. Figures and conclusions are in the app repo's `docs/STATUS.md` and `docs/LATENCY.md`; this folder holds the raw material so the numbers can be re-derived.

## Verification campaign — start here

**Status: none of these sittings has been run.** This is a running order, not evidence. A sitting
stays *not run* until the owner pastes back the figures its sheet asks for; a green CI build is not
a hardware result. Each sheet still owns its procedure, go/no-go gates, pass criteria and
paste-back block — this table only says what to do first and where the answer lands.

The order is **reflash cost, cheapest first**. A reflash costs the owner a bench trip, so sittings
that run on the image already flashed come first, then the one-reflash sittings, then the close-out,
which needs two. The one-reflash rows are ordered so each flash replaces the previous row's
non-default image (R's address-swap image → B′'s LEFT image → C's both-hands image), so no flash is
spent only on restoring state. Sitting B (two bridges) is parked: it needs hardware the owner does
not have.

| Sitting | What it settles | Which build it needs | What to paste back | Where the write-up goes |
|---|---|---|---|---|
| **A — latency v2 + tracking quality**<br>one bridge, both hands — **not run**<br>[latency-2 (companion)](https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/latency-2.md) + [tracking-quality.md](sheets/tracking-quality.md)<br>[iOS v1 steps (companion LATENCY.md)](https://github.com/nightcinder/wmr-companion/blob/main/docs/LATENCY.md#flutter-library--ios-v1-run-phase-5) | The **M7 Flutter-vs-native decision** (milestone 5): app against the bare-Kotlin GATT probe on RTT p50 and >50 ms stalls, using latency-2's fixed rule. The **M8 fusion defaults**: calibration on/off, 90° turn error, yaw drift at beta 0.02/0.05/0.10. The **#19 unknowns**: raw trigger, trackpad, battery and temperature ranges. The iOS v1 (phase 5) steps run here too if an iOS 18 device is available. Does not test #18. | **The image already flashed, no reflash.** Both-hands `main` at or after the FF13 streaming fix (#21), with `bridge_hands=0`. Install the companion app debug APK and the GATT probe APK (CI artifact `wmr-gatt-probe-debug-apk`); no firmware build and no flash. | latency-2 §5 and §7 (JSON + echo CSV per run, `STREAM` lines, app/probe commits); tracking-quality §10–§11 (every CSV + JSON, serial log, cold/+30/end table); for the iOS v1 steps, the files and record listed in companion `docs/LATENCY.md`. | `wmr-companion/docs/STATUS.md` milestone 5 and `docs/LATENCY.md`; tracking → `docs/runs/sheets/tracking-quality.md` §11, then `wmr-companion/docs/STATUS.md` (M8.4) and `docs/DECODING.md` “Unknown” for the #19 values. |
| **R — RIGHT-alone asymmetry**<br>one board, no purchase (M12.6) — **not run**<br>[right-asymmetry.md](sheets/right-asymmetry.md) | **RIGHT's own loss** (~10.8 % alone against LEFT's ~0.5 %, user-reported): does it follow the physical controller, the firmware hand slot, or the table position? This is the product blocker; it needs no second board. It does not prove #18's contention explanation, which remains a working hypothesis. | **Phases 1–2: the image already flashed** (`main` at `41771f8` or later), no reflash. **Phase 3: one reflash** with the sheet's address-override `EXTRA_CFLAGS` build (§4), which swaps the two controller addresses between hand slots; it is not a `--variant` image, and it leaves the board on the swap image until the next row's flash replaces it. | right-asymmetry §6 (the per-run block for every A/B/P/S run) and §7 (results table). | `docs/runs/sheets/right-asymmetry.md` §7; `docs/STATUS.md` (#18 section and the next-step list); `wmr-companion/docs/STATUS.md` “The main open problem”. |
| **B′ — one board, `--variant left`**<br>(M12.3′) — **not run**<br>[multi-bridge.md §2.1](sheets/multi-bridge.md#21-one-board-instead-of-two-plan-m123) | Does **single-hand mode** boot, advertise as `WMR Bridge L` (`bridge_hands=1`) and stream? Does the app's **multi-bridge path** work against one real bridge? Are the **per-bridge figures** right for one bridge? A smoke/validation sitting only: it cannot test two-bridge contention, so #18 stays open and B stays parked. | **One reflash.** Windows: `python tools\firmware\build.py --variant left --flash COM5`<br>Linux/macOS: `python3 tools/firmware/build.py --variant left --idf-export ~/esp/esp-idf/export.sh --flash /dev/ttyUSB0`<br>Artifacts `wmr-bridge-left-factory.bin`, `manifest-left.json`, `build-info-left.json`; check `build-info-left.json` for variant `left`, hands 1, name `WMR_CFG_BLE_NAME_LEFT`. The LEFT image from the [browser installer (`docs/flasher/`)](../flasher/index.html) is the no-ESP-IDF route to the same variant when its `dist/` is served. This leaves the board on the LEFT image: plan a `--variant both` flash before the next both-hands sitting. | multi-bridge §5 build record and the §2.1 one-board checks. Do not paste it as a two-bridge pass. | `docs/STATUS.md` (single-hand mode row) and `multi-bridge.md` §2 build record/§5; `wmr-companion/docs/STATUS.md` (multi-bridge row). |
| **C — robustness soak + gamepad HID**<br>one bridge, PC for the HID half (M12.4) — **not run**<br>[robustness.md](sheets/robustness.md) + [closeout.md §4](sheets/closeout.md#4-bluetooth-hid-output-on-a-phone-and-host-optional-needs-a-pc-or-tablet) + [GAMEPAD.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/GAMEPAD.md) | The remaining **TESTING.md** items: 10 minutes of FF12 with both hands, the 1-hour FF14 soak, reconnects, bond reset, MTU/CCC/stream switching. Plus the **gamepad HID checklist on a PC**: does the phone present as a HID gamepad, do buttons/sticks/triggers land where expected, does input stall? | **One reflash** of the both-hands `main` at or after the robustness sheet's merge commit: `python tools\firmware\build.py --variant both --flash COM5` (Linux/macOS: `python3 ... --variant both --idf-export ... --flash /dev/ttyUSB0`). Default artifacts `wmr-bridge-factory.bin`, `manifest.json`, `build-info.json`; the **Both hands** image on the [browser installer (`docs/flasher/`)](../flasher/index.html) is the no-ESP-IDF route when its `dist/` is served. | robustness §12 results log plus each section's paste-back; closeout §4 for the HID lines. | `docs/runs/sheets/robustness.md` §12; `docs/STATUS.md` (capability rows); `wmr-companion/docs/STATUS.md` “Not yet run on hardware” and `docs/GAMEPAD.md`. |
| **Close-out checks**<br>one bridge, phone, PC for HID — **not run**<br>[closeout.md](sheets/closeout.md) | MTU on the Connect screen, FF14 B4/B9, FF12 5–7/11/12/15, C7 (FF13 read off), the fixed-position round-trip rerun, gamepad mapping/persistence, HID. Closes the last “not yet run” rows; C7 is the only firmware change. | **First flash: `python tools\firmware\build.py --variant both --flash COM5` (the default)**, skipped if the board already runs `main` at or after `203108c`; the **Both hands** image on the [browser installer (`docs/flasher/`)](../flasher/index.html) is the no-ESP-IDF route when its `dist/` is served. **Then one C7 reflash** with `WMR_CFG_FF13_READ=0` using closeout §6, and one restore flash afterwards. | closeout §1–§7, each section's own paste-back (numbers, lines, exported files). | `docs/runs/sheets/closeout.md`; `docs/STATUS.md` (C7, packaged version/flasher rows); `wmr-companion/docs/STATUS.md` (MTU/gamepad rows) and `docs/GAMEPAD.md`. |
| **B — two bridges, A/B/A/B twice**<br>**parked, not scheduled** (M12.3) — **not run**<br>[multi-bridge.md](sheets/multi-bridge.md) | **#18's disposition**: whether one bridge per controller removes the extra two-controller loss, and what RIGHT's own loss follows. The contention explanation is a working hypothesis, not a proven cause. **Cannot run: it needs a second Classic ESP32, which the owner does not have.** An ESP32-S3 cannot substitute — WMR controllers need Bluetooth Classic, which the S3 lacks. | **Three images from one commit**, then two boards:<br>`python tools\firmware\build.py --variant both --out dist-multi`<br>`python tools\firmware\build.py --variant left --out dist-multi`<br>`python tools\firmware\build.py --variant right --out dist-multi`<br>then flash per multi-bridge §2 (`--flash COM5` for board A, `--flash COM6` for board B). Artifacts `wmr-bridge-factory.bin`/`manifest.json`/`build-info.json` and the `-left`/`-right` variants. Two USB ports and two monitors are required. | multi-bridge §5 (whole-session and per-run blocks). | `multi-bridge.md` §5; `docs/STATUS.md` #18 section. When figures exist, a results file in the style of `contention-2-results.md`. |

**Before the first sitting.** Record the firmware hash and boot banner once (`bridge_hands=0`);
install the app debug APK from `wmr-companion` `main` (and, for A, the GATT probe APK); use one
phone client at a time and keep the phone on a charger. The browser installer needs Chrome or Edge
and a served `dist/` (its manifests and images); if it is not being served, use `build.py`. The
campaign does not replace any sheet's go/no-go table: if a sheet's prerequisite is not true, that
sitting stops there and is reported as not run, not as a failure.

**What closes the campaign.** M12.5: every pass criterion in these sheets answered with a
user-reported figure or explicitly declined, then the #18 disposition, the M7 decision, the M8
defaults and `docs/STATUS.md` in both repos written up. Until then, no sitting above is a result.

## Run sheets (`sheets/`)

| File | Purpose | State |
|---|---|---|
| `ff14-ab.md` | FF14 against FF12, A/B/A | run |
| `left-coex.md` | Auto latency and modem sleep off on LEFT | run, no effect shown |
| `left-baseline-repeat.md` | How far LEFT drifts with nothing changed | long baseline run |
| `single-controller.md` | LEFT alone, RIGHT alone, both | run |
| `contention-2.md` | Staggered connect and Classic link policy variants for issue #18 | run sheet (variants V2, V3 tested) |
| `contention-2-results.md` | Results of contention-2 runs (role check, V2 no-sniff, RTT separation) for issue #18 | **run** (user-reported): V3 no-op (master confirmed), V2 sniff=clear 17.5%/23.2% (no effect shown), ~40 ms RTT is BLE stack bound |
| `right-asymmetry.md` | One-board separation of controller, hand slot, and position for RIGHT's ~10.8 % loss; controller swap and position control (M12.6) | **not run** |
| `tracking-quality.md` | Static calibration check, 90° turns about each axis, yaw drift against Madgwick beta, and the #19 trigger/trackpad/battery/temperature unknowns (M8.1) | **not run** |
| `multi-bridge.md` | Two bridges, one hand each, against one bridge; S1/S2/X for RIGHT's own loss (M6.2) | **not run** |
| `two-bridge-hands.md` | One hand per bridge: the raw `EXTRA_CFLAGS` build and monitor checks behind `WMR_CFG_BRIDGE_HANDS` / `WMR_CFG_BLE_NAME` (M6.1) | **not run** |
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
