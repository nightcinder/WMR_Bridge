# Paired BLE bench and `BCM4345C0` hardware error verdict (2026-11-22)

On 2026-11-22, the paired bench (`wmr-bridge` `3970074`, `wmr-companion` `a9d2fa5`) kept prior passes on `ONY-1313` items 1, 2, and 4. Item 3 failed after BLE cycle 1 when Pi `hci0` logged `hardware error 0x00` and `Opcode 0x0c03 failed: -110`. The `#281` recovery ladder (`pi/systemd/wmr-bridge-bt-recover.sh:243-248`) skipped `hci_uart_bcm` sysfs unbind, prevented any `bcm_recv` Oops or 120 s hang, and restored TCP in 26.2 s while BLE stayed `Powered=no`. `ONY-1711` resolved this fault with Option B: disable onboard `BCM4345C0` (`dtoverlay=disable-bt`) and run Classic HID plus BLE fallback on one `btusb` USB adapter.

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

The recovery unit logged that `hci0` uses `hci_uart_bcm` on the serial bus and skipped sysfs unbind and bind (`pi/systemd/wmr-bridge-bt-recover.sh:243-248`). It exited with `EXIT_UNPOWERED` (status 2) in 26.2 s with zero `bcm_recv` Oops and zero 120 s unit hang (`paired/recovery-end.txt`). `wmr-bridge.service` restarted with TCP listening on `0.0.0.0:8765`, while BLE remained `Powered=no` (`paired/pi-samples.txt`). A later Pi power cycle restored both controllers (`docs/PI_BRIDGE.md:1423`).

## `BCM4345C0` hardware-error evidence across six captures

In `paired`, both `eth0` and `wlan0` stayed up with zero `wlan-cycle`, zero `AutoPairer` scans, and zero `refresh_advertising` calls before the fault (`paired/pi-during.txt`). At 6.7 s after cycle-1 `FF14` enable (`1795365160.410` Pi), `hci0` logged `hardware error 0x00`, both `hidraw` nodes failed with `EIO`, and `Opcode 0x0c03 failed: -110` followed 2.14 s later (`paired/pi-during.txt`).

Across five sustained `FF14` captures (`MTU 517`, `11.25 ms` interval, 50 Hz batches alongside dual 200 Hz Classic HID), `BCM4345C0` crashed after 1.37 s (`bench-2026-11-20-codex/btmon.txt`), 19.0 s (`09:24` `radio-pi-fault.txt`), 11.7 s (`11:30`), 6.7 s (`11:32` `paired`), and 17.6 s (`11:42` `ony-1615`). In `btmon.txt:124887-127864`, three-fragment `len 508` `FF14` notifications (`#25595` to `#25597`) entered the controller's `251 x 7` LE TX buffer (`135270-135273`), followed 5 ms later by 37 `Hardware Error (0x10) Code: 0x00` events (`#25600` to `#25636`) and `Unknown (0x23|0x003c)` (`#25638`, H4 UART desync).

The same `BCM4345C0` (`hci_uart_bcm` over 3-Mbps PL011 UART) also failed without `FF14` load during a service restart and idle sleep (`2026-11-18-pi-bench.md:71-85`, `-110`) and while idle (`2026-11-21-pi-wlan-cycle-wedge.md:50-58`, 30,994 lines of `Frame reassembly failed (-84)`). Three repo behaviors amplified BLE load on `hci0`:

- `wmrRequestedMtu` requests `517` (`packages/wmr_core/lib/protocol/gatt.dart:75`) and `requestHighConnectionPriority` (`universal_ble_transport.dart:553`) lowers the `15-18.75 ms` BlueZ interval (`pi/wmr_bridge/bluez_conf.py:51-52`) to `11.25 ms`.
- `pi/wmr_bridge/ble_gatt.py:635-645` packs up to 9 records (`508` B L2CAP, 3 HCI ACL packets), and `_queue_batch` (`pi/wmr_bridge/ble_gatt.py:987-996`) flushes immediately when `len(_batch) >= self.batch_max_count`.
- `pi/wmr_bridge/ble_gatt.py:928-948` also sends 50 Hz `HID_REPORT` (`0x2A4D`) notifications over `hci0` alongside `FF14` and 400 Hz Classic HID RX.

## External OS biometric prompt (Anomaly 2)

Brief biometric prompts appeared on `[Phone]` after each Bluetooth enable. Foreground snapshots at +11 s and +15 s showed `wmr-companion`, so those snapshots did not capture the short prompts.

Logs show `com.google.android.crossdeviceaccessservice/.primary.shared.ui.biometric.BiometricPromptActivity` (`uid 10299`) launched 315 ms after `cycle1-enable` and 226 ms after `cycle2-enable` (`paired/events.txt`, `pixel-radio-log.txt`). Both launches happened before `wmr_companion` registered `onClientRegistered(0)`, 7.76 s before cycle-1 GATT connect, and in cycle 2 while `hci0` was `Powered=no`. `wmr-companion` uses no biometric or CrossDevice APIs, and encrypted BlueZ GATT characteristics (`pi/wmr_bridge/ble_gatt.py:210-257`) were not read before the prompt appeared. This prompt is external to both repositories.

## Cycle-2 dead-peer `requestMtu` swallow (Anomaly 3)

Cycle 2 ran while Pi `hci0` was already `Powered=no` (`paired/recovery-end.txt`). `[Phone]` received a connect callback, waited about 10 s for `requestMtu` with no response, requested service discovery near +10 s, and ended the capture near +16 s with zero records (`paired/gatt.log`). This run is not a clean app-only test because the Pi controller was already down.

Code inspection found a real app defect during dead-peer setup. `UniversalBleTransport._setup` (`universal_ble_transport.dart:538-542`) catches all errors from `_adapter.requestMtu(deviceId, requestedMtu)`, including `TimeoutException` from `universal_ble`'s 10 s command queue. It records a warning and continues to `requestHighConnectionPriority` (`universal_ble_transport.dart:553`) and `discoverServices` (`universal_ble_transport.dart:571`), burning a second 10 s timeout on a dead link.

## `ONY-1711` Option B resolution

`ONY-1711` selected Option B over software throttling on `BCM4345C0`. Software throttling alters the `200 Hz` / `MTU 517` BLE fallback contract and cannot prevent restart (`-110`) or idle (`-84`) faults on the onboard PL011 UART chip.

1. Configure `dtoverlay=disable-bt` on the Pi and run both WMR Classic HID links and BLE GATT fallback on one mainline Bluetooth 5.x `btusb` USB adapter (`docs/PI_BRIDGE.md:179`).
2. Keep `MTU 517`, `11.25 ms` connection interval, and `200 Hz` `FF14` unchanged (`packages/wmr_core/lib/protocol/gatt.dart:75`).
3. Auto-gate `wmr-bridge` startup to TCP-only when `hci0` uses `hci_uart` or `serial`, or when no adapter is present, with an off-by-default CLI override.
4. Bound `requestMtu` (`<= 5 s`) and `discoverServices` (`<= 10 s`) in `UniversalBleTransport._setup` so `TimeoutException` fails fast with `BleLinkPhase.connectFailed` and disconnects.
5. Gate `ONY-1313` item 3 acceptance on a clean two-cycle TCP-down bench run using `dtoverlay=disable-bt` and a `btusb` dongle, with no further unmitigated `BCM4345C0` runs.
