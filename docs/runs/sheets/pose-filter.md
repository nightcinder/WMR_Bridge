# Pose filter capture — internal procedure

Internal procedure for the optional **Capture 6DoF pose** step offered by the
guided bench wizard. Tester-facing summary lives in
[`external-test-user.md`](external-test-user.md) §6.

Source of truth for segments, durations and acceptance: **ONY-868 Option A**
(the 6-run alternating-hand protocol, 2026-11-03), which supersedes the
42-run schedule in filter-spec rev 4, section 8. The search grid remains
filter-spec rev 4, section 7.

## Search grid (filter-spec rev 4, section 7)

Copied verbatim from `filter-spec` rev 4 section 7 for the analysis harness.
Operators do not choose these values. If this table and the spec disagree, the
spec wins and the run is invalid.

| Constant | Min | Max | Step | Points |
|---|---|---|---|---|
| minCutoff | 1.0 Hz | 4.0 Hz | 0.25 Hz | 13 |
| beta | 0.00 | 0.40 Hz per cm/s | 0.01 | 41 |
| dCutoff | 0.5 Hz | 4.0 Hz | 0.5 Hz | 8 |

That is 4,264 points per runtime, all of them evaluated by capture replay. No
early stop and no coarse-to-fine pruning.

## When the step appears

- Only when the active tracking source reports hand joints (VITURE / XREAL /
  Android XR).
- Otherwise the wizard records `unavailable: no 6DoF hand-tracking source`,
  marks the step skipped, and continues. It never blocks bundle export.

## Runs (6 total, alternating hands)

The guided bench records one run per motion class plus one occlusion diagnostic
run: six runs, about 98 s of active recording. Alternating hands exercises both
hands while keeping one capture per motion for the pooled runtime tuner.

| Run | Hand | Segment (`PoseMotion`) | Duration |
|---|---|---|---|
| 1 | left | Still | 15 s |
| 2 | right | Held, drifting | 15 s |
| 3 | left | Slow pan | 18 s |
| 4 | right | Medium pan | 15 s |
| 5 | left | Fast flick | 20 s |
| 6 | right | Occlusion | 15 s |

The bench timer holds each run for its listed duration; triage accepts
non-occlusion runs of **14 s or longer** to tolerate timer boundary rounding.
Each run covers one full cycle (1.5 cycles of the 10 s still, drifting and
medium-pan cycles). The legacy 42-run corpus remains available only through
the `--full` bench preset.

## Output

- One ONY-249 `PoseRecording` JSON per run, written into the same bundle zip.
- Filename: `pose_capture_<runtime>_<hand>_<segment>.json`; a `_runN` suffix
  still marks repeat runs from the legacy `--full` preset.
- Manifest records runtime, segment, app commit and firmware SHA for each file.
- Reuses the existing recorder and storage; no new format.

## Skip / fail handling

- Skipped or failed runs are recorded as skipped with the reason.
- Skipping all six still produces a complete bundle.

## Notes

- Tester language only in the external sheet: "six short recordings -- still,
  drifting, slow pan, medium pan, fast flick, and one occlusion run"; about
  1.5-2 min total; files already in the bundle; safe to skip.
- No device SDK detail, no filter jargon, no mention of One Euro in the
  tester-facing copy.
- Filter parameters and tuning are out of scope here; see filter-spec rev 4.