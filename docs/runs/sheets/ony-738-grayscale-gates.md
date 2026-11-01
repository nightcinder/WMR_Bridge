# Run sheet: grayscale-camera gates (ONY-733 Gate 4, ONY-735 Gates 1 and 9)

> **Proposed sheet, not yet run.** Nothing here is a hardware result. Every
> figure is recorded by the tester. The procedures and thresholds come from
> ONY-733, ONY-735, and the counsel amendments in ONY-731. Do not change the
> thresholds.

Companion documents:

- ONY-733 Gate 4: hand tracking while gripping a WMR controller (Pro Neckband).
- ONY-735 Gates 1 and 9: LED visibility, checkerboard calibration, head-pose
  speed on the Luma Ultra.
- ONY-734: the debug capture tool (`VitureCarinaDiagnostics`).
- ONY-736: the scorecard that uses what this sheet produces.
- Companion `docs/tracking/GATES.md` and `docs/runs/sheets/pose-viture.md`.

Gates covered: **Gate 4** (hand tracking gripping a controller), **Gate 1**
(8-bit stereo frame LED visibility), **Gate 9** (C-SDK head pose on a phone).

## 0. What this sheet settles

- Whether a hand that grips a WMR controller stays tracked on the Pro Neckband.
- Whether the WMR controller LEDs show in the Luma Ultra 8-bit grayscale
  stereo frames, and at which distance and exposure.
- Whether the checkerboard stereo pairs let us recover camera intrinsics.
- Whether the C-SDK head pose on a plain Android phone runs at a usable rate.

## 1. Prerequisites and builds

| Item | Required for | Notes |
|---|---|---|
| Glasses | Gate 1, Gate 9 | VITURE Luma Ultra. 3DoF models (`One`, `OneLite`, `Pro`, `Pro2`, `Luma`, `LumaPro`, `LumaCyber`, `Beast`) are rejected by the hand-tracking adapter and must not be used for Gate 4. |
| Host | Gate 4 | VITURE Pro Neckband (Android, wireless ADB). The app runs on the Neckband, not on a paired phone. |
| Phone | Gate 9 | Android phone with USB host (OTG) and a USB-C cable. |
| Bridge | All gates | One board on `main`, both controllers paired and awake. Record firmware SHA and boot banner. |
| Controllers | All gates | Both WMR controllers, powered, LEDs active. |
| Checkerboard | Gate 1 (calibration) | Printed checkerboard with known square size, flat and rigid, at least 8 by 6 inner corners. |
| Tooling | All gates | `adb` (wireless for the Neckband), a terminal, logcat capture, a way to screenshot the debug overlay. |

App builds:

| Build | Flags | Used for |
|---|---|---|
| Companion debug APK | `--dart-define=WMR_VITURE_TRACKING=true` | Gate 4. The Unity `unityLibrary` AAR must be linked (export pinned by CI). |
| Companion debug APK | `--dart-define=WMR_VITURE_CARINA_DIAG=true` | Gate 1 and Gate 9. The proprietary `libglasses.so` must be staged in `jniLibs/arm64-v8a/` (see the ONY-734 README). Without it the capture returns `sdkUnavailable`. |
| Companion debug APK | both flags | If one APK is to cover all gates. |

Build the APKs:

```bash
cd apps/wmr_companion_app
flutter build apk --debug --dart-define=WMR_VITURE_TRACKING=true
flutter build apk --debug --dart-define=WMR_VITURE_CARINA_DIAG=true
adb install -r build/app/outputs/flutter-apk/app-debug.apk
```

Record the app commit, the Unity export SHA, and the firmware SHA in the intake
block.

## 2. Setup, once per sitting

1. Power the Neckband, enable wireless ADB, confirm `adb connect <ip>:5555`
   succeeds.
2. Power the bridge. Put both controllers on taped marks about 0.5 m from the
   bridge. Photo the setup.
3. Start the serial monitor for the bridge and a logcat capture for the
   Neckband.
4. Connect the app to the bridge. Select Batched FF14. Confirm `STREAM` shows
   `stream=batched`, `sub=1`, live `rate_rx`. Confirm `bridge_hands=0`.
5. Confirm the glasses model reported by the app is `LumaUltra` before Gate 4.
   If it is a 3DoF model, Gate 4 fails with `unsupported` and must be recorded
   as such.
