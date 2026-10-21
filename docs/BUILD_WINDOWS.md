# Build and flash (Windows PowerShell)

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
Edit the controller addresses in `src\wmr_config.h` first.

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
