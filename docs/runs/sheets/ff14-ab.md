# Run sheet: ESP32 refresh, FF14 vs FF12 A/B/A (2026-10-22)

> Archived sheet. It was run on 2026-10-22 (user-reported); see ../README.md for the logs.

Firmware `main` = `8f5921a` (FF14 merged). Checks are WMR_Bridge `docs/TESTING.md` B1-B12 and "The comparison the director asked for". Nothing here has been run yet.

## 1. Refresh the build (Anya, ESP-IDF PowerShell; do not replace PATH)

```powershell
$clone = "$env:TEMP\WMR_Bridge-8f5921a"
if (Test-Path $clone) { Remove-Item $clone -Recurse -Force }
git clone https://github.com/nightcinder/WMR_Bridge $clone
git -C $clone log --oneline -1          # expect 8f5921a FF14 batched decoded stream ...

robocopy $clone C:\Users\Nick\btstack-wmr\wmr_bridge /E /XD .git captures __pycache__ .github venv
Copy-Item $clone\integration\main-CMakeLists.txt C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Force
Select-String C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Pattern wmr_batch   # must print a line

# Controller addresses: check src\wmr_config.h still has your two MACs (the clone has the repo defaults)
Select-String C:\Users\Nick\btstack-wmr\wmr_bridge\src\wmr_config.h -Pattern "ADDR"

cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge build        # expect "Project build complete"
idf.py -B build-wmr-bridge -p COM5 -b 115200 flash monitor
```

If the build log says it is building `gatt_counter` / LE Counter, `$env:EXAMPLE` is missing.

## 2. Phone client: nRF Connect for every phone run

The app cannot subscribe to FF14 yet, so use nRF Connect for all three phone runs (FF12 and FF14), so A and B differ only in the stream.

Per connection: connect to `WMR Bridge` > menu > Request MTU **517** > Request connection priority **High** > service `0xFF10` > tap the notify icon of **FF12** or **FF14**. To switch, tap notify off on the active one first.

## 3. Quick checks before the timed runs (about 5 min)

| Check | Do | Expect |
|---|---|---|
| B1 | Look at service FF10 | FF11, FF12, FF14 (notify only, has CCC) |
| B2 (partial) | With FF12 on, enable FF14; then FF12 off, FF14 on, try FF12 | Second enable fails (nRF shows error 0x80 / 128), first keeps streaming |
| B3 | FF14 on, 60 s | Values start `02 0N`, N 1-9; serial `stream=batched`, `FF14 batches_tx` rising, `notify` grows much slower than ~320/s |
| B7 | Fresh connect, enable FF14 *before* raising MTU (stays 23) | Nothing on FF14, `mtu_skip` rises both hands, `batches_tx` stays 0 |

## 4. Timed A/B/A, one fixed position (controllers face down, still, same spot as the decimation runs)

Before **each** run: reset the ESP32 (in the monitor: Ctrl+T then Ctrl+R), wait until both controllers are connected, wait 30 s, then time 2 minutes. Resetting makes `cum` fresh so the runs are comparable.

| # | Run | Phone |
|---|---|---|
| 0 | No phone | nRF disconnected |
| 1 | A: FF12 | nRF, MTU 517, High, FF12 notify |
| 2 | B: FF14 | nRF, MTU 517, High, FF14 notify |
| 3 | A again: FF12 | as run 1 |

Paste per run: the `LINKLOG_FIELDS` header once, then all `LINKLOG,` lines of the 2 minutes, plus the last `STREAM ...` / `FF14 ...` / `LEFT` / `RIGHT` block of the run. Tell me the position and anything that changed (phone moved, someone walked by).

What would count: RIGHT on FF14 falling toward 12-15 % (the N=2/N=4 level) means notification count is part of RIGHT's loss. LEFT is not expected to change. If run 3 differs a lot from run 1, the session drifted and the comparison is weak.

## 5. Later, same method: coexistence options (LEFT's extra loss)

One option per build, A/B/A on FF12 N=1 at the same position. Full steps: WMR_Bridge `docs/BUILD_WINDOWS.md` "Experiment: Classic-against-BLE controller options".

```powershell
cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
Copy-Item sdkconfig sdkconfig.before-coex-test
idf.py -B build-wmr-bridge menuconfig   # / then BTDM_CTRL_AUTO_LATENCY -> on, S, Q
idf.py -B build-wmr-bridge fullclean
idf.py -B build-wmr-bridge build
Select-String build-wmr-bridge\config\sdkconfig.h -Pattern "AUTO_LATENCY|MODEM_SLEEP "
idf.py -B build-wmr-bridge -p COM5 -b 115200 flash monitor
# restore afterwards:
Copy-Item sdkconfig.before-coex-test sdkconfig -Force; idf.py -B build-wmr-bridge fullclean; idf.py -B build-wmr-bridge build
```

Then the same with modem sleep off (`BTDM_CTRL_MODEM_SLEEP` unset), from the restored config.
