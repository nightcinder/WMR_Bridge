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
| `--version VERSION` | Version written to the manifest, `build-info.json` and the firmware (FF16/console `v`). At most 31 bytes; a longer value is refused. Default: `git describe --tags --always --dirty`, or `GITHUB_REF_NAME` when set to a `v*` tag. |
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
| `build-info.json` | Source commit, dirty flag, BTstack commit, ESP-IDF version, build time, flash settings. |
| `index.html` | The browser installer page. |

To use the installer locally, serve `dist/` over HTTP (Web Serial requires
HTTPS or `localhost`):

```bash
python3 -m http.server 8000 --directory dist
# open http://localhost:8000
```

Opening `index.html` directly from disk does not work because the browser
cannot fetch the manifest over `file://`.

## Browser flasher (end users)

The flasher is a static page using ESP Web Tools. The user connects the board
over USB, clicks **Install WMR Bridge**, and picks the serial port; the browser
writes the merged image. There is no ESP-IDF, Arduino IDE or Python install.

The source page lives in `docs/flasher/index.html` and is copied into `dist/`
by the build script. It needs three files served next to it:
`manifest.json`, `wmr-bridge-factory.bin` and (optionally) `build-info.json`.

### Hosting

`.github/workflows/firmware.yml` deploys `dist/` to GitHub Pages on a `v*`
tag. This keeps the manifest and the images same-origin, which the browser
requires. GitHub release assets do not send CORS headers, so the flasher
cannot load images straight from a release. A manual dispatch only builds and
uploads the workflow artifact; it never overwrites the live installer.

Enable Pages once in the repository: **Settings -> Pages -> Build and
deployment -> Source: GitHub Actions**. Until that is done, the Pages job
fails but the release job still publishes the images.

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
  flasher.
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

The release job writes `manifest.json` with `"version": "0.1.0"` (the leading
`v` is stripped) and `build-info.json` with the source and toolchain commits.
The same value is passed to ESP-IDF as `PROJECT_VER`, so the running firmware
reports it on the console (`v`) and over the read-only FF16 characteristic; a
client can compare it with `manifest.json` without a serial log.

## Tests

`tests/test_firmware_package.py` covers the packaging logic without a
toolchain: parsing `flasher_args.json`, ordering flash offsets, the
`idf.py merge-bin` invocation, the manifest shape, version normalisation, the
`PROJECT_VER` build argument, the repo-to-BTstack copy layout, the flasher page
contract and JSON round trips. The host-tests workflow runs it on every push
and pull request.

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
