# External tester quickstart — WMR Bridge v0.2.4

One tier, ~20 min. Android 13+ phone, both WMR controllers, board already on v0.2.4.
Private room: the BLE link is unauthenticated.

## 0. Prerequisites

- Board flashed to v0.2.4 (flasher: https://nightcinder.github.io/WMR_Bridge/). Pre-flashed is fine; say so in the intake block.
- If unsure of the version, reflash; flashing erases stored pairings (expected).

## 1. Get the APK

https://github.com/nightcinder/wmr-companion/releases/download/v0.2.1/wmr-companion-0.2.1-unified-debug.apk

If an older WMR Companion is installed, uninstall it first (CI APKs are signed
differently): `adb uninstall com.onyxlens.wmr_companion` or app settings.

## 2. Install

1. Open the link on the phone; allow "install unknown apps" when asked.
2. Install. If Android blocks it, record the message and stop.
3. Older than Android 13: use the base APK
   https://github.com/nightcinder/wmr-companion/releases/download/v0.1.0/wmr-companion-0.1.0-debug.apk

## 3. Connect to the bridge

1. Power the board. Status LED should be **red** (no controllers).
2. Open the app; grant Bluetooth / Nearby-devices permission.
3. On the **Connect** screen, scan for `WMR Bridge` and connect.
4. Confirm the screen says **connected, streaming** with negotiated **MTU** 517
   (>= 61 is usable). Record the MTU shown.
5. Tap **Open 3D debug view**; stay on the debug screen.

## 4. Pair LEFT

1. No other host connected to the controllers.
2. Put LEFT in pairing mode: hold the Windows button until LEDs flash.
3. On the debug screen, find **Controller control** and tap **Scan for controllers**.
4. Wait up to 60 s. Success: `LEFT connected - WMR - HID ready`, live stream,
   LED **yellow**.
5. If it fails: tap the **Forget bond (LEFT)** icon (tooltip), retry once, then stop.

## 5. Pair RIGHT while LEFT streams

1. Leave LEFT connected and streaming. Do not disconnect it.
2. Put RIGHT in pairing mode.
3. Tap **Scan for controllers** again.
4. Wait up to 60 s. Success: both hands `HID ready`, live stream on both,
   LED **green**.
5. If RIGHT fails while LEFT streams: tap **Forget bond (RIGHT)**, retry once,
   then stop and send what you have.

## 6. Bundle export

1. Open **Latency test**.
2. Set **Run length** to **120 s**, **Stream** to **FF14 batched**,
   **Measure round trip (FF15 echo)** on, **Ping interval** to **250 ms**.
3. Tap **Start**; do not touch anything for 120 s; tap **Stop**.
4. Tap **Export CSV + JSON** (or **Export CSV + JSON + echo**) and save/share
   the files.

A **Capture 6DoF pose** step is coming in app build 0.2.4 or later (only when your glasses' hand tracking is active); until then it is not in the guided bench, so do not look for it.

## 7. Send

Zip the exported files + one LED photo + the intake block below. Send in the
thread where you received this runbook.

### Intake block

```markdown
### WMR external test — Tier 0

- Handle:
- Date/timezone:
- Firmware: 0.2.4
- App: 0.2.1 unified / 0.1.0 base
- Phone: make/model/Android
- Controllers: make/model, LEFT/RIGHT
- Result: PASS / PARTIAL / FAIL / BLOCKED
- Pairing: LEFT ____  RIGHT while LEFT streaming ____
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

Let me count lines.<|end|><|start|>start to=shell<|channel|>analysis <|constrain|>xml<|message|><rfl:parameter name="command">
cd /paperclip/instances/default/projects/aa90afe6-ebcf-46c2-8a7b-e6823086ca61/a71674c5-deb2-4688-8a51-3a4d48693526/WMR_Bridge/.paperclip/worktrees/ONY-488-next-plan-is-to-have-external-test-users-i-believe-this-means-we-need-to-sign-the-apk-differently-and-need-to-de && wc -l docs/runs/sheets/external-test-user.md