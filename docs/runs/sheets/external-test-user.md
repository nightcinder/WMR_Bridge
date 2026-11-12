# External tester quickstart for WMR Bridge v0.3.11

Android 14+ phone, both WMR controllers, board already on v0.3.11. Private room: controller links are unauthenticated. Tier 0 takes about 20 min. Tier 1 adds diagnostics and is required if Tier 0 is not a clean PASS. Tier 2 is optional evidence on a working baseline.

## 0. Prerequisites
- Android 14 or newer. Android 13 and below is not supported.
- Board on v0.3.11. Flasher: <https://nightcinder.github.io/WMR_Bridge/>. Pre-flashed is fine; say so in the intake block. If unsure, reflash. Flashing erases stored pairings (expected).

## 1. Get and install the APK

<https://github.com/nightcinder/wmr-companion/releases/download/v0.3.17/wmr-companion-0.3.17-unified-arm64-v8a.apk>

The APK ships `libglasses.so` (VITURE XR Glasses SDK 2.4.1) under the VITURE licence; EULA and notices are in About. Checksums: <https://github.com/nightcinder/wmr-companion/releases/download/v0.3.17/SHA256SUMS>

Do not use an older build. v0.3.17 adds report-only Semgrep, gitleaks and Dependabot scans and the six-run pose-filter corpus. A crash on v0.3.17 is a regression worth recording; v0.3.7-v0.3.9 crashes are known. Uninstall a pre-0.3.3 Companion first (`adb uninstall com.onyxlens.wmr_companion`).

Open the link on the phone, allow installs from unknown apps, and install.

## 2. Connect to the bridge
1. Power the board. Status LED should be red (no controllers).
2. Open the app; grant Bluetooth / Nearby-devices permission.
3. Scan for `WMR Bridge` and connect. Allow the pairing request.
4. Confirm connected, streaming, MTU 517 (at least 61 usable). Record the MTU.
5. Open the 3D debug view.

Do not enable the app's own HID output (`BluetoothHidDevice`) while bridge HOGP is active: it disables Android's HID Host service and adds radio contention.

## 3. Pair both controllers
1. Make sure no other host is connected to the controllers.
2. Put **both** controllers in pairing mode at the same time (turn them on with the front Windows button, then hold the small pairing button inside the battery compartment until the LEDs flash).
3. On the debug screen, tap **Scan for controllers**.
4. Wait up to 60 s. Pass: both hands `LEFT/RIGHT connected - WMR - HID ready`, live stream on both, LED green.
5. If it fails: tap Forget bond for the failed hand, retry once, then stop.

## 4. Tier 0 bundle
1. Open Latency test.
2. Set 120 s, FF14 batched, FF15 echo on, 250 ms.
3. Start, touch nothing for 120 s, Stop.
4. Export CSV + JSON (and echo CSV if offered).
5. Take one LED photo.

Optional guided bench: from Connect tap **Run guided bench**. With no Pi bridge, pick 3DoF only or 6DoF only. The bridge steps are skipped in 6DoF mode; that is expected. Capture 6DoF pose appears only when glasses hand tracking is active: six short recordings, alternating hands, about 1.5-2 min total. Skipping it is fine. The run ends with **Write test bundle**.

## 5. Tier 1 — diagnostics (about 30-45 min)

Do this when Tier 0 is not a clean PASS, and for the first full sitting.

### 5.1 Serial log

Close the browser flasher; only one program can hold the port.

```bash
python3 -m pip install --user pyserial
python3 -u -m serial.tools.miniterm --raw /dev/ttyUSB0 115200 | tee serial.log
```

Windows: `py -m pip install --user pyserial`, then `py -u -m serial.tools.miniterm --raw COM5 115200 | Tee-Object -FilePath serial.log`. Use Device Manager to find `COMx`; macOS `/dev/tty.usbserial-*`, Linux `/dev/ttyUSB0` or `/dev/ttyACM0`.

Reset the board so the log has the banner. Type `v` (version, expect 0.3.11), `s` (status), repeat the pairing steps, let retries finish (about 30 s), type `s` again, then stop. Do not edit the log; redact later.

### 5.2 Logcat

With USB debugging enabled:

