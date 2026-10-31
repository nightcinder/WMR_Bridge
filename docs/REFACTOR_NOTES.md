# Refactor notes

First milestone: refactor only. Not added: decoding, batching, automatic bond
deletion, or any change to the wire format, GATT UUIDs or service layout.

## Where the old code went

Source: `example/hid_host_demo.c` from the author's BTstack working tree (single
file, 629 lines; not included in this repository).

| Old | New |
| --- | --- |
| `controllers[]`, `controller_for_cid`, connect/disconnect, `HID_SUBEVENT_*` handling | `src/wmr_controller.c` |
| PIN reject, SSP auto-confirm | `wmr_controller_on_hci_event` |
| `k` command (bond reset, LEFT only) | `wmr_controller_forget_bond` + `src/wmr_console.c` (now `k` LEFT and `K` RIGHT) |
| `stream_*` queue, fragment building | `src/wmr_queue.c`, `src/wmr_protocol.c`, pump in `src/wmr_transport.c` |
| `phone_*` ATT read/write, advertising, LE connect/disconnect | `src/wmr_transport.c` |
| `stream_print_stats`, `stdin_process`, `show_usage` | `src/wmr_console.c` |
| `btstack_main`, `packet_handler` routing | `src/wmr_bridge_main.c` |
| Magic numbers (`256`, `64`, `12`, `244`, timers, advertising) | `src/wmr_config.h` |
| `hid_host_demo.gatt` | `wmr_bridge.gatt` (content identical) |

## Intentional behavior differences

These are the only deliberate changes. Everything else is meant to be identical.

1. Diagnostics off by default: the per-event "HCI diagnostic" hex dumps are now
   behind `WMR_CFG_HCI_TRACE` (0), and report hex dumps behind
   `WMR_CFG_REPORT_HEXDUMP` (0).
2. Per-controller report log: only the first report of each connection is
   announced (one line, no hexdump). The "every 10000th report" dump was
   removed.
3. Console: added `K` (bond reset for RIGHT, symmetric with `k`). `s` now also
   prints the stream counters. Help text relabelled "BOND RESET".
4. `SET_PROTOCOL`, descriptor, connect and error messages are unchanged.
5. Status-read "sequence" is unchanged in value and format (seconds since boot).
6. The statistics timer is now independent of the 1 s status tick. The period
   stays 5 s.
7. Build: `main/CMakeLists.txt` selects `wmr_bridge`. Python for
   `compile_gatt.py` is taken from ESP-IDF instead of a hard-coded path
   (fallback to the old path).

## Regression guardrails (do not undo)

- Keep incoming HID handling. Removing it broke controller reconnects.
- Do not parse `ATT_EVENT_CAN_SEND_NOW` with an assumed size/handle layout. It
  blocked notifications. The handler only triggers the pump. Check the local
  BTstack API before changing it.
- Keep callback sources separate: HCI events -> `wmr_bridge_hci_handler` only.
  HID events -> `wmr_controller`'s HID handler only. ATT events ->
  `wmr_transport`'s ATT handler only, which acts on `CAN_SEND_NOW` alone.
  Registering one handler for several sources processed phone connections twice.
- Run `compile_gatt.py` through Python explicitly, never directly (it launched
  Node.js).
- Bluetooth callbacks stay short. The HID report path copies the frame into the
  queue and returns. Formatting and printing happen elsewhere or are rare.
- Only `wmr_transport.c` includes the generated `wmr_bridge.h` (it defines
  `profile_data`).

## Not changed on purpose

Queue size (256), frame cap (64), header (12), notification cap (244),
advertising parameters, drop-newest policy, queue reset points, counters, and
status layout.
