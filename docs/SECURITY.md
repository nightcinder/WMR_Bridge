# Security limitations

wmr_bridge is a **test bridge**. It is not hardened and must not be treated as
secure.

## 0.2 decision (M13.3, owner)

For 0.2 the owner chose to **keep the unauthenticated test bridge** and record
its limits rather than add pairing and encryption before the release
(`WMR_Bridge` #64, option A). This is an explicit temporary decision, not a
claim that the bridge is secure.

- **BLE:** no pairing, encryption or authentication is required, as described
  below. Use it only in a trusted environment: any central in range can read the
  stream, and an unwanted central that connects first blocks the intended phone.
  Controllers are trusted by HID name, which is not authentication.
- **Raspberry Pi:** the daemon keeps the plaintext WebSocket and the shared
  bearer token. Loopback remains the default, a non-loopback bind requires a
  token, and the stream is still unencrypted unless TLS is terminated externally
  (reverse proxy or SSH tunnel) or the Pi sits on a trusted WPA2/WPA3 LAN. The
  companion app warns about cleartext `ws://` in its Wi-Fi connect panel.
- **Platform security:** ESP32 flash encryption and secure boot remain off.
  **Raspberry Pi secure boot is out of scope for 0.2 and deferred to 0.3**; no
  companion feature or pairing flow depends on it.
- **After 0.2:** the work under [Before wider use](#before-wider-use) is the
  post-0.2 hardening path. Pi TLS and Pi secure-boot coverage move with the 0.3
  deferral.

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

**Multi-bridge (two ESP32 bridges).** In single-hand mode (`--variant left` /
`--variant right`) one bridge serves one hand, and two bridges advertise
concurrently as `WMR Bridge L` and `WMR Bridge R`. Each bridge keeps the same
unauthenticated, unencrypted GATT service described above, so an unwanted
central can hijack or block **one hand independently** while the other hand
continues streaming. Splitting the hands across radios does not add any
authentication or encryption. BLE pairing and encryption are **not required in
0.2** (owner decision, M13.3); see [0.2 decision](#02-decision-m133-owner).

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

## Linux / Raspberry Pi Wi-Fi host bridge (`pi/`)

The Linux/Pi host bridge in [`pi/`](../pi/) has a different security posture from
the ESP32 firmware: the phone link is Wi-Fi WebSocket instead of BLE. It was
hardened in #47 / ONY-116; the full contract is in
[PI_BRIDGE.md](PI_BRIDGE.md).

- `--bind` defaults to loopback (`127.0.0.1`). Binding to any non-loopback
  address requires a shared bearer token (`--token-file` is preferred; the file
  must be mode `0600` and owned by the service user, `wmr`), otherwise the
  daemon exits before opening a socket.
- Requests carrying an `Origin` header are rejected unless that exact origin was
  allow-listed (`--allow-origin`), so a browser page cannot open a socket.
- The HTTP upgrade and token authentication must each complete within 5 seconds;
  half-open connections are closed.
- The WebSocket is **plaintext**: the token authorises clients but does not
  encrypt controller data. The daemon has no TLS yet; terminate TLS externally
  (reverse proxy or SSH tunnel) or keep it on a trusted WPA2/WPA3 network.
- BlueZ on the host OS manages Classic Bluetooth pairing with the controllers.
  The bridge trusts the devices paired there and assigns LEFT/RIGHT by HID name,
  which is not authentication.

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
stored, and enable flash encryption and secure boot. None of this ships in 0.2;
it is the post-0.2 path recorded in [0.2 decision](#02-decision-m133-owner).
