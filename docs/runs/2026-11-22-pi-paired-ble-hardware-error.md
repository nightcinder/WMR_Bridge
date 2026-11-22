# Paired BLE bench and `BCM4345C0` hardware error verdict (2026-11-22)

On 2026-11-22, the paired bench (`wmr-bridge` `3970074`, `wmr-companion` `a9d2fa5`) kept prior passes on `ONY-1313` items 1, 2, and 4. Item 3 failed after BLE cycle 1 when Pi `hci0` logged `hardware error 0x00` and `Opcode 0x0c03 failed: -110`. The `#281` recovery ladder (`pi/systemd/wmr-bridge-bt-recover.sh:243-248`) skipped `hci_uart_bcm` sysfs unbind, prevented any `bcm_recv` Oops or 120 s hang, and restored TCP in 26.2 s while BLE stayed `Powered=no`. `ONY-1711` (revised) withdrew Option B (no USB dongle) and selected Option C: keep onboard `BCM4345C0` with config-gated BLE load cuts on by default, keep TCP unthrottled at `200 Hz`, and ship TCP-only if one `btmon` bench hits `hardware error 0x00`, `-84`, or `-110`.

## Evidence key and redaction

All Bluetooth MAC addresses, link keys, tokens, and screenshots from the private `liesa` capture directory (`/home/liesa/agent-runs/ony-1313-3970074.IkxUAt3T/paired`) are omitted or replaced with `[Pi hci0]`, `[Controller L]`, `[Controller R]`, and `[Phone]`.

- Repository citations use `path:line` at `WMR_Bridge` commit `3970074` and `wmr-companion` commit `a9d2fa5`.
- Pi wall clocks in `paired/events.txt` ran about 1.37 s behind the Pixel wall clock.
- Prior bench records are in [`2026-11-18-pi-bench.md`](2026-11-18-pi-bench.md) and [`2026-11-21-pi-wlan-cycle-wedge.md`](2026-11-21-pi-wlan-cycle-wedge.md).

## Bench summary and `#281` recovery verification

| Check or phase | Outcome | Key evidence |
|---|---|---|
| `ONY-1313` items 1, 2, 4 | Retain prior PASS | Prior baseline (`paired` capture) |
| BLE cycle 1 connect and stream | Streamed at +11 s and +15 s, then failed | `paired/gatt.log`, `paired/pi-during.txt` |
| `#281` recovery ladder | PASS degraded TCP survival in 26.2 s | `paired/recovery-end.txt`, `pi/systemd/wmr-bridge-bt-recover.sh:243-248` |
| BLE cycle 2 (dead `hci0`) | Stalled on MTU and discovery (~16 s) | `paired/gatt.log`, `universal_ble_transport.dart:538-578` |
| Post-enable biometric prompts | External Android OS prompt | `paired/events.txt`, `ony-1313-3970074.IkxUAt3T/pixel-radio-log.txt` |
| Pi power cycle | Restored `[Controller L]` and `[Controller R]` | Post-run Pi power cycle |

In BLE cycle 1, `[Phone]` reconnected, negotiated MTU 517, completed service discovery, and enabled notifications. The stream ran at the +11 s and +15 s checks. Both `[Controller L]` and `[Controller R]` then disconnected with `EIO`, `hci0` logged `hardware error 0x00`, `HCI Reset` (`0x0c03`) timed out with `-110`, and `hci0` went `Powered=no` (`paired/pi-during.txt`).

The recovery unit logged that `hci0` uses `hci_uart_bcm` on the serial bus and skipped sysfs unbind and bind (`pi/systemd/wmr-bridge-bt-recover.sh:243-248`). It exited with `EXIT_UNPOWERED` (status 2) in 26.2 s with zero `bcm_recv` Oops and zero 120 s unit hang (`paired/recovery-end.txt`). `wmr-bridge.service` restarted with TCP listening on `0.0.0.0:8765`, while BLE remained `Powered=no` (`paired/pi-samples.txt`). A later Pi power cycle restored both controllers (post-run bench observation).

## `BCM4345C0` hardware-error evidence across six captures

In `paired`, both `eth0` and `wlan0` stayed up with zero `wlan-cycle`, zero `AutoPairer` scans, and zero `refresh_advertising` calls before the fault (`paired/pi-during.txt`). At 6.7 s after cycle-1 `FF14` enable (`1795365160.410` Pi), `hci0` logged `hardware error 0x00`, both `hidraw` nodes failed with `EIO`, and `Opcode 0x0c03 failed: -110` followed 2.14 s later (`paired/pi-during.txt`).

Across five sustained `FF14` captures (`MTU 517`, `11.25 ms` interval, 50 Hz batches alongside dual 200 Hz Classic HID), `BCM4345C0` crashed after 1.37 s (`bench-2026-11-20-codex/btmon.txt`), 19.0 s (`09:24` `radio-pi-fault.txt`), 11.7 s (`11:30`), 6.7 s (`11:32` `paired`), and 17.6 s (`11:42` `ony-1615`). In `btmon.txt:124887-127864`, three-fragment `len 508` `FF14` notifications (`#25595` to `#25597`) entered the controller's `251 x 7` LE TX buffer (`135270-135273`), followed 5 ms later by 37 `Hardware Error (0x10) Code: 0x00` events (`#25600` to `#25636`) and `Unknown (0x23|0x003c)` (`#25638`, H4 UART desync).

