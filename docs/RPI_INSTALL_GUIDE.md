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
- A power supply. Use 5 V / 2.5 A micro-USB for a Pi 3B+.
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

The installer turns power saving off later. For now, just note the value.

## Step 4: Install WMR Bridge

This guide uses the released package version 0.2.4 from release v0.3.11.

Download the package:

```bash
wget https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.11/wmr-bridge_0.2.4_all.deb
```

Check the file hash (optional but recommended):

```bash
sha256sum wmr-bridge_0.2.4_all.deb
```

The expected hash is `8c54616806ba7cddb78f0e68a71f2f3503e23c4e4d16625129ea220c3a841eba`.

Install the package:

```bash
sudo apt install ./wmr-bridge_0.2.4_all.deb
```

The package creates a system user, sets up files, and creates a security token.

## Step 5: Start and check the service

The bridge runs as a system service. Check it:

```bash
wmr-bridge --version   # wmr_bridge 0.2.4
wmr-bridge --list
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wmr-bridge
```

Check the status:

```bash
systemctl status wmr-bridge
systemctl is-enabled wmr-bridge   # enabled
systemctl is-active wmr-bridge    # active
journalctl -u wmr-bridge -n 50 --no-pager
```

## Step 6: Pair a controller

Pair each controller over Bluetooth Classic. Start the Bluetooth tool:

```bash
bluetoothctl
```

Turn scanning on:

```text
[bluetooth]# scan on
```

Put the Left controller into pairing mode. Hold the Windows button under the battery cover until the LEDs pulse slowly.
Find the controller in the scan output. Then run:

```text
[bluetooth]# pair AA:BB:CC:DD:EE:01
[bluetooth]# trust AA:BB:CC:DD:EE:01
[bluetooth]# connect AA:BB:CC:DD:EE:01
```

Use the real MAC address from your scan output. Repeat for the Right controller. Stop scanning and exit:

```text
[bluetooth]# scan off
[bluetooth]# exit
```

Check that both controllers are registered:

```bash
cat /sys/class/hidraw/hidraw*/device/uevent | grep -E 'HID_NAME|HID_UNIQ'
```

You should see entries for both `Motion controller - Left` and `Motion controller - Right`.

Restart the bridge so it sees both controllers:

```bash
sudo systemctl restart wmr-bridge
journalctl -u wmr-bridge -n 30 --no-pager
```

## Step 7: Check it works

Run the test client on the Pi:

```bash
python3 /opt/wmr-bridge/pi/tools/wifi_probe.py \
    --host 127.0.0.1 \
    --token-file /etc/wmr-bridge/token \
    --stream batched \
    --duration 10
```

Check the output. Both hands should show records above zero. Gaps should be zero. Dropped should be zero.

## If something fails

**High report loss or jitter.**
The Wi-Fi may be on the 2.4 GHz band, or power saving may be on.
Run `iw dev wlan0 link` and make sure `freq` is above 5000.
Run `iw dev wlan0 get power_save` and make sure it says `off`.

**Service fails with a token error.**
The token file permissions may be wrong. Fix them:

```bash
sudo chown wmr:wmr /etc/wmr-bridge/token
sudo chmod 0600 /etc/wmr-bridge/token
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
Make sure you trusted each controller during pairing:

```bash
bluetoothctl trust <MAC-ADDRESS>
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