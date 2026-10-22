# Run sheet: LEFT alone and RIGHT alone with the phone streaming

> Archived sheet. It was run on 2026-10-22 (user-reported); see ../README.md for the logs.

Question: in the long baseline of 2026-10-22 (user-reported), RIGHT's loss fell from about 20 % to about 11 % the moment LEFT fell asleep. Does a controller lose less when the other one is not connected (radio contention), or does it lose the same about 19 % on its own (the controller itself)?

Figures to compare with (FF14 only, MTU 517, both controllers connected, `last5s`): LEFT 18 to 19 %, RIGHT 18 to 20 %. A single controller near 10 to 13 % points to contention; staying near 19 % points to the controller.

## 1. Flash firmware that streams (pre-FF13 commit)

`2b17707` is the last commit before any FF13 change (FF14, FF15 echo and discovery are in; E1-E10 and D1-D11 were reported as passed on it). Anya's ESP-IDF PowerShell; append to PATH, never replace it.

```powershell
$h = "2b17707776a96378852e70124ec52b89347cb895"
$clone = "$env:TEMP\WMR_Bridge-2b17707"
if (Test-Path $clone) { Remove-Item $clone -Recurse -Force }
git clone https://github.com/nightcinder/WMR_Bridge $clone
git -C $clone checkout $h
git -C $clone log --oneline -1          # expect 2b17707 Restore FF15 echo test wiring ...

robocopy $clone C:\Users\Nick\btstack-wmr\wmr_bridge /E /XD .git captures __pycache__ .github venv
Copy-Item $clone\integration\main-CMakeLists.txt C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Force
Select-String C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Pattern "wmr_batch|wmr_echo|wmr_discovery"   # must print three lines
Select-String C:\Users\Nick\btstack-wmr\port\esp32\main\CMakeLists.txt -Pattern "wmr_cfg"                            # must print nothing

cd C:\Users\Nick\btstack-wmr\port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge build        # expect "Project build complete"
idf.py -B build-wmr-bridge -p COM5 -b 115200 flash monitor
```

Robocopy exit codes below 8 mean success. It does not delete files, so the newer `wmr_cfg*.c/h` files from the FF13 build stay in the folder; they are harmless because the CMake file above does not list them. The stored controller addresses (NVS) survive a flash, so discovery should not need to run again.

## 1b. Optional: firmware from the FF13 fix (PR #21), only after section 1 works

Branch `claude/project-thread-5z6njd`, head `811155c` when this sheet was written. Same steps, different checkout:

```powershell
$clone = "$env:TEMP\WMR_Bridge-ff13fix"
if (Test-Path $clone) { Remove-Item $clone -Recurse -Force }
git clone https://github.com/nightcinder/WMR_Bridge $clone --branch claude/project-thread-5z6njd
git -C $clone log --oneline -1          # note the hash you got
# then the robocopy / Copy-Item -Force / build / flash block from section 1
```

Select-String for `wmr_cfg` must now print `wmr_cfgmgr.c`.

Check with **no phone connected**: wake one controller, wait for the config read to finish (the console reports FF13 READY / `state=2`), then look at the next `link:` lines.

- Go: `rate_rx` about 170 to 200/s and staying there for at least a minute.
- No-go: `rx` stops growing at a few hundred reports with `rate_rx=0.0/s` (the bug from #17). Paste the first `STREAM` block after it and stop.

This fix is not run on hardware; the single-controller runs below do not depend on it (use section 1 firmware for them).

## 2. Common setup for both runs

- Monitor from `C:\Users\Nick\btstack-wmr\port\esp32` (not from inside a build folder), saving the output:
  ```powershell
  cd C:\Users\Nick\btstack-wmr\port\esp32
  $env:EXAMPLE = "wmr_bridge"
  idf.py -B build-wmr-bridge -p COM5 monitor | Tee-Object -FilePath C:\Users\Nick\btstack-wmr\left-only.txt
  ```
  (use `right-only.txt` for the RIGHT run). Reset the ESP32 first (Ctrl+T then Ctrl+R) so uptime starts at zero.
- Same table position as before, controller face down and still. Do not touch it, the phone or the ESP32 during timing.
- **Only one controller connects.** Wake only the one under test; leave the other asleep and do not press any button on it (taking its battery out also works). Watch the console: the other hand must show `no ACL link`.
- Phone: nRF Connect, connect to `WMR Bridge`, Request MTU **517**, connection priority **High**, then notify on **FF14 only** (leave FF13 and FF15 unsubscribed, as in the baseline).

## 3. Go / no-go before timing

Look at the next `STREAM` block and the `link:` line of the hand under test:

| Check | Go | No-go (stop and paste the block) |
|---|---|---|
| `STREAM` line | `phone=1 sub=1 stream=batched mtu=517` | `sub=0` or `stream=none` or `mtu=23` |
| Hand under test | `rate_rx` about 170/s, `lq` about 225+ | `rate_rx=0.0/s` or `rx` not growing |
| Other hand | `no ACL link`, `rx=0` | connected: put it back to sleep and reset |

If all three pass: wait **30 s**, then time **8 minutes**.

## 4. The runs

| # | Controller connected | Phone |
|---|---|---|
| 1 | LEFT only | FF14 only |
| 2 | RIGHT only | FF14 only |
| 3 (optional) | both | FF14 only (fresh reference at the same position) |

Stop early if `rate_rx` drops to 0 (the controller fell asleep) and say when. Run 1 and 2 can be in either order; do run 3 last.

## 5. Save and paste back

Per run: the saved `left-only.txt` / `right-only.txt` (all `LINKLOG,` lines plus the `LINKLOG_FIELDS` header), and one line saying: which run, the firmware hash from `git log`, when the 30 s settle ended, when you stopped, and anything that changed (phone moved, controller slept, someone walked by).

## 6. How I will read it (fixed before the data)

- Metric: `last5s` mean of the connected hand over the 8 minutes after the settle, split into 2-minute blocks.
- Yardstick: block-to-block spread inside a run, about 1 point on 2026-10-22; and the long-baseline figures above.
- Near 10 to 13 %: contention between the two controllers. Near 18 to 20 %: the controller itself (or the phone link). In between: not decided, repeat once.
- RSSI/lq are reported next to it; they were not correlated with loss in the long baseline.
- One run per hand is one data point. A third run (both connected) at the same position shows whether the session drifted.
