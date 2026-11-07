# Deploying WMR Bridge to a Raspberry Pi (Tester Runbook)

New to the Pi? Read the simple-English beginner guide first:
[Raspberry Pi install guide](RPI_INSTALL_GUIDE.md).

This runbook guides testers through deploying and updating the WMR Bridge
software on a Raspberry Pi (Raspberry Pi 3 Model B+, Raspberry Pi 4 Model B, or
Raspberry Pi 5).

The Pi host bridge runs under stock Raspberry Pi OS Lite and bridges WMR
controllers over Classic Bluetooth to the phone/PC via WebSocket over 5 GHz
Wi-Fi.

> **Important**: Deployment uses stock Raspberry Pi OS Lite (64-bit) and the
> official Debian package or one-line installer. **Do not flash custom OS
> images.** Flashing an image wipes `/var/lib/bluetooth`, destroying controller
> pairings on every update. With package-based deployment, Bluetooth bonds,
> tokens, and local configuration persist across updates.

---

## 0. Bench checklist for 2026-11-07 (Raspberry Pi 3B+)

Short path for tomorrow's sitting. Tick each line; the section in brackets has
the related procedure and its commands.

- [ ] Flash Raspberry Pi OS Lite **64-bit** to the 3B+ card. In Imager choose
      device `Raspberry Pi 3` and the current `Raspberry Pi OS Lite (64-bit)`
      release (Trixie 2026-10-06). [§2]
- [ ] Use a 5 V / 2.5 A micro-USB supply; after boot
      `vcgencmd get_throttled` must print `throttled=0x0`. [§8.5]
- [ ] Join the dedicated **5 GHz** SSID; `iw dev wlan0 link` shows
      `freq: 5xxx`. [§3]
- [ ] `uname -m` prints `aarch64` and `systemctl is-active bluetooth` prints
      `active`. [§2]
- [ ] Install the released package and check `wmr-bridge --version` prints
      `wmr_bridge 0.2.4`. [§4]
- [ ] `systemctl is-enabled wmr-bridge` reports `enabled` and
      `systemctl is-active wmr-bridge` reports `active`. [§4]
- [ ] Pair, trust, and connect both controllers; both appear in
      `/sys/class/hidraw/*/device/uevent`. [§5]
