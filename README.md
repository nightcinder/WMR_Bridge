# WMR_Bridge

ESP32 firmware, built on [BTstack](https://github.com/bluekitchen/btstack), that
connects to **two Windows Mixed Reality motion controllers** over Classic
Bluetooth and forwards their input to an **Android phone** over a custom BLE GATT
service, either as raw frames (FF11), as decoded IMU and input records (FF12), or as batches of those records (FF14). The
phone side is the companion app [wmr-companion](https://github.com/nightcinder/wmr-companion);
the overall state, hardware results and roadmap are in its
[docs/STATUS.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/STATUS.md).

```
 WMR LEFT  --\  Classic Bluetooth HID                       BLE GATT notifications
              >------------------> ESP32 (BTstack) ------------------------------> Android phone
 WMR RIGHT --/  46-byte frames        bounded queue          service FF10 / characteristic FF11
```

The raw stream (FF11) is a **raw pipe**: each controller frame goes to the phone
byte-for-byte, with per-hand sequence numbers. The decoded stream (FF12) carries
56-byte records with the sensor clock (`device_ticks`), gyro, accel, buttons, stick,
trackpad, trigger and battery. It sends raw IMU values, not a quaternion, and neither
stream filters or calibrates; orientation fusion runs on the phone. The two streams
are mutually exclusive per connection, and so is the batched stream (FF14); the
app uses FF14. A controller configuration block (FF13, read from the controllers and
served to the phone) and an echo characteristic for round-trip timing (FF15) are also
implemented; see [docs/PROTOCOL.md](docs/PROTOCOL.md).

> **Project status: experimental, partly verified.** Both streams work in bench
> tests with two controllers and a phone, and the app shows correct 3D orientation
> from FF12 (checked visually, user-reported). Controller-to-ESP32 report loss
> (about 9 to 13 % with no phone connected, about 19 % per controller with both
> connected and a phone streaming FF14, user-reported; one controller alone lost about
> 0.5 % (LEFT) and 10.8 % (RIGHT)) is the main open problem; contention for the ESP32
> radio is the working hypothesis, not proven. See [docs/DECODING.md](docs/DECODING.md) and
> the run records in [docs/runs/](docs/runs/README.md). Many
> checks in [docs/TESTING.md](docs/TESTING.md) have not been run. Read
> [What is and isn't tested](#what-is-and-isnt-tested) before relying on it.

## What it does

- **Controllers.** Acts as the Bluetooth HID host for two controllers (LEFT and
  RIGHT), found by their Bluetooth names (`Motion controller - Left` / `- Right`)
  and remembered by address in NVS ([docs/DISCOVERY.md](docs/DISCOVERY.md); not yet
  run on hardware). Controllers reconnect to the
  ESP32 on their own (incoming connection); the console can also connect
  outwards. Each hand has its own connection state.
- **Frames.** Each controller sends 46-byte input reports (`A1`, report ID `01`,
  44 payload bytes) at roughly 150-180 reports per second per hand in the one
  run that was measured.
- **Queue.** Frames from both hands go into one bounded FIFO (256 frames). If it
  fills, the newest frame is dropped and counted.
- **BLE.** The ESP32 advertises as `WMR Bridge`. When the phone subscribes to FF11,
  frames are sent as BLE notifications, fragmented to fit the negotiated ATT MTU
  (one notification per frame when the MTU is large). When it subscribes to FF12
  instead (MTU 59 or more), each report is decoded and sent as one 56-byte record.
  Only one of the two can be enabled at a time.
- **Sequence numbers.** Every frame gets a per-hand sequence number *when it
  arrives*, whether or not it is delivered, so the phone can detect loss by
  looking for gaps.
- **Statistics.** A line every 5 s shows queue depth, negotiated MTU, and per
  hand received/sent/dropped/offline/purged counts and rates.
- **Console.** Serial keys to connect/disconnect each controller, print status,
  and (manually) delete a stored Bluetooth bond.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/` | The firmware: controller handling, BLE transport, protocol, queue, console, init |
| `wmr_bridge.gatt` | GATT database |
| `tests/`, `samples/` | Host unit tests for the protocol, queue and decoder; sample frames |
| `tools/` | PC-side capture (`capture_ble.py`, needs hardware and `bleak`) and rotation analysis (`analyze_capture.py`); see [docs/DECODING.md](docs/DECODING.md) |
| `tools/firmware/` | One-command build, packaging and flash (`build.py`) plus the browser-flasher manifest logic; see [docs/FLASHING.md](docs/FLASHING.md) |
| `integration/` | The one BTstack file that must be replaced to build this (`main-CMakeLists.txt`, plus a diff) |
| `docs/` | Build, flashing, protocol, Android, counters, security, reconnect/queue, controller discovery, testing, refactor notes |

Module responsibilities, in brief: `wmr_controller` (HID side), `wmr_transport`
(BLE side, queue pump), `wmr_protocol` and `wmr_queue` (pure C, no BTstack,
unit-tested), `wmr_console`, `wmr_bridge_main` (init and event routing only),
`wmr_config.h` (all tunables).

## Build and flash

This repo is not standalone: it builds inside the BTstack ESP32 port, with one
BTstack file replaced. The one-command path does that setup for you and writes
a browser-installable image ([docs/FLASHING.md](docs/FLASHING.md)):

```powershell
# Windows PowerShell: load ESP-IDF once per terminal
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1
python tools\firmware\build.py --flash COM5
```

```bash
# Linux / macOS
python3 tools/firmware/build.py --idf-export ~/esp/esp-idf/export.sh --flash /dev/ttyUSB0
```

The first run clones the pinned BTstack commit into `.fw-build/`; later runs
reuse it. The output in `dist/` includes a merged factory image and a browser
flasher page. To install a released build without ESP-IDF, use the browser
installer described in [docs/FLASHING.md](docs/FLASHING.md); for a local build,
serve `dist/` over HTTP.

The manual steps remain in [docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md).
Short version: put this repo at `<btstack>\wmr_bridge`, copy
`integration\main-CMakeLists.txt` over `port\esp32\main\CMakeLists.txt`, then
build `EXAMPLE=wmr_bridge` with `idf.py` from `port\esp32`.

Tested with: ESP-IDF v6.1 (`fff9895c82d7`), BTstack `e38553977a25`, ESP32 rev v1.1
(2 MB flash), Windows 11. `compile_gatt.py` must be run through Python explicitly;
on the author's Windows machine, running it directly started Node.js. The
supplied CMake file already does this.

## Using it

1. Flash, open the monitor. You should see `BLE advertising: WMR Bridge (FF11 raw stream, FF12 decoded stream, FF14 batched decoded stream)`.
2. Power on the controllers (already paired): they connect on their own, and the log
   shows `Incoming HID ... accepting` and `descriptor status=0x00` for each.
   First flash: put the controllers in pairing mode within a minute of boot; discovery finds
   them by name, stores their addresses and pairs ([docs/DISCOVERY.md](docs/DISCOVERY.md)).
   Later: `d` runs discovery again for a hand without an address, `F` forgets the stored
   addresses, `l`/`r` reconnect a known controller.
3. On the Android phone: connect to `WMR Bridge`, request the MTU **517** (the app does;
   the smallest MTU FF14 batching accepts is 61), request High connection priority, enable
   notifications on one of `FF11`, `FF12` or `FF14` (only one at a time).
   **Repeat all of this after every reconnect.** See [docs/ANDROID.md](docs/ANDROID.md).
4. Read the statistics lines: `rate_tx` should match `rate_rx`, `drop` should stay
   flat, `q` should stay near zero.

Console keys: `l`/`c` connect LEFT, `r` connect RIGHT, `L`/`R` disconnect, `s` status
and counters, `v` firmware version, `?` help, `d` discovery, `F` **forget** stored controller addresses,
`k`/`K` **bond reset** LEFT/RIGHT (deletes the ESP32's stored link key for that controller
only; manual, refused while connected).

## BLE interface (summary)

Service `0000FF10-0000-1000-8000-00805F9B34FB`; raw characteristic
`0000FF11-0000-1000-8000-00805F9B34FB` (read + notify); decoded characteristic
`0000FF12-0000-1000-8000-00805F9B34FB` (notify only, 56-byte records); batched
decoded `FF14` (notify only, up to 9 records per notification at MTU 517); controller
config block `FF13` (read in 496-byte windows, CRC-32); echo `FF15` (4-byte request, 20-byte
answer, independent of the stream exclusion); firmware version `FF16` (read-only UTF-8
string). All layouts: [docs/PROTOCOL.md](docs/PROTOCOL.md). Each notification is a
12-byte little-endian header (`'R'`, hand, u32 sequence, u16 total length,
u16 offset, u16 chunk length) followed by a chunk of the frame. A read of FF11
returns a 16-byte status record. Exact layout, fragmentation and reassembly rules:
[docs/PROTOCOL.md](docs/PROTOCOL.md). The UUIDs are experimental, not registered.

## What is and isn't tested

### Actually run

| What | Result |
| --- | --- |
| Firmware build (ESP-IDF v6.1, ESP32) | Passes, no warnings from this code |
| Host unit tests of the protocol encoder, reassembler and queue (6087 checks) | Pass. Cover fragmented, incomplete, lost, duplicated, interleaved and malformed fragments, and a fixed byte-level example |
| Flash and boot on a real ESP32 | Passes; advertises; stats print every 5 s; no debug spam |
| **One bench run, about 15 s, both controllers and a phone** (log pasted by the user, not instrumented on the phone) | Both controllers connected on their own; ATT MTU went 23 to 517; about 174/s (LEFT) and 154/s (RIGHT); sent = received for both hands; 0 drops, 0 offline, 0 purged, 0 send errors; queue back to 0 after a startup peak of 11; counter accounting added up exactly |

### Not tested yet

- **Lossless end-to-end delivery.** The bridge's counters only prove frames were
  handed to the BLE stack. Nothing on the phone has checked sequence numbers, so
  loss between the ESP32 and the app is checked only in a few bench captures (the
  companion app counts sequence gaps and reported none); no long run.
- **Duration.** Longest user-reported run is about 25 minutes (a baseline); no soak test; no
  look at memory, error counters, or queue behavior over hours.
- **Reconnects.** Controller power-cycle while streaming, controller disconnect while the
  phone stays connected, phone disconnect/reconnect with re-subscribe, and what the
  counters do across those events. The behavior is designed and documented
  ([docs/RECONNECT_AND_QUEUE.md](docs/RECONNECT_AND_QUEUE.md)) but unobserved on hardware.
- **Outgoing connect** (`l`/`r`) and the **bond reset** (`k`/`K`) with this version.
- **Phone side.** The phone's own negotiated MTU as shown by the app (the 517 above is the ESP32's view), other phones and Android versions. Only one phone, one pair of
  controllers and one ESP32 board have been used.
- **Sample captures** labelled by hand. `samples/controller_frames.txt` comes from earlier
  bring-up sessions where LEFT/RIGHT was not recorded.
- **Regression comparison** against the original single-file version under identical conditions.
- Known oddity, not investigated: each controller logs `SET_PROTOCOL handshake status=0x03`.

Full list and details: [docs/TESTING.md](docs/TESTING.md).

## Limitations and security

- The BLE service has **no pairing, authentication or encryption**. Anyone in range can
  connect, read, and receive the raw controller data; one unwanted connection also blocks
  the real phone.
- Classic pairing confirmations (Secure Simple Pairing) are **accepted automatically** for
  any device; legacy PIN requests are rejected.
- No flash encryption or secure boot. The serial console is unauthenticated.
- Single phone, exactly two controllers. Controllers are recognised by name until their
  addresses are stored; a name can be spoofed (see [docs/DISCOVERY.md](docs/DISCOVERY.md)).
- `tx` means "accepted by the Bluetooth stack", not "received by the phone".

Details and hardening suggestions: [docs/SECURITY.md](docs/SECURITY.md),
counters and their limits: [docs/COUNTERS.md](docs/COUNTERS.md).

## Done since the first release, open items

Done: FF14 batching, the FF13 config block read (the app decodes calibration from it), the FF15
echo for round-trip timing, discovery by name. Hardware results are user-reported and listed in
[docs/TESTING.md](docs/TESTING.md); [HANDOFF notes](https://github.com/nightcinder/wmr-companion/blob/main/HANDOFF.md)
in the app repo list what is still not run.

Open:

- Explaining and reducing controller-to-ESP32 report loss (issue [#18](../../issues/18)); coexistence options tested so far showed no effect ([docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md), [docs/runs/](docs/runs/README.md)).
- The remaining unknowns in [docs/DECODING.md](docs/DECODING.md) (accel scale, temperature, battery, input ranges).
- Where the roughly 40 ms phone round trip goes (median, user-reported first run).
- BLE security (no pairing or encryption today), see [docs/SECURITY.md](docs/SECURITY.md).

## Configuration

Every tunable is a named constant in [src/wmr_config.h](src/wmr_config.h) (controller
discovery and optional address overrides, queue size, frame and notification limits, timers, advertising). Raw hex dumps and
detailed HCI tracing are off by default (`WMR_CFG_REPORT_HEXDUMP`, `WMR_CFG_HCI_TRACE`).
It ships with no controller addresses; `WMR_CFG_LEFT_ADDRESS` / `WMR_CFG_RIGHT_ADDRESS` are
optional overrides that skip discovery for that hand. Discovery settings:
`WMR_CFG_DISCOVERY_*`.

## Running the host tests

```powershell
.\tests\run_tests.ps1     # needs gcc, clang or zig on PATH; no ESP-IDF required
```

## License

Derived from BlueKitchen BTstack. Every source file keeps the BlueKitchen copyright and
license notice (see `license_header.txt`). That license permits redistribution and
modification **only for personal, non-commercial use**; commercial use needs a license from
BlueKitchen GmbH (contact@bluekitchen-gmbh.com). The same terms apply to this repository.
