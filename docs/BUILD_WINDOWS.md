# Build and flash (Windows PowerShell)

> **One-command alternative.** `tools/firmware/build.py` performs the setup,
> build and optional flash described below, and packages a browser-installable
> image. See [FLASHING.md](FLASHING.md). The manual steps here are kept for
> troubleshooting and for understanding exactly what the script does.

## Versions actually used

| Component | Version / identifier |
| --- | --- |
| ESP-IDF | v6.1, tag `v6.1`, commit `fff9895c82d744c7237be8847347bdd1b07c6643` (`C:\esp\v6.1\esp-idf`; tracked files unmodified) |
| BTstack | `v1.8.2-404-ge38553977` based on commit `e38553977a25fb0b55b383c72c289be0975f422c` ("Merge branch 'develop'"), plus the one modified file in `integration/`. The firmware was built and tested from a working tree with additional uncommitted local edits that are not part of this repository |
| Toolchain | `xtensa-esp-elf` esp-15.2.0_20251204 (GCC 15.2.0) |
| CMake | 4.0.3 (from `C:\Espressif\tools\cmake`) |
| Python | ESP-IDF venv `C:\Espressif\tools\python\v6.1\venv` (Python 3.11) |
| Target | ESP32 (dual-core, chip revision v1.1 per boot log), 2 MB flash |
| Host OS | Windows 11 Pro 10.0.26200 |

Controller mode is `CONFIG_BTDM_CTRL_MODE_BTDM` (Classic + BLE) from
`port/esp32/sdkconfig.defaults`.

## One-time setup: place wmr_bridge inside BTstack

This repository is **not** a complete project. It builds inside the BTstack
ESP32 port. Use the BTstack commit listed above (newer ones may work but are
untested):

```powershell
git clone https://github.com/bluekitchen/btstack.git
cd btstack
git checkout e38553977a25fb0b55b383c72c289be0975f422c

# put this repository at <btstack>\wmr_bridge (clone it there, or copy it)
git clone <this-repo-url> wmr_bridge

# replace the ESP32 port's main CMakeLists.txt with the one from this repo
Copy-Item wmr_bridge\integration\main-CMakeLists.txt port\esp32\main\CMakeLists.txt
```

`integration\main-CMakeLists.diff` shows exactly what differs from the stock
file: it selects `EXAMPLE=wmr_bridge`, and runs `compile_gatt.py` explicitly
through the ESP-IDF Python (see "GATT compilation" below). No other BTstack file
is modified; the stock `port/esp32/sdkconfig.defaults` is used.

**Re-copy `main-CMakeLists.txt` after pulling the FF14 change:** the file lists the
application's sources, and `wmr_batch.c` was added. Without the new copy the link
fails with undefined `wmr_batch_*` symbols.
No controller addresses need editing: the bridge finds the controllers by name after the
first flash and stores their addresses ([DISCOVERY.md](DISCOVERY.md)). Put them in pairing
mode within the first minute after boot. **Re-copy `main-CMakeLists.txt` after pulling the
discovery change:** it adds `wmr_discovery.c` (undefined `wmr_discovery_*` symbols
otherwise).
The FF13 change adds `wmr_cfg.c` and `wmr_cfgmgr.c` the same way (refresh `integration\main-CMakeLists.txt`
over `port\esp32\main\CMakeLists.txt`; the link fails without them).

**Simplest rule: re-copy `main-CMakeLists.txt` with `-Force` after every pull** (`Copy-Item wmr_bridge\integration\main-CMakeLists.txt port\esp32\main\CMakeLists.txt -Force`). When updating the copy at `wmr_bridge` with `robocopy ... /E`, note that robocopy does not delete files: source files removed or renamed upstream stay in the folder, which is harmless as long as the CMake file does not list them. Robocopy exit codes below 8 are success.

**FF13 config read switch.** `WMR_CFG_FF13_READ` (default 1, `src/wmr_config.h`) makes the bridge read each controller's config block after the descriptor arrives. Set it to 0 (edit the header, or `-DWMR_CFG_FF13_READ=0`) to send no config command; input then streams as it did before FF13 and FF13 state stays 0. Check C7 in [TESTING.md](TESTING.md) is the test for this and has not been run.

The FF15 echo change adds `wmr_echo.c` in the same way (undefined `wmr_echo_*` without a
fresh copy).

## Build

```powershell
# 1. Load the ESP-IDF environment (sets IDF_PATH, PATH, Python venv)
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1

# 2. Select the application and build into a separate directory
cd <btstack>\port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge build
```

`EXAMPLE` is read by `port/esp32/main/CMakeLists.txt`. The value `wmr_bridge`
selects the multi-file application in `<btstack>\wmr_bridge\src`; any other value
builds the stock BTstack example of that name as before. The environment
variable lasts for the PowerShell session: set it again in each new window,
and clear it (`Remove-Item Env:EXAMPLE`) before building something else.

