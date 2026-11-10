# Raspberry Pi install guide (simple English)

This guide takes you from a blank SD card to a working WMR Bridge.
It uses short sentences and plain words.
If a word is new to you, check the glossary at the end.

WMR Bridge is the software that reads your WMR controllers over Bluetooth
and sends their data to your phone or PC over Wi-Fi.

## Before you start

You need these things:

- A Raspberry Pi 3 Model B+, Pi 4, or Pi 5.
- A microSD card of 8 GB or more and a card reader.
- A power supply. Use 5 V / 2.5 A micro-USB for a Pi 3B+, 5 V / 3 A USB-C for a Pi 4, and the official Pi 5 supply for a Pi 5.
- A Wi-Fi network that uses the 5 GHz band.
- Two WMR controllers with fresh batteries.
- A computer with Raspberry Pi Imager installed.

A Pi Zero 2 W does not work. Its Wi-Fi uses only the 2.4 GHz band.

## Step 1: Flash Raspberry Pi OS Lite (64-bit)

Raspberry Pi Imager writes the operating system to the SD card.

1. Open Raspberry Pi Imager.
2. Choose Device. Pick your board. For a 3B+, pick `Raspberry Pi 3`.
3. Choose OS. Pick `Raspberry Pi OS (other)`, then `Raspberry Pi OS Lite (64-bit)`.
4. Choose Storage. Pick your microSD card.
5. Click Next. When asked to apply settings, pick Edit Settings.
   - Hostname: enter a name, for example `wmr-pi`.
   - Username and Password: set a username and a password.
   - Wireless LAN: enter your 5 GHz Wi-Fi name and password. Set your country code.
   - Services: enable SSH. Use a key or a password.
6. Save the settings. Click Yes to write the card.
7. Put the SD card in the Pi and power it on. Wait 1 to 2 minutes.

## Step 2: Check the OS and Bluetooth

SSH into the Pi:

```bash
ssh <username>@wmr-pi.local
```

Run these two checks:

```bash
uname -m                      # must print aarch64
systemctl is-active bluetooth # must print active
```

If `uname -m` prints `armv7l`, you flashed the 32-bit image. Flash the 64-bit one again.
If Bluetooth is inactive, start it:

```bash
sudo systemctl enable --now bluetooth
```

## Step 3: Check the Wi-Fi band

The Pi and your phone or PC must use the 5 GHz band. Run:

```bash
iw dev wlan0 link
```

Look for `freq: 5xxx`, for example `freq: 5180`. If you see `24xx`, connect the Pi to your 5 GHz network first.

Also check power saving:

```bash
iw dev wlan0 get power_save
```

The package installs a setting to turn power saving off. That setting takes effect after a reboot or a Wi-Fi reconnect. For now, just note the value.

## Step 4: Install WMR Bridge

The installer does the whole setup in one step. It downloads the release
package, checks it against SHA256SUMS, installs it, and starts the service.

Run this on the Pi:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
```

A short browser guide for the same steps is at
https://nightcinder.github.io/WMR_Bridge/pi.html.

The installer creates the `wmr` user, installs files under `/opt/wmr-bridge/pi`,
adds the udev rule and the NetworkManager power-save setting, writes the token
at `/etc/wmr-bridge/token`, and starts `wmr-bridge.service`.

To pin a release, set the version and the tag:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.22/install.sh | WMR_BRIDGE_VERSION=<pi-version> WMR_BRIDGE_TAG=v0.3.22 sudo -E bash
```

The Pi package version matches the repository `VERSION`, and the release
workflow refuses to build unless the tag equals `v$(cat VERSION)`. So a release
cut from this tree reports one version across firmware, Pi package and tag.
The current released version is v0.3.22; its Pi package is `0.3.22`.

To check a download by hand, fetch SHA256SUMS from the same release and run:

```bash
sha256sum -c SHA256SUMS --ignore-missing
```

The `v0.3.22` release carries nine files: three merged factory images
(`wmr-bridge-factory.bin`, `-left`, `-right`), three application images
(`wmr-bridge-app.bin`, `-left`, `-right`), the `0.3.22` package, `install.sh`,
and SHA256SUMS. There are no per-file checksum files.

