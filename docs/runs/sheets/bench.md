# Minimum bench run sheet

Hardware: Raspberry Pi 3B+, ESP32 (v0.3.18 candidate), LEFT and RIGHT WMR controllers, Pixel 11 Pro running wmr_companion_app v0.3.19. Target time is about 24 minutes.

## Part A: Raspberry Pi 3B+ (~10 min)

1. Boot and uplink (PR #187, #188). Start `wmr-bridge` on Pi 3B+ with `--idle-sleep-timeout 30 --idle-poweroff-timeout 90`. Pair LEFT and RIGHT controllers. Run `wmr-bridge status`.
   - [ ] PASS / FAIL — Both controllers connected. Uplink matches the active interface (`eth0` metric 100 or `wlan0` 5 GHz metric 200). BlueZ advertises `WMR Bridge`.
2. UDP discovery and Sitting P gates (ONY-1144, PR #193). Run `python3 pi/tools/wifi_probe.py --discover --assert-gates --duration 30 --json-out /tmp/pi_probe.json` while moving both controllers.
   - [ ] PASS / FAIL — Command exits 0. UDP port 9877 discovered. RTT p50 under 15 ms. Missing share at most 3.0% per hand. Zero sequence gaps. Server drops (`dropped`) are 0 (observed; not a gate).
3. LAN auto-connect (ONY-1143). Open `wmr_companion_app` on Pixel 11 Pro on the same LAN.
   - [ ] PASS / FAIL — App auto-discovers Pi via UDP port 9877 and streams both hands over TCP at 150-180 reports/s per hand.
4. TCP-to-BLE failover (ONY-1143, ONY-1070). Run `sudo ip link set wlan0 down` on Pi while streaming to Pixel 11 Pro.
   - [ ] PASS / FAIL — CompositeBleTransport fails over to BLE GATT within 2500 ms with continuous pose updates.
5. BLE-to-TCP recovery (ONY-1143). Run `sudo ip link set wlan0 up` on Pi and wait up to 5 s.
   - [ ] PASS / FAIL — Transport auto-upgrades back to TCP within 5 s with no app restart.
6. Pi idle sleep and wake (PR #190, ONY-1058). Leave both controllers untouched for 35 s, then tilt LEFT.
   - [ ] PASS / FAIL — Journal logs keep-alive paused at about 30 s. Tilting wakes the stream within 1 s.
7. Pi idle power-off and reconnect guard (PR #190, ONY-1185). Leave both controllers untouched for 95 s.
   - [ ] PASS / FAIL — Journal logs the BlueZ block at about 90 s and both LED rings turn off. The expected trace is: `idle for <N> s: disconnecting ... to power it off`, `bluetoothctl block <addr>` (BlueZ drops the ACL with the block), then either `bluetoothctl disconnected <addr>` or `bluetoothctl <addr> already disconnected` depending on which drop wins, and no `LEFT/RIGHT connected` line for 60 s. After `power-off cooldown elapsed: reconnects allowed` and `bluetoothctl unblock <addr>`, press a button on LEFT; it reconnects and streams. Stop Pi `wmr-bridge` before Part B.

   The ONY-1064 log showed reports still arriving after the 5-minute keep-alive pause; do not treat that pause alone as evidence the controller is asleep. The ACL drop in item 7 is the power-off signal.

## Part B: ESP32 (~14 min)

8. ESP32 BLE GATT stream (ONY-1070). Boot ESP32 (v0.3.18). Power on both controllers. Connect Pixel 11 Pro over BLE in `wmr_companion_app`.
   - [ ] PASS / FAIL — Both hands stream over BLE characteristic 0xFF14 at 150-180 reports/s per hand. Zero disconnects over 60 s.
9. FF16 version and FF17 control (Block B). Open DebugScreen on Pixel 11 Pro. Check firmware version. Tap disconnect on LEFT (0xFF17). Reconnect LEFT.
   - [ ] PASS / FAIL — FF16 version matches serial `v` output. FF17 drops LEFT cleanly and reconnects within 5 s.
10. Five-minute active keep-alive (ONY-1058). Keep controllers moving or tapped for 5 min 30 s while connected to ESP32.
    - [ ] PASS / FAIL — No controller powers off during more than 5 min of active use. Zero unexpected ACL drops.
11. ESP32 idle sleep and motion wake (PR #191). Leave both controllers untouched for 5 min 15 s. Check serial log. Tilt both controllers.
    - [ ] PASS / FAIL — Serial logs `FF13 LEFT: idle for <N> ms; keep-alive paused, controller may sleep` (and RIGHT), with N between 300000 and 310000. Tilting logs `FF13 LEFT: activity after idle sleep; resuming the keep-alive` (and RIGHT) and resumes streaming within 1 s.
12. Bundle export (ONY-1117). Tap Export Test Bundle in `wmr_companion_app` on Pixel 11 Pro.
    - [ ] PASS / FAIL — ZIP bundle exports with `manifest.md`, `diagnostics.json` and non-empty controller samples.

## Optional expansions

Run only when investigating a regression: (a) ESP32 full 15-min untouched power-off plus 60 s cooldown rejection (900 s wait); (b) FF13 stick deadzone and calibration persistence across reboot (3 min); (c) 3DoF 90-degree turn and 60 s yaw-drift check (5 min).