The same `BCM4345C0` (`hci_uart_bcm` over 3-Mbps PL011 UART) also failed without `FF14` load during a service restart and in a separate spontaneous crash (`2026-11-18-pi-bench.md:71-85`, `-110`), and with both hands down (`2026-11-21-pi-wlan-cycle-wedge.md:50-58`, 30,994 lines of `Frame reassembly failed (-84)`). Three repo behaviors amplified BLE load on `hci0`:

- `wmrRequestedMtu` requests `517` (`packages/wmr_core/lib/protocol/gatt.dart:75`) and `requestHighConnectionPriority` (`universal_ble_transport.dart:553`) lowers the `15-18.75 ms` BlueZ interval (`pi/wmr_bridge/bluez_conf.py:51-52`) to `11.25 ms`.
- `pi/wmr_bridge/ble_gatt.py:635-645` packs up to 9 records (`508` B L2CAP, 3 HCI ACL packets), and `_queue_batch` (`pi/wmr_bridge/ble_gatt.py:987-996`) flushes immediately when `len(_batch) >= self.batch_max_count`.
- `pi/wmr_bridge/ble_gatt.py:928-948` also sends 50 Hz `HID_REPORT` (`0x2A4D`) notifications over `hci0` alongside `FF14` and 400 Hz Classic HID RX.

## External OS biometric prompt (Anomaly 2)

Brief biometric prompts appeared on `[Phone]` after each Bluetooth enable. Foreground snapshots at +11 s and +15 s showed `wmr-companion`, so those snapshots did not capture the short prompts.

Logs show `com.google.android.crossdeviceaccessservice/.primary.shared.ui.biometric.BiometricPromptActivity` (`uid 10299`) launched 315 ms after `cycle1-enable` and 226 ms after `cycle2-enable` (`paired/events.txt`, `pixel-radio-log.txt`). Both launches happened before `wmr_companion` registered `onClientRegistered(0)`, 7.76 s before cycle-1 GATT connect, and in cycle 2 while `hci0` was `Powered=no`. `wmr-companion` uses no biometric or CrossDevice APIs, and encrypted BlueZ GATT characteristics (`pi/wmr_bridge/ble_gatt.py:210-257`) were not read before the prompt appeared. This prompt is external to both repositories.

## Cycle-2 dead-peer `requestMtu` swallow (Anomaly 3)

Cycle 2 ran while Pi `hci0` was already `Powered=no` (`paired/recovery-end.txt`). `[Phone]` received a connect callback, waited about 10 s for `requestMtu` with no response, requested service discovery near +10 s, and ended the capture near +16 s with zero records (`paired/gatt.log`). This run is not a clean app-only test because the Pi controller was already down.

Code inspection found a real app defect during dead-peer setup. `UniversalBleTransport._setup` (`universal_ble_transport.dart:538-542`) catches all errors from `_adapter.requestMtu(deviceId, requestedMtu)`, including `TimeoutException` from `universal_ble`'s 10 s command queue. It records a warning and continues to `requestHighConnectionPriority` (`universal_ble_transport.dart:553`) and `discoverServices` (`universal_ble_transport.dart:571`), burning a second 10 s timeout on a dead link.

## `ONY-1711` revised Option C resolution

`ONY-1711` withdrew Option B (no USB dongle exists or will be added) and selected Option C with TCP-only as the end state if the onboard `BCM4345C0` fails again:

1. Keep the bridge on onboard `BCM4345C0` (`hci_uart_bcm`) with config-gated BLE load cuts enabled by default: `MTU <= 247` (`<= 4` records, one LE PDU per notify), strict batch spacing in `_queue_batch` (no back-to-back flush), pause `0x2A4D` (`HID_REPORT`) while `FF1x` streams, skip `highPerformance` priority (keep `15–30 ms` interval), and decimate BLE `FF14` to `50–100 Hz/hand` (`50 Hz/hand` default).
2. Keep TCP unthrottled at `200 Hz` with its contract unchanged. Document the lower-rate BLE fallback profile in `docs/PROTOCOL.md` and `docs/PI_BRIDGE.md`, plus `--no-ble` for explicit TCP-only configuration.
3. Run one human bench with `btmon` capture. If `hardware error 0x00`, `-84`, or `-110` returns once, the Pi 3B+ ships TCP-only for BLE fallback using the `#281` degraded path (`--no-ble`), with no further retries on the onboard chip. Caveat: the restart (`-110`) and idle (`-84`) faults occurred without `FF14` load, so this bench may fail; if it does, the TCP-only fallback applies.
4. Anomaly 2 (external `com.google.android.crossdeviceaccessservice` prompt) remains external to both repositories.
5. Anomaly 3 remains unchanged: bound `requestMtu` to `<= 5 s` and `discoverServices` to `<= 10 s` in `UniversalBleTransport._setup`, transitioning to `BleLinkPhase.connectFailed` on timeout.
