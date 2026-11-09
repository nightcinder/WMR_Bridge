# Run sheet: Linux / Raspberry Pi Wi-Fi host bridge (P1-P14)

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
- Security: `--bind` defaults to loopback. A non-loopback bind needs `--token` or `--token-file`. Create a token for the probe:

  ```bash
  head -c 24 /dev/urandom | base64 > token
  chmod 600 token
  ```

Desk pre-flight (already run 2026-10-31, not Pi results): package build and layout, `.deb` contents, installer staging, and `pi/tools/dry_run.sh` all pass. Re-run `dry_run.sh` after any change under `pi/`. Hardware checks stay P1-P14.

## 2. Go / no-go before timing

| Check | Go |
|---|---|
| BlueZ | service active, `input` plugin enabled |
| `hidp` | `lsmod \| grep hidp` |
| hidraw | `test -r /dev/hidraw0` works without `sudo` |
| Wi-Fi band | `iw dev wlan0 link` reports `freq` > 5000 |
| Power save | `iw dev wlan0 get power_save` reports `off` |
| Token | mode `0640`, owned by `wmr:wmr` |
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

### P11 — Automatic pairing from unpaired

Remove both controller bonds first (`bluetoothctl remove <MAC>` for each). Start the daemon without `--no-auto-pair`. Put LEFT in pairing mode. Pass: the bridge pairs, trusts and connects LEFT within 60 s without operator input; LEFT appears in `/sys/class/hidraw`. Repeat for RIGHT. Then stop the daemon, remove both bonds again, and start with `--no-auto-pair`. Put one controller in pairing mode. Pass: the bridge does not pair it; pairing stays with the operator.

### P12: Two-controller TCP-to-BLE failover and jitter on Pixel 11 Pro (ONY-1070)

