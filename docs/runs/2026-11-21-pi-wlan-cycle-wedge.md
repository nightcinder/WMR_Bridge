# Pi adapter wedge in `wlan-cycle`, and a later loss with no trigger

Two adapter losses occurred on the 2026-11-21 bench. The first loss happened during `wlan-cycle 60`, before the first phone command. The second happened about 21 minutes later, with no identified trigger. Both are HCI transport failures with different kernel signatures. The evidence names no single root cause for either loss. ONY-1313 item 3 remains unaccepted (ONY-1624 report `hardware-bench-20261120`).

## Evidence key

The run used installed `wmr-bridge 0.3.28-rc.3` (`version.txt`). Capture started at 2026-11-21T03:44:42.302865Z (`start.txt`). The Pi boot id was `541b56fe-d6aa-4466-940f-48d252ae3570` (`boot-id.txt`).

Citations use these forms:

- `path:line` is a file in this repository.
- `name:line` is a file in attachment `8cc46ba8`, SHA-256 `b2c7be32f416e5fe903d317ed891b49cbf6d1236181a8ccb5a5c346cfb29f57d`.
- `late-name:line` is a file in attachment `b568c6f2`, SHA-256 `fa57b9c7af36b7d821fdb3142426f832f0b691213a8822379a9e6f23c7fe6345`.
- An 8-character id is a Paperclip comment id.
- `hardware-bench-20261120` is the ONY-1624 hardware bench report.

## Event 1: adapter loss during `wlan-cycle 60`

| Time (UTC) | Event | Evidence |
|---|---|---|
| 03:45:44.580159 | `wlan-cycle 60` T0 | `round1-pi-after.txt:1` |
| 03:45:47.707 | LEFT read error, then LEFT and RIGHT disconnect | `journal-full.txt:17-19` |
| 03:45:48.469758 | first sampled `Powered: no` | `samples.jsonl:59` |
| 03:45:48.612999 | first phone disable request | `round1-events.jsonl:2` |
| 03:46:03.738933 | recovery unit starts | `journal-full.txt:32` |
| 03:46:25.962733 | recovery unit reports `Finished` | `journal-full.txt:65-66` |
| 03:46:27.157582 | daemon finds no `Adapter1` | `journal-full.txt:73` |

The order is the main finding. The cycle began at 03:45:44.580159 (`round1-pi-after.txt:1`). Both controllers disconnected at 03:45:47.707 and 03:45:47.759 (`journal-full.txt:18-19`). The first `Powered: no` sample was at 03:45:48.469758 (`samples.jsonl:59`). The first phone disable request was at 03:45:48.612999 (`round1-events.jsonl:2`). The adapter and both controllers therefore failed before any phone command.

The sample log holds 58 `powered=yes` samples, 36 `powered=no` samples, and 93 samples where `bluetoothctl show` returned a non-zero exit. The first unavailable sample is at `samples.jsonl:95`. The log has 187 samples in all (count of `samples.jsonl`).

The phone-toggle path is a separate trigger. ONY-1621 ran two rounds of two phone toggles and recorded 154 of 154 one-second samples at `powered=yes` (`d9cad85c`). Event 1 started before the first phone disable request, so it is not that trigger.

### Kernel signature

