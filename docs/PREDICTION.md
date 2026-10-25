# Packet loss and pose prediction

**Status: investigation and design decision. No firmware or library code is
changed by this document.** The loss figures are user-reported bench results;
the timing and gap analysis below is re-derived from the exported 120 s run in
[`runs/roundtrip/`](runs/roundtrip/latency-20261022-145631.json). One run, one
phone, one position: every number is an observation, not a hardware
characteristic.

## Verdict

- **The loss is on the two Classic links, not on the phone BLE link.** In the
  120 s FF14 run the phone received every record the ESP32 sent (0 sequence
  gaps, 0 incomplete frames, 0 parse errors), while the controllers lost
  17.8 % (RIGHT) and 25.1 % (LEFT) of their reports before the ESP32 numbered
  them.
- **18-25 % missing is too much to call lossless, but it is not 25 % missing
  rotation.** The library integrates the gyro over the controller's own tick
  delta, so a gap still advances the orientation by the true elapsed time. The
  visible cost is stale poses between arrivals, not missing time.
- **Prediction can smooth the presentation, not repair the loss.** It can
  extrapolate orientation for a bounded time; it cannot recover samples that
  were never measured, remove the ~21 ms transport delay, or predict button and
  stick edges. It should be capped and faded, not run open-ended.
- **Prediction belongs in the shared library, not on the ESP32 and not in the
  app.** The ESP32 never sees the missing reports and has no render clock; the
  app has the render clock but would duplicate the logic per platform. The
  library owns the orientation, gyro bias and timestamps, is shared by the
  Flutter app and other consumers, and can expose a presentation-only
  extrapolator with host tests.
- **Recommendation:** treat prediction as an **optional presentation layer**
  after the loss work, not as the fix for loss. The primary loss work remains
  the multi-bridge and Pi/Wi-Fi sittings already planned in
  [`runs/README.md`](runs/README.md). Do not enable prediction by default until
  the tracking-quality sitting has measured its error.

### Does one ESP32 lose too much?

**For a VR-grade dual-controller target, yes. For the current 3DoF viewer, no.**

- The current viewer survives because the effective rate stays at 150-165 Hz
  and the gyro is integrated across the gaps. That is the use it was measured
  in, and it is why smoothing is worth considering there.
- A VR-grade target needs roughly 3-5 % loss or better per hand. One ESP32
  delivers 18-25 % with both controllers and a phone, 9-11 % per hand even with
  no phone, and about 10.8 % on RIGHT alone. The best single-bridge case,
  LEFT alone at about 0.5 %, does not generalise to the pair. Against a 3-5 %
  bar, one ESP32 misses by a factor of four to eight.
- This is not a CPU, queue or firmware-buffering limit. The bridge forwards
  what it receives (`tx` trails `rx` only by records still queued; `drop=0`,
  queue high-water 41 of 256, sample `q=2/256 high=41`), and the
  loss is already present with no phone connected. The deficit is in the radio
  path that delivers controller reports, not in the ESP32's ability to process
  them.
- It follows that more than one ESP32 is **justified for VR-grade tracking**:
  one Classic link per radio is the architecture the multi-bridge sheet tests.
  It is also **not proven to be enough**. With one bridge per hand, LEFT is
  expected to return toward its alone level and RIGHT to lose the roughly eight
  points that appear when LEFT is also connected; RIGHT's own ~10.8 % is still
  there, so even two bridges may not reach a 3-5 % bar. That sitting has not
  run ([`runs/sheets/multi-bridge.md`](runs/sheets/multi-bridge.md)).
- The Pi/Wi-Fi bridge moves only the phone link. It can remove BLE airtime from
  the ESP32 radio, but it cannot remove the 9-11 % no-phone baseline, so it is
  not a single-ESP32 fix either.
- **Architecture decision:** a single ESP32 is adequate for the current 3DoF
  demo and gamepad output, and is not adequate as the final radio for
  VR-grade dual-controller tracking. Prediction does not change that answer;
  it hides sample age, not missing reports.

## 1. The three links and what each one loses

The product has three Bluetooth links:

```
WMR LEFT  -- Classic BR/EDR ACL --> ESP32 --\
WMR RIGHT -- Classic BR/EDR ACL --> ESP32 ----> BLE GATT --> phone
```

| Link | Direction | Measured loss | What the user sees |
| --- | --- | --- | --- |
| LEFT controller to ESP32 | Classic, about 200 reports/s | 17.8-25.1 % in the dual-controller FF14 run; about 0.5 % with LEFT alone; 9-11 % with no phone connected | Reports missing before the ESP32 assigns a sequence number. Only the tick delta reveals them |
| RIGHT controller to ESP32 | Classic, about 200 reports/s | 17.8-25.1 % in the dual-controller FF14 run; about 10.8 % with RIGHT alone; 9-11 % with no phone connected | Same |
| ESP32 to phone | BLE notifications, FF14 batches | **0 sequence gaps, 0 incomplete frames, 0 parse errors** in the same 120 s run | Latency and jitter, not missing records |

