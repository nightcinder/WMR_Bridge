# Counters

All counters are `uint32_t`, start at 0 at boot, and are never reset by a
reconnect. They wrap after 2^32 events. They live in `wmr_stats_t`
(`src/wmr_stats.h`) and are owned by the transport.

## Periodic line (every 5 s) and the `s` command

```
STREAM phone=1 sub=1 mtu=247 conn=[11.25ms lat=0 to=4000ms] q=0/256 high=3 notify=1234 errors=0
  LEFT  rx=600 tx=600 drop=0 offline=0 purged=0 rate_rx=60.0/s rate_tx=60.0/s
  RIGHT rx=600 tx=600 drop=0 offline=0 purged=0 rate_rx=60.0/s rate_tx=60.0/s
```

(Values above are illustrative of the format, not measurements.) The `s`
command prints the same counters without the rate columns, plus per-controller
connection state (`cid`, `connected`, `descriptor`, `reports`).

| Field | Meaning | Limitation |
| --- | --- | --- |
| `phone` | 1 if a BLE phone is connected | |
| `sub` | 1 if the phone enabled notifications (CCC = 1) | |
| `mtu` | Negotiated ATT MTU of the phone link (0 = no phone, 23 = default) | The *negotiated* value, not what the phone requested |
| `conn=[...]` | Connection interval, slave latency and supervision timeout currently in use (`n/a` with no phone). Set at connect and updated when the phone grants a connection-parameter update | The phone decides. `(update requested, not granted yet)` is appended while the bridge's request (7.5-15 ms, latency 0, 4 s timeout) has had no answer; a phone that refuses or ignores it leaves that text on, and the interval stays at the phone's choice |
| `q=n/N` | Frames queued now / queue capacity | Counts frames, both hands together; a partly sent frame counts as queued |
| `high` | Peak queue depth since boot | Never decreases |
| `notify` | BLE notifications accepted by `att_server_notify` | Fragments, not frames |
| `errors` | `att_server_notify` calls that returned an error | Each failed attempt counts; the frame is kept and retried, not lost |
| `rx` | Reports received from that controller | Counts every report, including those never forwarded |
| `tx` | Frames whose last fragment was accepted by `att_server_notify` | **Handed to the BLE stack, not confirmed received by the phone** |
| `drop` | Reports discarded because the queue was full, or the length was 0 or over `WMR_CFG_MAX_FRAME` (64) | Drop-newest: queued frames are kept |
| `offline` | Reports received while no phone was connected and subscribed | Intentional, not an error |
| `purged` | Frames that were queued, then discarded by a queue reset | See [RECONNECT_AND_QUEUE.md](RECONNECT_AND_QUEUE.md) |
| `rate_rx`, `rate_tx` | (change in `rx` / `tx` since the previous periodic line) / 5 s | Uses the nominal 5 s interval, not a measured one; `s` does not print or disturb them |

The per-hand `sequence` counter (the number placed on the wire) always equals `rx`.

## Accounting identity

Every received report ends up in exactly one place, so at any instant:

```
sum over hands of  rx  =  sum of (tx + drop + offline + purged)  +  q
```

(`q` is the current queue depth.) This holds per hand if you could see the
per-hand queue depth. A mismatch in a steady state means a counting bug.
`tx` growing slower than `rx` with `q` rising towards 256 and then `drop`
rising means the phone link cannot keep up.

## What the counters cannot tell you

- Frames lost **after** `att_server_notify` accepted them (radio loss beyond
  link-layer retransmission, phone app too slow, Android dropping
  callbacks) are invisible to the bridge. Only phone-side sequence checking
  can find them. That receiver does not exist yet.
- `rx` is the rate the controller delivers, measured at the ESP32 after Classic
  Bluetooth; frames lost on the controller-to-ESP32 link are not detectable
  (the controller supplies no frame counter that this firmware inspects).
- `high` only shows the worst depth since boot, not when it happened.
- Counters are cumulative; judge health from **changes**: `drop` should stop
  increasing, `q` should not grow persistently, and `rate_tx` should track `rate_rx`.
