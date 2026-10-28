# Counters

All counters are `uint32_t`, start at 0 at boot, and are never reset by a
reconnect. They wrap after 2^32 events. They live in `wmr_stats_t`
(`src/wmr_stats.h`) and are owned by the transport.

## Periodic line (every 5 s) and the `s` command

```
STREAM phone=1 adv=off sub=1 stream=decoded mtu=247 conn=[11.25ms lat=0 to=4000ms] q=0/256 high=3 notify=1234 errors=0
  FF14 batches_tx=0 records_in_batches=0 mean=n/a max=0 batch_full_flushes=0
  HID reports=1200 errors=0 decode_errors=0
  FF15 echo accepted=0 sent=0 pending=0 busy=0 bad=0 unsubscribed=0 cancelled=0
  LEFT  rx=600 tx=600 drop=0 offline=0 purged=0 mtu_skip=0 dec_err=0 decimated=0 rate_rx=60.0/s rate_tx=60.0/s
  RIGHT rx=600 tx=600 drop=0 offline=0 purged=0 mtu_skip=0 dec_err=0 decimated=0 rate_rx=60.0/s rate_tx=60.0/s
```

(Values above are illustrative of the format, not measurements.) The `s`
command prints the same counters without the rate columns (and `last5s=-`), plus per-controller
connection state (`cid`, `connected`, `descriptor`, `reports`). Before this change `s` also
printed rates and moved their baselines, which contradicted this paragraph; it no longer does.

| Field | Meaning | Limitation |
| --- | --- | --- |
| `phone` | 1 if a BLE phone is connected | |
| `sub` | 1 if the phone enabled FF11, FF12 or FF14 notifications | |
| `stream` | `none`, `raw` (FF11), `decoded` (FF12) or `batched` (FF14): the active stream. Only one can be active | |
| `mtu` | Negotiated ATT MTU of the phone link (0 = no phone, 23 = default) | The *negotiated* value, not what the phone requested |
| `conn=[...]` | Connection interval, slave latency and supervision timeout currently in use (`n/a` with no phone). Set at connect and updated when the phone grants a connection-parameter update | The phone decides. `(update requested, not granted yet)` is appended while the bridge's request (7.5-15 ms, latency 0, 4 s timeout) has had no answer; a phone that refuses or ignores it leaves that text on, and the interval stays at the phone's choice |
| `q=n/N` | Frames queued now / queue capacity | Counts frames, both hands together; a partly sent frame counts as queued |
| `high` | Peak queue depth since boot | Never decreases |
| `notify` | BLE notifications accepted by `att_server_notify` | FF11: fragments, not frames. FF12: one per record. FF14: one per **batch** (so it equals `batches_tx` while FF14 streams) |
| `errors` | `att_server_notify` calls that returned an error (stream and FF15 echo) | Each failed attempt counts; the frame or echo answer is kept and retried, not lost |
| `HID reports` | HID gamepad input reports accepted by the HID service | Coalesced and deduplicated; not the same as controller reports (`rx`) |
| `HID errors` | HID input report sends that failed | The report is marked dirty and retried on the next pump tick |
| `HID decode_errors` | Controller reports the HID path could not decode | Should stay 0; the FF10 stream has its own `dec_err` |
| `rx` | Reports received from that controller | Counts every report, including those never forwarded |
| `tx` | FF11: frames whose last fragment was accepted by `att_server_notify`. FF12: records accepted by `att_server_notify`. FF14: valid records inside batches accepted by `att_server_notify` (records, not notifications) | **Handed to the BLE stack, not confirmed received by the phone** |
| `drop` | Reports discarded because the queue was full, or the length was 0 or over `WMR_CFG_MAX_FRAME` (80) | Drop-newest: queued frames are kept |
| `offline` | Reports received while no FF11/FF12/FF14 stream was active | Intentional, not an error. Since ONY-441 these reports may still have been sent through the HID gamepad |
| `purged` | Frames that were queued, then discarded by a queue reset | See [RECONNECT_AND_QUEUE.md](RECONNECT_AND_QUEUE.md) |
| `mtu_skip` | FF12 and FF14: reports discarded because the ATT MTU was below the stream's minimum (59 for FF12, 61 for FF14) | Zero on FF11. Intentional, not a queue problem |
| `dec_err` | FF12 and FF14: queued frames that were not a valid 46-byte input report, discarded | Should stay 0 |
| `decimated` | FF12 only: reports skipped on purpose by the diagnostic build option `WMR_CFG_FF12_DECIMATE` | Always 0 in the default build (`N=1`). Intentional, not a loss |
| `batches_tx` | FF14 only, both hands together: batches accepted by `att_server_notify` | Handed to the BLE stack, not confirmed received. Cumulative since boot |
| `records_in_batches` | FF14 only: valid records inside those batches | Equals the growth of `tx` summed over both hands while only FF14 streamed |
| `mean` | `records_in_batches / batches_tx`, two decimals (`n/a` before the first batch) | Depends on the link, the MTU and the hold time (one connection interval); at 15 ms and about 320 reports/s the expectation is a little under 5, not measured |
| `max` | Most records in one batch since boot | At most `(MTU - 3 - 2) / 56`, never more than 9 |
| `batch_full_flushes` | FF14 only: batches sent because a full notification's worth was queued | The remaining `batches_tx - batch_full_flushes` were sent because the oldest record had waited one connection interval |
| FF15 `accepted` | Echo requests taken (valid, FF15 subscribed, none waiting) | `accepted = sent + cancelled + pending` |
| FF15 `sent` | Echo answers accepted by `att_server_notify` | Not counted in `notify` |
| FF15 `pending` | 1 while an answer waits to be sent | At most one |
| FF15 `busy`, `bad`, `unsubscribed` | Requests ignored: another answer was waiting; wrong length or version; FF15 notifications off | Write without response, so the phone gets no error; it only misses the answer |
| FF15 `cancelled` | Waiting answers discarded by an FF15 unsubscribe or a phone connect/disconnect | |
| `rate_rx`, `rate_tx` | (change in `rx` / `tx` since the previous periodic line) / 5 s | Uses the nominal 5 s interval, not a measured one; `s` does not print or disturb them |

