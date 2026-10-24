/*
 * Copyright (C) 2017 BlueKitchen GmbH
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holders nor the names of
 *    contributors may be used to endorse or promote products derived
 *    from this software without specific prior written permission.
 * 4. Any redistribution, use, or modification is done solely for
 *    personal benefit and not for any commercial purpose or for
 *    monetary gain.
 *
 * THIS SOFTWARE IS PROVIDED BY BLUEKITCHEN GMBH AND CONTRIBUTORS
 * ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL BLUEKITCHEN
 * GMBH OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 * BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
 * OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
 * AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF
 * THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 *
 * Please inquire about commercial licensing options at 
 * contact@bluekitchen-gmbh.com
 *
 */

/*
 * wmr_config.h - compile-time configuration for wmr_bridge.
 *
 * Every tunable is a named constant guarded by #ifndef so it can be
 * overridden from the compiler command line. Changing a value marked
 * "wire" changes the BLE packet format seen by the phone.
 */

#ifndef WMR_CONFIG_H
#define WMR_CONFIG_H

#include <stdbool.h>

/* ---- Controllers ------------------------------------------------------- */

#define WMR_HAND_LEFT        0
#define WMR_HAND_RIGHT       1
#define WMR_NUM_CONTROLLERS  2

/* Which controllers this build serves. 0 = both (default, current behaviour), 1 = LEFT
 * only, 2 = RIGHT only. The excluded hand is ignored by discovery, refused by the outgoing
 * connect, declined on an incoming connection and never written to NVS. Compile-time so one
 * commit can build the REF and the two single-hand images; see
 * docs/runs/sheets/two-bridge-hands.md for the EXTRA_CFLAGS route. */
#ifndef WMR_CFG_BRIDGE_HANDS
#define WMR_CFG_BRIDGE_HANDS              0
#endif
#if WMR_CFG_BRIDGE_HANDS < 0 || WMR_CFG_BRIDGE_HANDS > 2
#error "WMR_CFG_BRIDGE_HANDS must be 0 (both), 1 (LEFT only) or 2 (RIGHT only)"
#endif

/* True if a bridge built for `bridge_hands` serves `hand`. Kept as a function so the host
 * tests can check all three values without rebuilding. `hand` outside the two hands is
 * never served. */
static inline bool wmr_bridge_hand_enabled(int bridge_hands, int hand){
    if (hand < 0 || hand >= WMR_NUM_CONTROLLERS) return false;
    if (bridge_hands == 0) return true;
    return hand + 1 == bridge_hands;
}

/* The compile-time answer used by the firmware. */
#define WMR_BRIDGE_HAND_ENABLED(hand)  wmr_bridge_hand_enabled(WMR_CFG_BRIDGE_HANDS, (int) (hand))

/* BLE advertised name. Default is unchanged; the GATT database and its device-name value
 * are not touched (GATT layout and handles stay as they are). A single-hand build
 * normally keeps the `WMR Bridge` prefix so prefix-matching clients still find it.
 *
 * ESP-IDF's EXTRA_CFLAGS is split on every space (tools/cmake/project.cmake ->
 * spaces2list), so a literal `"WMR Bridge L"` cannot be passed there as one macro value.
 * Select one of these quoted tokens in that route instead:
 *   $env:EXTRA_CFLAGS = "-DWMR_CFG_BRIDGE_HANDS=1 -DWMR_CFG_BLE_NAME=WMR_CFG_BLE_NAME_LEFT"
 * Must be a string literal of 1..22 characters to fit the legacy advertising packet. */
#define WMR_CFG_BLE_NAME_BOTH                "WMR Bridge"
#define WMR_CFG_BLE_NAME_LEFT                "WMR Bridge L"
#define WMR_CFG_BLE_NAME_RIGHT               "WMR Bridge R"
#ifndef WMR_CFG_BLE_NAME
#define WMR_CFG_BLE_NAME                     WMR_CFG_BLE_NAME_BOTH
#endif

