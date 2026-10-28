# Security posture and threat model

wmr_bridge is a **bench and test bridge**. 0.3 hardens the phone link, but the
bridge is still not a hardened product: the trusted environment is the owner's
own room and LAN, and the remaining gaps below are deliberate.

Status of this page: describes the 0.3.0 firmware (LESC + bonding, encrypted
characteristics). The pairing run on Nick's phone + ESP32 is the acceptance
test and is recorded in [STATUS.md](STATUS.md) once done; host tests and the
firmware build do not prove radio behavior.

## What is protected

| Asset | Where it lives | Why it matters |
| --- | --- | --- |
| Controller motion stream (FF11/FF12/FF14) | BLE notifications | Reflects the user's movement; personal data in aggregate |
| Controller config/calibration (FF13) | GATT read/write | Device-specific data; writes change controller behavior |
| Echo and control/status (FF15/FF17) | GATT write/notify | Control plane; a forged control write can start discovery or disconnect a hand |
| BLE long-term keys | ESP32 NVS (LE device DB) | Whoever reads them can impersonate the bonded phone |
| Classic link keys and controller addresses | ESP32 NVS (Classic link key DB, NVS) | Whoever reads them can impersonate a controller |
| Pi bearer token | mode-600 file on the Pi | Authorises WebSocket clients |

## Threat model

| # | Attacker | Capability | 0.3 mitigation | Residual risk |
| --- | --- | --- | --- | --- |
| T1 | Passive observer in radio range | Sniffs BLE advertising and data | Data characteristics require an encrypted bonded link; LESC key exchange is not derivable from traffic | Advertising still reveals the bridge's presence and name; traffic volume/timing is visible |
| T2 | Unbonded central in range | Connects, reads FF11, subscribes, blocks the phone | Every data characteristic and CCC requires encryption; the bridge requests security on connect and disconnects a central that is not bonded within 30 s | A central can still connect, pair (Just Works) and *become* the bonded phone if it is first in range during a pairing window |
| T3 | Active MITM during pairing | Relays the LESC exchange | LESC resists passive key recovery, but the bridge has no display/keypad, so pairing is Just Works and is **not** MITM-protected | A determined relay during the first pairing can impersonate the bridge. Pair in a private place; check the phone's "paired device" entry afterwards |
| T4 | Any BLE device | Spoofs a controller name during discovery | Unchanged from 0.2: a name is not authentication. Discovery is bounded, and stored addresses are never replaced automatically | A spoofed controller can claim a hand while that hand has no stored address |
| T5 | On-path attacker on the Wi-Fi LAN | Sniffs the Pi WebSocket | Bearer token + loopback default + origin rejection; TLS is terminated externally (below) | Plaintext `ws://` on a hostile LAN is readable; the token is replayable by anyone who sees it |
| T6 | Someone with the phone | Installs a malicious app | Android's own app sandbox and the OS pairing UI | A paired app can read the stream; the bridge has no per-app authorization |
| T7 | Physical access to the ESP32 | Reads flash, replaces firmware | None yet: flash encryption and secure boot are **off** (plan below) | Full key and firmware compromise; treat a stolen board as burned |
| T8 | Random internet host | Reaches the Pi daemon | Loopback default, token required off-loopback, browser origins rejected, 5 s handshake bound | Never port-forward 8765; the daemon has no rate limiting |

## BLE side (phone)

0.3 requires **LE Secure Connections with bonding**:

- `sm_set_io_capabilities(NO_INPUT_NO_OUTPUT)` plus
  `sm_set_authentication_requirements(SECURE_CONNECTION | BONDING)` and
  `sm_set_secure_connections_only_mode(true)`. Legacy pairing is refused during
  the pairing exchange (SM reason `Authentication Requirements`).
- `sm_set_request_security(true)`: the bridge sends a Security Request as soon as
  a central connects, so a fresh phone gets the pairing dialog without the app
  needing a special call.
