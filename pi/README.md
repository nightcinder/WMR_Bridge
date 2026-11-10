# WMR Bridge on Linux / Raspberry Pi (Wi-Fi transport)

This directory is the second supported first-class target. ESP32-over-BLE is the
default. The host-side replacement runs on any Linux machine with Bluetooth
Classic and a 5 GHz Wi-Fi adapter. A Raspberry Pi 4 or 5 is the reference target
(dual-band Wi-Fi and BT 5.0). A Raspberry Pi 3 Model B+ is expected to work
(dual-band Wi-Fi and BT 4.2 Classic, with less CPU headroom and 1 GB RAM). A Pi
Zero 2 W is not suitable, because its Wi-Fi is 2.4 GHz only. That band shares
the radio with Bluetooth.

The host bridge:

- uses BlueZ as the Classic Bluetooth HID host (controllers pair with the Pi
  instead of the ESP32);
- decodes reports and serves the same FF11 raw, FF12 decoded and FF14 batched
  payloads as the firmware;
- carries them over a WebSocket on Wi-Fi instead of BLE GATT.

The full design, wire protocol, security notes, setup steps and hardware test
plan are in [`docs/PI_BRIDGE.md`](../docs/PI_BRIDGE.md). The bring-up run sheet is `docs/runs/sheets/bench.md` (P1-P14 are in
[`docs/PI_BRIDGE.md`](../docs/PI_BRIDGE.md)).

New to the Pi? Read the simple-English beginner guide:
[`docs/RPI_INSTALL_GUIDE.md`](../docs/RPI_INSTALL_GUIDE.md).

## Quick start (replay, no controllers)

```bash
cd pi
python3 -m unittest discover -s tests -v          # 312 host tests
python3 -m wmr_bridge --replay ../samples/controller_frames.txt --bind 127.0.0.1

# in a second terminal
python3 tools/wifi_probe.py --stream batched --duration 10
```

The replay path is a complete smoke test of the WebSocket transport, the
decoder and the three stream formats. `--bind` defaults to `127.0.0.1`. The
quick start needs no token and is not reachable from the LAN. `decoded` can
trail `reports` by the time the daemon ran before the client subscribed. That is
not loss (see `docs/PI_BRIDGE.md`).

### Bench gates and discovery

`wifi_probe.py` prints the `hello` Wi-Fi and uplink objects, and can send a UDP
discovery probe before connecting:

```bash
python3 tools/wifi_probe.py --host 192.168.1.10 --discover
```

`--json-out PATH` writes the final summary as JSON. `--assert-gates` checks the
Sitting P pass criteria and exits non-zero when any fails: echo RTT p50 below
15 ms, tick-based missing-report share at or below 3 % per hand, and zero
sequence gaps. A run without RTT samples fails the RTT gate, and every hand the
bridge expects must have at least one record, so an idle run cannot pass.

```bash
python3 tools/wifi_probe.py --host 192.168.1.10 --duration 600 \
    --assert-gates --json-out sitting-p.json
```

To prove the installable package end to end on the desk (no Pi, no controller,
no root):

```bash
pi/tools/dry_run.sh
```

## Installable packages

```bash
pi/build_package.sh                          # dist/wmr-bridge-<version>.tar.gz + checksum
pi/build_deb.sh                              # dist/wmr-bridge_<version>_all.deb + checksum
pi/tools/verify_package.sh dist/wmr-bridge-<version>.tar.gz
```

On a Raspberry Pi OS 64-bit host (Bookworm or Trixie), install either the `.deb`
or the tarball:

```bash
sudo apt install ./wmr-bridge_<version>_all.deb   # enables and starts the unit
# or: extract the tarball and run sudo wmr-bridge-<version>/pi/install.sh
```

Both install methods put `/usr/bin/wmr-bridge` on `PATH`. It runs
`python3 -m wmr_bridge` from `/opt/wmr-bridge`. So
`wmr-bridge --version`, `wmr-bridge --list` and
`wmr-bridge --replay …` work without a `cd` or `PYTHONPATH`.

