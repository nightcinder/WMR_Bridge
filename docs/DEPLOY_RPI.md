# Deploying WMR Bridge to a Raspberry Pi (Tester Runbook)

This runbook guides testers through deploying and updating the WMR Bridge
software on a Raspberry Pi (Raspberry Pi 4 Model B or Raspberry Pi 5).

The Pi host bridge runs under stock Raspberry Pi OS Lite and bridges WMR
controllers over Classic Bluetooth to the phone/PC via WebSocket over 5 GHz
Wi-Fi.

> **Important**: Deployment uses stock Raspberry Pi OS Lite (64-bit) and the
> official Debian package or one-line installer. **Do not flash custom OS
> images.** Flashing an image wipes `/var/lib/bluetooth`, destroying controller
> pairings on every update. With package-based deployment, Bluetooth bonds,
> tokens, and local configuration persist across updates.

---

## 1. Hardware Prerequisites

- **Raspberry Pi 4 Model B** or **Raspberry Pi 5**
  *(Raspberry Pi 3 Model B+ is expected to work with lower CPU headroom.
  **Raspberry Pi Zero 2 W is not supported** because it lacks 5 GHz Wi-Fi).*
- MicroSD card (8 GB or larger) and card reader.
- Power supply for your Raspberry Pi.
- Dedicated **5 GHz Wi-Fi network**.
  - **Requirement**: The Pi and client device (phone or PC) MUST connect to a
    5 GHz Wi-Fi SSID. Bluetooth Classic operates in the 2.4 GHz spectrum;
    running Wi-Fi at 2.4 GHz on the Pi causes destructive packet loss and
    radio coexistence contention.