The single-controller and no-phone rows are from
[`STATUS.md`](STATUS.md) and [`runs/sheets/contention-2-results.md`](runs/sheets/contention-2-results.md);
the dual-controller row is the run analysed here. The no-phone baseline is the
important qualifier: **even with the BLE link idle, each controller already
loses about a tenth of its reports.** Turning the phone link off, or moving it
to Wi-Fi on the Pi bridge, cannot by itself produce a lossless Classic link.

The fact that the phone sees no gaps also means the loss is not being hidden by
the BLE stack. What the ESP32 receives, it forwards. This is consistent with
[`COUNTERS.md`](COUNTERS.md) and with the bridge's own counters in the recorded
FF14 runs (`drop=0`, `purged=0`, `mtu_skip=0`, `dec_err=0`; `tx` trails `rx`
only by records still queued, so it is not a no-loss proof); this
particular run did not save the serial log, so those bridge counters are not
part of its export.

### 1.1 The 120 s run

Source: `latency-20261022-145631` (FF14, MTU 517, 15 ms interval, high
priority, both controllers, RSSI moved from -69 to -48 dBm during the run).

| Figure | LEFT | RIGHT |
| --- | --- | --- |
| Records received | 17,999 | 19,696 |
| Effective record rate | 150.1 /s | 164.2 /s |
| Missing by device ticks | 6,016 (25.1 %) | 4,268 (17.8 %) |
| Notifications carrying that hand | 4,895 | 5,031 |
| Inter-arrival p50 / p95 / p99 / max | 19.1 / 57.7 / 89.6 / 166.8 ms | 19.7 / 50.0 / 80.8 / 209.9 ms |
| Stalls over 50 ms | 308 | 252 |
| Sequence gaps | 0 | 0 |
| Delay above window minimum p50 / p95 / max | 20.9 / 39.4 / 138.4 ms | 20.1 / 39.5 / 137.6 ms |

Inter-arrival is the spacing of distinct notifications. At 200 reports/s
per hand and up to 9 records per batch, a perfect FF14 stream would flush on
its 15 ms hold timer or on a full batch at about 22 ms when both hands share
the link. The observed 19-20 ms median is therefore plausible; the tail is the
problem.

The "delay above window minimum" is the phone-observed time between the ESP32
receiving a record and the notification reaching the app, with the fixed offset
removed. It is the part a delay-aware predictor would have to cover to produce
a pose "at now": a median of about 21 ms and a p95 of about 39 ms. The
predictor in section 4 does not attempt this; it starts from the arrival time
and hides only inter-arrival gaps.

## 2. Is the loss too much?

There are two different questions: how many samples are missing, and how old
the newest pose is when the renderer needs it. The first is worse than the
second, because orientation time is not lost.

### 2.1 Missing samples

At 4.98 ms per controller report, 18-25 % loss leaves 150-165 Hz. That is still
above a 60, 90 or 120 Hz display, so the sample rate alone is not the problem.
The pattern is the problem. Missing runs from the same run:

| Missing run | LEFT events | LEFT missing reports | RIGHT events | RIGHT missing reports |
| --- | --- | --- | --- | --- |
| 1 report | 503 (31.9 %) | 503 (8.4 %) | 513 (32.8 %) | 513 (12.0 %) |
| 2-3 reports | 302 (19.2 %) | 716 (11.9 %) | 774 (49.5 %) | 1,762 (41.3 %) |
| 4-9 reports | 656 (41.6 %) | 3,391 (56.4 %) | 233 (14.9 %) | 1,438 (33.7 %) |
| 10-19 reports | 115 (7.3 %) | 1,406 (23.4 %) | 45 (2.9 %) | 555 (13.0 %) |
| Longest run | 19 reports (94.6 ms) | | 19 reports (94.8 ms) | |

(A run of `n` means `n` consecutive reports missing; one gap event can remove
several reports.) The two hands have different patterns, which is consistent
with the earlier finding that the two links are not symmetric. More than half
of LEFT's missing reports are in runs of 4-9 (20-45 ms); RIGHT's losses are
shorter but more frequent. Neither is a clean single-report radio loss.

This matters for prediction: a 40 ms gap is not one lost frame, it is a
direction change that the next report will apply to the whole gap. If the hand
was turning steadily, the result is close; if it reversed inside the gap, the
next filter update integrates the new rate over the old gap and the error is
visible.

### 2.2 Age of the newest pose

A renderer does not consume "reports per second"; it consumes the newest
orientation it has. The following is the time-weighted age since the last
arrival, sampled every millisecond over the run, so it includes the long gaps
in proportion to their duration:

| Age of newest sample | LEFT share of time | RIGHT share of time |
| --- | --- | --- |
| p50 | 13.5 ms | 13.0 ms |
| p90 | 39.4 ms | 34.9 ms |
| p95 | 52.9 ms | 46.7 ms |
| p99 | 85.5 ms | 79.5 ms |
| max | 166.2 ms | 209.5 ms |
| over 20 ms | 34.2 % | 31.8 % |
| over 33 ms | 13.8 % | 10.9 % |
| over 50 ms | 5.7 % | 4.2 % |
| over 100 ms | 0.5 % | 0.4 % |

So a third of the time the freshest orientation is more than 20 ms old, and
about one twentieth of the time it is more than 50 ms old. That is the budget a
smoother would be trying to hide. It is large enough to be visible on fast
motion, and it is not removed by adding more samples: the samples do not exist.

### 2.3 What the current library already does

`HandPipeline` (companion `packages/wmr_core/lib/domain/hand_pipeline.dart`)
uses `device_ticks` deltas, not arrival time, and the firmware and app both
keep the tick counter advancing through lost reports
([`PROTOCOL.md`](PROTOCOL.md), "Timing"). The filter therefore integrates the
new gyro sample over the true elapsed interval, including the gap. With a
constant angular velocity this reconstructs the missing rotation exactly. With
a changing velocity it uses the one rate it has, which is a zero-order hold.

That is already a form of prediction, and it explains why the loss does not
produce a frozen 3D view: the orientation keeps moving on the next report. What
is missing is a pose **between** reports and a pose **at render time**. That is
the narrow job left for a predictor.

### 2.4 A working threshold

"Too much" is task-dependent. For this project it helps to split the uses:

| Use | Does 18-25 % loss break it? | Why |
| --- | --- | --- |
| 3DoF orientation viewer / gesture demo | No, but jitter is visible | Tick-based integration preserves rotation time; effective 150-165 Hz is above the display rate. The age table is the cost |
| Gamepad output | Usually no | Buttons and sticks are repeated in every report. A normal 50 ms press spans about ten reports, so several must be lost in a row to miss it; a press that falls entirely inside a 40-90 ms gap can still be missed |
| VR-grade hand tracking | Yes | 40 ms gaps and 100 ms runs are far outside a motion-to-photon budget, and the next report applies the new rate over the old interval. No amount of prediction fixes a wrong turn inside the gap |
| 6DoF / positional tracking | Not applicable today | The WMR stream has no position; only orientation is available. Do not extrapolate position from IMU |

The practical conclusion: the current loss is **acceptable for the existing
3DoF demo with smoothing**, and **not acceptable as a VR tracking result**. It
should keep being treated as the main open problem, not as something a
predictor makes go away.

### 2.5 Have all single-ESP32 contention options been exhausted?

**No.** The obvious firmware and link-policy options have been tried; several
practical ones have not, and a few candidates are closed by the hardware or by
the ESP-IDF API. The distinction matters because "we tried the switches" is not
the same as "a single ESP32 cannot do better".

**Status of each option:**

| Option | Result |
| --- | --- |
| Classic role: ESP32 already master; force-master variant (V3) | No-op; both links establish as master, so slave scheduling is not the cause |
| Disable Classic sniff (`WMR_CFG_CLASSIC_NO_SNIFF=1`, V2) | 17.55 % / 23.19 % in one 120 s run, inside the 14-20 % / 19-26 % baseline spread; no effect shown, not proof of no effect |
| BLE auto latency on (`CONFIG_BTDM_CTRL_AUTO_LATENCY=y`) | A/B/A session against baseline; no effect shown ([`runs/sheets/left-coex.md`](runs/sheets/left-coex.md)) |
| Bluetooth modem sleep off (`CONFIG_BTDM_CTRL_MODEM_SLEEP=n`) | Same A/B/A session; no effect shown |
| Fewer BLE notifications: FF12 decimation N=2/N=4 | LEFT stayed about 18-20 %; RIGHT was lower at N=2/N=4 (about 14-15 % / 12-13 %) than at N=1 (28-36 %), but the runs were not interleaved and the change could not be separated from session drift ([`PROTOCOL.md`](PROTOCOL.md)) |
| FF14 batching (about 48 notifications/s instead of about 300/s) | No clear loss gain; the FF14 run sat inside the FF12 run-to-run spread, and LEFT rose through the session ([`STATUS.md`](STATUS.md)) |
| Phone connected but no stream, then FF11/FF12/FF14 | Loss rises when the phone is present and more when it streams, but the no-phone baseline is already 9-11 %; BLE is a contributor, not the whole cause (recorded sessions, not all at one position) |
| One controller alone versus both | LEFT ~0.5 % alone versus ~19 % both; RIGHT ~10.8 % alone versus ~19 % both. The pair adds contention, and RIGHT has a separate baseline loss |
| Staggered connect (`WMR_CFG_STAGGER_FIRST_HAND=1/2`, V1) | **Code exists, run-ready; the run was never done** ([`runs/sheets/single-esp32-levers.md`](runs/sheets/single-esp32-levers.md) §2) |
| BLE interval: request 30 ms instead of 7.5-15 ms | **Not run.** The request is already a compile-time knob; prerequisite is the B1 image and the phone's granted interval |
| FF14 hold: two connection intervals (`WMR_CFG_BATCH_HOLD_INTERVALS=2`) | **Not run.** Knob added in ONY-176; default 1 keeps the current one-interval hold |
| DLE confirmation on the current build | **Not run on the current build; the 2026-10-22 log shows 251 octets / 2120 µs accepted.** Capture the line again on this image |
| Controller task core/priority and HCI buffers | **Ruled out by the archived diagnostics:** `vhci_drop=0`, `log_errors=0`, queue high-water 41/256 (sample `q=2/256 high=41`), and `tx` trailing `rx` only by queued records; no run needed unless a future window shows a nonzero drop or error counter |

