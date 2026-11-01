# WMR_Bridge

The default bridge is ESP32 firmware built on [BTstack](https://github.com/bluekitchen/btstack).
It connects to two Windows Mixed Reality motion controllers or
PlayStation VR2 Sense controllers (searched for by default; never run on a
controller) over Classic Bluetooth. It forwards their input to an Android phone
over a custom BLE GATT service. The input can go as raw frames (FF11), as decoded
IMU and input records (FF12), or as batches of those records (FF14). The phone side
is the companion app [wmr-companion](https://github.com/nightcinder/wmr-companion).
The overall state, hardware results and roadmap are in its
[docs/STATUS.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/STATUS.md).

A second, supported Linux / Raspberry Pi host bridge is in [`pi/`](pi/README.md).
It uses BlueZ for Classic Bluetooth and replaces BLE with a WebSocket on 5 GHz Wi-Fi,
while sending the same FF11/FF12/FF14 payloads. See
[docs/PI_BRIDGE.md](docs/PI_BRIDGE.md) and the bring-up run sheet
[docs/runs/sheets/pi-bridge.md](docs/runs/sheets/pi-bridge.md). Both the ESP32-over-BLE
firmware (default) and the Pi-over-Wi-Fi daemon are supported first-class bridge
targets. The Pi bridge is host-tests-only until the Pi sitting runs. The ESP32
firmware has the bench runs recorded below.

```
 WMR LEFT  --\  Classic Bluetooth HID                       BLE GATT notifications
              >------------------> ESP32 (BTstack) ------------------------------> Android phone
 WMR RIGHT --/  46/79-byte frames     bounded queue          service FF10 / characteristic FF11
```

The raw stream (FF11) is a raw pipe. Each controller frame goes to the phone
byte-for-byte, with per-hand sequence numbers. The decoded stream (FF12) carries
56-byte records with the sensor clock (`device_ticks`), gyro, accel, buttons, stick,
trackpad, trigger and battery. It sends raw IMU values, not a quaternion. Neither
stream filters or calibrates. Orientation fusion runs on the phone. The two streams
are mutually exclusive per connection, and so is the batched stream (FF14). The app
uses FF14. A controller configuration block (FF13, read from the controllers and
served to the phone) and an echo characteristic for round-trip timing (FF15) are also
implemented. See [docs/PROTOCOL.md](docs/PROTOCOL.md).

> Project status: experimental, partly verified. Both streams work in bench
> tests with two controllers and a phone. The app shows correct 3D orientation
> from FF12 (checked visually, user-reported). Controller-to-ESP32 report loss
> (about 9 to 13 % with no phone connected, about 19 % per controller with both
> connected and a phone streaming FF14, user-reported; one controller alone lost
> about 0.5 % (LEFT) and 10.8 % (RIGHT)) is the main open problem. Contention for
> the ESP32 radio is the working hypothesis, not proven. See [docs/DECODING.md](docs/DECODING.md)
> and the run records in [docs/runs/](docs/runs/README.md). Many checks in
> [docs/TESTING.md](docs/TESTING.md) have not been run. Read
> [What is and is not tested](#what-is-and-is-not-tested) before relying on it.

## What it does

- Controllers. The firmware acts as the Bluetooth HID host for two controllers
  (LEFT and RIGHT). They are found by their Bluetooth names (`Motion controller - Left` /
  `- Right` or `PlayStation VR2 Sense Controller (L)` / `(R)`) and remembered by
  address and model in NVS ([docs/DISCOVERY.md](docs/DISCOVERY.md); not yet run on
  hardware). Controllers reconnect to the ESP32 on their own (incoming connection).
  The console can also connect outwards. Each hand has its own connection state.
- Frames. A WMR controller sends 46-byte input reports (`A1`, report ID `01`,
  44 payload bytes) at roughly 150-180 reports per second per hand in the one
  run that was measured. A PSVR2 Sense controller sends `A1 31` reports of up to
  79 bytes. These are forwarded raw only for now ([docs/PSVR2.md](docs/PSVR2.md)).
- Queue. Frames from both hands go into one bounded FIFO (256 frames). If it
  fills, the newest frame is dropped and counted.
- BLE. The ESP32 advertises as `WMR Bridge`. The phone must pair and bond
  (LE Secure Connections). Every data characteristic refuses an unbonded central.
  When the phone subscribes to FF11, frames are sent as BLE notifications,
  fragmented to fit the negotiated ATT MTU (one notification per frame when the
  MTU is large). When it subscribes to FF12 instead (MTU 59 or more), each report
  is decoded and sent as one 56-byte record. Only one of the two can be enabled at
  a time.
- Sequence numbers. Every frame gets a per-hand sequence number when it arrives,
  whether or not it is delivered. The phone can detect loss by looking for gaps.
- Statistics. A line every 5 s shows queue depth, negotiated MTU, and per hand
  received/sent/dropped/offline/purged counts and rates.
- Console. Serial keys connect/disconnect each controller, print status, and
  (manually) delete a stored Bluetooth bond.

The default firmware build operates in the dual-controller topology described above
(two controllers connected to one ESP32 bridge). A single-hand / multi-bridge
topology (one ESP32 bridge per hand) is also supported via compile-time flags
(`WMR_CFG_BRIDGE_HANDS=1` for LEFT only, `2` for RIGHT only) and advertised BLE
name suffixes (`WMR Bridge L` / `WMR Bridge R`, via `WMR_CFG_BLE_NAME`). In
single-hand mode, inquiry, incoming connections, outgoing connections, and address
persistence are gated strictly to the designated hand. This topology is untested
on hardware. The BLE GATT protocol, record formats, and characteristics are
identical. See [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Repository layout

| Path | Contents |
| --- | --- |
| `src/` | The firmware: controller handling, BLE transport, protocol, queue, console, init |
| `wmr_bridge.gatt` | GATT database |
| `tests/`, `samples/` | Host unit tests (protocol, queue, decoder, FF14 batching, FF15 echo, FF13 config, discovery, diagnostics, status LED, phone-link security); sample frames |
| `tools/` | PC-side capture (`capture_ble.py`, needs hardware and `bleak`) and rotation analysis (`analyze_capture.py`); see [docs/DECODING.md](docs/DECODING.md) |
| `tools/firmware/` | One-command build, packaging and flash (`build.py`) plus the browser-flasher manifest logic; see [docs/FLASHING.md](docs/FLASHING.md) |
| `pi/` | Linux / Raspberry Pi host bridge over Wi-Fi: BlueZ hidraw source, WebSocket transport, decoder and host tests; see [docs/PI_BRIDGE.md](docs/PI_BRIDGE.md) |
| `integration/` | The one BTstack file that must be replaced to build this (`main-CMakeLists.txt`, plus a diff) |
| `docs/` | Build, flashing, protocol, Android, counters, security, BLE UUID migration, reconnect/queue, controller discovery, PSVR2, battery life and packet-loss/prediction investigations, testing, refactor notes |

Module responsibilities, in brief: `wmr_controller` (HID side), `wmr_transport`
(BLE side, queue pump), `wmr_protocol` and `wmr_queue` (pure C, no BTstack,
unit-tested), `wmr_console`, `wmr_bridge_main` (init and event routing only),
`wmr_config.h` (all tunables).

## Build and flash

This repo is not standalone. It builds inside the BTstack ESP32 port, with one
BTstack file replaced. The one-command path does that setup for you and writes a
browser-installable image ([docs/FLASHING.md](docs/FLASHING.md)):

```powershell
# Windows PowerShell: load ESP-IDF once per terminal
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1
python tools\firmware\build.py --flash COM5
```

```bash
# Linux / macOS
python3 tools/firmware/build.py --idf-export ~/esp/esp-idf/export.sh --flash /dev/ttyUSB0
```

The first run clones the pinned BTstack commit into `.fw-build/`. Later runs
reuse it. The output in `dist/` includes a merged factory image and a browser
flasher page. `0.3.3` is the current release. It is the ONY-699 review-pass
release on top of 0.3.2: the BLE HID gamepad now maps each control to its
Android usage slot, a controller link is declared down after a 5 s supervision
timeout, and FF17 CONNECT acknowledges a hand that is already open. No
protocol payload change; companions at 0.2.2 or later continue to work.
Packaged releases are CI-built and hardware-unverified. See
[CHANGELOG.md](CHANGELOG.md) for the full notes. To install the current release
without ESP-IDF, use the hosted browser installer at
<https://nightcinder.github.io/WMR_Bridge/>, which serves all three variants. The
[GitHub release](https://github.com/nightcinder/WMR_Bridge/releases/latest)
also carries the images and manifests. For a local build, serve `dist/` over
HTTP. Details are in [docs/FLASHING.md](docs/FLASHING.md).

For one hand per bridge (or single-hand testing on a single board), add
`--variant left` or `--variant right`. `--variant both` is the default and is
unchanged. See [Two bridges](docs/FLASHING.md#two-bridges-one-hand-each).

### Status LED

The firmware drives one addressable RGB LED (WS2812/NeoPixel) and shows which
controllers are connected: red = none, yellow = left only, blue = right only,
green = both. Connect the LED's data line to GPIO 4, plus 5 V and GND. The LED is
red at boot until a controller connects.

A classic ESP32 devkit usually has no addressable LED on board, so this assumes
an external WS2812. The prebuilt images drive a second data line on GPIO 27, the
onboard SK6812 of an M5Stack ATOM Lite. The same image lights either board. A
pin with nothing wired only sees one short frame per 250 ms poll. The monitor
prints `LED: WS2812 on GPIO 4 27` at boot and `LED: red|yellow|blue|green` when
the colour changes. A missing first line means the driver did not start.

If your LED is wired elsewhere, pass the pin at build time:
`EXTRA_CFLAGS="-DWMR_CFG_STATUS_LED_GPIO=5"` (PowerShell:
`$env:EXTRA_CFLAGS = "-DWMR_CFG_STATUS_LED_GPIO=5"`). Add
`-DWMR_CFG_STATUS_LED_GPIO_ALT=-1` when that is the only LED you drive. Set
`-DWMR_CFG_STATUS_LED=0` to build without one. The colour follows the connected
controllers, not the stored pairings. A single-hand build only shows red until its
hand connects, then that hand's colour.

The manual steps remain in [docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md).
Short version: put this repo at `<btstack>\wmr_bridge`, copy
`integration\main-CMakeLists.txt` over `port\esp32\main\CMakeLists.txt`, then
build `EXAMPLE=wmr_bridge` with `idf.py` from `port\esp32`.

Tested with: ESP-IDF v6.1 (`fff9895c82d7`), BTstack `e38553977a25`, ESP32 rev v1.1
(2 MB flash), Windows 11. `compile_gatt.py` must be run through Python explicitly.
On the author's Windows machine, running it directly started Node.js. The supplied
CMake file already does this.

## Using it

1. Flash and open the monitor. You should see `BLE advertising: WMR Bridge (FF11 raw stream, FF12 decoded stream, FF14 batched decoded stream)`.
2. Power on the controllers (already paired). They connect on their own. The log
   shows `Incoming HID ... accepting` and `descriptor status=0x00` for each.
   First flash: put the controllers in pairing mode within a minute of boot.
   Discovery finds them by name, stores their addresses and pairs
   ([docs/DISCOVERY.md](docs/DISCOVERY.md)).
   Later: `d` runs discovery again for a hand without an address. `F` forgets the
   stored addresses. `l`/`r` reconnect a known controller.
3. On the Android phone: connect to `WMR Bridge` and accept the pairing dialog
   (the bridge requires LE Secure Connections and bonding; it appears once per
   phone, then the phone re-encrypts on later connections). Then request the MTU
   517 (the app does; the smallest MTU FF14 batching accepts is 61). Request
   High connection priority. Enable notifications on one of `FF11`, `FF12` or
   `FF14` (only one at a time).
   Repeat all of this after every reconnect. See [docs/ANDROID.md](docs/ANDROID.md).
4. Read the statistics lines. `rate_tx` should match `rate_rx`. `drop` should stay
   flat. `q` should stay near zero.

Console keys: `l`/`c` connect LEFT, `r` connect RIGHT, `L`/`R` disconnect, `s`
status and counters, `v` firmware version, `?` help, `d` discovery, `F` forget
stored controller addresses, `k`/`K` bond reset LEFT/RIGHT (deletes the ESP32's
stored link key for that controller only; manual, refused while connected),
`P` phone bond reset (deletes all bonded BLE phones; refused while a phone is
connected), `b` all controller link keys.

### Reading the monitor

Firmware logs one format (seconds with a millisecond fraction, one-letter level,
six-char tag):

```
[    12.345] I pair   LEFT ready: HID descriptor received (model WMR)
[    12.980] W pair   RIGHT authentication failed: authentication failed (link key mismatch) (0x05); remove the controller's old pairing or press k/K for a bond reset
```

Tags: `boot`, `pair`, `link`, `psvr2`, `stagger`, `ff13` (plus older `BLE`/`FF`
lines not yet converted). Read the `pair` tag for pairing problems. Discovery
ends with a per-hand summary: ready / waiting for descriptor / known but
disconnected / not paired with next action. When a controller will not pair, look
for `link key mismatch` (0x05), `PIN or key missing` (0x06) or `remote user
terminated` (0x13). The line already states the next step (remove the old pairing
or press `k`/`K` for a bond reset). See [docs/DISCOVERY.md](docs/DISCOVERY.md).

## Linux / Raspberry Pi host bridge (Wi-Fi)

The firmware in `src/` is the default. A supported second target in
[`pi/`](pi/README.md) instead runs on a Linux machine with Bluetooth Classic and
a 5 GHz Wi-Fi adapter (a Raspberry Pi 4 or 5 is the reference). BlueZ owns the
Classic HID host side. The bridge reads `/dev/hidraw*`, decodes reports, and
serves the same FF11/FF12/FF14 payloads over a WebSocket. This removes BLE
airtime from the phone link without changing the companion app's parsers.

```bash
cd pi
./run_tests.sh
python3 -m wmr_bridge --replay ../samples/controller_frames.txt   # no hardware
python3 tools/wifi_probe.py --stream batched                      # second terminal
```

The `0.1.0` release adds `--hand left|right` for one Pi per hand, optional mDNS
advertisement (`--advertise-mdns`), a udev rule for `/dev/hidraw*` access and a
self-contained `pi/install.sh` installer for a systemd service.
`--replay-once` stops after one replay pass.

For live use, pair the controllers with the Pi first. `python3 -m wmr_bridge --list`
shows the hidraw nodes. Full setup, the wire protocol, security notes and the
hardware test checklist are in [docs/PI_BRIDGE.md](docs/PI_BRIDGE.md). The
first-sitting bring-up procedure is in
[docs/runs/sheets/pi-bridge.md](docs/runs/sheets/pi-bridge.md). The `pi/` tree
builds two installable artifacts: `wmr-bridge_<version>_all.deb` and
`wmr-bridge-<version>.tar.gz`, plus a piped `install.sh` that fetches and
verifies the matching release tarball. The `wmr-pi-bridge-0.1.0.tar.gz` asset on
the v0.1.0 GitHub release is the only old packaged Pi tarball. The `pi/` tree in
this repository is package `0.2.3`. Every `v*` release now attaches the current
`.deb`, tarball, both `.sha256` files and `install.sh`. The one-line install
works from `releases/latest/download/install.sh`.

## BLE interface (summary)

Service `0000FF10-0000-1000-8000-00805F9B34FB`. Raw characteristic
`0000FF11-0000-1000-8000-00805F9B34FB` (read + notify). Decoded characteristic
`0000FF12-0000-1000-8000-00805F9B34FB` (notify only, 56-byte records). Batched
decoded `FF14` (notify only, up to 9 records per notification at MTU 517).
Controller config block `FF13` (read in 496-byte windows, CRC-32). Echo `FF15`
(4-byte request, 20-byte answer, independent of the stream exclusion). Firmware
version `FF16` (read-only UTF-8 string). Compatibility matrix across firmware
versions and Dart library packages: [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).
All layouts: [docs/PROTOCOL.md](docs/PROTOCOL.md). Each notification is a
12-byte little-endian header (`'R'`, hand, u32 sequence, u16 total length,
u16 offset, u16 chunk length) followed by a chunk of the frame. A read of FF11
returns a 16-byte status record. Exact layout, fragmentation and reassembly rules:
[docs/PROTOCOL.md](docs/PROTOCOL.md). The UUIDs are experimental, not registered.

## What is and is not tested

### Actually run

| What | Result |
| --- | --- |
| Firmware build (ESP-IDF v6.1, ESP32) | Passes, no warnings from this code |
| Host unit tests of the protocol encoder, reassembler and queue (6127 checks) | Pass. Cover fragmented, incomplete, lost, duplicated, interleaved and malformed fragments, and a fixed byte-level example |
| Host unit tests of the Linux / Raspberry Pi Wi-Fi bridge (`pi/run_tests.sh`, 122 tests) | Pass (host tests only; never run on a Raspberry Pi or with real controllers) |
| Flash and boot on a real ESP32 | Passes; advertises; stats print every 5 s; no debug spam |
| One bench run, about 15 s, both controllers and a phone (log pasted by the user, not instrumented on the phone) | Both controllers connected on their own; ATT MTU went 23 to 517; about 174/s (LEFT) and 154/s (RIGHT); sent = received for both hands; 0 drops, 0 offline, 0 purged, 0 send errors; queue back to 0 after a startup peak of 11; counter accounting added up exactly |

### Not tested yet

- Lossless end-to-end delivery. The bridge's counters only prove frames were
  handed to the BLE stack. Nothing on the phone has checked sequence numbers. Loss
  between the ESP32 and the app is checked only in a few bench captures (the
  companion app counts sequence gaps and reported none). No long run.
- Duration. Longest user-reported run is about 25 minutes (a baseline). No soak
  test. No look at memory, error counters, or queue behavior over hours.
- Reconnects. Controller power-cycle while streaming, controller disconnect while
  the phone stays connected, phone disconnect/reconnect with re-subscribe, and
  what the counters do across those events. The behavior is designed and
  documented ([docs/RECONNECT_AND_QUEUE.md](docs/RECONNECT_AND_QUEUE.md)) but
  unobserved on hardware.
- Outgoing connect (`l`/`r`) and the bond reset (`k`/`K`) with this version.
- Phone side. The phone's own negotiated MTU as shown by the app (the 517 above
  is the ESP32's view), other phones and Android versions. Only one phone, one
  pair of controllers and one ESP32 board have been used.
- Sample captures labelled by hand. `samples/controller_frames.txt` comes from
  earlier bring-up sessions where LEFT/RIGHT was not recorded.
- Regression comparison against the original single-file version under identical
  conditions.
- PSVR2 Sense controllers. The recognition, model storage, raw forwarding and HID
  wake path are host-tested and build-tested but have never run on a PSVR2
  controller. They are searched for by default alongside WMR
  ([docs/PSVR2.md](docs/PSVR2.md)).
- Linux / Raspberry Pi Wi-Fi host bridge (`pi/`). Second supported target, but
  host-tests-only until the Pi sitting runs
  ([docs/PI_BRIDGE.md](docs/PI_BRIDGE.md),
  [docs/runs/sheets/pi-bridge.md](docs/runs/sheets/pi-bridge.md)).
- Known oddity, not investigated: each controller logs `SET_PROTOCOL handshake
  status=0x03`.

Full list and details: [docs/TESTING.md](docs/TESTING.md).

## Limitations and security

- The BLE service requires LE Secure Connections pairing with bonding. An
  unbonded central is refused (disconnected). Only one phone is served at a
  time. Because the ESP32 has no display or keypad, pairing is Just Works. The
  link is encrypted but not MITM-protected. A device that pairs first can take
  the phone slot. The FF10 UUIDs are still experimental. The 128-bit cutover is
  [docs/BLE_MIGRATION.md](docs/BLE_MIGRATION.md).
- Classic pairing confirmations (Secure Simple Pairing) are accepted automatically
  for any device. Legacy PIN requests are rejected.
- No flash encryption or secure boot. The serial console is unauthenticated.
- Single phone, exactly two controllers. Controllers are recognised by name until
  their addresses are stored. A name can be spoofed (see
  [docs/DISCOVERY.md](docs/DISCOVERY.md)).
- PSVR2 Sense controllers are raw-only today. FF12/FF14 have no PSVR2 decoder.
  FF13 is not read. The wake request is unverified and has never run on a
  controller (see [docs/PSVR2.md](docs/PSVR2.md)). Each hand's model is
  independent, but the decoded stream remains WMR-only.
- `tx` means "accepted by the Bluetooth stack", not "received by the phone".

Details and hardening suggestions: [docs/SECURITY.md](docs/SECURITY.md).
Counters and their limits: [docs/COUNTERS.md](docs/COUNTERS.md).

## Done since the first release, open items

Done: FF14 batching, the FF13 config block read (the app decodes calibration from
it), the FF15 echo for round-trip timing, discovery by name. Hardware results are
user-reported and listed in [docs/TESTING.md](docs/TESTING.md). HANDOFF notes in
the app repo list what is still not run:
<https://github.com/nightcinder/wmr-companion/blob/main/HANDOFF.md>.

Open:

- Explaining and reducing controller-to-ESP32 report loss (issue
  [#18](https://github.com/nightcinder/WMR_Bridge/issues/18)). Coexistence
  options tested so far showed no effect
  ([docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md), [docs/runs/](docs/runs/README.md)).
  Why the measured loss is too much for a single ESP32 to be the final VR-grade
  radio, whether a capped pose predictor should hide the remaining sample-age
  jitter, and why it belongs in the shared library rather than the firmware or
  app: [docs/PREDICTION.md](docs/PREDICTION.md).
- The remaining unknowns in [docs/DECODING.md](docs/DECODING.md) (accel scale,
  temperature, battery, input ranges).
- Where the roughly 40 ms phone round trip goes (median, user-reported first run).
- BLE UUID cutover to the project-owned 128-bit set, and the owner decision that
  ends FF10 support: see [docs/BLE_MIGRATION.md](docs/BLE_MIGRATION.md).
- Hardware validation of the supported Linux/Raspberry Pi Wi-Fi host bridge
  (P1-P10 in [docs/PI_BRIDGE.md](docs/PI_BRIDGE.md) and
  [docs/runs/sheets/pi-bridge.md](docs/runs/sheets/pi-bridge.md)). It is
  host-tested only.

## Configuration

Every tunable is a named constant in [src/wmr_config.h](src/wmr_config.h)
(controller discovery and optional address overrides, queue size, frame and
notification limits, timers, advertising). Raw hex dumps and detailed HCI tracing
are off by default (`WMR_CFG_REPORT_HEXDUMP`, `WMR_CFG_HCI_TRACE`). It ships with
no controller addresses. `WMR_CFG_LEFT_ADDRESS` / `WMR_CFG_RIGHT_ADDRESS` are
optional overrides that skip discovery for that hand (WMR only; a PSVR2
controller has to be discovered by name). Discovery settings:
`WMR_CFG_DISCOVERY_*`.

## Running the host tests

```powershell
.\tests\run_tests.ps1     # needs gcc, clang or zig on PATH; no ESP-IDF required
```

The Linux/Pi host bridge has its own standard-library Python suite:

```bash
./pi/run_tests.sh
```

## License

Derived from BlueKitchen BTstack. Every source file keeps the BlueKitchen copyright
and license notice (see `license_header.txt`). That license permits redistribution
and modification only for personal, non-commercial use. Commercial use needs a
license from BlueKitchen GmbH (contact@bluekitchen-gmbh.com). The same terms apply
to this repository.
