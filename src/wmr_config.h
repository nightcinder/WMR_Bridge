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

/* BR/EDR Class of Device. The bridge is a Classic HID *host* for the controllers, not a
 * HID device, so it must not look like a Classic gamepad: a phone that knows the bridge's
 * public BR/EDR address otherwise opens Classic HID (which this side cannot serve) and
 * drops the LE link. The 2026-10-28 logs show it: B0:9C:B2:CE:26:88 (Google OUI, the
 * Pixel's BR/EDR address) comes up within a second of every LE drop. Miscellaneous (0)
 * leaves only the LE HID service; GAP Appearance 0x03C4 still gives the gamepad icon. */
#ifndef WMR_CFG_CLASS_OF_DEVICE
#define WMR_CFG_CLASS_OF_DEVICE              0x000000u
#endif

/* Classic inquiry scan (discoverability). Off by default: the bridge finds the controllers
 * itself by inquiry and pages them, and controller reconnects are incoming pages, which
 * need page scan (connectability), not inquiry scan. A Classically discoverable dual-mode
 * device is a problem for the phone: Android merges the inquiry result with the LE
 * advertisement, marks the device dual-mode and pairs it over BR/EDR, where this bridge is
 * a HID *host* with no profile to offer, so pairing ends in "cannot communicate". With
 * inquiry scan off and the BR/EDR-not-supported advertising flag set, the phone sees an
 * LE-only HID device and pairs over LE. Set to 1 to restore the old behaviour. */
#ifndef WMR_CFG_CLASSIC_DISCOVERABLE
#define WMR_CFG_CLASSIC_DISCOVERABLE         0
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
/* Pause before a discovery burst is tried again while a hand still has no address (a
 * controller put in pairing mode after the burst started is then found without a reboot),
 * and how many extra bursts are tried before discovery stops and waits for `d`. Inquiry
 * shares the radio with a controller that is already streaming, so this stays bounded.
 * Only used when auto-start is enabled. */
#ifndef WMR_CFG_DISCOVERY_RETRY_MS
#define WMR_CFG_DISCOVERY_RETRY_MS      15000
#endif
#ifndef WMR_CFG_DISCOVERY_RETRIES
#define WMR_CFG_DISCOVERY_RETRIES       3
#endif
/* How many times a controller found by inquiry is connected (which is also when it pairs)
 * before its address is given up as unusable, and the pause between those attempts. A
 * failed attempt is common while a controller finishes entering pairing mode; the address
 * is not written to NVS until a connection to it has succeeded, so giving up ends this run
 * and lets the next automatic burst (or `d`) look for it again instead of leaving a stored
 * address that never connects. */
#ifndef WMR_CFG_PAIR_ATTEMPTS
#define WMR_CFG_PAIR_ATTEMPTS           3
#endif
#ifndef WMR_CFG_PAIR_RETRY_MS
#define WMR_CFG_PAIR_RETRY_MS           1000
#endif
/* Pause before retrying a connect that failed with HCI 0x09 (no free ACL slot). The
 * controller can keep a link used for a remote name request for a few seconds, so this is
 * longer than an ordinary failed attempt: paging again immediately repeats the 0x09. */
#ifndef WMR_CFG_PAIR_BUSY_RETRY_MS
#define WMR_CFG_PAIR_BUSY_RETRY_MS      2500
#endif
/* A discovery page can complete its ACL while the controller then neither starts
 * encryption nor opens its HID channel. The 2026-10-28 18:54 log has LEFT's ACL up for
 * 20 s in that state and the connect phase waited forever. When the hand has a stored
 * link key, ask for encryption after this long (a normal pairing/encryption completes
 * in about 0.7 s); a hand without a key is left to its own Secure Simple Pairing. */
#ifndef WMR_CFG_PAIR_ENCRYPT_MS
#define WMR_CFG_PAIR_ENCRYPT_MS         1500
#endif
/* Give up on a connect whose ACL is up but which has not produced a HID channel by this
 * long: drop the ACL so the attempt is retried instead of hanging the discovery run. */
#ifndef WMR_CFG_PAIR_CONNECT_TIMEOUT_MS
#define WMR_CFG_PAIR_CONNECT_TIMEOUT_MS 5000
#endif
/* How long an incoming Classic ACL from an address that is not LEFT or RIGHT may sit
 * without being accepted as a controller before it is disconnected. A stray device (a
 * phone's BR/EDR side, a nearby headset) otherwise holds one of the controller's few ACL
 * slots: the 2026-10-28 log has such a link sitting idle while "connecting LEFT" failed
 * with HCI 0x09. When both hands already have addresses the link is dropped at once. */
