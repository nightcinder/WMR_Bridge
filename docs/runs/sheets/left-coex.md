# Run sheet: LEFT's extra loss, auto latency and modem sleep

> Archived sheet. It was run on 2026-10-22 (user-reported); see ../README.md for the logs.

Question: LEFT loses about 10 points more as soon as a phone streams, whatever the notification count. Does `CONFIG_BTDM_CTRL_AUTO_LATENCY` on, or Bluetooth modem sleep off, change that? Neither option is documented to reduce loss; either may do nothing. Background and menu paths: WMR_Bridge `docs/BUILD_WINDOWS.md`, "Experiment: Classic-against-BLE controller options".

Why this sheet alternates: on 2026-10-22 RIGHT on FF12 read 33.6 % and then 23.9 % in two identical runs with nothing moved (user-reported). A single before/after cannot show a difference smaller than that swing. So every option is run twice, interleaved with the baseline, and the gap between the two baselines is the yardstick.

## 1. Build three images once (Anya, ESP-IDF PowerShell; append to PATH, never replace it)

The image files stay in separate build folders, so changing the option does not mean rebuilding between runs.

```powershell
cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
Copy-Item sdkconfig sdkconfig.before-coex-test      # keep an exact copy

# BASE: current config (auto latency off, modem sleep on)
idf.py -B build-wmr-bridge build
Select-String build-wmr-bridge\config\sdkconfig.h -Pattern "AUTO_LATENCY|MODEM_SLEEP "
#   expect: CONFIG_BTDM_CTRL_MODEM_SLEEP 1 and no AUTO_LATENCY line

# AL: auto latency ON
(Get-Content sdkconfig) -replace '^# CONFIG_BTDM_CTRL_AUTO_LATENCY is not set','CONFIG_BTDM_CTRL_AUTO_LATENCY=y' | Set-Content sdkconfig
idf.py -B build-wmr-al build
Select-String build-wmr-al\config\sdkconfig.h -Pattern "AUTO_LATENCY|MODEM_SLEEP "
#   expect: CONFIG_BTDM_CTRL_AUTO_LATENCY 1 and MODEM_SLEEP 1
Copy-Item sdkconfig.before-coex-test sdkconfig -Force

# MS: modem sleep OFF (auto latency back off)
(Get-Content sdkconfig) -replace '^CONFIG_BTDM_CTRL_MODEM_SLEEP=y','# CONFIG_BTDM_CTRL_MODEM_SLEEP is not set' | Set-Content sdkconfig
idf.py -B build-wmr-ms build
Select-String build-wmr-ms\config\sdkconfig.h -Pattern "AUTO_LATENCY|MODEM_SLEEP "
#   expect: no MODEM_SLEEP line and no AUTO_LATENCY line
Copy-Item sdkconfig.before-coex-test sdkconfig -Force
```

If a `Select-String` result is not what is expected, stop and paste it; do not flash. (If an option has dependent settings that menuconfig would also change, editing the file this way can miss them; `idf.py -B <dir> menuconfig` is the documented route.)

## 2. Flashing an image without rebuilding

`idf.py flash` may notice that `sdkconfig` changed since that folder was built and rebuild with the restored config, which would silently flash the wrong image. Flash the finished files directly instead (I have not run this; it is the standard esptool route, check that `flash_args` exists in the folder):

```powershell
cd C:\Users\Nick\btstack-wmr\port\esp32\build-wmr-al        # or build-wmr-bridge / build-wmr-ms
python -m esptool --chip esp32 -p COM5 -b 115200 write_flash "@flash_args"
```

After each flash, confirm the image from the boot log or the `sdkconfig.h` check above, and say which image each run used.

## 3. Runs (same fixed position as before, nothing moved at all, nRF Connect at MTU 517, High priority, FF14)

Same method as the FF14 runs: reset the ESP32 before each run, wait until both controllers are connected, wait 30 s, then time 2 minutes. Use the numbers below as labels, so the paste maps to this table.

| Run | Image | Purpose |
|---|---|---|
| 1 | BASE | baseline |
| 2 | AL | auto latency on |
| 3 | BASE | baseline again |
| 4 | AL | auto latency on, second time |
| 5 | MS | modem sleep off |
| 6 | BASE | baseline |
| 7 | MS | modem sleep off, second time |
| 8 | BASE | baseline |

Stop after run 4 if AL clearly does nothing for LEFT (say so and I will judge from the figures). Each run takes about 4 minutes with the flash.

Paste per run: the `LINKLOG,` lines for the 2 minutes (plus one `LINKLOG_FIELDS` header), labelled `run N`.

## 4. How I will read it (fixed before the data)

- Metric: LEFT `last5s` mean over 35 to 101 s of each run (RIGHT as well, for side effects).
- Yardstick: the gap between the baselines (runs 1, 3, 6, 8). An option only counts as an effect if both of its runs are outside the range the baselines cover, in the same direction.
- RSSI/lq for LEFT is recorded too: it moved about 4 dB between runs on 2026-10-22, which may belong to the drift.
- Anything smaller than the baseline spread is "no effect shown", not "no effect".

## 5. Put it back

```powershell
cd C:\Users\Nick\btstack-wmr\port\esp32
Copy-Item sdkconfig.before-coex-test sdkconfig -Force
Select-String sdkconfig -Pattern "AUTO_LATENCY|MODEM_SLEEP"      # expect: not set, =y
idf.py -B build-wmr-bridge build
cd build-wmr-bridge
python -m esptool --chip esp32 -p COM5 -b 115200 write_flash "@flash_args"
```

`sdkconfig` is shared with other builds in that tree (for example `hid_host_demo`), which is why it is restored.
