# Changelog

Releases before 0.3.0 are listed on the
[GitHub releases page](https://github.com/nightcinder/WMR_Bridge/releases).
This file records what each release contains; hardware evidence stays in
[`docs/STATUS.md`](docs/STATUS.md) and the run sheets.

## 0.3.16 — 2026-11-09

The Pi uplink and BLE advertising release on top of 0.3.15. The bridge now
prefers an Ethernet uplink over 5 GHz Wi-Fi and 2.4 GHz Wi-Fi, advertises the
FF10 GATT service by default with per-client CCCD gating, and the token file
access rules for `wifi_probe.py` are documented. Firmware payloads are unchanged
from 0.3.15: FF10–FF17 keep their payloads and companions at 0.2.2 or later
continue to work.

### Raspberry Pi bridge

- **Default always-on BLE advertising (ONY-1115).** The daemon advertises the
  FF10 GATT service by default. Each client controls its own notifications
  through the CCCD. Pass `--no-ble` to disable the BLE peripheral. `--ble-fallback`
  stays as a deprecated no-op alias and prints a deprecation warning. Slow
  advertising uses LEAdvertisement1 intervals of about 1000 to 1285 ms.
- **Uplink preference (ONY-1114).** The bridge picks the active uplink in this
  order: `eth0`, then `wlan0` on 5 GHz, then `wlan0` on 2.4 GHz. It uses route
  metrics (IPv4 and IPv6), not only autoconnect priority. `prefer_5ghz()`
  takes `eth0` into account and lowers the wired route metric. `hello` and
  `status` report the active uplink in a separate `uplink` object.
- **Token access for `wifi_probe.py` (ONY-1115).** Read
  `/etc/wmr-bridge/token` with `sudo python3 tools/wifi_probe.py --token-file /etc/wmr-bridge/token`, or as a
  user in the `wmr` group with `--token-file /etc/wmr-bridge/token`. The token file is mode 0640 and owned by
  `wmr:wmr`.
- **Probe discovery and Sitting P gates (ONY-1144).** `wifi_probe.py` gains
  `--discover` (UDP `WMR_DISCOVER_V1`, port 8765), `--json-out PATH`, and
  `--assert-gates`, which exits non-zero unless RTT p50 is below 15 ms,
  per-hand tick-based loss is at most 3 %, and sequence gaps are zero. A hand
  with no frames fails the loss and gap gates, so an idle run cannot pass. The
  probe prints the `hello` `uplink` object next to `wifi`.
- **Service hardening (ONY-1114).** `AF_NETLINK` is added to
  `RestrictAddressFamilies` in the systemd unit.

### Verification

- Host C tests, the Pi unittest suite and the ESP-IDF build run in CI before
  tagging. BLE advertising waits on ONY-1070 in three states (advertising only,
  connected-not-subscribed, notifying) with a pass bar of 0 gaps and at most
  0.5 percent loss.

## 0.3.15 — 2026-11-09

The Pi connectivity release on top of 0.3.14. The Pi bridge now prefers 5 GHz
without locking out 2.4 GHz-only hardware, answers UDP discovery probes with a
stable identity, and has an opt-in BlueZ BLE GATT fallback for when Wi-Fi is
unreachable. WMR controllers get a keep-alive on both targets: the ESP32
re-sends the FF13 streaming-enable pair on reconnect and every 10 s, and the Pi
re-sends the WMR `0x06` pair. FF10–FF17 payloads and companions at 0.2.2 or
later are unchanged. The BLE fallback is opt-in (`--ble-fallback`), has not run
on hardware, and waits on ONY-1070.

### ESP32 bridge

- **Controllers stay awake after reconnect (ONY-1062).** A reconnecting hand
  with a cached config block no longer re-reads it; the firmware re-sends the
  two streaming-enable commands for the fresh link, then repeats them every
  10 s while the hand is connected and READY. `WMR_CFG_KEEPALIVE_MS=0` disables
  the periodic timer. Host tests cover the reconnect and keep-alive phases.

### Raspberry Pi bridge

- **Wi-Fi band inspection and automatic 5 GHz preference (ONY-1091, ONY-1092,
  ONY-1093).** The daemon inspects the radio before connecting, keeps a
  5 GHz-capable adapter on 5 GHz, and treats a 2.4 GHz-only radio such as the
  CYW43439 in a Pi Zero 2 W as a warning-only no-op. `hello`, `status` and
  `wmr-bridge probe` report `freqMhz`, `band`, `supports5Ghz`,
  `contentionWarning` and `warning`. `--prefer-5ghz` runs as a one-shot action;
  the systemd unit and Debian installer stage the NetworkManager profile before
  start, and `--no-reconnect` keeps an SSH session on its current link.
- **UDP discovery and stable bridge identity (ONY-1068, ONY-1073).** The daemon
  answers `WMR_DISCOVER_V1 <nonce>` probes on UDP port 8765 with its bridge id,
  hostname, WebSocket port, path, hands and version, rate-limited per source.
  The `bridge_id` is stable across restarts, the reply never carries the token,
  and clients confirm `hello.bridge_id` before authenticating. mDNS
  advertisement remains optional (`--advertise-mdns`), and `docs/PI_BRIDGE.md`
  documents the client discovery contract.
- **Opt-in BlueZ BLE GATT fallback (ONY-1069).** `--ble-fallback` serves the
  same FF11, FF12 and FF14 payloads over a BlueZ GATT peripheral when Wi-Fi is
  unreachable, with `--ble-adapter` and `--ble-pairing-window` overrides. It is
  off by default, needs `python3-dbus-next`, and is not yet verified on
  hardware (ONY-1070).
- **Resilient service start and scan backoff (ONY-1057, ONY-1063).** The
  service clears a boot-time Bluetooth rfkill soft block as root before start
  and keeps retrying instead of exiting, caps discovery backoff, logs the LAN
  address and advertises mDNS. The daemon throttles single-hand scans and sends
  the WMR `0x06` keep-alive pair.

### Tooling

- CI adds CodeQL and publishes Semgrep SARIF to code scanning (ONY-1080), and
  libFuzzer harnesses cover the three byte-level parsers (ONY-1087).

### Verification

- Host C tests, the Pi unittest suite and the ESP-IDF build run in CI before
  tagging; the tag stages exactly nine release assets and the firmware reports
  the tag version over FF16. No packaged firmware has been flashed to hardware,
  the 5 GHz preference has no hardware run recorded, and the BLE fallback waits
  on ONY-1070.

## 0.3.14 — 2026-11-08

The Pi pairing-hardware release on top of 0.3.13. Automatic pairing now
registers a Just Works BlueZ agent and waits for the asynchronous pair reply,
which fixes the pairing failures caused by an unregistered agent and an early
`quit`, and `wmr-bridge status` plus new daemon log lines make pairing progress
visible. Firmware payloads are unchanged from 0.3.13: FF10–FF17 keep their
payloads and companions at 0.2.2 or later continue to work. Firmware and Pi
artifacts remain CI-built; the real BlueZ path has not run on a Raspberry Pi or
with real controllers yet.

### Raspberry Pi bridge

- **Auto-pairing works with real controllers (ONY-1050).** The BlueZ session
  now registers a `NoInputNoOutput` (Just Works) agent before scanning. Without
  one, `bluetoothctl` answered every `pair` with
  `org.bluez.Error.AuthenticationFailed` and no bond was made. The session also
  waits for each asynchronous pair reply before it sends `quit`; the previous
  code quit one second after issuing `pair`, which tore down the agent and
  aborted the attempt. Unpaired controllers known to BlueZ are left to the
  scan session instead of a separate agentless `pair`.
- **Visible pairing progress.** The daemon logs one `auto-pair: scanning ...`
  line per attempt, then `found`, `pairing`, `paired`, `trusting`, `connecting`
  and `connected`, and reports the BlueZ error on a failed `pair`. The new
  `wmr-bridge status` (also `--status`) prints the adapter's
  `powered`/`pairable`/`discovering` flags, each hand's hidraw node, and the
  BlueZ bond flags without needing the daemon.
- **Diagnostics in the Pi docs.** The Pi bridge guide, install guide and
  deployment runbook now name `wmr-bridge status`, the `journalctl -u
  wmr-bridge` diagnostic and the expected auto-pair log lines.
- Host tests model a real `bluetoothctl` session: they fail if pairing is
  attempted before the agent registers, or if `quit` precedes the pair reply.
  The real BlueZ path has still not run on a Raspberry Pi or with real
  controllers.

### Verification

- Host C tests and the 174 Python tests run in CI before tagging; the tag runs
  the ESP-IDF build and the Pi package job and stages exactly nine release
  assets. The firmware reports the tag version over FF16. No packaged firmware
  has been flashed to hardware, and the real BlueZ path has not run on a
  Raspberry Pi or with real controllers yet.

## 0.3.13 — 2026-11-08

The Raspberry Pi automation release on top of 0.3.12. The Pi package now
carries the release version instead of `0.2.4`, pairs WMR controllers by itself
through BlueZ, and the tag build refuses a package whose version disagrees with
the tag. Firmware payloads are unchanged from 0.3.12: FF10–FF17 keep their
payloads and companions at 0.2.2 or later continue to work. The web flasher now
writes the bootloader, partition table and application separately, and a
release carries the three application images as well (nine assets).
Firmware and Pi artifacts remain CI-built and hardware-unverified; automatic
pairing has run only against a mocked `bluetoothctl`.

### Raspberry Pi bridge

- **Automatic pairing (#164, ONY-1026; hardened in #166, ONY-1037).** The
  daemon scans, pairs, trusts and connects WMR controllers on its own, so the
  operator only puts each hand in pairing mode. It looks at allowed hands only,
  backs off idle scans, parses the `bluetoothctl` scan output in one session,
  logs adapter power or scan failures once, and stops paging hands that already
  have a `Paired: yes` + `Trusted: yes` bond. `--no-auto-pair` leaves pairing to
  the operator.
- **Package version and install (#165, ONY-1025; #166).** `pi/wmr_bridge` reports
  the root `VERSION` (`0.3.13`), and `build_package.sh`/`build_deb.sh` fail on a
  mismatch. The installer creates its staging directory readable by `_apt`,
  adds the `wmr` user to the `bluetooth` group, moves `bluez` to `Depends`, and
  starts the service from a checkout install. The systemd unit enables
  `bluetooth` and passes `--advertise-mdns`.
- **Tag/VERSION guard (#166).** The `package` job fails unless the tag equals
  `v$(cat VERSION)`, so a release tag cannot publish a `.deb` from a different
  version.

### Flasher and release assets

- **Three-image manifest (#161, ONY-1019; docs #162, ONY-1020).** The ESP Web
  Tools manifest writes `bootloader.bin` at `0x1000`, `partition-table.bin` at
  `0x8000` and `app.bin` at `0x10000` instead of the merged factory image at 0.
  Flashing with "Erase device" unchecked leaves NVS (`0x9000`–`0xFFFF`) alone,
  so the stored phone and controller pairings survive a reflash. Releases now
  attach the three application images beside the factory images, the `.deb`,
  `install.sh` and `SHA256SUMS`.

### Docs

- Pi setup and pairing rewritten for automatic pairing (#163, ONY-1027; #167,
  ONY-1038): the run sheet gains P11 (start unpaired, put a controller in
  pairing mode, unattended pair/trust/connect within 60 s, repeat for the other
  hand, and confirm `--no-auto-pair` does not pair). The guide and flasher page
  cover the token, omit the restart, state WMR-only support and say the app
  still takes host and port manually. The GATT probe is retired from the
  latency plan and ONY-23 is recorded as **Stay Flutter** (#160, ONY-1014).

### Verification

- Host C tests and Python tests run in CI before tagging; the tag runs the
  ESP-IDF build and the Pi package job, and stages exactly nine release assets.
  The firmware reports the tag version over FF16. No packaged firmware has been
  flashed to hardware, automatic pairing has not run on a real Pi or the real
  BlueZ service, and the Pi cannot yet read FF13 calibration from controllers
  ([#145](https://github.com/nightcinder/WMR_Bridge/issues/145)).

## 0.3.12 — 2026-11-07

The release-layout release on top of 0.3.11. A GitHub Release now carries six
user-facing assets instead of 28, the Pi install is one command, and the
Raspberry Pi setup guide is a page on the GitHub Pages site. Firmware payloads
are unchanged from 0.3.11: FF10–FF17 keep their payloads, companions at 0.2.2
or later continue to work, and the firmware reports the tag version over FF16
(0.3.12 when tagged). The Pi package stays `0.2.4`: only `install.sh` changed
in behaviour, and the packaged `pi/README.md` and `docs/PI_BRIDGE.md` carry the
new install steps.
Firmware and Pi artifacts remain CI-built and hardware-unverified.

### Release assets

- **Six assets per release (#155, ONY-997).** The `release` job in
  `.github/workflows/firmware.yml` stages only `wmr-bridge-factory.bin`,
  `wmr-bridge-left-factory.bin`, `wmr-bridge-right-factory.bin`,
  `wmr-bridge_<pi-version>_all.deb`, `install.sh` and `SHA256SUMS`. The
  per-variant app, bootloader and partition images, manifests, build-info
  JSON, tarball and per-file `.sha256` files stay in the workflow artifacts.
  Piped `pi/install.sh` fetches the `.deb`, verifies it against `SHA256SUMS`,
  installs it with `apt-get`, and enables and restarts `wmr-bridge.service`;
  releases attach no tarball, so a host without `apt-get` installs from a
  checkout or a locally built tarball. `tests/test_release_sums.py` and
  `pi/tests/test_packaging.py` cover the digest list and the installer URLs.

### Raspberry Pi Pages site

- **Pi setup page (#156, ONY-998).** `docs/flasher/pi.html` is a three-step
  guide (Pi Imager, one-line install, `bluetoothctl` pairing) with copy
  buttons in the flasher's dark theme; `docs/flasher/index.html` links it.
  The tag-time `pages` job publishes `index.html`, `pi.html` and `install.sh`,
  and `tools/firmware/build.py` copies every `docs/flasher/*.html` into
  `dist/`. `tests/test_firmware_package.py` checks the payload and the
  cross-links on every pull request.

### Triage

- **6DoF-only pose bundles and repeated anchor frames (#154, ONY-990).**
  `tools/triage_bundle.py` no longer requires `captures/` when the bundle
  carries pose recordings, treats a zero bridge count and a missing
  `latency/` as expected in `6dof_only` mode, and reports a pose file whose
  recorded hand repeats the preceding anchor frame. The fresh external bundle
  is recorded in
  [`docs/runs/sheets/android-xr-6dof-20261107.md`](docs/runs/sheets/android-xr-6dof-20261107.md).

### Docs

- Simple-English Raspberry Pi install guide (#153, ONY-993), Raspberry Pi and
  ESP32 flashing docs leading with the one-line installer and one factory
  image per variant (#158, ONY-999), run sheets trimmed and consolidated
  (#152 ONY-983, #150 ONY-980), latency and bench runs archived (#151
  ONY-979, #157 ONY-1006), and current-release lines refreshed for 0.3.11
  (#149 ONY-972).

### Verification

- Host C tests and Python tests run in CI before tagging; the ESP-IDF build
  runs on the tag, and firmware reports the tag version over FF16. The
  release job stages exactly the six assets above and the Pages job publishes
  `index.html`, `pi.html` and `install.sh`. No packaged firmware has been
  flashed to hardware, and the Switch path has never run on a controller
  ([#145](https://github.com/nightcinder/WMR_Bridge/issues/145)).

## 0.3.11 — 2026-11-07

The controller-family release on top of 0.3.5. Firmware gains a per-model
driver table, PSVR2 FF12/FF14 decoding and Nintendo Switch Joy-Con/Pro
support; the Pi host bridge forwards FF13 calibration blobs over
`wmr-wifi-v1`. FF10–FF17 keep their payloads, so companions at 0.2.2 or later
continue to work, and the firmware reports the tag version over FF16 (0.3.11
when tagged). **Switch support is host-tested and firmware-build-tested only
and has not run on a controller ([#145](https://github.com/nightcinder/WMR_Bridge/issues/145)).**
The Pi package is `0.2.4`: it caches and forwards FF13 blobs but does not yet
read calibration from a controller. Firmware and Pi artifacts remain CI-built
and hardware-unverified. (Tags `v0.3.6`–`v0.3.10` are WMR Companion releases in
this repository, so `0.3.11` is the next free Bridge version.)

### Firmware

- **Driver table and PSVR2 FF12/FF14 decode (#142, ONY-945).**
  `src/wmr_controller_driver.{h,c}` holds one entry per model with its
  Bluetooth names, input-report predicate, FF12/FF14 decoder, wake report and
  FF13 support; discovery, classification and decoding look it up instead of
  branching on the model. A 79-byte `A1 31` PSVR2 frame now decodes into the
  same 56-byte record as a WMR frame (gyro `pi/1024` rad/s, accel
  `9.80665/4096` m/s², 333 ns IMU ticks converted to 100 ns), and a frame that
  is not exactly 79 bytes is counted in `dec_err`. Covered by `test_decode`,
  `test_discovery` and `test_protocol`; never run on a PSVR2 controller.
- **Switch Joy-Con/Pro support (#144, ONY-946).** Discovery matches
  `Joy-Con (L)`, `Joy-Con (R)` and `Pro Controller`; after
  `DESCRIPTOR_AVAILABLE` the bridge sends HID output report `0x01`
  subcommands (enable the 6-axis sensor, read the two SPI calibration blocks,
  select 60 Hz `0x30` mode) with NACK/short-reply retry. The SPI calibration
  is packed into FF13, and each `0x30` report yields three IMU/stick samples in
  FF12/FF14. `tests/test_switch.c` covers the handshake sequence, calibration
  packing and sample unpacking; the `WMR_MODEL_SWITCH` model bit is stored in
  the address record. Not run on a controller; hardware verification is
  tracked in [#145](https://github.com/nightcinder/WMR_Bridge/issues/145).
- **Internal codec target (#141, ONY-944).** The wire-format constants live in
  `src/wmr_codec.h` and build into a static `wmr_codec` target, so host tests
  and the firmware share one definition without `wmr_protocol.h` or
  `wmr_psvr2.h` pulling in `wmr_config.h`. No payload change.

### Pi host bridge

- **FF13 calibration forwarding over `wmr-wifi-v1` (#140, ONY-931).** `hello`
  advertises `calibration` (version, 16-byte header, 496-byte window and
  message tag `0x13`); a `{"type":"calibration","hand":...,"offset":N}` request
  returns one tagged binary window from a per-hand cache, and `hello`/`status`
  report each hand's state and blob CRC. The daemon answers `not_read` until an
  ingest layer supplies a blob, because the Pi-side controller read over
  hidraw is not implemented: **the Pi does not yet read FF13 calibration from
  controllers.** `pi/tests/test_protocol.py` and `pi/tests/test_ws.py` cover
  the windows, CRC and WebSocket forwarding.
- **Package `0.2.4` (#140, ONY-931).** The Pi package version moves from
  `0.2.3` to `0.2.4` because `hello` gains the calibration fields. The `.deb`
  and tarball asset names follow `wmr_bridge.__version__`.

### CI and build

- **Blocking security scans and Pi package verification (#136, ONY-929).**
  gitleaks and Semgrep no longer run with `continue-on-error`, and Semgrep uses
  `--error`, so a finding holds the merge. The host-tests workflow also builds
  the Pi tarball and `.deb` and verifies the tarball before the dry run.
- **VERSION file (#137, ONY-928).** The root `VERSION` file is the version
  source when git has no tag to describe: `tools/firmware/build.py` reads it
  after `git describe`, and `tests/run_c_tests.sh` stamps it into the host-test
  application descriptor. This release sets it to `0.3.11`.

### Docs

- Internal ESP32/WMR bench runbook and GATT probe steps (#139, #146, #147), the Pi
  3B+ bench kit (#135), the 12-package compatibility refresh (#143), tester
  sheets retargeted to Bridge 0.3.5 and Companion 0.3.17 (#134), and the
  current-release lines refreshed for 0.3.5 (#138).

### Verification

- Host C tests and Python tests run in CI before tagging; the ESP-IDF build
  runs on the tag, and firmware reports the tag version over FF16.
  `tests/test_switch.c`, `tests/test_decode.c` and `pi/tests/test_protocol.py`
  / `pi/tests/test_ws.py` cover the new decode and forwarding paths. No
  packaged firmware has been flashed to hardware, and the Switch path has
  never run on a controller ([#145](https://github.com/nightcinder/WMR_Bridge/issues/145)).

## 0.3.5 — 2026-11-06

The FF10 encryption-enforcement release on top of 0.3.4. FF10–FF17 keep their
payloads, so companions at 0.2.2 or later continue to work, and the firmware
reports the tag version over FF16 (0.3.5 when tagged). The FF10 data
characteristics now name a 16-byte encryption key size, so BTstack rejects an
unencrypted or shorter-key access; the bridge already required a bonded LESC
link. Triage also moves to the 6-run pose protocol, and the repository gains
report-only secret and static-analysis scans. Firmware and Pi artifacts remain
CI-built and hardware-unverified.

### Firmware

- **FF10 encryption enforcement (#132, ONY-889).** `wmr_bridge.gatt` now marks
  every FF10 data characteristic and its CCC `ENCRYPTION_KEY_SIZE_16` as well
  as `WRITE_ENCRYPTED`/`READ_ENCRYPTED`. BTstack rejects an unencrypted access
  only when the attribute's key-size nibble is non-zero, so the earlier
  encryption flags (firmware 0.3.0–0.3.4) did not by themselves enforce
  encryption. FF16, the GAP/GATT attributes, the DIS fields and the HID boot
  reports stay readable before pairing; none carries motion or controller
  configuration. [`docs/SECURITY.md`](docs/SECURITY.md) records the wider
  plaintext surface and the 16-byte requirement, and
  [`docs/PROTOCOL.md`](docs/PROTOCOL.md) matches the attribute flags.
  `tests/test_gatt_encryption.py` checks the compiled attribute table against
  the GATT source; no host test replaces a radio run.

### Triage

- **6-run pose bundles (#129, ONY-870).** `tools/triage_bundle.py` accepts
  non-occlusion pose runs of 14 s or longer (15 s bench timer with boundary
  tolerance) and treats a six-run, one-run-per-motion bundle as complete. The
  pose-filter sheet moves from the 42-run schedule in filter-spec rev 4 §8 to
  ONY-868 Option A. Regression tests cover the floor and the bundle shape in
  `tests/test_triage_bundle.py`.

### CI

- **Secret and static-analysis scans (#130, ONY-886).** `security.yml` runs
  gitleaks and Semgrep on pull requests and pushes to `main`, and Dependabot
  covers the GitHub Actions and pip ecosystems. Both scanners are report-only
  (`continue-on-error`), so a finding is visible in the checks list without
  blocking a merge.

### Verification

- Host C tests and Python tests run in CI before tagging; the ESP-IDF build
  runs on the tag, and firmware reports the tag version over FF16.
  `test_gatt_encryption` covers the key-size and encryption flags on all FF10
  data attributes and their CCCs, and `test_triage_bundle` covers the 14 s
  floor and 6-run bundle. No packaged firmware has been flashed to hardware.

## 0.3.4 — 2026-11-02

The in-page flasher release on top of 0.3.3. No firmware payload or protocol
change: the hosted browser flasher can pair controllers and show packet
counters without a serial console, and the tester docs pin the Companion
releases that match. Firmware and Pi artifacts remain CI-built and
hardware-unverified.

### Flasher

- **In-page controller pairing and packet counters (#127, ONY-799).**
  `docs/flasher/index.html` can request the controller bonds from the running
  firmware and shows per-hand packet counters, so a controller can be paired
  from the browser instead of a serial console. The flasher keeps the existing
  three-variant images and only resets a bond after the link closes.
  `tests/test_firmware_package.py` covers the counter parser and the packaged
  page.

### Docs

- **Web flasher pairing workflow (#128, ONY-800).** [`README.md`](README.md)
  and [`docs/DISCOVERY.md`](docs/DISCOVERY.md) document the in-page pairing
  steps and what to check when a hand does not bind.
- **Tester docs.** The external-tester sheet and runbooks pin Companion
  v0.3.7, v0.3.8 and v0.3.10 and Bridge v0.3.3 as those releases ship
  (#123–#126; ONY-724, ONY-738, ONY-743, ONY-762).

## 0.3.3 — 2026-11-01

The ONY-699 review-pass release on top of 0.3.2. FF10–FF17 keep their
payloads, so companions at 0.2.2 or later continue to work, and the firmware
reports the tag version over FF16 (0.3.3 when tagged). Two firmware
behaviour fixes: the BLE HID gamepad now puts each control in its Android
usage slot, and a controller link is declared down after a 5 s supervision
timeout. Firmware and Pi artifacts remain CI-built and hardware-unverified.

### Firmware

- **HID gamepad button slots (#118, ONY-685).** Firmware 0.3.0–0.3.2 packed
  the companion's model order straight into HID bits, but Android maps Button
  usages 1–16 straight to `BTN_A`… and reserves usages 3 and 6, so X/Y landed
  one usage early, squeeze L1/R1 and trigger L2/R2 two, and the stick clicks
  L3/R3 five (bits 8/9 arrived as L2/R2); Select/Start/Mode were already
  correct. `src/wmr_hid.c` now assigns bits by host usage number: A/B, X/Y,
  squeeze L1/R1, trigger L2/R2, menu Select/Start, Home Mode, stick click
  L3/R3; C and Z stay unused. The HID descriptor and report shape are
  unchanged. The file is the same from 0.3.0 through 0.3.2, so the mis-mapping
  was shipped in all three releases.
- **5 s disconnect detection and FF17 CONNECT ACK (#119, ONY-686).**
  `HCI_Write_Link_Supervision_Timeout` now applies the 5 s
  `WMR_CFG_CLASSIC_SUPERVISION_TIMEOUT` per ACL while the bridge is master,
  deferred and retried from `HCI_EVENT_ROLE_CHANGE` when a controller-
  initiated reconnect arrives with the bridge as peripheral (where the
  command is disallowed). FF17 CONNECT is idempotent for a hand that is
  already open/opening and returns BUSY while a requested disconnect is
  still closing. The FF17 CONNECT contract is updated in
  [`docs/PROTOCOL.md`](docs/PROTOCOL.md) and the bench step is in
  [`docs/RECONNECT_AND_QUEUE.md`](docs/RECONNECT_AND_QUEUE.md).

### Triage

- **Multi-run pose captures (#120, ONY-710).** `tools/triage_bundle.py`
  accepts `pose_capture_<runtime>_<hand>_<segment>_runN.json` for repeats
  beyond the first run, so occlusion runs and multi-word segment names are
  no longer skipped. Regression tests cover runs 1–4.

### Docs

- **Companion pin and BLE HOGP coexistence (#121, ONY-711).** The tester
  runbooks pin WMR Companion v0.3.6 (2011), and
  [`docs/HID_GAMEPAD.md`](docs/HID_GAMEPAD.md) explains that the firmware's
  HOGP gamepad (`0x1812`) and the companion's `BluetoothHidDevice` output
  must not be paired at the same time.
- **Docs refresh (#115–#117).** A single 0.3.2 no-reflash bench checklist,
  run sheets retargeted to v0.3.2 / Pi 0.2.3 / Companion 0.3.5, and a
  plain-English pass over the README and docs.

### Verification

- Host C tests and Python tests run in CI before tagging (PR/main); the
  ESP-IDF build runs on the tag; firmware reports the tag version over FF16.
- `test_hid` covers the HID button-slot remap and `test_ctrl` the FF17
  CONNECT OK/BUSY gate; no host test exercises the 5 s supervision-timeout
  write, so that half of #119 is checked by review only and stays
  hardware-unverified. The triage change is covered by
  `tests/test_triage_bundle.py`. No packaged firmware has been flashed to
  hardware.

## 0.3.2 — 2026-10-31

A release-infrastructure and Pi-packaging release on top of 0.3.1. No
protocol change: FF10–FF17 keep their payloads, so companions at 0.2.2 or
later continue to work, and the firmware reports the tag version over FF16
(0.3.2 when tagged) with no behaviour change from 0.3.1. The Linux/
Raspberry Pi host bridge package is `0.2.3`. Firmware and Pi artifacts
remain CI-built and hardware-unverified.

### CI

- **All-asset `SHA256SUMS` (#112, ONY-653).** The release job now writes one
  `SHA256SUMS` covering every attached asset (firmware images, `.deb`,
  tarball, installer, per-file `.sha256`), verified against the files before
  the release runs; `tools/release/make_sums.sh`.
- **Repo checkout in the release job (#112, ONY-653).** The release runner
  checks out the repository so `make_sums.sh` is available.

### Pi host bridge

- **`wmr-bridge` on `PATH` + package `0.2.3` (#113, ONY-654).** The `.deb`
  and installer put a `wmr-bridge` launcher on `PATH`
  (`/usr/bin/wmr-bridge`); `wmr-bridge --version` prints
  `wmr_bridge 0.2.3`. Package bump `0.2.2` → `0.2.3`.

### Tooling tests

- **Release-sums host test (#112, ONY-653).** `tests/test_release_sums.py`
  covers the all-asset digest list.
- **Pi packaging/deb host tests (#113, ONY-654).** `pi/tests/test_deb.py`
  and `pi/tests/test_packaging.py`.

### Verification

- Host tests run in CI before tagging (PR/main); the ESP-IDF build runs on
  the tag; firmware reports the tag version over FF16.
- Pi package is `0.2.3`; protocol unchanged.

## 0.3.1 — 2026-10-31

A packaging and tester-docs release on top of 0.3.0. No protocol change:
FF10–FF17 keep their payloads, so companions at 0.2.2 or later continue
to work, and the firmware reports the tag version over FF16 (0.3.1 when
tagged) with no behaviour change from 0.3.0. The Linux/Raspberry Pi host
bridge package stays at `0.2.2`. Firmware and Pi artifacts remain CI-built
and hardware-unverified.

### Pi host bridge

- **Pi bridge `.deb` + one-line installer (#103, ONY-550).** `pi/` now
  builds a `.deb` and a release-fetching one-line installer.
- **Pi release attachments (#104, ONY-551).** CI attaches the Pi bridge
  `.deb`, tarball and installer to `v*` releases.
- **RPi deployment and update runbook (#105, ONY-552).** Tester-facing RPi
  deployment and update documentation.
- **Pi package `0.2.2` (ONY-639).** Packaging/installer fixes with no
  runtime change.

### Tester docs and tooling

- **Tester quickstart + pose-filter sheet (#100, ONY-527; #101, ONY-531).**
  Tester quickstart plus the pose-filter sheet carrying the filter-spec rev 4
  §7 grid.
- **Tester-bundle triage CLI (#106, ONY-583).** CLI for triaging tester
  bundles.
- **Flag 6DoF capture runs shorter than 30 s (#109, ONY-634).**
- **ONY-439 sheet refresh + TESTING.md leftovers (#107, ONY-601).** App
  target and testing-doc cleanup.

### CI

- **Workflow actions to current Node 24 majors (#99, ONY-521).**
- **Firmware build on the Liesa self-hosted runner (#108, ONY-605).**

### Housekeeping

- **Truth-up test counts and current release (ONY-640).** Pi test count
  corrected to 122; host checks to 6127.

### Verification

- Host tests and the ESP-IDF build run in CI on the tag; firmware reports
  the tag version over FF16 and in the console `v` line.
- Pi package remains `0.2.2`; protocol unchanged.

## 0.3.0 — 2026-10-29

A security and phone-link release. No protocol version change: FF10–FF17 keep
their payloads, so companions at 0.2.2 or later continue to work. The phone
link now requires LE Secure Connections with bonding, so earlier companions,
which pair without bonding, cannot connect. Firmware artifacts are
CI-built and hardware-unverified; the 0.3.0 bench sitting is
[`docs/runs/sheets/ony-439-android-verification.md`](docs/runs/sheets/ony-439-android-verification.md).

### Phone link

- **LE Secure Connections with bonding (#88, ONY-438).** The bridge requires
  LESC + bonding on the phone link, sends a Security Request as soon as a
  central connects, and encrypts every data characteristic except FF16 (the
  version stays readable before pairing). A stale bond is deleted and the
  phone can pair again; a central that does not become bonded within 30 s is
  disconnected. Just Works pairing is encrypted but not MITM-protected — see
  [`docs/SECURITY.md`](docs/SECURITY.md).
- **BLE HID gamepad (#89, ONY-441).** One standard HID-over-GATT gamepad
  (service `0x1812`, Report ID 1) shares the phone link, so Android binds the
  controllers with no app. CTKD is disabled so a dual-mode phone keeps the LE
  HID transport; the bridge advertises from a static random LE address and
  exposes DIS `0x180A`. See [`docs/HID_GAMEPAD.md`](docs/HID_GAMEPAD.md).
- **GATT security docs and pair-timeout state (#90, ONY-449).** The GATT
  documentation now matches the descriptor, and a pair timeout reports the
  real link state.

### Controller link

- **ONY-446: encrypt a controller ACL that stalls after discovery (#92).** A
  controller that opens its own ACL after `discovery finished` now gets
  `HCI_Set_Connection_Encryption` from the bridge after 750 ms without
  encryption, and the ACL is dropped after 4 s without a HID channel so the
  controller pages again. This removes the 18 s stall that needed a board
  reset on 0.2.4. See [`docs/DISCOVERY.md`](docs/DISCOVERY.md).
- **FF17 `FORGET_BOND` retry recorded (#95).** The Android run sheet now has
  the confirmed `0x05 0x01` → `last_result` `0x00` case, and the earlier
  `05/06` result is explained as a write longer than two bytes (ATT `0x86`),
  not a firmware defect.

### Bench and diagnostics

- **Status LED map (#96).** Red = no controller, yellow = LEFT only, blue =
  RIGHT only, green = both. Red at boot until a controller connects; GPIO 4,
  WS2812/NeoPixel.
- **LEVERS runtime contention toggles (#86, ONY-430).** `l`, `v`, `t` and `q`
  switch the contention levers from the console without rebuilding. The bench
  image was rebuilt on post-ONY-446 master (#97); see
  [`docs/runs/sheets/single-esp32-levers.md`](docs/runs/sheets/single-esp32-levers.md).
- **CI always reports `Build firmware` (#93).** The workflow no longer has a
  paths filter, so the required check runs on every PR.
- **GATT docs cross-checked against `wmr_bridge.gatt` (#94).**

### Pi host bridge

- **Packaging and a no-hardware dry run (#87, ONY-440).** `pi/build_package.sh`
  builds `dist/wmr-bridge-<version>.tar.gz` and its `.sha256`;
  `pi/tools/verify_package.sh` checks an archive and `pi/tools/dry_run.sh`
  exercises the daemon without hardware. The Pi package version stays `0.2.1`
  (out of scope for this release), and the 0.3.0 release does not attach a Pi
  tarball — the only packaged Pi tarball remains on `v0.1.0`. See
  [`docs/PI_BRIDGE.md`](docs/PI_BRIDGE.md).

### Verification

- Host tests and the ESP-IDF build run in CI on the tag; the firmware reports
  `0.3.0` over FF16 and in the console `v` line.
- One flashed board reading FF16 = `0.3.0` is still pending; the bench sheet
  records it as user-reported evidence once run.