#ifndef WMR_CFG_UNKNOWN_ACL_TIMEOUT_MS
#define WMR_CFG_UNKNOWN_ACL_TIMEOUT_MS  5000
#endif
/* Pause after the last remote name request before the connect phase pages the hands, long
 * enough for the controller to release the link it used for the name request. */
#ifndef WMR_CFG_DISCOVERY_CONNECT_DELAY_MS
#define WMR_CFG_DISCOVERY_CONNECT_DELAY_MS 1500
#endif
/* How long a reserved inquiry round waits for a connected hand's ACL to drop before the
 * inquiry starts. The controller answers the disconnect command well inside this. */
#ifndef WMR_CFG_DISCOVERY_RELEASE_MS
#define WMR_CFG_DISCOVERY_RELEASE_MS    400
#endif
/* Delay after the stack comes up before automatic discovery starts, so known controllers
 * that reconnect on their own are not competing with an inquiry. */
#ifndef WMR_CFG_DISCOVERY_START_DELAY_MS
#define WMR_CFG_DISCOVERY_START_DELAY_MS 3000
#endif
/* Reserve the Classic radio for every discovery round that still has a hand missing: a
 * controller that already has an address is disconnected at the start of each such round
 * (its ACL, not just the HID channel) and every incoming link is refused until the round
 * ends. The 2026-10-26 field logs show why: every successful discovery of the missing hand
 * happened while the other hand had no link, while 18 consecutive inquiry rounds with the
 * other hand streaming saw no answer at all. A hand connected in an earlier round would
 * otherwise block the remaining rounds, so discovery can only pair one controller per run.
 * The radio is shared; give each round that is still looking a quiet radio. The held hand
 * reconnects by itself afterwards. Set to 0 to keep the old behaviour (an already-connected
 * hand keeps streaming through discovery). */
#ifndef WMR_CFG_DISCOVERY_RESERVE_RADIO
#define WMR_CFG_DISCOVERY_RESERVE_RADIO  1
#endif
/* Stop reserving the radio after this many rounds of one run. A hand that is off or not in
 * pairing mode never answers, and the 2026-10-28 18:54 run shows the cost of holding the
 * other hand off for every round: RIGHT paired and streamed, then was disconnected for
 * round 4 and refused for the rest of the log. The quiet rounds the field logs need to
 * pair the missing hand happen early; after this bound a connected hand may stay while the
 * remaining rounds run, and `d` starts a fresh run with a fresh reservation. */
#ifndef WMR_CFG_DISCOVERY_RESERVE_ROUNDS
#define WMR_CFG_DISCOVERY_RESERVE_ROUNDS 3
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
 * errors. PSVR2 discovery, frames and wake are always compiled in; every hand is
 * searched for both models by default. Scope and open questions: docs/PSVR2.md.
 *
 *   -DWMR_CFG_PSVR2_WAKE=0      recognise PSVR2 but send no wake request (diagnostics) */
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
 * WMR frames are 46 bytes; PSVR2 input frames are up to 79. 80 is the smallest
 * value that carries both and still fits the static queue in ESP32 DRAM (96
 * overflows dram0_0_seg; see docs/PSVR2.md). Longer frames are counted as
 * drops. */
#ifndef WMR_CFG_MAX_FRAME
#define WMR_CFG_MAX_FRAME          80
#endif
#if WMR_CFG_MAX_FRAME < WMR_PSVR2_INPUT_FRAME_MAX
#error "WMR_CFG_MAX_FRAME must be >= WMR_PSVR2_INPUT_FRAME_MAX (79)"
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
/* How long a central may stay connected without a bonded LESC link before the
 * bridge disconnects it. It has to cover the phone's pairing dialog on a first
 * connection; after that the phone re-encrypts with stored keys immediately.
 * See docs/SECURITY.md. */
#ifndef WMR_CFG_BLE_PAIR_TIMEOUT_MS
#define WMR_CFG_BLE_PAIR_TIMEOUT_MS 30000
#endif

/* ---- Classic ACL supervision ------------------------------------------- */

