# Run sheet: Linux / Raspberry Pi Wi-Fi host bridge bring-up (P1–P10)

> **Proposed sheet, not yet run.** Nothing here is a hardware result. Figures quoted from
> earlier runs are user-reported; the pass criteria are fixed before any data. The owner is
> buying a Raspberry Pi; this sheet defines the bring-up procedure and pass criteria for the
> first sitting so measurements are directly comparable with the ESP32 baseline runs.

Question: does moving the host bridge to a Raspberry Pi over 5 GHz Wi-Fi eliminate upstream
report loss between the WMR motion controllers and the bridge, and how do the Pi's Bluetooth
Classic radio numbers compare to the ESP32 baseline?

---

## 0. Context & What the Existing Runs Say

From `docs/runs/sheets/contention-2-results.md` (lines 14–16, 132), `docs/runs/sheets/single-controller.md`,
and `docs/runs/sheets/multi-bridge.md`:

| Setup | LEFT Loss | RIGHT Loss | RTT p50 | Transport | Source |
|---|---|---|---|---|---|
| **ESP32: LEFT alone** | **~0.5 %** (rate ~203/s) | — (`no ACL link`) | ~40.8 ms | BLE GATT (FF14) | `single-controller-left-only.txt` |
| **ESP32: RIGHT alone** | — (`no ACL link`) | **~10.8 %** (rate ~182/s) | ~40.8 ms | BLE GATT (FF14) | `single-controller-right-only.txt` |
| **ESP32: Both connected** | **~18–20 %** | **~18–26 %** | ~40.8 ms | BLE GATT (FF14) | `contention-2-results.md` |
| **Raspberry Pi: P6 sitting** | *to be measured* | *to be measured* | *to be measured* | 5 GHz Wi-Fi WebSocket | This sheet (P1–P10) |

### Honest Framing: What Wi-Fi Changes, and What It Does Not

Moving the phone link from BLE to Wi-Fi removes **BLE notification airtime** from the bridge entirely.
Using the **5 GHz** band also isolates the phone link from Bluetooth Classic (which operates at 2.4 GHz)
and bypasses the Pi's internal 2.4 GHz Wi-Fi/Bluetooth coexistence arbiter.

However, moving to Wi-Fi does **not** alter Bluetooth Classic loss: the controller-to-bridge link remains
Bluetooth Classic HID over 2.4 GHz. Whether the Raspberry Pi's Bluetooth Classic radio and BlueZ/kernel
HIDP stack achieve lower loss than the ESP32's integrated radio is the core empirical question this
sitting is designed to answer. **That is what check P6 settles.**

### Comparability Contract with ESP32 Firmware Runs

To enable direct comparison between a Raspberry Pi sitting and an ESP32 firmware run in the same
verification table:

1. **Loss Window & Settle:** Timing must use a **30-second settle** after both controllers and client
   connect, followed by a continuous **120-second (2-minute) measurement window** (matching
   `multi-bridge.md` §3.1, `right-asymmetry.md` §3, and `closeout.md`).
2. **Tick-Based Missing Reports:** P6 must report tick-based missing-report share per hand:
   $$\text{Loss \%} = \frac{\text{missingFromTicks}}{\text{records} + \text{missingFromTicks}} \times 100\%$$
   derived from controller `device_ticks` (100 ns timestamps). This corresponds directly to the
   firmware's `dev_missing_delta`. Do not evaluate link quality from sequence gaps alone.
3. **Explicit `n/a` Fields:** Where the Pi architecture does not possess an ESP32 GATT field, mark it
   explicitly as `n/a`. **Never leave it blank or zero**, which would be misinterpreted as 0 dBm or 0 ms:
   - BLE `rssi`: `n/a` (Wi-Fi transport; Wi-Fi RSSI in dBm from `iw dev wlan0 link` may be noted separately)
   - BLE `phy`: `n/a` (802.11a/n/ac/ax Wi-Fi physical layer)
   - BLE `mtu`: `n/a` (RFC 6455 WebSocket framing, no 23..517 GATT MTU negotiation)
   - BLE `interval`: `n/a` (TCP/IP over 802.11, no 15.00 ms LE connection event interval)
   - `esp_timer`: `n/a` (`rx_time_us` is Linux monotonic clock since boot, not ESP32 boot timer)

