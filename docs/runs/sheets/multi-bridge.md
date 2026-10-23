# Run sheet: multi-bridge — one ESP32 per controller (issue #18)

> **Proposed sheet, not yet run.** Nothing here is a hardware result. Figures quoted from
> earlier runs are user-reported; the pass criteria are fixed before any data. This sheet
> tests the topology, not a firmware change: two bridges, one hand each, against one bridge
> carrying both hands.

Question: does one ESP32 per controller remove the extra loss of the two-controller setup,
and does RIGHT's own ~10.8 % follow the controller, the position or the board?

The sheet is written against the flag names fixed for ONY-20 / ONY-22:
`WMR_CFG_BRIDGE_HANDS` = 0 (both), 1 (LEFT only), 2 (RIGHT only), and
`WMR_CFG_BLE_NAME` for the advertised names `WMR Bridge`, `WMR Bridge L` and
`WMR Bridge R`. The implementation is WMR_Bridge PR #33 (ONY-20), and the app is
wmr-companion PR #34 (ONY-18). Build all three firmware images from the same commit that
contains PR #33. Do not run the session until both PRs are merged, or until you have the
firmware from #33 and the APK built from #34; the sheet depends on both.

## 0. What the existing runs say (user-reported)

All figures are FF14, MTU 517, phone streaming, `last5s_pct` after a 30 s settle. Sources:
`docs/runs/README.md`, `docs/runs/sheets/single-controller.md`, WMR_Bridge issue #18.

| Setup | LEFT | RIGHT |
|---|---|---|
| Both controllers, no phone | 9–13 % | 9–13 % |
| LEFT alone, phone streaming | ~0.5 % | — |
| RIGHT alone, phone streaming | — | ~10.8 % |
| Both connected, same build and position | ~19.6 % | ~19.1 % |
| Both connected, long baseline | 18–19 % | 18–20 % |

- LEFT is almost clean on its own and loses about 19 % when RIGHT is also connected.
  RIGHT has about 10.8 % of its own and gains about 8 points when LEFT is connected.
- So two things are being tested at once:
  1. Does splitting the hands onto two ESP32s bring LEFT back towards its alone level and
     remove the extra ~8 points on RIGHT?
  2. Does RIGHT's own ~10.8 % move with the board (S1), the marked position (S2), or stay
     with the controller?
- Multi-bridge cannot be “zero loss” even if it works: RIGHT's own loss is the second
  question, not something the topology removes by itself.
- The absolute level is never compared across sessions. The A/B/A/B order below is what
  makes a comparison possible against the 15–26 % between-boot spread seen earlier.

## 1. Pairing the second board

Two classic ESP32 boards are enough. Label them now; the labels are used for the whole
session.

| Label | What it runs | Notes |
|---|---|---|
| **Board A** | REF image during every A run; L image during every B run; R image after the S1 swap | The L/REF board. This is the board that is reflashed most often. |
| **Board B** | R image during every B run; L image after the S1 swap; **powered off during every A run** | The second board. This is the board added for this test. |

Use two USB ports and two monitors, e.g. `COM5` for board A and `COM6` for board B. If only
one port is available, flash one board at a time, but the runs need both logs at once, so a
second port or a hub is required before timing.

### 1.1 Clear the old state, one board at a time

Both boards may still hold addresses and link keys from earlier both-hands builds, and the
controllers may be bonded to either board. Do this with the other board **powered off**, so a
controller cannot reconnect to the wrong host while you work.

On the board in front of you (repeat for A, then B):

1. Open the monitor and let it boot.
2. Press `L` and `R` to disconnect any controller that is still connected.
3. Press `k` (LEFT) and `K` (RIGHT) to delete the ESP32's stored link keys. A hand with no
   address prints `has no address yet; nothing to delete`; that is fine. `k`/`K` are also
   refused while that hand is connected; if it reconnects between steps, press `L`/`R`
   again before the bond reset.
4. Put **only the controller this board should serve** into pairing mode (hold the Windows
   button until the LEDs flash). Do not put the other controller in pairing mode.
5. Press `F` to forget both stored addresses and start discovery. `F` is refused while a
   controller is connected or connecting, so do step 2 first. If the controller's pairing
   mode times out before discovery reaches it, put it in pairing mode again and press `d`.