`-B build-wmr-bridge` keeps this build separate from the existing `build`
directory (used for `hid_host_demo`), so neither overwrites the other. Both
share `port/esp32/sdkconfig`; building `wmr_bridge` did not modify it.

Expected result: `Project build complete`, binary about 0x87000 bytes (about 36 % of
the 1.5 MB app partition).

## Flash and monitor

```powershell
idf.py -B build-wmr-bridge -p COM3 flash
idf.py -B build-wmr-bridge -p COM3 monitor     # needs an interactive terminal
```

Use your board's COM port. If flashing says "No serial data received", hold
BOOT while it prints `Connecting....`, check the cable carries data, and make
sure no other program (monitor, Android Studio logcat bridge, terminal) holds
the port. Exit the monitor with Ctrl+].

## GATT compilation

The GATT database is compiled by `tool\compile_gatt.py`. `main/CMakeLists.txt`
runs it **explicitly through the ESP-IDF Python interpreter**. Executing the
script directly on this Windows setup launched Node.js instead of Python, so
do not "simplify" the custom command back to `target_gatt()` or a bare script
path. The generated header is `build-wmr-bridge\esp-idf\main\wmr_bridge.h`.

## Rolling back

Keep a copy of whatever firmware you had before flashing; this repository does
not ship binaries. The stock BTstack examples still build as before by
setting `$env:EXAMPLE` to their name.

## Experiment: Classic-against-BLE controller options (changes the BTstack tree, not this repository)

Two ESP-IDF controller options may matter for the missing-report experiments in
[DECODING.md](DECODING.md). **Neither is documented to reduce loss; either may do nothing.**
`CONFIG_BTDM_CTRL_AUTO_LATENCY` is described by ESP-IDF as "BLE auto latency, used to enhance
classic BT performance while classic BT and BLE are enabled at the same time" and is **off** in
this build; `CONFIG_BTDM_CTRL_MODEM_SLEEP` is **on**. Both live in the generated file
`<btstack>\port\esp32\sdkconfig`, which belongs to your BTstack tree, not to this repository,
and which is **shared** with the other builds in that tree (for example `hid_host_demo`).
(Menu paths below were read from the generated `build-wmr-bridge\config\kconfig_menus.json`;
nothing was flashed or run.)

**Do each variant at the same fixed, stated controller position, in an A/B/A order**: baseline
(A), option changed (B), baseline again (A), using the three-state protocol in DECODING.md for
each. Moving the controllers changes the loss by itself.

```powershell
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1
cd <btstack>\port\esp32
$env:EXAMPLE = "wmr_bridge"

# 0. Keep a copy so you can put it back exactly.
Copy-Item sdkconfig sdkconfig.before-coex-test

# 1. Change ONE option, either in menuconfig ...
idf.py -B build-wmr-bridge menuconfig
#    Component config > Bluetooth > Controller Options > Auto latency          (BTDM_CTRL_AUTO_LATENCY)
#    Component config > Bluetooth > Controller Options > MODEM SLEEP Options >
#                                          Bluetooth modem sleep                (BTDM_CTRL_MODEM_SLEEP)
#    (press / and type the option name to jump to it), save with S, quit with Q ...
#    ... or by editing sdkconfig. The two lines currently read:
#        # CONFIG_BTDM_CTRL_AUTO_LATENCY is not set
#        CONFIG_BTDM_CTRL_MODEM_SLEEP=y
#    Auto latency on:   CONFIG_BTDM_CTRL_AUTO_LATENCY=y
#    Modem sleep off:   # CONFIG_BTDM_CTRL_MODEM_SLEEP is not set

# 2. Clean rebuild (the controller configuration is compiled into the objects):
idf.py -B build-wmr-bridge fullclean      # deletes only the build-wmr-bridge directory
idf.py -B build-wmr-bridge build

# 3. Check that it took effect before flashing:
Select-String build-wmr-bridge\config\sdkconfig.h -Pattern "AUTO_LATENCY|MODEM_SLEEP "
#    expect  #define CONFIG_BTDM_CTRL_AUTO_LATENCY 1   (or no MODEM_SLEEP line when it is off)
```

**Do not put these lines in `sdkconfig.defaults`**: ESP-IDF reads that file only when
`sdkconfig` does not exist, so on an existing tree the change would silently do nothing.

**Putting it back:**

```powershell
Copy-Item sdkconfig.before-coex-test sdkconfig -Force
idf.py -B build-wmr-bridge fullclean
idf.py -B build-wmr-bridge build
Select-String build-wmr-bridge\config\sdkconfig.h -Pattern "AUTO_LATENCY|MODEM_SLEEP "
#    expect  CONFIG_BTDM_CTRL_MODEM_SLEEP 1  and no CONFIG_BTDM_CTRL_AUTO_LATENCY
```

Changing only one option per run keeps the result attributable. Record the option, the
position and the `last5s`/`cum` figures in the experiment table.
