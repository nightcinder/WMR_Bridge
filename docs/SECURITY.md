# Security limitations

wmr_bridge is a **test bridge**. It is not hardened and must not be treated as
secure.

## BLE side (phone)

- The GATT service has **no authentication, pairing or encryption
  requirement**. Any BLE central in range can connect, read FF11 and enable
  notifications, and then receive the raw controller data in clear text over
  the air. Anyone who sniffs the link sees the same data.
- Only one phone is served at a time, so an unwanted device that connects and
  subscribes first also **blocks** the intended phone until it disconnects.
- The device is continuously advertising as `WMR Bridge`, so its presence is
  observable.
- The FF10/FF11 UUIDs are experimental and unregistered; another device or app
  could use the same UUIDs.
- There is no rate limiting or input validation beyond the CCC write (the
  characteristic accepts no other writes).

## Classic Bluetooth side (controllers)

- The ESP32 is Classic-discoverable (`gap_discoverable_control(1)`) and accepts
  incoming HID connections.
- **Controllers are identified by name** (`Motion controller - Left` /
  `- Right`, see [DISCOVERY.md](DISCOVERY.md)) and their addresses are then stored
  in NVS. A name is not authentication and can be spoofed by any device. While a
  hand has no stored address, a device that uses its name during discovery, or
  connects in with its name, becomes that hand and is paired with automatically.
  This window exists only while a hand has no address, and discovery is bounded
  (`WMR_CFG_DISCOVERY_ROUNDS`).
- Once both hands have addresses, incoming HID connections are accepted only from
  those two addresses; others are declined. A stored address is never replaced
  automatically. A Bluetooth address is still not a secret and can be spoofed.
- Optional compile-time addresses (`WMR_CFG_LEFT_ADDRESS` / `WMR_CFG_RIGHT_ADDRESS`)
  skip discovery for that hand. The repository ships none.
- Secure Simple Pairing numeric comparison is **accepted automatically for any
  device** that asks (`gap_ssp_confirmation_response`). This is a test
  convenience; it provides no protection against man-in-the-middle pairing.
- Legacy PIN requests are rejected.

## Local

- Link keys are stored in flash without flash encryption
  (`CONFIG_FLASH_ENCRYPTION_ENABLED` is not set) and secure boot is not
  enabled. Anyone with physical access can read keys and replace firmware.
- The serial console is unauthenticated; the `k`/`K` keys delete bonds, `F` deletes
  the stored controller addresses (which reopens the discovery window).
- The stored controller addresses are in NVS without flash encryption.
- Controller motion data can be personal data (it reflects user movement).
  Do not capture or publish it casually; the sample frames in `samples/` are
  from bring-up sessions, are not decoded and contain no Bluetooth address.

## Before wider use

Require LE Secure Connections pairing with encrypted/authenticated
characteristic permissions, restrict the service to a bonded phone, replace
the experimental UUIDs, only auto-confirm pairing during an explicit
pairing window, require a console confirmation before a discovered controller is
stored, and enable flash encryption and secure boot.
