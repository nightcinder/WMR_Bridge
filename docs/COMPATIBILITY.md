# Firmware and Library Compatibility Matrix

This document defines the compatibility contract between the `WMR_Bridge` firmware (ESP32) and the `wmr-companion` Flutter/Dart client library packages, keyed on the **FF16 firmware version characteristic**.

This document is the **canonical copy** of the compatibility matrix. The client library repository references this document from `docs/LIBRARY.md` §9 and `packages/wmr_companion/README.md` rather than maintaining a duplicate copy that could drift.

---

## Important Status Notices (Stated Explicitly)

1. **The client library is not published.**
   All six `wmr-companion` Dart packages (`wmr_core`, `wmr_companion`, `wmr_ble`, `wmr_fusion`, `wmr_companion_ui`, and `wmr_companion_hid_android`) are at version `0.1.0` in git source within a Dart pub workspace. Publication to pub.dev is explicitly **on hold by the owner's decision** (2026-10-23, ONY-41; see `wmr-companion` repository docs `docs/PUBLISHING.md` §5 and `docs/LIBRARY.md`). The package versions in this matrix refer strictly to **source/git checkout versions**, not pub.dev releases. None of these packages are available on pub.dev.

2. **The firmware release `v0.1.0-rc1` is an unverified prerelease.**
   Firmware release `v0.1.0-rc1` (commit `547ab09`) is explicitly flagged as a **prerelease** on GitHub and is not marked as "Latest". It is **hardware-unverified**: CI compiled and packaged the release artifacts, but no physical board flashed with this release has been tested or confirmed to function. No row in this matrix implies hardware validation.

---

## Compatibility Matrix (Keyed on FF16 Version)

The bridge exposes its firmware version over GATT characteristic `FF16` (`0000FF16-0000-1000-8000-00805F9B34FB`) as a read-only UTF-8 string with no terminator (up to 31 bytes).

The table below describes every firmware era and how the client library interacts with it:

| FF16 Version String | Firmware Era & Commits | Offered GATT Characteristics | Compatible Dart Packages (Source `0.1.0`) | Client Behavior & Release Notes |
| --- | --- | --- | --- | --- |
| *(None / Absent)* | Older than #38 (commits before `41771f8`) | FF11 (always), FF12 (since #5), FF14 (since #12), FF13 (since #17), FF15 (since #13) *(No FF16)* | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported.** The client facade treats missing FF16 as normal legacy firmware: `protocol.firmwareVersion` is `null` and `features.firmwareVersion` is `false`. FF11-FF15 operate normally per their respective arrival milestones. |
| `0.0.0+<sha>`<br>*(e.g. `0.0.0+41771f8`, `0.0.0+<sha>-dirty`)* | Development builds between #38 (`41771f8`) and #40 (`547ab09` / `v0.1.0-rc1`) | FF11, FF12, FF14, FF13, FF15, FF16 | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported.** FF16 reports the git commit placeholder (`0.0.0+<sha>`) produced by `build.py` prior to tagging. Client parses the string into `protocol.firmwareVersion` and sets `features.firmwareVersion` to `true`. |
| `0.1.0-rc1`<br>*(or `0.1.0-rc1-N-g<sha>`)* | `v0.1.0-rc1` (`547ab09`) and subsequent builds | FF11, FF12, FF14, FF13, FF15, FF16 | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported (Prerelease / Hardware-Unverified).** First build whose FF16 reports a real version string (`0.1.0-rc1`), and subsequent commits report git describe versions based on that tag. Introduces packaged LEFT/RIGHT/both bridge variants (`WMR Bridge`, `WMR Bridge L`, `WMR Bridge R`), factory binaries, and web flasher. |

