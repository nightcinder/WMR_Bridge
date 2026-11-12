# Linux / Raspberry Pi host bridge over Wi-Fi

Status: the second supported first-class target (ESP32-over-BLE is the default),
host-tested in this repository but not yet run on a Raspberry Pi or with real
controllers. Everything below is the design and the bench plan, not a hardware
result. The bring-up run sheet for the first sitting is
`docs/runs/sheets/bench.md` (Part A).

This document specifies a second bridge implementation for any Linux machine
with Bluetooth Classic and a 5 GHz Wi-Fi adapter. A Raspberry Pi 4 or 5 is the
reference target. It replaces the ESP32 and the BLE GATT link with the Pi and a
WebSocket over Wi-Fi, while keeping the controller-facing Bluetooth Classic
protocol and the existing FF11/FF12/FF14 payloads unchanged.

```
WMR LEFT --\ Classic Bluetooth HID 5 GHz Wi-Fi
              >----------------------> Raspberry Pi ----------------> phone / PC
WMR RIGHT --/ 46-byte frames BlueZ + wmr_bridge WebSocket (FF11/12/14)
```

## Why a Pi, and why 5 GHz

The open problem on the ESP32 is upstream report loss between the controllers
and the bridge, worst when both controllers and the phone's BLE link share the
same 2.4 GHz radio ([STATUS.md](STATUS.md), [DECODING.md](DECODING.md)). Moving
the phone link to Wi-Fi removes BLE airtime from the bridge entirely. Using the
5 GHz band also keeps the phone link away from Bluetooth Classic, which lives
at 2.4 GHz, and away from the Pi's own 2.4 GHz Wi-Fi/BT coexistence machinery.

| Target | Suitable | Notes |
| --- | --- | --- |
| Raspberry Pi 5 | Yes | BT 5.0 Classic + dual-band Wi-Fi; reference target |
| Raspberry Pi 4 Model B | Yes | BT 5.0 Classic + dual-band Wi-Fi |
| Raspberry Pi 3 Model B+ | Expected | dual-band Wi-Fi + BT 4.2 Classic; 64-bit OS supported; less CPU headroom and 1 GB RAM |
| Raspberry Pi Zero 2 W | No | 2.4 GHz Wi-Fi only, shares the band with BT |
| Generic x86/ARM Linux | Expected | needs BlueZ HID host + a 5 GHz adapter |
| Pi with a USB BT dongle | Expected | kernel must expose HID input as `/dev/hidraw*` |

This does not fix Classic Bluetooth loss by itself. The controller-to-bridge
link is still Bluetooth. What it removes is the BLE notification airtime and
the phone's participation in the 2.4 GHz contention. Whether the Pi's Classic
radio does better than the ESP32's is an open measurement question (see the
test plan).

## What changes, and what does not

| Concern | ESP32 firmware | Linux/Pi host bridge |
| --- | --- | --- |
| Classic BT host | BTstack | BlueZ `bluetoothd` input plugin + kernel `hidp` |
| Controller discovery | inquiry + names + NVS addresses | BlueZ pairing/trust; names from `hidraw` |
| Reports | BTstack HID callback | `/dev/hidrawN` reads |
| Phone link | BLE GATT FF10 | WebSocket on Wi-Fi |
| Raw stream | FF11 fragments | same 12-byte fragment header, same reassembler |
| Decoded stream | FF12 56-byte records | same records, same fields |
| Batched stream | FF14 version 2 | same 2-byte header + records |
| Sequence numbers | per hand, every report | per hand, every report; reset only on daemon restart |
| `rx_time_us` | ESP32 `esp_timer` since boot | Linux monotonic clock (since boot, not daemon start); diagnostics only |
| Calibration FF13 | read from controllers | cached blob served in 496-byte windows over WebSocket; controller read not implemented yet |
| Echo FF15 | GATT characteristic | JSON `ping`/`pong` control messages |
| Firmware version FF16 | GATT characteristic | `version` in `hello`/`status` |
| BLE MTU | 23 .. 517 | no MTU; one raw fragment by default |
| Security | no BLE pairing/encryption | token required beyond loopback; still plaintext unless tunnelled |

The companion app can reuse its existing FF11 reassembler, FF12 record parser
and FF14 batch parser. Only the transport changes.

## Installation

### Host prerequisites

Raspberry Pi OS Bookworm (64-bit) is the reference; the current Trixie (64-bit)
release is also supported. You need:

- BlueZ with the `input` plugin enabled (the Raspberry Pi OS default);
- the kernel `hidp` module;
- permission to read `/dev/hidraw*` (root, or a user in the `input` group).

```bash
sudo apt update
sudo apt install bluez rfkill avahi-utils
sudo modprobe hidp
echo hidp | sudo tee /etc/modules-load.d/wmr-bridge.conf
sudo systemctl enable --now bluetooth
```

Confirm that BlueZ is running with the input plugin:

```bash
systemctl status bluetooth
bluetoothctl show
```

The bridge deliberately does not open L2CAP PSM 0x11/0x13 itself. The kernel
HIDP driver already listens there. A user-space listener would collide with it.
BlueZ owns the HID host channels and exposes each controller as a hidraw node.

### Pair the controllers (automatic)

The bridge pairs controllers on its own. It runs a BlueZ auto-pairer while
fewer than two hands are open on hidraw. Put a controller in pairing mode and
the bridge scans, pairs, trusts, and connects it. You do not run `bluetoothctl`.

Put one controller in pairing mode:

1. Turn on the controller. Hold the front Windows button for 2 seconds.
2. Open the battery cover.
3. Hold the small pairing button inside the battery compartment until the LEDs
   pulse slowly.

Wait for the bridge to pair it. Repeat for the other controller. The
controllers normally reconnect to a trusted host on their own after waking.
Pass `--no-auto-pair` to disable the auto-pairer and pair by hand.

Check the pairing state at any time:

```bash
wmr-bridge status # or: wmr-bridge --status
```

It prints the adapter (`powered`, `pairable`, `discovering`), each hand's
hidraw node, and the BlueZ bond flags. A hand that has not paired says
`no hidraw node` and `no bond known`.

The service logs each pairing step. Watch it while a controller is in pairing
mode:

```bash
journalctl -u wmr-bridge -f
```

Expected lines (the address and name are yours):

```text
auto-pair: scanning 10 s for LEFT, RIGHT; put the controller in pairing mode
auto-pair: found AA:BB:CC:DD:EE:01 (Motion controller - Right)
auto-pair: pairing AA:BB:CC:DD:EE:01 (Motion controller - Right)
auto-pair: paired AA:BB:CC:DD:EE:01
auto-pair: trusting AA:BB:CC:DD:EE:01 (Motion controller - Right)
auto-pair: connecting AA:BB:CC:DD:EE:01 (Motion controller - Right)
auto-pair: connected AA:BB:CC:DD:EE:01 (Motion controller - Right)
```

If no `found` line appears, the Pi is not hearing the controller. Check that
the controller is in pairing mode, that it is not still bonded to the ESP32 or
another host, and:

```bash
rfkill list bluetooth
bluetoothctl show
bluetoothctl devices
journalctl -u wmr-bridge -n 200 --no-pager
```

`auto-pair: Bluetooth adapter is not usable yet` means the adapter is still
rfkill-blocked or `bluetoothd` is down. A stock Pi OS image soft-blocks
Bluetooth at boot; the systemd unit clears that block as root before start and
the pairer retries with a 30 s backoff instead of disabling itself. Check
`rfkill list bluetooth` and `systemctl status bluetooth`. A `could not scan;
retrying with backoff` line names a scan failure such as a resetting adapter;
the pairer re-probes power and retries, so pairing resumes when the adapter
recovers. A `pairing ... failed: org.bluez ... ` line is the BlueZ error for that
attempt. If it says `AuthenticationFailed`, remove the controller's old bond on
the other host (or with `bluetoothctl remove <MAC>`), then put it in pairing
mode again.

Check the nodes:

```bash
ls -l /dev/hidraw*
cat /sys/class/hidraw/hidraw*/device/uevent | grep -E 'HID_NAME|HID_UNIQ'
```

A WMR input report appears on hidraw as report ID `0x01` followed by 44 payload
bytes. The bridge accepts that 45-byte form, the firmware's 46-byte
`A1 01 ... ` form, and a bare 44-byte payload. Other report IDs are ignored.
Diagnose with `sudo evtest` or `sudo hexdump -C /dev/hidrawN`.

If the nodes are not readable by your user:

```bash
sudo usermod -aG input "$USER" # log out and back in
```

or install the shipped udev rule:

```bash
sudo cp pi/udev/99-wmr-bridge.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Run from the checkout

```bash
cd pi
python3 -m wmr_bridge --list

# Loopback only (the default): useful for a local client on the same machine.
python3 -m wmr_bridge

