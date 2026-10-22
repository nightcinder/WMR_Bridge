# Android setup

Requirements: BLE-capable Android phone, a BLE client app that can do the
steps below (nRF Connect for a manual check, or your own app).

## Requested versus negotiated MTU

`BluetoothGatt.requestMtu(n)` only **requests** an ATT MTU. The ESP32 does not
request one. The MTU actually used is the **negotiated** value, delivered
asynchronously to `onMtuChanged(gatt, mtu, status)`; it is the smaller of
what the phone asks for and what the ESP32 allows, and it can be lower than
requested. Never assume the requested value.

- Android allows requests up to 517. The companion app requests 517; FF14 batching needs at least 61 and carries up to 9 records per notification at 517 (4 at 247).
- Until negotiation completes the MTU is 23 and each 46-byte frame is sent
  as 6 notifications; this works but costs throughput.
- The bridge reads the negotiated MTU itself on every send, so it adapts. It
  reports it as `mtu=` in the periodic statistics line.
- Whatever the MTU, a notification is capped at 244 bytes
  (`WMR_CFG_MAX_NOTIFICATION`); with MTU >= 61 a whole frame is one 58-byte notification.
- Log the **negotiated** value on the phone and compare with `mtu=` on the
  ESP32 console.

## Connection sequence

1. Scan for the name `WMR Bridge` or service `0xFF10`; `connectGatt(...)`.
2. After `onConnectionStateChange(... STATE_CONNECTED)`, call
   `gatt.requestMtu(517)` and wait for `onMtuChanged`; record the
   negotiated MTU.
3. Call `gatt.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)`.
   This asks for a short connection interval. It is a request, not a
   guarantee, and it raises phone battery use. Sustained forwarding in testing used a large negotiated MTU
   **and** High priority together.
4. `gatt.discoverServices()`, find service `0000FF10-...` / characteristic
   `0000FF11-...`.
5. `gatt.setCharacteristicNotification(ch, true)` **and** write
   `ENABLE_NOTIFICATION_VALUE` (`0x0001`) to the descriptor
   `00002902-0000-1000-8000-00805f9b34fb`. Both are required; the first only
   changes local routing.
6. Receive fragments in `onCharacteristicChanged` and reassemble per
   [PROTOCOL.md](PROTOCOL.md). Do not do heavy work on that callback thread.

Serialize GATT operations: Android allows only one outstanding request, so
start the next step from the previous step's callback.

## After any reconnect: subscribe again

The bridge forgets the subscription whenever the phone disconnects, and
treats every new LE connection as unsubscribed (the CCC is not persisted).
**After every reconnect repeat steps 2-5**, in particular the descriptor
write. Also request the MTU and High priority again; neither is remembered.
Until the descriptor write arrives, the bridge counts controller frames as
`offline` and sends nothing.

On subscribe the bridge discards any stale backlog, so the first frame you
receive is current, not old (see [RECONNECT_AND_QUEUE.md](RECONNECT_AND_QUEUE.md)).

## Android pitfalls

- A cached GATT database can be stale after reflashing; call
  `BluetoothGatt.refresh()` (hidden API) or toggle Bluetooth.
- The bridge serves a single phone: once one connection owns the subscription,
  CCC writes from a second connection are rejected.
- Call `close()` on the `BluetoothGatt` after disconnect, or Android may keep
  the link slot.
- The 16-byte status value (read of FF11) is for diagnostics; do not poll it
  at stream rate.

## Not yet provided

A sequence-checking Android receiver (detect gaps, count incomplete frames,
log negotiated MTU) is planned as separate work. Until it exists, lossless
end-to-end delivery has **not** been demonstrated.