Reboot the Pi so the Wi-Fi power-saving setting takes effect:

```bash
sudo reboot
```

Wait 1 to 2 minutes. Then SSH back in:

```bash
ssh <username>@wmr-pi.local
```

## Step 5: Check the service

The installer starts the service. Check it:

```bash
wmr-bridge --version
wmr-bridge --list
systemctl status wmr-bridge
systemctl is-active wmr-bridge   # active
journalctl -u wmr-bridge -n 50 --no-pager
```

If the service is not running, start it with `sudo systemctl enable --now wmr-bridge`.

## Step 6: Pair the controllers (automatic)

The Pi bridge supports Windows Mixed Reality controllers only. It does not
support PSVR2 Sense or Nintendo Switch controllers.

The bridge pairs controllers on its own. You put a controller in pairing mode.
The bridge finds it, pairs it, trusts it, and connects it. You do not run
`bluetoothctl`.

Put each controller in pairing mode, one at a time:

1. Turn on the controller. Hold the front Windows button for 2 seconds.
2. Open the battery cover.
3. Hold the small pairing button inside the battery compartment until the LEDs
   pulse slowly.

Wait for the bridge to pair the controller. Repeat for the other controller.

Check the pairing state:

```bash
wmr-bridge status
```

Each hand should show its `/dev/hidrawN` node. A hand that is still missing
shows `no hidraw node`. You can also watch the pairing steps live:

```bash
journalctl -u wmr-bridge -f
```

Look for `auto-pair: scanning`, then `found`, `paired` and `connected` with the
controller's address. Then check that both controllers are registered:

```bash
cat /sys/class/hidraw/hidraw*/device/uevent | grep -E 'HID_NAME|HID_UNIQ'
```

You should see entries for both `Motion controller - Left` and
`Motion controller - Right`.

The bridge sees each controller as soon as it connects; you do not restart
the service.

If a controller does not pair on its own, see "Automatic pairing does not
work" and "Manual pairing (optional)" in the "If something fails" section.

## Step 7: Check it works

Run the test client on the Pi:

```bash
sudo python3 /opt/wmr-bridge/pi/tools/wifi_probe.py \
    --host 127.0.0.1 \
    --token-file /etc/wmr-bridge/token \
    --stream batched \
    --duration 10
```

Check the output. Both hands should show records above zero. Gaps should be zero. Dropped should be zero.

## Step 8: Connect a client

The bridge listens on `ws://<pi-ip>:8765/wmr`. Read the token on the Pi:

```bash
sudo cat /etc/wmr-bridge/token
```

Paste that token into the companion app. The companion app does not discover
the Pi: enter its host and port by hand. The v0.3.22 service advertises
`_wmr-bridge._tcp` for discovery tools (`--advertise-mdns`), but the app still
takes a manual host and port.

## If something fails

**High report loss or jitter.**
The Wi-Fi may be on the 2.4 GHz band, or power saving may be on.
Run `iw dev wlan0 link` and make sure `freq` is above 5000.
Run `iw dev wlan0 get power_save` and make sure it says `off`.
If it says `on`, reboot the Pi with `sudo reboot` so the package's NetworkManager setting takes effect. You can also check that `/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf` contains `wifi.powersave = 2`.

**Service fails with a token error.**
The token file permissions may be wrong. Fix them:

```bash
sudo chown wmr:wmr /etc/wmr-bridge/token
sudo chmod 0640 /etc/wmr-bridge/token
sudo usermod -aG wmr "$USER"   # log out and back in, or use sudo for the probe
sudo systemctl restart wmr-bridge
```

**No hidraw nodes.**
The controllers may be paired but not connected. Or the `hidp` module may not be loaded. Run:

```bash
sudo modprobe hidp
echo hidp | sudo tee /etc/modules-load.d/wmr-bridge.conf
sudo systemctl restart bluetooth
```

**Controller does not reconnect after a reboot.**
Make sure the controller is trusted. Run:

```bash
bluetoothctl trust <MAC-ADDRESS>
```

**Automatic pairing does not work.**
Run the status command and read the recent service log:

```bash
wmr-bridge status
journalctl -u wmr-bridge -n 200 --no-pager
```

`wmr-bridge status` shows whether the Bluetooth adapter is powered, pairable
and scanning, and whether each hand has a bond and a hidraw node. In the log,
`auto-pair: scanning` is the heartbeat, `found`/`paired` shows a controller was
heard, and `pairing <MAC> failed: ...` gives the BlueZ error.

**A phone sees the bridge but will not pair.**
Open a pairing window on the running service, then pair from the phone:

```bash
wmr-bridge pair
```

The window stays open for 180 seconds by default; `wmr-bridge pair --seconds 60`
changes that, and `wmr-bridge pair --close` ends the manual window (pairability
then follows the stored-bond rule). Each change prints
its reason in the journal, for example
`BLE pairing window open (manual window, 180 s left); Adapter1.Pairable -> on`.
A stored LE bond from another phone closes the adapter after the window; remove
that bond in `bluetoothctl` or open a new window. With a window open, the phone
that owns the stored bond may forget the bridge and pair again from its
Bluetooth settings: the
installer sets `JustWorksRepairing = confirm` so BlueZ asks the bridge's agent
instead of rejecting the Just Works confirmation. If the journal shows
`BlueZ JustWorksRepairing = never`, re-run the installer to apply that setting.

If the log says `auto-pair: Bluetooth adapter is not usable yet`, check that
Bluetooth is running and not blocked. The service clears a boot-time soft
block as root before start and keeps retrying, so no restart is needed once
the block clears:

```bash
rfkill list bluetooth
systemctl status bluetooth
sudo rfkill unblock bluetooth
```

`automatic controller pairing is off ... until restart` names a scan failure;
restart the service after fixing it:

```bash
sudo systemctl restart wmr-bridge
```

If `found` never appears, the controller is still bonded to another host (the
ESP32 or a PC). Remove that old pairing, or forget the controller on the other
host, then put it in pairing mode again. If it says `AuthenticationFailed`,
remove the stale bond on the Pi with `bluetoothctl remove <MAC-ADDRESS>` and
retry.

**Manual pairing (optional).**
If automatic pairing fails, pair by hand with `bluetoothctl`:

```bash
bluetoothctl
```

Turn scanning on:

```text
[bluetooth]# scan on
```

Put one controller in pairing mode. Use the pairing button inside the battery
compartment. Find the controller in the scan output. Then run:

```text
[bluetooth]# pair AA:BB:CC:DD:EE:FF
[bluetooth]# trust AA:BB:CC:DD:EE:FF
[bluetooth]# connect AA:BB:CC:DD:EE:FF
```

Use the real MAC address from your scan output. Repeat for the other controller.
Stop scanning and exit:

```text
[bluetooth]# scan off
[bluetooth]# exit
```

**Pi 3B+ under-voltage or throttling.**
Use a 5 V / 2.5 A supply. Check:

```bash
vcgencmd get_throttled
```

The output must be `throttled=0x0`. Also check temperature:

```bash
vcgencmd measure_temp
```

## Glossary

- **BlueZ**: the Bluetooth software that runs on Linux. It pairs and connects controllers.
- **HID (Human Interface Device)**: a standard way for a controller to send button and stick data.
- **hidraw**: a Linux file (like `/dev/hidraw0`) that carries raw controller reports.
- **5 GHz band**: a Wi-Fi frequency band. It avoids the 2.4 GHz band that Bluetooth uses.
- **SSH**: a way to type commands on the Pi from another computer.
- **systemd**: the Linux system that starts and manages services like `wmr-bridge`.
- **token**: a secret password file. The bridge requires it when accessed over the network.
- **.deb**: a Debian package file. It installs software on Raspberry Pi OS.
- **MAC address**: a unique hardware address for a controller, like `AA:BB:CC:DD:EE:FF`.
- **Imager**: Raspberry Pi Imager, the program that writes the OS to an SD card.