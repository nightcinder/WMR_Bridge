# External tester runbook: WMR Bridge v0.2.4

**Status: template, not run.** This is the first external-tester runbook. Nothing here is a
result. It tells you what to install, what to do in what order, and which files to send back.
The project owner fills in the result tables after your run.

**Time:** Tier 0 is about 20 minutes. Tier 1 is about 30–45 minutes. Tier 2 is about
60–90 minutes per device.

**What this run is for.** The first tester was blocked by controller pairing on firmware
`0.2.0`. The pairing fixes are in `0.2.1`–`0.2.4`; this run uses **`0.2.4` only**. If pairing
still fails on `0.2.4`, that is a new and important result, not a reason to go back to
`0.2.0`.

**The one thing to do first.** Complete Tier 0 on an Android phone with both WMR controllers.
Do not start with the glasses. Pairing is the gate for every other device.

**Quick start:**

1. Flash v0.2.4 (or confirm it is already installed).
2. Install the app on an Android 13+ phone.
3. Connect to `WMR Bridge`, pair LEFT, then pair RIGHT while LEFT is already streaming.
4. Screenshot **Controller control**, **Connect**, and the LED; fill in the manifest.
5. Send the Tier 0 bundle. If anything failed, add the Tier 1 serial log.

**Which sections do I need?** Tier 0: §5, §8.2, §9 and §12.1. Tier 1: §6 and §8. Tier 2:
§7 and §8. The rest is reference.

---

## 1. What you need

| Item | Needed for | Notes |
|---|---|---|
| WMR Bridge board (ESP32 Classic, at least 2 MB flash) | everything | An ESP32-S3 will **not** work: it has no Bluetooth Classic. Do not open the board. |
| USB data cable | flashing and serial | A charge-only cable will not appear as a port. |
| Desktop or laptop with Chrome or Edge | flashing | Web Serial is desktop-only; Firefox, Safari and mobile browsers will not work. |
| Android phone, 64-bit Android 13 or newer | Tier 0 / Tier 2 | Android 13+ is required by the unified app APK. For an older phone, use the fallback APK in §3.2. |
| Both WMR controllers, charged | Tier 0 | LEFT and RIGHT. Know which is which; the ring/trigger orientation is in the capture steps. |
| VITURE Pro Neckband + glasses | device 2 | Optional. The Neckband is an Android host; the glasses are the display. |
| XREAL glasses + Beam Pro (or S24/S25-class phone) | device 3 | Optional. Air 2 Ultra is the only XREAL model with hand tracking; other models are display/3DoF only. |
| PSVR2 Sense controllers | device 4 | Optional. Raw FF11 only; see §10.4. |
| Other glasses | device 5 | Optional. Display-only; no tracking claim. |
| A private room | everything | The BLE link has no pairing, encryption or authentication. Anyone in range can connect. |

If your board was already prepared for you, you can skip flashing, but you must still confirm
the firmware version. If you are not sure, flash `0.2.4` again; flashing erases the stored
controller pairings, so the first pairing after a flash is expected.

### 1.1 Terms used below

| Term | Meaning |
|---|---|
| **WMR controller** | A Windows Mixed Reality motion controller (LEFT or RIGHT), not a gamepad. |
| **Bridge** | The ESP32 board running WMR_Bridge; it receives the controllers over Bluetooth Classic and sends data to the app over BLE. |
| **FF10 / FF11 / FF12 / FF14 / FF16** | The bridge's BLE service and characteristics. FF11 is raw, FF12 is decoded, FF14 is batched decoded, FF16 reports the firmware version. You do not need to read them by hand; the app does it. |
| **MTU** | The negotiated BLE packet size. The app asks for 517; FF14 batching needs at least 61. |
| **HID** | The controller's input protocol. `HID ready` means the bridge received the controller's descriptor. |
| **HCI error code** | A Bluetooth controller-level error in the serial log, for example `0x05`, `0x06`, `0x09` or `0x13`. Send the line; do not decode it yourself. |

---

## 2. Tester intake (send this with your first message)

Copy this block, fill it in, and send it before or with your Tier 0 bundle. Use a handle if you
prefer; do not include your address, phone number, passwords or account names.

