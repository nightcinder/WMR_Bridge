# CLAUDE.md

## What this is

ESP32 firmware (C, ESP-IDF + BTstack) that bridges Windows Mixed Reality motion
controllers (also PSVR2 Sense and Switch Joy-Con/Pro) to an Android phone. It
hosts the controllers over Classic Bluetooth HID and forwards input over a
custom BLE GATT service (FF10). A second supported target, the Linux / Raspberry
Pi bridge in `pi/`, sends the same payloads over a WebSocket on Wi-Fi.

Phone side: [nightcinder/wmr-companion](https://github.com/nightcinder/wmr-companion)
(Flutter/Android). Its `docs/STATUS.md` holds overall state and hardware results.
A wire-format change here needs a matching change and version bump there.

- Target board: classic ESP32 (tested on rev v1.1, 2 MB flash; M5Stack ATOM Lite works).
- Toolchain: ESP-IDF v6.1, pinned BTstack commit (cloned into `.fw-build/` by the build script).
- Version lives in `VERSION` (currently 0.3.28). Release notes in `CHANGELOG.md`.

## Commands

Host tests (no ESP-IDF, no hardware; these are the CI truth, see `.github/workflows/host-tests.yml`):

```bash
./tests/run_c_tests.sh                       # all host C tests, -Wall -Wextra -Wconversion -Werror
CFLAGS="-O1 -fsanitize=address,undefined -fno-sanitize-recover=all -g" ./tests/run_c_tests.sh   # sanitizer job
cmake -B build && cmake --build build && ctest --test-dir build --output-on-failure
python3 tests/test_docs_links.py             # docs link check
python3 tests/test_doc_gatt.py               # docs vs wmr_bridge.gatt properties
python3 tests/test_gatt_encryption.py
python3 tests/test_analyze_capture.py
python3 -m unittest tests.test_triage_bundle -v
python3 tests/test_firmware_package.py
python3 tests/test_firmware_size.py          # size budget
python3 tests/test_release_sums.py
(cd pi && ./run_tests.sh)                    # Pi bridge unit tests; needs python3-dbus-next
./pi/tools/dry_run.sh                        # Pi package and daemon dry run
```

Firmware build and flash (needs ESP-IDF; builds all variants in CI, never flash without the owner):

```bash
python3 tools/firmware/build.py --variant both --out dist          # also: left, right
python3 tools/firmware/build.py --idf-export ~/esp/esp-idf/export.sh --flash /dev/ttyUSB0
# Windows: . C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1; python tools\firmware\build.py --flash COM5
```

Other CI: CodeQL (c-cpp via the host CMake build, python), gitleaks, semgrep, libfuzzer
(`fuzz/`), weekly ccache warm-up. Container image `espressif/idf:v6.1`.

## Done gate

Before opening a PR, all of these must pass locally:
1. `./tests/run_c_tests.sh`, plus the sanitizer variant when you touched C.
2. `cmake ... && ctest`.
3. The Python tests above that your change can affect. Always `tests/test_docs_links.py` and
   `tests/test_doc_gatt.py` for any docs or GATT edit. `pi/run_tests.sh` for anything under `pi/`.
4. `gitleaks detect --no-git --source .` is clean.
5. `CHANGELOG.md` `## Unreleased` updated for user-visible changes.

The firmware build itself only runs in CI (`firmware.yml`) unless you have ESP-IDF. Say in the
PR which commands you did not run.

## Layout

| Path | Contents |
| --- | --- |
| `src/` | Firmware. `wmr_controller*`/`wmr_hid` (HID host side), `wmr_transport` (BLE side, queue pump), `wmr_protocol`, `wmr_queue`, `wmr_decode*`, `wmr_batch` (pure C, no BTstack, unit-tested), `wmr_console`, `wmr_bridge_main` (init and event routing only), `wmr_config.h` (all tunables) |
| `wmr_bridge.gatt` | GATT database (compiled by BTstack `compile_gatt.py`) |
| `tests/`, `samples/` | Host C and Python tests, `host_stubs/`, sample frames |
| `fuzz/` | libfuzzer targets (batch, decode, decoded) |
| `tools/` | `capture_ble.py`, `analyze_capture.py`, `triage_bundle.py`, `firmware/build.py` (+ size budget), `release/` (sums, signing) |
| `pi/` | Linux/Pi bridge (BlueZ, WebSocket, systemd, deb packaging); bench scripts in `pi/tools/` touch real hardware |
| `integration/` | The one BTstack CMake file that must replace the stock one |
| `docs/` | PROTOCOL, FLASHING, TESTING, DISCOVERY, COMPATIBILITY, STATUS, PI_BRIDGE, run records in `docs/runs/` |

## Protocol notes

- Service `0000FF10-...`. FF11 raw frames, FF12 decoded 56-byte records, FF14 batched decoded
  records, FF13 controller config, FF15 echo (round-trip timing), FF16 firmware version (read),
  FF17 control and status. Only one of FF11, FF12, FF14 can be subscribed at a time.
- FF12/FF14 need a large enough ATT MTU (FF12 needs 59 or more, FF14 batching needs 61 or more;
  the app asks for 517). Every data characteristic refuses an unbonded central (LE Secure
  Connections, key size 16; FF16 is plaintext).
- No filtering or calibration on the bridge. Records carry raw IMU values and `device_ticks`;
  orientation fusion runs on the phone.
- Input frames: WMR 46 bytes (`A1 01`), PSVR2 79 bytes (`A1 31`), Switch 50 bytes (`A1 30`).
  One bounded FIFO of 256 frames, newest dropped and counted when full. Per-hand sequence numbers.
- A golden-packet test (`tests/test_protocol.c`) pins the byte layout. FF10-FF17 payloads are
  unchanged since 0.3.27. A payload change is a breaking change for wmr-companion.
- Full spec: `docs/PROTOCOL.md`. The Pi bridge must send the same FF11/FF12/FF14 payloads.

## Gotchas

- This repo does not build standalone. It builds inside the BTstack ESP32 port with
  `integration/main-CMakeLists.txt` replacing the port's one. Use `tools/firmware/build.py`.
- `compile_gatt.py` must run through Python explicitly (running it directly started Node.js on
  Windows). The supplied CMake file already does this.
- Browser flasher writes bootloader, partition table and app at `0x1000`, `0x8000`, `0x10000`,
  not NVS, so pairings survive a reflash unless "Erase device" is checked.
- Variants are compile-time defines in a generated `wmr_variant.h`. Never edit `src/wmr_config.h`
  to switch variants.
- Status LED: WS2812 on GPIO 4, plus GPIO 27 (ATOM Lite). Overridable with `EXTRA_CFLAGS`.
- Host C tests compile with `-Werror -Wconversion`. Fix warnings, do not silence them.
- Docs are tested: `test_docs_links.py` checks links, `test_doc_gatt.py` checks property claims
  against `wmr_bridge.gatt`. Renaming a doc or changing a GATT property breaks them.
- `pi/` tests are slow (about 1 minute) and have flaked before (failed once, passed on rerun);
  rerun once before blaming your change.
- Hardware status is mostly unverified (CI-built). Claim only what a run record in `docs/runs/`
  supports, and write "not run" otherwise.

## Review mistakes seen on past PRs (the Arthur/Paperclip reviewer)

Almost all findings were doc accuracy, not code:
- Wrong PR or ticket ranges in release text (said `#274-#288`, actual `#272-#288`). Check
  `git log <prev-tag>..<sha>` before writing a range.
- Release cut left an inconsistent table: a row says "Superseded by X" but no row for X exists.
  Add the firmware row to `docs/COMPATIBILITY.md` for every new tag.
- Citing a doc line for a claim that line does not make (`docs/PI_BRIDGE.md` line cites). Cite the
  real source or the bench capture, or drop the citation.
- Behavior regression in the Pi GATT backend: resetting MTU when the subscription set empties,
  not on disconnect. Add a test for the resubscribe case when changing state resets.
- Re-reviews score 5/5 once each finding gets its own fix commit plus a test.

## Don'ts

- Do not flash hardware or run hardware bench steps unless the owner asks.
- Do not commit secrets, Wi-Fi credentials, tokens, `.env` values or raw btmon captures with key
  material. Redact bench evidence. `.gitleaksignore` is only for synthetic test strings.
- Do not change FF10-FF17 payload layouts without updating `docs/PROTOCOL.md`, the golden test,
  `docs/COMPATIBILITY.md` and wmr-companion.
- Do not add BTstack-dependent code to the pure C modules; they must stay host-testable.
- Do not claim hardware results in docs without a run record.
- Keep `docs/runs/` records as written; add new files instead of editing old evidence.
- Do not push to `main` or tag releases; open a PR. Keep new C files' license header (`license_header.txt`).
