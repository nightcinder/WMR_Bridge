# External tester quickstart — WMR Bridge v0.3.0

One tier, ~20 min. Android 14+ phone, both WMR controllers, board already on v0.3.0.
Private room: controller links are unauthenticated.

## 0. Prerequisites

- Android 14 or newer. Android 13 and below is not supported.
- Board flashed to v0.3.0 (flasher: https://nightcinder.github.io/WMR_Bridge/). Pre-flashed is fine; say so in the intake block.
- If unsure of the version, reflash; flashing erases stored pairings (expected).

## 1. Get the APK

https://github.com/nightcinder/wmr-companion/releases/download/v0.2.2/wmr-companion-0.2.2-unified-debug.apk

If an older WMR Companion is installed, uninstall it first (CI APKs are signed
differently): `adb uninstall com.onyxlens.wmr_companion` or app settings.

## 2. Install

1. Open the link on the phone; allow "install unknown apps" when asked.
2. Install. If Android blocks it, record the message and stop.

## 3. Connect to the bridge

1. Power the board. Status LED should be **red** (no controllers).
2. Open the app; grant Bluetooth / Nearby-devices permission.
3. On the **Connect** screen, scan for `WMR Bridge` and connect.
4. If Android shows a pairing request for `WMR Bridge`, allow it. Firmware 0.3.0
   bonds and encrypts the phone link.
5. Confirm the screen says **connected, streaming** with negotiated **MTU** 517
   (>= 61 is usable). Record the MTU shown.
6. Tap **Open 3D debug view**; stay on the debug screen.

## 4. Pair controllers

1. No other host connected to the controllers.
2. Pair both controllers at the same time, not one after the other: put BOTH in
   pairing mode by holding the Windows button on each until their LEDs flash.
3. On the debug screen, find **Controller control** and tap **Scan for controllers**.
4. Wait up to 60 s. Success: both hands `LEFT/RIGHT connected - WMR - HID ready`,
   live stream on both, LED **green**.
5. If it fails: tap the **Forget bond (LEFT)** / **Forget bond (RIGHT)** icon
   (tooltip), retry once, then stop.

## 5. Bundle export

1. Open **Latency test**.
2. Set **Run length** to **120 s**, **Stream** to **FF14 batched**,
   **Measure round trip (FF15 echo)** on, **Ping interval** to **250 ms**.
3. Tap **Start**; do not touch anything for 120 s; tap **Stop**.
4. Tap **Export CSV + JSON** (or **Export CSV + JSON + echo**) and save/share
   the files.

## 6. Guided bench (app build 0.2.2 or later)

Optional alternative. From the **Connect** screen tap **Run guided bench** and pick
a mode: **3DoF only** (bridge, full run), **6DoF only** (glasses, no bridge,
shorter run), or **Combined**. With no Raspberry Pi bridge, use **6DoF only** —
the bridge steps are skipped and the run is shorter; that is expected, not a
broken install. (For deploying and running with a Raspberry Pi bridge, see
the [RPi deployment runbook](../../DEPLOY_RPI.md)). The bench ends with **Write test bundle**.

**Capture 6DoF pose** appears only when your glasses' hand tracking is active.
It asks for five short movements per hand — hold still, let it drift, slow pan,
medium pan, fast flick — about two minutes per hand. Skipping it (whole or per
segment) is fine and does not break the run. Its files are already inside the
bundle you send.

## 7. Send

Zip the exported files + one LED photo + the intake block below. Send in the
thread where you received this runbook.

### Intake block

```markdown
### WMR external test — Tier 0

- Handle:
- Date/timezone:
- Firmware: 0.3.0
- App: 0.2.2 unified (debug-signed)
- Phone: make/model/Android
- Controllers: make/model, LEFT/RIGHT
- Result: PASS / PARTIAL / FAIL / BLOCKED
- Pairing: BOTH controllers paired together ____
- LED: red / yellow / blue / green
- MTU shown: ____
- Files: manifest, exports, LED photo
- What happened (3 lines max):
```

## Stop rules

- Board hot, smells, or LED goes dark: unplug and stop.
- Pairing fails twice: stop and send what you have.
- Do not flash 0.2.0 or a random image; do not open the board.
```