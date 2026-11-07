# Run sheet: fresh Android XR 6DoF-only bench (2026-11-07)

> **Hardware result from an external test bundle.** The figures below are read
> from the bundle's own files; the triage command is recorded so they can be
> re-derived. This is not a pose-filter tuning corpus (see §4).

Source: `wmr-ext-20261107.zip` from an external tester, Android XR on an XREAL
Android XR Puck (Android 14 / API 34). Companion `0.3.18+2023` at `118b63b`,
no bridge attached, so `firmware_sha` is null and no FF16 read happened.

## 1. Bundle

The guided bench ran in **6DoF only** mode: 5 steps done, 11 skipped, 0 failed
(16 steps). The bridge steps and the latency run are skipped by the mode, so a
zero bridge count and a missing `latency/` directory are expected here, not a
broken export.

Triage:

```bash
python3 tools/triage_bundle.py wmr-ext-20261107.zip
```

Exit 0. App, device and bench mode are reported, all six pose files are listed
in `diagnostics.json` and present, the package names match the recorded
runtime/hand/segment, and every non-occlusion run clears the 14 s floor. Six
problems are reported, all the same repeated-frame finding in §3.

## 2. Pose captures

| File | Hand | Segment | Samples | Distinct anchor frames | Span | Distinct rate |
|---|---|---|---:|---:|---:|---:|
| `pose_capture_androidXr_left_still.json` | LEFT | still | 2151 | 717 | 15.0 s | 47.9 Hz |
| `pose_capture_androidXr_right_drifting.json` | RIGHT | drifting | 2151 | 718 | 15.0 s | 47.9 Hz |
| `pose_capture_androidXr_left_slow_pan.json` | LEFT | slow pan | 2583 | 861 | 18.0 s | 47.9 Hz |
| `pose_capture_androidXr_right_medium_pan.json` | RIGHT | medium pan | 2154 | 719 | 15.0 s | 48.0 Hz |
| `pose_capture_androidXr_left_fast_flick.json` | LEFT | fast flick | 2871 | 957 | 20.0 s | 47.9 Hz |
| `pose_capture_androidXr_right_occlusion.json` | RIGHT | occlusion | 2154 | 719 | 15.0 s | 48.0 Hz |

All six files carry 26 hand joints on every sample, and the timestamps are
monotonic. The manifest's `anchor_rate_hz` is 143.5-143.8; the recorded hand
changes at about **48 Hz**.

## 3. Repeated anchor frames

Each recorded hand frame is written **three times** with timestamps about 1 ms
apart, and the next new frame follows about 18 ms later. `tools/triage_bundle.py`
now reports this as a problem:

```text
pose_capture_androidXr_left_still.json: 1434 of 2151 samples repeat the
preceding tracking frame; the declared anchor rate is overstated
```

The other five files show the same 2/3 repeat share. The cause is in the
companion recorder, not the bridge: it accepts every tracking frame whose
vendor `time_ns` increased, and on this runtime the hand anchor is static across
three such frames while the head and the other hand move. `anchor_rate_hz`
counts those accepted frames, so it is three times the rate of new anchors.

Do not use this corpus for filter tuning. A one-euro filter sees two zero-motion
1 ms samples and then an 18 ms step; the speed estimate and `minDt` are not the
ones `filter-spec` assumes at the declared ~144 Hz. The companion's
`PoseCapture.fromRecording` collapses frames only when `fusionTimeNs` is equal,
which it is not here.

## 4. Capture protocol

The still run is hand-held, not the braced hold in ONY-501's addendum. The left
palm anchor moves 118 x 269 x 142 mm peak to peak over 15 s and about 9 mm RMS
against a 1 s moving mean over the last 10 s. The drifting run has a median
speed of 7.5 cm/s after the first second, below the 10-50 cm/s band; 253 of its
717 distinct frames are inside the band. The repeated frames in §3 still make
this corpus unusable for the ONY-502 score, and the still is not the braced
recapture.

## 5. Follow-ups

- Companion: emit one sample per new anchor (or derive `anchor_rate_hz` from
  distinct hand frames) before the next corpus is recorded. This bundle cannot
  answer ONY-502.
- Companion: the tester instructions still say "hold still" and "let it
  drift". The next capture needs ONY-501's section 8 wording: brace the forearm
  on a table for S, and move D at 10-50 cm/s.
- No bridge, FF16 or latency result is expected from a 6DoF-only sitting; those
  rows stay open in the internal bench runbook.