```bash
adb devices
adb logcat -c
adb logcat -v threadtime > logcat.txt
```

Repeat the failure, then Ctrl+C. For wireless hosts: `adb connect <ip>:5555` first. If adb is unavailable, skip logcat and say so.

### 5.3 Photo and app state

One photo of the board, phone/host, controllers and positions (no faces or room details), the Tier 0 screenshots, and the app screen at failure.

### 5.4 Stop rules

One clean reproduction is enough; do not repeat the same failure more than twice. If the board gets hot, smells or the LED goes dark, unplug and stop. If the serial port disappears, record it and send what you have.

## 6. Tier 2 — optional device evidence

Only after a Tier 0 PASS, one device per sitting. Do not run two hosts at once.

### 6.1 Phone + both controllers: 120 s latency

Fix the board and phone positions. FF14 batched, FF15 echo on, 250 ms, 120 s. Export JSON and both CSVs; screenshot the summary. This is a capture, not an analysis.

### 6.2 Phone + one WMR controller: FF11 capture

Open **Capture**, choose LEFT, start. Hold still 5 s, twist 360° in 4 s, flip forward 4 s, lean right 4 s. Finish and share the `.txt`; repeat for RIGHT. Name them `...-capture-left.txt` and `...-capture-right.txt`. Send raw files; do not analyse them yourself.

### 6.3 PSVR2: desktop FF11 capture

The app capture is WMR-only. With the phone disconnected:

```bash
git clone https://github.com/nightcinder/WMR_Bridge
cd WMR_Bridge
python3 -m pip install --user bleak
python3 tools/capture_ble.py --hand LEFT --out captures/psvr2-left.txt
```

Repeat for RIGHT. Do not run `analyze_capture.py` on PSVR2 frames.

### 6.4 VITURE / XREAL / other glasses

Disconnect the phone. Install and launch the unified APK on the Android 14+ host. Confirm the app is visible in the glasses. Connect to `WMR Bridge`, confirm stream and controllers, then open **Open 3D debug view -> Tracking source -> VITURE** (or **XREAL**).

Record the status (`available`, `unavailable`, `unsupported`, error), glasses model (hand tracking needs Luma Ultra or Air 2 Ultra), whether head pose moves the view, hand-joint count/rate, whether a hand is tracked while gripping a WMR controller, and whether recenter works. Screenshot the status and display. `unavailable` or `unsupported` is valid; do not install vendor SDKs yourself. Other glasses: record make/model and whether the app is visible; claim no tracking.

### 6.5 PSVR2 observation

Use the v0.3.11 board with the phone disconnected. Put one Sense controller in pairing mode; record exactly what you did. Start the serial monitor before pairing and let discovery run. Record whether the log says `model PSVR2`, whether a wake line appears, and the first report's frame length (starts `A1 31`, up to 79 bytes; record the actual length). Select FF11 raw. v0.3.11 decodes PSVR2 FF12/FF14; if the app shows no 3D view, record that. FF13 is not read for PSVR2. Capture the log and a raw FF11 capture if possible.

## 7. Send

Use one folder:

```text
wmr-ext-<handle>-<YYYYMMDD>/
  manifest.md
  tier0/  installer.png, controller-control.png, connect.png, debug.png, leds.jpg
  tier1/  serial.log, logcat.txt, setup.jpg, error.png
  tier2/  latency-*.json/csv, capture-left/right.txt, glasses screenshots, psvr2-left.txt
  README.txt
```

Omit missing files and say why. Zip the bundle plus the intake block and send it in the thread where you got this runbook.

### Intake block

```markdown
### WMR external test
- Handle:
- Date/timezone:
- Firmware: 0.3.11
- App: 0.3.17 unified (signed)
- Phone/host: make, model, Android
- Controllers: make/model, LEFT/RIGHT
- Tier: 0 / 1 / 2
- Result: PASS / PARTIAL / FAIL / BLOCKED
- Pairing: both controllers paired together ____
- LED: red / yellow / blue / green
- MTU shown: ____
- Files:
- What happened (3 lines max):
```

## 8. Stop rules
- Board hot, smells, or LED dark: unplug and stop.
- Pairing fails twice: stop and send what you have.
- Do not flash 0.2.0 or a random image. Do not open the board.
