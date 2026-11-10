# Deploying WMR Bridge to a Raspberry Pi

New to the Pi? Read the simple-English beginner guide first:
[Raspberry Pi install guide](RPI_INSTALL_GUIDE.md).

The Pi host bridge runs under stock Raspberry Pi OS Lite and carries WMR
controllers over Classic Bluetooth to a phone or PC by WebSocket over 5 GHz
Wi-Fi. Supported boards: Pi 3B+, Pi 4 Model B, Pi 5. Pi Zero 2 W is not
supported (2.4 GHz only).

**Do not flash a custom OS image.** That wipes `/var/lib/bluetooth` and every
controller pairing. Use the package or installer so bonds, tokens and local
config survive updates.

## 0. Bench checklist for 2026-11-07 (Raspberry Pi 3B+)

- [ ] Flash Raspberry Pi OS Lite **64-bit**.
- [ ] Use a 5 V / 2.5 A micro-USB supply; `vcgencmd get_throttled` prints
      `throttled=0x0`.
- [ ] Join the dedicated **5 GHz** SSID; `iw dev wlan0 link` shows `freq: 5xxx`.
- [ ] `uname -m` prints `aarch64`; `systemctl is-active bluetooth` prints
      `active`.
- [ ] Install the package; `wmr-bridge --version` prints the installed version.
- [ ] Service is `enabled` and `active`.
- [ ] Let the bridge auto-pair both controllers (or pair by hand with
      `--no-auto-pair`); both appear in `/sys/class/hidraw/*/device/uevent`.
- [ ] P11 auto-pair check passes: from unpaired, both controllers pair, trust
      and connect within 60 s; `--no-auto-pair` leaves pairing to the operator.
- [ ] Replay smoke test passes with zero gaps.
- [ ] Live `wifi_probe` shows both hands above 0 records/s and `dropped=0`.
- [ ] Record the host metadata and P1-P14 results for
      [`pi-bridge.md`](runs/sheets/pi-bridge.md) §5.

The 3B+ is a Cortex-A53 at 1.4 GHz with 1 GB RAM and Bluetooth 4.2 Classic.
Run only the bridge and probe during timing; keep 3B+ results separate from
Pi 4/5 data.

## 1. Hardware

- Pi 3B+, Pi 4 Model B or Pi 5. Pi 4/5 are the reference targets.
  Stock Pi OS already carries the Bluetooth and Wi-Fi firmware; no extra
  package is needed.
- MicroSD card (8 GB+) and reader.
- Correct supply: 5 V / 2.5 A micro-USB for 3B+, 5 V / 3 A USB-C for Pi 4,
  official supply for Pi 5.
- A dedicated **5 GHz** SSID. Bluetooth Classic is at 2.4 GHz; running the Pi
  Wi-Fi at 2.4 GHz causes packet loss and radio contention.
- Two WMR controllers with fresh batteries.
- A workstation with Raspberry Pi Imager.

## 2. Flash the OS

1. In Raspberry Pi Imager, choose the board (3B and 3B+ are under
   `Raspberry Pi 3`).
2. Choose `Raspberry Pi OS (other)` -> **`Raspberry Pi OS Lite (64-bit)`**.
   Trixie (2026-10-06) is current; Bookworm (2025-05-13) is also supported.
3. Choose the card. In Edit Settings set a hostname (e.g. `wmr-pi`), a user and
   password, the **5 GHz** SSID and country, and enable SSH (key or password).
4. Write, insert and boot. Wait 1-2 minutes.

Confirm:

```bash
uname -m                      # aarch64
systemctl is-active bluetooth # active
```

If `uname -m` is `armv7l`, the 32-bit image was flashed; re-flash 64-bit. If
Bluetooth is inactive: `sudo systemctl enable --now bluetooth`.

Pinned images for 2026-11-07:

| Release | SHA-256 (`.img.xz`) |
|---|---|
| Trixie 2026-10-06 | `483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843` |
| Bookworm 2025-05-13 | `62d025b9bc7ca0e1facfec74ae56ac13978b6745c58177f081d39fbb8041ed45` |

Verify with `sha256sum <image>.img.xz`. If Imager offers a newer release,
record its date and hash instead.

## 3. Network

```bash
ssh <user>@wmr-pi.local
iw dev wlan0 link              # freq must be 5xxx
iw dev wlan0 get power_save    # off; the installer adds a drop-in
```

If the link is at 2.4 GHz, reconnect to the 5 GHz SSID before continuing.

### Automatic 5 GHz preference