**ONY-176 status (2026-10-24).** No new hardware run has happened, so no lever in
this section has a new result. All six are now either run-ready or ruled out:
V1 and the BLE interval/hold images are compile-time builds; right-asymmetry
phases 1-2 need no flash; DLE is already logged and only needs a current-build
capture; core/priority and HCI buffers are ruled out by the diagnostics above.
The run order, commands and per-lever pass rules are in
[`runs/sheets/single-esp32-levers.md`](runs/sheets/single-esp32-levers.md).
The best measured configuration therefore remains unchanged: default both-hands
FF14 at MTU 517, the granted 15 ms interval and a one-interval batch hold, with
18-25 % dual-hand loss. No single-ESP32 variant has yet beaten it.

**Not tested, and practical:**

- **Physical separation / antenna geometry.** The right-asymmetry sheet swaps
  the controller between hand slots and positions, then swaps the board, to
  separate controller, position and antenna. Its phases 1-2 have not run, and
  they need no reflash ([`runs/sheets/right-asymmetry.md`](runs/sheets/right-asymmetry.md),
  [`runs/sheets/single-esp32-levers.md`](runs/sheets/single-esp32-levers.md) §3).
  This is the cheapest way to find out whether part of the loss is RF geometry
  rather than scheduling.
- **Data Length Extension and BLE airtime.** The firmware now logs the
  data-length event; the 2026-10-22 log shows 251 octets / 2120 µs accepted, so
  DLE is not the unknown it was. A current-build capture and the A/B of a 30 ms
  interval request and a two-interval FF14 hold have not run
  ([`runs/sheets/single-esp32-levers.md`](runs/sheets/single-esp32-levers.md) §4).
- **BLE connection interval/priority variations.** The bridge asks for
  7.5-15 ms; the phone granted 15 ms with latency 1. A longer interval lowers
  the number of connection events but adds latency; it is phone-controlled and
  has not been compared.
- **Controller task core/priority and HCI buffer count.** Not tuned, and no
  longer worth a run on current evidence: the archived logs show `vhci_drop=0`,
  `log_errors=0`, a queue high-water of 41/256 (sample `q=2/256 high=41`) and
  `tx` trailing `rx` only by queued records. A future run that
  shows a nonzero drop or error counter reopens it; otherwise the diagnostics
  rule it out.
- **Classic TX power and channel/frequency-hopping control.** `esp_bredr_tx_power_set`
  is not called and the default is already the maximum; Classic AFH is
  controller-managed and not exposed by this firmware. There is no tested lever
  here, only a board/antenna change.

**Closed or not a lever on this hardware:**

- **2M BLE PHY** is not available on the original ESP32 (BLE 4.2 only); the
  firmware never requests it and the controller would reject it. It would need a
  BLE 5.0 bridge SoC, and the ESP32-S3/C3/C6 parts have no Classic Bluetooth,
  which the controllers require
  ([`DECODING.md`](DECODING.md), "PHY").
- **Classic-against-BLE priority** has no public API. `esp_coex_preference_set`
  arbitrates Wi-Fi against Bluetooth, not BR/EDR against BLE; the controller
  firmware that would implement it is closed.
- **ESP32 controller scheduling and the controllers' own firmware** are not
  modifiable from this repository.

The tested set already bounds the possible gain. Even if every BLE/coexistence
lever worked perfectly, the no-phone baseline is 9-11 %, and RIGHT loses about
10.8 % with no second controller. A single ESP32 therefore cannot reach a 3-5 %
bar by contention tuning alone; the untested work is worth doing to find where
the remaining loss belongs, not because a hidden switch is likely to fix it.
The highest-value next steps are V1, the right-asymmetry sitting, and DLE
logging, in that order. Multi-bridge remains the architecture that could remove
the pair-contention component, and it is still unrun.