/* Controllers are found by their Bluetooth names ("Motion controller - Left" / "- Right")
 * and the addresses are stored in NVS; see docs/DISCOVERY.md. There are no default
 * addresses. Optional fixed override for one or both hands, which skips discovery for that
 * hand and is never written to NVS, e.g.
 *   -DWMR_CFG_LEFT_ADDRESS=\"AA:BB:CC:DD:EE:FF\"
 * Leave undefined to use discovery. An override assumes a WMR controller: there is no
 * model field on the command line, so a PSVR2 controller must be discovered by name
 * (which also stores its model). */
/* #define WMR_CFG_LEFT_ADDRESS   "AA:BB:CC:DD:EE:FF" */
/* #define WMR_CFG_RIGHT_ADDRESS  "AA:BB:CC:DD:EE:FF" */

/* Start discovery by itself after boot when a hand has no address (1), or only from the
 * console key `d` (0). */
#ifndef WMR_CFG_DISCOVERY_AUTOSTART
#define WMR_CFG_DISCOVERY_AUTOSTART     1
#endif
/* Length of one inquiry in 1.28 s units (8 = 10.24 s), and how many inquiry rounds one
 * discovery runs before giving up on a hand that was not found. Inquiry takes Classic
 * airtime from a controller that is already streaming, so it is bounded. */
#ifndef WMR_CFG_DISCOVERY_INQUIRY_LEN
#define WMR_CFG_DISCOVERY_INQUIRY_LEN   8
#endif
#ifndef WMR_CFG_DISCOVERY_ROUNDS
#define WMR_CFG_DISCOVERY_ROUNDS        6
#endif
/* Delay after the stack comes up before automatic discovery starts, so known controllers
 * that reconnect on their own are not competing with an inquiry. */
#ifndef WMR_CFG_DISCOVERY_START_DELAY_MS
#define WMR_CFG_DISCOVERY_START_DELAY_MS 3000
#endif
/* BTstack TLV (NVS) tag holding the discovered addresses ('WMRA'). */
#ifndef WMR_CFG_ADDR_TLV_TAG
#define WMR_CFG_ADDR_TLV_TAG            0x574D5241u
#endif

/* Storage BTstack uses to hold HID report descriptors. */
#ifndef WMR_CFG_HID_DESCRIPTOR_STORAGE
#define WMR_CFG_HID_DESCRIPTOR_STORAGE  4096
#endif

/* ---- PlayStation VR2 Sense controllers ---------------------------------- */

/* The bridge also recognises Sony's PSVR2 Sense controllers by name (see
 * wmr_discovery.h for the names and the per-hand model). Their input reports are 0x31
 * frames of up to WMR_PSVR2_INPUT_FRAME_MAX bytes (A1, report ID, 77 payload bytes),
 * forwarded on the raw stream (FF11). They send no input until the host reads HID
 * Feature Report 0x05 over the control channel, and they do not speak Microsoft's FF13
 * config protocol; both are handled per controller model in wmr_controller.c. There is
 * no PSVR2 parser in the decoded stream yet, so FF12/FF14 count those frames as decode
 * errors. Scope and open questions: docs/PSVR2.md.
 *
 * Both switches are compile-time so PSVR2 support is opt-in to preserve DRAM headroom:
 *   -DWMR_CFG_PSVR2_SUPPORT=1   enable PSVR2 discovery, frames, and wake
 *   -DWMR_CFG_PSVR2_WAKE=0      match PSVR2 but send no wake request (diagnostics) */
#ifndef WMR_CFG_PSVR2_SUPPORT
#define WMR_CFG_PSVR2_SUPPORT      0
#endif
#ifndef WMR_CFG_PSVR2_WAKE
#define WMR_CFG_PSVR2_WAKE         1
#endif

/* HID transaction header, input report ID and largest complete frame (A1 + report ID +
 * payload). 0x31 reports carry 77 payload bytes, so the frame is 79 bytes. */
