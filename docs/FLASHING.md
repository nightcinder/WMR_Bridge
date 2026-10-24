# Flashing WMR_Bridge

WMR_Bridge builds inside the BTstack ESP32 port, with one BTstack file
replaced. That has always been the awkward part of flashing it. This page
describes the framework that automates the setup, produces release images, and
serves a browser installer. It is the first slice of
[issue #20](https://github.com/nightcinder/WMR_Bridge/issues/20), one-click
deploy for users with no ESP32 knowledge.

There are two audiences:

| You want to... | Use |
| --- | --- |
| Install the bridge on a board without ESP-IDF | the [browser flasher](#browser-flasher-end-users) |
| Build and flash your own changes | [`tools/firmware/build.py`](#one-command-build-and-flash) |
| Publish a firmware release | [the release process](#releases-and-ci) |

## One-command build and flash

`tools/firmware/build.py` performs the whole BTstack layout for you:

1. clones BTstack at the pinned commit into `.fw-build/btstack` (once),
2. copies the current working tree to `<btstack>/wmr_bridge`,
3. copies `integration/main-CMakeLists.txt` over
   `port/esp32/main/CMakeLists.txt`,
4. builds with `EXAMPLE=wmr_bridge`, running `idf.py -B <work>/build-wmr-bridge
   -DPROJECT_VER=<version> build` from `<work>/btstack/port/esp32`,
5. packages the images, a merged factory image, the flasher manifest and the
   browser installer page into `dist/`,
6. optionally flashes a serial port.

### Windows PowerShell

```powershell
# once per terminal: put ESP-IDF on PATH
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1

cd <repo>
python tools\firmware\build.py --flash COM5
```

### Linux / macOS

```bash
cd <repo>
python3 tools/firmware/build.py \
    --idf-export ~/esp/esp-idf/export.sh \
    --flash /dev/ttyUSB0
```

`--idf-export` sources the script in bash and re-runs the build under the
ESP-IDF Python environment. It is POSIX-only; on Windows, dot-source the
PowerShell profile as above. To build without flashing, omit `--flash`.

### Options

| Option | Meaning |
| --- | --- |
| `--version VERSION` | Version written to the manifest, `build-info*.json` and the firmware (FF16/console `v`). At most 31 bytes; a longer value is refused. Default: `git describe --tags --always --dirty`, or `GITHUB_REF_NAME` when set to a `v*` tag. |
| `--variant both\|left\|right` | Which hands the image serves. `both` (default) is today's build. `left` / `right` are compile-time single-hand images that advertise `WMR Bridge L` / `WMR Bridge R`; see [Two bridges](#two-bridges-one-hand-each). |
| `--out DIR` | Artifact directory. Default `dist/`. |
| `--work DIR` | Managed BTstack/build directory. Default `.fw-build/`. |
| `--flash PORT` | Flash `COM5`, `/dev/ttyUSB0`, ... after a successful build. |
| `--idf-export SCRIPT` | POSIX ESP-IDF `export.sh` to source first. |
| `--clean` | Delete the managed work directory first (fresh clone and build). |

Only `idf.py`, `git`, `cmake`/the ESP-IDF toolchain and the IDF Python
environment are needed. The script itself is standard-library Python 3.8+.

### Output

`dist/` contains:

| File | Purpose |
| --- | --- |
| `wmr-bridge-factory.bin` | Bootloader, partition table and application merged at offset 0. This is the file the browser flasher writes. |
| `wmr-bridge-app.bin` | The application image alone, for `idf.py`/`esptool` users. |
| `wmr-bridge-bootloader.bin` | Bootloader image (`0x1000`). |
| `wmr-bridge-partition-table.bin` | Partition table (`0x8000`). |
| `manifest.json` | [ESP Web Tools](https://esphome.github.io/esp-web-tools/) manifest, one merged part at offset 0. |
| `build-info.json` | Variant, flag values, source commit, dirty flag, BTstack commit, ESP-IDF version, build time, flash settings. |
| `index.html` | The browser installer page. |

The `both` build keeps those exact names. `--variant left` and `--variant right`
insert the variant before the role and in the manifest/build-info names:
`wmr-bridge-left-factory.bin`, `manifest-left.json`, `build-info-left.json`,
and likewise `-right`. That lets all three variants sit in one `dist/` without
overwriting each other; the browser page uses the three manifests.

To use the installer locally, serve `dist/` over HTTP (Web Serial requires
HTTPS or `localhost`):

```bash
python3 -m http.server 8000 --directory dist
# open http://localhost:8000
```

Opening `index.html` directly from disk does not work because the browser
cannot fetch the manifest over `file://`.

## Two bridges (one hand each)

`--variant left` and `--variant right` build the single-hand images described in
[docs/runs/sheets/multi-bridge.md](runs/sheets/multi-bridge.md). Use them when
one bridge carrying both controllers loses packets: put one controller on each
board, so each bridge serves one hand and does not accept the other.

```powershell
# Windows PowerShell, one board per command. --flash is optional.
python tools\firmware\build.py --variant left  --flash COM5
python tools\firmware\build.py --variant right --flash COM6
```

```bash
# Linux / macOS
python3 tools/firmware/build.py --variant left  --idf-export ~/esp/esp-idf/export.sh --flash /dev/ttyUSB0
python3 tools/firmware/build.py --variant right --idf-export ~/esp/esp-idf/export.sh --flash /dev/ttyUSB1
```

The flags are compile-time defines only. The script appends them to
`EXTRA_CFLAGS`; it never edits `src/wmr_config.h`:

| Variant | `WMR_CFG_BRIDGE_HANDS` | `WMR_CFG_BLE_NAME` | Advertised name |
| --- | --- | --- | --- |
| `both` | 0 (default, no define) | `WMR_CFG_BLE_NAME_BOTH` (default, no define) | `WMR Bridge` |
| `left` | 1 | `WMR_CFG_BLE_NAME_LEFT` | `WMR Bridge L` |
| `right` | 2 | `WMR_CFG_BLE_NAME_RIGHT` | `WMR Bridge R` |

The `WMR Bridge` prefix is load-bearing: clients find the bridge by prefix, so
the single-hand names keep it.

Each variant uses its own build directory (`build-wmr-bridge`,
`build-wmr-bridge-left`, `build-wmr-bridge-right`). `EXTRA_CFLAGS` is read at
configure time, so a shared directory could silently reuse the previous
variant's defines. `--variant both` still uses `build-wmr-bridge` and produces
today's artifact names.

### One board, single-hand mode

The same command works with a single board: `--variant left` flashes a board
that serves only LEFT. That is the cheap route for the one-board validation
sitting in plan M12.3' — no hand-edited rebuild and no second Classic ESP32.
Use `--variant right` if you want to validate the RIGHT path instead. The
boot banner and `STREAM` line show which image is running
(`WMR Bridge L`, `bridge_hands=1`).

### Build all three images for the browser installer

Build each variant into the same output directory; order does not matter:

```bash
python3 tools/firmware/build.py --variant both  --idf-export ~/esp/esp-idf/export.sh --out dist
python3 tools/firmware/build.py --variant left  --idf-export ~/esp/esp-idf/export.sh --out dist
python3 tools/firmware/build.py --variant right --idf-export ~/esp/esp-idf/export.sh --out dist
```

The result is three manifests (`manifest.json`, `manifest-left.json`,
`manifest-right.json`), three merged images and three `build-info*.json` files.
The browser installer offers all three. If only one variant was built, the page
marks the others as not included in that build.

### PSVR2 support (opt-in)

PSVR2 Sense controller support is **off by default** in standard release images
(`WMR_CFG_PSVR2_SUPPORT=0`) to preserve DRAM headroom for WMR operation (~7.6 KB
DRAM free vs ~3.5 KB with PSVR2 on).

To build an image with PSVR2 support enabled, pass `-DWMR_CFG_PSVR2_SUPPORT=1`
through `EXTRA_CFLAGS`. This automatically expands `WMR_CFG_MAX_FRAME` from 64
to 80 bytes to accommodate the 79-byte `0x31` reports:

```bash
# Linux / macOS
EXTRA_CFLAGS="-DWMR_CFG_PSVR2_SUPPORT=1" python3 tools/firmware/build.py --variant both --idf-export ~/esp/esp-idf/export.sh --out dist
```

```powershell
# Windows PowerShell
$env:EXTRA_CFLAGS = "-DWMR_CFG_PSVR2_SUPPORT=1"
python tools\firmware\build.py --variant both
Remove-Item Env:EXTRA_CFLAGS
```

See [docs/PSVR2.md](PSVR2.md) for framework details, open hardware questions, and build switches.

## Browser flasher (end users)

The flasher is a static page using ESP Web Tools. The user connects the board
over USB, clicks the install button for the image they want (**Both hands**,
**LEFT** or **RIGHT**), and picks the serial port; the browser writes that
variant's merged image. There is no ESP-IDF, Arduino IDE or Python install.

The source page lives in `docs/flasher/index.html` and is copied into `dist/`
by the build script. It loads `manifest.json` / `manifest-left.json` /
`manifest-right.json` plus the matching `wmr-bridge*-factory.bin` files from the
same directory; `build-info*.json` is optional and only fills in the version
labels.

### Hosting

The installer is live at **<https://nightcinder.github.io/WMR_Bridge/>**,
serving the latest tagged release (`v0.1.0`) images (all three variants).

`.github/workflows/firmware.yml` deploys `dist/` to GitHub Pages on a `v*`
tag. This keeps the manifest and the images same-origin, which the browser
requires. GitHub release assets do not send CORS headers, so the flasher
cannot load images straight from a release. A manual dispatch only builds and
uploads the workflow artifact; it never overwrites the live installer.

Two repository settings have to be right, and both were only found by tagging:

- **Settings -> Pages -> Build and deployment -> Source: GitHub Actions.** A
  site left on the legacy branch source is not deployable by
  `actions/deploy-pages`; `actions/configure-pages` only sets the build type
  when it creates the site, so an already-enabled legacy site stays legacy.
- **Settings -> Environments -> `github-pages` -> Deployment branches and
  tags.** GitHub seeds this with the default branch only, so a run triggered by
  a tag is refused before the deploy step. A `v*` entry of type *tag* is
  required.

If either is wrong the Pages job fails, but the release job still publishes the
images.

The page is intentionally kept out of the normal docs build: it is only useful
when a matching manifest and images are next to it.

### Limits

- Chrome and Edge on desktop only (Web Serial). Firefox and Safari are not
  supported.
- Classic ESP32 with at least 2 MB flash. ESP32-S3 has no Classic Bluetooth,
  so it cannot run this firmware.
- The manifest installs one merged image at offset 0 and asks the installer to
  erase first. The partition table is ESP-IDF's
  `Single factory app, no OTA (large)` layout (application at `0x10000`); there
  is no OTA partition, so updates are always over USB.
- The build is not signed. There is no secure boot or flash encryption
  (see [SECURITY.md](SECURITY.md)). Only flash images built by this
  repository's workflow.

## Reproducibility

The framework pins the two moving pieces that caused most setup failures:

| Component | Pin |
| --- | --- |
| BTstack | commit `e38553977a25fb0b55b383c72c289be0975f422c` (`tools/firmware/build.py`) |
| ESP-IDF | `espressif/idf:v6.1` in CI; v6.1 locally, as in [BUILD_WINDOWS.md](BUILD_WINDOWS.md) |
| Partition table | stock BTstack `port/esp32/sdkconfig.defaults`: `CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE=y` |

The same source, BTstack commit and ESP-IDF image are used for every release.
The build is not claimed to be bit-for-bit reproducible: ESP-IDF embeds
build-time metadata in the application image. A local Windows build can also
differ byte-for-byte from the Linux CI image; the release artifact is the CI
one, and `build-info.json` records exactly what went into it.

The source copied into BTstack is the working tree, not a git archive, so a
developer can build uncommitted changes. The `commit` and `dirty` fields in
`build-info.json` make that visible.

## Releases and CI

`.github/workflows/firmware.yml`:

- **Pull requests and pushes to `main`** build the firmware and upload `dist/`
  as a workflow artifact. This catches integration breakage (a source file
  missing from `integration/main-CMakeLists.txt`, for instance) without
  touching a board.
- **Tags `v*`** additionally attach every file in `dist/` to the GitHub
  release and deploy `dist/` to GitHub Pages, which updates the browser
  flasher. A tag with a suffix (`v0.1.0-rc1`) is published as a prerelease and
  is not offered as "Latest"; a plain `v0.1.0` is. The tag name is also the
  version the firmware reports over FF16, so `v0.1.0-rc1` boots as
  `0.1.0-rc1`.
- **Manual dispatch** builds and uploads the workflow artifact but does not
  deploy to Pages. Serve `dist/` locally to test the installer before tagging.

The build runs in the official `espressif/idf:v6.1` container; no self-hosted
runner or secrets are needed. `actions/checkout` uses full history so
`git describe` can produce a version.

To publish a release:

```bash
git tag -a v0.1.0 -m "WMR Bridge v0.1.0"
git push origin v0.1.0
```

The release job writes `manifest.json` (and the single-hand
`manifest-left.json` / `manifest-right.json`) with `"version": "0.1.0"` (the
leading `v` is stripped) and `build-info*.json` with the variant, flag values,
source and toolchain commits. The same version is passed to ESP-IDF as
`PROJECT_VER`, so the running firmware reports it on the console (`v`) and over
the read-only FF16 characteristic; a client can compare it with the manifest
without a serial log. CI builds all three variants into one `dist/` so the
browser installer can offer them.

## Tests

`tests/test_firmware_package.py` covers the packaging logic without a
toolchain: parsing `flasher_args.json`, ordering flash offsets, the
`idf.py merge-bin` invocation, the manifest shape, version normalisation, the
`PROJECT_VER` build argument, the repo-to-BTstack copy layout, the flasher page
contract and JSON round trips. It also covers the variants: `both` keeps every
original name and manifest field, `left`/`right` carry the variant in artifact,
manifest and build-info names, the manifest label names the hand, `build-info`
records the variant and flag values, and the build environment/build directory
are per-variant. The host-tests workflow runs it on every push and pull request.

It does **not** prove that the firmware builds or that a board boots. Those
still need the firmware CI job and a hardware report.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `idf.py is not on PATH` | Load ESP-IDF first (Windows profile script, or `--idf-export`). |
| `Failed to connect` while flashing | Hold **BOOT** while it retries; use a data cable; close any serial monitor using the port. |
| `No serial data received` | Same as above; check the port name. |
| Browser says Web Serial is unsupported | Use Chrome/Edge on desktop, over HTTPS or `localhost`. |
| Installer cannot load the manifest | Serve the folder over HTTP; do not open `index.html` from disk. |
| Link fails with `undefined wmr_*` symbols | A new source file is missing from `integration/main-CMakeLists.txt`. Add it there; the build script re-copies the file on every run. |
| Pages job fails | Enable GitHub Pages with **Source: GitHub Actions** (Settings -> Pages). |

## Next steps

The framework is deliberately the foundation, not the finished product:

- the app reading FF16 and warning when it differs from the release it expects;
- optional signing/verification of release images before wider distribution;
- a small CLI (`esptool`) downloader for users who cannot use Web Serial;
- deciding whether to support an ESP32-S3 plus external Classic dongle
  (issue #20 mentions it; the S3 alone cannot work).