# Pi on the LAN, phone connecting to it: a token is required.
printf '%s\n' "$(head -c 24 /dev/urandom | base64)" > token
chmod 600 token
python3 -m wmr_bridge --bind 0.0.0.0 --token-file token
```

No third-party packages are required. The WebSocket implementation, protocol
codecs, decoder and tests use only the Python standard library (3.10+).

`--bind` defaults to `127.0.0.1`. If you bind to any non-loopback address
without `--token` or `--token-file`, the daemon exits before opening a socket
instead of silently serving controller telemetry to the LAN.
`--insecure-no-token` is the explicit, typed escape hatch and is unsafe on any
shared network.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--bind`, `--port` | WebSocket listen address (default `127.0.0.1:8765`); a non-loopback address requires a token |
| `--token`, `--token-file` | require a shared token from clients (the file form is preferred) |
| `--allow-origin ORIGIN` | allow this browser `Origin` (repeatable); other `Origin` values are rejected |
| `--insecure-no-token` | allow a non-loopback bind without a token (unsafe) |
| `--hidraw PATH` | open a specific node instead of auto-discovery |
| `--hand {both,left,right}` | only open the selected controller hand (default `both`; for one-Pi-per-hand setups) |
| `--advertise-mdns` | optionally publish `_wmr-bridge._tcp.local.` with avahi or systemd-resolved; never fatal |
| `--left MATCH`, `--right MATCH` | assign by MAC, path or name part |
| `--all-hidraw` | consider every HID device (then use `--left/--right`) |
| `--batch-max`, `--batch-hold-ms` | FF14 batching (default 9 records, 5 ms) |
| `--queue-size` | per-client outbound queue (default 512) |
| `--replay FILE` | replay `samples/controller_frames.txt` without hardware |
| `--duration SECONDS` | stop after a fixed time (bench convenience) |
| `--list` | print hidraw nodes and exit |
| `--status` (or `status`) | print adapter, BlueZ bond and hidraw state, then exit |
| `--no-auto-pair` | do not run the BlueZ auto-pairer |
| `--prefer-5ghz` | Create or update the `<connection>-5ghz` NetworkManager profile and prefer 5 GHz. This is a one-shot action and does not start the bridge. |
| `--no-reconnect` | with `--prefer-5ghz`, save the profile without switching to it |

### Installation

For a step-by-step tester guide covering OS flashing with Raspberry Pi Imager,
5 GHz Wi-Fi setup, controller pairing, `wifi_probe` validation, upgrades, and
troubleshooting, see the [Raspberry Pi Deployment and Update Runbook](DEPLOY_RPI.md).

On Raspberry Pi OS 64-bit (Bookworm or Trixie), install the Debian package:

```bash
sudo apt install ./wmr-bridge_<version>_all.deb
journalctl -u wmr-bridge -f
```

The package is `Architecture: all` (the daemon is Python). `postinst` creates
the `wmr` system user (`input` group), generates `/etc/wmr-bridge/token`
(`0640 wmr:wmr`) if it is absent, adds the invoking `sudo` user to the `wmr`
group, reloads udev, then enables and starts the unit.
`/etc/wmr-bridge/wmr-bridge.env` and its example are conffiles, so an
upgrade keeps local edits. The generated token is not a package file and
survives reinstall and upgrade. `apt remove wmr-bridge` stops and disables the
unit and keeps the token. `apt purge wmr-bridge` removes the token.

If a traceback points at a line that does not match
`/opt/wmr-bridge/pi/wmr_bridge/*.py`, the installed tree is stale from an
earlier version, not a bytecode problem: reinstall the matching `.deb` with
`sudo apt install --reinstall ./wmr-bridge_<version>_all.deb`. The package
copies the tagged modules and ships no bytecode, and `postinst`/`install.sh`
remove `__pycache__` left by a previous install.

The piped installer installs the `.deb` and enables the unit for you:

```bash
# one line, no checkout: installs the .deb and enables/starts the unit
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash

# or from a checkout or an extracted locally built tarball
sudo pi/install.sh
sudo systemctl enable --now wmr-bridge
```

The release attaches no tarball, so the piped mode needs `apt-get`. Without it,
use a checkout or the `wmr-bridge-<version>.tar.gz` that `pi/build_package.sh`
produces on any Linux host. When `install.sh` is piped, `$0` is the shell, so it
downloads the matching `wmr-bridge_<version>_all.deb` from the same release,
verifies the release `SHA256SUMS` against the minisign public key pinned in
`pi/install.sh` (shipped as `pi/minisign.pub`), verifies the `.deb` against that
list, installs it with `apt-get` and enables/starts the unit. Set `WMR_BRIDGE_VERSION=<version>` to pin
the Pi package version and `WMR_BRIDGE_TAG=<tag>` to name the release tag it
comes from (the tag defaults to `v<version>`, which only matches when the
package version equals the tag). Set `WMR_BRIDGE_RELEASE_BASE_URL` to use a
mirror. A checkout install does not enable the unit; enable it as shown above.

Releases cut by this workflow attach ten files: `wmr-bridge-factory.bin`,
`wmr-bridge-left-factory.bin`, `wmr-bridge-right-factory.bin`,
`wmr-bridge-app.bin`, `wmr-bridge-left-app.bin`, `wmr-bridge-right-app.bin`,
`wmr-bridge_<version>_all.deb`, `install.sh`, `SHA256SUMS` and
`SHA256SUMS.minisig`. The three
factory images and the three application images come from the `build-liesa`
firmware job; the `.deb` and `install.sh`
come from the `package` job (`pi/build_deb.sh`, `pi/build_package.sh` and a copy
of `pi/install.sh`); `SHA256SUMS` is written by `tools/release/make_sums.sh` and
signed by `tools/release/sign_sums.sh` in
the `release` job. Older tags published different asset lists; `v0.3.11`, for
example, still carries the tarball and per-file `.sha256` files. The package
version comes from `pi/wmr_bridge/__version__`, not from the tag.

### Version display and app-triggered upgrade

The Pi package version is reported by `wmr-bridge --version`, by
`hello.version` on every WebSocket handshake, and by `status.version`. The
companion app shows the received version and compares it with the latest GitHub
release. The trigger itself is a bridge feature: a companion build has to send
the command described below, and one that does not yet implement it shows the
version and an update notice only.

`hello.upgradeCapable` is `true` only when the daemon has a token and
`/var/lib/wmr-bridge` exists and is writable by the `wmr` service user. A
bridge without a token marks every client as authenticated, so it never
advertises the trigger. A missing or false flag means the app must not send the
command and should show the one-line installer instead. Releases up to 0.3.23
omit the field and answer `{"type":"upgrade"}` with
`{"type":"error","code":"unknown_type"}`.

An authenticated client asks for an upgrade with:

```json
{"type": "upgrade"}
```

The reply is `{"type":"upgrade_status","state":"started"}`,
`{"type":"upgrade_status","state":"busy"}`, or
`{"type":"upgrade_status","state":"error","code":"auth_failed"|"unavailable"}`.
The client sends no data. The daemon creates an empty file,
`/var/lib/wmr-bridge/upgrade.trigger`, and only its name is used.

The path has four parts:

1. `wmr-bridge-upgrade.path` watches for the trigger and starts
   `wmr-bridge-upgrade.service` as root.
2. The root oneshot deletes the trigger first, so anything written to it is
   ignored and a failed run cannot re-trigger itself. It runs only the
   root-owned `/opt/wmr-bridge/pi/install.sh --upgrade`, with
   `WMR_BRIDGE_REPO`, `WMR_BRIDGE_RELEASE_BASE_URL`, `WMR_BRIDGE_TAG` and
   `WMR_BRIDGE_VERSION` cleared and no `EnvironmentFile=`, so a client cannot
   choose a URL, tag or version.
3. `--upgrade` fetches the latest release, verifies `SHA256SUMS.minisig`
   against the pinned minisign key, verifies the `.deb` against
   `SHA256SUMS`, installs it with `apt-get` and restarts the service. When the
   latest version is not newer than the installed one it exits 0 without
   reinstalling, so the trigger never downgrades or reinstalls.
4. `flock` and the unit's `StartLimitBurst=3` / `StartLimitIntervalSec=600`
   bound repeated requests. The daemon also keeps its own persisted start
   history and reports `busy` before systemd would refuse a start; a trigger
   root never picked up expires after 120 seconds.

The daemon runs as `wmr` and cannot install packages. This is the only path
that uses root: an authenticated client can ask for an upgrade, but every
choice of what to install stays with the root-owned installer and unit. The
signature check makes a corrupt or tampered download fail closed. It does not
prove the release author, so TLS to GitHub remains the trust anchor for the
release contents, the same as the piped bootstrap.