The package applies a 5 GHz preference during install and on every service start. `postinst` and `install.sh` run `wmr-bridge --prefer-5ghz --no-reconnect`. This saves a profile named `<connection>-5ghz` without moving an active SSH session. The service unit runs `wmr-bridge --prefer-5ghz` before the daemon starts. That command may reconnect to the 5 GHz profile when a 5 GHz BSSID is visible.

The base connection stays in place as the 2.4 GHz fallback. A 2.4 GHz-only radio, such as the CYW43439 in a Pi Zero 2 W, is a warning-only no-op. The command reports that the adapter has no 5 GHz radio.

The bridge reports the Wi-Fi band and the active uplink in `hello` and `status`. The `"wifi"` object includes `iface`, `connected`, `freqMhz`, `band`, `supports5Ghz`, `contentionWarning`, and `warning`. `contentionWarning` is true when the Pi is on 2.4 GHz. The `"uplink"` object names the default-route interface (`eth0`, `wlan0`, or another), its kind, and its addresses; `hello.transport` is `"ethernet"` when `eth0` is active. `wmr-bridge status` prints the uplink, band, 5 GHz support, and any warning.

## 4. Install

The fastest path is the one-line installer. It downloads the package, checks
it against SHA256SUMS, and installs it:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
```

A short browser guide for the same steps is at
https://nightcinder.github.io/WMR_Bridge/pi.html.

Tags before #104 (such as `v0.3.0`) carry firmware images only. Tags from
#104 onward also publish `install.sh` and `wmr-bridge_<version>_all.deb`.
`v0.3.15` publishes nine files: the three merged factory images
(`wmr-bridge-factory.bin`, `-left`, `-right`), the three application images
(`wmr-bridge-app.bin`, `-left`, `-right`), the `.deb`, `install.sh` and
`SHA256SUMS`. `v0.3.14` and `v0.3.13` also published nine; `v0.3.12` published
six (no application images). Earlier tags
such as `v0.3.11` attached the full build output: the factory, application,
bootloader and partition-table images for all three variants, the three
manifests, `build-info*.json`, `index.html`, the tarball and per-file `.sha256`
checksums. The pinned list below is `v0.3.15`.

Pinned 2026-11-09 assets:

| Asset | SHA-256 |
|---|---|
| `wmr-bridge-factory.bin` | (see SHA256SUMS) |
| `wmr-bridge-left-factory.bin` | (see SHA256SUMS) |
| `wmr-bridge-right-factory.bin` | (see SHA256SUMS) |
| `wmr-bridge-app.bin` | (see SHA256SUMS) |
| `wmr-bridge-left-app.bin` | (see SHA256SUMS) |
| `wmr-bridge-right-app.bin` | (see SHA256SUMS) |
| `wmr-bridge_<version>_all.deb` | (see SHA256SUMS) |
| `install.sh` | (see SHA256SUMS) |
| `SHA256SUMS` | (shared digest list) |

**From a checkout (current or pre-release):**

```bash
git clone https://github.com/nightcinder/WMR_Bridge.git
cd WMR_Bridge
./pi/build_deb.sh
sudo apt install ./dist/*.deb
# or: sudo ./pi/install.sh
```

**One-line installer:**

```bash
# latest
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
# pin package and tag
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.15/install.sh | WMR_BRIDGE_VERSION=<pi-version> WMR_BRIDGE_TAG=v0.3.15 sudo -E bash
```

**Standalone `.deb`:**

```bash
wget https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.15/wmr-bridge_<version>_all.deb
wget https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.15/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
sudo apt install ./wmr-bridge_<version>_all.deb
```

Replace `<version>` with the Pi package version.

The installer downloads the release `.deb` and verifies it against
`SHA256SUMS`, then installs with `apt-get`. The package creates the `wmr`
service user, installs to `/opt/wmr-bridge/pi`, adds the udev rule and the
NetworkManager power-save drop-in, writes a `0640 wmr:wmr` token at
`/etc/wmr-bridge/token`, installs the systemd unit and puts
`/usr/bin/wmr-bridge` on `PATH`. The piped installer needs `apt-get`; without
it, install from a checkout or a locally built tarball (see the
[Pi bridge guide](PI_BRIDGE.md#installation)).

Start and check:

```bash
wmr-bridge --version
wmr-bridge --list
wmr-bridge status
systemctl status wmr-bridge
journalctl -u wmr-bridge -n 50 --no-pager
```

`wmr-bridge status` prints the adapter's `powered`/`pairable`/`discovering`
flags and the Bond/hidraw state of both hands.

## 5. Pair controllers (automatic)

The bridge pairs controllers on its own. Put each controller in pairing mode.
The bridge scans, pairs, trusts, and connects it. You do not run `bluetoothctl`.

Put one controller in pairing mode:

1. Turn on the controller. Hold the front Windows button for 2 seconds.
2. Open the battery cover.
3. Hold the small pairing button inside the battery compartment until the LEDs
   pulse slowly.

Wait for the bridge to pair it. Repeat for the other controller. Trust is
stored automatically, so the controller reconnects after a Pi reboot.

Watch the pairing steps and check the state:

```bash
journalctl -u wmr-bridge -f
wmr-bridge status
```

The log shows `auto-pair: scanning`, then `found`, `paired`, `trusting`,
`connecting` and `connected` with the controller's address. `wmr-bridge status`
shows each hand's `/dev/hidrawN` node and BlueZ bond flags; a missing hand says
`no hidraw node`.

Confirm the HID nodes:

```bash
cat /sys/class/hidraw/hidraw*/device/uevent | grep -E 'HID_NAME|HID_UNIQ'
```

Both `Motion controller - Left` and `Motion controller - Right` should appear.

If nothing is found, collect the diagnostic output and check the list below:

```bash
wmr-bridge status
rfkill list bluetooth
bluetoothctl show
bluetoothctl devices
journalctl -u wmr-bridge -n 200 --no-pager
```

**Manual pairing (optional).**
If automatic pairing fails, pair by hand with `bluetoothctl`:

```bash
bluetoothctl
scan on
```

Put one controller in pairing mode. Use the pairing button inside the battery
compartment. Then:

```text
pair AA:BB:CC:DD:EE:01
trust AA:BB:CC:DD:EE:01
connect AA:BB:CC:DD:EE:01
```

Repeat for the other controller, then `scan off` and `exit`.

## 6. Test with `wifi_probe`

```bash
python3 /opt/wmr-bridge/pi/tools/wifi_probe.py \
    --host 127.0.0.1 \
    --token-file /etc/wmr-bridge/token \
    --stream batched \
    --duration 10
