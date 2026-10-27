# ESP32 Bluetooth contention: remaining levers and cap-out assessment

ONY-428 investigation. Source of truth for the numbers: [`docs/PREDICTION.md`](PREDICTION.md) §1–§2.5,
[`docs/STATUS.md`](STATUS.md) §5, [`docs/DECODING.md`](DECODING.md) "Investigation notes", and the run sheets
[`docs/runs/sheets/single-esp32-levers.md`](runs/sheets/single-esp32-levers.md),
[`docs/runs/sheets/contention-2.md`](runs/sheets/contention-2.md),
[`docs/runs/sheets/right-asymmetry.md`](runs/sheets/right-asymmetry.md),
[`docs/runs/sheets/multi-bridge.md`](runs/sheets/multi-bridge.md),
[`docs/runs/sheets/pi-bridge.md`](runs/sheets/pi-bridge.md).

## Answer

**Not capped out on investigation, but capped out on single-ESP32 tuning for a sub-15 % dual-hand result.**

- The single-ESP32 search still has run-ready levers (V1 stagger, right-asymmetry phases 1–2,
  a current-build DLE capture, a 30 ms interval request, a two-interval FF14 hold, a controlled
  antenna/position sweep, and a Classic TX power check). None has hardware evidence yet.
- The tested set already bounds the possible gain: the no-phone baseline is 9–11 % per hand
  and RIGHT alone is ~10.8 %. Even if every remaining BLE/coexistence lever worked perfectly,
  a single ESP32 cannot reach a 3–5 % VR bar by contention tuning alone. With the phone link
  active (FF14, MTU 517) both hands sit at 18–25 %, so sub-15 % on *both* hands at once is not
  supported by the phone-active baselines; the 9–11 % no-phone figure is already below 15 %.
- RIGHT's ~10.8 % is independent of the second controller and is not removed by multi-bridge
  or by Pi/Wi-Fi; it is the product blocker and may be controller hardware, RF geometry or
  the firmware hand path.
- The only architectures that can remove the pair-contention component are one bridge per hand
  (multi-bridge) and the Pi/Wi-Fi host bridge. Neither has run.

## Baselines a single ESP32 cannot beat

| Condition | LEFT | RIGHT | Source |
| --- | --- | --- | --- |
| Both controllers + phone, FF14, MTU 517, 15 ms interval | 18–25 % | 18–25 % | PREDICTION §1 |
| No phone subscribed (`stream=none`) | 9–11 % loss (rate 8–18 % below 200/s) | 9–11 % loss (rate 8–18 % below 200/s) | PREDICTION §1; DECODING investigation notes |
| One controller alone | ~0.5 % | ~10.8 % | STATUS §4 |
| Phone idle (or stream held at MTU 23), near | ~10 % | ~10 % | DECODING bench runs |
| FF12 streaming at MTU 517, near | ~20 % | ~20 % | DECODING bench runs |

The no-phone baseline is the important qualifier: turning the phone link off, or moving it to
Wi-Fi on the Pi bridge, cannot by itself make a Classic link lossless.

## Lever status

### Tested: no measurable effect shown

| Lever | Result |
| --- | --- |
| Classic role (V3, `WMR_CFG_CLASSIC_FORCE_MASTER`) | No-op; both links already establish as master |
| Disable Classic sniff (V2, `WMR_CFG_CLASSIC_NO_SNIFF=1`) | 17.55 % / 23.19 % in one 120 s run, inside baseline spread |
| `CONFIG_BTDM_CTRL_AUTO_LATENCY=y` | A/B/A vs baseline; no effect shown |
| `CONFIG_BTDM_CTRL_MODEM_SLEEP=n` | Same A/B/A session; no effect shown |
| FF12 decimation N=2/N=4 | LEFT stayed ~18–20 %; RIGHT lower (~14–15 % / ~12–13 %) but runs not interleaved |
| FF14 batching (~48 notif/s vs ~300/s) | No clear gain; sat inside FF12 run-to-run spread |
| Phone present but not streaming | Loss rises with phone present and more when streaming, but no-phone baseline already 9–11 % |