**One-time sudo bootstrap.** Releases up to and including 0.3.23 (0.3.19 and
older included) ship neither `wmr-bridge-upgrade.path` nor
`wmr-bridge-upgrade.service`, and their daemon has no `StateDirectory=` and no
`upgradeCapable` field. A companion build with trigger support treats a missing
flag as false and shows the installer command; a build without trigger support
has no upgrade button to show it in place of. Run the command once as root:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
```

The release cut from this tree installs and enables both units, so a companion
build with trigger support can start later upgrades without `sudo`.

### Verifying a release by hand

The piped installer verifies the signature for you. To verify a downloaded
release by hand, fetch `SHA256SUMS` and `SHA256SUMS.minisig` from the release,
then check the digest list against the published minisign public key:

```bash
minisign -Vm SHA256SUMS -P RWTL80BhYeTEqPhBvFePoSc/uoC+iJCEa41o1uA3h+t4opQbX0yKCnA6
sha256sum -c SHA256SUMS --ignore-missing
```

The production minisign public key, shipped as `pi/minisign.pub` and pinned in
`pi/install.sh` as `WMR_BRIDGE_MINISIGN_PUBKEYS`, is:

```text
untrusted comment: minisign public key A8C4E4616140F3CB
RWTL80BhYeTEqPhBvFePoSc/uoC+iJCEa41o1uA3h+t4opQbX0yKCnA6
```

In a repository checkout, use the key file instead of `-P`:
`minisign -Vm SHA256SUMS -p pi/minisign.pub`.
`SHA256SUMS.minisig` covers `SHA256SUMS` only. `SHA256SUMS` in turn covers
every other asset, so one signature protects the whole release.

### Bootstrapping an older unsigned install

Pi installs older than the signed release carry no signature check. Bootstrap
them to the signed path with the same one-line installer:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
```

The installer fetches the current `.deb`, verifies `SHA256SUMS` against the
pinned key, verifies the `.deb` against that list, and upgrades in place. The
token, `/etc/wmr-bridge/wmr-bridge.env` (a conffile), the udev rule and the
unit survive the upgrade. Set `WMR_BRIDGE_VERSION=<version>` to pin a release.

### Rotating the minisign key

The installer accepts more than one key so a rotation does not break older
installs. `WMR_BRIDGE_MINISIGN_PUBKEYS` in `pi/install.sh` holds the
space-separated list of accepted public keys; the installer tries each key in
turn and accepts the first match. To rotate:

1. Generate a new key pair with minisign: `minisign -G`.
2. Add the new public key to `WMR_BRIDGE_MINISIGN_PUBKEYS` next to the old one
   (dual-key window).
3. Update `pi/minisign.pub` to the new key and re-pin `pi/install.sh`.
4. Sign the next release's `SHA256SUMS` with the new secret key using
   `tools/release/sign_sums.sh` (`MINISIGN_SECRET_KEY= ... `).
5. Once every supported install trusts the new key, remove the old key from
   `WMR_BRIDGE_MINISIGN_PUBKEYS`. `pi/minisign.pub` holds one key and was
   switched to the new key in step 3.

`SHA256SUMS.minisig` for the rotated release verifies against the new key, while
older releases still verify against the old key during the dual-key window.
`WMR_BRIDGE_MINISIGN_PUBKEYS_OVERRIDE` is honoured only in unprivileged
staging mode (`WMR_SKIP_PRIVILEGED=1`), so a root upgrade never takes a trust
anchor from its environment.

Or run the equivalent manual steps:

```bash
sudo useradd --system --create-home --groups input,bluetooth wmr
sudo mkdir -p /opt/wmr-bridge /etc/wmr-bridge
sudo cp -r pi /opt/wmr-bridge/
sudo cp pi/udev/99-wmr-bridge.rules /etc/udev/rules.d/
sudo install -m 640 /dev/null /etc/wmr-bridge/token
sudo sh -c 'head -c 24 /dev/urandom | base64 > /etc/wmr-bridge/token'
sudo chown wmr:wmr /etc/wmr-bridge/token
sudo chmod 640 /etc/wmr-bridge/token
sudo cp /opt/wmr-bridge/pi/systemd/wmr-bridge.env.example /etc/wmr-bridge/wmr-bridge.env
sudo cp /opt/wmr-bridge/pi/systemd/wmr-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wmr-bridge
journalctl -u wmr-bridge -f
```

The unit runs as `wmr` with `SupplementaryGroups=input bluetooth`, restarts on failure,
reads the token from `/etc/wmr-bridge/token`, and loads optional flags from
`/etc/wmr-bridge/wmr-bridge.env` via `WMR_BRIDGE_ARGS` (for example,
`WMR_BRIDGE_ARGS=--hand left` on a single-hand Pi without editing the unit
file). The token file must be owned by `wmr` (mode 0640, group `wmr`). Members
of the `wmr` group (the installer adds the invoking `sudo` user) can read it
and run `pi/tools/wifi_probe.py` without sudo; group membership applies on the
next login. A root-owned file is unreadable by the service, which then exits
with a message naming the path and the uid it runs as.

### Pre-flight kit (no hardware)

The image is stock Raspberry Pi OS 64-bit (Lite or Desktop; Bookworm or Trixie)
written with Raspberry Pi Imager, plus this package. No custom `pi-gen` image is
maintained.
The daemon is one dependency-free Python tree, so a bespoke image would only
duplicate Raspberry Pi OS and add another artifact to keep current. Record the
image date and kernel in the sitting record (section 5.1 of the run sheet).

Build and verify the installable packages on any Linux host:

```bash
pi/build_package.sh # dist/wmr-bridge-0.3.25.tar.gz + .sha256
pi/build_deb.sh # dist/wmr-bridge_0.3.25_all.deb + .sha256
pi/tools/verify_package.sh dist/wmr-bridge-0.3.25.tar.gz
scp dist/wmr-bridge_0.3.25_all.deb pi@<pi>:~
ssh pi@<pi> 'sudo apt install ./wmr-bridge_0.3.25_all.deb'
```

The tarball alternative is
`scp dist/wmr-bridge-0.3.25.tar.gz* pi@<pi>:~` then
`ssh pi@<pi> 'tar -xzf wmr-bridge-0.3.25.tar.gz && sudo wmr-bridge-0.3.25/pi/install.sh'`.
Both carry `pi/` (daemon, probe, installer, systemd unit, udev rule,
NetworkManager drop-in), `docs/PI_BRIDGE.md` and
`samples/controller_frames.txt`, so they install without a git checkout. The
tarball also carries the root `VERSION`, so a build run from the extracted tree
can rebuild its own artifacts. The
installed `/opt/wmr-bridge/pi` keeps the drop-in next to `install.sh`, so
re-running the installer from the installed tree works. `run_tests.sh` stays in
the checkout/release tree because the suite needs the repo build scripts. The
installer copies the replay capture to `/opt/wmr-bridge/samples/`, which makes
the P2 check runnable on the Pi before a controller is paired:

```bash
cd /opt/wmr-bridge/pi
python3 -m wmr_bridge --list
python3 -m wmr_bridge --replay ../samples/controller_frames.txt --bind 127.0.0.1
python3 tools/wifi_probe.py --stream batched --duration 10
```

The probe prints the `hello` Wi-Fi and uplink objects. `--discover` sends a
`WMR_DISCOVER_V1` probe to UDP port 8765 before connecting. `--json-out PATH`
writes the final summary as JSON. `--assert-gates` checks the Sitting P pass
criteria (RTT p50 below 15 ms, tick-based missing-report share at or below 3 %
per hand, zero sequence gaps) and exits non-zero when any fails. Every hand the
bridge expects needs at least one record, so a run with no controller frames
fails the loss and gap gates instead of passing at 0 %:

```bash
python3 tools/wifi_probe.py --host 192.168.1.10 --discover \
    --duration 600 --assert-gates --json-out sitting-p.json
```

Host-side dry run (no Pi, no controller, no root; safe to run in CI):

```bash
pi/tools/dry_run.sh
```

It builds and verifies the `.deb`, stages it in a temporary rootfs, creates the
token, reinstalls it (keeping the token and a locally edited
`wmr-bridge.env`), starts the daemon from the staged tree in replay mode, and
asserts that the packaged probe receives both hands with zero sequence gaps,
zero tick-based missing reports, zero server drops and zero decode errors. It
also checks the installed unit, udev rule, power-save drop-in and `0640` token,
then runs `prerm`/`postrm` for remove and purge. A pass means the packaging
path is sound. It is not a Pi, BlueZ or radio result. The hardware checks
remain P1-P14 in the run sheet.

### Wi-Fi and firewall

- Use a 5 GHz SSID and verify with `iw dev wlan0 link` (`freq` above 5000).
- Wi-Fi power saving is disabled persistently by the package's NetworkManager
  drop-in `/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf`
  (`wifi.powersave = 2`). Reconnect or reboot after install to apply it. For a
  host without NetworkManager, use `sudo iw dev wlan0 set power_save off` for the
  current boot and add it to your network bring-up script.
- Open only the bridge port: `sudo ufw allow 8765/tcp` and, for LAN
  auto-discovery, `sudo ufw allow 8765/udp`.