6. Wait for the discovery and pairing lines, then for a live stream:
   - `Discovery: LEFT is ...` (or `RIGHT`), `Addresses saved ...`
   - `Connecting LEFT` / `Connecting RIGHT`, the SSP accept, `descriptor status=0x00`
   - `STREAM ... bridge_hands=1` on board A, `bridge_hands=2` on board B
   - the served hand has `rate_rx` about 150–200/s; the excluded hand does not connect.
7. If the wrong controller was put in pairing mode, press `F` again after disconnecting it
   and repeat with only the intended controller.

For board A use the **L image** and the **LEFT** controller; for board B use the **R image**
and the **RIGHT** controller. Board A's NVS will later also receive RIGHT's address for the
REF runs; that is expected and is how the same board can host both hands in A runs.

### 1.2 Make board A hold both hands (the REF state)

Do this once, before the first A run:

1. Power board B off.
2. Flash board A with the **REF image** (§2). NVS survives this flash; do not erase it.
3. On board A: `L` and `R` to disconnect, then `k` and `K` to delete stale link keys.
4. Put **both** controllers in pairing mode. Then press `F` to forget both addresses and
   start discovery. This also forgets LEFT's address; both controllers pair again, which is
   why this is done once and not at every switch. If a pairing mode times out before
   discovery reaches it, put that controller back in pairing mode and press `d`.
5. Discovery may find them in one or two rounds and connects them one after the other (the
   same procedure as check D4 in `docs/TESTING.md`). Wait for both `descriptor status=0x00`
   lines.
6. Confirm `STREAM ... bridge_hands=0` and both hands streaming. This is the REF state.

From here on LEFT stays paired to board A through every REF↔L reflash. Only RIGHT is
re-paired when it moves between board A (REF runs) and board B (VAR runs).

### 1.3 Re-pairing a controller that was bonded to the other host

This is the step most likely to go wrong. A controller's bond has two halves: the link key
stored on the ESP32 (`k`/`K` deletes it) and the bond stored on the controller itself
(pairing mode clears it). Both halves must be cleared; doing only one leaves a link that
fails authentication or never reconnects.

Use this recipe when a controller was last paired to the *other* board and will not connect
to the intended one. Keep the other board powered off throughout.

**Case A — the intended board already has the controller's address** (the usual REF↔VAR
switch for RIGHT):

1. Power off the board the controller is currently bonded to.
2. On the intended board, disconnect that hand (`L` or `R`), then press `k` or `K` for that
   hand. This deletes the stale link key but keeps the stored address; the other hand's
   address is untouched.
3. Put the controller in pairing mode.
4. Press `l` (LEFT) or `r` (RIGHT) on the intended board. Because the address is stored,
   no discovery is needed; the bridge connects outwards and pairs again.
5. Wait for `descriptor status=0x00` and a live `rate_rx`. If it fails once, repeat from
   step 2.

**Case B — the intended board does not have the controller's address** (a fresh board, or
the S1 swap for LEFT):

1. Keep the other board powered off.
2. Put the missing controller in pairing mode.
3. On the intended board, disconnect both hands (`L`, `R`), then press `F`. `F` clears both
   stored addresses and starts discovery, so the *other* hand must be paired again too. If
   the pairing mode times out, put the controller back in pairing mode and press `d`.
4. Wait for discovery to find the controller, then pair the other hand again if it is needed
   on this board; with a single-hand image the excluded hand is ignored anyway.

Rules that keep this from going wrong:

- Never run `F` or discovery on both boards at the same time, and never have both boards
  powered on while a controller is in pairing mode unless both boards are single-hand images
  for different hands. Discovery uses the first device found with a hand's name for that
  hand and ignores any other device with the same name in the same discovery, so keep only
  one controller of each hand in pairing mode.
- Do not press `F` while any controller is connected; use `L`/`R` first.
- A controller that was bonded to the other host may keep trying the old host. Power the old
  host off before pairing the new one, and only power it back on after the new link is
  streaming.
- If a controller connects to the wrong bridge, that bridge logs
  `Incoming <hand> is excluded by this build (WMR_CFG_BRIDGE_HANDS=...); declining`. That is
  expected and is not a pairing failure; wake the controller again, or use the recipe above.
- Record every re-pair, including which board and which case (A or B) was used.

### 1.4 Go / no-go before timing