```markdown
### Tester intake

- Handle / preferred name:
- Contact for this run:
- Date and timezone:
- Country (for shipping questions only; do not include an address):
- Desktop OS + browser (Chrome/Edge version):
- Android phone: make, model, Android version, RAM if known:
- WMR controllers: make/model, LEFT and RIGHT, batteries charged?:
- Have the controllers ever paired with this bridge? Which firmware?:
- 0.2.0 symptoms: which hand failed, what the app/serial said, LED colours:
- Other Bluetooth hosts nearby or previously paired (PC, headset, console):
- Glasses inventory: VITURE model(s), Neckband model, XREAL model(s), Beam Pro model:
- PSVR2 Sense controllers available?:
- Other glasses (RayNeo, Rokid, etc.):
- Comfort with adb / serial monitor: yes / no / unsure
- Can you install an APK from a file?:
- Anything that limits movement, vision, hearing or time:
- Consent: I agree that redacted captures may be used in the project's public docs. I will not
  send credentials, addresses or unredacted personal data.
```

The last line is a consent statement, not a legal contract. You can stop at any time.

---

## 3. Versions and files

### 3.1 What this run uses

| Part | Version | Where it comes from |
|---|---|---|
| Firmware | **v0.2.4** (`41a9e5f`) | Browser installer or GitHub release. This is the current tagged release, CI-built and hardware-unverified. |
| App, Android 13+ | **v0.2.1 unified debug APK** (`0.2.1+3`) | GitHub release asset. This APK contains one shared Unity runtime for the VITURE and XREAL paths. It is large (about 584 MB). |
| App, Android older than 13 | **v0.1.0 base debug APK** | GitHub release asset. It has the BLE/3DoF/capture/latency screens, but not the unified vendor tracking runtime. |
| Capture tools (desktop, optional) | WMR_Bridge `main` | `tools/capture_ble.py`, `tools/analyze_capture.py`. |

### 3.2 Links

- Browser installer (all three firmware variants): <https://nightcinder.github.io/WMR_Bridge/>
- Firmware release v0.2.4: <https://github.com/nightcinder/WMR_Bridge/releases/tag/v0.2.4>
- Unified app APK (Android 13+): <https://github.com/nightcinder/wmr-companion/releases/download/v0.2.1/wmr-companion-0.2.1-unified-debug.apk>
- Fallback base app APK (older Android): <https://github.com/nightcinder/wmr-companion/releases/download/v0.1.0/wmr-companion-0.1.0-debug.apk>
- App release notes: <https://github.com/nightcinder/wmr-companion/releases/tag/v0.2.1>
- Pairing/discovery reference: <https://github.com/nightcinder/WMR_Bridge/blob/main/docs/DISCOVERY.md>
- Flashing reference: <https://github.com/nightcinder/WMR_Bridge/blob/main/docs/FLASHING.md>
- PSVR2 reference: <https://github.com/nightcinder/WMR_Bridge/blob/main/docs/PSVR2.md>
- Capture format: <https://github.com/nightcinder/wmr-companion/blob/main/docs/CAPTURE.md>
- Latency export: <https://github.com/nightcinder/wmr-companion/blob/main/docs/LATENCY.md>
- Glasses/tracking gates: <https://github.com/nightcinder/wmr-companion/blob/main/docs/tracking/GATES.md>
- VITURE pose sheet: <https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/pose-viture.md>
- XREAL pose sheet: <https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/pose-xreal.md>

If a link is unavailable, say so in the paste-back and stop there; do not substitute a random
APK or firmware image.

---

## 4. Device order (do not skip ahead)

| Order | Device | What it settles | Minimum bundle | Stop / continue |
|---|---|---|---|---|
| **1** | **Android phone + both WMR controllers on v0.2.4** | Whether the `0.2.0` pairing regression is fixed: pair LEFT, then RIGHT while LEFT is already streaming. Also the baseline BLE stream and app screens. | **Tier 0**; Tier 1 if anything fails; Tier 2 if Tier 0 passes and you have time. | If pairing fails, stop and send Tier 1. Do not move to the glasses first. |
| **2** | **VITURE Pro Neckband + glasses** | Whether the unified APK runs on the Neckband, displays in the glasses, and whether the VITURE tracking path reports `available`/`unsupported`. Hand tracking needs a Luma Ultra. | Tier 0 (display + app) + Tier 2-V (tracking). | If the app will not install or display, record that and move on. |
| **3** | **XREAL glasses + Beam Pro** | Same for the XREAL path. Hand tracking needs an Air 2 Ultra and the XREAL input source set to Hands. | Tier 0 + Tier 2-X. | If the model is not an Air 2 Ultra, record `unsupported`/display-only and move on. |
| **4** | **PSVR2 Sense** | Whether v0.2.4 discovers/pairs PSVR2 hands and forwards raw `0xA1 0x31` frames. No decoded PSVR2 stream exists. | Tier 1 + Tier 2-P. | No PSVR2 hardware is a valid "not run". |
| **5** | **Other glasses** | Display compatibility only: does the app appear and remain usable in the glasses? | Tier 0 display notes + screenshot. | Last. No controller or tracking claim. |