```

From another machine on the 5 GHz network, use `--host wmr-pi.local` and
`--token <string>` copied from the token file.

Pass: `auth_ok`, `subscribed`, both hands report records, `dropped=0`, and
`sequenceGaps` / `missingFromTicks` zero or near zero on clean RF. The summary
JSON carries `records`, `lossPct`, `sequenceGaps`, `rttMs` and `serverStatus`.
BLE-only fields report `n/a`.

## 7. Upgrade

From a checkout:

```bash
git pull
./pi/build_deb.sh
sudo apt install ./dist/*.deb
```

From release artifacts:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
# pin: WMR_BRIDGE_VERSION=<pi-version> WMR_BRIDGE_TAG=<tag>
```

After upgrade, confirm the bond, token and environment survived:

```bash
bluetoothctl devices Paired
ls -la /etc/wmr-bridge/token
cat /etc/wmr-bridge/wmr-bridge.env      # if customised
sudo systemctl restart wmr-bridge
python3 /opt/wmr-bridge/pi/tools/wifi_probe.py --host 127.0.0.1 --token-file /etc/wmr-bridge/token --stream batched --duration 5
```

Both controllers should reconnect without re-pairing.

## 8. Troubleshooting

- **Loss or jitter:** check `iw dev wlan0 link` for 5 GHz and
  `iw dev wlan0 get power_save` for `off`. The drop-in
  `/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf` should contain
  `wifi.powersave = 2`.
- **Token error:** `sudo chown wmr:wmr /etc/wmr-bridge/token`,
  `sudo chmod 0640 /etc/wmr-bridge/token`, then restart. For the host probe,
  add your user to `wmr` (`sudo usermod -aG wmr "$USER"`) and log in again.
- **No `/dev/hidraw*`:** load `hidp`, persist it in
  `/etc/modules-load.d/wmr-bridge.conf`, and restart Bluetooth.
- **Automatic pairing does not pair a controller:** `wmr-bridge status` shows
  adapter power/pairable/scanning and each hand's bond; `journalctl -u
  wmr-bridge -n 200 --no-pager` shows `auto-pair: scanning`, `found`, `paired`
  or the BlueZ error on a failed attempt. If `found` never appears, remove the
  controller's stale bond on the ESP32 or PC, or `bluetoothctl remove <MAC>` on
  the Pi, then retry in pairing mode.
- **Controller will not reconnect after reboot:** `bluetoothctl trust <MAC>`.
- **3B+ throttling:** use a 5 V / 2.5 A supply and cooling; re-check
  `vcgencmd get_throttled` and `vcgencmd measure_temp`.