## 3. What prediction can and cannot do

### 3.1 It can

- Extrapolate the last orientation toward the render time using the latest
  bias-corrected angular velocity, covering part or all of the 13-20 ms median
  sample age and some of the 50-90 ms tail.
- Keep the output continuous when a report is late, instead of holding the same
  quaternion for 40 ms.
- Be capped and faded so that a long gap ends in a hold, not a runaway spin.
- Be tested on a host with synthetic constant-rate and reversal motion.

### 3.2 It cannot

- Recover a report that never reached the ESP32.
- Remove the transport delay in section 1.1. Prediction can hide sample age;
  it cannot make the phone's received data newer than it is. The ~41 ms FF15
  round trip and the ~21 ms one-way delay remain. The API in section 4.1 uses
  the arrival timestamp, so it deliberately hides inter-arrival gaps only;
  compensating the fixed transport delay would need a separate delay estimate
  from FF15 and is out of scope here.
- Predict buttons, trigger, stick or trackpad edges. Those must stay at their
  last received value; inventing an edge would be worse than a late one.
- Reconstruct motion inside a gap better than the next gyro sample allows.
- Fix a wrong turn during a 40 ms gap. More prediction would only make the
  wrong pose arrive sooner.

### 3.3 Error bound and the cap

For angular speed `ω` and prediction horizon `τ`, the worst-case error if the
hand stops dead at the last sample is `ω·τ` (a small-angle approximation; the
exact value is the rotation angle). This is the number that decides whether the
result is noticeable:

| Angular speed | 10 ms | 20 ms | 40 ms |
| --- | --- | --- | --- |
| 90 °/s (slow aim) | 0.9° | 1.8° | 3.6° |
| 360 °/s (normal turn) | 3.6° | 7.2° | 14.4° |
| 720 °/s (fast swing) | 7.2° | 14.4° | 28.8° |

The table is an upper bound, not the typical error: at constant velocity the
extrapolation is exact. It shows why an open-ended "predict until the next
packet" is not safe. The proposal is therefore:

- **Maximum horizon 25 ms**, which covers the median sample age with headroom
  for a 60 Hz frame. Beyond that, hold the pose reached at 25 ms; do not keep
  rotating through a 100 ms outage.
- **Fade the correction** when the next real sample arrives over about 10-20 ms,
  so the residual does not snap.
- **Cap the angular speed used for prediction** (for example 720 °/s) and
  discard non-finite values. The filter's own inputs already guard
  non-finite values.

The "won't be noticeable" acceptance criterion should be stated as a measured
proxy, not a feeling: on a scripted 90 °/s and 360 °/s turn, the predicted pose
stays within 2° of the next real sample at the 95th percentile, and a direction
reversal at the last sample produces no more than 3° of overshoot. Those are
targets for a follow-up implementation and a hardware sitting; they are not
results of this investigation.

## 4. Where the predictor should live

| Option | What it can see | Advantages | Problems | Verdict |
| --- | --- | --- | --- | --- |
| **ESP32 firmware** | Only the reports that arrived; no render clock | One implementation for every phone; no app change | It cannot predict lost samples because it never saw them. Synthesizing records would consume BLE airtime, add at least one connection interval of delay, and make the phone treat invented data as measured. It does not know when the app will draw | **No** |
| **App** (Flutter view, iOS view, ...) | The rendered frame clock | Can extrapolate exactly to the frame being drawn; easy to tune per app | Duplicates the same logic in every app and on every platform; the 3D view is not the only consumer; the library's public pose stream would still be stale. It also makes the behaviour untestable outside a UI | **No, except as a temporary local patch** |
| **Shared library** (`wmr_core`, above `wmr_fusion`) | Orientation, bias-corrected gyro, device ticks and arrival timestamps; the app's injected clock | One tested implementation for the Flutter app and other consumers; pure Dart, no plugins; can be optional and fail soft; can expose both the measured pose and the predicted pose | It must not feed prediction back into the filter; it needs the app to pass its render timestamp; the Swift kit needs the same formula if it ever runs a native pipeline | **Yes** |

The library is the right boundary because it already owns the state a
predictor needs. `HandPipeline` has the calibrated, bias-corrected gyro and the
filter orientation; `DecodedSample` has the arrival time and the tick step. The
app should choose whether to draw the measured or predicted pose, and at what
timestamp. The library should not own a timer or a display rate.

### 4.1 Proposed API

Keep it presentation-only and independent of the filter:

```dart
class PosePredictor {
  PosePredictor({
    this.maxHorizon = const Duration(milliseconds: 25),
    this.maxAngularSpeed = 12.6, // rad/s, about 720 deg/s
  });

  /// Does not modify the filter or any stored state.
  ///
  /// [orientation] is the last fused pose, [angularVelocity] is in the same
  /// view frame as that pose, [receivedAtUs] is the phone monotonic time when
  /// that sample arrived, and [nowUs] is the render time.
  Quaternion predict({
    required Quaternion orientation,
    required Vector3 angularVelocity,
    required int receivedAtUs,
    required int nowUs,
  });
}
```

`HandPipeline.add` would expose the bias-corrected view-frame angular velocity
on `DecodedSample` (it currently exposes only the fused orientation), and the
predictor would integrate it:

```text
tau = clamp((now - receivedAt), 0, maxHorizon)
q_pred = q_last * exp(0.5 * omega * tau)      // quaternion exponential
```

At the next real sample, the view can blend from `q_pred` to the new measured
pose over 10-20 ms. The predictor must never call `_filter.update` and must
never change `q_last`; repeated calls at different render times must be pure.

If the correction is applied at the UI, the UI still consumes the library's
sample stream. That keeps the protocol, timing and filter ownership in one
place; the app only supplies "now" and a blend policy.

### 4.2 Tests for a follow-up implementation

These are host tests, no hardware:

- Constant angular rate over a known interval: the predicted quaternion equals
  the analytic rotation within floating-point tolerance.
- Zero angular velocity: the predicted pose equals the last pose at every
  horizon.
- Horizon cap: at 100 ms of age the output equals the output at 25 ms, not a
  larger rotation, and it does not keep rotating as the outage grows.
- Direction reversal at the last sample: overshoot stays under the stated
  bound.
- Purity: two calls with the same inputs return the same value and leave the
  pipeline's orientation and filter state unchanged.
- Non-finite gyro: the predictor returns the last pose instead of producing a
  NaN.

## 5. Is the 2026-10-22 latency run still current?

**Short answer: the stream ladder has one current pass; the repeats and
latency-2 are not run. Four 120 s runs on 2026-10-24 with firmware `v0.1.1`
and the `0.1.0` app measured rtt p50 40.5 ms (FF14) against 40.8 ms on
2026-10-22, 44.8 ms (FF11), 44.3 ms (FF12) and 61.6 ms with no stream. So
neither the client refactor nor the firmware changes since then moved the
streamed median, and the old ~59 ms no-stream penalty reproduced. The runs are
sequential at one stated position rather than interleaved, the FF14 and none
repeats are missing, and latency-2 (app against the native probe) is still not
run, so the split between app, plugin and radio remains open.** The rest of
this section keeps the 2026-10-22 comparison as the record of what changed
between the two builds.

The run was made at 2026-10-22 14:56, when firmware `main` was `203108c`
(2026-10-22 14:41) and companion `main` was `3df70a4` (14:42). The export does
not record the actual APK or firmware hash. Current `main` is `6520deb` on the
firmware side and a later commit on the companion side.

### 5.1 Firmware changes since the run

| Change | Latency relevance |
| --- | --- |
| Single-hand mode (`3c9fad3`) | Default both-hands build is unchanged; it gates which hands are served |
| FF16 packaged version (`41771f8`) | Read-only characteristic and console command; no stream path |
| Build/flash framework (`a1859ad`), LEFT/RIGHT variants (`547ab09`) | Packaging and default build selection; the both-hands runtime path is unchanged |
| Pi/Wi-Fi bridge (`079ef19`) | Separate host bridge; not the ESP32 BLE path |
| PSVR2 framework (`32de94a`) | Compile-time opt-in, off by default; the default WMR build keeps its frame limit and paths |
| RGB status LED (`d2c7553`) | Status only; adds an LED poll timer, no transport or batching change |
| FF14 edge fixes (`b2268dd`) | Receiver-side batch bounds, 64-bit hold-timer rounding, and abandoning a partial frame on a bad-magic fragment. Normal 15 ms FF14 operation is unchanged; the timer fix only matters for very large hold values |
| Docs and flasher (`6520deb` and the docs commits) | No runtime change |

So no known firmware change invalidates the old latency number, and none
explains the 40.8 ms either. The old commits are not current `main`, though:
a current figure still has to be measured on a current image.

### 5.2 App changes since the run

The app changed materially, including in the exact path that produced the
number:

- `b818a21` added the latency screen's no-stream mode, stream selector and ping
  interval after the run.
- `29f6b02`, `abc3090`, `8dc21df` and others changed the fusion/gamepad path
  (CPU work per sample, not the transport itself).
- The package extraction (`5d37cf8`, `e90dde0`, and follow-ups) moved the
  latency recorder, transport and export into `wmr_core`/`wmr_ble`.
- `76268ca` changed the latency export to per-bridge attribution and version 3;
  the saved run is version 2.
- `9c42006` added multi-bridge support, and `28e0e45` changed how a latency run
  stops across provider disposal.

The run therefore used a client that no longer exists. The old figure is still
useful as a historical data point, but it is not a current measurement of the
current app.

