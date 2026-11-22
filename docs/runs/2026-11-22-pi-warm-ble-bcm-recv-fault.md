# Warm-BLE failover, `bcm_recv` Oops, and rung-3 hang (2026-11-22)

On 2026-11-22, the TCP-up Bluetooth toggle control passed. A later Wi-Fi cut on the phone produced a warm-BLE `FF14` failover, followed by a Pi `hci0` hardware error, a `bcm_recv` kernel Oops during recovery rung 3, and a 120 s unit timeout that left `wmr-bridge` inactive. ONY-1313 item 3 remains open because no post-cut two-cycle score completed on this boot.

## Evidence key

- `path:line` is a file in this repository (`VERSION` is `0.3.28-rc.3` at `VERSION:1`).
- `name:line` is a file in attachment `3b75b8e6-a380-4cf9-b033-890f08d6af95` (`ony-1313-bench-20261122.zip`, SHA-256 `ac8b270dcdeeb430cc1973311b18827de87cf64054d3fa9e1e6bb74e4269159d`).
- Companion ran commit `a9d2fa51551e22708365e743f88106c4e90764b4`, version `0.3.27+35`, CI run `44986799486`, APK SHA-256 `c74bb487d8ba3c32e1c68e8adfa44994cf787658a7812800e6c3d18e279d83ef` (`apk-pin.txt:1-4`).
- The Pi ran `wmr-bridge 0.3.28-rc.3` on boot id `550c5225-3bb6-4da6-8302-8e262fc809fb` (`pi-pin.txt:1`, `:4`, `radio-pi-fault.txt:105`).
- Installed Pi hashes match `pi/systemd/wmr-bridge-bt-recover.sh` (`dee91872...`) and `pi/wmr_bridge/ble_gatt.py` (`dbf37214...`) (`pi-pin.txt:2-3`).

## TCP-up control pass

The phone toggled Bluetooth off at 14:20:15.431 UTC and on at 14:20:20.953 UTC (`radio-control-events.txt:1-3`). Both before and after exports stayed on `active_leg: "TCP"` (`radio-control-before.json:112`, `radio-control-after.json:112`). Decoded records increased from 35,551 left and 34,756 right to 40,669 left and 39,872 right, an advance of 5,118 left and 5,116 right (`radio-control-before.json:40-43`, `radio-control-after.json:40-43`).

Sequence gaps stayed at 1,570 left and 1,545 right, with zero parse errors (`radio-control-before.json:48-51`, `:64`, `radio-control-after.json:48-51`, `:64`). Radio marks moved from `null` to `408293147` and `413673973`, while `gatt_connect_start_us` (`208994697`) and `ff14_cccd_write_us` (`360940261`) did not change (`radio-control-before.json:200-206`, `radio-control-after.json:200-206`). The Pi kept `clients=2 subscribed=1`, `Powered: yes`, and both `eth0` and `wlan0` up (`radio-control-pi.txt:1-5`, `:12`, `:39-40`).

## Warm-BLE failover and `hci0` fault

At `1795357440.1444535` (09:24:00.144 local), the bench script disabled Wi-Fi on the phone (`radio-warm-ble-events.txt:1`). Both Pi uplinks (`eth0` and `wlan0`) remained up (`radio-pi-fault.txt:36`, `:103-104`). Companion failed over to `active_leg: "BLE"` with `failover_outcome: "landed"` over a warm GATT connection (`gatt_connect_start_us: 504701972`, `ff14_cccd_write_us: 631920962`, `first_ff14_notification_us: 631921436`) (`radio-warm-ble.json:113`, `:127`, `:201-204`). Because no fresh GATT dial occurred, the bench script raised `No fresh failover GATT connect within 12 s` (`radio-warm-ble-events.txt:5`).

| Timestamp | Event | Evidence |
|---|---|---|
| `1795357458.753596` | Daemon reports `clients=0 subscribed=0` | `radio-pi-fault.txt:6` |
| `1795357459.964398` | Kernel logs `Bluetooth: hci0: hardware error 0x00` | `radio-pi-fault.txt:49` |
| `1795357460.077496` | RIGHT and LEFT `hidraw` read errors and disconnects | `radio-pi-fault.txt:7-10` |
| `1795357462.092366` | Kernel logs `Opcode 0x0c03 failed: -110` (2.13 s later) | `radio-pi-fault.txt:50` |
| `1795357478.687797` | `wmr_bridge.ble_gatt` detects `Powered=no` | `radio-pi-fault.txt:19` |
| `1795357480.716796` | `Adapter1.Powered=True` fails (`Authentication Failed`) | `radio-pi-fault.txt:25-26` |
| `1795357480.733829` | `wmr-bridge-bt-recover.service` starts | `radio-pi-fault.txt:27-28` |

