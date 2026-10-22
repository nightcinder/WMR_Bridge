# Run sheet: how much does LEFT move with nothing changed?

> Archived sheet. It was run on 2026-10-22 (user-reported); see ../README.md for the logs.

Why: the LEFT experiments of 2026-10-22 (auto latency, modem sleep) could not be read. The four baselines ranged from 15.4 to 25.6 % (user-reported), and from run 5 on LEFT's RSSI was 5 to 10 dB weaker than in runs 1 to 4. Before testing any option again, we need to know how far LEFT drifts by itself, and whether the loss follows RSSI.

Image: the normal baseline build only (`build-wmr-bridge`, auto latency off, modem sleep on; see the "put it back" step of `run-sheet-left-coex.md`). Flash it once. No other image is used.

## Setup (fixed for the whole session)

- Same position as before, controllers face down and still. Mark the spot (tape, or a photo) and do not touch them, the phone, or the ESP32's antenna side until the session ends.
- nRF Connect: MTU 517, connection priority High, FF14 notify (as in the FF14 runs).
- Note the time of day and the room (doors, people walking by, other 2.4 GHz devices such as a router or headset).

## The run

One continuous, long run, no resets in between:

1. Reset the ESP32 once, wait until both controllers are connected, wait 30 s.
2. Let the monitor log for **30 minutes** (longer is fine, say 60 if it suits you; more is better).
3. Paste the `LINKLOG_FIELDS` header once, then all `LINKLOG,` lines. If the paste is too long, save the monitor output to a file with `idf.py -B build-wmr-bridge -p COM5 monitor | Tee-Object baseline-long.txt` (run from `port\esp32`) and attach the file.

Optional second part, only if the first looks calm: reset and repeat once, to see whether a reset alone changes the level.

## How I will read it (fixed before the data)

- Split the run into 2-minute blocks and take LEFT `last5s` per block, like the earlier runs.
- The spread between blocks is the yardstick for any later option test: an option has to move LEFT more than that.
- Relate `last5s` to RSSI/lq per block to see whether the loss follows the signal level.
- A trend across the run (rising or falling) is reported as drift, not averaged away.

## Afterwards

Nothing to restore: the image is the normal one.