**Why this order.** Device 1 is the blocker the tester already hit, and every other device
assumes a controller stream. Device 2 is the rarest inventory and the first vendor path. Device
3 is the second vendor path. Device 4 is raw-only and needs a serial log to be useful. Device 5
is the lowest-information case.

---

## 5. Tier 0 — pairing triage (about 20 minutes, no serial required)

Do this with the Android phone and both WMR controllers. The goal is a yes/no answer and enough
screen evidence to triage a failure. You do **not** need adb or a serial monitor for Tier 0.

### 5.1 Flash or confirm v0.2.4

1. Use a desktop Chrome or Edge window.
2. Open <https://nightcinder.github.io/WMR_Bridge/>.
3. Connect the board with a USB **data** cable. If no port appears, install the USB-serial
   driver for your board (CP210x, CH340 or FTDI; the board vendor names it). On Linux you may
   need `dialout`/`uucp` group membership.
4. Pick **Both hands** and install. If it cannot connect, hold the **BOOT** button while it
   retries; close any serial monitor that is using the port.
5. Take a screenshot of the installer page showing the **v0.2.4** label before or after the
   install. That is the Tier 0 firmware evidence.

If the board is already on `0.2.4`, take the same screenshot and skip the flash. If you are not
sure, flash it; this erases the stored pairings, which is normal. If you did **not** flash the
board yourself, the installer screenshot shows what is available, not what is installed; say
"pre-flashed" in the manifest and confirm the version with serial `v` in Tier 1.

### 5.2 Install the app

**Phone on Android 13 or newer:**

1. Open the unified APK link in §3.2 on the phone.
2. Allow "install unknown apps" for the browser or file manager when asked.
3. Install. If an older WMR Companion is already installed, uninstall it first: every CI APK
   is signed differently.

**Phone older than Android 13:**

1. Use the v0.1.0 base APK instead.
2. If that also refuses to install, record the phone model and Android version and stop; do not
   sideload a random build.

**If you have adb and prefer it:**

```bash
adb devices
adb uninstall com.onyxlens.wmr_companion
adb install -r wmr-companion-0.2.1-unified-debug.apk
```

`adb uninstall` clears app settings and captures. Do it only before the first install or when
the APK signature changes.

### 5.3 Connect the phone to the bridge

1. Power the board. The status LED should be **red** (no controllers).
2. Open the app and grant the Bluetooth/Nearby-devices permissions.
3. On the **Connect** screen, scan for `WMR Bridge` and connect.
4. Confirm the screen says **connected, streaming** and shows a negotiated **MTU** of 517 (or
   at least 61). If it shows 23, FF14 batching will not work; record it and continue with FF12
   if the app offers it.
5. Open **Open 3D debug view**. Leave the app on the debug screen.

Already-paired controllers may connect on their own after the board boots. If both appear as
`connected - WMR - HID ready`, record "already paired" in the manifest. That proves the stream,
but it does **not** test the `0.2.4` pairing fix. To test pairing, flash `0.2.4` (which erases
the stored pairings) or disconnect each hand, use **Forget bond** on both, and re-pair. If you
only need the stream baseline, continue without re-pairing.

### 5.4 Pair LEFT first

1. Make sure no other host is connected to the controllers (turn off Bluetooth on a PC or
   headset that was previously paired, or remove the pairing there).
2. Put **LEFT** in pairing mode: hold the Windows button until the LEDs flash.
3. In the app, find **Controller control** on the debug screen and press **Scan for
   controllers**. If the board's automatic discovery already timed out after boot, this starts
   a fresh run.
4. Wait up to 60 seconds. Success looks like `LEFT connected - WMR - HID ready` and live
   stream numbers on the debug screen. The LED should turn **green**.
5. If the hand stays `not connected` or the app shows an error, press the **Forget bond
   (LEFT)** icon, put LEFT back in pairing mode, and scan again once. The exact Bluetooth error
   code, such as `0x05`, `0x06` or `0x13`, is in the serial log (Tier 1). If it still fails,
   stop this hand and go to Tier 1.