#define WMR_PSVR2_HID_HEADER       0xA1
#define WMR_PSVR2_INPUT_REPORT_ID  0x31
#define WMR_PSVR2_INPUT_FRAME_MAX  79
/* HID Feature Report read that wakes a PSVR2 Sense controller (unverified on hardware). */
#define WMR_PSVR2_WAKE_FEATURE_ID  0x05
/* Delay between DESCRIPTOR_AVAILABLE and that read. On incoming/reconnect links BTstack
 * has SET_PROTOCOL(report) queued at descriptor time; starting GET_REPORT immediately
 * makes it skip the SET_PROTOCOL. This delay also covers outgoing links, which do not
 * send SET_PROTOCOL at all. */
#ifndef WMR_CFG_PSVR2_WAKE_DELAY_MS
#define WMR_CFG_PSVR2_WAKE_DELAY_MS 250
#endif

/* ---- Frames, queue and wire format ------------------------------------- */

/* Largest controller frame (A1 + report ID + payload) that is forwarded.
 * WMR frames are 46 bytes; PSVR2 input frames are up to 79. When PSVR2 support
 * is enabled, MAX_FRAME defaults to 80 (96 overflows dram0_0_seg; see
 * docs/PSVR2.md). When PSVR2 support is disabled (default), MAX_FRAME defaults
 * to 64 to preserve DRAM headroom for the WMR image. Longer frames are counted
 * as drops. */
#ifndef WMR_CFG_MAX_FRAME
#if WMR_CFG_PSVR2_SUPPORT
#define WMR_CFG_MAX_FRAME          80
#else
#define WMR_CFG_MAX_FRAME          64
#endif
#endif
#if WMR_CFG_PSVR2_SUPPORT && WMR_CFG_MAX_FRAME < WMR_PSVR2_INPUT_FRAME_MAX
#error "WMR_CFG_PSVR2_SUPPORT needs WMR_CFG_MAX_FRAME >= WMR_PSVR2_INPUT_FRAME_MAX (79)"
#endif

/* Bounded FIFO shared by both hands, in whole frames. When full, new
 * frames are dropped (drop-newest) and counted. */
#ifndef WMR_CFG_QUEUE_CAPACITY
#define WMR_CFG_QUEUE_CAPACITY     256
#endif

/* wire: size of the fragment header preceding each payload chunk. */
#define WMR_PROTO_HEADER_SIZE      12

/* wire: first byte of every notification ('R'). */
#define WMR_PROTO_MAGIC            0x52

/* Upper bound on one ATT notification value, whatever the MTU allows. */
#ifndef WMR_CFG_MAX_NOTIFICATION
#define WMR_CFG_MAX_NOTIFICATION   244
#endif

/* ATT opcode + handle overhead inside an ATT_MTU-sized PDU. */
#define WMR_ATT_NOTIFY_OVERHEAD    3

/* ---- BLE peripheral ---------------------------------------------------- */

/* Advertising interval in 0.625 ms units (0x00a0 = 100 ms, 0x00f0 = 150 ms). */
#ifndef WMR_CFG_ADV_INTERVAL_MIN
#define WMR_CFG_ADV_INTERVAL_MIN   0x00a0
#endif
#ifndef WMR_CFG_ADV_INTERVAL_MAX
#define WMR_CFG_ADV_INTERVAL_MAX   0x00f0
#endif
/* Advertise on all three channels (bit mask 7). */
#define WMR_CFG_ADV_CHANNEL_MAP    7

/* Connection parameters the bridge asks the phone for after it connects (an
 * L2CAP connection parameter update request; the phone may refuse or pick other
 * values, and what it actually grants is logged and shown in the statistics
 * line). Interval in 1.25 ms units (6 = 7.5 ms, 12 = 15 ms), supervision timeout
 * in 10 ms units (400 = 4 s). A short interval matters because the two
 * controllers produce about 350 reports/s together and each is one notification
 * on the raw stream. */