- Do not expose the port to the internet. See [Security](#security).

### Automatic 5 GHz preference

The service runs `wmr-bridge --prefer-5ghz` before it starts the bridge. This command is a one-shot action. It does not start the daemon. It reads the active NetworkManager connection on the Wi-Fi interface, drops a trailing `-5ghz` to identify the 2.4 GHz base, and creates or updates a profile named `<base>-5ghz`. The clone keeps the base profile as the 2.4 GHz fallback. The clone sets `802-11-wireless.band` to `a` and `connection.autoconnect-priority` to 50. NetworkManager then prefers a 5 GHz BSSID when one is visible. The base profile stays in place, so a 2.4 GHz network still works.

The command also orders the uplinks with NetworkManager route metrics: wired profiles (`802-3-ethernet`) get `ipv4.route-metric` and `ipv6.route-metric` 100, the 5 GHz clone gets 200, and the 2.4 GHz base profile gets 300. A wired `eth0` therefore beats 5 GHz Wi-Fi, which beats 2.4 GHz, regardless of which profile autoconnected.

The command reconnects to the new profile only when a 5 GHz BSSID is visible. If the switch fails, the current connection stays in place. Pass `--no-reconnect` to save the profile without switching. The installer and `postinst` use that form so an SSH session stays on its current link.

For a radio with no 5 GHz support, such as the CYW43439 in a Pi Zero 2 W, the command is a warning-only no-op. It reports that the adapter is 2.4 GHz only and leaves the connection alone.

The bridge also reports the Wi-Fi band and the active uplink in its protocol messages. The `hello` and `status` JSON objects include a `"wifi"` field when the daemon inspected the radio. The field is an object with these keys: `iface`, `connected`, `freqMhz`, `band`, `supports5Ghz`, `contentionWarning`, and `warning`. `contentionWarning` is true when the Pi is connected on 2.4 GHz. That band shares the radio with Bluetooth. The `warning` text says whether a 5 GHz radio is available.

The `"uplink"` field describes the interface carrying the IPv4 default route: `iface`, `kind` (`ethernet`, `wifi` or `other`), and `addresses`. `hello.transport` is `"ethernet"` when that interface is wired and `"wifi"` otherwise. `hello` samples the Wi-Fi and uplink state again on each handshake.

`wmr-bridge status` prints the same data. It shows the uplink interface, `band=`, `supports5Ghz=`, and any warning. FF16 lists the wired address first, then Wi-Fi, so a client tries the preferred uplink before the fallback.

## Wire protocol, version 1

### Endpoint and handshake

```
ws://<bridge>:8765/wmr
Sec-WebSocket-Protocol: wmr-wifi-v1
```

The server accepts the connection, selects `wmr-wifi-v1` when offered, and
immediately sends one JSON `hello`. The HTTP upgrade must complete within 5
seconds, otherwise the server sends `408 Request Timeout` and closes, so a
half-open TCP connection cannot hold a slot.

Requests that carry an `Origin` header (browsers always do; the probe and the
companion app do not) are rejected with `403 Forbidden` unless that exact
origin was allowed at startup with `--allow-origin <value>` (repeatable). This
stops a web page on the LAN from opening a socket to the daemon. Origin matching
is case-insensitive on the normalised value. `*` is not a wildcard.

JSON examples below use the package version in this tree, `0.3.25`.
`pi/wmr_bridge/__version__` reports `0.3.25`. The `v0.3.12` release shipped
the `0.2.4` package, `v0.3.13` shipped the `0.3.13` package, `v0.3.14`
shipped the `0.3.14` package, `v0.3.15` shipped the `0.3.15` package,
`v0.3.16` shipped the `0.3.16` package, `v0.3.17` shipped the `0.3.17`
package, `v0.3.18` shipped the `0.3.18` package, `v0.3.19` shipped the
`0.3.19` package, `v0.3.20` shipped the `0.3.20` package, `v0.3.21` shipped
the `0.3.21` package, `v0.3.22` shipped the `0.3.22` package, and `v0.3.23`
shipped the `0.3.23` package, `v0.3.24` shipped the `0.3.24` package; `v0.3.25` ships this package.

```json
{
  "type": "hello",
  "protocol": 1,
  "transport": "ethernet",
  "device": "wmr-pi",
  "bridge_id": "wmr-pi-0123456789",
  "version": "0.3.25",
  "handFilter": ["LEFT", "RIGHT"],
  "hostname": "wmr-pi",
  "authRequired": true,
  "upgradeCapable": true,
  "streams": ["raw", "decoded", "batched"],
  "recordSize": 56,
  "batchMax": 9,
  "fragmentCapacity": 244,
  "uplink": {
    "iface": "eth0",
    "kind": "ethernet",
    "addresses": ["192.168.1.20"]
  },
  "calibration": {
    "version": 1,
    "headerSize": 16,
    "windowMax": 496,
    "maxBlob": 16384,
    "messageTag": 19,
    "states": ["not_read", "reading", "ready", "failed"],
    "hands": [
      {"hand": "LEFT", "state": "not_read", "totalLen": 0, "crc32": 0},
      {"hand": "RIGHT", "state": "not_read", "totalLen": 0, "crc32": 0}
    ]
  }
}
```

Text messages are JSON control messages. Binary messages from the server are
stream payloads or tagged FF13 calibration windows. Binary messages from the
client are rejected with `{"type":"error","code":"binary_not_accepted"}`.

`upgradeCapable` says whether the daemon may accept `{"type":"upgrade"}`; it is
false when the bridge has no token or when its state directory is missing or not
writable. `bridge_id` is a stable identity derived from the hostname and
`/etc/machine-id`; it is the same value advertised by mDNS and UDP discovery,
and by the BLE GATT peripheral. The BLE address of a Pi may be
random, so the BLE advertisement carries the first eight bytes of
`bridge_id` in its service data instead of relying on the address.
`version` is the bridge's FF16 analogue: the daemon/package version, not the
controller firmware. `handFilter` is the configured hand selector (`--hand`):
`["LEFT", "RIGHT"]` for a both-hand daemon, `["LEFT"]` or `["RIGHT"]` for a
single-hand Pi. A client that expects one hand per bridge can use it to confirm
the daemon is in the mode the deployment intended. The same two fields are
present in every `status` reply. `calibration` is present when the daemon runs
with FF13 forwarding (the default); a daemon started with calibration disabled
omits it and answers calibration requests with `calibration_unavailable`.

### Service discovery

#### UDP responder

The daemon listens on UDP port 8765 on the `--bind` address; unlike the
WebSocket port this does not follow `--port`, so a client always probes the
fixed discovery port and learns the actual WebSocket port from the reply. A
client broadcasts a nonce probe:

```
WMR_DISCOVER_V1 <nonce>
```

Each bridge that hears it replies by unicast to the sender with one JSON object:

```json
{
  "type": "wmr_bridge",
  "protocol": 1,
  "bridge_id": "wmr-pi-0123456789",
  "name": "wmr-pi",
  "port": 8765,
  "path": "/wmr",
  "hands": ["LEFT", "RIGHT"],
  "version": "0.3.25",
  "authMode": "token",
  "nonce": "<nonce from the probe>"
}
```

The reply echoes the nonce so the client can match it to its probe, and it never
carries the shared token. Replies are rate-limited per source address (at most
one per second by default). A reply is a hint only: open the WebSocket and
confirm `hello.bridge_id` matches the reply before sending a token. With
`--bind 127.0.0.1` the responder is loopback-only, like the WebSocket.

#### mDNS (optional)

`--advertise-mdns` publishes `_wmr-bridge._tcp.local.` on the LAN. It is off by
default and never affects whether the daemon starts. The Debian package
depends on `avahi-utils` (which pulls in `avahi-daemon`), so advertisement
works on a fresh install. Two host mechanisms are tried, with no third-party
Python dependency:

- `avahi-publish-service` publishes the instance while the daemon runs. TXT
  records carry `id=<bridge_id>`, `version=`, `hands=LEFT,RIGHT` and `path=/wmr`.
- otherwise `resolvectl` (systemd-resolved) is used: a `wmr-bridge.dnssd`
  service file is written to `/run/systemd/dnssd/` and removed on shutdown.
  This needs write access to that directory (normally root), so systemd units
  running as an unprivileged user will log and skip it.
- when neither tool exists the daemon logs one informational line and
  continues. Use `--advertise-mdns` only for convenience. Clients can always
  connect with the explicit host and port.

Advertisement exposes the bridge's hostname and port to the local network. It is
not authentication and does not change the token requirement.

### BlueZ BLE GATT fallback (default)

The daemon also serves the same FF11, FF12 and FF14 payloads over a BlueZ
BLE GATT service. This is a fallback for when the Wi-Fi WebSocket is
unreachable. It starts by default; disable it with `--no-ble`. The old
`--ble-fallback` flag is a deprecated no-op alias. The peripheral needs
`python3-dbus-next` and a free hci adapter (default `hci0`); if either is
missing the daemon logs a warning and keeps serving TCP.

The fallback registers the firmware's FF10 service (characteristics FF11 to
FF17) with BlueZ through `org.bluez.GattManager1` and advertises it with
`org.bluez.LEAdvertisingManager1`. Advertising uses two tiers: 150–250 ms
while no Classic controller is connected, and 1000–1285 ms once one connects,
which leaves airtime for the two Classic HID links on the same radio. The
advertisement carries the gamepad appearance (`0x03C4`) and lists the HID
service (`0x1812`) before FF10, so Android classifies the bridge as a
controller. The advertising name defaults to `WMR Bridge` (`--ble-name`).
The adapter is pairable for 180 seconds after boot (`--ble-pairing-window`),
then only while no LE bond is stored; a bonded phone reconnects without
pairability. Bonds proven to be the controllers (WMR name or Classic HID UUID)
never count as an LE bond, even though a dual-mode phone's LE bond carries a
`Class` and a public address. Any other bond counts as the phone's, so a bonded
headset closes the adapter until `wmr-bridge pair` opens a window. `wmr-bridge pair` opens a manual window on the running
service for 180 seconds by default (`wmr-bridge pair --seconds 60`), and
`wmr-bridge pair --close` ends it. `--ble-continuous-pairing` keeps the adapter
pairable while BLE is active, matching the ESP32, and is opt-in. A
`NoInputNoOutput` BlueZ pairing agent is registered with
`org.bluez.AgentManager1`, claimed as the default agent while that window is
open, and unregistered again when the window closes, so a phone's LE bond is
accepted as Just Works instead of being rejected for lack of an agent. The
agent accepts every device BlueZ presents and exists only for that window; the
adapter's pairable window is the gate. `install.sh` and the `.deb` post-install
script set BlueZ's `[General] JustWorksRepairing` to `confirm` and restart
`bluetooth.service` **only when that setting actually changes**. An upgrade from
a release that already carries the policy leaves bluetoothd (and the stored
bonds) alone; until 0.3.24 every install restarted bluetoothd while the bridge
held a GATT application, advertisement and Classic scans, which can crash the
Pi's combo controller into `HCI Hardware Error` (see Troubleshooting). When a
restart is needed the scripts stop `wmr-bridge.service` first and start it again
after bluetoothd is up. Without that setting, bluetoothd's default `never`
answers the kernel's Just Works confirm hint `0x01` itself with a negative reply
when the adapter still holds a bond for the phone, before any `Agent1` callback;
the
phone then shows `Numeric comparison failed`. `confirm` sends that repair to
the bridge's default agent, which accepts and logs it. This is the lesson from
the ESP32 firmware's stale-bond handling: a phone that forgot the
bridge may pair again and replace the old key instead of being refused. An
explicit `always` is left alone. The Classic auto-pairer runs its own
interactive `bluetoothctl` session to bond controllers on the same adapter.
While the BLE window is open it tells that client to drop its own agent
(`agent off`), so `pair` uses the bridge's Just Works agent for controllers
too. BlueZ clears a client's default agent when the client exits, so the
peripheral also re-requests default for its own agent after every scan
(`BLE pairing agent reclaimed as the adapter default`). Without both steps, a
phone pairing around a controller scan finds bluetoothctl's empty-capability
agent or no default agent at all, and Android closes the pairing page. The
agent and pairability also stay open for 10 seconds after the last agent
request, so a stored bond does not release the agent while the phone is still
discovering services and enabling the HID report.

