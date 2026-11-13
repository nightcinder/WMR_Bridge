# Shared golden vectors (`wmr_golden_v1.json`)

One JSON file, committed byte-identically in two repositories:

| Repository | Path | Read by |
| --- | --- | --- |
| WMR_Bridge | `tests/vectors/wmr_golden_v1.json` | `pi/tests/test_golden_vectors.py` (Python, standard library only) |
| wmr-companion | `packages/wmr_core/test/fixtures/wmr_golden_v1.json` | `packages/wmr_core/test/golden_vectors_test.dart` |

This README is also the same text in both places (`tests/vectors/README.md`
and `packages/wmr_core/test/fixtures/README_golden.md`). Change the JSON, this
text and both tests together, and keep the two copies of the JSON identical
(`sha256sum` must match).

All inputs are **synthetic**. Nothing in the file was captured from a
controller, a bridge or a phone, so a passing test says the two code bases
agree on the bytes, not that the bytes match hardware.

## How the file was produced

The byte values were not typed by hand. A generator script ran the Pi
reference code (`pi/wmr_bridge/decode.py`, `pi/wmr_bridge/protocol.py`, the
FF15 handler in `pi/wmr_bridge/ble_gatt.py`) at WMR_Bridge `c7fa4ce` on
synthetic 46-byte input reports and stored what came out. The timing
expectations came from a line-by-line Python port of the app's
`TickTimer.advance` (`packages/wmr_core/lib/domain/tick_timer.dart`) and were
cross-checked against the Pi probe's `DevLossEstimator`
(`pi/tools/wifi_probe.py`, a port of `src/wmr_devloss.c`) for every sequence
without an `rx_time_us` discontinuity. The generator is not committed; the
file is the contract. To extend it, add cases with the reference code, never
by editing hex.

## Conventions

- Hex strings are lowercase, no separators, bytes in wire order.
- Every integer is below 2^53, so any JSON reader keeps it exact.
- Floats are exact IEEE 754 binary32 values widened to binary64, so tests
  compare with `==`. `*_f32_hex` gives the four wire bytes (little-endian).
- `hand` is 0 = LEFT, 1 = RIGHT.

## Fields

| Key | Content |
| --- | --- |
| `format`, `version` | `"wmr-golden"`, `1` |
| `ff11_frames[]` | Synthetic 46-byte WMR input reports (`frame_hex`, starting `a1 01`) with the `hand`, `sequence` and `rx_time_us` the bridge would attach; `state` is the expected `ControllerState` (`decode_frame`); `record` is the expected FF12 version 1 record field by field; `record_hex` is the exact 56-byte record |
| `ff11_invalid_frames[]` | Reports the decoder must refuse (wrong length, HID header or report ID) |
| `ff11_fragments` | The FF11 fragments of `ff11_frames[frame_index]` at notification capacity 244 (one fragment) and 44 (two fragments) |
| `ff12_invalid_records[]` | Values an FF12 parser must refuse; `dart_error` is the `DecodedParseError` name |
| `ff14_batch` | One FF14 value (`version` 2, `count` 3) built from `ff11_frames[record_indices]`, plus `invalid[]` values with the `BatchParseError` name in `dart_error` |
| `ff15_echo` | 4-byte requests and 20-byte answers: the PROTOCOL.md golden example (ESP32 layout), the Pi answer (`turnaround_us` and `queue_depth` 0), a saturated one; `invalid_answers[]` with the `EchoParseError` name |
| `timing` | The `TickTimer` constants and `sequences[]` (below) |

Cases in `ff11_frames`: centred stick, stick at both ends and one count off
centre, trigger full and half, trackpad untouched (`0xFF`), touched at the
centre and at the edges, all seven button bits, IMU words at the signed 24-bit
limits and at +-1 count, a rest sample near 1 g, and `device_ticks` just
before, at and after the 2^32 wrap (with the sequence number wrapping too).

### `timing.sequences[]`

Each sequence is one hand. `samples[]` are `(device_ticks, rx_time_us)` pairs
in arrival order. `expected_steps[i]` is what `TickTimer.advance` returns for
sample `i`: `kind` (`baseline`, `step`, `discontinuity`), `dt_ticks`
(`(ticks - previous) mod 2^32`, 0 unless `step`) and `dt_us`
(`dt_ticks / 10`). `expected_totals` are the counters after the last sample:
`steps`, `discontinuities`, `long_gaps`, `missing`, `period_ticks` (null
before the 16-delta warm-up). `expected_ticks64`, present when the sequence
has no discontinuity, is the tick counter unwrapped to 64 bits from the first
sample.

| Sequence | What it pins |
| --- | --- |
| `u32_tick_wrap` | `dt` stays 5 ms across the 2^32 wrap |
| `discontinuity_over_100ms` | PROTOCOL.md rule: tick time and `rx_time_us` more than 100 ms apart re-baselines |
| `tolerance_edge` | exactly 100 ms apart is still a step; 100.001 ms, and `rx_time_us` going backwards, are discontinuities |
| `reconnect_tick_restart` | the tick counter restarting near zero after a 2 s reconnect |
| `gap_missing_after_warmup` | 4-period and 2-period deltas count 3 + 1 missing reports |
| `gap_during_warmup` | a gap inside the first 16 deltas is counted retroactively |
| `long_gap_over_5s` | a delta over 5 s is a step counted in `long_gaps`, never in `missing` |
| `zero_delta_and_jitter` | a jittered 5.88 ms period, a repeated tick value (ignored) and a 3-period delta |

## What each side checks

- Python (`cd pi && ./run_tests.sh`, or
  `cd pi && python3 -m pytest tests/test_golden_vectors.py -q`): `decode_frame` against `state`, `record_from_state` +
  `pack_record` against `record_hex`, `unpack_record` against `record`,
  fragments, the batch, the echo layout and the Pi echo handler, the step
  kinds by the PROTOCOL.md rule, `extend_ticks` against `expected_ticks64`,
  and `DevLossEstimator` against the missing counts.
- Dart (`cd packages/wmr_core && dart test test/golden_vectors_test.dart`):
  `parseDecodedRecord` / `encodeDecodedRecord`, `parseDecodedBatch` /
  `encodeDecodedBatch`, `encodeEchoRequest` / `parseEchoAnswer`, and
  `TickTimer` step by step. The app has no 46-byte decoder, so `frame_hex`
  and `state` are not consumed there.

## Known differences the file does not paper over

- The Pi and firmware parsers refuse an FF14 `count` above 9;
  `parseDecodedBatch` has no upper bound. No vector covers it, so that the
  file stays valid for both until the app is changed.
- The app's raw reassembler caps a frame at 64 bytes (`wmrMaxFrame`) while
  the spec value is 80. The vectors use 46-byte frames only.