`0x0c03` is `HCI_OP_RESET` ([Linux `hci.h`](https://github.com/torvalds/linux/blob/master/include/net/bluetooth/hci.h)) and `-110` is `ETIMEDOUT` ([Linux `errno.h`](https://github.com/torvalds/linux/blob/master/include/uapi/asm-generic/errno.h)). `wmr_bridge.ble_gatt` triggered `wmr-bridge-bt-recover.service` after `Adapter1.Powered=True` returned `Authentication Failed` (`pi/wmr_bridge/ble_gatt.py:1552-1569`, `:2036-2041`).

## Rung-3 `bcm_recv` Oops and stranded state

The recovery unit stopped `wmr-bridge.service` at `1795357486.152687`, ran rung 1 (`rfkill unblock`), and entered rung 3 at `1795357486.216454` (`radio-pi-fault.txt:39-42`). Both `btmgmt power off` and `btmgmt power on` timed out after 10 s each and logged `(continuing)` at `1795357496.230319` and `1795357506.240234` (`radio-pi-fault.txt:44-45`, `pi/systemd/wmr-bridge-bt-recover.sh:177-180`).

Next, rung 3 writes the bound UART device (`serial0-0` on `hci_uart_bcm` on this Pi image) to sysfs `unbind` before rung 4 restarts `bluetooth.service` (`pi/systemd/wmr-bridge-bt-recover.sh:185-203`, `:218-220`, `docs/runs/2026-11-21-pi-wlan-cycle-wedge.md:46`). At `1795357506.342375`, `bluetoothd[525]` was still running and logged `Battery Provider Manager destroyed` (`radio-pi-fault.txt:46`). At `1795357506.428406`, the kernel logged a paging Oops in `bcm_recv+0x98/0x178 [hci_uart]` on workqueue `events_unbound flush_to_ldisc` (`radio-pi-fault.txt:55`, `:68`, `:73-75`, `:88-96`).

No further `wmr-bridge-bt-recover` lines appear after `1795357506.240234`. As `pi/systemd/wmr-bridge-bt-recover.sh:200-202` warns, a wedged driver can block the sysfs write in uninterruptible kernel sleep (`D`-state). At `1795357601.028194` (120.29 s after unit start), systemd killed the unit at `TimeoutStartSec=120` with `status=15/TERM` (`radio-pi-fault.txt:47-48`, `pi/systemd/wmr-bridge-bt-recover.service:38`). Because rung 3 hung before rung 5 and `sample_postcondition` (`pi/systemd/wmr-bridge-bt-recover.sh:222-250`), `wmr-bridge` stayed `inactive`, `bluetooth` stayed `active`, and `bluetoothctl` returned `No default controller available` (`radio-pi-fault.txt:99-101`).

## `wmr-companion` is ruled out

Companion passed the TCP-up control with zero added sequence gaps and zero parse errors (`radio-control-after.json:48-51`, `:64`). During the Wi-Fi cut, Companion switched to `BLE` over the warm GATT setup and received 27,252 left and 27,886 right decoded records with zero parse errors (`radio-warm-ble.json:39-42`, `:63`, `:113`). The Pi kernel logged `hci0: hardware error 0x00` and `0x0c03` `-110` on the Pi UART controller (`radio-pi-fault.txt:49-50`), and the `bcm_recv` Oops happened inside `hci_uart` during rung 3 after `wmr-bridge` had stopped (`radio-pi-fault.txt:39`, `:42-48`, `:75`).

## Root cause not established

The captures establish the failure sequence, not a single root cause. The initial `hci0: hardware error 0x00` preceded `wmr-bridge-bt-recover.service` by 20.77 s (`radio-pi-fault.txt:28`, `:49`). No capture isolates firmware, UART transport, or shared-radio power as the primary cause of the `hci0` wedge.

This document also makes no software-prevention guarantee. Continuing past timed-out `btmgmt` into `serial0-0` `hci_uart_bcm/unbind` while `bluetoothd` ran coincided with the `bcm_recv` Oops (`radio-pi-fault.txt:45-46`, `:75`), but preventing that `unbind` path does not prevent the preceding `hci0` hardware error.