- Every data characteristic except FF16 is `WRITE_ENCRYPTED` in
  `wmr_bridge.gatt`; the readable ones (FF11, FF13, FF17) are also
  `READ_ENCRYPTED`. FF12 and FF14 are notify-only and FF15 is
  write-without-response plus notify (the phone writes the 4-byte ping), so none
  of them has a read permission. The CCC descriptors inherit the write
  requirement, so notifications cannot be enabled on an unencrypted link either.
  FF16 (firmware
  version) stays readable so a client can check compatibility before pairing.
- After a pairing or re-encryption result, the bridge requires both success and a
  stored bond (`sm_le_device_index`). A central that pairs "encrypt only", fails
  pairing, or fails re-encryption for any reason other than a stale key is
  disconnected, not served. When re-encryption fails with `PIN_OR_KEY_MISSING`
  (the phone forgot the bridge), the bridge deletes its stale bond, keeps the
  link and lets the phone's fresh pairing complete; the same
  `WMR_CFG_BLE_PAIR_TIMEOUT_MS` (30 s) bound applies, and a central that still
  produces no bonded link is disconnected.
- Only one phone is served at a time. A second central is disconnected on
  connect, so it cannot block the intended phone by subscribing first.
- Bonds are stored in the LE device DB in NVS. `P` on the console deletes all
  phone bonds (refused while connected); `k`/`K`/`b` are the controller
  (Classic) resets and do not touch the phone bond.

**Residual BLE risks.** Just Works pairing is encrypted but not
MITM-protected (T3), which is unavoidable without a display or keypad on the
ESP32. Anyone in range can still take the phone slot by pairing first (T2), and
the device remains discoverable. The bridge has no rate limiting and no
per-central authorization beyond the bond. The FF10 UUIDs are still
experimental and unassigned; the 128-bit replacement and its staged cutover are
in [BLE_MIGRATION.md](BLE_MIGRATION.md).

**Migration.** 0.3 keeps the existing FF10 UUIDs so a 0.2 companion still
discovers the bridge; it now has to survive the Android pairing flow. The
uuid cutover (Phase A/B/C) is specified in [BLE_MIGRATION.md](BLE_MIGRATION.md)
and Phase C needs an explicit owner decision on breaking old bridges.

## Classic Bluetooth side (controllers)

Unchanged from 0.2:

- The ESP32 answers Classic pages (page scan) and accepts incoming HID connections, but
  it is not Classic-discoverable (`WMR_CFG_CLASSIC_DISCOVERABLE=0`): the bridge finds the
  controllers by inquiry and pages them, and a discoverable dual-mode device makes a phone
  pair over BR/EDR, where there is no profile for it. See [HID_GAMEPAD.md](HID_GAMEPAD.md).
- Controllers are identified by name (`Motion controller - Left` / `- Right`,
  see [DISCOVERY.md](DISCOVERY.md)); addresses are stored in NVS. A name is not
  authentication and can be spoofed while a hand has no stored address.
- Once both hands have addresses, incoming HID connections are accepted only from
  those addresses; others are declined. A stored address is never replaced
  automatically. A Bluetooth address is not a secret and can be spoofed.
- Optional compile-time addresses (`WMR_CFG_LEFT_ADDRESS` /
  `WMR_CFG_RIGHT_ADDRESS`) skip discovery. The repository ships none.
- Secure Simple Pairing numeric comparison is accepted automatically for any
  device that asks; it is a test convenience and provides no MITM protection.
  Legacy PIN requests are rejected.

## Linux / Raspberry Pi Wi-Fi host bridge (`pi/`)

The Pi bridge carries the same data over a Wi-Fi WebSocket instead of BLE. Its
mechanical safeguards were added in #47 / ONY-116:

- `--bind` defaults to `127.0.0.1`; a non-loopback bind requires a shared bearer
  token (`--token-file` preferred; mode `0600`, owned by `wmr`), otherwise the
  daemon exits before opening a socket.
