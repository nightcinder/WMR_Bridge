# Changelog

Releases before 0.3.0 are listed on the
[GitHub releases page](https://github.com/nightcinder/WMR_Bridge/releases).
This file records what each release contains; hardware evidence stays in
[`docs/STATUS.md`](docs/STATUS.md) and the run sheets.

## 0.3.3 — 2026-11-01

The ONY-699 review-pass release on top of 0.3.2. FF10–FF17 keep their
payloads, so companions at 0.2.2 or later continue to work, and the firmware
reports the tag version over FF16 (0.3.3 when tagged). Two firmware
behaviour fixes: the BLE HID gamepad now puts each control in its Android
usage slot, and a controller link is declared down after a 5 s supervision
timeout. Firmware and Pi artifacts remain CI-built and hardware-unverified.

### Firmware

- **HID gamepad button slots (#118, ONY-685).** Firmware 0.3.2 packed the
  companion's model order straight into HID bits, but Android maps Button
  usages 1–16 straight to `BTN_A`… and reserves usages 3 and 6, so every
  control after B appeared one or two slots early. `src/wmr_hid.c` now
  assigns bits by host usage number: A/B, X/Y, squeeze L1/R1, trigger
  L2/R2, menu Select/Start, Home Mode, stick click L3/R3; C and Z stay
  unused. The HID descriptor and report shape are unchanged.
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
- Both firmware changes are covered by host tests (`test_hid`, `test_ctrl`)
  and the triage change by `tests/test_triage_bundle.py`; no packaged firmware
  has been flashed to hardware.

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
