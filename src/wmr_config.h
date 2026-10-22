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

/* ---- Controllers ------------------------------------------------------- */

#define WMR_HAND_LEFT        0
#define WMR_HAND_RIGHT       1
#define WMR_NUM_CONTROLLERS  2

/* Classic Bluetooth addresses of the two paired controllers. Edit these
 * for your own hardware. Incoming connections from any other address are
 * rejected. */
#ifndef WMR_CFG_LEFT_ADDRESS
#define WMR_CFG_LEFT_ADDRESS   "2C:4D:79:C8:C9:CC"
#endif
#ifndef WMR_CFG_RIGHT_ADDRESS
#define WMR_CFG_RIGHT_ADDRESS  "2C:4D:79:C8:F9:A9"
#endif

/* Storage BTstack uses to hold HID report descriptors. */
#ifndef WMR_CFG_HID_DESCRIPTOR_STORAGE
#define WMR_CFG_HID_DESCRIPTOR_STORAGE  4096
#endif

/* ---- Frames, queue and wire format ------------------------------------- */

/* Largest controller frame (A1 + report ID + payload) that is forwarded.
 * Observed frames are 46 bytes. Longer frames are counted as drops. */
#ifndef WMR_CFG_MAX_FRAME
#define WMR_CFG_MAX_FRAME          64
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