The `.deb` is `Architecture: all`. It keeps `wmr-bridge.env` and the generated
token across upgrades. It creates the `wmr` user and `/etc/wmr-bridge/token` in
`postinst`. Removing the package stops and disables the unit but keeps the
token. `apt purge wmr-bridge` removes it. `pi/tools/dry_run.sh` installs,
reinstalls, replays and removes the `.deb` in a staged rootfs with no root.

When `pi/install.sh` is piped (`curl … | sudo bash`), it fetches the matching
`wmr-bridge_<version>_all.deb`, verifies it against the release `SHA256SUMS`,
installs it with `apt-get` and enables/starts `wmr-bridge.service`. The release
attaches no tarball, so piped mode needs `apt-get`; without it, install from a
checkout or from the tarball built locally with `pi/build_package.sh`. Set
`WMR_BRIDGE_VERSION` to pin a version or
`WMR_BRIDGE_RELEASE_BASE_URL` to use a mirror. See
[`docs/PI_BRIDGE.md`](../docs/PI_BRIDGE.md#installation).

## Live bridge

```bash
sudo apt install bluez
sudo modprobe hidp
sudo systemctl enable --now bluetooth

# The bridge pairs controllers on its own. Put one controller in pairing mode:
# turn it on with the front Windows button, then hold the small pairing button
# inside the battery compartment until the LEDs pulse slowly.

sudo usermod -aG input "$USER"   # or run the bridge as a user in group input
wmr-bridge --list                # confirm both controllers are visible
wmr-bridge status                # adapter, bond and hidraw state when pairing fails

# Pi on the LAN, phone connecting to it: a token is required.
wmr-bridge --bind 0.0.0.0 --token-file /etc/wmr-bridge/token
```

Then point the companion app's Wi-Fi transport (or `tools/wifi_probe.py`) at
`ws://<pi-address>:8765/wmr`. Requests carrying a browser `Origin` are rejected
unless that origin is allow-listed with `--allow-origin`. The companion app and
the probe do not send one.

To read `/etc/wmr-bridge/token`, run `sudo python3 tools/wifi_probe.py --token-file /etc/wmr-bridge/token`, or
run the probe as a user in the `wmr` group with `--token-file /etc/wmr-bridge/token`. The token file is mode 0640
and owned by `wmr:wmr`.

## Wi-Fi uplink preference

The service runs `wmr-bridge --prefer-5ghz` before the daemon starts. The command clones the active NetworkManager connection into `<connection>-5ghz`. It restricts the clone to 5 GHz. It sets the clone's autoconnect priority to 50. The base profile stays as the 2.4 GHz fallback. The command switches only when a 5 GHz BSSID is visible. A failed switch leaves the current connection in place. Pass `--no-reconnect` to save the profile without switching.

`prefer_5ghz()` also lowers the route metric of the wired profile, so an Ethernet uplink wins over 5 GHz Wi-Fi and 5 GHz Wi-Fi wins over 2.4 GHz Wi-Fi. It uses route metrics (IPv4 and IPv6), not only autoconnect priority.

A 2.4 GHz-only radio, such as the CYW43439 in a Pi Zero 2 W, is a warning-only no-op. The command reports that the adapter has no 5 GHz radio.

The bridge picks the active uplink in this order: `eth0`, then `wlan0` on 5 GHz, then `wlan0` on 2.4 GHz. `hello` and `status` report the active uplink in a separate `uplink` object with `iface`, `kind`, and `addresses`.

The daemon includes a `"wifi"` object in `hello` and `status`. The object has `iface`, `connected`, `freqMhz`, `band`, `supports5Ghz`, `contentionWarning`, and `warning`. `contentionWarning` is true when the Pi is connected on 2.4 GHz. `wmr-bridge status` prints the band, 5 GHz support, and any warning.

## BLE advertising

The daemon advertises the FF10 GATT service over BlueZ by default. Advertising is always on. Each client controls its own notifications through the CCCD (Client Characteristic Configuration Descriptor). A client with notifications enabled receives FF11, FF12, and FF14. A client with notifications off receives nothing. The bridge does not gate notifications per client.

Slow advertising uses LEAdvertisement1 intervals of about 1000 to 1285 ms. You can also set this with `btmgmt`.

Pass `--no-ble` to disable the BLE peripheral. `--ble-fallback` stays as a deprecated no-op alias and prints a deprecation warning. The peripheral needs `python3-dbus-next` and a free hci adapter. It has not been verified on hardware.

## Files

| Path | Purpose |
| --- | --- |
| `wmr_bridge/protocol.py` | FF11 fragments, FF12 records, FF14 batches (C-compatible) |
| `wmr_bridge/decode.py` | Bit-exact Python port of `src/wmr_decode.c` and `wmr_decoded.c` |
| `wmr_bridge/bluez.py` | hidraw discovery and report normalisation |
| `wmr_bridge/bridge.py` | sequence numbers, stream fan-out, JSON control protocol, bridge identity |
| `wmr_bridge/discovery.py` | UDP `WMR_DISCOVER_V1` responder |
| `wmr_bridge/ws.py` | dependency-free RFC 6455 server/client |
| `wmr_bridge/inputs.py` | live hidraw manager and capture replay |
| `wmr_bridge/server.py` | asyncio server wrapper |
| `wmr_bridge/__main__.py` | `python3 -m wmr_bridge` CLI |
| `tools/wifi_probe.py` | bench client: throughput, gaps, tick-based missing reports, RTT, status, UDP discovery, JSON output, Sitting P gates |
| `build_package.sh` | builds `dist/wmr-bridge-<version>.tar.gz` and its checksum |
| `build_deb.sh` | builds `dist/wmr-bridge_<version>_all.deb` and its checksum (Architecture: all, no root) |
| `debian/` | Debian control, conffiles and `postinst`/`prerm`/`postrm` templates |
| `tools/verify_package.sh` | checks package layout, version and checksum |
| `tools/dry_run.sh` | .deb install/reinstall/remove + replay dry run, no hardware |
| `install.sh` | unattended installer; also works piped by fetching and verifying the release `.deb` |
| `wmr-bridge.in` | `/usr/bin/wmr-bridge` launcher template installed by the `.deb` and `install.sh` |
| `systemd/wmr-bridge.service` | unit file for an unattended bridge (`WMR_BRIDGE_ARGS` via `/etc/wmr-bridge/wmr-bridge.env`) |
| `udev/99-wmr-bridge.rules` | udev rule granting `input` group access to `/dev/hidraw*` |
| `networkmanager/99-wmr-bridge-powersave.conf` | persistent Wi-Fi power-save off (NetworkManager drop-in) |
| `tests/` | host tests; no hardware required |

## Version

Current package version: see `wmr_bridge.__version__`.

## Logs

The daemon logs hand and client events:

- Hand connect/disconnect: `LEFT connected: /dev/hidraw0 (AA:BB:...)` and
  `LEFT disconnected: /dev/hidraw0 after 123 report(s)`.
- Client connect/disconnect: `client connected: <peer>` / `client disconnected: <peer>`.
- Subscribe/unsubscribe: `client <peer> subscribed to batched` / `client <peer> unsubscribed`.
- No controller: a one-time warning `no WMR controllers found; put one in pairing mode and keep it near the Pi, then check ...`.
- Status monitor line: `left=up/1234(1200) right=down/0(0)` (`up`/`down` is connection state, then reports(delivered)).

## Status

Host-tested only. It has not been run on a Raspberry Pi or with real
controllers yet. The repository's existing hardware caveats still apply. The
Wi-Fi link has its own measurement plan in `docs/PI_BRIDGE.md`.