---

## 1. Prerequisites & Hardware / Software Floor

### 1.1 Which Pi to Buy

Before purchasing, consult the canonical suitability table in
[docs/PI_BRIDGE.md § Why a Pi, and why 5 GHz](../../PI_BRIDGE.md#why-a-pi-and-why-5-ghz):

- **Raspberry Pi 5** or **Raspberry Pi 4 Model B:** **Yes (Reference targets)** — BT 5.0 Classic + dual-band Wi-Fi.
- **Raspberry Pi 3 Model B+:** **Expected** — dual-band Wi-Fi; tighter CPU headroom.
- **Raspberry Pi Zero 2 W:** **Strictly NO** — 2.4 GHz Wi-Fi only. Sharing 2.4 GHz between Wi-Fi and Bluetooth
  causes severe on-chip coexistence contention, defeating the entire purpose of the Wi-Fi bridge.

### 1.2 Software Floor (Pi Host)

The host daemon requires:

- **OS:** Raspberry Pi OS Bookworm (64-bit) reference image.
- **Python:** Version `>= 3.10` (uses standard library only: `asyncio`, `socket`, `struct`, `json`, `hashlib`).
- **BlueZ:** Bluetooth daemon with the `input` plugin enabled (Raspberry Pi OS default).
- **Kernel:** `hidp` module loaded (`echo hidp | sudo tee /etc/modules-load.d/wmr-bridge.conf`).
- **Device Access:** Permission to read `/dev/hidraw*` (user in group `input`, or udev rule
  `KERNEL=="hidraw*", SUBSYSTEM=="hidraw", MODE="0660", GROUP="input"`).

### 1.3 Controllers & Bench Setup

- **Controllers:** Physical LEFT (`2C:4D:79:C8:C9:CC`) and physical RIGHT (`2C:4D:79:C8:F9:A9`).
- **Bench Marks:** Two stationary positions on the desk (Pos L and Pos R) matching `right-asymmetry.md` §1.
  Controllers face down, stationary during timing.
- **Wi-Fi AP:** Dual-band router with a dedicated 5 GHz SSID. The Pi must be associated on 5 GHz
  (`iw dev wlan0 link` showing `freq` > 5000 MHz).
- **Wi-Fi Power Save:** Power saving disabled on the Pi (`sudo iw dev wlan0 set power_save off`).

### 1.4 Client Selection

- **Option A (Preferred):** `wmr-companion` app with `wmr_ws` and the Connect screen's Wi-Fi `host:port` entry. Both are landed on `wmr-companion` `main` (#61 / M14.1, #62 / M14.3), but the app-side path remains host-tested/loopback-only until Sitting P runs on the Pi.
- **Option B (Fallback):** `pi/tools/wifi_probe.py` in this repository. It requires only Python 3.10+ (no Flutter or Android toolchain) and can run on a laptop or a phone via Termux on the same 5 GHz Wi-Fi network.

### 1.5 Security & Token Setup

As established in ONY-116:
- `--bind` defaults to `127.0.0.1`.
- Binding to any non-loopback address (such as `--bind 0.0.0.0` for LAN access) **requires** `--token` or `--token-file`. The daemon refuses to start off-loopback without a token.
- Generate a token file with mode `0600` owned by the service user (`wmr` for systemd, or current user for CLI):
  ```bash
  head -c 24 /dev/urandom | base64 > token
  chmod 600 token
  ```

### 1.6 Pre-flight kit (desk-only, already run 2026-10-31)

The package and the daemon can be validated before the Pi exists. These are
**host-side results**, not Pi results; keep them out of the P1–P10 table.

| Check | Command | Result |
|---|---|---|
| Package build + layout + sha256 | `pi/build_package.sh && pi/tools/verify_package.sh dist/wmr-bridge-0.2.3.tar.gz` | pass (`wmr-bridge-0.2.3.tar.gz`: daemon/probe/unit/udev/docs/replay present, no bytecode). The tarball's exact byte count and sha256 depend on the gzip/toolchain, so record them per build instead of comparing them here |
| Debian package | `pi/build_deb.sh && dpkg-deb -I/-c dist/wmr-bridge_0.2.3_all.deb` | pass (`Architecture: all`, app in `/opt/wmr-bridge`, unit + udev rule, `/etc/wmr-bridge` conffiles, `postinst` token) |
| Installer staging | `DESTDIR=<tmp> WMR_SKIP_PRIVILEGED=1 pi/install.sh` | pass (`/opt/wmr-bridge`, systemd unit, udev rule, power-save drop-in, token mode `0600`) |
| Replay end to end from the installed tree | `pi/tools/dry_run.sh` | pass for the `.deb` (staged install, reinstall keeps token and edited env, 480 records, 300 LEFT / 180 RIGHT, `gaps=(0,0)`, `missing=(0,0)`, `dropped=0`, `clients=1`, `errors=0`, remove/purge exit 0). Pass `dist/wmr-bridge-0.2.3.tar.gz` for the tarball path |

The image is Raspberry Pi OS Bookworm 64-bit from Raspberry Pi Imager plus this
package; there is no custom image. Re-run `pi/tools/dry_run.sh` after any change
to `pi/` before a hardware sitting. Hardware-only checks (BlueZ input plugin,
`hidp`, hidraw permissions, 5 GHz, radio loss) remain P1–P10 below.

---

## 2. Go / No-Go Gates Before Timing

Do not start any timing window until all checks in this table pass:

| Check | Go (Proceed) | No-Go (Stop and Fix Before Timing) |
|---|---|---|
| **Host BlueZ** | `systemctl status bluetooth` active, `input` plugin enabled | Service stopped, or Bluetooth blocked (`rfkill list`) |
| **Kernel `hidp`** | `lsmod \| grep hidp` confirms module loaded | Run `sudo modprobe hidp` |
| **hidraw Permissions** | `test -r /dev/hidraw0` succeeds without `sudo` | Add user to `input` group: `sudo usermod -aG input $USER` |
| **Wi-Fi Band** | `iw dev wlan0 link` reports `freq` > 5000 MHz | Associated on 2.4 GHz; switch to 5 GHz SSID |
| **Wi-Fi Power Save** | `iw dev wlan0 get power_save` reports `off` | Run `sudo iw dev wlan0 set power_save off` |
| **Token Permissions** | Token file is mode `0600`, owned by executing user | Mode world/group readable (warning) or unreadable (daemon aborts) |
| **Daemon Bind** | `--bind 0.0.0.0` with `--token-file` provided | Daemon exits with refusal error (missing token off-loopback) |
| **Origin Protection** | Probe / companion app sends no `Origin` header | Web browser origin rejected without `--allow-origin` |
| **Client Auth & Sub** | Client receives `{"type":"auth_ok"}` then `{"type":"subscribed"}` | Handshake times out (5 s limit), `auth_failed`, or `stream_active` |
| **Controller Stream** | `rate_rx` ~150–205/s per active hand; `errors = 0` | `rate_rx = 0.0/s`, controller sleeping, or decode errors |

---

## 3. Numbered Procedure (Checks P1–P10)

### P1 — Daemon Clean Start & Device Discovery

1. Install the pre-flight package (§1.6) if this is a fresh image:
   ```bash
   tar -xzf wmr-bridge-0.2.3.tar.gz
   sudo wmr-bridge-0.2.3/pi/install.sh
   ```
2. Ensure both controllers are paired and trusted in BlueZ:
   ```bash
   bluetoothctl devices | grep "Motion controller"
   ```
3. Run discovery from the installed tree (or the checkout):
   ```bash
   cd /opt/wmr-bridge/pi
   python3 -m wmr_bridge --list
   ```
4. **Pass Criterion:** Exactly two hidraw nodes are listed, one matching `LEFT` and one `RIGHT`, with valid Bluetooth MAC addresses and permissions readable. Exit code is 0.

### P2 — Replay Mode End-to-End Smoke Test

The same check already runs on the desk via `pi/tools/dry_run.sh` (§1.6); on the
Pi it proves the installed tree, not just the checkout.

1. Start the daemon in replay mode on loopback:
   ```bash
   cd /opt/wmr-bridge/pi
   python3 -m wmr_bridge --replay ../samples/controller_frames.txt --bind 127.0.0.1 --port 8765
   ```
2. In a second terminal, run the bench client:
   ```bash
   python3 tools/wifi_probe.py --host 127.0.0.1 --port 8765 --stream batched --duration 10
   ```
3. **Pass Criterion:** Client receives ~160 rec/s, 0 sequence gaps (`gaps=(0,0)`), 0 dropped frames (`dropped=0`), and clean exit after 10 s. (`decoded` legitimately trails `reports` by the pre-subscription duration; this is expected).

### P3 — Live Single-Controller Decoded Stream

1. Turn on **only the LEFT controller**. Verify it connects to the Pi.
2. Start the bridge with LAN bind and token:
   ```bash
   python3 -m wmr_bridge --bind 0.0.0.0 --port 8765 --token-file token
   ```
3. From the client device, connect and stream decoded records for 60 seconds:
   ```bash
   python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream decoded --duration 60
   ```
4. Exercise buttons, thumbstick, trigger, and 90° rotations on the controller.
5. **Pass Criterion:** Sequence numbers advance ~150–180/s continuously; IMU values, buttons, stick, and trigger change plausibly; `errors = 0` after 60 seconds.

### P4 — Live Both Controllers Concurrent

1. Turn on **both controllers**. Verify both connect via `bluetoothctl info`.
2. Connect the client for 60 seconds on the batched stream:
   ```bash
   python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 60
   ```
3. **Pass Criterion:** Both hands stream concurrently; per-hand sequence numbers advance independently at ~150–180/s each; batches contain interleaved records from both hands; `errors = 0`.

### P5 — 5 GHz Link Quality & Latency Benchmark

1. Confirm Pi is on 5 GHz Wi-Fi: `iw dev wlan0 link | grep freq` (must exceed 5000 MHz).
2. Run a 10-minute continuous batched run with ping RTT enabled:
   ```bash
   python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 600 --ping-interval 1.0
   ```
3. Record ping RTT `p50`, `p95`, `min`, `max`, and `dropped`.
4. *(Optional comparison)* Reconnect Pi to a 2.4 GHz SSID and run 5 minutes with the same command.
5. **Pass Criterion:** On 5 GHz, `dropped = 0` at the server; ping RTT p50 < 15 ms; no Wi-Fi packet drops. Document 2.4 GHz degradation for comparison.

### P6 — Upstream Report Loss Benchmark (Comparability Gate)

This is the primary benchmark to compare against the ESP32 firmware runs.

1. Place physical LEFT at Pos L and physical RIGHT at Pos R, stationary, face-down.
2. Both controllers connected, bridge streaming batched records over 5 GHz Wi-Fi.
3. Wait **30 seconds settle time**.
4. Run a continuous **120-second (2-minute)** measurement window:
   ```bash
   python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 120
   ```
5. Capture server status at end of run (`serverStatus` JSON object).
6. Record:
   - Per-hand total received records
   - Per-hand tick-based missing reports (from `device_ticks` discontinuities)
   - Calculated loss %: $\frac{\text{missingFromTicks}}{\text{records} + \text{missingFromTicks}} \times 100\%$
   - Controller sequence gaps
   - Stated `n/a` for BLE `rssi`, `phy`, `mtu`, and `interval`
7. **Pass Criterion:** See [Exit Criteria (§4)](#4-exit-criteria--pass--refute-rules).

### P7 — Controller Sleep & Wake Reconnect During Active Stream

1. Start probe with indefinite duration or 120 s:
   ```bash
   python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 120
   ```
2. At 30 s, power down the LEFT controller (hold Windows button for 5 s until LEDs turn off).
3. Verify client continues receiving RIGHT controller data without stall; `status` logs LEFT disconnected.
4. At 60 s, wake the LEFT controller (short press Windows button).
5. **Pass Criterion:** Controller reconnects to BlueZ; bridge re-opens hidraw node; LEFT stream resumes; per-hand sequence counter continues monotonically without resetting to 1; bridge does not crash.

### P8 — Network Disconnect & Reconnect

1. Start probe stream.
2. Disable Wi-Fi on the client device (or drop the link for 10 seconds), then re-enable Wi-Fi.
3. Client reconnects to `ws://<PI_IP>:8765/wmr`.
4. **Pass Criterion:** WebSocket upgrade completes within 5 s; token authentication succeeds (`auth_ok`); client re-subscribes; stream resumes; sequence numbers continue.

### P9 — Concurrent Multi-Client Fan-Out

1. Terminal 1 (Client A — Raw stream):
   ```bash
   python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream raw --duration 30
   ```
2. Terminal 2 (Client B — Batched stream):
   ```bash
   python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 30
   ```
3. **Pass Criterion:** Both clients stream simultaneously without blocking each other; Client A reassembles FF11 raw frames, Client B unpacks FF14 batches; `serverStatus.clients = 2`, `serverStatus.subscribed = 2`; `dropped = 0`.

### P10 — Extended Soak Test

1. Start the bridge daemon under systemd or with `--duration 28800` (8 hours).
2. Connect probe or companion app for an extended soak:
   ```bash
   python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 28800
   ```
3. Record initial and final memory RSS (`ps aux | grep wmr_bridge`), open file descriptors (`ls /proc/<PID>/fd | wc -l`), and server `status.dropped`.
4. **Pass Criterion:** No memory growth (RSS flat within 10 MB); open file descriptors constant; queue drops = 0; no daemon restart.

---

## 4. Exit Criteria & Pass / Refute Rules

Fixed before any hardware data is collected:

- **Pass (Contention Eliminated):** In P6, LEFT loss $\le 3.0\%$ AND RIGHT loss $\le 3.0\%$ with both controllers connected concurrently. This proves that moving to Wi-Fi completely removes the ~18–26% two-controller contention loss.
- **Pass (Asymmetry Localized to Controller):** In P6, LEFT loss $\le 1.0\%$ while RIGHT loss remains $\sim 10.0–12.0\%$. This proves that the ~18% contention was removed, and isolates RIGHT's ~10.8% loss to physical controller hardware (RF front-end or antenna), ruling out bridge radio contention.
- **Refute (Radio / Shared Air Bound):** In P6, both controllers remain at $\ge 15.0\%$ loss despite 5 GHz Wi-Fi. This proves that Bluetooth Classic packet collisions in the 2.4 GHz shared air or piconet scheduling dominate, rather than ESP32 BLE coexistence.
- **Robustness Pass:** P7 (controller reconnect), P8 (network reconnect), and P9 (multi-client) succeed with zero daemon crashes and continuous sequence numbers.

---

## 5. What to Paste Back

### 5.1 Host & Environment Configuration (Once Per Session)

```text
Raspberry Pi model: <e.g. Raspberry Pi 5 Model B Rev 1.0>
OS: <e.g. Raspberry Pi OS Bookworm 64-bit, kernel version>
BlueZ version: <bluetoothctl --version>
Python version: <python3 --version>
Wi-Fi interface & frequency: <iw dev wlan0 link output; freq MHz, SSID, RSSI dBm>
Wi-Fi power save: <iw dev wlan0 get power_save output>
Controller MACs: LEFT=<2C:4D:79:C8:C9:CC>, RIGHT=<2C:4D:79:C8:F9:A9>
Bridge version & commit: <git rev-parse HEAD>
Client platform & build: <companion app commit / wifi_probe.py version>
```

### 5.2 Checklist P1–P10 Results

| Check | Run State | Observed Figures / Behavior | Pass / Fail |
|---|---|---|---|
| **P1** | not run | `--list` output: node paths, MACs, hand assignments | |
| **P2** | not run | Replay: rec/s, gaps, drops | |
| **P3** | not run | Single hand: rate, sequence rise, controls responsive, errors | |
| **P4** | not run | Both hands: rate per hand, interleaved batches, errors | |
| **P5** | not run | 5 GHz 10-min soak: RTT p50, p95, max, drops (vs 2.4 GHz) | |
| **P6** | not run | 120 s benchmark: LEFT loss %, RIGHT loss % (see §5.3) | |
| **P7** | not run | Controller reconnect: sleep timestamp, wake, resume, seq reset? | |
| **P8** | not run | Wi-Fi reconnect: drop timestamp, rejoin, auth, sub resume | |
| **P9** | not run | Multi-client: raw rec/s, batched rec/s, client count, drops | |
| **P10** | not run | 8 h soak: RSS start/end, FDs start/end, uptime, drops | |

### 5.3 P6 Master Comparability Table

Fill in this table to compare the Pi's P6 results against the ESP32 baseline. Mark all non-applicable
fields explicitly as `n/a`:

| Field | ESP32 Alone Baseline | ESP32 Both Connected | Raspberry Pi P6 (Both Hands) | Notes |
|---|---|---|---|---|
| **Bridge Hardware** | ESP32 (Classic BT + BLE) | ESP32 (Classic BT + BLE) | Raspberry Pi (<model>) | Pi 4 / 5 reference |
| **Host Transport** | BLE GATT (FF14 batched) | BLE GATT (FF14 batched) | 5 GHz Wi-Fi (WebSocket) | RFC 6455 `wmr-wifi-v1` |
| **Window Duration** | 120 s (after 30 s settle) | 120 s (after 30 s settle) | 120 s (after 30 s settle) | Standardized window |
| **LEFT Loss \%** | **~0.5 \%** | **~18–20 \%** | | Tick-based missing share |
| **LEFT Rate** | ~203 rec/s | ~165 rec/s | | Unpacked records/s |
| **RIGHT Loss \%** | **~10.8 \%** | **~18–26 \%** | | Tick-based missing share |
| **RIGHT Rate** | ~182 rec/s | ~160 rec/s | | Unpacked records/s |
| **Sequence Gaps** | 0 (alone) | > 0 | | Discontinuities |
| **BLE RSSI (dBm)** | -58 dBm (approx) | -58 dBm (approx) | **n/a** | Wi-Fi transport |
| **Wi-Fi RSSI (dBm)**| **n/a** | **n/a** | | From `iw dev wlan0 link` |
| **BLE PHY** | 1M PHY | 1M PHY | **n/a** | Wi-Fi physical layer |
| **BLE MTU** | 517 | 517 | **n/a** | WebSocket framing |
| **BLE Conn Interval**| 15.00 ms | 15.00 ms | **n/a** | TCP/IP over 802.11 |
| **Echo RTT p50** | ~40.8 ms | ~40.8 ms | | JSON ping/pong RTT |
| **Bridge Drops** | 0 | 0 | | `status.dropped` |
