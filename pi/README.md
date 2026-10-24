# WMR Bridge on Linux / Raspberry Pi (Wi-Fi transport)

This directory is the **second supported first-class target** (ESP32-over-BLE is
the default): a host-side replacement for the ESP32 for any Linux machine with
Bluetooth Classic and a 5 GHz Wi-Fi adapter. A Raspberry Pi 4 or 5 is the
reference target (dual-band Wi-Fi and BT 5.0); a Pi Zero 2 W is **not** suitable
because its Wi-Fi is 2.4 GHz only, which shares the radio band with Bluetooth.

The host bridge:

- uses BlueZ as the Classic Bluetooth HID **host** (controllers pair with the Pi
  instead of the ESP32);
- decodes reports and serves the **same FF11 raw, FF12 decoded and FF14 batched
  payloads** as the firmware;
- carries them over a WebSocket on Wi-Fi instead of BLE GATT.

The full design, wire protocol, security notes, setup steps and hardware test
plan are in [`docs/PI_BRIDGE.md`](../docs/PI_BRIDGE.md), and the bring-up run
sheet is [`docs/runs/sheets/pi-bridge.md`](../docs/runs/sheets/pi-bridge.md).

## Quick start (replay, no controllers)

```bash
cd pi
python3 -m unittest discover -s tests -v          # 113 host tests
python3 -m wmr_bridge --replay ../samples/controller_frames.txt --bind 127.0.0.1

# in a second terminal
python3 tools/wifi_probe.py --stream batched --duration 10
```

`--bind` defaults to `127.0.0.1`, so the quick start needs no token and is not
reachable from the LAN. `decoded` can trail `reports` by the time the daemon ran
before the client subscribed; that is not loss (see `docs/PI_BRIDGE.md`).

The replay path is a complete smoke test of the WebSocket transport, the
decoder and the three stream formats.

## Live bridge

```bash
sudo apt install bluez
sudo modprobe hidp
sudo systemctl enable --now bluetooth

# Put one controller in pairing mode, then:
bluetoothctl --timeout 30 scan on
bluetoothctl trust "AA:BB:CC:DD:EE:FF"
bluetoothctl connect "AA:BB:CC:DD:EE:FF"

sudo usermod -aG input "$USER"   # or run the bridge as a user in group input
cd pi
python3 -m wmr_bridge --list     # confirm both controllers are visible

# Pi on the LAN, phone connecting to it: a token is required.
python3 -m wmr_bridge --bind 0.0.0.0 --token-file /etc/wmr-bridge/token
```

Then point the companion app's Wi-Fi transport (or `tools/wifi_probe.py`) at
`ws://<pi-address>:8765/wmr`. Requests carrying a browser `Origin` are rejected
unless that origin is allow-listed with `--allow-origin`; the companion app and
the probe do not send one.

## Files

| Path | Purpose |
| --- | --- |
| `wmr_bridge/protocol.py` | FF11 fragments, FF12 records, FF14 batches (C-compatible) |
| `wmr_bridge/decode.py` | Bit-exact Python port of `src/wmr_decode.c` and `wmr_decoded.c` |
| `wmr_bridge/bluez.py` | hidraw discovery and report normalisation |
| `wmr_bridge/bridge.py` | sequence numbers, stream fan-out, JSON control protocol |
| `wmr_bridge/ws.py` | dependency-free RFC 6455 server/client |
| `wmr_bridge/inputs.py` | live hidraw manager and capture replay |
| `wmr_bridge/server.py` | asyncio server wrapper |
| `wmr_bridge/__main__.py` | `python3 -m wmr_bridge` CLI |
| `tools/wifi_probe.py` | bench client: throughput, gaps, tick-based missing reports, RTT, status |
| `install.sh` | unattended installer (`/opt/wmr-bridge`, udev rule, token, systemd unit & env) |
| `systemd/wmr-bridge.service` | unit file for an unattended bridge (`WMR_BRIDGE_ARGS` via `/etc/wmr-bridge/wmr-bridge.env`) |
| `udev/99-wmr-bridge.rules` | udev rule granting `input` group access to `/dev/hidraw*` |
| `tests/` | host tests; no hardware required |

## Status

Host-tested only. It has **not** been run on a Raspberry Pi or with real
controllers yet. The repository's existing hardware caveats still apply, and
the Wi-Fi link has its own measurement plan in `docs/PI_BRIDGE.md`.