| Check | Go | No-go (stop and fix it) |
|---|---|---|
| Board A, B run | `BLE advertising: WMR Bridge L`, `STREAM ... bridge_hands=1` | other name or `bridge_hands` value |
| Board B, B run | `BLE advertising: WMR Bridge R`, `STREAM ... bridge_hands=2` | other name or `bridge_hands` value |
| Board A, A run | `BLE advertising: WMR Bridge`, `STREAM ... bridge_hands=0`; both hands | other name, or one hand missing |
| Excluded hand | `n/a (no ACL link)`; any attempt logs `... excluded ... declining` | the excluded hand is connected or streaming |
| Phone scan | exactly the expected name(s): `WMR Bridge` in A runs; `WMR Bridge L` and `WMR Bridge R` in B runs | a stale `WMR Bridge` while board B is on; a missing bridge |
| App connect screen | one row in A runs, two rows in B runs; each `mtu=517` and `batched` | one row in a B run, `mtu` below 517, or a stream mode other than FF14 |
| `STREAM` on each active bridge | `phone=1 sub=1 stream=batched mtu=517` | `sub=0`, `stream=none`, `mtu=23` |

Do not start timing until every row passes. If the app does not show two bridge rows in a B
run, it is not the ONY-18 build; stop.

## 2. Build the three images (one command per image)

All three images come from **one commit**. Use `tools/firmware/build.py --variant`; it sets
the compile-time flags through `EXTRA_CFLAGS` and never edits `src/wmr_config.h`. The same
framework is described in [docs/FLASHING.md](../../FLASHING.md).

`--variant both` keeps today's build and names unchanged. `--variant left` /
`--variant right` set `WMR_CFG_BRIDGE_HANDS` and `WMR_CFG_BLE_NAME` and use their own build
directory, so a cached define from another variant cannot hide. The three advertised names
are fixed: `WMR Bridge`, `WMR Bridge L`, `WMR Bridge R`. ESP-IDF splits `EXTRA_CFLAGS` on
every space, so the script passes the token `WMR_CFG_BLE_NAME_LEFT` / `_RIGHT`, which
`src/wmr_config.h` expands to the quoted name; do not pass a quoted string with a space.

```powershell
# Load ESP-IDF. This profile appends the tool paths; never replace $env:Path with a new value.
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1

# One commit for all three images; use the merged commit that contains PR #33 (ONY-20).
cd <repo>
$h = "<implementation sha>"
git checkout $h
git log --oneline -1

$dist = "dist-multi"     # one folder holds all three variants
$portA = "COM5"          # board A, the L/REF board
$portB = "COM6"          # board B, the R board

# REF: both hands, name unchanged. No EXTRA_CFLAGS; today's names.
python tools\firmware\build.py --variant both  --out $dist
# L: LEFT only, advertised "WMR Bridge L".
python tools\firmware\build.py --variant left  --out $dist
# R: RIGHT only, advertised "WMR Bridge R".
python tools\firmware\build.py --variant right --out $dist
```

Each command prints the variant flags and writes its own artifacts:
`wmr-bridge-factory.bin` / `manifest.json` / `build-info.json` for REF, and
`wmr-bridge-left-factory.bin` / `manifest-left.json` / `build-info-left.json` (then `-right`)
for the single-hand images. Check `build-info*.json` instead of a hand-typed folder:

```powershell
foreach ($v in @("", "-left", "-right")) {
  Get-Content "$dist\build-info$v.json" | Select-String '"variant"|WMR_CFG_BRIDGE_HANDS|WMR_CFG_BLE_NAME'
}
# REF: "variant": "both",  hands 0, WMR_CFG_BLE_NAME_BOTH
# L:   "variant": "left",  hands 1, WMR_CFG_BLE_NAME_LEFT
# R:   "variant": "right", hands 2, WMR_CFG_BLE_NAME_RIGHT
```

If a `build-info*.json` does not show the expected variant and hand, stop; that image is not
the variant. The flags are recorded by the build, not typed from memory.

Flash a finished image with `--flash`; the script reuses that variant's build directory and
does not erase NVS, so the stored addresses and link keys survive a reflash:

```powershell
# $dist, $portA and $portB are the variables from the block above.
python tools\firmware\build.py --variant both  --out $dist --flash $portA   # REF -> board A
python tools\firmware\build.py --variant left  --out $dist --flash $portA   # L -> board A
python tools\firmware\build.py --variant right --out $dist --flash $portB   # R -> board B
# After S1, swap: --variant left --flash $portB, --variant right --flash $portA.
```

To flash again later without rebuilding, write the merged image at offset 0 (the browser
flasher does the same):