### 5.3 The 2026-10-24 runs

The FF14 slot is covered by 2026-10-24 18:43:35, 120 s, FF14 batched, 250 ms
pings, both controllers, WMR_Bridge `v0.1.1` and wmr-companion `0.1.0`
(owner-reported versions; the export records neither hash). rtt p50 40.5 ms,
p95 73.6 ms, p99 102.5 ms, max 125.3 ms; 479 of 479 pings answered, median ESP32
turnaround 0.40 ms. Stream tick loss was LEFT 21.0 %, RIGHT 15.2 %, inside the
18–25 % band. RSSI was -86 to -81 dBm, weaker than 2026-10-22 and steadier, but
the position was not recorded. Files:
[`runs/roundtrip/`](runs/roundtrip/README.md).

A later sitting on the same release pair adds the other three streams: none at
20:11:49 (p50 61.6 ms, 438 of 479 pings over 50 ms, floor 45.0 ms, turnaround
0.31 ms), FF11 raw at 20:14:40 (p50 44.8 ms, 191 over 50 ms, 18/26 sequence
gaps) and FF12 decoded at 20:16:56 (p50 44.3 ms, 194 over 50 ms, 36/58 gaps,
tick loss 30.7 % LEFT / 17.6 % RIGHT). All three answered 479 of 479 pings.
The no-stream run is 17–21 ms slower than any streamed mode while its RSSI
sample was the best of the sitting (-80 dBm at both ends), which is the
opposite of a stream-load effect and matches the 2026-10-22 no-stream result.
The FF11 RIGHT stream did not start until 35.1 s into that run, so its LEFT-only
figures are complete. The runs were sequential, not interleaved, and the RSSI
samples span 8 dB (the app reads RSSI twice per run, at start and stop, at 1 dB
resolution, so that spread does not prove the rig moved): the 4 ms FF11/FF12
penalty against FF14 cannot be separated from session drift.

The FF14 and no-stream repeats, and latency-2, remain open. In particular, no
run yet separates the app's share of the round trip from the radio's.

### 5.4 What is still required for a current latency claim

**The ladder repeats and latency-2.** Do not run a new bespoke test; the two
existing sheets already define what is missing:

- [wmr-companion LATENCY.md](https://github.com/nightcinder/wmr-companion/blob/main/docs/LATENCY.md)
  "Run sheet": six runs at one fixed position (none, FF11, FF12, FF14, then
  none and FF14 repeated), 120 s each, FF15 echo on, 250 ms ping interval, with
  the current firmware and APK hashes recorded. The 2026-10-24 sitting covers
  the first four (one pass, not interleaved); the two repeats are still needed
  before a 4 ms difference between streams is called real.
- [latency-2](https://github.com/nightcinder/wmr-companion/blob/main/docs/runs/sheets/latency-2.md):
  the Flutter client against the bare-Kotlin GATT probe, which is the
  decision that actually needs a current number. The probe is merged
  (`tool/gatt_probe`, PR #36) and builds an APK in CI; the sitting has not run.

Add the current **both-hands versus single-hand** comparison if the question is
specifically the single-ESP32 capacity, rather than only the client. The
controller-loss runs already answer the direction (both hands are worse), but
they predate the current firmware too.

The old run is also weak as a fixed-position baseline: RSSI moved from -69 to
-48 dBm during it, so its 40.8 ms cannot be treated as a property of the
hardware at a fixed distance. The 2026-10-24 run was steadier (-86 to -81 dBm)
but its position was not recorded either, so neither is a taped-position
baseline. Any rerun should hold the phone and controllers in one taped position
and record RSSI at the start and end.

## 6. If prediction is built, when is it "done"?

The acceptance criteria for a future implementation are deliberately separate
from this document:

1. The predictor is opt-in in `wmr_core` and off by default; the measured pose
   remains the default output.
2. The host tests in section 4.2 pass, and `flutter analyze` / the package
   tests pass in `wmr-companion`.
3. The tracking-quality sitting shows no change in 90° turn error or yaw drift
   with prediction on (the predicted pose must not contaminate the filter).
4. The latency-2 sitting shows no increase in its fixed latency rule; the
   predictor must not add a frame of delay.
5. A subjective A/B on the 3D view at both slow and fast motion accepts that
   the correction is not visible. If the A/B fails, lower the horizon to 15 ms
   or drop the feature.

Until those exist, this is a **design recommendation, not a shipped feature**.

## 7. What would change this answer

- **Loss falls below about 5 % per hand** (for example with one bridge per
  controller, or a better Classic link): prediction becomes optional polish,
  not a mitigation. The multi-bridge and Pi sittings are the tests that could
  produce this; neither has run.
- **The BLE link starts losing records** (sequence gaps on the phone): the
  problem moves to the transport, and a predictor cannot help. The current run
  shows zero gaps, but it is one run.
- **A 6DoF stream is added**: orientation extrapolation would not be enough,
  and position must not be invented from IMU. That would be a different design.
- **The app moves to a native pipeline** (`WMRCompanionKit` doing fusion and
  prediction in Swift): the same formula and test vectors must be ported, or
  the native path must consume the Dart predictor. Do not create two
  incompatible definitions of "predicted pose".

## 8. Bottom line

- Loss is real and concentrated on the two Classic uplinks: about 18-25 % per
  hand with both controllers and a phone, about 9-11 % even with no phone, and
  essentially zero on the BLE downlink in the measured run.
- **One ESP32 is enough for the current 3DoF demo and gamepad use, and not
  enough for VR-grade dual-controller tracking.** At 18-25 % it misses a 3-5 %
  target by a factor of four to eight; the no-phone baseline and RIGHT-alone
  loss show the limit is the radio path, not ESP32 CPU or queueing. Two bridges
  are justified for that target but not yet measured, and even they may not
  remove RIGHT's own ~10.8 %.
- **The single-ESP32 contention search is not exhausted, but every remaining
  lever is now either run-ready or ruled out.** V1 stagger, right-asymmetry phases 1-2, current-build
  DLE capture, a 30 ms interval request and a two-interval FF14 hold have not
  run; the core/priority and HCI-buffer lever is ruled out by zero drop/error
  counters. 2M PHY and Classic-vs-BLE priority are closed by the hardware/API.
  The no-phone and RIGHT-alone baselines bound the possible gain, so these tests
  are diagnostic rather than a likely route to 3-5 % on one ESP32.
- It is too much for VR-grade tracking but survivable for the current 3DoF
  viewer because the library integrates over the controller's tick gaps. The
  visible defect is stale poses: over 20 ms one third of the time, over 50 ms
  about one twentieth of the time.
- A capped, faded orientation extrapolator can hide the sample-age part
  without inventing input events. It is not a substitute for reducing loss and
  cannot remove the transport delay.
- Put it in the shared library (`wmr_core`), driven by an app-supplied render
  time. Do not put it on the ESP32, which cannot see the loss, and do not make
  it an app-only feature, which would duplicate it per platform.
- The 40.5–44.8 ms streamed latency figures are current for the `0.1.1`/`0.1.0`
  release pair (the 2026-10-24 runs reproduced the 2026-10-22 FF14 median and
  added none/FF11/FF12), but the ladder repeats and the app/plugin/radio split
  still need the existing latency-2 sitting before the stream ordering or a
  native-vs-Flutter decision is quoted as a result.
- Build prediction only after the loss work, as an opt-in presentation layer
  with the tests and hardware acceptance in sections 4-6.

## Evidence and limits

- Loss, rates, inter-arrival, stalls, sequence gaps and delay figures:
  [`runs/roundtrip/latency-20261022-145631.json`](runs/roundtrip/latency-20261022-145631.json)
  and its `-samples.csv`, one 120 s run on 2026-10-22, user-reported. The five
  2026-10-24 runs (FF14, FF11, FF12, none and the 18:43 FF14) and their
  echo/samples CSVs are in the same folder, with the setup and the figures in
  [`runs/roundtrip/README.md`](runs/roundtrip/README.md).
- Missing-run lengths and age-of-newest-sample tables: computed for this
  document from `device_dt_us` and `arrival_us` in that CSV, using the median
  `device_dt_us` as the period and `round(dt / period) - 1` for missing reports.
  The same definition is in [`COUNTERS.md`](COUNTERS.md) and the companion
  `TickTimer`.
- Single-controller and no-phone loss: [`STATUS.md`](STATUS.md) and
  [`runs/sheets/contention-2-results.md`](runs/sheets/contention-2-results.md),
  user-reported.
- The runs had one phone each, an unrecorded position and RSSI sampled only at
  the start and end of each run (-69 to -48 dBm on 2026-10-22; -80 to -88 dBm
  across the 2026-10-24 sitting, whose owner states the rig did not move). They
  do not establish a loss rate for the hardware, and they do not separate radio
  contention from the controller's own behaviour. The 2026-10-24 ladder is one
  sequential pass, not the interleaved sheet, so stream-to-stream differences
  of a few ms are not results.
- The firmware and app commit audit in section 5 uses the repository histories:
  firmware `203108c` at the 2026-10-22 run versus `6520deb` then; companion
  `3df70a4` at that run versus the later `main`. It is a code-history
  comparison, not a measurement of the current builds. The 2026-10-24 run used
  the `v0.1.1`/`0.1.0` release pair, reported by the owner; the export records
  no hashes.
- No new hardware was run for this document and no firmware or library code was
  changed; the 2026-10-24 figures are user-reported exports, archived under
  `runs/roundtrip/`.
