"""Linux / Raspberry Pi host bridge for WMR motion controllers.

The package reuses the ESP32 firmware's stream payloads (FF11 raw fragments,
FF12 decoded records, FF14 batches) and carries them over a WebSocket on Wi-Fi
instead of BLE. BlueZ owns the Classic Bluetooth HID host side; see
``docs/PI_BRIDGE.md`` for setup and the wire protocol.
"""

__version__ = "0.2.3"
