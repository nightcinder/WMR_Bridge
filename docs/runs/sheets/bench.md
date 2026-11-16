# Minimum bench run sheet

Hardware: Raspberry Pi 3B+, ESP32 (v0.3.27 candidate), LEFT and RIGHT WMR controllers, Pixel 11 Pro running wmr_companion_app v0.3.25. Target time is about 24 minutes.

## Prerequisites

1. Set `WMR_BRIDGE_ARGS="--idle-sleep-timeout 30 --idle-poweroff-timeout 90"` in `/etc/wmr-bridge/wmr-bridge.env` on the Pi (`pi/systemd/wmr-bridge.service:13,25`).
2. Restart the daemon with `sudo systemctl restart wmr-bridge`.
3. Pair the phone to the Pi over Bluetooth in Android Settings (`docs/HID_GAMEPAD.md:481-523`). If pairability is off, open a pairing window with `wmr-bridge pair` (`docs/HID_GAMEPAD.md:542-560`, `pi/wmr_bridge/ble_gatt.py:2121-2144`). Run `wmr-bridge status` to confirm that the stored LE bond note appears with `pairable=no` (`pi/wmr_bridge/__main__.py:605-612`), or verify `Paired: yes` with `bluetoothctl info <MAC>`.
4. Isolate or unplug `eth0` before the Wi-Fi failover and recovery steps so that network routing stays unambiguous.
5. If a controller powers off, wake it with a physical button press after the 60 s cooldown expires (`pi/wmr_bridge/inputs.py:418-470`). Motion wakes the controller only from stage 1 idle sleep.

## Part A: Raspberry Pi 3B+ (~10 min)

