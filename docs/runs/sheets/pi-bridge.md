# Run sheet: Linux / Raspberry Pi Wi-Fi host bridge (P1-P10)

Status: proposed, not run. Nothing here is a hardware result. The owner is buying a Raspberry Pi; this sheet fixes the first sitting and keeps its numbers comparable with the ESP32 runs.

Question: does moving the phone link to a Raspberry Pi over 5 GHz Wi-Fi remove upstream report loss, and how does the Pi's Classic radio compare with the ESP32?

## 0. What changes and what does not

Wi-Fi removes BLE notification airtime from the bridge. 5 GHz also keeps the phone link away from Bluetooth Classic at 2.4 GHz. It does **not** change the controller-to-bridge Classic link. Whether the Pi radio and BlueZ HIDP do better than the ESP32 is what P6 measures.

Baselines: LEFT alone ~0.5 %, RIGHT alone ~10.8 %, both connected ~18-26 %, phone RTT p50 ~40.8 ms. The Pi row is measured by this sheet.

To compare with ESP32 runs:
1. **30 s settle**, then a **120 s window**.
2. Report tick-based missing share: `missingFromTicks / (records + missingFromTicks) * 100`.
3. Mark fields the Pi does not have as **`n/a`**, never blank or zero: BLE `rssi`, `phy`, `mtu`, `interval`, `esp_timer`. Wi-Fi RSSI may be noted separately from `iw dev wlan0 link`.

## 1. Prerequisites
- **Pi 5 or Pi 4 Model B** are the reference targets. **Pi 3B+** is expected and is the 2026-11-07 board. **Pi Zero 2 W is not supported** (2.4 GHz only).
- Raspberry Pi OS 64-bit (Bookworm or Trixie). Record image date and kernel.
- Python >= 3.10, BlueZ with the `input` plugin, `hidp` loaded, read access to `/dev/hidraw*` (user in `input` or a udev rule).
- Controllers: LEFT `2C:4D:79:C8:C9:CC`, RIGHT `2C:4D:79:C8:F9:A9`. Two fixed marks, face down and still.
- A dedicated 5 GHz SSID; `iw dev wlan0 link` must show `freq` above 5000.
- Wi-Fi power save off: `sudo iw dev wlan0 set power_save off`.
- Client: the Companion Wi-Fi path, or `pi/tools/wifi_probe.py` as fallback. The app path is host-tested only until this sitting runs.
- Security: `--bind` defaults to loopback. A non-loopback bind needs `--token` or `--token-file`. Create a `0600` token:

  ```bash
  head -c 24 /dev/urandom | base64 > token
  chmod 600 token
  ```

Desk pre-flight (already run 2026-10-31, not Pi results): package build and layout, `.deb` contents, installer staging, and `pi/tools/dry_run.sh` all pass. Re-run `dry_run.sh` after any change under `pi/`. Hardware checks stay P1-P10.

## 2. Go / no-go before timing

| Check | Go |
|---|---|
| BlueZ | service active, `input` plugin enabled |
| `hidp` | `lsmod \| grep hidp` |
| hidraw | `test -r /dev/hidraw0` works without `sudo` |
| Wi-Fi band | `iw dev wlan0 link` reports `freq` > 5000 |
| Power save | `iw dev wlan0 get power_save` reports `off` |
| Token | mode `0600`, owned by the service user |
| Bind | `--bind 0.0.0.0` only with a token file |
| Client auth | `auth_ok`, then `subscribed` within 5 s |
| Stream | `rate_rx` 150-205/s per active hand, `errors=0` |

## 3. Procedure

### P1 — Daemon start and discovery

```bash
cd /opt/wmr-bridge/pi
python3 -m wmr_bridge --list
```

Pass: exactly two hidraw nodes, one LEFT and one RIGHT, with valid MACs and readable permissions; exit code 0.

### P2 — Replay smoke test