6. From the debug overlay, record the VITURE `start` reply fields:
   `playerHosted`, `stereoMode`, and `stage`. A healthy run reaches
   `playerHosted` then `loaderStarted` then `firstFrame`, with `stereoMode` one
   of `multiPass`, `singlePass`, or `singlePassInstanced`. If `stereoMode` is
   `unknown` or the stage stops early, record it and stop.

## 3. Gate 4 — hand tracking while gripping a controller

Question: does `VitureXR.HandTracking.Start()` deliver 26 joints, and is a hand
tracked while gripping a WMR controller?

Procedure:

1. Confirm the glasses model is `LumaUltra`.
2. Start tracking from the debug overlay.
3. With the hand empty and visible, log for 60 s.
4. Record joint count and rate.
5. Repeat with the hand gripping a WMR controller (controller awake, not
   streaming). Record whether joints still arrive.
6. Tap recenter. Confirm `VitureXR.HeadTracking.SetWorldOrigin()` recentres the
   head pose.
7. Stop and dispose. Confirm no leak on repeat.

Capture logcat:

```bash
adb logcat -v threadtime > ony-738-gate4-<YYYYMMDD-HHMMSS>.log
```

Record:

| Item | Value |
|---|---|
| Glasses model | |
| Head-pose rate (Hz) | |
| Joint rate, empty hand (Hz) | |
| Joint rate, gripping controller (Hz) | |
| Hand tracked while gripping? (yes / no / partial) | |
| Joints present (count of 26) | |
| Recenter works? | |
| Errors or exceptions | |
| `playerHosted` (true / false) | |
| `stereoMode` | |
| Highest stage reached | |

Pass and fail for ONY-736 scoring (amendment 5 from ONY-731):

- Grip dropout under 5%: pass.
- Grip dropout over 15%: fail.
- Between 5% and 15%: marginal.

Dropout means the share of the 60 s run in which no hand joints arrive while
gripping. Record the dropout percentage, not only yes or no.

## 4. Gate 1 — LED visibility in 8-bit grayscale frames

Question: do the Luma Ultra stereo grayscale frames show the WMR controller
LEDs?

The ONY-734 tool captures 25 Hz frames and walks a fixed exposure ladder
(0.1, 0.3, 1.0, 3.0 ms) at gain 6. Each rung records at least 250 frames
(10 s). The tool writes one PGM per channel per rung
(`carina-rung<N>-<channel>-<ms>ms.pgm`) plus `carina-diagnostics.json`.

Procedure:

1. Power on both WMR controllers (LEDs active). Dim the room.
2. Run the diagnostic capture at each distance. Use 0.3 m, 0.5 m, 0.8 m, and
   1.0 m. Hold the controllers in three orientations at each distance.
3. At each distance, trigger one 12 s capture:

```dart
final report = await VitureCarinaDiagnostics.capture(
  outputDirectory: '/data/user/0/com.onyxlens.wmr_companion/cache/carina-<distance>',
);
```

4. After each capture, pull the output directory:

```bash
adb pull /data/user/0/com.onyxlens.wmr_companion/cache/carina-<distance> ./carina-<distance>
```

5. Record the telemetry from `carina-diagnostics.json`: width, height, fps,
   frames, frames per rung, pose rate, pose status.

Record per distance:

| Item | 0.3 m | 0.5 m | 0.8 m | 1.0 m |
|---|---|---|---|---|
| Frames captured (>=250?) | | | | |
| FPS observed | | | | |
| Width x height | | | | |
| Frames per rung | | | | |
| LEDs visible? (yes / no) | | | | |
| Approximate LED blob count per frame | | | | |
| Distance at which LEDs disappear | | | | |

Pass and fail for ONY-736 scoring (amendment 3 from ONY-731):

- Pass: at least 5 LED blobs in at least 90% of frames at 0.8 m.
- The tester does not have to count blobs by hand. ONY-736 scores the frames.
  The tester must return the raw PGM frames and the telemetry JSON.

## 5. Checkerboard calibration captures

ONY-736 needs stereo pairs to recover intrinsics. The ONY-734 tool keeps one
copied frame per rung per channel. For calibration, capture extra frames with
the checkerboard in view.

Procedure:

1. Print the checkerboard. Measure and record the square size in millimetres.
2. Hold the checkerboard flat in front of the glasses at 0.3 m to 1.0 m.
3. Tilt it in at least 5 angles (fronto-parallel, rotated left, rotated
   right, tilted up, tilted down) at each of 2 distances.
