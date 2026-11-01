# Run sheet: grayscale-camera gates (ONY-733 Gate 4, ONY-735 Gates 1 and 9)

> **Proposed sheet, not yet run.** Nothing here is a hardware result. Every
> figure is recorded by the tester. The procedures and thresholds come from
> ONY-733, ONY-735, and the counsel amendments in ONY-731. Do not change the
> thresholds.

Companion documents:

- ONY-733 Gate 4: hand tracking while gripping a WMR controller (Pro Neckband).
- ONY-735 Gates 1 and 9: LED visibility, checkerboard calibration, head-pose
  speed on the Luma Ultra.
- ONY-736: the scorecard that uses what this sheet produces.
- Companion (wmr-companion) v0.3.8: `docs/tracking/GATES.md` and the guided
  bench (`apps/wmr_companion_app/lib/ui/bench/bench_run.dart`).

Gates covered: **Gate 4** (hand tracking gripping a controller), **Gate 1**
(8-bit stereo frame LED visibility), **Gate 9** (C-SDK head pose on a phone).

This sheet runs the gates through the **guided bench** in Companion v0.3.8,
not the debug capture tool. The bench records each step in the test bundle
manifest and packs the capture directories into the export zip.

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
| Bridge | Combined / 3DoF only | One board on `main`, both controllers paired and awake. Record firmware SHA and boot banner. Gate 1, the checkerboard and Gate 9 run without a bridge: in 3DoF only mode the bridge steps come first but can be skipped with Skip step, which attaches the USB-only Carina session. |
| Controllers | All gates | Both WMR controllers, powered, LEDs active. |
| Checkerboard | Gate 1 (calibration) | Printed checkerboard with known square size, flat and rigid, at least 8 by 6 inner corners. |
| Tooling | All gates | `adb` (wireless for the Neckband), a terminal, logcat capture, a way to screenshot the debug overlay. |

App build:

- Install the signed Companion **v0.3.8** unified APK (versionCode 2013) from
  the GitHub release:
  `https://github.com/nightcinder/wmr-companion/releases/download/v0.3.8/wmr-companion-0.3.8-unified-arm64-v8a.apk`
- The release APK already carries `WMR_VITURE_CARINA_DIAG=true`, so the Carina
  gate runs on a signed tester build. It also ships `libglasses.so` (VITURE XR
  Glasses SDK for Android 2.4.1) under the VITURE licence; the EULA and the
  third-party notices ship in the APK and are reachable from About.
- No Unity export zip, no manual `jniLibs` staging, and no debug flags are
  needed for a tester run.

Record the app version (0.3.8), the Unity export SHA if known, and the
firmware SHA in the intake block.

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

## 3. Run the guided bench

Open the app, go to the Connect screen, tap **Run guided bench**, and pick
a mode. No single run covers every gate: Gate 4 needs the VITURE Pro Neckband
(hosts the app), while Gate 9 needs a plain Android phone over USB (section 7).
Run the bench twice and combine the two bundles for ONY-736.

- **Combined** mode (Neckband host): bridge steps, then Gate 4 (`handEmpty`
  then `handGrip`, 60 s each), then the 6DoF pose capture (`poseCapture`),
  then Gate 1 (`carinaLeds`), then the checkerboard (`carinaCheckerboard`),
  then Gate 9 (`carinaPose`; skip it here, since Gate 9 is the phone run,
  or record it labelled as the Neckband host), then `export` (test bundle
  zip). This covers Gate 4, Gate 1 and the checkerboard.
- **6DoF only** mode (glasses, no bridge): skips the bridge steps but keeps
  Gate 4 and the pose capture.
- **3DoF only** mode (phone or bridge host): keeps Gate 1, the checkerboard
  and Gate 9 (`carinaPose`). Run this on a phone for Gate 9; the bridge
  steps appear first but can be skipped with Skip step on each bench step
  screen, which attaches the USB-only Carina session.

`export` writes the test bundle zip for each run.

## 4. Gate 4 — hand tracking while gripping a controller