### 5.5 Pair RIGHT while LEFT is streaming (the regression case)

1. Leave LEFT connected and streaming. Do not disconnect it.
2. Put **RIGHT** in pairing mode.
3. Press **Scan for controllers** again.
4. Wait up to 60 seconds. Success looks like `RIGHT connected - WMR - HID ready`, live stream
   numbers for both hands, and a **blue** LED.
5. If RIGHT fails while LEFT is streaming, that is the known `0.2.x` second-hand failure
   pattern this run is meant to test. If your `0.2.0` failure looked different, say so in the
   intake and still run this sequence. Do not reflash `0.2.0`. Press **Forget bond (RIGHT)**,
   retry once, then stop and send Tier 1.

### 5.6 Capture Tier 0

Take these five items:

1. Screenshot of the installer page showing **v0.2.4**.
2. Screenshot of the **Controller control** panel with both hands' status.
3. Screenshot of the **Connect** screen with the MTU and stream state.
4. Screenshot of the **Debug** screen with both hands' rates and sequence gaps.
5. A photo of the board and controllers showing the LED colour and which controller is which.
   No faces, no room details, no address labels.

If pairing failed, add:

6. A screenshot of the exact error/status text.
7. A photo of the controller LED pattern.

### 5.7 Tier 0 result

Mark one:

- **PASS** — both hands show `HID ready`; both stream; LED blue; no repeated pairing errors.
  If you skipped re-pairing because the controllers were already bonded, mark **PASS (stream)**
  and note "pairing not re-tested".
- **PARTIAL** — one hand works, one does not.
- **FAIL** — neither hand connects, or the app cannot connect to the bridge.
- **BLOCKED** — cannot flash, install the APK, or get a USB port.

`PARTIAL`, `FAIL` and `BLOCKED` all mean: send Tier 0 now, then do Tier 1 if you can. Do not
spend more time repeating the same pairing attempt.

---

## 6. Tier 1 — diagnostic bundle (about 30–45 minutes)

Tier 1 is required when Tier 0 is not a clean PASS, and recommended for the first full sitting
even if it passes. It adds the serial log, which is the only file that shows *why* a pairing
attempt failed.

### 6.1 Capture the serial log

You need Python 3 and a USB cable. Close the browser flasher first; only one program can hold
the serial port.

Install the serial helper once:

```bash
python3 -m pip install --user pyserial
```

Windows PowerShell:

```powershell
py -m pip install --user pyserial
```

Find the port:

- Windows: Device Manager → **Ports (COM & LPT)** → `COMx`.
- macOS: `/dev/tty.usbserial-*` or `/dev/cu.usbserial-*`.
- Linux: `/dev/ttyUSB0` or `/dev/ttyACM0`.

Start the monitor and save everything:

```bash
# macOS / Linux
python3 -u -m serial.tools.miniterm --raw /dev/ttyUSB0 115200 | tee serial.log
```

```powershell
# Windows PowerShell
py -u -m serial.tools.miniterm --raw COM5 115200 | Tee-Object -FilePath serial.log
```

Replace `/dev/ttyUSB0` or `COM5` with your port. On Windows, use `py` or `python` if `py` is
not installed. Leave this window open for the whole run. Press `Ctrl+]` to exit miniterm when
you are done; if the pipe keeps it open, press `Ctrl+C`.

Then:

1. Reset the board (press **EN**, or unplug/replug USB) so the log includes the boot banner.
2. Type `v` and press Enter. The log should print the firmware version; confirm `0.2.4`.
3. Type `s` and press Enter to print the status block.
4. Repeat the LEFT-then-RIGHT pairing steps from §5.4–§5.5.
5. If the second hand fails, let the retries finish (about 30 seconds) before stopping.
6. Type `s` once more, then stop the capture.

Do not edit the log. If it contains your Wi-Fi name or a Bluetooth address, redact it later
(§9), not while capturing.

### 6.2 Capture logcat

With the phone connected by USB, USB debugging enabled, and the "Allow USB debugging" prompt
accepted:

```bash
adb devices
adb logcat -c
adb logcat -v threadtime > logcat.txt
```

Leave it running while you repeat the failure, then press `Ctrl+C`. If more than one device is
listed, use `adb -s <device-serial> logcat -v threadtime > logcat.txt`.

For a wireless host (VITURE Neckband or Beam Pro), connect first if you have enabled wireless
debugging:

```bash
adb connect <host-ip>:5555
adb devices
```

If you cannot enable adb on the host, skip logcat for that device and say so. Screenshots and
the serial log are still useful.

### 6.3 Add the setup photo and app state

- One photo of the board, phone/host, controllers and their positions. No faces or room
  details.
- The Tier 0 screenshots, if you have not already sent them.
- The app's **Latency** or **Debug** screen at the moment of failure, if it shows an error.

### 6.4 Tier 1 stop rules

- One clean reproduction is enough. Do not repeat the same failure more than twice.
- If the board becomes hot, smells, or the LED goes dark, unplug it and stop.
- If the serial port disappears, do not keep replugging; record the symptom and send what you
  have.

---

## 7. Tier 2 — stream and device evidence (optional, after a Tier 0 PASS)

Tier 2 is for a working baseline. It is not a debugging step. Do it only if Tier 0 passed and
you have the time. One device per sitting; do not run two hosts against the bridge at once.

The device-specific parts are **Tier 2-V** for VITURE (§7.4), **Tier 2-X** for XREAL (§7.5)
and **Tier 2-P** for PSVR2 (§7.3 and §7.7).

### 7.1 Phone + both controllers: 120 s latency export

1. Put the board and phone in fixed positions. Mark them with tape if you can. Controllers
   face down and still unless a step says otherwise.
2. Connect the app to the bridge.
3. Open **Latency test**.
4. Set **Run length** to **120 s**, **Stream** to **Batched FF14**, **Measure round trip
   (FF15 echo)** on, and **Ping interval** to **250 ms**.
5. Start and do not touch anything for 120 seconds.
6. Stop and use **Export**. Send the JSON and both CSVs (the frame CSV and the echo CSV).
7. Screenshot the result summary.

This is a capture, not an analysis. Do not interpret the numbers.

### 7.2 Phone + one WMR controller: FF11 capture

The app's **Capture** screen records the raw FF11 stream. It writes only complete 46-byte WMR
frames; PSVR2 frames are skipped (see §7.3).

1. Keep the phone connected. Put the other controller aside or let it sleep.
2. Open **Capture** from the Connect screen. The app switches to **FF11 raw** for the capture.
3. Choose **LEFT**. Start. Follow the four phases:
   - **still** — hold the controller upright, ring at the top, trigger away from you, buttons
     untouched, at least 5 seconds.
   - **twist_cw** — one slow 360° twist about the handle, about 4 seconds, ending in the same
     pose.
   - **flip_away** — one slow forward somersault, about 4 seconds.
   - **lean_right** — one slow cartwheel to your right, about 4 seconds.
4. Finish and **Share** the `.txt` file. Repeat for **RIGHT**.
5. Name them `...-capture-left.txt` and `...-capture-right.txt`.

If the app says the other controller is still streaming and the capture looks mixed, repeat
with only one controller awake. Do not run the analyzer yourself; send the raw files.

### 7.3 PSVR2: desktop FF11 capture

The app capture screen is WMR-only. For PSVR2, use the desktop tool with the phone
disconnected (the bridge serves one client).

```bash
git clone https://github.com/nightcinder/WMR_Bridge
cd WMR_Bridge
python3 -m pip install --user bleak
python3 tools/capture_ble.py --hand LEFT --out captures/psvr2-left.txt
```

Follow the prompts. Repeat with `--hand RIGHT`. Keep the raw `.txt`; do **not** run
`analyze_capture.py` on a PSVR2 capture (the analyzer expects 46-byte WMR frames).

### 7.4 VITURE: display and tracking observation

Do this only if you have a VITURE Pro Neckband (Android 13+) and glasses. Use the unified APK.

1. Disconnect the phone from the bridge. The bridge serves one client.
2. Install the unified APK on the Neckband. Launch it.
3. Confirm the app is visible and readable in the glasses. Take a screenshot.
4. Connect the app to `WMR Bridge` over Bluetooth. Confirm the stream and both controllers.
5. Open **Open 3D debug view** → **Tracking source** → **VITURE**.
6. Record the status shown: `available`, `unavailable`, `unsupported`, or an error.
7. If it starts, record:
   - glasses model (hand tracking needs a **Luma Ultra**);
   - whether head pose moves the view;
   - the hand-joint count/rate, if shown;
   - whether a hand is still tracked while gripping a WMR controller;
   - whether recenter works, if the app shows a recenter control.