```powershell
python -m esptool --chip esp32 -p $portA -b 115200 write_flash 0x0 "$dist\wmr-bridge-left-factory.bin"
```

**Do not run `idf.py erase-flash` between runs**; that erases the pairing state and §1 must be
repeated.

### 2.1 One board instead of two (plan M12.3')

The owner currently has no second Classic ESP32, so the two-bridge sitting is parked. The
single-hand build is still useful on its own: flash one board with `--variant left` and use
only the LEFT rows for a one-board validation of single-hand mode. This is the cheap route
for plan M12.3' — one command, no hand-edited rebuild. It is a smoke/validation sitting, not
a substitute for the two-bridge A/B comparison or its pass criteria below; use
`--variant right` only if the RIGHT path is what is being checked.

After each flash, open the monitor and check the boot banner and the one-line marker:

| Image | Expected |
|---|---|
| REF | `BLE advertising: WMR Bridge (...)`; `STREAM ... bridge_hands=0` |
| L | `BLE advertising: WMR Bridge L (...)`; `STREAM ... bridge_hands=1` |
| R | `BLE advertising: WMR Bridge R (...)`; `STREAM ... bridge_hands=2` |

If the banner or `bridge_hands` is wrong, the wrong image was flashed; stop and reflash.

Build record (fill this in and paste it back):

| Tag | Image | Commit | Variant / flags (from `build-info*.json`) | Artifacts | Flashed to | Port |
|---|---|---|---|---|---|---|
| REF | both hands | | `both`; hands 0, name `WMR_CFG_BLE_NAME_BOTH` | `wmr-bridge-factory.bin`, `manifest.json` | board A | |
| L | LEFT only | | `left`; hands 1, name `WMR_CFG_BLE_NAME_LEFT` | `wmr-bridge-left-factory.bin`, `manifest-left.json` | board A (board B after S1) | |
| R | RIGHT only | | `right`; hands 2, name `WMR_CFG_BLE_NAME_RIGHT` | `wmr-bridge-right-factory.bin`, `manifest-right.json` | board B (board A after S1) | |

## 3. Run protocol

### 3.1 Common setup for every run

- One fixed marked position for the whole session: both controllers face down, still, on
  their marks. Note the distance to each bridge, the orientation and the phone position. A
  photo is the record; the position is not instrumented.
- In B runs the two bridges are about **1 m apart**, both within reach of their own
  controller. Record the actual spacing and orientation; do not move them between B runs
  except for S1/S2.
- Phone: the ONY-18 app build, FF14 stream (batched), MTU 517, high connection priority.
  Record the app build/commit. In B runs the app connects to **both** bridges; in A runs to
  the single `WMR Bridge`.
- Confirm on every active bridge: `STREAM` shows `phone=1 sub=1 stream=batched mtu=517`, and
  the served hand has a live `rate_rx` (about 150–200/s). Do not start timing while
  `rate_rx` is 0 or `sub=0`.
- **30 s settle** after the last connection (the second hand in A runs; both bridges' phone
  links in B runs), then a **2-minute window**.
- Metric: the `LINKLOG,` rows only. Per hand, `last5s_pct` is the mean over the 24 rows of
  the window, with min–max. `cum_pct` is recorded for context but is not compared across
  boots. `rate_rx`, `dev_missing_delta`, `rssi`, `lq` and `fcc` come from the same rows.
  On a single-hand board, ignore the excluded hand's rows; only the served hand's rows are
  part of the run.
- Reset the active board(s) before every run so `t_ms` starts at zero and the counters are
  fresh. Resetting does not erase NVS; the controllers reconnect on their own. Wait for the
  `Incoming`/`descriptor` lines, then the 30 s settle.
- Discard and repeat a run if a controller sleeps or disconnects, the phone disconnects, a
  `LINKLOG` row is missing, or a `last5s_pct` field is empty (`,,`) inside the window. Say
  that it was discarded; do not silently drop it.
- Do not touch the controllers, the bridges or the phone during a window. Do not run
  discovery during a window; inquiry shares the Classic radio.

### 3.2 The A/B/A/B block

One block is A/B/A/B; two blocks per session make four pairs (A1–B4 below). A is the REF
image (one bridge, both hands), B is the VAR setup (bridge L + bridge R, one hand each).
Interleaving is what makes a paired comparison possible; the first REF is the session
reference and the last REF is the drift check.