The per-hand `sequence` counter (the number placed on the wire) always equals `rx`.

## Accounting identity

Every received report ends up in exactly one place, so at any instant:

```
sum over hands of  rx  =  sum of (tx + drop + offline + purged + mtu_skip + dec_err + decimated)  +  q
```

(`q` is the current queue depth.) This holds per hand if you could see the
per-hand queue depth.

**On FF14 the identity is unchanged, but `tx` counts records, not notifications.** One
notification (one batch) carries up to 9 records, so `tx` grows much faster than `notify`:
`notify` grows by `batches_tx`, and `tx` (both hands) grows by `records_in_batches`. Use
`records_in_batches / batches_tx` for the mean batch size. `dec_err` frames take no slot in a
batch, and a batch that fails to send leaves its records queued (they are counted when a
retry succeeds, so `errors` can rise without any loss). The check `notify == batches_tx`
holds only while FF14 is the only stream used since boot. `rate_tx` stays a records-per-second
figure. A mismatch in a steady state means a counting bug.
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

## Link diagnostics lines (`src/wmr_linkdiag.c`; run on hardware, user-reported 2026-10-22)

Added to the periodic line and the `s` command to help find where controller reports are lost.
Background and caveats: [DECODING.md](DECODING.md), "Investigation notes: upstream report loss".

```
STREAM phone=1 adv=off sub=1 stream=decoded mtu=517 conn=[15.00ms lat=1 to=5000ms] q=0/256 high=3 notify=1234 errors=0
  vhci_drop=0 log_errors=0
  LEFT  rx=600 tx=600 ... rate_rx=147.0/s rate_tx=147.0/s
  LEFT link: rssi=0 fcc=0 lq=255 mode=active rate_rx=147.0/s dev_missing=312 last5s=26.5% cum=17.7% period=4.99ms discont=0
  RIGHT rx=...
  RIGHT link: rssi=0 fcc=0 lq=255 mode=active rate_rx=165.0/s dev_missing=21 last5s=3.4% cum=3.4% period=4.99ms discont=0
```

(Illustrative format only, not measurements.)