8. Stop tracking and close the app. Take a screenshot of the status before closing.

A `unavailable` or `unsupported` result is valid. Record it; do not try to install vendor SDKs
or Unity yourself.

### 7.5 XREAL: display and tracking observation

Do this only if you have XREAL glasses and a Beam Pro (or an S24/S25-class phone). The unified
APK needs a 64-bit Android 13+ host.

1. Disconnect the phone from the bridge.
2. Connect the XREAL glasses to the Beam Pro/phone and confirm the display works.
3. Install and launch the unified APK. Confirm the app is visible in the glasses.
4. Connect to the bridge over Bluetooth; confirm the stream and controllers.
5. Open **Open 3D debug view** → **Tracking source** → **XREAL**.
6. Record the status (`available`, `unavailable`, `unsupported`, error).
7. If it starts, record:
   - glasses model (hand tracking needs an **Air 2 Ultra**);
   - the XREAL input source (set **Hands** in the XREAL home if that option exists);
   - the device/model name the app reports;
   - hand-joint count/rate, if shown;
   - whether a hand is still tracked while gripping a WMR controller.
8. Take screenshots of the status and the display.

A non-Ultra XREAL model is expected to be `unsupported` for hands. Record it as display/3DoF
only; that is not a failure.

### 7.6 Other glasses: display-only

For any glasses not covered above:

1. Connect the glasses to their normal Android host.
2. Install and launch the app (base APK is fine if the host is older than Android 13).
3. Record the make/model and whether the app is visible and usable.
4. Screenshot the display.
5. Do not claim tracking or controller support from this step.

### 7.7 PSVR2 observation

1. Use the v0.2.4 board. Disconnect the phone.
2. Put one Sense controller in pairing mode using its normal procedure. Record exactly what
   you did; the bridge does not change the controller's pairing procedure.
3. Start the serial monitor (§6.1) before pairing. Let discovery run.
4. Record whether the log shows the controller as `model PSVR2`, whether a wake line appears,
   and the first report's frame length. The raw report starts `A1 31` and is expected to be up
   to 79 bytes; record the actual `frame_len` rather than assuming 79.
5. Connect the app and select **FF11 raw**. There is no PSVR2 decoder for FF12/FF14, and FF13
   is not read for PSVR2. If the app shows no 3D view, that is expected.
6. Capture the serial log and, if possible, a raw FF11 capture (§7.3).
7. Repeat for the second hand if you have both.

---

## 8. What to send back: the capture bundle

Use one folder per run:

```text
wmr-ext-<handle>-<YYYYMMDD>/
  manifest.md
  tier0/
    installer-v0.2.4.png
    controller-control.png
    connect-screen.png
    debug-screen.png
    leds.jpg
  tier1/
    serial.log
    logcat.txt
    setup.jpg
    error.png
  tier2/
    latency-<timestamp>.json
    latency-<timestamp>.csv
    latency-<timestamp>-echo.csv
    capture-left.txt
    capture-right.txt
    viture-status.png
    xreal-status.png
    psvr2-left.txt
  README.txt
```

`README.txt` can be one line: what is in the folder and what is missing. If a file is missing,
leave it out and say why in the paste-back; do not create an empty placeholder.

### 8.1 Capture bundle index

| Artifact | Tier | How | Why it matters |
|---|---|---|---|
| Manifest | 0 | §8.2 | Ties every file to a version, device and result. |
| Installer screenshot | 0 | §5.1 | Shows which firmware was installed. |
| Controller-control screenshot | 0 | §5.6 | Shows both hands' pairing/model/HID state. |
| Connect/debug screenshots | 0 | §5.6 | Shows MTU, stream, rates and sequence gaps. |
| LED photo | 0 | §5.6 | Independent confirmation of zero/one/two connected hands. |
| Serial log | 1 | §6.1 | The only record of discovery, pairing retries and HCI errors. |
| Logcat | 1 | §6.2 | Phone-side BLE and app errors. |
| Setup photo | 1 | §6.3 | Position, distance and hardware identification. |
| Latency export | 2 | §7.1 | Stream health and round-trip timing on a working baseline. |
| FF11 captures | 2 | §7.2 / §7.3 | Raw frames for both WMR hands, or PSVR2 hands. |
| Glasses screenshots/logs | 2 | §7.4–§7.6 | Whether the app displays and which tracking status is reported. |

### 8.2 Manifest template