```bash
python3 -m wmr_bridge --replay ../samples/controller_frames.txt --bind 127.0.0.1 --port 8765
python3 tools/wifi_probe.py --host 127.0.0.1 --port 8765 --stream batched --duration 10
```

Pass: about 160 records/s, `gaps=(0,0)`, `dropped=0`, clean exit.

### P3 — Live single controller

Turn on only LEFT, then:

```bash
python3 -m wmr_bridge --bind 0.0.0.0 --port 8765 --token-file token
python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream decoded --duration 60
```

Exercise buttons, stick, trigger and 90-degree turns. Pass: sequence advances ~150-180/s, values respond, `errors=0`.

### P4 — Both controllers

```bash
python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 60
```

Pass: both hands stream independently at ~150-180/s, batches interleave both, `errors=0`.

### P5 — 5 GHz link quality

```bash
python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 600 --ping-interval 1.0
```

Record RTT p50/p95/min/max and drops. Optional: repeat 5 minutes on 2.4 GHz. Pass: `dropped=0`, RTT p50 < 15 ms, no Wi-Fi packet drops.

### P6 — Upstream loss (primary)

Controllers on their marks, both connected, batched stream, 5 GHz. Wait 30 s. Run:

```bash
python3 tools/wifi_probe.py --host <PI_IP> --port 8765 --token-file token --stream batched --duration 120
```

Record per hand: records, `missingFromTicks`, loss %, sequence gaps; mark BLE fields `n/a`; capture `serverStatus`.

### P7 — Controller sleep and wake

During a 120 s stream, power off LEFT at 30 s, wake it at 60 s. Pass: RIGHT keeps streaming; LEFT reconnects, hidraw reopens, sequence continues; no crash.

### P8 — Network reconnect

Drop the client Wi-Fi for 10 s, then rejoin. Pass: WebSocket upgrade within 5 s, `auth_ok`, re-subscribe, stream resumes, sequence continues.

### P9 — Multi-client fan-out

Run one `--stream raw` client and one `--stream batched` client for 30 s. Pass: both stream, `clients=2`, `subscribed=2`, `dropped=0`.

### P10 — Soak

Run the daemon under systemd or with `--duration 28800`, and a client for the same period. Record RSS, open file descriptors and `status.dropped` at start and end. Pass: RSS flat within 10 MB, FDs constant, no drops, no restart.

## 4. Pass / refute

Fixed before data:
- **Pass, contention removed:** P6 LEFT <= 3 % and RIGHT <= 3 %.
- **Pass, asymmetry localised:** P6 LEFT <= 1 % and RIGHT ~10-12 %. The pair contention is gone; RIGHT's loss is controller hardware.
- **Refute:** P6 both hands >= 15 % despite 5 GHz Wi-Fi. Shared 2.4 GHz air or Classic scheduling dominates, not ESP32 BLE coexistence.
- **Robustness pass:** P7, P8 and P9 succeed with no crash and continuous sequence numbers.

## 5. Paste back

Once per session: Pi model, OS/kernel, BlueZ, Python, Wi-Fi interface and frequency, power save, controller MACs, bridge version and commit, client build.

Then the P1-P10 table with observed figures and pass/fail, and the P6 comparison table:

| Field | ESP32 alone | ESP32 both | Pi P6 |
|---|---|---|---|
| LEFT loss / rate | ~0.5 % / ~203/s | ~18-20 % / ~165/s | |
| RIGHT loss / rate | ~10.8 % / ~182/s | ~18-26 % / ~160/s | |
| Sequence gaps | 0 alone | > 0 | |
| BLE RSSI / PHY / MTU / interval | values | values | `n/a` |
| Wi-Fi RSSI | `n/a` | `n/a` | |
| Echo RTT p50 | ~40.8 ms | ~40.8 ms | |
| Bridge drops | 0 | 0 | |

## 6. Not covered
This sheet tests the host only; it does not change ESP32 firmware. P6 may localise RIGHT's ~10.8 %, but it cannot remove it. A 2.4 GHz-only Pi defeats the test.
