# Run sheet: close-out checks (every hardware item still marked "not yet run")

Order is chosen for the fewest reflashes: one flash of firmware `main` (it already contains the FF13 fix, PR #21), all phone-side checks on it, then one flash of the FF13-off build for C7, with the optional round-trip rerun on that same image. Nothing here is a result yet; paste back what each step asks for and I will mark it user-reported.

Phone: install the APK from the latest `main` first (Ixhel: `git pull` in wmr-companion on `main`, then the usual build and `adb install -r`). Controllers face down and still unless a step says otherwise.

## 0. Flash `main` once (skip if the board already runs `main` at or after commit `203108c`)

Anya's ESP-IDF PowerShell:

```powershell
$clone = "$env:TEMP\WMR_Bridge-main"
if (Test-Path $clone) { Remove-Item $clone -Recurse -Force }
git clone https://github.com/nightcinder/WMR_Bridge $clone
git -C $clone log --oneline -1                      # note the hash
robocopy $clone C:\Users\Nick\btstack-wmr\wmr_bridge /E /XD .git captures __pycache__ .github venv
Copy-Item $clone\integration\main-CMakeLists.txt C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Force
Select-String C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Pattern "wmr_cfgmgr"    # must print a line
cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge build
idf.py -B build-wmr-bridge -p COM5 -b 115200 flash monitor
```

Robocopy exit codes below 8 are success. Both controllers should connect without discovery (addresses are in NVS).

## 1. Negotiated MTU on the Connect screen (phone only)

1. Open the app, connect to `WMR Bridge`.
2. Read the MTU shown on the Connect screen.
3. Pass: 517, or any value of 61 or more with the app still choosing FF14. Fail: 23 or 247 although the phone supports 517.
4. Paste back: the number shown, the phone model and Android version, and the ESP32 `STREAM ... mtu=` value from the monitor.

## 2. Gamepad 4-step check (phone only, FF12 or FF14)

Open the Gamepad screen from the debug screen's app bar. Wake both controllers. Do the five checks in `docs/GAMEPAD.md` ("To check on hardware"):

| Step | Do | Pass | Paste back |
|---|---|---|---|
| 1 | Press each button on each hand | Matching lamp lights and nothing else | Any button with a wrong or missing lamp |
| 2 | Push each stick up | y goes positive | Whether "Invert stick Y" was needed |
| 3 | Pull each trigger slowly | Value rises smoothly; note the highest value at full pull | Highest raw value per trigger (sets the full scale) and where L2/R2 light |
| 4 | Click trackpad top, bottom, left, right | LEFT: Up/Down/Left/Right, RIGHT: Y/A/X/B | Whether "Invert trackpad Y" was needed |
| 5 (optional) | Tilt to the right stick | Lean top right gives +x, tip away gives +y | Any sign that is reversed |

## 3. Gamepad settings persist across a restart (phone only)

1. On the Gamepad screen change two settings, for example trigger dead zone and "Invert stick Y".
2. Wait one second (the file is written 0.4 s after the last change).
3. Swipe the app away and reopen it, then open the Gamepad screen.
4. Pass: both changed values are still set. Fail: defaults are back.
5. Paste back: pass or fail, and which settings you changed. The stick dead zone and centre on the debug screen and the HID state are not saved by design; do not count those.

## 4. Bluetooth HID output on a phone and host (optional, needs a PC or tablet)

Steps are in `docs/GAMEPAD.md` ("To check on a phone and host"). Paste back: whether the permission prompt appeared, whether the status line said "Connected to <host>", which host button each controller input landed on, any inverted stick, and whether input stalled with the app in the foreground.

## 5. FF14 B4 and B9 (phone only, firmware from step 0)

These were never run on FF14. Use the numbering in firmware `docs/TESTING.md`. Paste back: the line or screen that shows each result, or "could not do it" with the reason.

## 6. Firmware C7: FF13 read switched off (one reflash)

Goal: with `WMR_CFG_FF13_READ=0` the bridge sends no config command, shows no `FF13` lines, keeps FF13 state at 0 and streams as it did before FF13.

```powershell
$cfg = "C:\Users\Nick\btstack-wmr\wmr_bridge\src\wmr_config.h"
(Get-Content $cfg) -replace '(#define WMR_CFG_FF13_READ\s+)1','${1}0' | Set-Content $cfg
Select-String $cfg -Pattern "define WMR_CFG_FF13_READ"        # must end in 0
cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge build
idf.py -B build-wmr-bridge -p COM5 -b 115200 flash monitor | Tee-Object -FilePath C:\Users\Nick\btstack-wmr\c7.txt
```

If `Select-String` shows the define wrapped in `#ifndef`, check that the value line ends in 0 and rebuild with `idf.py -B build-wmr-bridge fullclean` only if the value did not change.

Run: reset the ESP32, wake both controllers, phone connected and subscribed to FF14 (MTU 517), wait 30 s, leave it 3 minutes.

| Check | Pass | Fail |
|---|---|---|
| Console | No `FF13` lines | Any `FF13 ... config block` line |
| Statistics | `FF13 state 0`, `other=0` | State other than 0 |
| `link:` lines | `rate_rx` about 165-200/s per hand, as before FF13 | `rate_rx=0.0/s` |

Paste back: the saved `c7.txt` (or its first `STREAM` block, the FF13 lines if any, and 10 `LINKLOG,` lines) and the commit hash.

## 7. Optional: fixed-position round-trip rerun (on the C7 image, no further flash)

Why: the first run (user-reported, 2026-10-22) had RSSI move from -69 to -48 dBm, so the position could not be treated as fixed.

1. Fix the phone and the ESP32 in place with tape. Controllers face down.
2. App: FF14, MTU 517, 15 ms interval, run the latency test for 120 s. Do not touch anything.
3. Export the latency JSON and echo CSV from the app.
4. Pass criteria: 100 % answered. Compare p50 and p99 with the first run (p50 40.8 ms, p99 97.1 ms). A p50 well under 40 ms at steady RSSI would point at the earlier position; the same p50 would point at the radio or the stream.
5. Paste back: the exported files, the RSSI at start and end, and the firmware image used (C7, FF13 off).

Afterwards, to put the normal image back: restore `WMR_CFG_FF13_READ` to 1 (re-run step 0).

## 8. Housekeeping on Ixhel (not a hardware check; the permission layer refused the assistant, so run it yourself)

Leftover folders from earlier sessions: `wmr-companion-ff12` is empty, `wmr-apk*` hold only downloaded APKs, and `%TEMP%\wmr-key-test` is a clean clone of a merged branch. (`%TEMP%\wmr-ff13` was already gone.) In PowerShell on Ixhel:

```powershell
Remove-Item C:\Users\Nick\source\repos\wmr-companion-ff12, C:\Users\Nick\source\repos\wmr-apk, C:\Users\Nick\source\repos\wmr-apk-capture, C:\Users\Nick\source\repos\wmr-apk-ff12, $env:TEMP\wmr-key-test -Recurse -Force
```

If a folder refuses to go (long paths or locks): `$e = "$env:TEMP\empty"; mkdir $e; robocopy $e <folder> /MIR; Remove-Item <folder> -Force`.

Merged branches on GitHub (delete from the branches page or `git push origin --delete <name>`): WMR_Bridge `claude/project-thread-5z6njd` and `claude/project-thread-6pi7k2`; wmr-companion `claude/project-thread-5z6njd`, `-dt8m36`, `-vrlh8e` and `-6pi7k2` (the last of each repo only after its handoff PR is merged).