- Requests with an `Origin` header are rejected unless that exact origin was
  allow-listed.
- The upgrade and token authentication must each complete within 5 s.
- The WebSocket is plaintext unless TLS is terminated in front of it. 0.3
  documents the supported TLS deployment (stunnel/nginx, `wss://`, certificate
  handling) in [PI_BRIDGE.md](PI_BRIDGE.md#tls-termination). Native TLS in the
  daemon is still future work; the token still authorises clients but does not
  encrypt anything on its own.
- BlueZ manages Classic pairing with the controllers. The bridge trusts the
  devices paired there and assigns LEFT/RIGHT by HID name, which is not
  authentication.

## Local / physical

- Link keys and the stored controller addresses are in NVS **without flash
  encryption** (`CONFIG_FLASH_ENCRYPTION_ENABLED` is not set), and secure boot is
  not enabled. Anyone with physical access can read keys and replace firmware
  (T7).
- The serial console is unauthenticated; the `k`/`K`/`b` keys delete controller
  bonds/keys, `F` deletes the stored controller addresses, and `P` deletes the
  phone bonds.
- Controller motion data can be personal data. Do not capture or publish it
  casually; the sample frames in `samples/` are from bring-up sessions, are not
  decoded and contain no Bluetooth address.

### ESP32 flash encryption and secure boot — plan (not enabled)

Both features burn eFuses and are **irreversible** on a given board, so they stay
off until the owner approves the change and a recovery path exists. The plan:

1. **Preconditions.** Pin ESP-IDF v6.1 (already pinned in CI and
   `tools/firmware/build.py`), freeze the partition table, and confirm the
   release image boots on a bench board from a clean NVS. Keep one board
   un-fused as the recovery/reflash unit.
2. **Approach.** Prefer **release-mode flash encryption** (hardware AES,
   encrypted NVS/OTA partitions) with **secure boot v2** (RSA-3072 signature on
   the bootloader and app). Do not use development-mode flash encryption for
   anything but the first bring-up: it leaves a plaintext-key window and a
   reflash escape hatch that release mode removes.
3. **Keys.** Generate the secure-boot signing key offline; do not commit it. Store
   one backup off the build machine (losing it means no future OTA). Add the
   public key digest to the build inputs and record it in the release manifest.
4. **Build changes (separate PR, after approval).** Add a `secure` build variant
   to `tools/firmware/build.py` that sets `CONFIG_SECURE_FLASH_ENC_ENABLED`,
   `CONFIG_SECURE_BOOT`, and the signing key path, and refuses to build without
   `--secure-approval <ticket>` so an accidental secure image cannot be flashed.
5. **Provisioning.** First boot with the fused release image encrypts flash and
   writes the bootloader digest. Verifying: `espefuse.py summary` shows
   `FLASH_CRYPT_CNT` odd / write-protected, `SECURE_BOOT_EN` set, and
   `idf.py monitor` shows the app booting with secure boot enabled. Record the
   eFuse summary and the app hash in STATUS.md.
6. **OTA and recovery.** OTA images must be signed; a failed OTA falls back to the
   previous signed slot. A fused board cannot be reflashed over USB with an
   unsigned image, so the flasher page must refuse fused boards and point at the
   signed OTA path.
7. **Approval gate.** Enabling either feature is a board-level, irreversible
   decision: get explicit owner approval in the issue, then do one board first,
   verify, and only then add it to the release procedure.

Until that lands, treat every board as readable and replaceable by anyone who
can hold it.

## Before wider use

- Pair and bond on the target phone, confirm an unbonded central is refused, and
  record the run (Nick, ONY-438 acceptance).
- Cut over the UUIDs per [BLE_MIGRATION.md](BLE_MIGRATION.md), including the
  owner decision on removing FF10.
- Terminate TLS on the Pi for any use beyond a trusted LAN.
- Enable flash encryption and secure boot through the approval gate above.
- Add rate limiting and a per-central authorization decision if the bridge ever
  leaves the owner's own environment.