/* Link supervision timeout written on every Classic ACL to a known controller, in
 * 0.625 ms slots. BTstack leaves the controller default alone (0x7D00 = 20 s), which is
 * how long a switched-off controller stays "connected" in FF17 and the LED. 0x1F40 is
 * 5 s: a powered-off controller is reported disconnected about 5 s after its last
 * packet. Keep it well above the longest radio stall a discovery round or WiFi
 * coexistence can cause, because a spurious expiry costs a full controller reconnect.
 * 0 = do not write a timeout (leave the controller default). */
#ifndef WMR_CFG_CLASSIC_SUPERVISION_TIMEOUT
#define WMR_CFG_CLASSIC_SUPERVISION_TIMEOUT 0x1F40
#endif
#if WMR_CFG_CLASSIC_SUPERVISION_TIMEOUT != 0 && \
    (WMR_CFG_CLASSIC_SUPERVISION_TIMEOUT < 0x000A || WMR_CFG_CLASSIC_SUPERVISION_TIMEOUT > 0xFFFF)
#error "WMR_CFG_CLASSIC_SUPERVISION_TIMEOUT must be 0 or 0x000A..0xFFFF (0.625 ms slots)"
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

/* One addressable RGB LED (WS2812/NeoPixel): red = no controller connected, yellow = left
 * only, blue = right only, green = both. The LED needs 5 V and GND. Set WMR_CFG_STATUS_LED
 * to 0 in builds without an LED. Single-hand builds only reach red or their hand's colour,
 * because the other hand is not served.
 *
 * The same frame is driven on more than one data pin so a prebuilt image works without
 * knowing the board: WMR_CFG_STATUS_LED_GPIO (default 4) is the documented external-LED
 * pin on a devkit, and WMR_CFG_STATUS_LED_GPIO_ALT (default 27) is the M5Stack ATOM
 * Lite's onboard SK6812. Pins with nothing wired see only one short frame per poll.
 * Set the ALT pin to -1 when you drive a single LED, or override the primary pin with
 * -DWMR_CFG_STATUS_LED_GPIO=<n>. */
#ifndef WMR_CFG_STATUS_LED
#define WMR_CFG_STATUS_LED          1
#endif
#ifndef WMR_CFG_STATUS_LED_GPIO
#define WMR_CFG_STATUS_LED_GPIO     4
#endif
#ifndef WMR_CFG_STATUS_LED_GPIO_ALT
#define WMR_CFG_STATUS_LED_GPIO_ALT 27
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

/* ---- One-flash contention test image (ONY-430) --------------------------- */

/* 0 = release behaviour: every lever below uses the compile-time value above and the
 * console keys are absent. 1 = LEVERS image: V1 stagger, the 30 ms BLE interval request,
 * the two-interval FF14 hold and Classic TX power P6/P9 become runtime toggles (`g`, `i`,
 * `h`, `p`) with `x`/`t` state and readback. One image serves bench runs R1-R5; all
 * defaults at boot are the release configuration. Build with
 * -DWMR_CFG_TEST_LEVERS=1. */
#ifndef WMR_CFG_TEST_LEVERS
#define WMR_CFG_TEST_LEVERS                0
#endif
#if WMR_CFG_TEST_LEVERS < 0 || WMR_CFG_TEST_LEVERS > 1
#error "WMR_CFG_TEST_LEVERS must be 0 (release) or 1 (LEVERS test image)"
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

/* FF14 hold in connection intervals. 1 = one interval (default and current behaviour).
 * 2 holds a partial batch for two intervals, trading about one interval of extra latency
 * for fewer, larger notifications; the ONY-176 airtime sitting uses 2 as its variant. */
#ifndef WMR_CFG_BATCH_HOLD_INTERVALS
#define WMR_CFG_BATCH_HOLD_INTERVALS      1
#endif
#if WMR_CFG_BATCH_HOLD_INTERVALS < 1 || WMR_CFG_BATCH_HOLD_INTERVALS > 4
#error "WMR_CFG_BATCH_HOLD_INTERVALS must be 1..4"
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

/* Decode every ATT PDU on the phone link (one line per request/response, plus a
 * counters row in the status block). Diagnostic builds only: a HOGP host runs the
 * whole HID handshake over ATT, and this shows where it stops. */
#ifndef WMR_CFG_ATT_TRACE
#define WMR_CFG_ATT_TRACE          0
#endif

#endif /* WMR_CONFIG_H */