4. At each pose, trigger a short capture (or take screenshots of the camera
   preview) so both left and right channels are saved.
5. Keep the controller LEDs off during calibration captures, so the blobs do
   not confuse the corner detector.

Record:

| Item | Value |
|---|---|
| Checkerboard square size (mm) | |
| Inner corners (width x height) | |
| Number of stereo pairs captured | |
| Distances covered | |
| File naming used | |

Return the images with their pose labels (for example
`checkerboard-0.5m-fronto.pgm`).

## 6. Gate 9 — head-pose speed on a phone (C SDK)

Question: does `get_gl_pose_carina` run at a usable rate on a plain Android
phone over USB?

Procedure:

1. Connect the Luma Ultra to the phone over USB (OTG). Accept the USB
   permission prompt.
2. Run the diagnostic capture on the phone (same debug APK, same flag). The
   capture polls `xr_device_provider_get_gl_pose_carina` every 10 ms.
3. Record the pose rate, status flag, and USB permission behaviour for 60 s.
4. Record whether `set_dof_type_carina` (3DoF vs 6DoF) is required and what
   happens if it is skipped.

Record:

| Item | Value |
|---|---|
| Pose rate (Hz) | |
| Pose successes / polls | |
| Status flag behaviour | |
| Pose valid? (yes / no) | |
| USB permission prompt? (yes / no) | |
| 6DoF selectable? (yes / no) | |
| Errors | |

Pass and fail for ONY-736 scoring: Gate 9 is worth having even if Gate 1 fails.
Record the result either way.

## 7. What the tester must return

ONY-736 scores the result. Return all of the following:

1. Gate 4 logcat file and the filled Gate 4 table, including the dropout
   percentage.
2. One directory per distance from Gate 1, each containing:
   - The PGM frames (`carina-rung<N>-<channel>-<ms>ms.pgm`).
   - `carina-diagnostics.json`.
3. The checkerboard stereo pairs with pose labels and the square size.
4. Gate 9 telemetry (the same JSON covers pose rate and status).
5. The intake block (section 8) filled in.
6. Zip of all files, with SHA-256:

```bash
zip -r ony-738-<YYYYMMDD>.zip ony-738-<YYYYMMDD>/
sha256sum ony-738-<YYYYMMDD>.zip
```

If a file cannot be attached, paste the logcat excerpt and the `STREAM` lines and
state which files are missing.

## 8. Intake block

```markdown
### ONY-738 grayscale-camera gates

- Handle:
- Date / timezone:
- Firmware SHA:
- App commit:
- Unity export SHA:
- Glasses model: Luma Ultra
- Phone (Gate 9): make / model / Android
- Neckband (Gate 4): model
- Result Gate 4: PASS / MARGINAL / FAIL / BLOCKED
- Result Gate 1: PASS / FAIL / BLOCKED
- Result Gate 9: PASS / FAIL / BLOCKED
- Gate 4 dropout (%): ____
- Gate 1 frames at 0.8 m with >=5 blobs: ____ / ____
- Gate 9 pose rate (Hz): ____
- Files: zip name, SHA-256
- What happened (3 lines max):
```

## 9. Stop rules

- Board hot, smells, or LED goes dark: unplug and stop.
- USB permission denied twice: stop and send what you have.
- `stereoMode` is `unknown` or the stage stops at `playerHosted`: stop; the
  loader is not rendering in stereo 3D (SpaceWalker mode).
- `libglasses.so` missing and the capture returns `sdkUnavailable`: stop and
  report; the build is not deployable for Gate 1.
- Pairing or tracking fails twice: stop and send what you have.
- Do not flash firmware. Do not open the board.

## 10. Pass and fail summary

| Gate | Pass | Fail | Marginal |
|---|---|---|---|
| Gate 4 (grip dropout) | under 5% | over 15% | 5% to 15% |
| Gate 1 (LEDs at 0.8 m) | >=5 blobs in >=90% of frames | below that | n/a |
| Gate 9 (pose rate) | usable rate recorded | no pose or no USB | n/a |

ONY-736 uses these results and the decision table from ONY-731 amendment 5:
grip dropout under 5% and Gate 1 pass promotes Route A; Gate 1 fail keeps the
head-pose-only hybrid; a licensing blocker plus grip dropout under 5% keeps
Route B.
