# Firmware and Library Compatibility Matrix

This document defines the compatibility contract between the `WMR_Bridge` bridge targets — the default **ESP32-over-BLE** firmware (`src/`) and the second supported **Linux / Raspberry Pi-over-Wi-Fi** host bridge (`pi/`, [`docs/PI_BRIDGE.md`](PI_BRIDGE.md)) — and the `wmr-companion` Flutter/Dart client library packages, keyed on the **FF16 firmware version characteristic** (for BLE firmware) or the **WebSocket `hello.version` field** (for the Pi Wi-Fi bridge, which does not expose a GATT FF16 characteristic).

This document is the **canonical copy** of the compatibility matrix. The client library repository references this document from `docs/LIBRARY.md` §9 and `packages/wmr_companion/README.md` rather than maintaining a duplicate copy that could drift.

---

## Important Status Notices (Stated Explicitly)

1. **The client library is not published.**
   All seven `wmr-companion` Dart packages (`wmr_core`, `wmr_companion`, `wmr_ble`, `wmr_ws`, `wmr_fusion`, `wmr_companion_ui`, and `wmr_companion_hid_android`) are at version `0.1.0` in git source within a Dart pub workspace. Publication to pub.dev is explicitly **on hold by the owner's decision** (2026-10-23, ONY-41; see `wmr-companion` repository docs `docs/PUBLISHING.md` §5 and `docs/LIBRARY.md`). The package versions in this matrix refer strictly to **source/git checkout versions**, not pub.dev releases. None of these packages are available on pub.dev.

