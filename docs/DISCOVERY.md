# Controller discovery by name (issue #2, closed)

**Status: implemented, merged and run on hardware. The D1-D11 checklist was reported passed by the project owner (user-reported 2026-10-22; no output pasted).**
Checklist in [TESTING.md](TESTING.md) ("Discovery by name"). Reference logic:
`src/wmr_discovery.{h,c}` (pure C, `tests/test_discovery.c`); BTstack side in
`src/wmr_controller.c`.

The bridge no longer ships with Bluetooth addresses. It finds the two controllers by their
Classic Bluetooth names and stores the addresses in NVS, so later boots reconnect without
scanning.

## Names

| Name (case-insensitive, whole name) | Hand | Model |
| --- | --- | --- |
| `Motion controller - Left` | LEFT | WMR |
| `Motion controller - Right` | RIGHT | WMR |
| `PlayStation VR2 Sense Controller (L)` | LEFT | PSVR2 |
| `PlayStation VR2 Sense Controller (R)` | RIGHT | PSVR2 |

Trailing spaces and NUL padding are ignored; anything else must match. The WMR names are the
ones given in issue #2; the PSVR2 names come from the ONY-81 investigation and the
[PSVR2 tracking issue](https://github.com/nightcinder/WMR_Bridge/issues/44). Check D2 in
TESTING.md covers the WMR names (reported passed, no output pasted); the PSVR2 names have
not been seen by this firmware. What the bridge does differently for a PSVR2 hand is in
[PSVR2.md](PSVR2.md).

## Model

The name decides the controller's **model** (`wmr_model_t`: `WMR` or `PSVR2`), not just its
hand. The model is stored with the address and restored on later boots, so a reconnect does
not have to ask for the name again. It selects the HID wake handshake, whether the Microsoft
FF13 config read is sent, and which report IDs are forwarded. A device that matches neither
name is not a controller.

## Where an address comes from

Per hand, in this order:

1. **Compile-time override** (`-DWMR_CFG_LEFT_ADDRESS=\"AA:BB:CC:DD:EE:FF\"`, same for RIGHT;
   undefined by default). Never written to NVS, never forgotten by `F`. An unparsable value is
   reported at boot and ignored. An override is always treated as a **WMR** controller:
   there is no model on the command line, so a PSVR2 Sense controller must be found by
   name instead of being pinned this way.
2. **Stored**: found by discovery (or by name on an incoming connection) and saved in NVS
   through BTstack's TLV store (the same store as the link keys), tag `WMRA`, a 16-byte record
   (`wmr_addr_record_pack`, golden bytes in the test). Byte 14 carries two model bits per
   hand (00 = WMR, 01 = PSVR2); bytes 15 stay reserved. Model bits are written only for a
   valid hand. The version stays 1, so firmware from before PSVR2 support still reads the
   record and treats both hands as WMR; a WMR-only build reads a stored PSVR2 model as WMR
   so the address stays usable.
3. **None**: the hand is unknown until discovery finds it.

The `s` command prints each hand's address, its source and its model.

## Discovery

Starts by itself about 3 s after boot when a hand has no address
(`WMR_CFG_DISCOVERY_AUTOSTART`, `WMR_CFG_DISCOVERY_START_DELAY_MS`), or with console key `d`.
It looks only for hands that have no address; a known hand is never replaced.

1. **Inquiry** for 10.24 s (`WMR_CFG_DISCOVERY_INQUIRY_LEN` = 8 x 1.28 s). A result whose
   name is in the inquiry response is matched at once. Devices without a name are remembered
   (up to 8 per round). The inquiry stops early once every wanted hand is found.
2. **Remote name requests**, one at a time, for the remembered devices, until every wanted
   hand is found.
3. **Store and connect.** New addresses are saved, then each new hand is connected outwards,
   one at a time. A controller in pairing mode pairs on that connection (Secure Simple
   Pairing, auto-confirmed as before).
4. If a hand is still missing, another round runs, up to `WMR_CFG_DISCOVERY_ROUNDS` (6, about
   a minute of inquiry). Every round starts from a clean slate: devices seen earlier are
   looked for again and name requests that failed are repeated, while a hand already matched
   keeps its first controller. When the rounds are exhausted and a hand still has no address,
   discovery waits `WMR_CFG_DISCOVERY_RETRY_MS` (15 s) and starts another burst, up to
   `WMR_CFG_DISCOVERY_RETRIES` (3) extra bursts, so a controller put in pairing mode after
   the first burst is found without rebooting. After that it stops and waits for `d`; the
   bound matters because an inquiry shares the radio with a controller that is already
   streaming. With `WMR_CFG_DISCOVERY_AUTOSTART` = 0 there is no automatic start or retry and
   `d` is the only way. An inquiry that cannot start because a connection is still using the
   radio is retried the same way instead of ending discovery.

While discovery runs, `l`/`r` are refused. A controller that is already connected keeps
streaming, but an inquiry shares the ESP32's Classic radio with it, so expect more missing
reports on that hand during discovery (not measured). Discovery is bounded for that reason.

### Pairing-mode procedure

Assumed, not yet checked on these controllers: a WMR controller answers inquiry only in
pairing mode (hold the Windows button until the LEDs flash). For a fresh flash: power on the
ESP32, put **one or both** controllers in pairing mode within the first minute, and watch for
`Discovery: LEFT is ...` and `descriptor status=0x00`. If pairing mode times out first, put
the controller in pairing mode again and press `d`.

### More than one matching controller in range

The **first** device found with a hand's name is used for that hand; any other device with
the same name in the same discovery is logged (`another device with a controller name ...
ignored`) and not used. This applies across models too: a WMR LEFT found first keeps a
PSVR2 LEFT out. Keep only one controller of each hand in pairing mode during
discovery. If the wrong one was taken: `F`, then discovery again with only the right one in
pairing mode. Asking for confirmation on the console was considered and not done: the bridge
runs headless in normal use.

## Incoming connections

- From a **stored or overridden address**: accepted as before (this is how a controller
  reconnects on its own after waking).
- From an **unknown address** while both hands have addresses: declined.
- From an **unknown address** while a hand has no address: the connection is **held, not
  accepted**, its name is requested, and it is accepted only if the name matches a hand that
  has no address. The address is then stored. Otherwise it is declined. Only one such check
  runs at a time; a second unknown device meanwhile is declined.

## One hand per bridge (`WMR_CFG_BRIDGE_HANDS`)

A build can serve only one controller: `WMR_CFG_BRIDGE_HANDS` is `0` (both, default), `1`
(LEFT only) or `2` (RIGHT only). The excluded hand is ignored during inquiry and remote name
requests, refused by the outgoing connect (`l`/`r` and the discovery connect), declined on an
incoming connection even if its address is still in NVS from an older both-hands build, and
its address is never written back to NVS. Discovery for the excluded hand is not started, so
a single-hand image does not keep inquiring for a hand it will not use.

`WMR_CFG_BLE_NAME` defaults to `WMR Bridge` (unchanged). The single-hand images normally
advertise `WMR Bridge L` / `WMR Bridge R`, keeping the prefix for clients that match on it.
The GATT database, including its device-name value, is unchanged. The build route for the
three images is in
[runs/sheets/two-bridge-hands.md](runs/sheets/two-bridge-hands.md).

## Forgetting addresses (`F`)

`F` deletes the stored addresses of both hands (overrides stay) and starts discovery. It is
refused while a controller is connected or connecting, or while discovery runs: disconnect
with `L` and `R` first. It does **not** delete link keys; that is still the per-hand bond reset
(`k`/`K`), which now needs the hand to have an address.

## Security

A Bluetooth name is not authentication: any device can call itself `Motion controller - Left`
or `PlayStation VR2 Sense Controller (L)`.
Before this change the bridge trusted two compiled-in addresses (since removed; the addresses now live in NVS), which can also be spoofed.
What changes:

- A device that answers inquiry with a controller name while a hand has no address becomes
  that hand, and is paired with automatically (SSP is auto-confirmed for any device). The
  exposure is the discovery window, which runs only while a hand has no address and is bounded.
- Once both hands have addresses, name matching is off: incoming devices with other addresses
  are declined, and discovery has nothing to look for.
- A stored address is never replaced by discovery or by an incoming device; only `F` (local
  serial console) clears it.

See [SECURITY.md](SECURITY.md).

## Not done / open

- Real names, pairing-mode behaviour and timings: not yet checked on hardware.
- Whether a controller in pairing mode also tries to connect inwards (it would then take the
  incoming path above, which handles it).
- The BTstack HID host keeps the incoming L2CAP connection pending while the name is
  requested; how long a controller waits before giving up is not known. A controller that
  gives up is handled (the late name answer is ignored).