Pairing diagnostics are in the journal: `BLE pairing window open/closed` with
the reason and the new `Adapter1.Pairable` state (for example
`BLE pairing window closed (an LE bond is stored); Adapter1.Pairable -> off`),
`BLE pairing window opened manually for 180 s`,
`BLE pairing agent registered`/`claimed`/`reclaimed`, the
`BlueZ JustWorksRepairing = ... ` policy line read from `main.conf` at BLE
start, one line per agent
callback with the method, device and result
(`BLE pairing agent: method=RequestAuthorization device=<device> result=accepted`),
and `BLE pairing agent: method=Cancel device=- result=cancelled` when the
exchange fails. A callback that BlueZ rejected internally never reaches the
agent, so a `JustWorksRepairing = never` warning explains a
"Numeric comparison failed" capture with no callback line.
The Classic auto-pairer also logs when its scan leaves `Adapter1.Pairable` off.
A capture that opens the window and then has no `BLE pairing agent:` line means
BlueZ never reached the agent; a `cancelled` line without a request line means
the request never arrived.

The GATT application also exposes the HID-over-GATT service (`0x1812`) on the
same LE link: HID Information, the firmware's 91-byte Report Map, Protocol
Mode, HID Control Point, and an encrypted input Report with report ID 1 and a
Report Reference descriptor. `wmr_bridge/hid_gamepad.py` ports the
`src/wmr_hid.c` mapping and `tests/vectors/hid_gamepad.txt` keeps the 9-byte
report byte-identical to the firmware. A HOGP host therefore binds the
controllers as a gamepad with no app. The bridge sends no HID report until a
Classic controller is connected and the phone enables the Report CCCD, and it
pauses HID notifications whenever a WebSocket client or an FF11/FF12/FF14
subscriber is active, emitting one neutral report on the transition.

Discovery and identity:

- `bridge_id` is the same value on BLE and on TCP. A client that learns it on
  one transport can match the same bridge on the other.
- A Pi's BLE address may be random, so a scanner cannot key on it. The BLE
  advertisement carries the first eight bytes of `bridge_id` in its service
  data, as `WMB1` followed by those eight bytes. A client matches the
  advertisement to the `bridge_id` it reads from FF16.
- FF16 is plaintext and carries `bridge_id`, `name`, `version`, `hosts`,
  `port`, `path` and `authRequired`. It never carries the token.
- FF13 is encrypt-read because it carries the session token next to the
  calibration windows. It also reports `pairingOpen`.

Per-client gating:

- Notifications are gated by each central's CCCD state, not by the bridge.
  A phone with notifications enabled keeps receiving FF11, FF12 and FF14 even
  while another client is on TCP. A phone with notifications off receives
  nothing.
- FF14 is capped at 50 Hz because the Pi shares `hci0` with the two Classic
  HID controller links.
- Bonding is accepted only while the adapter is pairable: during the boot
  window (`--ble-pairing-window`), for a manual window opened with
  `wmr-bridge pair`, with `--ble-continuous-pairing`, or while no LE bond is
  stored.

Coexistence with TCP:

- The BLE fallback runs beside the WebSocket. A client can use both at once.
- A client that prefers TCP turns its own BLE notifications off when TCP
  becomes active. It writes 0x0000 only to the characteristics it enabled. It
  does not ask the bridge to silence other clients.
- While on BLE, the client retries TCP at a fixed interval (default 2 seconds).
  When TCP connects, the notification subscriptions move to TCP without being
  closed.
- The UDP probe nonce and `hello.bridge_id` check still apply: a reply is a
  hint only, and the client confirms `hello.bridge_id` before it sends a token.

### Authentication

If `authRequired` is true, the client must send, within 5 seconds:

```json
{"type": "auth", "token": "<shared token>"}
```

Success is `{"type":"auth_ok"}`. Failure sends
`{"type":"error","code":"auth_failed"}` and closes with WebSocket close code
`4401`. No control message other than `auth` is processed before
authentication.

The token is a shared secret, not a user account. It is compared in constant
time. A token is required whenever the daemon listens on a non-loopback
address: `--bind` defaults to `127.0.0.1`, and binding to a LAN address
without `--token`/`--token-file` exits with an error instead of starting. This
is the path the owner runs (Pi on the LAN, phone connecting to it), so configure
`--token-file /etc/wmr-bridge/token`. `--insecure-no-token` overrides the check
for deliberate lab use only. It is unsafe because every host that can reach the
port can subscribe. With no token on loopback, only local clients can connect.

### Control messages

| Client sends | Server replies |
| --- | --- |
| `{"type":"subscribe","stream":"raw"\|"decoded"\|"batched"}` | `{"type":"subscribed","stream": ... ,"recordSize":56,"batchMax":9,"fragmentCapacity":244}` |
| `{"type":"unsubscribe"}` | `{"type":"unsubscribed"}` |
| `{"type":"status"}` | a `status` object (below) |
| `{"type":"ping","id":N,"t":ms}` | `{"type":"pong","id":N,"t":ms,"serverTimeUs": ... }` |
| `{"type":"hello"}` | another `hello` |
| `{"type":"auth","token":" ... "}` | `auth_ok` or `auth_failed` |
| `{"type":"calibration","hand":"LEFT"\|"RIGHT"\|0\|1,"offset":N}` | one tagged binary FF13 window (below) |
| `{"type":"pairing","action":"open","seconds":N}` or `{"type":"pairing","action":"close"}` | `{"type":"pairing","open":true\|false,"seconds":N\|null}` |
| `{"type":"upgrade"}` | `{"type":"upgrade_status","state":"started"\|"busy"\|"error","code": ... }` |
| `{"type":"close"}` | close frame, then disconnect |