- Two Windows Mixed Reality (WMR) controllers with fresh batteries.
- A workstation with [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
  installed.

---

## 2. Step 1: Flash Raspberry Pi OS Lite (64-bit)

Use Raspberry Pi Imager to write the stock operating system and pre-configure
networking and authentication:

1. Launch **Raspberry Pi Imager**.
2. **Choose Device**: Select `Raspberry Pi 4` or `Raspberry Pi 5`.
3. **Choose OS**: Select `Raspberry Pi OS (other)` -> **`Raspberry Pi OS Lite (64-bit)`** (Bookworm).
4. **Choose Storage**: Select your microSD card.
5. Click **Next**. When prompted to apply OS customization settings, choose **Edit Settings**:
   - **Hostname**: Enter a unique name, e.g., `wmr-pi` (resolves as `wmr-pi.local`).
   - **Username and Password**: Set a local username (e.g. `pi` or your preferred tester account) and a secure password.
   - **Configure wireless LAN**:
     - **SSID**: Enter your **5 GHz** Wi-Fi SSID.
     - **Password**: Enter the Wi-Fi password.
     - **Wireless LAN country**: Set your country code (e.g., `US`).
   - **Services tab**:
     - Check **Enable SSH**.
     - Choose **Allow public-key authentication only** and provide your SSH public key (or use password authentication).
6. Save the settings, click **Yes** to apply customizations, and write the card.
7. Insert the microSD card into the Pi and power it on. Wait 1–2 minutes for first boot.

---

## 3. Step 2: Verify Network and 5 GHz Wi-Fi Link

SSH into the Pi:

```bash
ssh <username>@wmr-pi.local
```

### Verify 5 GHz Band

Confirm the wireless interface is linked on the 5 GHz band (frequency above 5000 MHz):

```bash
iw dev wlan0 link
```

Look for `freq: 5xxx` (e.g. `freq: 5180` or `5745`). If it indicates a 2.4 GHz
frequency (24xx MHz), reconnect the Pi to your dedicated 5 GHz network before
proceeding.

Check Wi-Fi power-save status:

```bash
iw dev wlan0 get power_save
```

*(Note: The bridge installer will automatically install a NetworkManager drop-in
to disable Wi-Fi power saving permanently).*

---

## 4. Step 3: Install WMR Bridge

You can install using either the one-line installer pinned to a specific
version (recommended) or the standalone Debian package.

### Option A: One-line installer (pinned version)

To install a specific released version (e.g. `0.2.1`), run:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | WMR_BRIDGE_VERSION=0.2.1 sudo -E bash
```

To install the latest release without pinning:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
```

The installer:
1. Downloads the release tarball and verifies its SHA-256 checksum against GitHub release assets.
2. Creates the dedicated `wmr` system service user and adds it to the `input` group.
3. Installs files to `/opt/wmr-bridge/pi` and udev rules to `/etc/udev/rules.d/99-wmr-bridge.rules`.
4. Disables Wi-Fi power saving via `/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf`.
5. Generates a secure authorization token at `/etc/wmr-bridge/token` (mode `0600 wmr:wmr`).
6. Configures the systemd service `/etc/systemd/system/wmr-bridge.service`.

### Option B: Debian package (`.deb`)

Download the `.deb` asset from the [GitHub Releases](https://github.com/nightcinder/WMR_Bridge/releases) page:

```bash
wget https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.0/wmr-bridge_0.2.1_all.deb
sudo apt install ./wmr-bridge_0.2.1_all.deb
```

### Start and Verify the Service

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wmr-bridge
```

Check the service status:

```bash
systemctl status wmr-bridge
journalctl -u wmr-bridge -n 50 --no-pager
```

---

## 5. Step 4: Pair WMR Controllers

Pair both controllers via Bluetooth Classic:

1. Launch `bluetoothctl`:
   ```bash
   bluetoothctl
   ```
2. Turn scanning on:
   ```text
   [bluetooth]# scan on
   ```
3. Put the **Left** controller into pairing mode: hold the Windows button under
   the battery cover or on the grip until the LEDs pulse slowly.
4. Locate the controller MAC address in the scan output (e.g., `Motion controller - Left`):
   ```text
   [bluetooth]# pair AA:BB:CC:DD:EE:01
   [bluetooth]# trust AA:BB:CC:DD:EE:01
   [bluetooth]# connect AA:BB:CC:DD:EE:01
   ```
5. Put the **Right** controller into pairing mode and repeat:
   ```text
   [bluetooth]# pair AA:BB:CC:DD:EE:02
   [bluetooth]# trust AA:BB:CC:DD:EE:02
   [bluetooth]# connect AA:BB:CC:DD:EE:02
   ```
6. Stop scanning and exit:
   ```text
   [bluetooth]# scan off
   [bluetooth]# exit
   ```

### Confirm HID Devices

Verify the controllers are registered as `/dev/hidraw*` devices:

```bash
cat /sys/class/hidraw/hidraw*/device/uevent | grep -E 'HID_NAME|HID_UNIQ'
```

You should see entries for both `Motion controller - Left` and
`Motion controller - Right`.

Restart or inspect the bridge service to confirm it opened both controllers:

```bash
sudo systemctl restart wmr-bridge
journalctl -u wmr-bridge -n 30 --no-pager
```

---

## 6. Step 5: Test Telemetry with `wifi_probe`

Run the included verification client directly on the Pi (or from another machine
on the same LAN) to verify report streaming, sequence continuity, and latency:

```bash
python3 /opt/wmr-bridge/pi/tools/wifi_probe.py \
    --host 127.0.0.1 \
    --token-file /etc/wmr-bridge/token \
    --stream batched \
    --duration 10
```

### Expected Output

```text
wifi_probe: connecting to ws://127.0.0.1:8765/wmr
hello: version 0.2.1, handFilter ['LEFT', 'RIGHT']
subscribed to 'batched' (10 s)
...
Summary:
  Left : reports: 1000, gaps: 0, missing (tick): 0
  Right: reports: 1000, gaps: 0, missing (tick): 0
  Echo RTT ms: min=..., p50=..., p95=..., max=...
```

Verify that:
- Both `Left` and `Right` controllers report incoming frames.
- Sequence gaps and tick-based missing reports remain 0 or near 0 in clean RF conditions.

---

## 7. Step 6: Upgrading WMR Bridge (Verifying Bond Persistence)

When a new version is released, upgrade using the one-line installer or `apt install`:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | WMR_BRIDGE_VERSION=<new-version> sudo -E bash
```

Or for a `.deb` package:

```bash
sudo apt install ./wmr-bridge_<new-version>_all.deb
```

### Confirm Persistence

After upgrade:
1. **Bluetooth Pairings**: Check that paired devices still exist without re-pairing:
   ```bash
   bluetoothctl devices Paired
   ```
2. **Authentication Token**: Confirm `/etc/wmr-bridge/token` was not altered:
   ```bash
   ls -la /etc/wmr-bridge/token
   ```
3. **Environment Overrides**: Confirm any custom settings in
   `/etc/wmr-bridge/wmr-bridge.env` remain intact.
4. **Service Health**:
   ```bash
   sudo systemctl restart wmr-bridge
   python3 /opt/wmr-bridge/pi/tools/wifi_probe.py --host 127.0.0.1 --token-file /etc/wmr-bridge/token --stream batched --duration 5
   ```

Both controllers should wake up and reconnect automatically without requiring
button presses or re-pairing.

---

## 8. Troubleshooting

### 1. High Report Loss / Jitter
- **Symptom**: `wifi_probe` shows non-zero sequence gaps or tick-based missing reports.
- **Cause**: Wi-Fi is operating on 2.4 GHz instead of 5 GHz, or Wi-Fi power saving is enabled.
- **Fix**:
  - Run `iw dev wlan0 link` and ensure `freq` is > 5000 MHz.
  - Run `iw dev wlan0 get power_save` and verify power save is `off`.
  - Ensure `/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf` contains `wifi.powersave = 2`.

### 2. Service Fails with Token Error
- **Symptom**: `journalctl -u wmr-bridge` shows `could not read token file ... Permission denied`.
- **Cause**: `/etc/wmr-bridge/token` permissions or ownership are incorrect.
- **Fix**:
  ```bash
  sudo chown wmr:wmr /etc/wmr-bridge/token
  sudo chmod 0600 /etc/wmr-bridge/token
  sudo systemctl restart wmr-bridge
  ```

### 3. Missing `/dev/hidraw*` Nodes
- **Symptom**: Controllers appear connected in `bluetoothctl` but `cat /sys/class/hidraw/hidraw*/device/uevent` shows no WMR controllers.
- **Cause**: BlueZ `input` plugin not active or kernel `hidp` module not loaded.
- **Fix**:
  ```bash
  sudo modprobe hidp
  echo hidp | sudo tee /etc/modules-load.d/wmr-bridge.conf
  sudo systemctl restart bluetooth
  ```

### 4. Controller Reconnect Fails After Reboot
- **Symptom**: Controllers do not reconnect when clicking buttons after rebooting the Pi.
- **Fix**:
  Make sure each controller was explicitly marked trusted during initial pairing:
  ```bash
  bluetoothctl trust <MAC-ADDRESS>
  ```