```markdown
# WMR external test manifest

- Run ID: wmr-ext-<handle>-<YYYYMMDD>
- Tier(s): 0 / 1 / 2
- Date and timezone:
- Tester handle:
- Board: ESP32 model, flash size, USB port
- Firmware: 0.2.4 (source: browser installer / GitHub release / pre-flashed)
- Firmware evidence: installer screenshot / serial `v` / both
- App: wmr-companion 0.2.1 unified / 0.1.0 base
- Phone/host: make, model, Android version
- Controllers: make/model, LEFT/RIGHT, batteries
- Glasses: make/model, host, connection (USB-C / wireless)
- Position: distance from board, height, orientation, tape marks?
- Other radios: Wi-Fi 2.4/5 GHz, other Bluetooth hosts, number of phones
- Result: PASS / PARTIAL / FAIL / BLOCKED / NOT RUN
- What happened (5 lines max):
- Files and SHA-256:
  - name — what it is — SHA-256
- Missing files and why:
```

SHA-256 is optional but helpful. Commands:

```bash
# macOS
shasum -a 256 *
# Linux
sha256sum *
```

```powershell
# Windows
Get-ChildItem -File | Get-FileHash -Algorithm SHA256 | Format-Table
```

---

## 9. Redaction and privacy

Do this before you upload anything.

- Replace Bluetooth addresses with `<BRIDGE>`, `<CTRL-L>` and `<CTRL-R>`. Keep the last two
  octets only if the project contact asks for them.
- Remove Wi-Fi names, passwords, tokens, account names, device serial numbers and notification
  text.
- Crop or blur faces, room details, shipping labels and anything on a screen that is not the
  app.
- Keep model names, Android versions, firmware versions and error codes. Those are the data.
- Do not upload the APK, firmware image, or a full phone backup. Logs and captures only.
- If a file cannot be redacted safely, leave it out and say what it was.

A quick text check:

```bash
grep -RniE '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|password|passwd|token|ssid' .
```

This is a helper, not a guarantee. Read the files before sending.

---

## 10. Device notes

### 10.1 Android phone + both WMR controllers (device 1)

This is the gate. The important sequence is **LEFT first, then RIGHT while LEFT is already
streaming**. That is the `0.2.0` regression pattern. A clean run shows both hands as
`connected - WMR - HID ready`, a blue LED, live rates on both hands, and no repeated
authentication errors.

If the second hand fails, capture the serial log with the `pair` lines and the HCI error. The
useful codes are `0x05` (link key mismatch), `0x06` (PIN or key missing), `0x09` (connection
limit exceeded) and `0x13` (remote user terminated). Do not interpret them; send them.

### 10.2 VITURE Pro Neckband + glasses (device 2)

- The Neckband is an Android host; the glasses are the display. The app still talks to the
  bridge over BLE.
- The unified APK is the only release that carries the VITURE Unity runtime. It needs Android
  13+.
- Hand tracking requires a **Luma Ultra**; other VITURE models are 3DoF/display-only and are
  expected to report `unsupported` for hands.
- Record the glasses model exactly as shown. Do not install the VITURE SDK or Unity yourself.
- If the app is visible but tracking is `unavailable`, that is a valid result. It means the
  runtime is not linked or the device path did not start.

### 10.3 XREAL / Beam Pro (device 3)

- Use a Beam Pro or an S24/S25-class phone. XREAL warns against Android 16 on development
  devices; record the Android version.
- Hand tracking requires an **Air 2 Ultra**. Other XREAL models are display/3DoF only.
- Set the XREAL input source to **Hands** if the option exists; if it does not, record that.
- Record the device/model name the app reports, if any. A non-Ultra model reporting
  `unsupported` is expected, not a failure.

### 10.4 PSVR2 Sense (device 4)

- v0.2.4 searches PSVR2 names by default. No build switch is needed.
- PSVR2 is **raw-only**: FF11 carries `0xA1 0x31` frames up to 79 bytes. FF12/FF14 have no
  PSVR2 decoder, and FF13 is not read for a PSVR2 hand.
- The app's Capture screen writes only 46-byte WMR frames, so use the desktop
  `capture_ble.py` for PSVR2.
- Record the exact pairing procedure you used, the `model PSVR2` line, the wake line, and the
  first report's `frame_len`. If no PSVR2 hardware is available, mark "not run — no hardware".

### 10.5 Other glasses (device 5)

- Display-only. Record make/model, host, connection and whether the app is readable.
- Do not claim tracking, controller support or 6DoF from this step.
- One screenshot is enough.