The bridge enables BLE GATT fallback by default (PR #187). In `package:wmr_companion` (0.3.19+24 or later), `CompositeBleTransport` prefers TCP and falls back to BLE GATT when TCP disconnects. In the reference app (`wmr_companion_app`), the Connect screen provides a manual Bluetooth or Wi-Fi selector. You run automated composite failover with a library host that instantiates `CompositeBleTransport`.

Prerequisites:
- Raspberry Pi 3B+ runs `wmr-bridge` on 5 GHz Wi-Fi with token authentication.
- Both WMR controllers are paired and streaming to the bridge.
- The Pixel 11 Pro (or host test client) runs a library harness that instantiates `CompositeBleTransport` against the Pi TCP endpoint and the BLE peripheral `WMR Bridge`.

Procedure:

1. Configure the `CompositeBleTransport` harness with the Pi IP address, port 8765, token, and BLE device name `WMR Bridge`.
2. Connect to the bridge. The transport selects TCP as the primary leg.
3. Subscribe to the `batched` stream.
4. Make sure that both controllers stream at 150-180 reports/s per hand with zero decode errors.
5. Open the latency recorder and measure the baseline round-trip time and frame arrival intervals.
6. Disconnect Wi-Fi while both controllers stream: disable Wi-Fi on the client device or drop `wlan0` on the Pi:

   ```bash
   sudo ip link set wlan0 down
   ```

7. Observe failover on `CompositeBleTransport`. The transport must fall back to BLE GATT without closing the stream session.
8. Measure the failover transition time and the inter-report jitter:
   - Time from TCP disconnect to first BLE report must be under 2500 ms.
   - Inter-frame delivery pause must not exceed 3000 ms.
   - Controller sequence numbers must continue without resetting.
   - Zero application crashes and zero unhandled transport exceptions.
9. Restore Wi-Fi: enable Wi-Fi on the client device or run `sudo ip link set wlan0 up` on the Pi.
10. Make sure that `CompositeBleTransport` upgrades back to TCP within 5 s, silences BLE notifications, and resumes streaming on the TCP leg.
11. If you use `wmr_companion_app` directly on the Pixel 11 Pro, test the legs individually: select Wi-Fi in the Connect screen to test TCP streaming, then select Bluetooth to test direct BLE GATT streaming.

Pass: failover to BLE GATT completes in under 2.5 s with sequence continuity, zero application crashes, and clean upgrade back to TCP when Wi-Fi recovers.

### P13: Controller idle sleep and power-off on Pi 3B+ (ONY-1058, ONY-1064)

The Pi bridge pauses 0x06 keep-alive reports after 300 s of inactivity and drops the Bluetooth link after 900 s (PR #190). Unlike the ESP32 firmware, the Pi host daemon does not implement a 60 s reconnect cooldown. Powering the controller on reconnects normally.

Prerequisites:
- The Pi 3B+ runs `wmr-bridge` with default idle timeouts (`--idle-sleep-timeout 300 --idle-poweroff-timeout 900`).
- Both WMR controllers are paired and connected.

Monitor the systemd journal in real time:

```bash
journalctl -u wmr-bridge -f
```

Procedure:

1. Turn on both controllers. Make sure that both controllers stream in `wmr-bridge status` or `wifi_probe.py`.
2. Place both controllers untouched on their fixed desk marks. Record timestamp `T0`.
3. Wait 300 s (5 minutes) without moving either controller.
4. At `T0 + 300 s`, make sure that the journal records keep-alive pausing:
   `LEFT idle for 300 s: pausing 0x06 keep-alives`
   `RIGHT idle for 300 s: pausing 0x06 keep-alives`
5. Make sure that both controllers enter standby (LEDs dim or pulse slowly). The Bluetooth ACL link must remain connected (`bluetoothctl info <MAC>` reports `Connected: yes`).
6. At `T0 + 400 s`, test the activity wake on LEFT: move the thumbstick or press the trigger.
7. Make sure that the journal reports:
   `LEFT activity: resuming 0x06 keep-alives`
   Make sure that LEFT resumes full streaming in under 1.0 s without an ACL reconnect.
8. Leave RIGHT untouched until `T0 + 900 s` (15 minutes).
9. At `T0 + 900 s`, make sure that the bridge disconnects RIGHT:
   `RIGHT idle for 900 s: disconnecting <MAC> to power it off`
10. Make sure that the RIGHT controller turns off completely (ring LEDs extinguish).
11. Turn on the RIGHT controller again: press the Windows power button on RIGHT.
12. Make sure that RIGHT reconnects and streams cleanly.

Pass: keep-alive pauses at 300 s ± 5 s. Motion wakes the controller in under 1.0 s. Power-off disconnect occurs at 900 s ± 5 s. Powering on the controller reconnects and streams cleanly.

### P14: Uplink priority and Sitting P gates (ONY-1114, PR #188)

PR #188 configures network route metrics so `eth0` (metric 100) takes priority over 5 GHz Wi-Fi (metric 200), which takes priority over 2.4 GHz Wi-Fi (metric 300). `pi/tools/wifi_probe.py` connects to the bridge and outputs JSON metrics for Sitting P validation.

Procedure:

1. Connect the Pi 3B+ to both Ethernet (`eth0`) and 5 GHz Wi-Fi (`wlan0`).
2. Run `wmr-bridge status`. Make sure that the output reports `eth0` as the active uplink:
   `Uplink eth0: kind=ethernet addresses=[...]`
3. Run `wifi_probe.py` against localhost:

   ```bash
   python3 tools/wifi_probe.py --host 127.0.0.1 --token-file /etc/wmr-bridge/token --duration 5
   ```

   Make sure that `hello` reports `transport: "ethernet"` and `uplink.iface: "eth0"`.
4. Disconnect the Ethernet cable. Wait 5 s for route metrics to update.
5. Run `wmr-bridge status`. Make sure that the uplink switches to `wlan0` with `kind=wifi` and `supports5Ghz=yes`.
6. Run `wifi_probe.py` on 5 GHz Wi-Fi with both controllers active:

   ```bash
   python3 tools/wifi_probe.py --host 127.0.0.1 --stream batched --duration 120 --token-file /etc/wmr-bridge/token > /tmp/probe-p14.log
   ```

7. Inspect the test summary at the end of `/tmp/probe-p14.log` (or on stdout).

Pass criteria for Sitting P gates:
- Uplink reports `eth0` when wired and `wlan0` (5 GHz) when unwired.
- `rttMs.p50` < 15.0 ms.
- Missing share per hand <= 3.0 % (or LEFT <= 1.0 % and RIGHT ~10-12 % for asymmetry localisation).
- Sequence gaps == 0 on both hands.
- Server dropped messages == 0 (non-null `serverStatus` with `serverStatus.dropped == 0`).
- `wifi_probe.py` exits with code 0.

## 4. Pass / refute

Fixed before data:
- **Pass, contention removed:** P6 LEFT <= 3 % and RIGHT <= 3 %.
- **Pass, asymmetry localised:** P6 LEFT <= 1 % and RIGHT ~10-12 %. The pair contention is gone; RIGHT's loss is controller hardware.
- **Refute:** P6 both hands >= 15 % despite 5 GHz Wi-Fi. Shared 2.4 GHz air or Classic scheduling dominates, not ESP32 BLE coexistence.
- **Robustness pass:** P7, P8 and P9 succeed with no crash and continuous sequence numbers.
- **Failover pass (P12):** TCP-to-BLE failover occurs in under 2.5 s with sequence continuity, zero crashes, and clean upgrade back to TCP when Wi-Fi recovers.
- **Power management pass (P13):** Keep-alive pauses at 5 min and ACL disconnects at 15 min.
- **Uplink gate pass (P14):** Ethernet takes priority over 5 GHz Wi-Fi, and Sitting P criteria pass in `wifi_probe.py` JSON output.

## 5. Paste back

Once per session: Pi model, OS/kernel, BlueZ, Python, Wi-Fi interface and frequency, power save, controller MACs, bridge version and commit, client build.

Then the P1-P14 table with observed figures and pass/fail, and the P6 comparison table:

| Check | Name | Result | Notes |
|---|---|---|---|
| P1 | Daemon start and discovery | | |
| P2 | Replay smoke test | | |
| P3 | Live single controller | | |
| P4 | Both controllers | | |
| P5 | 5 GHz link quality | | |
| P6 | Upstream loss (primary) | | |
| P7 | Controller sleep and wake | | |
| P8 | Network reconnect | | |
| P9 | Multi-client fan-out | | |
| P10 | Soak | | |
| P11 | Automatic pairing from unpaired | | |
| P12 | TCP-to-BLE failover and jitter | | |
| P13 | Controller idle sleep and power-off | | |
| P14 | Uplink priority and Sitting P gates | | |

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