| Order | Setup | Board A | Board B | Client | Re-pair before the run |
|---|---|---|---|---|---|
| A1 | REF: one bridge, both hands | REF image, on | **off** | app → `WMR Bridge`, FF14 | RIGHT → board A (Case A) if it does not reconnect on its own; LEFT stays |
| B1 | VAR: bridge L + bridge R, one hand each, ~1 m apart | L image, on | R image, on | app (ONY-18) → both, FF14 on both | RIGHT → board B (Case A); LEFT stays |
| A2 | as A1 | REF image, on | **off** | as A1 | RIGHT → board A (Case A); LEFT stays |
| B2 | as B1 | L image, on | R image, on | as B1 | RIGHT → board B (Case A); LEFT stays |
| A3 | as A1 | REF image, on | **off** | as A1 | RIGHT → board A (Case A); LEFT stays |
| B3 | as B1 | L image, on | R image, on | as B1 | RIGHT → board B (Case A); LEFT stays |
| A4 | as A1 | REF image, on | **off** | as A1 | RIGHT → board A (Case A); LEFT stays |
| B4 | as B1 | L image, on | R image, on | as B1 | RIGHT → board B (Case A); LEFT stays |
| S1 | Swap: the L board flashed as R and the other way round, VAR again | R image, on | L image, on | app → both, FF14 on both | **both controllers**: RIGHT → A (Case A), LEFT → B (Case B) |
| S2 | VAR with the two controllers' marked spots swapped | R image, on | L image, on | as S1 | none; move the controllers and settle 30 s |
| X | VAR, then switch off the L bridge: RIGHT's `last5s` before and after | R image, on | L image, on, then **off** | as S1, then the R bridge only | none |

Board B is powered off in every A run. If it is on, RIGHT may connect to it instead of board
A and the A run is not REF. During A1–B4, board A is reflashed REF↔L between the A and B
runs; this is deliberate, because it keeps LEFT paired to board A for the whole session.
Only RIGHT is re-paired at each switch.

At each A↔B switch, try the power-up first: wake the controllers and give them 30 s. If
RIGHT connects to the intended board by itself, do not re-pair; record that. If it does not,
or if the wrong bridge logs `... excluded ... declining`, use §1.3 Case A. Do not press `F`
for a Case A re-pair; that would also forget LEFT.

### 3.3 Per-run order

For every row in the table:

1. Power/reset the board(s) for the run. For A runs, board B is off.
2. Re-pair only if step 1 did not put each hand on its intended bridge (see §1.3).
3. Open a monitor on each active port and save it, e.g. from
   `<repo>\.fw-build\btstack\port\esp32`:
   `idf.py -B <repo>\.fw-build\build-wmr-bridge-left -p $portA monitor | Tee-Object -FilePath multi-B1-A.txt`.
   Use the build directory that matches the variant on that board
   (`build-wmr-bridge` for REF, `build-wmr-bridge-left` for L, `build-wmr-bridge-right`
   for R).
4. Phone: connect to the bridge(s) in the table; select FF14; confirm the `STREAM` lines.
5. Wait 30 s after the last connection, then start the 2-minute window. Note the wall-clock
   start and the `t_ms` of the first and last `LINKLOG` row.
6. Stop the window after 2 minutes. In B runs, then do the latency export and the reconnect
   checks below.
7. Save the logs and record the run before starting the next one.

For A runs, the window and the `LINKLOG` rows are for both hands on board A. For B runs, take
the LEFT rows from board A and the RIGHT rows from board B; the two boards share only the
phone and the room, not a radio.

### 3.4 Per-VAR extras: latency export and reconnect check

**Latency export.** The ONY-18 build merges the FF14 stream from both bridges, but the
latency screen's FF15 echo is measured on **one** bridge: the first one connected
(`ControllerService.deviceId` is the first device). It does not merge RTT across bridges.
So:

- Connect the bridge you want to measure **first**, then the other one. Record which was
  first from the Connect screen.
- After the 2-minute loss window, open the Latency test screen, select FF14, choose a
  **120 s** run, leave “Measure round trip” on, and start. The export is saved as
  `latency-YYYYMMDD-HHMMSS.csv`, `.json` and `-echo.csv`.
- Alternate the first-connected bridge across the four B runs: B1 and B3 connect L first,
  B2 and B4 connect R first. That gives two exports per bridge over the session.
- Do not present one export as both bridges' RTT. The JSON's `link` and `round_trip` belong
  to the bridge you connected first; the run record must name it.