---

## 11. Troubleshooting and stop rules

| Symptom | Try | Then |
|---|---|---|
| Installer cannot see the board | Use a data cable; install CP210x/CH340/FTDI driver; hold **BOOT** while retrying; close any serial monitor. | Record "blocked — no port" and stop. |
| Android blocks the APK install | Allow "install unknown apps" for the browser/file manager; if Play Protect warns, install only the exact release file linked in §3.2 and record the warning. | Record the Android message and stop if it still refuses. |
| App cannot find `WMR Bridge` | Grant Nearby-devices/Bluetooth permission; confirm the board LED is red; disconnect any other phone/host; toggle Bluetooth. | Send Tier 0 + logcat. |
| App connects but no stream | Confirm MTU ≥61 for FF14; switch to FF12 or FF11; disconnect/reconnect and let the app resubscribe. | Record MTU and stream mode. |
| LEFT pairs, RIGHT does not | Keep LEFT streaming; press **Forget bond (RIGHT)**; put RIGHT in pairing mode; scan once. | Send Tier 1 serial log. Do not reflash `0.2.0`. |
| `authentication failed` / `0x05` | Remove the old pairing on the other host; **Forget bond** on the bridge; put the controller in pairing mode. | Send serial log. |
| `0x09` / connection limit | Let the retries finish; do not pair both at once repeatedly. | Send serial log; this is the regression evidence. |
| LED stays red | Check controller batteries; confirm pairing mode; check the serial log for discovery. | Send Tier 1. |
| Board hot / smell / dark LED | Unplug immediately. | Stop; do not continue. |
| Glasses show `unavailable`/`unsupported` | Record the exact status and model. | This is a valid result; move on. |
| PSVR2 shows no 3D view | Expected: raw-only, no decoder. Use FF11 and the serial log. | Capture raw frames. |

**Do not:**

- flash `0.2.0` or a random image;
- open the board or touch exposed pins;
- connect two phones/hosts to the bridge at once;
- post unredacted logs or photos;
- repeat a failing pairing more than twice without sending Tier 1.

---

## 12. Paste-back layout

Send this in the thread where you received this runbook, with the folder attached or linked.
Keep the short block in the message; put the full manifest in `manifest.md`.

### 12.1 Short paste-back (Tier 0)

```markdown
### WMR external test — Tier 0

- Handle:
- Date/timezone:
- Firmware: 0.2.4 (installer screenshot attached)
- App: 0.2.1 unified / 0.1.0 base
- Phone: make/model/Android
- Controllers: make/model, LEFT/RIGHT
- Result: PASS / PARTIAL / FAIL / BLOCKED
- Pairing: LEFT ____, RIGHT while LEFT streaming ____
- LED: red / green / blue
- MTU shown: ____
- Files: manifest, installer, controller-control, connect, debug, LEDs
- What happened (3 lines max):
```

### 12.2 Full paste-back (Tier 1 / Tier 2)

```markdown
### WMR external test — Tier 1/2

- Run ID:
- Device: phone / VITURE / XREAL / PSVR2 / other glasses
- Result: PASS / PARTIAL / FAIL / BLOCKED / NOT RUN
- Firmware: 0.2.4; serial `v` output:
- App: version and source:
- Host: make/model/Android:
- Controllers/glasses: make/model:
- Position and distance:
- Reproduction steps (numbered):
- What the serial log shows (paste the `pair` / `Discovery` / `HCI` lines):
- What the app/logcat shows (paste the error line, not a screenshot of text):
- Files:
  - serial.log — SHA-256:
  - logcat.txt — SHA-256:
  - latency JSON/CSV — SHA-256:
  - capture-left/right — SHA-256:
  - screenshots — names:
- Missing files and why:
- Anything unusual (heat, disconnect, crash, repeat):
```

If a log is short, paste it inline. If it is long, attach the file and paste only the lines
around the failure.

---

## 13. What this run does not cover

- Firmware or app code changes. This is a capture run; no code is changed.
- Hardware shipping, returns or payment.
- Result analysis or a 6DoF claim. The glasses paths are experimental; record what happens,
  do not conclude.
- Wi-Fi/Raspberry Pi, two-bridge, gamepad HID, iOS and long-soak testing.
- A security audit. The BLE link is unauthenticated by design for this test; use a private
  room.

When the bundle is in, the project owner will update the result tables and say what the next
sitting is. Thank you for testing.