Question: does `VitureXR.HandTracking.Start()` deliver 26 joints, and is a hand
tracked while gripping a WMR controller?

The bench runs two measured steps, 60 s each:

- `handEmpty`: hold the hand empty and visible. The run records the joint rate
  and the share of time with no hand joints.
- `handGrip`: grip a powered WMR controller and keep the hand in view. Grip
  dropout under 5% passes, over 15% fails, and 5-15% is marginal. ONY-736
  scores the recorded percentage.

Record:

| Item | Value |
|---|---|
| Glasses model | |
| Head-pose rate (Hz) | |
| Joint rate, empty hand (Hz) | |
| Joint rate, gripping controller (Hz) | |
| Hand tracked while gripping? (yes / no / partial) | |
| Joints present (count of 26) | |
| Errors or exceptions | |
| `playerHosted` (true / false) | |
| `stereoMode` | |
| Highest stage reached | |
| Gate 4 dropout (%) | |

Pass and fail for ONY-736 scoring (amendment 5 from ONY-731):

- Grip dropout under 5%: pass.
- Grip dropout over 15%: fail.
- Between 5% and 15%: marginal.

Dropout means the share of the 60 s run in which no hand joints arrive while
gripping. Record the dropout percentage, not only yes or no.

## 5. Gate 1 — LED visibility in 8-bit grayscale frames

Question: do the Luma Ultra stereo grayscale frames show the WMR controller
LEDs?

The bench runs 12 captures: 4 distances (0.3, 0.5, 0.8, 1.0 m) times 3
orientations (front, rotated, tilted). Each capture runs 12 s. The capture
walks a fixed exposure ladder (0.1, 0.3, 1.0, 3.0 ms) at gain 6, so each of
the four rungs gets about 3 s, roughly 75 frames at 25 Hz (about 300 total)
per capture. It writes one PGM per channel per rung
(`carina-rung<N>-<channel>-<ms>ms.pgm`) plus `carina-diagnostics.json`. It
retains one frame per rung per channel (16 stills per capture, 48 per
distance across the three orientations); the telemetry JSON carries the full
frame counts.

Hold one orientation for the whole capture. The tool advances the exposure
rung by elapsed time, so mixing orientations in one capture confounds
exposure with pose.

Procedure the bench follows:

1. Power on both WMR controllers (LEDs active). Dim the room.
2. Connect the Luma Ultra over USB. Accept the USB permission prompt.
3. Run the guided bench `carinaLeds` step. It creates one directory per
   distance and orientation and runs one 12 s capture in each.
4. After the run, the test bundle packs every directory.

Record per distance:

| Item | 0.3 m | 0.5 m | 0.8 m | 1.0 m |
|---|---|---|---|---|
| Frames captured (3 x ~300 per distance) | | | | |
| Frames per rung (3 x ~75 per distance) | | | | |
| FPS observed | | | | |
| Width x height | | | | |
| Retained stills (48 per distance) | | | | |
| LEDs visible? (yes / no) | | | | |
| Approximate LED blob count per retained still | | | | |
| Distance at which LEDs disappear | | | | |

Pass and fail for ONY-736 scoring (amendment 3 from ONY-731):

- Pass: at least 5 LED blobs in at least 90% of frames at 0.8 m. This
  threshold is unchanged.
- The bench retains one frame per rung per channel (16 stills per capture, 48
  per distance), so ONY-736 scores the retained stills plus the frame counts in
  the telemetry JSON. If a full frame series is needed, the harness must retain
  it; that is outside this sheet.
- The tester does not have to count blobs by hand. ONY-736 scores the frames.
  The tester must return the raw PGM frames and the telemetry JSON.

## 6. Checkerboard calibration captures

ONY-736 needs stereo pairs to recover intrinsics. The bench runs 10 captures:
2 distances (0.5 m and 0.8 m) times 5 angles (fronto-parallel, left, right,
up, down). Each capture runs 4 s. Keep the controller LEDs off during these
captures (power the controllers down; the bench does not switch them off), so
the blobs do not confuse the corner detector.