#ifndef WMR_CFG_CONN_INTERVAL_MIN
#define WMR_CFG_CONN_INTERVAL_MIN  6
#endif
#ifndef WMR_CFG_CONN_INTERVAL_MAX
#define WMR_CFG_CONN_INTERVAL_MAX  12
#endif
#ifndef WMR_CFG_CONN_LATENCY
#define WMR_CFG_CONN_LATENCY       0
#endif
#ifndef WMR_CFG_CONN_TIMEOUT
#define WMR_CFG_CONN_TIMEOUT       400
#endif
/* Delay between the phone connecting and the request, so it does not compete
 * with the phone's service discovery and MTU exchange. */
#ifndef WMR_CFG_CONN_REQUEST_DELAY_MS
#define WMR_CFG_CONN_REQUEST_DELAY_MS 500
#endif

/* ---- Timers ------------------------------------------------------------ */

/* Retry period for the notification pump: re-requests a send when frames are
 * waiting and none is outstanding (e.g. after a send error).
 * See docs/RECONNECT_AND_QUEUE.md. */
#ifndef WMR_CFG_PUMP_RETRY_MS
#define WMR_CFG_PUMP_RETRY_MS      10
#endif

/* Period of the status-sequence tick (the counter in the status read). */
#ifndef WMR_CFG_STATUS_TICK_MS
#define WMR_CFG_STATUS_TICK_MS     1000
#endif

/* Period of the periodic statistics line. */
#ifndef WMR_CFG_STATS_INTERVAL_MS
#define WMR_CFG_STATS_INTERVAL_MS  5000
#endif

/* ---- Status LED --------------------------------------------------------- */

/* One addressable RGB LED (WS2812/NeoPixel): red = no controller connected, green = one,
 * blue = two. Data line on WMR_CFG_STATUS_LED_GPIO; the LED needs 5 V and GND. Set
 * WMR_CFG_STATUS_LED to 0 in builds without an LED. Single-hand builds only ever reach
 * green, because the other hand is not served. */
#ifndef WMR_CFG_STATUS_LED
#define WMR_CFG_STATUS_LED          1
#endif
#ifndef WMR_CFG_STATUS_LED_GPIO
#define WMR_CFG_STATUS_LED_GPIO     4
#endif
#ifndef WMR_CFG_STATUS_LED_BRIGHTNESS
#define WMR_CFG_STATUS_LED_BRIGHTNESS  32
#endif
#if WMR_CFG_STATUS_LED_BRIGHTNESS < 0 || WMR_CFG_STATUS_LED_BRIGHTNESS > 255
#error "WMR_CFG_STATUS_LED_BRIGHTNESS must be 0..255"
#endif
#ifndef WMR_CFG_STATUS_LED_POLL_MS
#define WMR_CFG_STATUS_LED_POLL_MS  250
#endif

/* ---- Classic contention variants (docs/runs/sheets/contention-2.md) ------ */

/* The three variants below are pre-implemented so one can be selected at build time
 * without touching source. Each defaults to 0, which leaves the current `main` behaviour
 * exactly as it was. Build at most one Classic policy variant (V2 or V3) at a time; V1 may
 * be combined with either. Selection is normally made with the ESP-IDF build variable:
 *   $env:EXTRA_CFLAGS = "-DWMR_CFG_STAGGER_FIRST_HAND=1"
 * The run sheet documents the experiment and the BTstack calls. */

/* V1: hold the second hand's HID connection until the first hand has delivered input
 * reports for 30 s. 0 = off, 1 = LEFT first (RIGHT held), 2 = RIGHT first (LEFT held).
 * The gate is on report flow, not on the ACL; see docs/runs/sheets/contention-2.md. */
#ifndef WMR_CFG_STAGGER_FIRST_HAND
#define WMR_CFG_STAGGER_FIRST_HAND        0
#endif
#if WMR_CFG_STAGGER_FIRST_HAND < 0 || WMR_CFG_STAGGER_FIRST_HAND > 2
#error "WMR_CFG_STAGGER_FIRST_HAND must be 0 (off), 1 (LEFT first) or 2 (RIGHT first)"
#endif

/* V2: keep sniff out of the Classic link policy. At init the default policy becomes role
 * switch only, and every ACL connection gets an explicit per-handle write (plus an exit
 * from sniff) so a reconnect cannot race the init. The write status and read-back are
 * logged as `POLICY:`. 0 = off, 1 = no sniff. */