2. **The firmware releases `v0.1.0-rc1`, `v0.1.0` and `v0.2.0` are CI-built and hardware-unverified, and the Pi bridge is host-tests-only.**
   Firmware releases `v0.1.0-rc1` (commit `547ab09`, prerelease), `v0.1.0` and `v0.2.0` (latest release) are **hardware-unverified**: CI compiled and packaged the release artifacts (all three bridge variants plus the browser flasher), but no physical board flashed with these packaged releases has been tested or confirmed to function. `v0.2.1` is not yet released (pending PR #78). Likewise, the Linux/Raspberry Pi Wi-Fi host bridge (`pi/`, merged in #47) and the PSVR2 Sense controller path (merged in #46, searched by default from 0.2) are **host-tests-only and have never run on a Raspberry Pi or PSVR2 controller**. No row in this matrix implies hardware validation beyond what is explicitly noted in [`docs/STATUS.md`](STATUS.md).

---

## Compatibility Matrix (Keyed on FF16 Version / WebSocket `hello.version`)

The ESP32 BLE bridge exposes its firmware version over GATT characteristic `FF16` (`0000FF16-0000-1000-8000-00805F9B34FB`) as a read-only UTF-8 string with no terminator (up to 31 bytes). The Linux / Raspberry Pi Wi-Fi host bridge does not run a GATT server; it reports its software version in the initial WebSocket `hello` control message (`"version": "0.1.0"` on main, `"0.2.1"` after PR #78 merges, protocol 1 on `wmr-wifi-v1`), which `wmr_ws` surfaces through the `read(FF16)` seam without a network round trip.

The table below describes every bridge era/target and how the client library interacts with it:

| Version String (FF16 or WS `hello`) | Bridge Target, Era & Commits | Offered Characteristics / Streams | Compatible Dart Packages (Source `0.1.0`) | Client Behavior & Release Notes |
| --- | --- | --- | --- | --- |
| *(None / Absent)* | **ESP32-over-BLE (default):** Older than #38 (commits before `41771f8`) | FF11 (always), FF12 (since #5), FF14 (since #12), FF13 (since #17), FF15 (since #13) *(No FF16)* | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported.** The client facade treats missing FF16 as normal legacy firmware: `protocol.firmwareVersion` is `null` and `features.firmwareVersion` is `false`. FF11-FF15 operate normally per their respective arrival milestones. |
| `0.0.0+<sha>`<br>*(e.g. `0.0.0+41771f8`, `0.0.0+<sha>-dirty`)* | **ESP32-over-BLE (default):** Development builds between #38 (`41771f8`) and #40 (`547ab09` / `v0.1.0-rc1`) | FF11, FF12, FF14, FF13, FF15, FF16 | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported.** FF16 reports the git commit placeholder (`0.0.0+<sha>`) produced by `build.py` prior to tagging. Client parses the string into `protocol.firmwareVersion` and sets `features.firmwareVersion` to `true`. |
| `0.1.0-rc1`<br>*(or `0.1.0-rc1-N-g<sha>`)* | **ESP32-over-BLE (default):** `v0.1.0-rc1` (`547ab09`) and subsequent builds before `v0.1.0` (including #46 opt-in PSVR2 framework, `WMR_CFG_PSVR2_SUPPORT=0` by default) | FF11, FF12, FF14, FF13, FF15, FF16 | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported (Prerelease / Hardware-Unverified).** First build whose FF16 reports a real version string (`0.1.0-rc1`), and subsequent commits report git describe versions based on that tag. Introduces packaged LEFT/RIGHT/both bridge variants (`WMR Bridge`, `WMR Bridge L`, `WMR Bridge R`), factory binaries, and web flasher. PSVR2 Sense support (#46) is opt-in in this release (`-DWMR_CFG_PSVR2_SUPPORT=1`; removed in 0.2, where it is always compiled in), FF11 raw-only, and never run on a controller. |
| `0.1.0`<br>*(or `0.1.0-N-g<sha>`)* | **ESP32-over-BLE (default):** `v0.1.0` release and subsequent builds (includes #51 FF14/protocol fixes and #55 RGB status LED) | FF11, FF12, FF14, FF13, FF15, FF16 | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported (Release / Hardware-Unverified).** Tagged `v0.1.0` release; FF16 reports `0.1.0`. Ships packaged LEFT/RIGHT/both factory and split binaries and updates the live browser flasher. |
| `0.2.0`<br>*(or `0.2.0-N-g<sha>`)* | **ESP32-over-BLE (default):** `v0.2.0` release (PSVR2 searched by default, ONY-265; discovery retries, ONY-183; FF17 scanning/status v2, ONY-179) | FF11, FF12, FF14, FF13, FF15, FF16, FF17 | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported (Release / Hardware-Unverified).** Tagged `v0.2.0` release; FF16 reports `0.2.0`. PSVR2 Sense and WMR controllers searched by default; FF17 controller scanning/status v2. Firmware hardware runs remain unverified. |
| `0.2.1`<br>*(or `0.2.1-N-g<sha>`)* | **ESP32-over-BLE (default):** `v0.2.1` release (ONY-313 logging cleanup), pending PR #78 | FF11, FF12, FF14, FF13, FF15, FF16, FF17 | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ble`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Pending (PR #78, not yet released).** Single firmware log format and human-readable pairing diagnostics; `pair` tag for pairing problems; per-hand discovery summary. FF16 would report `0.2.1`. Firmware hardware runs remain unverified. |
| `0.1.0` in WebSocket `hello.version`<br>*(not over FF16; protocol `1`, `wmr-wifi-v1`)* | **Linux / Raspberry Pi-over-Wi-Fi (`pi/`, second supported target):** #47 (`079ef19`) and later ([`docs/PI_BRIDGE.md`](PI_BRIDGE.md), [`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md)) | WebSocket binary streams `raw` (FF11), `decoded` (FF12), `batched` (FF14); JSON `ping`/`pong` (FF15 analogue); `hello.version` (FF16 analogue). **No FF13** (calibration block not implemented). | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ws`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Supported (Host-Tests-Only; never run on Pi hardware or with controllers).** The Pi daemon reports its version in the WebSocket `hello` JSON (`"version": "0.1.0"`), not over a GATT FF16 characteristic. `WmrWsTransport` (`wmr_ws`) synthesizes FF10 (`FF11`/`FF12`/`FF14` from `hello.streams`), maps `ping`/`pong` to FF15, and serves `hello.version` on `read(FF16)` so `WmrCompanion` populates `protocol.firmwareVersion`. `features.configBlock` (FF13) is `false`; `mtu` reports `hello.fragmentCapacity` (244); `rssi`, `connectionIntervalUs`, and `phy` are `null`. |
| `0.2.1` in WebSocket `hello.version`<br>*(not over FF16; protocol `1`, `wmr-wifi-v1`)* | **Linux / Raspberry Pi-over-Wi-Fi (`pi/`):** 0.2.1 package (ONY-313), pending PR #78 | WebSocket binary streams `raw` (FF11), `decoded` (FF12), `batched` (FF14); JSON `ping`/`pong` (FF15 analogue); `hello.version` (FF16 analogue). **No FF13**. | `wmr_core`: `0.1.0`<br>`wmr_companion`: `0.1.0`<br>`wmr_ws`: `0.1.0`<br>`wmr_fusion`: `0.1.0`<br>`wmr_companion_ui`: `0.1.0`<br>`wmr_companion_hid_android`: `0.1.0` | **Pending (PR #78, not yet released).** Pi package would report `0.2.1`; logs hand connect/disconnect, client connect/disconnect, subscribe/unsubscribe, a one-time no-controller warning, and status lines shaped `left=up/1234(1200) right=down/0(0)`. Pi hardware runs remain unverified. |

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

All seven Dart packages in `wmr-companion` are maintained in a unified pub workspace at version `0.1.0`. All seven speak to the bridge targets in the matrix:

| Package | Kind | Git Source Version | pub.dev Status | Role |
| --- | --- | --- | --- | --- |
| `wmr_fusion` | Pure Dart | `0.1.0` | **On hold** (not published) | 6-axis Madgwick and Mahony sensor fusion filters. Zero dependencies. |
| `wmr_core` | Pure Dart | `0.1.0` | **On hold** (not published) | GATT UUIDs, packet parsers, tick-timing maths, domain models, controller pipelines, and abstract `BleTransport` seam. |
| `wmr_companion` | Dart / Flutter | `0.1.0` | **On hold** (not published) | Public facade (`WmrCompanion`). Reads FF16 on connect, exposes protocol version & features, and manages streams. |
| `wmr_ble` | Flutter Plugin | `0.1.0` | **On hold** (not published) | Concrete `UniversalBleTransport` using `universal_ble` for the ESP32-over-BLE bridge, connection lifecycle, and adapter state. |
| `wmr_ws` | Pure Dart | `0.1.0` | **On hold** (not published) | Concrete `WmrWsTransport` implementing `BleTransport` over WebSocket (`wmr-wifi-v1`) for the Linux/Raspberry Pi Wi-Fi host bridge. |
| `wmr_companion_ui` | Flutter (Optional) | `0.1.0` | **On hold** (not published) | Reusable diagnostic widgets: 3D controller orientation view, link quality stats, and gamepad visualization. |
| `wmr_companion_hid_android` | Flutter Plugin (Optional) | `0.1.0` | **On hold** (not published) | Optional Android-only Bluetooth HID device output (`BluetoothHidDevice`) for phone-as-gamepad routing. |