1. Boot and uplink (PR #187, #188). Start `wmr-bridge` on Pi 3B+ with the prerequisites configured. Pair LEFT and RIGHT controllers. Run `wmr-bridge status`.
   - [ ] PASS / FAIL: Both controllers connected. Uplink matches the active interface (`eth0` metric 100 or `wlan0` 5 GHz metric 200). BlueZ advertises `WMR Bridge`. Stored LE bond note appears with `pairable=no` (`pi/wmr_bridge/__main__.py:605-612`) or `bluetoothctl info <MAC>` confirms `Paired: yes`.
2. UDP discovery and Sitting P gates (PR #193). Run `sudo python3 /opt/wmr-bridge/pi/tools/wifi_probe.py --discover --assert-gates --duration 30 --token-file /etc/wmr-bridge/token --json-out /tmp/pi_probe.json` while moving both controllers (`pi/build_deb.sh:106`, `pi/install.sh:438`).
   - [ ] PASS / FAIL: Command exits 0. UDP port 8765 discovered (`pi/wmr_bridge/discovery.py:28`, `pi/tools/wifi_probe.py:48`). RTT p50 under 15 ms. Missing share at most 3.0% per hand. Zero sequence gaps. Server drops (`dropped`) are 0 (observed, not a gate).
3. LAN auto-connect and decode CPU (M2). Open `wmr_companion_app` on Pixel 11 Pro on the same LAN. Sample daemon CPU usage with `top -b -n 60 -d 10 -p "$(systemctl show -p MainPID --value wmr-bridge.service)"` (`pi/wmr_bridge/mdns.py:81-90`).
   - [ ] PASS / FAIL: App auto-discovers Pi via UDP port 8765 and streams both hands over TCP at 150-180 reports/s per hand (`pi/wmr_bridge/discovery.py:28`). Mean decode CPU remains below 25% of one core over 10 minutes with no 10 s sample above 50% (`wmr-companion docs/TEST_PLAN_PI_WMR.md:97`).
4. TCP-to-BLE failover. Make sure that `eth0` is unplugged. Run `sudo ip link set wlan0 down` on Pi while streaming to Pixel 11 Pro.
   - [ ] PASS / FAIL: CompositeBleTransport fails over to BLE GATT within 2500 ms with continuous pose updates.
5. BLE-to-TCP recovery. Run `sudo ip link set wlan0 up` on Pi. Record T0 (command), T1 (first IPv4 on `wlan0`: `ip -o -4 addr show wlan0`), and T2 (app reports TCP).
   - [ ] PASS / FAIL: T2 - T1 <= 5 s and T2 - T0 <= 15 s, with no app restart (`ONY-1456`).
6. Pi idle sleep and wake (PR #190). Leave both controllers untouched for 35 s, then tilt LEFT.
   - [ ] PASS / FAIL: Journal logs keep-alive paused at about 30 s. Tilting wakes the stream within 1 s.
7. Pi idle power-off and reconnect guard (PR #190). Leave both controllers untouched for 95 s.
   - [ ] PASS / FAIL: Journal logs the BlueZ block at about 90 s and both LED rings turn off. The expected trace is: `idle for <N> s: disconnecting...to power it off`, `bluetoothctl block <addr>` (BlueZ drops the ACL with the block), then either `bluetoothctl disconnected <addr>` or `bluetoothctl <addr> already disconnected` depending on which drop wins, and no `LEFT/RIGHT connected` line for 60 s. After `power-off cooldown elapsed: reconnects allowed` and `bluetoothctl unblock <addr>`, press a button on LEFT. It reconnects and streams (`pi/wmr_bridge/inputs.py:418-470`). Stop Pi `wmr-bridge` before Part B.

   The log showed reports still arriving after the keep-alive pause (30 s with bench overrides, default 300 s in `pi/wmr_bridge/inputs.py:26`). Do not treat that pause alone as evidence the controller is asleep. The ACL drop in item 7 is the power-off signal. Motion wakes a controller only from stage 1 idle sleep. Wake a powered-off controller with a button press after the 60 s cooldown (`pi/wmr_bridge/inputs.py:418-470`).

## Part B: ESP32 (~14 min)

8. ESP32 BLE GATT stream. Boot ESP32 (v0.3.27). Power on both controllers. Connect Pixel 11 Pro over BLE in `wmr_companion_app`.
   - [ ] PASS / FAIL: Both hands stream over BLE characteristic 0xFF14 at 150-180 reports/s per hand. Zero disconnects over 60 s.
9. FF16 version and FF17 control (Block B). In `wmr_companion_app` (`v0.3.25`), tap "Open 3D debug view" on the connect screen (`connect_screen.dart:436-445`). Find the FF16 firmware line under the "Stream" heading (`debug_screen.dart:205-213,523-556`, `wmr_ws_transport.dart:285,338-340`). Check the firmware version. In `ControllerPanel`, tap disconnect on LEFT (0xFF17) and reconnect LEFT (`debug_screen.dart:229-235`). Note that `ControllerPanel` (FF17) is hidden on Wi-Fi bridges and is tested on ESP32 BLE only.
   - [ ] PASS / FAIL: FF16 version matches serial `v` output. FF17 drops LEFT cleanly and reconnects within 5 s.
10. Five-minute active keep-alive and Android HID check. Keep controllers moving or tapped for 5 min 30 s while connected to ESP32. While HID and BLE stream together, inspect Android input registration with `adb shell dumpsys input | grep -E -A 25 -i "wmr[- ]bridge"` (`docs/HID_GAMEPAD.md:481-523`).
    - [ ] PASS / FAIL: No controller powers off during more than 5 min of active use. Zero unexpected ACL drops. Android registers `WMR Bridge` with no HID slot starvation.
11. ESP32 idle sleep and motion wake (PR #191). Leave both controllers untouched for 5 min 15 s. Check serial log. Tilt both controllers.
    - [ ] PASS / FAIL: Serial logs `FF13 LEFT: idle for <N> ms; keep-alive paused, controller may sleep` (and RIGHT), with N between 300000 and 310000. Tilting logs `FF13 LEFT: activity after idle sleep; resuming the keep-alive` (and RIGHT) and resumes streaming within 1 s.
12. Bundle export. Tap Export Test Bundle in `wmr_companion_app` on Pixel 11 Pro.
    - [ ] PASS / FAIL: ZIP bundle exports with `manifest.md`, `diagnostics.json` and non-empty controller samples.

## Optional expansions

Run only when investigating a regression: (a) ESP32 full 15-min untouched power-off plus 60 s cooldown rejection (900 s wait), (b) FF13 stick deadzone and calibration persistence across reboot (3 min), (c) 3DoF 90-degree turn and 60 s yaw-drift check (5 min).
