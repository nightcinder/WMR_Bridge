# Run sheet: one hand per bridge (`WMR_CFG_BRIDGE_HANDS`, `WMR_CFG_BLE_NAME`)

> **Not run. No hardware result is claimed by this sheet or the implementing PR.** It records
> how to build and what to look for when Nick runs it. Issue: ONY-20 (M6.1). The advertised
> name is visible to clients, so `WMR_CFG_BLE_NAME` needs Nick's explicit yes before merge.

Purpose: two stock bridges can be flashed from **one commit** as a LEFT-only bridge and a
RIGHT-only bridge. Each bridge then ignores, refuses and does not store the other hand, so
the two controllers no longer race to the same bridge. The default build (`0`) is unchanged.

## 1. The two flags

| Flag | Values | Default | Effect |
|---|---|---|---|
| `WMR_CFG_BRIDGE_HANDS` | `0`, `1`, `2` | `0` | `0` = both hands (current behaviour). `1` = LEFT only. `2` = RIGHT only. The excluded hand is ignored by discovery, refused by the outgoing connect, declined on an incoming connection (even from an address stored by an older both-hands build) and not written to NVS. |
| `WMR_CFG_BLE_NAME` | string literal, 1–22 characters | `"WMR Bridge"` | Advertised complete local name. The GATT database, layout, device-name value and handles do not change. |

The single-hand names keep the `WMR Bridge` prefix so prefix-matching clients still find
them. `src/wmr_config.h` defines `WMR_CFG_BLE_NAME_BOTH` / `_LEFT` / `_RIGHT` as quoted
tokens because ESP-IDF splits `EXTRA_CFLAGS` on every space; use the token in that route,
not a quoted string with a space.

## 2. Build the three images from one commit

This is the same `EXTRA_CFLAGS` route as `contention-2.md` §2. No source or CMake change is
needed per image. Use a fresh build folder for each image so a cached define cannot hide.

```powershell
# Load ESP-IDF. This profile appends the tool paths; never replace $env:Path with a new value.
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1

cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"

# Refresh the copied tree from the implementation commit (replace <sha>).
$h = "<implementation sha>"
$clone = "$env:TEMP\WMR_Bridge-two-bridge-$h"
if (Test-Path $clone) { Remove-Item $clone -Recurse -Force }
git clone https://github.com/nightcinder/WMR_Bridge $clone
git -C $clone checkout $h
git -C $clone log --oneline -1
robocopy $clone C:\Users\Nick\btstack-wmr\wmr_bridge /E /XD .git captures __pycache__ .github venv
Copy-Item $clone\integration\main-CMakeLists.txt C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Force
Select-String C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Pattern "wmr_controller|wmr_linkdiag"   # must print lines

# REF: both hands, default name. Fresh folder; no EXTRA_CFLAGS.
if (Test-Path build-wmr-hands-ref) { Remove-Item build-wmr-hands-ref -Recurse -Force }
Remove-Item Env:EXTRA_CFLAGS -ErrorAction SilentlyContinue
idf.py -B build-wmr-hands-ref build
Select-String build-wmr-hands-ref\compile_commands.json -Pattern "WMR_CFG_BRIDGE_HANDS|WMR_CFG_BLE_NAME"   # expect no match

# L: LEFT only, "WMR Bridge L".
if (Test-Path build-wmr-hands-left) { Remove-Item build-wmr-hands-left -Recurse -Force }
$env:EXTRA_CFLAGS = "-DWMR_CFG_BRIDGE_HANDS=1 -DWMR_CFG_BLE_NAME=WMR_CFG_BLE_NAME_LEFT"
idf.py -B build-wmr-hands-left build
Select-String build-wmr-hands-left\compile_commands.json -Pattern "WMR_CFG_BRIDGE_HANDS=1|WMR_CFG_BLE_NAME=WMR_CFG_BLE_NAME_LEFT"

# R: RIGHT only, "WMR Bridge R".
if (Test-Path build-wmr-hands-right) { Remove-Item build-wmr-hands-right -Recurse -Force }
$env:EXTRA_CFLAGS = "-DWMR_CFG_BRIDGE_HANDS=2 -DWMR_CFG_BLE_NAME=WMR_CFG_BLE_NAME_RIGHT"
idf.py -B build-wmr-hands-right build
Select-String build-wmr-hands-right\compile_commands.json -Pattern "WMR_CFG_BRIDGE_HANDS=2|WMR_CFG_BLE_NAME=WMR_CFG_BLE_NAME_RIGHT"

Remove-Item Env:EXTRA_CFLAGS
```

If the `compile_commands.json` check does not show the expected define, stop; that folder is
not the image. `EXTRA_CFLAGS` is read at configure time, hence the fresh folder.

Flash a finished folder without rebuilding (`contention-2.md` §2, `left-coex.md` §2):

```powershell
cd C:\Users\Nick\btstack-wmr\port\esp32\build-wmr-hands-left
Test-Path flash_args                      # must be True
python -m esptool --chip esp32 -p COM5 -b 115200 write_flash "@flash_args"
```

## 3. What to check on the monitor (not yet observed)

After each flash, note the boot banner and the exact folder image. These are expectations to
check, not results:

| Image | Expected boot / runtime lines |
|---|---|
| REF | `BLE advertising: WMR Bridge (...)`, `STREAM ... bridge_hands=0` |
| L | `BLE advertising: WMR Bridge L (...)`, `STREAM ... bridge_hands=1`; a RIGHT controller must log `Incoming RIGHT is excluded by this build (WMR_CFG_BRIDGE_HANDS=1); declining` and never appear as a stored RIGHT address |
| R | `BLE advertising: WMR Bridge R (...)`, `STREAM ... bridge_hands=2`; a LEFT controller must be declined the same way |

Discovery on an L image looks only for LEFT; it does not keep retrying because RIGHT has no
address. `l` / `r` for the excluded hand are refused by `connect_hand`.

## 4. Open question for Nick (approval gate)

`WMR_CFG_BLE_NAME` changes the name clients see: `WMR Bridge L` / `WMR Bridge R` in the L/R
images, `WMR Bridge` unchanged in REF. The implementing PR must not be merged until Nick
says yes to that advertised name. The GATT layout and handles are unchanged.