- `Bluetooth: hci0: hardware error 0x00` at monotonic 17191.300077 (`kernel-journal.txt:2`).
- `Opcode 0x0c03 failed: -110` at 17193.444029, 17195.460104 and 17207.428327 (`kernel-journal.txt:3`, `:5`, `:6`). `0x0c03` is `HCI_OP_RESET` ([Linux `hci.h`](https://github.com/torvalds/linux/blob/master/include/net/bluetooth/hci.h)).
- `BCM: failed to write update baudrate (-110)` and `BCM: Reset failed (-110)` (`kernel-journal.txt:10`, `:13`). `-110` is `ETIMEDOUT` ([Linux `errno.h`](https://github.com/torvalds/linux/blob/master/include/uapi/asm-generic/errno.h)).
- After the failed reset the driver reloaded `BCM4345C0` firmware and read `build 0382` (`kernel-journal.txt:15-23`). The adapter still did not enumerate.

The daemon's `Adapter1.Powered=True` write failed with `Authentication Failed` (`journal-full.txt:29-31`). The kernel had already logged the HCI Reset timeout. The failure is at the HCI transport and controller setup layer, below the BlueZ `Adapter1` interface. It is not a pairing or policy failure.

### The ladder reported success with no controller

The recovery ladder ran rfkill unblock, a timed-out `btmgmt` off/on, a rebind of `serial0-0` to `hci_uart_bcm`, a `bluetoothd` restart, and a daemon start (`journal-full.txt:42-46`, `:48-49`, `:62`). The unit then reported `Finished` and its main process exited with status 0 (`journal-full.txt:65-66`, `recovery-status.txt:8-9`). The restarted daemon then logged `BLE GATT disabled: BlueZ adapter hci0 is not available` (`journal-full.txt:73`). A later status check returned `Index list with 0 items` from `btmgmt info` and `No default controller available` from `bluetoothctl show` (`recovery-status.txt:22-23`).

The ladder has no postcondition. Its last action starts the daemon and then calls `exit 0` (`pi/systemd/wmr-bridge-bt-recover.sh:112-116`). The header states that every failure is non-fatal and that the script always exits 0 (`pi/systemd/wmr-bridge-bt-recover.sh:14-16`). The unit reported success, and the next status check returned zero controllers.

## Event 2: later loss with a `-84` signature

At 04:07:41.045758 the kernel began a flood of `Bluetooth: hci0: Frame reassembly failed (-84)` lines (`late-kernel-journal.txt:1`). The last line was at 04:08:59.257791 (`late-kernel-journal.txt:30997`). The journal holds 30,994 `-84` lines and three `-90` lines in 30,997 lines (grep count). The dmesg capture holds 2,057 `-84` lines. They cover monotonic 1215.921954 to 1221.070882, or 5.15 s (`late-dmesg.txt:1`, `:2057`).

The same window has zero `0x0c03`, `-110` or BCM lines (grep count 0). The late journal has 15 lines, all daemon polls with `left=down` and `right=down` (`late-journal.txt:1-15`). No `wmr-bridge-bt-recover` line appears.

The source says the trigger and root cause are not established. It also says the loss must not be attributed to a phone toggle or a specific short run (`1f63e360`). The only recorded restoration was an out-of-band power cycle of UniFi Shield Plug UP1 `fc:ec:da:76:24:cb`, outlet 1. It produced a new Pi boot id, `1ee263ae-da28-4b8d-9718-358817bb1686`. At 04:13:40 UTC both hands were up (`1f63e360`).

The `-84` message means that the kernel failed to reassemble HCI frames from the received byte stream. It is a different signature from the HCI command timeout in event 1. The evidence does not tie event 2 to `wlan-cycle`, the phone or the ladder.

## Sentences this verdict corrects

ONY-1622 recorded "there is no wedge to root-cause" (`ad4e8c55`). That conclusion rested on the ONY-1621 re-baseline, which used phone toggles only (`ad4e8c55`). It did not exercise `wlan-cycle 60`. The sentence is correct for the phone-toggle trigger. It is false as a statement about the adapter.

ONY-1623 recorded "No recovery change lands" and kept the ~300 s suppression (`b2b0a336`). That memo rested on a ladder that appeared to leave the adapter powered. It did not examine the ladder exit contract. On main the script still ends with `exit 0` (`pi/systemd/wmr-bridge-bt-recover.sh:116`). Its finding of no working non-reboot primitive is not contradicted by these captures. Its no-change conclusion rested on a success signal that cannot fail.

## Root cause not established

The captures fix the layer, not the cause. Three hypotheses remain open:

- Shared-radio power. Event 1 began during a Wi-Fi cycle. The Bluetooth and Wi-Fi firmware both report Broadcom parts, `BCM4345C0` and `BCM43455` (`kernel-journal.txt:17`, `dmesg-after.txt:410`). Event 2 names no trigger (`1f63e360`). Neither attachment file list has a rail or current measurement. No capture tests this hypothesis.
- Firmware load. Event 1 contains `BCM: failed to write update baudrate (-110)` and `BCM: Reset failed (-110)` (`kernel-journal.txt:10`, `:13`). The driver later read the chip id and firmware version (`kernel-journal.txt:15-23`). Event 2 has no BCM line. These lines show a reload attempt, not the first cause.
- UART. Event 2 shows unparseable frames on the receive path (`late-kernel-journal.txt:1-30997`). Event 1 shows command timeouts (`kernel-journal.txt:3-6`). Neither attachment file list has a UART error counter, scope trace or cable-swap record.

No capture separates these hypotheses. The document therefore names no root cause and no single trigger. A fix must not claim one.

## What this document does not claim

- It does not claim a firmware root cause.
- It does not treat the software reboot after event 1 or the outlet cycle after event 2 as a PASS. ONY-1313 item 3 remains unaccepted (`hardware-bench-20261120`).
- It does not claim that `wlan-cycle` is implemented in this repository. The only mention is the bench verb list at `docs/runs/2026-11-18-pi-bench.md:108`, and no tracked file matches `wlan` or `wmr-agent` (`git ls-files`). The cycle ran from the bench agent on the Pi, not from this tree.
- It does not claim an exact per-cycle BLE subscribe latency. The failed round's export is excluded (see Excluded exports).
- It does not cover the 15 s failover gate.

## Excluded exports

ONY-1624 records that the failed round's automated export retrieved an old ZIP and that those ZIPs are excluded from timing evidence (`hardware-bench-20261120`, Limits). Two exports in the archive do not belong to event 1:

- `baseline.zip` has `generated_at` 2026-11-21T03:41:38.688678Z, before capture start. It has no `link_timeline` episodes.
- `wifi-baseline.zip` has `generated_at` 2026-11-21T03:51:42.858758Z, after the software reboot (`hardware-bench-20261120`). It is the baseline for the 10 s runs (`wifi-results.json`).

Both are excluded from event 1 timing. `wifi1.zip`, `wifi2.zip` and `wifi3.zip` belong to the 10 s runs and are outside this document (`wifi-results.json`).

## Sources

- ONY-1624 report `hardware-bench-20261120`: build, method, failure timeline, limits and the unaccepted item.
- Attachment `8cc46ba8-6b41-4acf-8c65-d1a1126a01d2`: shared evidence archive, 41 files. Used here: `kernel-journal.txt`, `journal-full.txt`, `samples.jsonl`, `recovery-status.txt`, `round1-events.jsonl`, `round1-pi-after.txt`, `evidence-manifest.json`, `wifi-results.json`.
- Attachment `b568c6f2-6afd-4e53-9b3d-dc853c9046e4`: late-loss archive, six files: `wmr-bench-run`, `wmr_bench.py`, `runner-notes.txt`, `late-dmesg.txt`, `late-journal.txt`, `late-kernel-journal.txt`.
- Comment `1f63e360-e722-4144-af08-d7fa8742adfe`: late-loss report and outlet restoration.
- Comment `ad4e8c55`: ONY-1622 close.
- Comment `b2b0a336`: ONY-1623 decision memo.
- Comment `d9cad85c-789a-4da9-9381-03ee7904cf17`: ONY-1621 phone-toggle re-baseline.
- Linux `include/uapi/asm-generic/errno.h`: `ETIMEDOUT` is 110. <https://github.com/torvalds/linux/blob/master/include/uapi/asm-generic/errno.h>
- Linux `include/net/bluetooth/hci.h`: `HCI_OP_RESET` is `0x0c03`. <https://github.com/torvalds/linux/blob/master/include/net/bluetooth/hci.h>