#ifndef WMR_CFG_CLASSIC_NO_SNIFF
#define WMR_CFG_CLASSIC_NO_SNIFF           0
#endif

/* V3: request master on both Classic links and disable role switch/sniff per handle.
 * `gap_set_allow_role_switch(false)` keeps outgoing connections from asking for a switch;
 * after each ACL connection the current role is logged, a slave link gets a role request,
 * and the link policy is written as 0. 0 = off, 1 = force master. */
#ifndef WMR_CFG_CLASSIC_FORCE_MASTER
#define WMR_CFG_CLASSIC_FORCE_MASTER      0
#endif

#if WMR_CFG_CLASSIC_NO_SNIFF && WMR_CFG_CLASSIC_FORCE_MASTER
#error "WMR_CFG_CLASSIC_NO_SNIFF and WMR_CFG_CLASSIC_FORCE_MASTER are alternative variants; enable at most one"
#endif

/* ---- Diagnostic build option: FF12 decimation ---------------------------- */

/* Default 1 = off (every decoded record is sent). With N > 1 only every Nth report of
 * each hand is sent on FF12 and the rest are counted as `decimated`; the phone then sees
 * `sequence` jump by N. FF11 is never decimated. It exists to test whether BLE airtime
 * costs controller reports: if the missing-report figure falls back towards the idle
 * level at N=2 or 4, it does. Not for normal use. */
#ifndef WMR_CFG_FF12_DECIMATE
#define WMR_CFG_FF12_DECIMATE             1
#endif
#if WMR_CFG_FF12_DECIMATE < 1
#error "WMR_CFG_FF12_DECIMATE must be 1 (off) or more"
#endif

/* ---- FF13 config block read -------------------------------------------------- */

/* 1: read each controller's config block (block 0x02) after its HID descriptor arrives
 * (docs/PROTOCOL.md, FF13). 0: never send any config command, as before FF13; use it to check
 * whether the read is what stops a controller's input stream. */
#ifndef WMR_CFG_FF13_READ
#define WMR_CFG_FF13_READ                 1
#endif

/* ---- FF14 batching -------------------------------------------------------- */

/* Longest a queued record waits before a partial FF14 batch is sent while the connection
 * interval is not yet known (the phone's granted interval is used once it is). 15 ms is the
 * interval the phone granted in the bench runs. A full batch is sent at once. */
#ifndef WMR_CFG_BATCH_HOLD_DEFAULT_US
#define WMR_CFG_BATCH_HOLD_DEFAULT_US     15000
#endif

/* ---- Link diagnostics (wmr_linkdiag.c) ----------------------------------- */

/* Every interval each connected Classic link is polled with 3 HCI commands (Read RSSI,
 * Read Failed Contact Counter, Read Link Quality): 1.2 commands/s and 1.2 Command Complete
 * events/s for two links at 5 s. Commands go out one at a time, at least PACE_MS apart,
 * and an unanswered one is abandoned after REPLY_TIMEOUT_MS. */
#ifndef WMR_CFG_LINKDIAG_INTERVAL_MS
#define WMR_CFG_LINKDIAG_INTERVAL_MS      5000
#endif
#ifndef WMR_CFG_LINKDIAG_PACE_MS
#define WMR_CFG_LINKDIAG_PACE_MS          100
#endif
#ifndef WMR_CFG_LINKDIAG_REPLY_TIMEOUT_MS
#define WMR_CFG_LINKDIAG_REPLY_TIMEOUT_MS 500
#endif

/* ---- Diagnostics (all off by default) ---------------------------------- */

/* Print selected HCI events and hex-dump them. Very verbose. */
#ifndef WMR_CFG_HCI_TRACE
#define WMR_CFG_HCI_TRACE          0
#endif

/* Hex-dump the first report of each connection. */
#ifndef WMR_CFG_REPORT_HEXDUMP
#define WMR_CFG_REPORT_HEXDUMP     0
#endif

#endif /* WMR_CONFIG_H */