| Field | Meaning | Limitation |
| --- | --- | --- |
| `vhci_drop` | HCI packets the ESP32's VHCI transport dropped because its receive buffer was full | Depends on BTstack's log wording; 0 if `CONFIG_BTSTACK_HCI_DUMP_STDOUT` is set |
| `log_errors` | All error-level BTstack log messages (includes `vhci_drop`) | |
| `rssi`, `fcc`, `lq` | Read RSSI, Read Failed Contact Counter, Read Link Quality for that Classic link, polled every 5 s; `n/a(0xNN)` = the controller returned HCI status `0xNN` or nothing has been read yet | Semantics for BR/EDR are not verified (see DECODING.md); `n/a (no ACL link)` while the controller is not connected |
| `mode` | `active`, `sniff`, `hold` or `park` for that link, from Mode Change events | Assumed `active` when the link is created; each change is also logged once |
| `dev_missing` | Estimated controller reports that never reached the ESP32, from the tick deltas | An estimate (see DECODING.md for the definition and its limits). Resets on each Classic reconnect; not part of the accounting identity above, because these reports never became `rx` |
| `cum=x.y%` | `dev_missing / (reports seen + dev_missing)` since the link was created | Hides a change over time: it only creeps while the current loss differs from the running average |
| `last5s=x.y%` | The same share over the last statistics interval only: growth of `dev_missing` over (that growth + growth of `rx`) | `n/a` right after a reconnect (the missing count restarted) or when nothing was expected; `-` in the `s` output. Printed with `rate_rx` (a copy of the rate on the line above) so a drift is visible without arithmetic |
| `period` | Estimated controller report period (about 4.99 ms for 200 Hz) | 0 until 16 reports have arrived |
| `discont` | Tick jumps of more than 5 s, not counted as missing | |

**HCI traffic added by the polling:** 3 commands per connected link every 5 s, so 1.2 commands per
second (and 1.2 Command Complete events per second) with both controllers connected, sent one at a
time at least 100 ms apart.

## Machine-readable line (`LINKLOG`) and one-off log lines (run on hardware, user-reported 2026-10-22)

Logging only; none of these adds HCI traffic. The HCI budget above is unchanged.

```
LINKLOG_FIELDS,t_ms,hand,rate_rx,dev_missing_delta,dev_missing_cum,last5s_pct,cum_pct,rssi,lq,fcc,stream,mtu,conn_interval_ms,decimated,batches_tx,records_in_batches,batch_mean,batch_max,batch_full_flushes
LINKLOG,12345,LEFT,157.8,1124,9000,21.2,17.7,-28,227,0,batched,517,15.00,0,980,4710,4.81,9,12
```

(Illustrative format only, not measurements.) The header is printed once, before the first
line; then one `LINKLOG,...` line per hand per statistics interval (5 s), after the `link:` line
with the same numbers. Filter on the prefix `LINKLOG,`.

| Field | Meaning |
| --- | --- |
| `t_ms` | Milliseconds since boot when the line was printed |
| `hand` | `LEFT` or `RIGHT` |
| `rate_rx` | Reports per second received from that controller over the last interval (same as `rate_rx` on the `rx` line) |
| `dev_missing_delta` | Growth of `dev_missing` over the interval; empty after a reconnect restarted it |
| `dev_missing_cum` | `dev_missing` since the link was created |
| `last5s_pct`, `cum_pct` | Missing share over the interval and since the link was created, in percent, without the `%` sign |
| `rssi`, `lq`, `fcc` | The last polled link-health values; empty if the controller is not connected or refused the command |
| `stream`, `mtu` | `none`, `raw`, `decoded` or `batched`; the negotiated ATT MTU (0 = no phone) |
| `conn_interval_ms` | The BLE connection interval in use, empty if not yet known |
| `decimated` | Cumulative reports skipped by `WMR_CFG_FF12_DECIMATE` (0 when off) |
| `batches_tx`, `records_in_batches` | FF14 batches accepted by the BLE stack and the records inside them, cumulative since boot (0 unless FF14 was used). **Appended at the end of the line**: the first fourteen fields, up to `decimated`, are unchanged, so an older script that reads by position still works. Both hands' lines carry the same value (these are not per hand) |
| `batch_mean` | `records_in_batches / batches_tx` with two decimals; empty before the first batch |
| `batch_max`, `batch_full_flushes` | Most records in one batch so far; batches sent because a full one was queued |

Unknown values are empty fields, never `n/a`, so a CSV reader sees a missing value.

Other log lines added in the same change, each printed at most once per event:

- `LEFT queue full: first drop of this episode, stream=raw mtu=23 q=256/256 errors=0`: the first
  queue-full drop since the queue last drained to empty. Later drops in the same episode are
  only counted in `drop`.
- `HCI LE buffers: ACL packet length 251 bytes, 12 packets`: from the controller's LE Read
  Buffer Size reply, read once during stack start-up (v1 or v2 command). Not polled.
- `BLE data length changed: tx 251 octets / 2120 us, rx 251 octets / 2120 us`: the LE Data
  Length Change event for the phone link. If no such event arrives, about 0.5 s after connect
  the firmware prints `BLE data length: no change event seen since connect`.
