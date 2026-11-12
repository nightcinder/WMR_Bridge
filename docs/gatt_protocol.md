# GATT protocol and split-link routing

This page lists the BLE GATT identifiers for WMR Bridge and the split-link
routing between BLE and Wi-Fi.

## Split-link routing

| Data | Link | Notes |
| --- | --- | --- |
| Gamepad input (HIDS `0x2A4D`) | BLE HIDS, always on | OS and DroidDeck input |
| Raw, decoded and batched streams (FF11, FF12, FF14) | Wi-Fi TCP/WebSocket | BLE FF1x is the fallback |
| Calibration (FF13) | Wi-Fi TCP/WebSocket | BLE FF13 is the fallback |
| Runtime control, echo and upgrade (FF15, FF17) | Wi-Fi TCP/WebSocket | BLE FF15 and FF17 are the fallback |

BLE HIDS (`0x1812`/`0x2A4D`) stays connected and active at all times. It is
not suppressed by a stream. The report is paced at 10 ms when idle and 20 ms
while any stream is active, TCP included. While an FF1x frame waits, HIDS
never takes two send slots in a row.

## GATT identifiers

| Item | UUID | Properties |
| --- | --- | --- |
| FF10 service | `0000FF10-0000-1000-8000-00805F9B34FB` | primary |
| Characteristic | `0000FF11-0000-1000-8000-00805F9B34FB` | read, notify |
| Characteristic | `0000FF12-0000-1000-8000-00805F9B34FB` | notify |
| Characteristic | `0000FF13-0000-1000-8000-00805F9B34FB` | read, write, notify |
| Characteristic | `0000FF14-0000-1000-8000-00805F9B34FB` | notify |
| Characteristic | `0000FF15-0000-1000-8000-00805F9B34FB` | write without response, notify |
| Characteristic | `0000FF16-0000-1000-8000-00805F9B34FB` | read |
| Characteristic | `0000FF17-0000-1000-8000-00805F9B34FB` | read, write, notify |
| HID service | `00001812-0000-1000-8000-00805F9B34FB` | primary |
| Characteristic | `00002A4D-0000-1000-8000-00805F9B34FB` | read, notify |
| DIS PnP ID | `00002A50-0000-1000-8000-00805F9B34FB` | read |

The DIS PnP ID stays fixed (VID `0x02E5`, PID `0x0001`) so a host can claim
the device by VID/PID.

For the report layout and mapping, read [HID_GAMEPAD.md](HID_GAMEPAD.md). For
the full BLE protocol, read [PROTOCOL.md](PROTOCOL.md). For the Pi host bridge,
read [PI_BRIDGE.md](PI_BRIDGE.md).