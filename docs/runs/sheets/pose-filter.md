# Pose filter capture — internal procedure

Internal procedure for the optional **Capture 6DoF pose** step offered by the
guided bench wizard. Tester-facing summary lives in
[`external-test-user.md`](external-test-user.md) §6.

Source of truth for segments, durations and acceptance: **filter-spec rev 4,
section 8**. This sheet restates them for operators; if they disagree, the spec
wins.

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

## Segments (per hand, one screen each)

Plain-language prompt plus countdown. Durations and motions are fixed by the
spec; do not improvise.

1. **Still** — cycle length 10 s. Controller rested in the hand, arm braced.
2. **Held, drifting** — cycle length 10 s. Hold as if aiming and let it wander slowly.
3. **Slow pan** — cycle length 18 s (30 cm sideways over 3 s, back and forth, 3 times).
4. **Medium pan** — cycle length 10 s (50 cm sideways in 1 s, back and forth).
5. **Fast flick** — cycle length 20 s (45 cm flick in 0.3 s, pause, return; 10 reps).

Run >= 30 s: each segment cycle repeats until the timer ends.

## Output

- One ONY-249 `PoseRecording` JSON per hand per segment, written into the same
  bundle zip.
- Filename: `pose_capture_<runtime>_<hand>_<segment>.json`.
- Manifest records runtime, segment, app commit and firmware SHA for each file.
- Reuses the existing recorder and storage; no new format.

## Skip / fail handling

- Skipped or failed segments are recorded as skipped with the reason.
- Skipping all five still produces a complete bundle.

## Notes

- Tester language only in the external sheet: "five short movements -- still,
  drifting, slow pan, medium pan, fast flick"; ~2 min per hand; files already
  in the bundle; safe to skip.
- No device SDK detail, no filter jargon, no mention of One Euro in the
  tester-facing copy.
- Filter parameters and tuning are out of scope here; see filter-spec rev 4.