- [ ] Replay smoke test from the installed tree passes with zero gaps
      ([PI_BRIDGE.md § Pre-flight kit](PI_BRIDGE.md#pre-flight-kit-no-hardware)).
- [ ] Live `wifi_probe` batched run shows both hands above 0 records/s and
      `dropped = 0`. [§6]
- [ ] Record the paste-back block from
      [the Pi bring-up sheet](runs/sheets/pi-bridge.md#5-what-to-paste-back),
      including board model, image date, kernel, BlueZ, and Python versions.

Known 3B+ limits for this sitting: less CPU headroom than a Pi 4/5 (Cortex-A53
at 1.4 GHz, 1 GB RAM) and Bluetooth 4.2 Classic instead of 5.0. Run only the
bridge and the probe on the board during timing, and record 3B+ results
separately from Pi 4/5 data.

---

## 1. Hardware Prerequisites

- **Raspberry Pi 3 Model B+**, **Raspberry Pi 4 Model B**, or
  **Raspberry Pi 5**.
  - The 3B+ has dual-band 2.4/5 GHz Wi-Fi and Bluetooth 4.2 Classic, so it
    meets the transport requirement; it has less CPU headroom and 1 GB RAM.
  - The Pi 4 and Pi 5 are the reference targets (Bluetooth 5.0 Classic).
  - **Raspberry Pi Zero 2 W is not supported** because it lacks 5 GHz Wi-Fi.
  - Stock Raspberry Pi OS already carries `bluez-firmware` and
    `firmware-brcm80211`, the Bluetooth and Wi-Fi firmware for the 3B+'s
    CYW43455 combo. No extra firmware package is needed.
- MicroSD card (8 GB or larger) and card reader.
- Power supply for your Raspberry Pi. Use the supply for the board: 5 V /
  2.5 A micro-USB for the 3B+, 5 V / 3 A USB-C for the Pi 4, and the official
  Pi 5 supply for a Pi 5.
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
2. **Choose Device**: Select your board. For a 3B+ choose `Raspberry Pi 3`
   (Imager groups the 3B and 3B+ under that entry). For a Pi 4 or Pi 5 choose
   that model.
3. **Choose OS**: Select `Raspberry Pi OS (other)` -> **`Raspberry Pi OS Lite (64-bit)`**.
   The current release is Trixie (2026-10-06); the previous Bookworm reference
   (2025-05-13) is also supported. Both are 64-bit and boot on the 3B+.
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

### Confirm the 64-bit OS and Bluetooth Stack

Once the Pi is up (SSH works in §3), confirm the image architecture and that
BlueZ is running before installing anything:

```bash
uname -m                      # must print aarch64
systemctl is-active bluetooth # must print active
```

If `uname -m` prints `armv7l`, the 32-bit image was flashed; re-flash the
64-bit Lite release. If Bluetooth is inactive, start it with
`sudo systemctl enable --now bluetooth`.

### Pinned images for the 2026-11-07 sitting

Raspberry Pi publishes the `.img.xz` and its SHA-256 side by side. The two
supported 64-bit Lite releases are:

| Release | Image | SHA-256 (`.img.xz`) |
| --- | --- | --- |
| Trixie, 2026-10-06 (current) | [2026-10-06-raspios-trixie-arm64-lite.img.xz](https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz) | `483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843` |
| Bookworm, 2025-05-13 (previous reference) | [2025-05-13-raspios-bookworm-arm64-lite.img.xz](https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2025-05-13/2025-05-13-raspios-bookworm-arm64-lite.img.xz) | `62d025b9bc7ca0e1facfec74ae56ac13978b6745c58177f081d39fbb8041ed45` |

Verify the download before flashing:

```bash
sha256sum 2026-10-06-raspios-trixie-arm64-lite.img.xz
```

If Imager offers a newer release than the table, record its date and SHA-256 in
the paste-back instead of pinning an older image.

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

> **Release Asset Availability**:
> Tags before #104 (such as `v0.3.0`) only contain ESP32 firmware assets.
> Tags from #104 through `v0.3.11` also publish `install.sh`,
> `wmr-bridge_<version>_all.deb` and `wmr-bridge-<version>.tar.gz`. Newer tags
> publish six files: the three merged factory images
> (`wmr-bridge-factory.bin`, `-left`, `-right`), the `.deb`, `install.sh` and
> `SHA256SUMS`. The current release is `v0.3.11`, which carries Pi package
> `0.2.4`.
>
> Use Method 1 only for a checkout you built yourself or for a pre-release
> change that has no tag yet.

### Method 1: Git Checkout Path (Current / Pre-release)

From a local clone on the Raspberry Pi:

```bash
git clone https://github.com/nightcinder/WMR_Bridge.git
cd WMR_Bridge
```

You can then build and install the Debian package (recommended) or run the installer directly:

**Build and install Debian package (`.deb`)**:
```bash
./pi/build_deb.sh
sudo apt install ./dist/*.deb
```

**Or run installer script directly**:
```bash
sudo ./pi/install.sh
```

---

### Method 2: Released Artifacts (`v0.3.11` and later)

Install from the release using the one-line installer or a downloaded package.

#### Pinned package for the 2026-11-07 sitting

| Artifact | Link | SHA-256 |
| --- | --- | --- |
| `wmr-bridge-0.2.4.tar.gz` | [download](https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.11/wmr-bridge-0.2.4.tar.gz) | `50d95736a80eae821ad23a8840e341a6d568703a7f40883b91cc153c7b7c2ab0` |
| `wmr-bridge_0.2.4_all.deb` | [download](https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.11/wmr-bridge_0.2.4_all.deb) | `8c54616806ba7cddb78f0e68a71f2f3503e23c4e4d16625129ea220c3a841eba` |
| `install.sh` | [download](https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.11/install.sh) | `7f738a4a084965de99a02819f459b64f893b21b2178eeea10555f7186ed94d28` |

The release also carries `SHA256SUMS` for every asset. Verify the files you
downloaded (the list covers all assets, so ignore the ones you did not fetch):

```bash
sha256sum -c SHA256SUMS --ignore-missing
```

#### Option A: One-line installer (pinned version)

The Pi package version and the firmware release tag are independent: package
`0.2.4` ships in tag `v0.3.11`. To pin both, set the package version and the tag:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.11/install.sh | WMR_BRIDGE_VERSION=0.2.4 WMR_BRIDGE_TAG=v0.3.11 sudo -E bash
```

To install the latest release without pinning:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
```

The installer:
1. Downloads the release `.deb` and verifies it against the release `SHA256SUMS`.
2. Installs it with `apt-get`. The package creates the dedicated `wmr` service
   user, installs files to `/opt/wmr-bridge/pi` and `/usr/bin/wmr-bridge`, adds
   the udev rule and the NetworkManager power-save drop-in, generates
   `/etc/wmr-bridge/token` and configures `wmr-bridge.service`.
3. Enables and starts `wmr-bridge.service`.
4. On a host without `apt-get`, falls back to the release tarball and runs its
   installer.

#### Option B: Standalone Debian package (`.deb`)

Download the `.deb` asset from the [GitHub Releases](https://github.com/nightcinder/WMR_Bridge/releases) page:

```bash
wget https://github.com/nightcinder/WMR_Bridge/releases/download/v0.3.11/wmr-bridge_0.2.4_all.deb
sudo apt install ./wmr-bridge_0.2.4_all.deb
```

---

### Start and Verify the Service

Both install methods also put `/usr/bin/wmr-bridge` on `PATH`; it runs
`python3 -m wmr_bridge` from `/opt/wmr-bridge`, so the daemon can be inspected
without changing directory:

```bash
wmr-bridge --version   # wmr_bridge 0.2.4
wmr-bridge --list
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wmr-bridge
```

Check the service status:

```bash
systemctl status wmr-bridge
systemctl is-enabled wmr-bridge   # enabled
systemctl is-active wmr-bridge    # active
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

Run the included verification client directly on the Pi to verify report
streaming, sequence continuity, and latency:

```bash
python3 /opt/wmr-bridge/pi/tools/wifi_probe.py \
    --host 127.0.0.1 \
    --token-file /etc/wmr-bridge/token \
    --stream batched \
    --duration 10
```

*(Optional: To run `wifi_probe.py` from another machine on the same 5 GHz Wi-Fi network instead of on the Pi, specify `--host wmr-pi.local` and provide the token with `--token <token-string>` copied from `/etc/wmr-bridge/token` on the Pi).*

### Expected Output

```text
connected to ws://127.0.0.1:8765/wmr as 0.2.4
hello: {
  "type": "hello",
  "protocol": 1,
  "transport": "wifi",
  "device": "wmr-pi",
  "version": "0.2.4",
  "handFilter": [
    "LEFT",
    "RIGHT"
  ],
  "hostname": "wmr-pi",
  "authRequired": true,
  "streams": [
    "raw",
    "decoded",
    "batched"
  ],
  "recordSize": 56,
  "batchMax": 9,
  "fragmentCapacity": 244
}
auth: {'type': 'auth_ok'}
subscribe: {'type': 'subscribed', 'stream': 'batched', 'recordSize': 56, 'batchMax': 9, 'fragmentCapacity': 244}
   2.0s    200.0 rec/s  left=400 right=400  gaps=(0,0)  missing=(0,0) loss=(0.0%,0.0%)  batches=45  rtt=1.2ms
   4.0s    200.0 rec/s  left=800 right=800  gaps=(0,0)  missing=(0,0) loss=(0.0%,0.0%)  batches=89  rtt=1.1ms
...
{
  "records": 2000,
  "messages": 224,
  "batches": 224,
  "gaps": [
    0,
    0
  ],
  "missingFromTicks": [
    0,
    0
  ],
  "lossPct": [
    0.0,
    0.0
  ],
  "rawFrames": 0,
  "rawIncomplete": 0,
  "lastSequence": [
    1000,
    1000
  ],
  "hands": [
    {
      "hand": "LEFT",
      "records": 1000,
      "recordsPerSecond": 100.0,
      "sequenceGaps": 0,
      "lastSequence": 1000,
      "missingFromTicks": 0,
      "lossPct": 0.0,
      "periodTicks": 50000,
      "periodMs": 5.0,
      "tickDiscontinuities": 0
    },
    {
      "hand": "RIGHT",
      "records": 1000,
      "recordsPerSecond": 100.0,
      "sequenceGaps": 0,
      "lastSequence": 1000,
      "missingFromTicks": 0,
      "lossPct": 0.0,
      "periodTicks": 50000,
      "periodMs": 5.0,
      "tickDiscontinuities": 0
    }
  ],
  "bleFields": {
    "rssi": "n/a",
    "phy": "n/a",
    "mtu": "n/a",
    "interval": "n/a",
    "esp_timer": "n/a"
  },
  "elapsedS": 10.0,
  "handFilter": [
    "LEFT",
    "RIGHT"
  ],
  "recordsPerSecond": 200.0,
  "rttMs": {
    "samples": 5,
    "min": 0.9,
    "p50": 1.2,
    "p95": 1.8,
    "max": 2.1
  },
  "dropped": 0,
  "serverStatus": {
    "type": "status",
    "version": "0.2.4",
    "handFilter": [
      "LEFT",
      "RIGHT"
    ],
    "uptimeS": 10.0,
    "clients": 1,
    "subscribed": 1,
    "dropped": 0,
    "batches": 224
  }
}
```

Verify that:
- Periodic lines show incoming reports per second (`rec/s`), active sequence counters, and round-trip ping time (`rtt`).
- In the final JSON summary, both `LEFT` and `RIGHT` hands report received records (`records > 0`).
- `sequenceGaps` and `missingFromTicks` remain 0 or near 0 in clean RF conditions (`lossPct: 0.0`).

---

## 7. Step 6: Upgrading WMR Bridge (Verifying Bond Persistence)

When a new version or commit is available, upgrade using the corresponding method:

### Upgrading via Git Checkout (Current / Pre-release)

```bash
cd WMR_Bridge
git pull

# Rebuild and install package:
./pi/build_deb.sh
sudo apt install ./dist/*.deb

# Or rerun installer directly:
# sudo ./pi/install.sh
```

### Upgrading via Release Artifacts (Post-#104 tags)

When upgrading against published release tags:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
```

To pin a release, name both the Pi package version and the firmware tag:

```bash
curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/download/<tag>/install.sh | WMR_BRIDGE_VERSION=<pi-version> WMR_BRIDGE_TAG=<tag> sudo -E bash
```

Or for a downloaded `.deb` package:

```bash
sudo apt install ./wmr-bridge_<pi-version>_all.deb
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

### 5. Pi 3B+ Under-Voltage or Thermal Throttling
- **Symptom**: `vcgencmd get_throttled` prints anything other than
  `throttled=0x0`, or the stream stalls while the board is busy.
- **Cause**: The 3B+ draws more current than a weak micro-USB supply can
  deliver, or the SoC is thermally throttled.
- **Fix**: Use a 5 V / 2.5 A supply and a heatsink or fan. Run only the bridge
  and `wifi_probe.py` on the board during P5/P6, then re-check
  `vcgencmd get_throttled` and `vcgencmd measure_temp`.