- The latency export is run **after** the loss window, not inside it, so the extra FF15 ping
  traffic does not change the A/B comparison.

**Reconnect check.** On each bridge in every B run (and on both hands of the REF board at A1
and A4):

1. Put that bridge's controller to sleep (press its Windows/PS button; note the method).
2. Confirm the bridge shows `rate_rx=0` and the phone stream stops for that hand. The
   phone's BLE link to the bridge should stay up; only the Classic link is lost.
3. Wake the controller.
4. Confirm the incoming path accepts it and the stream resumes:
   `Incoming HID ...`, `accepting incoming HID connection`, `descriptor status=0x00`, and
   `rate_rx` back near its previous level for at least 10 s.
5. If the controller connects to the wrong bridge, it logs the `excluded ... declining`
   line; wake it again or use §1.3. A check that needs a re-pair, or more than one wake, is
   recorded as failed, not silently retried.

The reconnect check changes the link state. Reset before the next run.

### 3.5 S1, S2 and X

**S1 — swap the boards.** After B4, flash board A with the **R image** and board B with the
**L image**. Both boards now run the opposite hand from the one they ran in B4; the
controllers have not moved, but their hosts have.

- With board B alone (L image) and board A off: put LEFT in pairing mode, then press `F` on
  B (it has only RIGHT's address, so this clears it) and let discovery find LEFT. This is
  §1.3 Case B.
- With board A alone (R image) and board B off: `K` on A, put RIGHT in pairing mode, press
  `r`. This is §1.3 Case A; board A already has RIGHT's address from the REF runs.
- Power both on, confirm `bridge_hands=1`/`2`, and run a normal VAR window. No change to the
  marked spots.

**S2 — swap the marked spots.** Keep the S1 boards and images. Move the LEFT controller to
RIGHT's mark and the RIGHT controller to LEFT's mark; the marks themselves stay where they
are. The links can stay up; if a controller drops while moving, wake it and wait for the
stream, then restart the 30 s settle. Run one VAR window. No re-pair is expected; if a
controller lands on the wrong bridge, record it and repeat the window after §1.3.

**X — switch off the L bridge.** With the S1 boards, run one normal VAR window and note
RIGHT's `last5s_pct` mean. Then switch **board B** (the L image after S1) off, wait 30 s, and
run a 2-minute RIGHT-only window on board A. The phone loses its link to L; that is
expected. Compare RIGHT's mean before and after. This is a diagnostic, not part of the pass
criteria: a fall of 3 points or more when L is off means part of RIGHT's remaining loss is
shared air or phone work; no change means it is local to RIGHT's bridge or controller.

### 3.6 How many repeats, and what counts

- The table is four pairs (A1–B4). Run them in order. The four REF means (A1–A4) must span
  **3 points or less** for the session to count; if they span more, the session drifted.
  Stop, do not average, and repeat another day.
- The block-to-block spread inside one continuous run was about 1 point in the earlier
  reports; the between-boot spread was about 11 points (15–26 %). That is why the REF runs
  bracket the VAR runs and why the absolute level is never compared across sessions.
- **Do not chase effects under 3 points.** Call them “not shown”. A 5-point effect is the
  smallest this design is meant to detect. A result between 3 and 5 points is “possible, not
  shown” and needs the second session before anyone acts on it.
- A pass needs a **second session** on another power-up (ideally another day) with the same
  direction. Repeat at least the A/B/A/B block; repeat S1/S2 if the first session left the
  RIGHT-loss answer ambiguous. X is optional in the second session.
- If a REF run lands outside the other three by more than 3 points, stop and check the
  marks, the phone and the bridge spacing before continuing; do not silently drop it.
- One sitting is about 2 h: building and pairing 30–40 min, eight A/B windows about 30 min,
  four latency exports and eight reconnect checks about 20 min, S1/S2/X about 20 min.

## 4. Exit criteria (verbatim, fixed before any data)

> - Pass: in every VAR window LEFT ≤ 3 % and ≥ 10 points below the lowest REF LEFT;
>   RIGHT ≤ 13 % and ≥ 5 points below the lowest REF RIGHT; the four REF means span ≤ 3
>   points; the same direction holds in a second session on another power-up; both
>   reconnect checks pass; phone sequence gaps = 0 on both bridges.
> - Refute: LEFT on its own bridge stays ≥ 10 % while the other bridge runs — then the
>   loss is in the air or on the phone side, not one chip's scheduler.
> - RIGHT's own loss: if S1 moves it with the board it is the board/antenna; if S2 moves it
>   with the spot it is the position; if it stays with the controller it is the controller.

How the sheet reads them, without changing them:

- “Every VAR window” means B1–B4 and S1/S2 (and the before part of X). Each window is read
  per hand from that hand's bridge.
- “The lowest REF LEFT/RIGHT” is the lowest of the four A1–A4 means for that hand, not the
  lowest single row.
- “Both reconnect checks pass” means the two bridge checks in the VAR runs: each bridge's
  own controller was slept, woken, and streamed again. A failed check invalidates that run.
- “Phone sequence gaps = 0 on both bridges” is read from the app's latency export per hand:
  LEFT's gaps come from the L bridge, RIGHT's from the R bridge. Any gap is a fail for that
  run, even if `last5s` is good. The export is the one run immediately after that VAR window,
  with FF14 still active; it is not the same 120 s as the loss window, so record both.
- A **second session** is mandatory for a pass; one session alone can only refute or leave
  the result open. Record both sessions separately; do not pool them.
- If the REF means drift by more than 3 points, the session is invalid, not a refutation.

RIGHT-loss decision table (fill from S1 and S2):

| Comparison | What moved | RIGHT mean before | RIGHT mean after | Difference | Reading (fixed before data) |
|---|---|---|---|---|---|
| S1 vs B4 | the R image moved to the other physical board | B4 RIGHT | S1 RIGHT | | ≥ 3 points with the new board: board/antenna; < 3: not the board |
| S2 vs S1 | the controllers' marked spots swapped | S1 RIGHT | S2 RIGHT | | ≥ 3 points following RIGHT to its new spot: position; < 3: not the position |
| S1 and S2 both < 3 | neither board nor spot moved it | | | | stays with the controller |

If both S1 and S2 move by 3 points or more, report both and do not pick one cause. A
difference under 3 points is “not shown”, not a cause.

## 5. What to paste back

Each step below names exactly what to copy. Save the monitor output with `Tee-Object`; if a
paste is too long, attach the saved file and still include the named lines in the message.

**Once per session**

- The build record table from §2, with the commit hash, the three
  `build-info*.json` files (variant and flag values) and the artifact name each board ran.
- The boot banner `BLE advertising: ...` and the `STREAM ... bridge_hands=...` line for each
  image as first flashed.
- The pairing record: for each board, the `Discovery:`, `Addresses saved`, `Connecting`,
  `descriptor status`, `BOND RESET`, `FORGET` and `... excluded ... declining` lines. Say
  which case (A or B) was used.
- One photo of the marked spots and one of the two board labels/ports.

**Per run (A1–B4, S1, S2, X)**

- For each active board, the whole log file (`multi-A1-A.txt`, `multi-B1-B.txt`, ...), and
  in the message:
  - the `LINKLOG_FIELDS` header once per board per session;
  - **all** `LINKLOG,` lines between the window start and end (24 per hand for a 2-minute
    window);
  - the `link:` lines for the same period (they carry `mode=`, `rssi`, `lq`, `fcc`);
  - the `STREAM` block at the start and at the end of the run;
  - any `Incoming HID`, `accepting incoming HID connection`, `descriptor status`, `BOND
    RESET`, `FORGET`, `Discovery:` or `... excluded ... declining` line in the run;
  - the wall-clock start/end and the first/last `t_ms`.
- In B runs, label the LEFT rows as board A / L image and the RIGHT rows as board B / R
  image. In A runs, both hands are on board A / REF image. In S1/S2/X, say which physical
  board carries which image.

**Per B run, in addition**

- The app's three latency files (`latency-....csv`, `.json`, `-echo.csv`), the filename
  stem, and which bridge was connected first (the one the RTT belongs to).
- From the JSON: `round_trip.rtt` (`min_us`, `p50_us`, `p90_us`, `p95_us`, `p99_us`,
  `max_us`), `link.mtu`, `link.connection_interval_us`, and per hand `sequence_gaps`,
  `sequence_loss`, `rate_per_s`, `records`, `missing_from_ticks`.
- The Connect screen's per-bridge rows (screenshot or text): name, MTU, stream.
- The reconnect record table: run, bridge, controller, sleep method, the lines from sleep to
  `rate_rx` resuming, and pass/fail.

**Per REF run (A1–A4)**

- The same lines for both hands on board A. The app's `sequence_gaps` per hand, even though
  the pass criterion names the VAR bridges; it is the reference for the phone side.
- The reconnect check at A1 and A4: both hands on the REF board.

**S1/S2/X**

- The same window lines, plus a one-line note of which board was where and which controller
  was on which mark; photos of the labels and marks after each move.
- For X: RIGHT's mean before and after, the L-off timestamp, and the `STREAM` line after the
  L bridge is off (the phone now has one bridge).

Results table (copy one block per session; one row per hand per window; `mode` comes from
the `link:` line, not from `LINKLOG`):

**Session:** `<date/time>` — **position:** `<distance, orientation, photo>` — **phone:** `<model, Android, app build>` — **boards/ports:** `<A, B>` — **firmware:** `<sha>` — **flags:** `<REF/L/R>` — **spacing:** `<m, orientation>`

| # | Image (board) | `bridge_hands` | Window | Hand | `last5s_pct` mean | min–max | `cum_pct` end | `rate_rx` mean | `dev_missing_delta` | `rssi` | `lq` | `fcc` | seq gaps | mode | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | REF (A) | 0 | both | LEFT | | | | | | | | | | | |
| A1 | REF (A) | 0 | both | RIGHT | | | | | | | | | | | |
| B1 | L (A) | 1 | both | LEFT | | | | | | | | | | | |
| B1 | R (B) | 2 | both | RIGHT | | | | | | | | | | | |
| A2 | REF (A) | 0 | both | LEFT | | | | | | | | | | | |
| A2 | REF (A) | 0 | both | RIGHT | | | | | | | | | | | |
| B2 | L (A) | 1 | both | LEFT | | | | | | | | | | | |
| B2 | R (B) | 2 | both | RIGHT | | | | | | | | | | | |
| A3 | REF (A) | 0 | both | LEFT | | | | | | | | | | | |
| A3 | REF (A) | 0 | both | RIGHT | | | | | | | | | | | |
| B3 | L (A) | 1 | both | LEFT | | | | | | | | | | | |
| B3 | R (B) | 2 | both | RIGHT | | | | | | | | | | | |
| A4 | REF (A) | 0 | both | LEFT | | | | | | | | | | | |
| A4 | REF (A) | 0 | both | RIGHT | | | | | | | | | | | |
| B4 | L (A) | 1 | both | LEFT | | | | | | | | | | | |
| B4 | R (B) | 2 | both | RIGHT | | | | | | | | | | | |
| S1 | L (B) | 1 | both | LEFT | | | | | | | | | | | |
| S1 | R (A) | 2 | both | RIGHT | | | | | | | | | | | |
| S2 | L (B) | 1 | both | LEFT | | | | | | | | | | | |
| S2 | R (A) | 2 | both | RIGHT | | | | | | | | | | | |
| X before | L (B) | 1 | both | LEFT | | | | | | | | | | | |
| X before | R (A) | 2 | both | RIGHT | | | | | | | | | | | |
| X after | R (A) | 2 | RIGHT only | RIGHT | | | | | | | | | | | |

Reconnect record:

| Run | Bridge / image | Controller | Sleep method | Wake lines (`Incoming` → `descriptor` → `rate_rx`) | Pass? | notes |
|---|---|---|---|---|---|---|
| B1 | L | LEFT | | | | |
| B1 | R | RIGHT | | | | |
| B2 | L | LEFT | | | | |
| B2 | R | RIGHT | | | | |
| B3 | L | LEFT | | | | |
| B3 | R | RIGHT | | | | |
| B4 | L | LEFT | | | | |
| B4 | R | RIGHT | | | | |
| A1 | REF | LEFT | | | | |
| A1 | REF | RIGHT | | | | |
| A4 | REF | LEFT | | | | |
| A4 | REF | RIGHT | | | | |

## 6. What this sheet does not do

- It makes no hardware claim and reports no result. Nothing here is verified until Nick runs
  a session and pastes the lines above.
- It does not test V1 stagger, sniff, role, QoS or any other single-ESP32 option; those are
  `contention-2.md` and are not changed by this topology.
- It does not settle RIGHT's ~10.8 % by itself: S1/S2 are one-sitting swaps, and a 3-point
  difference is the threshold. A second session is required for a pass.
- It does not make the app measure per-bridge RTT in one export. The per-bridge RTT is one
  export per bridge, labelled with the first-connected bridge; two B runs per bridge give
  the two samples.
