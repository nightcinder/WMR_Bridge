# Reconnect and queue-reset behavior

This documents what the firmware does today (unchanged by the refactor).

## Controller (Classic HID) side

- Incoming connections are the normal path. A controller that wakes or is
  power-cycled connects to the ESP32 by itself. `HID_SUBEVENT_INCOMING_CONNECTION`
  is handled in `wmr_controller.c`: the address is matched against LEFT/RIGHT,
  the HID channel ID (cid) is recorded for that hand before the connection is
  accepted, then `hid_host_accept_connection` is called. Removing this path
  stops reconnects from working.
- Unknown addresses are not accepted blindly. While a hand has no address, the
  connection is held and accepted only if the device's name matches that hand
  ([DISCOVERY.md](DISCOVERY.md)). Otherwise it is declined. A second, different cid
  for an already tracked hand is refused.
- Discovery (automatic after boot when a hand has no address, or `d`) finds
  controllers by name and connects them outwards. See [DISCOVERY.md](DISCOVERY.md).
- Outgoing connections (`l`, `r`) are for recovery. They need a known address.
  FF17 CONNECT (`0x02 hand`) is the same request. If that hand already has a HID
  channel open or opening, it returns OK and does nothing. It does not return
  BUSY or start a second connection. If the previous disconnect is still closing,
  it returns BUSY, because there is no open connection for OK to describe.
- Only one connection attempt (outgoing or accepted) may be in flight
  (`connect_pending`). It ends when the HID descriptor arrives, the open fails,
  or the connection closes before completing.
- Disconnect detection is the Classic link supervision timeout. The bridge writes
  `WMR_CFG_CLASSIC_SUPERVISION_TIMEOUT` (default `0x1F40` = 5 s) to each ACL for
  a known hand. BTstack's default is `0x7D00` = 20 s, which is how long a hand
  that is switched off keeps the connected bit set and the LED lit. The HCI write
  only works while the local controller is the master. At a controller-initiated
  reconnect the bridge starts as slave, so the write is deferred at connection
  complete and sent again from `HCI_EVENT_ROLE_CHANGE` when the link switches to
  master. The command-complete status is logged as `applied` or `not applied`.
  A link that stays slave keeps the timeout the controller (the master) chose,
  and the log says the write was not applied.
- The reconnect itself is controller-initiated. When the supervision timeout drops
  the ACL, the bridge does not page the controller automatically; that would undo
  an explicit disconnect. The controller pages the bridge when it boots or wakes,
  and the bridge accepts and opens HID as soon as that ACL appears. In the 0.3.2
  session (`full session` log, LEFT, uptime 2367.5 to 2379.8 s) the gap from
  `link closed` to `link up` was 12.2 s, and from `link up` to `HID channel open`
  0.21 s. The 12.2 s is the controller's own page/boot interval, not a bridge
  timer or backoff. FF17 CONNECT pages it immediately once the hand has no cid.

Measured targets after this change:

| Measurement | Target |
| --- | --- |
| Last controller packet to `link closed: connection timeout` (`reason=0x08`) | ≤ 6 s (5 s supervision + event latency) when the bridge is master and the write is applied; otherwise the peer master's timeout |
| `link up` to `HID channel open` on a reconnect | ≤ 1 s (0.21 s measured) |
| `link closed` to `link up` | controller-driven; 12.2 s measured, unchanged by firmware |
| FF17 CONNECT while the same hand is open/opening | result byte `0x00` (OK), log says `connect is a no-op` |
| FF17 CONNECT while the same hand is still closing | result byte `0x01` (busy), log says `is still closing` |

Bench step: connect both hands and stream FF14. Note the LEFT `rx` counter and
timestamp from `s`, then switch LEFT off. Check that the log says `link
supervision timeout applied` for LEFT, then expect `LEFT link closed: connection
timeout` and FF17 LEFT flags `0x00` within about 6 s of the last report. If it
says `not applied` (LEFT stayed slave), the peer's timeout still governs and the
6 s target does not apply. Switch LEFT on and record the `link closed` to
`link up` to `HID channel open` timestamps. Finally, while LEFT is open or
opening, write FF17 `02 00` and confirm the result byte is `0x00` and the console
log says `connect is a no-op`.

- State per hand (cid, connected, descriptor, report count) is cleared when its
  connection closes and when a new connection starts. The status `reports`
  value therefore counts since the current connection.
- Bridge counters and sequence numbers are not reset when a controller
  reconnects (see [COUNTERS.md](COUNTERS.md)). During the outage the other hand
  keeps flowing.
- Bond reset (`k` / `K`) deletes only the ESP32's stored link key for that
  controller. It is manual, never automatic, and refused while the controller is
  connected or a connection is pending. The controller's own bond must be
  cleared on the controller (pairing mode). Afterwards press `l`/`r`.

## Phone (BLE) side

- Advertising starts when the stack reaches the working state and is restarted
  after every phone disconnect or failed connection attempt.
- A new LE connection starts unsubscribed: the CCC is not persisted. Frames
  arriving then are counted `offline`.
- The phone must write the CCC again after every reconnect
  ([ANDROID.md](ANDROID.md)).

## When the queue is cleared

The queue holds frames waiting for transmission. It is emptied (every discarded
frame counted in `purged` for its hand, and the partly sent frame offset reset)
when:

1. a phone connects (LE Connection Complete, success);
2. the phone's subscription changes (notifications enabled, or disabled).

The point is a fresh live stream: the phone never receives a backlog of old
motion data. It is not cleared:

- when the phone disconnects (frames still queued stay until the next connect,
  where they are purged; while unsubscribed, new frames are not queued at all,
  they are counted `offline`);
- when a controller disconnects or reconnects;
- on a notification send error (the frame stays at the head and is retried).

A write that sets the CCC to the value it already has does not clear the queue.

## Sending

Frames are sent in arrival order, both hands interleaved, one frame's
fragments at a time. A send happens only in response to
`ATT_EVENT_CAN_SEND_NOW`, requested through
`att_server_request_can_send_now_event` (at most one request outstanding). A
10 ms retry timer re-requests whenever frames are waiting and no request is
outstanding. This is what retries after a send error (or an MTU too small to
send), since those paths return without requesting again. It does not recover a
request that the stack never answers. The `ATT_EVENT_CAN_SEND_NOW` payload is
not parsed. Do not reintroduce an assumed field layout (an earlier layout
assumption blocked all notifications).

If the queue is full, the newest frame is dropped and counted (`drop`).