Procedure:

1. Print the checkerboard. Measure and record the square size in millimetres.
2. Hold the checkerboard flat in front of the glasses.
3. Run the guided bench `carinaCheckerboard` step.
4. Record the square size in the intake block.

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

## 7. Gate 9 — head-pose speed on a phone (C SDK)

Question: does `get_gl_pose_carina` run at a usable rate on a plain Android
phone over USB?

Procedure:

1. Connect the Luma Ultra to the phone over USB (OTG). Accept the USB
   permission prompt.
2. Run the guided bench `carinaPose` step. It runs one 60 s capture and polls
   `xr_device_provider_get_gl_pose_carina` every 10 ms.
3. Record the pose rate, status flag, and USB permission behaviour.
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
Record the result either way. Pass: pose rate of at least 30 Hz sustained over
the 60 s run with a valid pose. Fail: no pose, or no USB. (ONY-735 does not
state a numeric target; 30 Hz is the scoring target used here.)

## 8. What the tester must return

ONY-736 scores the result. The guided bench `export` step writes the same zip
as Export test bundle: it contains this run's step results in the manifest
and packs the capture directories. Return all of the following:

1. Both test bundle zips from the `export` step, one per run, each with its
   SHA-256 and labelled by host: the Neckband (Combined) bundle carrying
   Gate 4, Gate 1 and the checkerboard, and the phone (3DoF only) bundle
   carrying Gate 9. Do not send only one; ONY-736 needs both.
2. Gate 4 logcat file and the filled Gate 4 table, including the dropout
   percentage.
3. The Gate 1 capture directories (one per distance and orientation:
   `carina-led-<distance>-<orientation>`), each containing:
   - The PGM frames (`carina-rung<N>-<channel>-<ms>ms.pgm`).
   - `carina-diagnostics.json`.
4. The checkerboard stereo pairs with pose labels and the square size.
5. Gate 9 telemetry (the same JSON covers pose rate and status).
6. The intake block (section 9) filled in.

The manifest records, for each step: id, status, summary, and the captured
data. For `carinaLeds` it lists 12 captures with distance, orientation,
directory and the full diagnostic report. For `carinaCheckerboard` it lists 10
captures with distance, pose, directory and report. For `carinaPose` it records
pose rate, successes, polls, status and valid flag. ONY-736 uses these fields
for the scorecard.

If a file cannot be attached, paste the logcat excerpt and the `STREAM` lines
and state which files are missing.

## 9. Intake block

```markdown
### ONY-738 grayscale-camera gates

- Handle:
- Date / timezone:
- Firmware SHA:
- App version: 0.3.8 (versionCode 2013)
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
- Files: zip name(s), SHA-256 (one per run, two bundles)
- What happened (3 lines max):
```

## 10. Stop rules

- Board hot, smells, or LED goes dark: unplug and stop.
- USB permission denied twice: stop and send what you have.
- `stereoMode` is `unknown` or the stage stops at `playerHosted`: stop; the
  loader is not rendering in stereo 3D (SpaceWalker mode).
- `libglasses.so` missing and the capture returns `sdkUnavailable`: stop and
  report; the build is not deployable for Gate 1.
- Pairing or tracking fails twice: stop and send what you have.
- Do not flash firmware. Do not open the board.

## 11. Pass and fail summary

| Gate | Pass | Fail | Marginal |
|---|---|---|---|
| Gate 4 (grip dropout) | under 5% | over 15% | 5% to 15% |
| Gate 1 (LEDs at 0.8 m) | >=5 blobs in >=90% of frames | below that | n/a |
| Gate 9 (pose rate) | >=30 Hz sustained, valid pose | no pose or no USB | n/a |

ONY-736 uses these results and the decision table from ONY-731 amendment 5:
grip dropout under 5% and Gate 1 pass promotes Route A; Gate 1 fail keeps the
head-pose-only hybrid; a licensing blocker plus grip dropout under 5% keeps
Route B.