Rules:

- One stream per connection. Subscribing to a different stream while one is
  active returns `{"type":"error","code":"stream_active"}`. Unsubscribe first.
  This mirrors the FF11/FF12/FF14 mutual exclusion in the firmware, so a client
  cannot believe it is receiving two streams when only one is active.
  Re-subscribing to the same stream is idempotent.
- Calibration reads are independent of the stream subscription, like FF13 and
  FF12 on BLE. A calibration request is answered without changing the
  subscription. `hand` may be a name or an index; a hand outside `handFilter`
  or a malformed offset returns `bad_hand` or `bad_offset`.
- A `pairing` message opens or closes the manual BLE pairing window that
  `wmr-bridge pair` uses. `seconds` is 1 .. 86400; when omitted while opening,
  the configured `--ble-pairing-window` duration applies. It returns
  `pairing_unavailable` when the daemon runs
  with `--no-ble` (or the BLE peripheral failed to start), `bad_action` for an
  unknown action, and `bad_seconds` for an invalid duration.
- An `upgrade` message requires the token and `upgradeCapable`. Without a
  token it replies `{"state":"error","code":"auth_failed"}` and writes nothing,
  even though a tokenless bridge considers the subscriber authenticated. It
  replies `unavailable` when the state directory is missing or not writable,
  and `busy` while a trigger is pending or root holds the upgrade lock. See
  [Version display and app-triggered upgrade](#version-display-and-app-triggered-upgrade).
- An unknown stream is `bad_stream`. Malformed JSON is `bad_json`. An unknown
  `type` is `unknown_type`.
- Multiple clients are allowed (default: no explicit cap). Each has its own
  queue and independent subscription. Frames are fanned out to every subscriber.
- Slow clients do not block the ingest path. When a client's 512-message queue
  is full, the newest message is dropped and counted in `dropped`. This mirrors
  the firmware's bounded queue policy.

### Binary payloads

Every binary message is exactly the BLE notification value of the subscribed
stream. All integers are little-endian.

Raw, one FF11 fragment. The 12-byte header is:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | u8 | magic `0x52` |
| 1 | u8 | hand, 0 LEFT / 1 RIGHT |
| 2 | u32 | per-hand sequence |
| 6 | u16 | total frame length (46 for an input report) |
| 8 | u16 | offset of this chunk |
| 10 | u16 | chunk length |
| 12 | `chunk_len` | frame bytes |

The default fragment capacity is 244 bytes including the header, so a 46-byte
frame is one message of 58 bytes. The companion's existing FF11 reassembler
handles this unchanged. See [PROTOCOL.md](PROTOCOL.md) for the receiver rules.

Decoded, one FF12 version 1 record. Exactly 56 bytes:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | u8 | version `1` |
| 1 | u8 | hand |
| 2 | u8 | flags (bit0 CALIBRATED; always 0 here) |
| 3 | u8 | buttons (same bit positions as the controller report) |
| 4 | u32 | sequence (same counter as raw) |
| 8 | u64 | `rx_time_us` (daemon monotonic microseconds) |
| 16 | u32 | `device_ticks` (controller clock, 100 ns/tick) |
| 20 | 3 x f32 | gyro, rad/s, uncalibrated |
| 32 | 3 x f32 | accel, m/s^2, uncalibrated |
| 44 | i16 | temperature raw |
| 46 | 2 x i16 | stick x, y (Q15) |
| 50 | 2 x i16 | trackpad x, y (Q15) |
| 54 | u8 | trigger |
| 55 | u8 | battery raw |

The decoder is a Python port of `src/wmr_decode.c` and `src/wmr_decoded.c`.
`pi/tests/test_protocol.py` pins two sample frames to bytes produced by the C
implementation, so the two decoders agree bit for bit.

Batched, one FF14 version 2 batch. Two-byte header (`version = 2`,
`count = 1 .. 9`) followed by `count` complete FF12 records, oldest first. A
partial batch is flushed after `--batch-hold-ms` (default 5 ms) or when full.
Records of both hands may share a batch. Each carries its own hand.

Calibration, one FF13 window. A binary reply starts with the one-byte message
tag `0x13`, followed by the characteristic value. The tag keeps calibration
replies distinguishable from stream payloads, whose first byte is `0x52`
(raw), `0x01` (decoded record) or `0x02` (batch), so a client can read
calibration while subscribed. The value is the same layout the ESP32 serves on
FF13 (see [PROTOCOL.md](PROTOCOL.md)):

| Offset | Size | Field |
| --- | --- | --- |
| 0 | u8 | version `1` |
| 1 | u8 | hand, 0 LEFT / 1 RIGHT |
| 2 | u8 | state: 0 not read, 1 reading, 2 ready, 3 failed |
| 3 | u8 | reserved, 0 |
| 4 | u32 | blob length in bytes, 0 unless ready |
| 8 | u32 | offset of this window |
| 12 | u32 | CRC-32 of the whole blob, 0 unless ready |
| 16 | `n` | `blob[offset .. offset+n)`, `n = min(total_len - offset, 496)`; empty unless ready |

The largest message is 513 bytes (tag, header and 496 data bytes), well inside
the WebSocket limit. The blob is the controller config block exactly as read,
still obfuscated; the client checks the CRC-32 and de-obfuscates it as for BLE
FF13.

The daemon caches the blob per hand through `Bridge.set_calibration_blob` and
tracks `set_calibration_state` for in-progress or failed reads. The Pi-side
read from the controller over hidraw is not implemented yet, so on a fresh
daemon both hands report `not_read` and a calibration request returns a
header-only window until an ingest layer fills the cache.

### Status

```json
{
  "type": "status",
  "version": "0.3.25",
  "handFilter": ["LEFT", "RIGHT"],
  "uptimeS": 42.5,
  "hostTimeUs": 123456789,
  "hands": [
    {
      "hand": "LEFT",
      "connected": true,
      "address": "AA:BB:CC:DD:EE:FF",
      "reports": 7312,
      "decoded": 7300,
      "errors": 0,
      "delivered": 7300,
      "dropped": 0,
      "sequence": 7312,
      "lastRxAgeMs": 6.2,
      "lastActivityAgeMs": 18.4,
      "idleSleep": false
    },
    {
      "hand": "RIGHT",
      "connected": false,
      "address": "",
      "reports": 0,
      "decoded": 0,
      "errors": 0,
      "delivered": 0,
      "dropped": 0,
      "sequence": 0,
      "lastRxAgeMs": null,
      "lastActivityAgeMs": null,
      "idleSleep": false
    }
  ],
  "clients": 1,
  "subscribed": 1,
  "dropped": 0,
  "batches": 811,
  "calibration": {
    "version": 1,
    "headerSize": 16,
    "windowMax": 496,
    "maxBlob": 16384,
    "messageTag": 19,
    "states": ["not_read", "reading", "ready", "failed"],
    "hands": [
      {"hand": "LEFT", "state": "ready", "totalLen": 600, "crc32": 123456789},
      {"hand": "RIGHT", "state": "not_read", "totalLen": 0, "crc32": 0}
    ]
  }
}
```

`reports` counts reports received from the controller. `decoded` counts reports
successfully decoded while a decoded/batched client was subscribed. `delivered`
counts messages enqueued for a client (one per fragment on `raw`, one per
record on `decoded`, and one per record inside an enqueued `batched` message).
`errors` counts malformed frames or non-input report IDs. `dropped` counts
messages dropped because a client queue was full. The per-hand `dropped` counts
the same drops attributed to that hand (one per dropped fragment on `raw`, one
per dropped record on `decoded`, and one per record inside a dropped `batched`
message).

`version` and `handFilter` mirror the `hello` fields. `hands` remains the
per-hand statistics array. For the configured selector read `handFilter`.
`calibration.hands` reports each hand's FF13 state, blob length and CRC-32 so a
client can tell a not-yet-read hand from a ready one without issuing a read.

### Sequence and timing semantics

- Sequence numbers start at 1 for each hand after daemon start and increment for
  every input report, whether or not anyone is subscribed and whether or not
  decoding succeeds. They wrap as `uint32`.
- Reconnecting a controller does not reset the counter. Restarting the daemon
  does.
- A gap therefore means a report was lost before the bridge queued it (upstream
  loss), or a decoded record was discarded. It is not evidence of downstream
  loss. Use `dropped` for that.
- `device_ticks` is the controller's own 100 ns clock and remains the primary
  sensor timestep. `rx_time_us` is a host clock for latency/jitter only. It is
  Linux's monotonic clock, which counts from boot, not from daemon start. Its
  origin differs from the ESP32's `esp_timer`, so do not compare the raw values
  across bridges.
- The JSON `ping`/`pong` pair is the FF15 analogue. `t` is echoed unchanged and
  `serverTimeUs` is the host clock at reply. RTT is measured by the client.

## Companion app integration

The intended companion change is small:

1. Add a Wi-Fi transport that connects to `ws://<host>:8765/wmr`, performs the
   handshake/auth, and subscribes to `batched` (or `decoded` for debug).
2. Feed each binary message into the existing FF11 reassembler / FF12 parser /
   FF14 batch parser, based on the stream. A message whose first byte is `0x13`
   is a tagged FF13 calibration window, not stream data.
3. Map the JSON `status` into the existing link-status model and the JSON
   `ping`/`pong` into the latency screen (or keep FF15 semantics for BLE).
4. Reconnect with backoff. Re-subscribe after a reconnect. Sequence numbers
   continue across a network reconnect, so the existing gap detection still
   works. A bridge daemon restart resets them, which looks like the ESP32
   rebooting.
5. Treat the bridge as one source per Pi. Multi-bridge setups (one Pi per hand)
   work the same way as multiple ESP32 bridges: connect one WebSocket per Pi,
   each carrying its own LEFT or RIGHT hand.
6. Read calibration with `{"type":"calibration","hand": ... ,"offset": ... }` and
   collect 496-byte windows until `total_len` bytes, then verify the CRC-32.
   The request works while a stream is subscribed.

The FF16 analogue is the bridge `version` in `hello` and `status`. FF13 is
advertised in `hello.calibration`; a bridge with calibration disabled omits the
field rather than pretending to support it.

7. Show `hello.version` on the Pi connection. Compare it with the latest GitHub
   release and offer an upgrade when the app is connected and
   `hello.upgradeCapable` is true. Send `{"type":"upgrade"}`, map `busy` and
   `error` to a retry/label, and after `started` show that the bridge restarts
   and reconnect with the normal backoff. When `upgradeCapable` is missing or
   false, show the one-line `sudo install.sh` command from
   [Version display and app-triggered upgrade](#version-display-and-app-triggered-upgrade);
   the app never asks for `sudo` or a release URL.

## Security

This is a bench bridge, not an internet-facing service. The mechanical
safeguards below are enforced by the daemon. The transport is still plaintext.

- Loopback by default, token required on the LAN. `--bind` defaults to
  `127.0.0.1`. Serving the Pi's LAN address requires both an explicit
  `--bind 0.0.0.0` (or the Pi's address) and `--token`/`--token-file`. The
  daemon refuses to start otherwise. The token is what protects the owner's
  actual setup (Pi on the LAN, phone connecting to it).
  `--insecure-no-token` exists only as a typed escape hatch for an isolated lab
  link. Never use it on a shared or untrusted network.
- Browser origins are rejected by default. A request with an `Origin` header
  is refused unless that exact origin was passed with `--allow-origin`. The
  probe and the companion app do not send `Origin`. Browsers always do, so a
  web page cannot open a socket to the daemon.
- Bounded handshake. An HTTP upgrade that does not complete within 5 seconds
  is closed, so half-open connections cannot accumulate.
- The WebSocket is plaintext by itself. The token prevents unauthorised clients,
  but it does not encrypt controller data and does not resist an on-path
  attacker. Terminate TLS in front of the daemon (see [TLS termination](#tls-termination)) or use
  WPA2/WPA3, a dedicated/trusted network, or an SSH tunnel.
- Never port-forward 8765 from the internet. Bind only the interface you
  intend, and keep the firewall closed except for the LAN clients you expect.
- Store the token in a mode-600 file owned by the service user (`wmr`) and prefer
  `--token-file` over `--token` (the latter is visible in the process list). The
  daemon warns if the token file is group/world-readable. Its read error names
  the path and the uid it is running as.
- On the Bluetooth side, the Pi trusts the controllers you pair in BlueZ. The
  bridge assigns LEFT/RIGHT by HID name (or by `--left/--right`), which is not
  authentication. A spoofed device that BlueZ pairs could claim a hand. Bond the
  controllers deliberately and keep the Pi's Bluetooth adapter trusted-only.
- Controller motion data can be personal data. Captures and logs should be
  handled accordingly. The repository samples contain no Bluetooth address.
- The app upgrade trigger is authenticated and carries no input. The daemon
  runs as `wmr` and can only create the empty
  `/var/lib/wmr-bridge/upgrade.trigger`. `wmr-bridge-upgrade.path` starts a
  root oneshot that deletes the file before running the root-owned installed
  installer with the release-selection variables cleared and no
  `EnvironmentFile=`, then exits without reinstalling when the release is not
  newer. A tokenless bridge never advertises `upgradeCapable`; the root unit is
  `flock`-guarded and rate-limited. The trigger does not change the transport
  posture: a token holder on the LAN can ask root to install the next signed
  GitHub release, but cannot choose its contents.
- WebSocket clients have no rate limit and no authorisation beyond the token,
  and the daemon itself has no TLS. UDP discovery replies are rate-limited per
  source but carry no secret. Treat it as bench equipment with a small, local
  attack surface.

### TLS termination

The daemon deliberately has no TLS stack of its own. Put a TLS terminator on the
Pi and point the companion at `wss://<pi>:8443/` instead of
`ws://<pi>:8765/`:

```
phone (wss://) --TLS--> stunnel or nginx on the Pi --ws://127.0.0.1--> wmr_bridge
```

Keep the daemon on loopback (`--bind 127.0.0.1`) so only the terminator can
reach it, and keep the bearer token. TLS protects data in transit. The token
still authorises the client. Do not add `--insecure-no-token` because the
listener is TLS-wrapped.

stunnel (simplest). `/etc/stunnel/wmr-bridge.conf`:

```
[wmr-bridge]
accept = 0.0.0.0:8443
connect = 127.0.0.1:8765
cert = /etc/wmr-bridge/tls/fullchain.pem
key = /etc/wmr-bridge/tls/privkey.pem
```

`systemctl enable --now stunnel4`. stunnel forwards the TLS payload unchanged,
so the WebSocket upgrade, token and origin checks all run in the daemon as
before.

nginx (if it is already on the Pi). The upgrade headers are required:

```nginx
server {
    listen 8443 ssl;
    server_name wmr-bridge.local;
    ssl_certificate /etc/wmr-bridge/tls/fullchain.pem;
    ssl_certificate_key /etc/wmr-bridge/tls/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

Certificates. A phone trusts a public CA, not a name on the LAN. Use either:

- a real hostname with a Let's Encrypt certificate (DNS-01, so the Pi does not
  need to be reachable from the internet), and connect to that hostname; or
- a local CA/self-signed certificate and install that CA on the phone's user
  trust store, then connect to the name in the certificate's SAN. Self-signed
  certificates are rejected by a phone that has not been told to trust them, and
  the companion app cannot bypass that.

Do not use `--allow-origin '*'` to make a browser work through the terminator.
The companion and probe do not send an `Origin` header, and a browser page that
can reach the terminator should not be allowed to open the stream.

Firewall port 8443 to the LAN clients you expect, and keep 8765 closed. TLS
termination does not change the Classic Bluetooth posture. The Pi still trusts
whatever BlueZ paired, and assigns hands by HID name.

## Performance expectations and limits

- The Pi link removes BLE notification airtime from the phone path. It cannot
  remove Classic Bluetooth loss between controllers and the Pi.
- One Python process handles both hands. Encoding one 46-byte frame is cheap.
  The event loop should keep up with 2 x about 180 reports/s on a Pi 4.
  Batching reduces WebSocket writes but adds at most the hold time (5 ms
  default).
- The Pi 3B+ has less CPU headroom than a Pi 4 (Cortex-A53 at 1.4 GHz, 1 GB
  RAM). The event loop should still fit 2 x about 180 reports/s, but run only
  the bridge and the probe on the board during P5/P6 and record CPU load and
  `vcgencmd get_throttled` with the results.
- The default 512-message queue absorbs scheduling hiccups for roughly 1.5 s at
  320 records/s per client. A slower client drops the newest messages. It does
  not stall the controllers.
- Wi-Fi power saving, channel congestion and roaming cause latency spikes.
  Measure RTT/jitter with `tools/wifi_probe.py` before drawing conclusions.
- A loopback replay (`--replay`, client on `127.0.0.1`) measures encoding and
  socket cost only; it bypasses both the Classic controller link and Wi-Fi. When
  the phone reports FF14 loss, compare it with the daemon's 5 s
  `left=up/R(D)` / `right=up/R(D)` counters and `dropped`. A per-hand rate near
  the expected 150-180 reports/s with `dropped=0` means the bridge ingested and
  enqueued the stream; the missing records were lost upstream on the controller
  link or after the socket on Wi-Fi, not in the Pi's encoder or queue.
- The bridge has no hardware timestamp from the Wi-Fi stack. `rx_time_us` is
  taken when the daemon reads the report. Do not interpret it as an arrival
  timestamp at the adapter.

## Test plan

### Host tests (already runnable)

```bash
cd pi
./run_tests.sh
```

Covers: FF11 golden fragments and every capacity 13 .. 244 for both hands, FF12
golden record and C-parity sample records, FF14 batches, reassembler rules,
hidraw discovery/normalisation, WebSocket handshake/fragmentation/ping/close,
the JSON control protocol, authentication, stream exclusion, batching and
replay. It also covers the non-loopback-without-token refusal, the loopback
default, Origin rejection and allow-listing, the handshake timeout, the
`auth_failed` path for a non-ASCII token, per-hand drop accounting and closing
active sockets on shutdown, the `handFilter` payload in `hello`/`status`, the
stable `bridge_id` in `hello`/discovery, UDP probes and rate limiting, and mDNS
advertisement via avahi/systemd-resolved plus the absent-tool no-op. No hardware
or third-party packages.

Replay smoke test:

```bash
python3 -m wmr_bridge --replay ../samples/controller_frames.txt --bind 127.0.0.1 --port 8765
python3 tools/wifi_probe.py --stream batched --duration 10
```

`decoded` can legitimately trail `reports` by however long the daemon ran before
the client subscribed. Reports are only decoded while a decoded/batched
subscriber is attached (`pi/wmr_bridge/bridge.py` `ingest`). That difference
is not loss. Use sequence gaps and `dropped` to judge loss.

### Hardware checklist (not yet run)

First target: **Raspberry Pi 3 Model B+**, bench scheduled 2026-11-07. The
tester checklist and pinned image/package checksums are in
[DEPLOY_RPI.md](DEPLOY_RPI.md#0-bench-checklist-for-2026-11-07-raspberry-pi-3b).

| ID | Check | Pass criterion |
| --- | --- | --- |
| P1 | `--list` after pairing both controllers | two nodes, names map to LEFT/RIGHT, addresses present |
| P2 | Replay probe | ~160 rec/s, no gaps, no drops |
| P3 | Live one controller, `decoded` | buttons, stick, trigger and IMU change sensibly; sequence rises ~150-180/s; `errors` 0 after 60 s |
| P4 | Live both controllers | both hands stream concurrently; per-hand sequences independent; no decode errors |
| P5 | 5 GHz link, 10 min | `iw ... link` above 5000 MHz; `dropped` 0; probe RTT p50/p95 recorded; compare with 2.4 GHz in the same sitting |
| P6 | Upstream loss | record tick-based missing-report share per hand (as in the firmware runs) and compare with the ESP32 baseline; do not claim improvement from sequence gaps alone |
| P7 | Controller reconnect | power-cycle one controller; it reappears, the client stays subscribed, sequences continue (no reset) |
| P8 | Network reconnect | drop/rejoin Wi-Fi; client reconnects and re-subscribes; token still enforced |
| P9 | Two clients | raw and batched clients both receive, independent queues |
| P10 | 8 h soak | no memory growth, no fd leak, no queue drops at idle |
| P11 | Auto-pair from unpaired | start with both controllers unpaired; put one in pairing mode; unattended pair/trust/connect within 60 s; repeat for the other hand; with `--no-auto-pair` the bridge does not pair and pairing stays with the operator |
| P12 | TCP-to-BLE failover and jitter | transition time under 2.5 s, inter-frame delivery pause under 3 s, sequence numbers continue, clean upgrade back to TCP when Wi-Fi recovers |
| P13 | Controller idle sleep and power-off | 0x06 keep-alive pauses at 300 s ± 5 s; motion wakes controller in under 1.0 s; the BlueZ block drops the ACL at 900 s ± 5 s; the controller stays disconnected for the 60 s cooldown, then a button press reconnects it |
| P14 | Uplink priority and Sitting P gates | `eth0` route metric 100 preferred over 5 GHz Wi-Fi metric 200; `rttMs.p50` < 15 ms, gaps 0, missing share <= 3.0 %, non-null `serverStatus` with `dropped == 0` |

Record host model, OS, BlueZ/kernel versions, Wi-Fi band/channel, controller
addresses (or omit them), and the probe output in the bring-up run sheet
`docs/runs/sheets/bench.md` (Part A). Until P1-P14 have
been run, treat the Pi path as unverified.

## Daemon logs

The daemon uses Python's `logging` module. Hand and client events are logged at
INFO/WARNING:

- Hand connect/disconnect: `LEFT connected: /dev/hidraw0 (AA:BB: ... )` and
  `LEFT disconnected: /dev/hidraw0 after 123 report(s)`.
- Client connect/disconnect: `client connected: <peer>` / `client disconnected: <peer>`.
- Subscribe/unsubscribe: `client <peer> subscribed to batched` /
  `client <peer> unsubscribed`.
- No controller: a one-time warning `no WMR controllers found; put one in pairing mode and keep it near the Pi, then check ... `.
- Auto-pair: one `auto-pair: scanning ... ` line per attempt (at most every 30 s while a hand is missing), then `found`, `pairing`, `paired`, `trusting`, `connecting`, and `connected` lines for the controller it hears. A failed attempt logs `pairing <MAC> failed: <BlueZ error>`. `wmr-bridge status` prints the same state without reading the journal.
- Status monitor line: `left=up/1234(1200) right=down/0(0)` (`up`/`down` is connection state, then reports(delivered)).
- `hello.version` reports the package version (`0.3.25`; `0.1.0` before PR #78 merged).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `no /dev/hidraw nodes` | `systemctl status bluetooth`, `lsmod \| grep hidp`, controller paired/trusted/connected |
| `bluetoothctl show` says `Powered: no`, `Pairable: no`, Class 0 | adapter firmware crashed or the UART attach failed; `btmon` showing `HCI Reset` answered by `Hardware Error` (0x10, code 0x00) is the crashed-controller signature. Reboot the Pi; if the error survives the reboot, power-cycle the board. Restarting only `bluetooth.service` does not reload the firmware |
| Phone will not re-pair after the upgrade; adapter `pairable=no` | a stored LE bond closes pairability. Open a window with `wmr-bridge pair` (180 s), or remove the phone's stale bond with `bluetoothctl remove <MAC>`. v0.3.25 does not touch `/var/lib/bluetooth`; the upgrade scripts now leave bluetoothd alone when `JustWorksRepairing` is already set |
| Wi-Fi auth fails with Bluetooth off | the shared token is only learned over the BLE FF13 encrypted read; the companion's manual Wi-Fi entry does not persist a token. Copy it from `/etc/wmr-bridge/token` (unchanged across upgrades) or read it over BLE |
| Node exists but `--list` shows name `-` | custom controller name; use `--left/--right` with the MAC or path |
| Permission denied opening hidraw | run as root or add the user to `input`; check udev mode |
| Controllers connect to the old ESP32 | power it off or unpair/forget the controller's previous host; pair to the Pi |
| Client gets `auth_failed` | token mismatch; check `/etc/wmr-bridge/token` and the client flag |
| Client gets `stream_active` | send `unsubscribe` before switching streams |
| High RTT or drops | confirm 5 GHz, disable Wi-Fi power save, check channel congestion, inspect `status.dropped` |
| Port unreachable | `--bind 0.0.0.0` (plus a token, or the daemon refuses to start), firewall, correct Pi address, same VLAN/client isolation off |

## Future work

- Hardware validation of P1-P14 using the bring-up run sheet
  `docs/runs/sheets/bench.md` (Part A).
- Companion app Wi-Fi transport (`wmr_ws` and connect UI in `wmr-companion`).
- Native TLS in the daemon itself (external termination is documented in
  [TLS termination](#tls-termination)).
- Pi-side FF13 controller read over hidraw (the cache and WebSocket forwarding
  are in place; a fresh daemon starts `not_read`).
- Native system packaging beyond the released `.deb` (`pip install`,
  APT repository, image).
- Bench-validate `--hand` single-hand mode and UDP/mDNS discovery on real
  hardware.

## Files

| Path | Contents |
| --- | --- |
| `pi/wmr_bridge/` | host bridge package: protocol, decoder, BlueZ, WebSocket, server, UDP/mDNS discovery, CLI |
| `pi/tools/wifi_probe.py` | bench client: throughput, sequence gaps, tick-based missing reports, RTT (min/p50/p95/max), status, UDP discovery, JSON output, Sitting P gates |
| `pi/build_package.sh` | builds `dist/wmr-bridge-<version>.tar.gz` plus `.sha256` |
| `pi/build_deb.sh` | builds `dist/wmr-bridge_<version>_all.deb` and its `.sha256` (no root; maintainer scripts in `pi/debian/`) |
| `pi/tools/verify_package.sh` | checks package layout, version and checksum |
| `pi/tools/dry_run.sh` | stages the `.deb`, checks reinstall/remove, runs replay (no hardware) |
| `pi/install.sh` | unattended installer; piped mode fetches and verifies the release `.deb` |
| `pi/systemd/` | service unit and environment example (`WMR_BRIDGE_ARGS`) |
| `pi/udev/` | udev rule (`99-wmr-bridge.rules`) granting `input` group access to `/dev/hidraw*` |
| `pi/networkmanager/` | NetworkManager drop-in disabling Wi-Fi power save persistently |
| `pi/tests/` | host tests, including the package/dry-run pre-flight (no hardware) |