### Run-ready: not yet run

| Lever | Prerequisite |
| --- | --- |
| V1 staggered connect (`WMR_CFG_STAGGER_FIRST_HAND=1/2`) | Two V1 images + A/B/A block at one fixed position |
| RIGHT asymmetry, phases 1–2 | No reflash; one controller powered at a time |
| DLE capture on current build | Capture the `tx 251 octets / 2120 us` line again on this image |
| Longer BLE interval request (30 ms) | B1 image; phone must grant and record the granted value |
| FF14 two-interval hold (`WMR_CFG_BATCH_HOLD_INTERVALS=2`) | Knob added in ONY-176; default 1 unchanged |
| Physical separation / antenna geometry | Right-asymmetry phases 1–2, no reflash |
| Classic TX power (raise max above default P3) | Verify via `esp_bredr_tx_power_range_get`; ESP-IDF default BR/EDR max is `ESP_PWR_LVL_P3` (range N0..P3) and the enum extends higher; untested |
| FF12 decimation re-run, interleaved | Same protocol at N=1/2/4 |
| Three-state A/B/C at one fixed position | A (streaming) vs B (connected, idle) vs C (disconnected), both passes |

### Ruled out by diagnostics

| Lever | Evidence |
| --- | --- |
| Controller task core/priority and HCI buffers | Archived: `vhci_drop=0`, `log_errors=0`, queue high-water 41/256, `tx` trails `rx` only by queued records |

### Closed by hardware or API

| Lever | Why closed |
| --- | --- |
| 2M BLE PHY | Original ESP32 is BLE 4.2 only; ESP32-S3/C3/C6 lack Classic Bluetooth |
| Classic-vs-BLE priority | `esp_coex_preference_set` arbitrates Wi-Fi vs Bluetooth, not BR/EDR vs BLE; no public API |
| ESP32 controller scheduling | Closed firmware; not modifiable from this repo |
| Classic AFH | Controller-managed; not exposed by this firmware |

## Other levers not yet considered or tested

- Antenna/board geometry and distance: the weak-link hand suffers most at distance; position was not
  measured in the reported A/B/C runs.
- Controller firmware behaviour: may drop reports under radio contention; not observable here.
- Silent VHCI receive drops: `CONFIG_BTSTACK_HCI_DUMP_STDOUT` is not set, so a drop there is silent;
  the diagnostic sink counts them but has not run on hardware.
- Sniff mode: one run only, mode changes not logged; not definitively ruled out.
- Console serial output: ~0.7 % at most; not a candidate for the observed 9–20 % shortfall.

## Architecture paths (the only things that can remove pair contention)

1. **Multi-bridge** (`docs/runs/sheets/multi-bridge.md`): one ESP32 per controller. Expected best case
   LEFT ~0.5 % / RIGHT ~10.8 %. Not run; needs a second Classic-capable ESP32 (an S3 cannot
   substitute). Pass bar: LEFT ≤ 3 % on its own bridge.
2. **Pi / Wi-Fi host bridge** (`docs/runs/sheets/pi-bridge.md`): moves the phone link to 5 GHz
   Wi-Fi WebSocket, removing BLE airtime from the ESP32 radio. Cannot remove the 9–11 % no-phone
   baseline; host-tests only, never run on Pi hardware.

## Recommendation

Run, in this order, before declaring the single-ESP32 search exhausted:

1. V1 staggered connect (cheapest, already coded).
2. RIGHT asymmetry phases 1–2 (no reflash; isolates controller vs position vs hand slot).
3. Current-build DLE capture plus the 30 ms interval and two-interval hold A/B.
4. Controlled antenna/position sweep with the three-state A/B/C protocol, interleaved.
5. Classic TX power: call `esp_bredr_tx_power_range_get`; if a level above P3 is available,
   A/B it against the default.

If those do not move loss below ~15 % on both hands, the result is the baseline, not a missed
switch: proceed to multi-bridge and/or the Pi bridge, and treat RIGHT's ~10.8 % as a
controller-hardware problem rather than contention.
