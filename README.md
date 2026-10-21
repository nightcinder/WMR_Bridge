# WMR_Bridge

ESP32 firmware, built on [BTstack](https://github.com/bluekitchen/btstack), that
connects to **two Windows Mixed Reality motion controllers** over Classic
Bluetooth and forwards their raw input reports to an **Android phone** over a
custom BLE GATT service.

```
 WMR LEFT  --\  Classic Bluetooth HID                       BLE GATT notifications
              >------------------> ESP32 (BTstack) ------------------------------> Android phone
 WMR RIGHT --/  46-byte frames        bounded queue          service FF10 / characteristic FF11
```

It is a **raw pipe**. It does not decode, filter, batch or time-stamp anything;
each controller frame goes to the phone byte-for-byte. Turning those frames into
poses or button states is future work.

> **Project status: experimental, partly verified.** It works in a bench test,
> but most of the checks that would justify trusting it have not been run yet.
> Read [What is and isn't tested](#what-is-and-isnt-tested) before relying on it.

## What it does

- **Controllers.** Acts as the Bluetooth HID host for two specific controllers
  (LEFT and RIGHT, identified by Bluetooth address). Controllers reconnect to the
  ESP32 on their own (incoming connection); the console can also connect
  outwards. Each hand has its own connection state.
- **Frames.** Each controller sends 46-byte input reports (`A1`, report ID `01`,
  44 payload bytes) at roughly 150-180 reports per second per hand in the one
  run that was measured.
- **Queue.** Frames from both hands go into one bounded FIFO (256 frames). If it
  fills, the newest frame is dropped and counted.
- **BLE.** The ESP32 advertises as `WMR Bridge`. When the phone subscribes to
  notifications, frames are sent as BLE notifications, fragmented to fit the
  negotiated ATT MTU (one notification per frame when the MTU is large).
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
| `tests/`, `samples/` | Host unit tests for the protocol and queue; sample frames |
| `integration/` | The one BTstack file that must be replaced to build this (`main-CMakeLists.txt`, plus a diff) |
| `docs/` | Build, protocol, Android, counters, security, reconnect/queue, testing, refactor notes |

Module responsibilities, in brief: `wmr_controller` (HID side), `wmr_transport`
(BLE side, queue pump), `wmr_protocol` and `wmr_queue` (pure C, no BTstack,
unit-tested), `wmr_console`, `wmr_bridge_main` (init and event routing only),
`wmr_config.h` (all tunables).

## Build and flash (Windows)

This repo is not standalone: it builds inside the BTstack ESP32 port. Short
version (full steps, versions and troubleshooting in
[docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md)):

```powershell
# one time: BTstack at the tested commit, this repo inside it, one file replaced
git clone https://github.com/bluekitchen/btstack.git
cd btstack
git checkout e38553977a25fb0b55b383c72c289be0975f422c
git clone <this-repo-url> wmr_bridge
Copy-Item wmr_bridge\integration\main-CMakeLists.txt port\esp32\main\CMakeLists.txt

# edit the two controller addresses
notepad wmr_bridge\src\wmr_config.h

# every session
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1
cd port\esp32
$env:EXAMPLE = "wmr_bridge"
idf.py -B build-wmr-bridge build
idf.py -B build-wmr-bridge -p COM5 -b 115200 flash
idf.py -B build-wmr-bridge -p COM5 -b 115200 monitor    # needs a real terminal; Ctrl+] exits
```

Tested with: ESP-IDF v6.1 (`fff9895c82d7`), BTstack `e38553977a25`, ESP32 rev v1.1
(2 MB flash), Windows 11. Run `idf.py` from `port\esp32`, not from this repo's folder.
`compile_gatt.py` must be run through Python explicitly; on the author's Windows
machine, running it directly started Node.js. The supplied CMake file already does this.

## Using it

1. Flash, open the monitor. You should see `BLE advertising: WMR Bridge (RAW STREAM FF11)`.
2. Power on the controllers (already paired): they connect on their own, and the log
   shows `Incoming HID ... accepting` and `descriptor status=0x00` for each.
   First pairing or recovery: put a controller in pairing mode and press `l` (LEFT) or `r` (RIGHT).
3. On the Android phone: connect to `WMR Bridge`, request the MTU (e.g. 247 or 517),
   request High connection priority, enable notifications on characteristic `FF11`.
   **Repeat all of this after every reconnect.** See [docs/ANDROID.md](docs/ANDROID.md).
4. Read the statistics lines: `rate_tx` should match `rate_rx`, `drop` should stay
   flat, `q` should stay near zero.

Console keys: `l`/`c` connect LEFT, `r` connect RIGHT, `L`/`R` disconnect, `s` status
and counters, `?` help, `k`/`K` **bond reset** LEFT/RIGHT (deletes the ESP32's stored
link key for that controller only; manual, refused while connected).

## BLE interface (summary)

Service `0000FF10-0000-1000-8000-00805F9B34FB`, characteristic
`0000FF11-0000-1000-8000-00805F9B34FB` (read + notify). Each notification is a
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
  loss between the ESP32 and the app is unmeasured. Phone-side sequence checking
  has not been written.
- **Duration.** Nothing longer than about 15 seconds of streaming; no soak test; no
  look at memory, error counters, or queue behavior over hours.
- **Reconnects.** Controller power-cycle while streaming, controller disconnect while the
  phone stays connected, phone disconnect/reconnect with re-subscribe, and what the
  counters do across those events. The behavior is designed and documented
  ([docs/RECONNECT_AND_QUEUE.md](docs/RECONNECT_AND_QUEUE.md)) but unobserved on hardware.
- **Outgoing connect** (`l`/`r`) and the **bond reset** (`k`/`K`) with this version.
- **Phone side.** The phone's own negotiated MTU (the 517 above is the ESP32's view), whether
  High priority was applied, other phones and Android versions. Only one phone, one pair of
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
- Single phone, exactly two controllers, fixed addresses compiled in.
- `tx` means "accepted by the Bluetooth stack", not "received by the phone".

Details and hardening suggestions: [docs/SECURITY.md](docs/SECURITY.md),
counters and their limits: [docs/COUNTERS.md](docs/COUNTERS.md).

## Planned, not done

- Android receiver that validates sequence numbers and reassembles frames.
- Input and sensor decoding (referencing Monado), then calibration and orientation if needed.

## Configuration

Every tunable is a named constant in [src/wmr_config.h](src/wmr_config.h) (controller
addresses, queue size, frame and notification limits, timers, advertising). Raw hex dumps and
detailed HCI tracing are off by default (`WMR_CFG_REPORT_HEXDUMP`, `WMR_CFG_HCI_TRACE`).
Note that `wmr_config.h` ships with the author's two controllers' Bluetooth addresses;
replace them with yours.

## Running the host tests

```powershell
.\tests\run_tests.ps1     # needs gcc, clang or zig on PATH; no ESP-IDF required
```

## License

Derived from BlueKitchen BTstack. Every source file keeps the BlueKitchen copyright and
license notice (see `license_header.txt`). That license permits redistribution and
modification **only for personal, non-commercial use**; commercial use needs a license from
BlueKitchen GmbH (contact@bluekitchen-gmbh.com). The same terms apply to this repository.
