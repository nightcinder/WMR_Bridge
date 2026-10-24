# Linux / Raspberry Pi host bridge over Wi-Fi

**Status: the second supported first-class target (ESP32-over-BLE is the default), host-tested
in this repository but not yet run on a Raspberry Pi or with real controllers.**
Everything below is the design and the bench plan, not a hardware result; the
bring-up run sheet for the first sitting is
[`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md).

This document specifies a second bridge implementation for any Linux machine
with **Bluetooth Classic** and a **5 GHz Wi-Fi** adapter. A Raspberry Pi 4 or 5
is the reference target. It replaces the ESP32 and the BLE GATT link with the
Pi and a WebSocket over Wi-Fi, while keeping the controller-facing Bluetooth
Classic protocol and the existing FF11/FF12/FF14 payloads unchanged.

```
WMR LEFT  --\   Classic Bluetooth HID            5 GHz Wi-Fi
              >----------------------> Raspberry Pi ----------------> phone / PC
WMR RIGHT --/   46-byte frames          BlueZ + wmr_bridge   WebSocket (FF11/12/14)
```

## Why a Pi, and why 5 GHz

The open problem on the ESP32 is upstream report loss between the controllers
and the bridge, worst when both controllers and the phone's BLE link share the
same 2.4 GHz radio ([STATUS.md](STATUS.md), [DECODING.md](DECODING.md)). Moving
the phone link to Wi-Fi removes BLE airtime from the bridge entirely. Using the
**5 GHz** band also keeps the phone link away from Bluetooth Classic, which
lives at 2.4 GHz, and away from the Pi's own 2.4 GHz Wi-Fi/BT coexistence
machinery.

| Target | Suitable | Notes |
| --- | --- | --- |
| Raspberry Pi 5 | Yes | BT 5.0 Classic + dual-band Wi-Fi; reference target |
| Raspberry Pi 4 Model B | Yes | BT 5.0 Classic + dual-band Wi-Fi |
| Raspberry Pi 3 Model B+ | Expected | dual-band Wi-Fi; less CPU headroom |
| Raspberry Pi Zero 2 W | **No** | 2.4 GHz Wi-Fi only, shares the band with BT |
| Generic x86/ARM Linux | Expected | needs BlueZ HID host + a 5 GHz adapter |
| Pi with a USB BT dongle | Expected | kernel must expose HID input as `/dev/hidraw*` |

This does **not** fix Classic Bluetooth loss by itself: the controller-to-bridge
link is still Bluetooth. What it removes is the BLE notification airtime and the
phone's participation in the 2.4 GHz contention. Whether the Pi's Classic radio
does better than the ESP32's is an open measurement question (see the test plan).

## What changes, and what does not

| Concern | ESP32 firmware | Linux/Pi host bridge |
| --- | --- | --- |
| Classic BT host | BTstack | BlueZ `bluetoothd` input plugin + kernel `hidp` |
| Controller discovery | inquiry + names + NVS addresses | BlueZ pairing/trust; names from `hidraw` |
| Reports | BTstack HID callback | `/dev/hidrawN` reads |
| Phone link | BLE GATT FF10 | WebSocket on Wi-Fi |
| Raw stream | FF11 fragments | same 12-byte fragment header, same reassembler |
| Decoded stream | FF12 56-byte records | same records, same fields |
| Batched stream | FF14 version 2 | same 2-byte header + records |
| Sequence numbers | per hand, every report | per hand, every report; reset only on daemon restart |
| `rx_time_us` | ESP32 `esp_timer` since boot | Linux monotonic clock (since boot, not daemon start); diagnostics only |
| Calibration FF13 | read from controllers | **not implemented** (future work) |
| Echo FF15 | GATT characteristic | JSON `ping`/`pong` control messages |
| Firmware version FF16 | GATT characteristic | `version` in `hello` |
| BLE MTU | 23..517 | no MTU; one raw fragment by default |
| Security | no BLE pairing/encryption | token required beyond loopback; still plaintext unless tunnelled |

The companion app can reuse its existing FF11 reassembler, FF12 record parser
and FF14 batch parser. Only the transport changes.

## Installation

### Host prerequisites

Raspberry Pi OS Bookworm (64-bit) is the reference. You need:

- BlueZ with the `input` plugin enabled (the Raspberry Pi OS default);
- the kernel `hidp` module;
- permission to read `/dev/hidraw*` (root, or a user in the `input` group).

```bash
sudo apt update
sudo apt install bluez
sudo modprobe hidp
echo hidp | sudo tee /etc/modules-load.d/wmr-bridge.conf
sudo systemctl enable --now bluetooth
```

Confirm that BlueZ is running with the input plugin:

```bash
systemctl status bluetooth
bluetoothctl show
```

The bridge deliberately does **not** open L2CAP PSM 0x11/0x13 itself. The kernel
HIDP driver already listens there; a user-space listener would collide with it.
BlueZ owns the HID host channels and exposes each controller as a hidraw node.

### Pair the controllers

Put one controller in pairing mode (hold the Windows button until the LEDs
flash), then:

```bash
bluetoothctl
[bluetooth]# scan on
# wait for "Motion controller - Left" / "- Right"
[bluetooth]# pair AA:BB:CC:DD:EE:FF
[bluetooth]# trust AA:BB:CC:DD:EE:FF
[bluetooth]# connect AA:BB:CC:DD:EE:FF
[bluetooth]# scan off
```

Repeat for the second controller. The controllers normally reconnect to a
trusted host on their own after waking. Check the nodes:

```bash
ls -l /dev/hidraw*
cat /sys/class/hidraw/hidraw*/device/uevent | grep -E 'HID_NAME|HID_UNIQ'
```

A WMR input report appears on hidraw as report ID `0x01` followed by 44 payload
bytes. The bridge accepts that 45-byte form, the firmware's 46-byte
`A1 01 ...` form, and a bare 44-byte payload; other report IDs are ignored.
Diagnose with `sudo evtest` or `sudo hexdump -C /dev/hidrawN`.

If the nodes are not readable by your user:

```bash
sudo usermod -aG input "$USER"     # log out and back in
```

or install the shipped udev rule:

```bash
sudo cp pi/udev/99-wmr-bridge.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Run from the checkout

```bash
cd pi
python3 -m wmr_bridge --list

# Loopback only (the default): useful for a local client on the same machine.
python3 -m wmr_bridge

# Pi on the LAN, phone connecting to it: a token is required.
printf '%s\n' "$(head -c 24 /dev/urandom | base64)" > token
chmod 600 token
python3 -m wmr_bridge --bind 0.0.0.0 --token-file token
```

No third-party packages are required: the WebSocket implementation, protocol
codecs, decoder and tests use only the Python standard library (3.10+).

`--bind` defaults to `127.0.0.1`. If you bind to any non-loopback address
without `--token` or `--token-file`, the daemon exits before opening a socket
instead of silently serving controller telemetry to the LAN. `--insecure-no-token`
is the explicit, typed escape hatch and is unsafe on any shared network.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--bind`, `--port` | WebSocket listen address (default `127.0.0.1:8765`); a non-loopback address requires a token |
| `--token`, `--token-file` | require a shared token from clients (the file form is preferred) |
| `--allow-origin ORIGIN` | allow this browser `Origin` (repeatable); other `Origin` values are rejected |
| `--insecure-no-token` | allow a non-loopback bind without a token (**unsafe**) |
| `--hidraw PATH` | open a specific node instead of auto-discovery |
| `--hand {both,left,right}` | only open the selected controller hand (default `both`; for one-Pi-per-hand setups) |
| `--left MATCH`, `--right MATCH` | assign by MAC, path or name part |
| `--all-hidraw` | consider every HID device (then use `--left/--right`) |
| `--batch-max`, `--batch-hold-ms` | FF14 batching (default 9 records, 5 ms) |
| `--queue-size` | per-client outbound queue (default 512) |
| `--replay FILE` | replay `samples/controller_frames.txt` without hardware |
| `--duration SECONDS` | stop after a fixed time (bench convenience) |
| `--list` | print hidraw nodes and exit |

### Unattended service

```bash
sudo useradd --system --create-home --groups input wmr
sudo mkdir -p /opt/wmr-bridge /etc/wmr-bridge
sudo cp -r pi /opt/wmr-bridge/
sudo install -m 600 /dev/null /etc/wmr-bridge/token
sudo sh -c 'head -c 24 /dev/urandom | base64 > /etc/wmr-bridge/token'
sudo chown wmr:wmr /etc/wmr-bridge/token
sudo chmod 600 /etc/wmr-bridge/token
sudo cp /opt/wmr-bridge/pi/systemd/wmr-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wmr-bridge
journalctl -u wmr-bridge -f
```

The unit runs as `wmr` with `SupplementaryGroups=input`, restarts on failure and
reads the token from `/etc/wmr-bridge/token`. The token file must be owned by
`wmr` (mode 0600): a root-owned file is unreadable by the service, which then
exits with a message naming the path and the uid it runs as. It does not enable
itself until you do.

### Wi-Fi and firewall

- Use a 5 GHz SSID and verify with `iw dev wlan0 link` (`freq` above 5000).
- Disable Wi-Fi power saving for lower jitter, for example
  `nmcli connection modify "<name>" wifi.powersave 2` and reconnect, or
  `sudo iw dev wlan0 set power_save off` for the current boot.
- Open only the bridge port: `sudo ufw allow 8765/tcp`.
- Do not expose the port to the internet. See [Security](#security).

## Wire protocol, version 1

### Endpoint and handshake

```
ws://<bridge>:8765/wmr
Sec-WebSocket-Protocol: wmr-wifi-v1
```

The server accepts the connection, selects `wmr-wifi-v1` when offered, and
immediately sends one JSON `hello`. The HTTP upgrade must complete within
**5 seconds**, otherwise the server sends `408 Request Timeout` and closes, so a
half-open TCP connection cannot hold a slot.

Requests that carry an `Origin` header (browsers always do; the probe and the
companion app do not) are rejected with `403 Forbidden` unless that exact origin
was allowed at startup with `--allow-origin <value>` (repeatable). This stops a
web page on the LAN from opening a socket to the daemon. Origin matching is
case-insensitive on the normalised value; `*` is not a wildcard.

```json
{
  "type": "hello",
  "protocol": 1,
  "transport": "wifi",
  "device": "wmr-pi",
  "version": "0.1.0",
  "hostname": "wmr-pi",
  "authRequired": true,
  "streams": ["raw", "decoded", "batched"],
  "recordSize": 56,
  "batchMax": 9,
  "fragmentCapacity": 244
}
```

Text messages are JSON control messages. Binary messages from the server are
stream payloads. Binary messages from the client are rejected with
`{"type":"error","code":"binary_not_accepted"}`.

### Authentication

If `authRequired` is true, the client must send, within **5 seconds**:

```json
{"type": "auth", "token": "<shared token>"}
```

Success is `{"type":"auth_ok"}`. Failure sends
`{"type":"error","code":"auth_failed"}` and closes with WebSocket close code
`4401`. No control message other than `auth` is processed before authentication.

The token is a shared secret, not a user account. It is compared in constant
time. A token is **required whenever the daemon listens on a non-loopback
address**: `--bind` defaults to `127.0.0.1`, and binding to a LAN address
without `--token`/`--token-file` exits with an error instead of starting. This
is the path the owner runs (Pi on the LAN, phone connecting to it), so configure
`--token-file /etc/wmr-bridge/token`. `--insecure-no-token` overrides the check
for deliberate lab use only; it is unsafe because every host that can reach the
port can subscribe. With no token on loopback, only local clients can connect.

### Control messages

| Client sends | Server replies |
| --- | --- |
| `{"type":"subscribe","stream":"raw"\|"decoded"\|"batched"}` | `{"type":"subscribed","stream":...,"recordSize":56,"batchMax":9,"fragmentCapacity":244}` |
| `{"type":"unsubscribe"}` | `{"type":"unsubscribed"}` |
| `{"type":"status"}` | a `status` object (below) |
| `{"type":"ping","id":N,"t":ms}` | `{"type":"pong","id":N,"t":ms,"serverTimeUs":...}` |
| `{"type":"hello"}` | another `hello` |
| `{"type":"auth","token":"..."}` | `auth_ok` or `auth_failed` |
| `{"type":"close"}` | close frame, then disconnect |

Rules:

- **One stream per connection.** Subscribing to a different stream while one is
  active returns `{"type":"error","code":"stream_active"}`. Unsubscribe first.
  This mirrors the FF11/FF12/FF14 mutual exclusion in the firmware, so a client
  cannot believe it is receiving two streams when only one is active.
  Re-subscribing to the same stream is idempotent.
- An unknown stream is `bad_stream`; malformed JSON is `bad_json`; an unknown
  `type` is `unknown_type`.
- Multiple clients are allowed (default: no explicit cap). Each has its own
  queue and independent subscription. Frames are fanned out to every subscriber.
- Slow clients do not block the ingest path. When a client's 512-message queue is
  full, the **newest** message is dropped and counted in `dropped`; this mirrors
  the firmware's bounded queue policy.

### Binary payloads

Every binary message is exactly the BLE notification value of the subscribed
stream. All integers are little-endian.

**`raw` — one FF11 fragment.** The 12-byte header is:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | u8 | magic `0x52` |
| 1 | u8 | hand, 0 LEFT / 1 RIGHT |
| 2 | u32 | per-hand sequence |
| 6 | u16 | total frame length (46 for an input report) |
| 8 | u16 | offset of this chunk |
| 10 | u16 | chunk length |
| 12 | `chunk_len` | frame bytes |

The default fragment capacity is 244 bytes including the header, so a 46-byte
frame is one message of 58 bytes. The companion's existing FF11 reassembler
handles this unchanged. See [PROTOCOL.md](PROTOCOL.md) for the receiver rules.

**`decoded` — one FF12 version 1 record.** Exactly 56 bytes:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | u8 | version `1` |
| 1 | u8 | hand |
| 2 | u8 | flags (bit0 CALIBRATED; always 0 here) |
| 3 | u8 | buttons (same bit positions as the controller report) |
| 4 | u32 | sequence (same counter as raw) |
| 8 | u64 | `rx_time_us` (daemon monotonic microseconds) |
| 16 | u32 | `device_ticks` (controller clock, 100 ns/tick) |
| 20 | 3 x f32 | gyro, rad/s, uncalibrated |
| 32 | 3 x f32 | accel, m/s^2, uncalibrated |
| 44 | i16 | temperature raw |
| 46 | 2 x i16 | stick x, y (Q15) |
| 50 | 2 x i16 | trackpad x, y (Q15) |
| 54 | u8 | trigger |
| 55 | u8 | battery raw |

The decoder is a Python port of `src/wmr_decode.c` and `src/wmr_decoded.c`;
`pi/tests/test_protocol.py` pins two sample frames to bytes produced by the C
implementation, so the two decoders agree bit for bit.

**`batched` — one FF14 version 2 batch.** Two-byte header (`version = 2`,
`count = 1..9`) followed by `count` complete FF12 records, oldest first. A
partial batch is flushed after `--batch-hold-ms` (default 5 ms) or when full.
Records of both hands may share a batch; each carries its own hand.

### Status

```json
{
  "type": "status",
  "uptimeS": 42.5,
  "hostTimeUs": 123456789,
  "hands": [
    {
      "hand": "LEFT",
      "connected": true,
      "address": "AA:BB:CC:DD:EE:FF",
      "reports": 7312,
      "decoded": 7300,
      "errors": 0,
      "delivered": 7300,
      "dropped": 0,
      "sequence": 7312,
      "lastRxAgeMs": 6.2
    },
    {
      "hand": "RIGHT",
      "connected": false,
      "address": "",
      "reports": 0,
      "decoded": 0,
      "errors": 0,
      "delivered": 0,
      "dropped": 0,
      "sequence": 0,
      "lastRxAgeMs": null
    }
  ],
  "clients": 1,
  "subscribed": 1,
  "dropped": 0,
  "batches": 811
}
```

`reports` counts reports received from the controller; `decoded` counts reports
successfully decoded while a decoded/batched client was subscribed; `delivered`
counts messages enqueued for a client (one per fragment on `raw`, one per
record on `decoded`, and one per record inside an enqueued `batched` message);
`errors` counts malformed frames or non-input report IDs; `dropped` counts
messages dropped because a client queue was full. The per-hand `dropped` counts
the same drops attributed to that hand (one per dropped fragment on `raw`, one
per dropped record on `decoded`, and one per record inside a dropped `batched`
message).

### Sequence and timing semantics

- Sequence numbers start at **1** for each hand after daemon start and increment
  for **every** input report, whether or not anyone is subscribed and whether
  or not decoding succeeds. They wrap as `uint32`.
- Reconnecting a controller does **not** reset the counter. Restarting the
  daemon does.
- A gap therefore means a report was lost before the bridge queued it (upstream
  loss), or a decoded record was discarded. It is not evidence of downstream
  loss; use `dropped` for that.
- `device_ticks` is the controller's own 100 ns clock and remains the primary
  sensor timestep. `rx_time_us` is a host clock for latency/jitter only; it is
  Linux's monotonic clock, which counts from boot, not from daemon start. Its
  origin differs from the ESP32's `esp_timer`, so do not compare the raw values
  across bridges.
- The JSON `ping`/`pong` pair is the FF15 analogue. `t` is echoed unchanged and
  `serverTimeUs` is the host clock at reply; RTT is measured by the client.

## Companion app integration

The intended companion change is small:

1. Add a Wi-Fi transport that connects to `ws://<host>:8765/wmr`, performs the
   handshake/auth, and subscribes to `batched` (or `decoded` for debug).
2. Feed each binary message into the existing FF11 reassembler / FF12 parser /
   FF14 batch parser, based on the stream.
3. Map the JSON `status` into the existing link-status model and the JSON
   `ping`/`pong` into the latency screen (or keep FF15 semantics for BLE).
4. Reconnect with backoff; re-subscribe after a reconnect. Sequence numbers
   continue across a network reconnect, so the existing gap detection still
   works. A bridge daemon restart resets them, which looks like the ESP32
   rebooting.
5. Treat the bridge as one source per Pi. Multi-bridge setups (one Pi per hand)
   work the same way as multiple ESP32 bridges: connect one WebSocket per Pi,
   each carrying its own LEFT or RIGHT hand.

FF13 (calibration blobs) and FF16 (firmware version) have no WebSocket
equivalent yet. The `hello.version` identifies the bridge software; calibration
is a documented follow-up rather than silently pretending to support it.

## Security

This is a **bench bridge**, not an internet-facing service. The mechanical
safeguards below are enforced by the daemon; the transport is still plaintext.

- **Loopback by default, token required on the LAN.** `--bind` defaults to
  `127.0.0.1`. Serving the Pi's LAN address requires both an explicit
  `--bind 0.0.0.0` (or the Pi's address) **and** `--token`/`--token-file`; the
  daemon refuses to start otherwise. The token is what protects the owner's
  actual setup (Pi on the LAN, phone connecting to it). `--insecure-no-token`
  exists only as a typed escape hatch for an isolated lab link; never use it on
  a shared or untrusted network.
- **Browser origins are rejected by default.** A request with an `Origin`
  header is refused unless that exact origin was passed with `--allow-origin`.
  The probe and the companion app do not send `Origin`; browsers always do, so
  a web page cannot open a socket to the daemon.
- **Bounded handshake.** An HTTP upgrade that does not complete within 5 seconds
  is closed, so half-open connections cannot accumulate.
- The WebSocket is plaintext. The token prevents unauthorised clients, but it
  does not encrypt controller data and does not resist an on-path attacker. Use
  WPA2/WPA3, a dedicated/trusted network, or an SSH tunnel until TLS is
  implemented.
- Never port-forward 8765 from the internet. Bind only the interface you
  intend, and keep the firewall closed except for the LAN clients you expect.
- Store the token in a mode-600 file **owned by the service user** (`wmr`) and
  prefer `--token-file` over `--token` (the latter is visible in the process
  list). The daemon warns if the token file is group/world-readable, and its
  read error names the path and the uid it is running as.
- On the Bluetooth side, the Pi trusts the controllers you pair in BlueZ. The
  bridge assigns LEFT/RIGHT by HID name (or by `--left/--right`), which is not
  authentication; a spoofed device that BlueZ pairs could claim a hand. Bond the
  controllers deliberately and keep the Pi's Bluetooth adapter trusted-only.
- Controller motion data can be personal data. Captures and logs should be
  handled accordingly. The repository samples contain no Bluetooth address.
- There is no rate limiting, no per-client authorisation beyond the token, and
  no TLS yet. Treat it as bench equipment with a small, local attack surface.

## Performance expectations and limits

- The Pi link removes BLE notification airtime from the phone path. It cannot
  remove Classic Bluetooth loss between controllers and the Pi.
- One Python process handles both hands. Encoding one 46-byte frame is cheap;
  the event loop should keep up with 2 x ~180 reports/s on a Pi 4. Batching
  reduces WebSocket writes but adds at most the hold time (5 ms default).
- The default 512-message queue absorbs scheduling hiccups for roughly 1.5 s at
  320 records/s per client. A slower client drops the newest messages; it does
  not stall the controllers.
- Wi-Fi power saving, channel congestion and roaming cause latency spikes.
  Measure RTT/jitter with `tools/wifi_probe.py` before drawing conclusions.
- The bridge has no hardware timestamp from the Wi-Fi stack; `rx_time_us` is
  taken when the daemon reads the report. Do not interpret it as an arrival
  timestamp at the adapter.

## Test plan

### Host tests (already runnable)

```bash
cd pi
./run_tests.sh
```

Covers: FF11 golden fragments and every capacity 13..244 for both hands, FF12
golden record and C-parity sample records, FF14 batches, reassembler rules,
hidraw discovery/normalisation, WebSocket handshake/fragmentation/ping/close,
the JSON control protocol, authentication, stream exclusion, batching and
replay. It also covers the non-loopback-without-token refusal, the loopback
default, Origin rejection and allow-listing, the handshake timeout, the
`auth_failed` path for a non-ASCII token, per-hand drop accounting and closing
active sockets on shutdown. No hardware or third-party packages.

Replay smoke test:

```bash
python3 -m wmr_bridge --replay ../samples/controller_frames.txt --bind 127.0.0.1 --port 8765
python3 tools/wifi_probe.py --stream batched --duration 10
```

`decoded` can legitimately trail `reports` by however long the daemon ran before
the client subscribed: reports are only decoded while a decoded/batched
subscriber is attached ([`pi/wmr_bridge/bridge.py`](../pi/wmr_bridge/bridge.py)
`ingest`). That difference is not loss; use sequence gaps and `dropped` to judge
loss.

### Hardware checklist (not yet run)

| ID | Check | Pass criterion |
| --- | --- | --- |
| P1 | `--list` after pairing both controllers | two nodes, names map to LEFT/RIGHT, addresses present |
| P2 | Replay probe | ~160 rec/s, no gaps, no drops |
| P3 | Live one controller, `decoded` | buttons, stick, trigger and IMU change sensibly; sequence rises ~150-180/s; `errors` 0 after 60 s |
| P4 | Live both controllers | both hands stream concurrently; per-hand sequences independent; no decode errors |
| P5 | 5 GHz link, 10 min | `iw ... link` above 5000 MHz; `dropped` 0; probe RTT p50/p95 recorded; compare with 2.4 GHz in the same sitting |
| P6 | Upstream loss | record tick-based missing-report share per hand (as in the firmware runs) and compare with the ESP32 baseline; do not claim improvement from sequence gaps alone |
| P7 | Controller reconnect | power-cycle one controller; it reappears, the client stays subscribed, sequences continue (no reset) |
| P8 | Network reconnect | drop/rejoin Wi-Fi; client reconnects and re-subscribes; token still enforced |
| P9 | Two clients | raw and batched clients both receive, independent queues |
| P10 | 8 h soak | no memory growth, no fd leak, no queue drops at idle |

Record host model, OS, BlueZ/kernel versions, Wi-Fi band/channel, controller
addresses (or omit them), and the probe output in the bring-up run sheet
[`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md). Until P1-P10 have
been run, treat the Pi path as unverified.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `no /dev/hidraw nodes` | `systemctl status bluetooth`, `lsmod \| grep hidp`, controller paired/trusted/connected |
| Node exists but `--list` shows name `-` | custom controller name; use `--left/--right` with the MAC or path |
| Permission denied opening hidraw | run as root or add the user to `input`; check udev mode |
| Controllers connect to the old ESP32 | power it off or unpair/forget the controller's previous host; pair to the Pi |
| Client gets `auth_failed` | token mismatch; check `/etc/wmr-bridge/token` and the client flag |
| Client gets `stream_active` | send `unsubscribe` before switching streams |
| High RTT or drops | confirm 5 GHz, disable Wi-Fi power save, check channel congestion, inspect `status.dropped` |
| Port unreachable | `--bind 0.0.0.0` (plus a token, or the daemon refuses to start), firewall, correct Pi address, same VLAN/client isolation off |

## Future work

- Hardware validation of P1-P10 using the bring-up run sheet [`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md).
- Companion app Wi-Fi transport (`wmr_ws` and connect UI in `wmr-companion`).
- TLS or a pinned-key handshake for non-LAN use; mDNS advertisement.
- FF13 calibration-blob forwarding and an FF16-equivalent endpoint.
- Native packaging (`pip install`, `.deb`) and udev rules shipped as files.
- Optional single-hand mode (one Pi per hand) with an explicit hand selector.

## Files

| Path | Contents |
| --- | --- |
| `pi/wmr_bridge/` | host bridge package: protocol, decoder, BlueZ, WebSocket, server, CLI |
| `pi/tools/wifi_probe.py` | bench client: throughput, sequence gaps, tick-based missing reports, RTT (min/p50/p95/max), status |
| `pi/systemd/` | service unit and environment example |
| `pi/udev/` | udev rule (`99-wmr-bridge.rules`) granting `input` group access to `/dev/hidraw*` |
| `pi/tests/` | 102 host tests (no hardware) |
