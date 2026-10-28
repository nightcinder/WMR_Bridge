# BLE UUID migration and pairing rollout (0.3)

The `FF10`/`FF11`… UUIDs are experimental. They live in the Bluetooth SIG's
16-bit range (`0000FFxx-0000-1000-8000-00805F9B34FB`) but were never assigned to
this project, so any other device may use them. 0.3 replaces them with a
project-owned 128-bit set and adds LE Secure Connections (LESC) with bonding.
This page is the migration path: what changes, in which order, and what breaks
when.

Security posture and the threat model are in [SECURITY.md](SECURITY.md); the
wire layouts do not change (see [PROTOCOL.md](PROTOCOL.md)).

## Target UUID set

All values are UUIDv5 (SHA-1, RFC 4122) in the standard URL namespace, derived
from names under the repository URL. Recompute them with:

```bash
python3 - <<'EOF'
import uuid
ns = uuid.NAMESPACE_URL
for key in ("service", "stream", "decoded", "batched", "echo", "config", "version", "control"):
    print(key, uuid.uuid5(ns, f"https://github.com/nightcinder/WMR_Bridge/ble/{key}"))
EOF
```

| Role | 0.2 UUID (retired) | 0.3 UUID |
| --- | --- | --- |
| Primary service | `0000FF10-0000-1000-8000-00805F9B34FB` | `d4535128-a839-5699-87d1-99575c0d8bcb` |
| Raw stream / status (FF11) | `0000FF11-…` | `ddf00b46-5f89-5dd8-8bf5-6e28e4e63805` |
| Decoded stream (FF12) | `0000FF12-…` | `193ce67d-04fd-5de8-a3b9-323c8c673ac4` |
| Batched decoded stream (FF14) | `0000FF14-…` | `c156b923-bd59-586d-86af-8754f7e6669d` |
| Echo (FF15) | `0000FF15-…` | `dc5a34f0-53dd-5a08-92cc-ff185ebe0938` |
| Controller config block (FF13) | `0000FF13-…` | `94d15fad-aac1-5f04-96fd-525f4cb1d839` |
| Firmware version (FF16) | `0000FF16-…` | `1520daa1-40aa-5186-a200-029724a82c12` |
| Control / status v2 (FF17) | `0000FF17-…` | `911528d6-de4a-5dfa-9af6-15128a21487f` |

These values are fixed once this document is merged. If the server-side name
ever has to change, add a new UUID and a new revision of this table; do not
re-derive existing ones.

The CCC descriptor keeps the assigned UUID `00002902-0000-1000-8000-00805f9b34fb`
in both sets.

## What breaks, and when

The cutover is staged so that no released app is ever locked out of a released
firmware image it can flash.

| Phase | Change | Old app + new firmware | New app + old firmware |
| --- | --- | --- | --- |
| **A — app first** | `wmr-companion` learns the new UUIDs, scans for both services (or unfiltered by name), prefers the 128-bit service, falls back to FF10. Adds the pairing prompt. | Not applicable (app has not changed) | **Works.** The app falls back to FF10. |
| **B — dual-service firmware** | Firmware exposes the new 128-bit service and FF10 in the same database, both with the same encrypted permissions. Advertising still carries `0xFF10` and the name so existing scanners find it. | **Works** (FF10 is still served, now behind a bond). | **Works** via FF10. |
| **C — FF10 removed** | FF10 disappears; advertising moves the name to the scan response and carries the 128-bit UUID. Companion removes the fallback. | **Breaks** — the old app can no longer find or use the bridge. | **Breaks** — a 0.3 app has no FF10 to fall back to. |

Phase C is a hard, one-way compatibility break. It needs an owner decision on
old bridges and an app release that has been in the field for at least one
release cycle. **Do not ship Phase C in the same release as Phase B, and do not
start it before the owner accepts the break.** Until then the 0.2 UUIDS remain
in the firmware, marked experimental, and this document plus
[COMPATIBILITY.md](COMPATIBILITY.md) record the cutover.

This repository currently implements the security half of 0.3 (LESC + bonding
on the existing FF10 service). Phases A–C are follow-ups: A belongs to
`wmr-companion`, B and C to a later firmware release.

## Pairing rollout (already in 0.3.0 firmware)

0.3 firmware refuses legacy pairing and requires a bonded LESC link before any
characteristic except the device name and FF16 is served:

- A fresh phone connects, the bridge sends a Security Request, Android shows its
  pairing dialog, and the phone bonds. The first access after bonding is served.
- A central that does not pair, pairs without storing a bond, or fails pairing is
  disconnected after `WMR_CFG_BLE_PAIR_TIMEOUT_MS` (30 s) or immediately on a
  failed pairing result. It cannot sit on the link and block the intended phone.
- A second central is disconnected as soon as it connects; there is still one
  phone at a time.
- A bonded phone reconnecting re-encrypts with its stored keys and is served
  without a dialog. If either side lost the bond, the link fails to encrypt, the
  bridge disconnects, and the phone pairs again on the next connection.

Apps need no UUID change for this step. A client that only reads FF11 must handle
the initial `Insufficient Encryption` (ATT `0x0F`) / `Insufficient
Authentication` (ATT `0x05`) response by letting the OS finish pairing and then
retrying the read or CCC write. Most Android stacks do this automatically when
the characteristic permission requires encryption; the companion should still
surface a pairing failure instead of retrying forever.

### Clearing bonds

- `P` on the serial console deletes all stored phone bonds (LE device DB). It is
  refused while a phone is connected. Use it when a phone has a stale bond and
  the bridge cannot be re-paired.
- `k`/`K`/`b` remain the controller (Classic) bond resets; they do not touch the
  phone bond.
- Deleting a phone's bond on the phone side (`Forget`/`Unpair` in Android) is
  enough; the bridge keeps its entry but the failed re-encryption makes it
  disconnect instead of serving the link, and the next connection pairs again.