*(Note: If a build is made directly with ESP-IDF `idf.py` instead of `tools/firmware/build.py`, ESP-IDF's default `PROJECT_VER` is `1`. The client treats `1` as a valid FF16 string without error.)*

---

## GATT Characteristic Capability Table

The capability matrix is discovered from the bridge's GATT table upon connection rather than inferred solely from the version string:

| Feature Flag (`WmrProtocolFeatures`) | Characteristic | UUID | When the bridge offers it | Client Notes |
| --- | --- | --- | --- | --- |
| `rawStream` | FF11 | `0000FF11-0000-1000-8000-00805F9B34FB` | Every known firmware | Raw controller HID reports fragmented into BLE notifications. Read returns a 16-byte status block. |
| `decodedStream` | FF12 | `0000FF12-0000-1000-8000-00805F9B34FB` | Every firmware with the decoded stream | Decoded 56-byte records. Mutually exclusive with FF11 and FF14. |
| `batchedStream` | FF14 | `0000FF14-0000-1000-8000-00805F9B34FB` | Firmware with batching | Batched decoded records. **Requires ATT MTU >= 61**: firmware accepts subscription but drops records as `mtu_skip` when MTU < 61; the client facade's stream selection requires ATT MTU >= 61 and surfaces ATT error `0x80` if refused by exclusion rules. |
| `configBlock` | FF13 | `0000FF13-0000-1000-8000-00805F9B34FB` | Firmware with the config block (`WMR_Bridge` #17) | Read-only access to the controller calibration/config block in 496-byte windows with CRC-32 verification. |
| `echo` | FF15 | `0000FF15-0000-1000-8000-00805F9B34FB` | Firmware with echo (`WMR_Bridge` #13) | Diagnostic ping/pong for round-trip latency measurement. Independent of stream mode. |
| `firmwareVersion` | FF16 | `0000FF16-0000-1000-8000-00805F9B34FB` | Firmware with #38 (`41771f8`) and later | Read-only UTF-8 version string (`PROJECT_VER`, max 31 bytes). Independent of stream mode. |

### Client Compatibility Rules
- **A bridge with no FF16 is an older firmware and a normal status:** `protocol.firmwareVersion` is `null` and `features.firmwareVersion` is `false`. The rest of the matrix still describes what that bridge can do.
- **Fail-soft protocol:** Unknown characteristics are ignored. Callers and client libraries must ignore records or fields their code does not recognize instead of treating them as fatal.
- **PHY is not a gap or to-do:** The bridge hardware is an original ESP32 (Bluetooth 4.2). 2M PHY is a Bluetooth 5.0 feature and cannot be supported by this chip. In client connection information, `BleConnectionInfo.phy` remains `null` on every platform because PHY is not observable by the host stack, not because 1M was confirmed by reading.

---

## Dart Package Breakdown (Source `0.1.0`)

All six Dart packages in `wmr-companion` are maintained in a unified pub workspace at version `0.1.0`. All six speak to the firmware builds in the matrix:

| Package | Kind | Git Source Version | pub.dev Status | Role |
| --- | --- | --- | --- | --- |
| `wmr_fusion` | Pure Dart | `0.1.0` | **On hold** (not published) | 6-axis Madgwick and Mahony sensor fusion filters. Zero dependencies. |
| `wmr_core` | Pure Dart | `0.1.0` | **On hold** (not published) | GATT UUIDs, packet parsers, tick-timing maths, domain models, controller pipelines, and abstract `BleTransport` seam. |
| `wmr_companion` | Dart / Flutter | `0.1.0` | **On hold** (not published) | Public facade (`WmrCompanion`). Reads FF16 on connect, exposes protocol version & features, and manages streams. |
| `wmr_ble` | Flutter Plugin | `0.1.0` | **On hold** (not published) | Concrete `UniversalBleTransport` using `universal_ble`, connection lifecycle, and adapter state. |
| `wmr_companion_ui` | Flutter (Optional) | `0.1.0` | **On hold** (not published) | Reusable diagnostic widgets: 3D controller orientation view, link quality stats, and gamepad visualization. |
| `wmr_companion_hid_android` | Flutter Plugin (Optional) | `0.1.0` | **On hold** (not published) | Optional Android-only Bluetooth HID device output (`BluetoothHidDevice`) for phone-as-gamepad routing. |
