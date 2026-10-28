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

#define BTSTACK_FILE__ "wmr_transport.c"

#include <stdio.h>
#include <string.h>
#include "btstack_config.h"
#include "btstack.h"
#include "hids_device.h"
#include "esp_app_desc.h"
#include "esp_timer.h"
#include "wmr_bridge.h"        /* generated from wmr_bridge.gatt: profile_data, ATT_*_HANDLE */
#include "wmr_batch.h"
#include "wmr_cfg.h"
#include "wmr_cfgmgr.h"
#include "wmr_controller.h"
#include "wmr_ctrl.h"
#include "wmr_diag.h"
#include "wmr_echo.h"
#include "wmr_hid.h"
#include "wmr_linkdiag.h"
#include "wmr_link.h"
#include "wmr_levers.h"
#include "wmr_decode.h"
#include "wmr_decoded.h"
#include "wmr_protocol.h"
#include "wmr_queue.h"
#include "wmr_secure.h"
#include "wmr_stream_mode.h"
#include "wmr_transport.h"

/* Experimental FF10 service; see wmr_bridge.gatt. The FF11 characteristic is
 * both the status read value and the raw notification stream. FF12 is the
 * notify-only decoded stream and FF14 the notify-only batched decoded stream.
 * At most one of the three is active per phone (wmr_stream_mode.h). */
#define STREAM_VALUE_HANDLE ATT_CHARACTERISTIC_0000FF11_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
#define STREAM_CCC_HANDLE   ATT_CHARACTERISTIC_0000FF11_0000_1000_8000_00805F9B34FB_01_CLIENT_CONFIGURATION_HANDLE
#define DECODED_VALUE_HANDLE ATT_CHARACTERISTIC_0000FF12_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
#define DECODED_CCC_HANDLE   ATT_CHARACTERISTIC_0000FF12_0000_1000_8000_00805F9B34FB_01_CLIENT_CONFIGURATION_HANDLE
#define BATCHED_VALUE_HANDLE ATT_CHARACTERISTIC_0000FF14_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
#define BATCHED_CCC_HANDLE   ATT_CHARACTERISTIC_0000FF14_0000_1000_8000_00805F9B34FB_01_CLIENT_CONFIGURATION_HANDLE
/* FF15: echo (write without response + notify), independent of the stream exclusion. */
#define ECHO_VALUE_HANDLE    ATT_CHARACTERISTIC_0000FF15_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
#define ECHO_CCC_HANDLE      ATT_CHARACTERISTIC_0000FF15_0000_1000_8000_00805F9B34FB_01_CLIENT_CONFIGURATION_HANDLE

/* FF13: controller config block (read window + write cursor + notify), independent of the
 * stream exclusion. */
#define CFG_VALUE_HANDLE     ATT_CHARACTERISTIC_0000FF13_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
#define CFG_CCC_HANDLE       ATT_CHARACTERISTIC_0000FF13_0000_1000_8000_00805F9B34FB_01_CLIENT_CONFIGURATION_HANDLE

/* FF16: the firmware version (ESP-IDF PROJECT_VER), read-only. */
#define VERSION_VALUE_HANDLE ATT_CHARACTERISTIC_0000FF16_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
/* DIS 0x2A26: the same version string, so a HOGP host can show the firmware. */
#define FW_REVISION_VALUE_HANDLE \
    ATT_CHARACTERISTIC_ORG_BLUETOOTH_CHARACTERISTIC_FIRMWARE_REVISION_STRING_01_VALUE_HANDLE

/* HID-over-GATT gamepad: report ID 1, 9 payload bytes, same descriptor as the
 * companion package. Notifications are independent of the FF10 stream modes. */
static wmr_hid_state_t hid;
/* HOGP hosts enable this CCCD after service discovery and bonding. A device
 * must not send input reports before that, and Android drops a device that
 * streams them during discovery. */
static bool hid_subscribed;
/* One line per connection for the first rejected HID notification. */
static bool hid_error_logged;

/* FF17: controller scan and status v2 (read + write + notify), independent of the
 * stream exclusion. */
#define CTRL_VALUE_HANDLE    ATT_CHARACTERISTIC_0000FF17_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
#define CTRL_CCC_HANDLE      ATT_CHARACTERISTIC_0000FF17_0000_1000_8000_00805F9B34FB_01_CLIENT_CONFIGURATION_HANDLE

#define STATUS_SIZE 16

/* Legacy advertising data: flags (3 bytes), complete local name (2-byte AD header + the
 * characters) and the complete 16-bit UUID list (2-byte AD header + HID 0x1812 and the
 * FF10 service, 6 bytes). The HID service UUID belongs in the advertising data, not only
 * in the scan response: a host that misses the scan response otherwise sees an unnamed
 * vendor UUID and cannot classify the device as a HID gamepad. The name is compile-time
 * (WMR_CFG_BLE_NAME); the whole packet must fit in 31 bytes, which is why the name is
 * limited to 20 characters. */
#define ADVERTISING_SIZE  (10u + sizeof(WMR_CFG_BLE_NAME))
static uint8_t advertising[ADVERTISING_SIZE];
typedef char wmr_ble_name_fits_legacy_advertising[
    (sizeof(WMR_CFG_BLE_NAME) >= 2 && sizeof(WMR_CFG_BLE_NAME) <= 21) ? 1 : -1];

static hci_con_handle_t phone_handle = HCI_CON_HANDLE_INVALID;
/* LE advertising state, tracked from the HCI command responses so `s` can say whether the
 * phone could have connected at that moment. BTstack stops advertising when a central
 * connects; the bridge re-asserts it after a disconnect, a failed connect attempt and
 * after controller discovery. */
static bool advertising_requested;
static bool advertising_active;
/* Active stream for the current phone connection; NONE until a CCC is enabled. */
static wmr_stream_t stream_mode = WMR_STREAM_NONE;
/* A CAN_SEND_NOW request is outstanding. */
static bool send_pending;
/* Seconds since boot; reported in the status read, not a frame sequence. */
static uint32_t status_tick;

static wmr_queue_t queue;
/* Logs the first queue-full drop of each episode, not every dropped report. */
static wmr_episode_t drop_episode;
/* An LE data-length-change event has been seen on the current phone connection. */
static bool data_length_seen;
static bool data_length_valid;
static uint16_t data_length_tx_octets;
static uint16_t data_length_tx_time_us;
static uint16_t data_length_rx_octets;
static uint16_t data_length_rx_time_us;
static uint16_t send_offset;       /* bytes of the head frame already sent */
static wmr_stats_t stats;
/* FF15: one outstanding echo; sent before any stream data. */
static wmr_echo_state_t echo;
static bool echo_subscribed;
/* FF13: per-connection read cursor and CCC; a state-change notify is owed per hand. */
static bool cfg_subscribed;
static uint8_t cfg_cursor_hand;
static uint32_t cfg_cursor_offset;
static bool cfg_notify_pending[WMR_NUM_CONTROLLERS];
/* FF17: control & status v2 subscription, pending notification and last command/result */
static bool ctrl_subscribed;
static bool ctrl_notify_pending;
static uint8_t ctrl_last_command;
static uint8_t ctrl_last_result;
static uint8_t ctrl_last_notified[WMR_CTRL_STATUS_SIZE];

static btstack_timer_source_t pump_timer;
/* FF14: fires when the oldest queued record has waited long enough to send a partial batch. */
static btstack_timer_source_t batch_timer;
static btstack_timer_source_t status_timer;
/* 0.3 security: disconnect a central that has not produced a bonded LESC link. */
static bool phone_secure;
static btstack_timer_source_t pair_timer;

/* ---- Connection parameters ---------------------------------------------- */

static wmr_conn_request_t conn_wanted = {
    WMR_CFG_CONN_INTERVAL_MIN, WMR_CFG_CONN_INTERVAL_MAX,
    WMR_CFG_CONN_LATENCY, WMR_CFG_CONN_TIMEOUT
};

/* LEVERS `i`: the request is rebuilt before each phone connection, so a toggle takes
 * effect on the next connection. The release build keeps the compile-time constants. */
static void conn_wanted_refresh(void){
#if WMR_CFG_TEST_LEVERS
    conn_wanted.interval_min = (uint16_t) wmr_levers_interval_min();
    conn_wanted.interval_max = (uint16_t) wmr_levers_interval_max();
#endif
}
static bool conn_known;
static wmr_conn_params_t conn_now;
static bool conn_update_pending;
static btstack_timer_source_t conn_request_timer;

static void conn_report(const char *what, uint16_t interval, uint16_t latency, uint16_t timeout){
    char text[48];
    conn_now.interval = interval;
    conn_now.latency = latency;
    conn_now.timeout = timeout;
    conn_known = true;
    wmr_conn_format(&conn_now, text, sizeof(text));
    printf("BLE connection parameters %s: %s (%s)\n", what, text,
           wmr_conn_params_satisfy(&conn_now, &conn_wanted) ? "meets request" : "does NOT meet request");
}

/* One-shot after the phone connects: ask for a short interval. The phone (the
 * central) decides; the result arrives as a connection-update-complete event,
 * or not at all if the request is refused or ignored (conn_update_pending then
 * stays set and shows in the statistics line). */
static void conn_request_fire(btstack_timer_source_t *timer){
    (void)timer;
    if (phone_handle == HCI_CON_HANDLE_INVALID) return;
    conn_wanted_refresh();
    if (!data_length_seen) printf("BLE data length: no change event seen since connect\n");
    int rc = gap_request_connection_parameter_update(phone_handle,
        conn_wanted.interval_min, conn_wanted.interval_max, conn_wanted.latency, conn_wanted.timeout);
    conn_update_pending = rc == 0;
    printf("BLE connection parameter update requested (interval %u-%u x 1.25 ms, latency %u, timeout %u x 10 ms): %s (rc=%d)\n",
           conn_wanted.interval_min, conn_wanted.interval_max, conn_wanted.latency, conn_wanted.timeout,
           rc == 0 ? "sent" : "NOT sent", rc);
}

/* ---- Queue and pump ----------------------------------------------------- */

/* Drop everything queued. Called whenever the live stream restarts (phone
 * connects, or notifications are switched on or off), so the phone never
 * receives a stale backlog. Purged frames are counted per hand. */
static void queue_reset(void){
    wmr_queue_clear(&queue, stats.purged);
    send_offset = 0;
}

static void pump_kick(void);
static uint16_t cfg_read(uint16_t read_offset, uint8_t *buffer, uint16_t buffer_size);

static bool cfg_notify_owed(void){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++)
        if (cfg_notify_pending[i]) return true;
    return false;
}

/* FF13: the config block state of `hand` changed. Only a subscribed phone is told; others
 * read the state. */
static void cfg_state_changed(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS || !cfg_subscribed || phone_handle == HCI_CON_HANDLE_INVALID) return;
    cfg_notify_pending[hand] = true;
    pump_kick();
}

static void batch_timer_fire(btstack_timer_source_t *timer){
    (void)timer;
    pump_kick();
}

/* FF14: is a batch due? True when a full notification is queued or the oldest record has
 * waited the hold time (one connection interval). Otherwise arms batch_timer for the
 * remaining wait, so a lone record is never held longer than that. */
static bool batch_due(void){
    const wmr_frame_t *oldest = wmr_queue_peek(&queue);
    if (!oldest) return false;
    const uint64_t now = (uint64_t) esp_timer_get_time();
    const uint64_t age64 = now > oldest->rx_time_us ? now - oldest->rx_time_us : 0;
    const uint32_t age = age64 > UINT32_MAX ? UINT32_MAX : (uint32_t) age64;
    const uint32_t hold = wmr_batch_hold_us(conn_known ? wmr_conn_interval_us(conn_now.interval) : 0);
    const unsigned max_count = wmr_batch_max_count(att_server_get_mtu(phone_handle));
    if (wmr_batch_flush_reason(queue.count, max_count, age, hold) != WMR_BATCH_FLUSH_NO) return true;
    btstack_run_loop_remove_timer(&batch_timer);
    btstack_run_loop_set_timer(&batch_timer, wmr_batch_timer_ms(age, hold));
    btstack_run_loop_add_timer(&batch_timer);
    return false;
}

static void pump_kick(void){
    const uint64_t now = (uint64_t) esp_timer_get_time();
    /* Time out a controller that stopped reporting before looking at the link,
     * so a release does not depend on a stream being subscribed. */
    wmr_hid_expire(&hid, now);
    wmr_episode_note_queue(&drop_episode, queue.count);
    if (phone_handle == HCI_CON_HANDLE_INVALID || send_pending) return;
    /* A pending echo or HID report needs a send opportunity whatever the stream
     * is doing. */
    if (!echo.pending && !cfg_notify_owed() && !ctrl_notify_pending &&
        !(hid_subscribed && wmr_hid_report_due(&hid, now))){
        if (stream_mode == WMR_STREAM_NONE || !queue.count) return;
        if (stream_mode == WMR_STREAM_BATCHED && !batch_due()) return;
    }
    send_pending = true;
    att_server_request_can_send_now_event(phone_handle);
}

/* The smallest ATT MTU the active stream can use; below it nothing is sent. */
static bool stream_mtu_ok(void){
    const uint16_t mtu = att_server_get_mtu(phone_handle);
    if (stream_mode == WMR_STREAM_DECODED) return wmr_decoded_mtu_ok(mtu);
    if (stream_mode == WMR_STREAM_BATCHED) return wmr_batch_mtu_ok(mtu);
    return true;
}

void wmr_transport_submit_report(unsigned hand, const uint8_t *report, uint16_t length){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    wmr_linkdiag_note_report(hand, report, length);       /* reads 4 bytes, see wmr_linkdiag.h */
    uint32_t sequence = ++stats.sequence[hand];
    stats.rx[hand]++;
    /* HID is independent of the FF10 stream: it runs with no app subscribed,
     * which is the point of the no-app Android gamepad. */
    /* Frames from other devices (PSVR2 and friends) share this transport. They are not
     * HID input, so they must not show up as a decode failure; only a WMR-shaped report
     * that the HID path rejects counts. */
    if (length && length <= WMR_CFG_MAX_FRAME &&
        wmr_decode_frame_is_wmr(report, length) &&
        !wmr_hid_note_report(&hid, (uint8_t) hand, report, length,
                             (uint64_t) esp_timer_get_time())){
        stats.hid_decode_errors++;
    }
    if (stream_mode == WMR_STREAM_NONE || phone_handle == HCI_CON_HANDLE_INVALID){
        stats.offline[hand]++;
        pump_kick();
        return;
    }
    /* FF12 needs room for a whole record (FF14 for a header and one record); below that
     * nothing is sent. Count the sample here instead of queueing something that can never
     * be delivered. */
    if (!stream_mtu_ok()){
        stats.mtu_skipped[hand]++;
        return;
    }
    /* Diagnostic build option, off by default (see wmr_config.h). */
    if (stream_mode == WMR_STREAM_DECODED &&
        !wmr_decoded_decimate_keep(sequence, WMR_CFG_FF12_DECIMATE)){
        stats.decimated[hand]++;
        return;
    }
    wmr_frame_t frame;
    if (!length || length > WMR_CFG_MAX_FRAME){
        stats.drop[hand]++;
        return;
    }
    frame.sequence = sequence;
    /* Receive time is captured here, at arrival. Decoding is deferred to the
     * pump, so the HID callback only copies bytes. */
    frame.rx_time_us = (uint64_t) esp_timer_get_time();
    frame.length = length;
    frame.hand = (uint8_t) hand;
    memcpy(frame.data, report, length);
    if (!wmr_queue_push(&queue, &frame)){
        stats.drop[hand]++;
        if (wmr_episode_note_drop(&drop_episode)){
            char line[128];
            wmr_diag_format_drop(line, sizeof(line), wmr_controller_name(hand),
                                 wmr_stream_mode_name(stream_mode),
                                 att_server_get_mtu(phone_handle), queue.count,
                                 WMR_CFG_QUEUE_CAPACITY, stats.errors);
            printf("%s\n", line);
        }
        return;
    }
    pump_kick();
}

/* FF12: decode the head frame now (not in the HID callback) and send one
 * 56-byte record. A frame that cannot be sent or decoded is removed and counted,
 * so it never blocks the queue; a failed notify keeps it for the retry timer. */
static void pump_send_decoded(const wmr_frame_t *frame){
    if (!wmr_decoded_mtu_ok(att_server_get_mtu(phone_handle))){
        /* Queued before the MTU was known to be too small. */
        stats.mtu_skipped[frame->hand]++;
        wmr_queue_pop(&queue);
        pump_kick();
        return;
    }
    uint8_t record[WMR_DECODED_SIZE];
    if (!wmr_decoded_from_frame(frame, record)){
        stats.decode_errors[frame->hand]++;
        wmr_queue_pop(&queue);
        pump_kick();
        return;
    }
    uint8_t result = att_server_notify(phone_handle, DECODED_VALUE_HANDLE, record, sizeof(record));
    if (result != ERROR_CODE_SUCCESS){
        stats.errors++;
        return;     /* The retry timer tries again; a send error never discards a frame. */
    }
    stats.notifications++;
    stats.tx[frame->hand]++;
    wmr_queue_pop(&queue);
    pump_kick();
}

/* FF14: decode up to a notification's worth of queued frames and send them as one batch.
 * Nothing leaves the queue until the notification is accepted, so a send error loses
 * nothing and the retry sends the same records again. A frame that is not a valid input
 * report is removed and counted (dec_err) and does not occupy a slot in the batch. */
static void pump_send_batch(void){
    const unsigned max_count = wmr_batch_max_count(att_server_get_mtu(phone_handle));
    if (!max_count){
        /* Queued before the MTU was known to be too small. */
        for (const wmr_frame_t *f; (f = wmr_queue_peek(&queue)) != NULL; wmr_queue_pop(&queue))
            stats.mtu_skipped[f->hand]++;
        return;
    }
    wmr_batch_t batch;
    wmr_batch_init(&batch, max_count);
    bool valid[WMR_BATCH_MAX_COUNT];
    const unsigned take = queue.count < max_count ? queue.count : max_count;
    for (unsigned i = 0; i < take; i++){
        uint8_t record[WMR_DECODED_SIZE];
        const wmr_frame_t *frame = wmr_queue_peek_at(&queue, i);
        valid[i] = wmr_decoded_from_frame(frame, record) && wmr_batch_add(&batch, record);
    }
    if (batch.count){
        uint8_t result = att_server_notify(phone_handle, BATCHED_VALUE_HANDLE, batch.data, wmr_batch_size(&batch));
        if (result != ERROR_CODE_SUCCESS){
            stats.errors++;
            return;     /* The retry timer tries again; a send error never discards a frame. */
        }
        stats.notifications++;
        stats.batches_tx++;
        stats.records_in_batches += batch.count;
        if (batch.count > stats.batch_max) stats.batch_max = batch.count;
        if (take == max_count) stats.batch_full_flushes++;
    }
    for (unsigned i = 0; i < take; i++){
        const wmr_frame_t *frame = wmr_queue_peek(&queue);
        if (valid[i]) stats.tx[frame->hand]++;
        else          stats.decode_errors[frame->hand]++;
        wmr_queue_pop(&queue);
    }
    pump_kick();
}

/* FF15: send the pending echo. Not counted in `notifications` (that counter stays the
 * stream's, so the FF14 identity holds); a failed notify keeps it for the retry timer. */
static void pump_send_echo(void){
    uint8_t value[WMR_ECHO_RESPONSE_SIZE];
    if (!wmr_echo_build(&echo, (uint64_t) esp_timer_get_time(), value)) return;
    uint8_t result = att_server_notify(phone_handle, ECHO_VALUE_HANDLE, value, sizeof(value));
    if (result != ERROR_CODE_SUCCESS){
        stats.errors++;
        return;
    }
    wmr_echo_sent(&echo);
    pump_kick();
}

/* FF13: send the 16-byte header (no data) for one hand whose state changed. Not counted in
 * `notifications`; a failed notify keeps it pending for the retry timer. */
static void pump_send_cfg(void){
    for (unsigned hand = 0; hand < WMR_NUM_CONTROLLERS; hand++){
        if (!cfg_notify_pending[hand]) continue;
        const uint8_t *blob;
        uint32_t total;
        wmr_cfg_blob_state_t state = wmr_cfgmgr_get(hand, &blob, &total);
        uint8_t value[WMR_CFG_FF13_HEADER_SIZE + WMR_CFG_FF13_WINDOW_MAX];
        wmr_cfg_pack_window((uint8_t) hand, state, blob, total, 0, value);
        uint8_t result = att_server_notify(phone_handle, CFG_VALUE_HANDLE, value, WMR_CFG_FF13_HEADER_SIZE);
        if (result != ERROR_CODE_SUCCESS){
            stats.errors++;
            return;
        }
        cfg_notify_pending[hand] = false;
        pump_kick();
        return;
    }
}

static void build_ctrl_status(uint8_t *out){
    wmr_ctrl_status_t s;
    memset(&s, 0, sizeof(s));
    s.version = WMR_CTRL_VERSION;
    for (unsigned hand = 0; hand < WMR_NUM_CONTROLLERS; hand++){
        wmr_controller_status_t cs;
        wmr_controller_get_status(hand, &cs);
        s.hands[hand].connected            = cs.connected;
        s.hands[hand].connecting           = cs.connecting;
        s.hands[hand].descriptor_available = cs.descriptor_available;
        s.hands[hand].address_source       = (uint8_t) cs.address_source;
        s.hands[hand].model                = (uint8_t) cs.model;
        memcpy(s.hands[hand].address, cs.address, 6);
    }
    s.discovery_state = wmr_controller_discovery_phase();
    s.discovery_round = wmr_controller_discovery_round();
    s.last_command    = ctrl_last_command;
    s.last_result     = ctrl_last_result;
    wmr_ctrl_pack_status(&s, out);
}

static void pump_send_ctrl(void){
    if (!ctrl_notify_pending || !ctrl_subscribed || phone_handle == HCI_CON_HANDLE_INVALID) return;
    uint8_t status[WMR_CTRL_STATUS_SIZE];
    build_ctrl_status(status);
    uint8_t result = att_server_notify(phone_handle, CTRL_VALUE_HANDLE, status, sizeof(status));
    if (result != ERROR_CODE_SUCCESS){
        stats.errors++;
        return;
    }
    memcpy(ctrl_last_notified, status, sizeof(status));
    ctrl_notify_pending = false;
    pump_kick();
}

static bool pump_send_hid(uint64_t now);

/* Send the pending echo (FF15) if there is one, otherwise one fragment (FF11), one record
 * (FF12) or one batch (FF14) from the head of the queue. Runs on ATT_EVENT_CAN_SEND_NOW. */
static void pump_send_one(void){
    send_pending = false;
    if (phone_handle == HCI_CON_HANDLE_INVALID) return;
    if (echo.pending){
        pump_send_echo();
        return;
    }
    if (ctrl_notify_pending){
        pump_send_ctrl();
        return;
    }
    if (cfg_notify_owed()){
        pump_send_cfg();
        return;
    }
    const uint64_t now = (uint64_t) esp_timer_get_time();
    /* Alternate with the GATT stream when both are ready: HID never takes two
     * send opportunities in a row while a stream frame waits, so FF11-FF14
     * keep their share of the link. */
    const bool stream_ready = stream_mode != WMR_STREAM_NONE && queue.count > 0 &&
        (stream_mode != WMR_STREAM_BATCHED || batch_due());
    static bool last_send_hid;
    if (hid_subscribed && wmr_hid_report_due(&hid, now) &&
        (!stream_ready || !last_send_hid)){
        last_send_hid = pump_send_hid(now);
        return;
    }
    if (!stream_ready) return;
    last_send_hid = false;
    const wmr_frame_t *frame = wmr_queue_peek(&queue);
    if (!frame) return;
    if (stream_mode == WMR_STREAM_DECODED){
        pump_send_decoded(frame);
        return;
    }
    if (stream_mode == WMR_STREAM_BATCHED){
        pump_send_batch();
        return;
    }
    uint16_t capacity = wmr_proto_notification_capacity(att_server_get_mtu(phone_handle));
    if (!capacity) return;
    uint8_t packet[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk = 0;
    uint16_t size = wmr_proto_encode_fragment(frame, send_offset, capacity, packet, &chunk);
    if (!size) return;
    uint8_t result = att_server_notify(phone_handle, STREAM_VALUE_HANDLE, packet, size);
    if (result != ERROR_CODE_SUCCESS){
        stats.errors++;
        return;     /* The retry timer tries again; a send error never discards a frame. */
    }
    stats.notifications++;
    send_offset += chunk;
    if (send_offset == frame->length){
        stats.tx[frame->hand]++;
        send_offset = 0;
        wmr_queue_pop(&queue);
    }
    pump_kick();
}

/* HID-over-GATT: one input report per can-send-now. hids_device writes the
 * Report characteristic; the report ID is in its Report Reference descriptor,
 * so the value is the 9-byte payload only. */
static bool pump_send_hid(uint64_t now){
    if (!hid_subscribed) return false;
    uint8_t payload[WMR_HID_REPORT_SIZE];
    if (!wmr_hid_take_report(&hid, now, payload)){
        pump_kick();
        return false;
    }
    uint8_t result = hids_device_send_input_report_for_id(phone_handle, WMR_HID_REPORT_ID,
                                                          payload, sizeof(payload));
    if (result != ERROR_CODE_SUCCESS){
        stats.hid_errors++;
        if (!hid_error_logged){
            hid_error_logged = true;
            printf("HID input report send failed: 0x%02x (subscribed=%u)\n",
                   result, hid_subscribed ? 1u : 0u);
        }
        hid.force = true;        /* retry the same report on the next pump tick */
        return false;
    }
    stats.hid_reports++;
    pump_kick();
    return true;
}

/* HIDS events tell us when the host enables the input report. Until then the
 * bridge sends nothing: Android's HID host is still discovering services, and
 * input notifications in that window make it drop the link. */
static void hid_packet_handler(uint8_t packet_type, uint16_t channel,
                               uint8_t *packet, uint16_t size){
    (void) channel;
    if (packet_type != HCI_EVENT_PACKET || size < 5) return;
    if (hci_event_packet_get_type(packet) != HCI_EVENT_HIDS_META) return;
    switch (hci_event_hids_meta_get_subevent_code(packet)){
        case HIDS_SUBEVENT_INPUT_REPORT_ENABLE: {
            hci_con_handle_t handle = hids_subevent_input_report_enable_get_con_handle(packet);
            if (handle != phone_handle) break;
            hid_subscribed = hids_subevent_input_report_enable_get_enable(packet) != 0;
            hid_error_logged = false;
            printf("HID input report %s (report id %u)\n",
                   hid_subscribed ? "enabled" : "disabled",
                   hids_subevent_input_report_enable_get_report_id(packet));
            if (hid_subscribed){
                wmr_hid_reset(&hid);    /* first notification is a neutral report */
                pump_kick();
            }
            break;
        }
        case HIDS_SUBEVENT_PROTOCOL_MODE:
            if (hids_subevent_protocol_mode_get_con_handle(packet) != phone_handle) break;
            printf("HID protocol mode: %s\n",
                   hids_subevent_protocol_mode_get_protocol_mode(packet) ? "report" : "boot");
            break;
        case HIDS_SUBEVENT_SUSPEND:
        case HIDS_SUBEVENT_EXIT_SUSPEND: {
            const uint8_t subevent = hci_event_hids_meta_get_subevent_code(packet);
            hci_con_handle_t handle = (subevent == HIDS_SUBEVENT_SUSPEND)
                ? hids_subevent_suspend_get_con_handle(packet)
                : hids_subevent_exit_suspend_get_con_handle(packet);
            if (handle != phone_handle) break;
            printf("HID %s\n", subevent == HIDS_SUBEVENT_SUSPEND ? "suspended" : "resumed");
            break;
        }
        default:
            break;
    }
}

static void pump_retry_tick(btstack_timer_source_t *timer){
    pump_kick();
    btstack_run_loop_set_timer(timer, WMR_CFG_PUMP_RETRY_MS);
    btstack_run_loop_add_timer(timer);
}

static void status_tick_handler(btstack_timer_source_t *timer){
    status_tick++;
    if (ctrl_subscribed && phone_handle != HCI_CON_HANDLE_INVALID){
        uint8_t current[WMR_CTRL_STATUS_SIZE];
        build_ctrl_status(current);
        if (memcmp(current, ctrl_last_notified, sizeof(current)) != 0){
            ctrl_notify_pending = true;
        }
    }
    pump_kick();
    btstack_run_loop_set_timer(timer, WMR_CFG_STATUS_TICK_MS);
    btstack_run_loop_add_timer(timer);
}

/* ---- Introspection ------------------------------------------------------ */

const wmr_stats_t *wmr_transport_stats(void){
    return &stats;
}

const wmr_echo_state_t *wmr_transport_echo(void){
    return &echo;
}

void wmr_transport_get_info(wmr_transport_info_t *info){
    info->phone_connected  = phone_handle != HCI_CON_HANDLE_INVALID;
    info->advertising      = advertising_active;
    info->subscribed       = stream_mode != WMR_STREAM_NONE;
    info->hid_subscribed   = hid_subscribed;
    info->stream           = stream_mode;
    info->mtu              = info->phone_connected ? att_server_get_mtu(phone_handle) : 0;
    info->queue_count      = queue.count;
    info->queue_capacity   = WMR_CFG_QUEUE_CAPACITY;
    info->queue_high_water = queue.high_water;
    info->conn_known       = conn_known;
    info->conn             = conn_now;
    info->conn_update_pending = conn_update_pending;
}

void wmr_transport_print_dle(void){
    if (!data_length_valid){
        printf("DLE: no data-length change seen since connect\n");
        return;
    }
    printf("DLE: tx %u octets / %u us, rx %u octets / %u us\n",
           (unsigned) data_length_tx_octets, (unsigned) data_length_tx_time_us,
           (unsigned) data_length_rx_octets, (unsigned) data_length_rx_time_us);
}

/* ---- ATT callbacks ------------------------------------------------------ */

/* 16-byte status value returned when the phone reads FF11:
 *   [0]      version (1)
 *   [1]      bit0 LEFT connected, bit1 RIGHT connected
 *   [2]      bit0 LEFT descriptor available, bit1 RIGHT descriptor available
 *   [3]      reserved (0)
 *   [4..7]   uint32 LE seconds since boot
 *   [8..11]  uint32 LE LEFT reports this connection
 *   [12..15] uint32 LE RIGHT reports this connection */
static void build_status(uint8_t *status){
    wmr_controller_status_t left, right;
    wmr_controller_get_status(WMR_HAND_LEFT, &left);
    wmr_controller_get_status(WMR_HAND_RIGHT, &right);
    status[0] = 1;
    status[1] = (left.connected ? 1 : 0) | (right.connected ? 2 : 0);
    status[2] = (left.descriptor_available ? 1 : 0) | (right.descriptor_available ? 2 : 0);
    status[3] = 0;
    little_endian_store_32(status, 4, status_tick);
    little_endian_store_32(status, 8, left.reports);
    little_endian_store_32(status, 12, right.reports);
}

/* Which stream a CCC handle belongs to. */
static bool ccc_stream(uint16_t attribute, wmr_stream_t *which){
    if (attribute == STREAM_CCC_HANDLE)  { *which = WMR_STREAM_RAW;     return true; }
    if (attribute == DECODED_CCC_HANDLE) { *which = WMR_STREAM_DECODED; return true; }
    if (attribute == BATCHED_CCC_HANDLE) { *which = WMR_STREAM_BATCHED; return true; }
    return false;
}

static uint16_t att_read(hci_con_handle_t handle, uint16_t attribute,
                         uint16_t offset, uint8_t *buffer, uint16_t buffer_size){
    (void)handle;
    if (attribute == VERSION_VALUE_HANDLE || attribute == FW_REVISION_VALUE_HANDLE){
        const esp_app_desc_t *app = esp_app_get_description();
        return att_read_callback_handle_blob((const uint8_t *) app->version,
                                             (uint16_t) strlen(app->version),
                                             offset, buffer, buffer_size);
    }
    if (attribute == STREAM_VALUE_HANDLE){
        uint8_t status[STATUS_SIZE];
        build_status(status);
        return att_read_callback_handle_blob(status, sizeof(status), offset, buffer, buffer_size);
    }
    wmr_stream_t which;
    if (ccc_stream(attribute, &which)){
        uint8_t ccc[2] = {wmr_stream_mode_ccc_enabled(stream_mode, which) ? 1 : 0, 0};
        return att_read_callback_handle_blob(ccc, sizeof(ccc), offset, buffer, buffer_size);
    }
    if (attribute == ECHO_CCC_HANDLE){
        uint8_t ccc[2] = {echo_subscribed ? 1 : 0, 0};
        return att_read_callback_handle_blob(ccc, sizeof(ccc), offset, buffer, buffer_size);
    }
    if (attribute == CFG_VALUE_HANDLE) return cfg_read(offset, buffer, buffer_size);
    if (attribute == CFG_CCC_HANDLE){
        uint8_t ccc[2] = {cfg_subscribed ? 1 : 0, 0};
        return att_read_callback_handle_blob(ccc, sizeof(ccc), offset, buffer, buffer_size);
    }
    if (attribute == CTRL_VALUE_HANDLE){
        uint8_t status[WMR_CTRL_STATUS_SIZE];
        build_ctrl_status(status);
        return att_read_callback_handle_blob(status, sizeof(status), offset, buffer, buffer_size);
    }
    if (attribute == CTRL_CCC_HANDLE){
        uint8_t ccc[2] = {ctrl_subscribed ? 1 : 0, 0};
        return att_read_callback_handle_blob(ccc, sizeof(ccc), offset, buffer, buffer_size);
    }
    return 0;
}

/* FF13: a phone connect or disconnect clears the CCC, the cursor and owed notifications. */
static void cfg_reset_connection(void){
    cfg_subscribed = false;
    cfg_cursor_hand = 0;
    cfg_cursor_offset = 0;
    memset(cfg_notify_pending, 0, sizeof(cfg_notify_pending));
}

/* FF17: a phone connect or disconnect clears the CCC, pending notifications and last state. */
static void ctrl_reset_connection(void){
    ctrl_subscribed = false;
    ctrl_notify_pending = false;
    memset(ctrl_last_notified, 0, sizeof(ctrl_last_notified));
}

/* FF13 value read: the window at the connection's cursor. The ATT read offset (long reads)
 * is separate from the cursor offset. */
static uint16_t cfg_read(uint16_t read_offset, uint8_t *buffer, uint16_t buffer_size){
    const uint8_t *blob;
    uint32_t total;
    wmr_cfg_blob_state_t state = wmr_cfgmgr_get(cfg_cursor_hand, &blob, &total);
    uint8_t value[WMR_CFG_FF13_HEADER_SIZE + WMR_CFG_FF13_WINDOW_MAX];
    size_t size = wmr_cfg_pack_window(cfg_cursor_hand, state, blob, total, cfg_cursor_offset, value);
    return att_read_callback_handle_blob(value, (uint16_t) size, read_offset, buffer, buffer_size);
}

/* FF13 cursor write (with response): hand u8, offset u32. An invalid hand or length fails
 * with ATT error 0x80, like the stream CCC refusals. */
static int cfg_write(const uint8_t *buffer, uint16_t buffer_size){
    uint8_t hand;
    uint32_t offset;
    if (!wmr_cfg_parse_cursor(buffer, buffer_size, &hand, &offset)) return 0x80;
    cfg_cursor_hand = hand;
    cfg_cursor_offset = offset;
    return 0;
}

static int cfg_ccc_write(hci_con_handle_t handle, uint16_t transaction_mode, uint16_t offset,
                         const uint8_t *buffer, uint16_t buffer_size){
    if (transaction_mode != ATT_TRANSACTION_MODE_NONE) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (offset != 0) return ATT_ERROR_INVALID_OFFSET;
    if (buffer_size != 2) return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH;
    uint16_t value = little_endian_read_16(buffer, 0);
    if (value > 1) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (phone_handle != HCI_CON_HANDLE_INVALID && handle != phone_handle)
        return ATT_ERROR_WRITE_NOT_PERMITTED;
    phone_handle = handle;
    cfg_subscribed = value == 1;
    if (!cfg_subscribed) memset(cfg_notify_pending, 0, sizeof(cfg_notify_pending));
    printf("BLE config (FF13) notifications %s\n", cfg_subscribed ? "enabled" : "disabled");
    return 0;
}

/* FF15 echo request (write without response: the return value is not sent). Stamped here,
 * in the ATT callback, so the turnaround starts when the request arrived. */
static int echo_write(hci_con_handle_t handle, const uint8_t *buffer, uint16_t buffer_size){
    const uint64_t now = (uint64_t) esp_timer_get_time();
    const bool subscribed = echo_subscribed && handle == phone_handle;
    const uint16_t depth = queue.count > UINT16_MAX ? UINT16_MAX : (uint16_t) queue.count;
    if (wmr_echo_on_write(&echo, subscribed, buffer, buffer_size, now, depth) == WMR_ECHO_ACCEPTED)
        pump_kick();
    return 0;
}

/* FF15 CCC: independent of the FF11/FF12/FF14 exclusion. */
static int echo_ccc_write(hci_con_handle_t handle, uint16_t transaction_mode, uint16_t offset,
                          const uint8_t *buffer, uint16_t buffer_size){
    if (transaction_mode != ATT_TRANSACTION_MODE_NONE) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (offset != 0) return ATT_ERROR_INVALID_OFFSET;
    if (buffer_size != 2) return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH;
    uint16_t value = little_endian_read_16(buffer, 0);
    if (value > 1) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (phone_handle != HCI_CON_HANDLE_INVALID && handle != phone_handle)
        return ATT_ERROR_WRITE_NOT_PERMITTED;
    phone_handle = handle;
    echo_subscribed = value == 1;
    if (!echo_subscribed) wmr_echo_cancel(&echo);
    printf("BLE echo notifications %s\n", echo_subscribed ? "enabled" : "disabled");
    return 0;
}

/* FF17 control write: opcode + optional hand. */
static int ctrl_write(const uint8_t *buffer, uint16_t buffer_size){
    uint8_t opcode = 0;
    uint8_t hand = 0;
    if (!wmr_ctrl_parse_command(buffer, buffer_size, &opcode, &hand)){
        ctrl_last_command = buffer_size > 0 ? buffer[0] : 0;
        ctrl_last_result = WMR_CTRL_RES_INVALID_PARAM;
        if (ctrl_subscribed){
            ctrl_notify_pending = true;
            pump_kick();
        }
        return wmr_ctrl_res_to_att_error(WMR_CTRL_RES_INVALID_PARAM);
    }
    ctrl_last_command = opcode;
    uint8_t res = WMR_CTRL_RES_OK;
    switch (opcode){
        case WMR_CTRL_OP_DISCOVER: {
            wmr_discovery_start_result_t dr = wmr_controller_discover();
            if (dr == WMR_DISCOVERY_STARTED) res = WMR_CTRL_RES_OK;
            else if (dr == WMR_DISCOVERY_BUSY) res = WMR_CTRL_RES_BUSY;
            else if (dr == WMR_DISCOVERY_NOTHING_TO_DO) res = WMR_CTRL_RES_NOTHING_TO_DO;
            break;
        }
        case WMR_CTRL_OP_CONNECT: {
            wmr_connect_result_t cr = wmr_controller_connect(hand);
            if (cr == WMR_CONNECT_OK) res = WMR_CTRL_RES_OK;
            else if (cr == WMR_CONNECT_BUSY) res = WMR_CTRL_RES_BUSY;
            else if (cr == WMR_CONNECT_NO_ADDRESS) res = WMR_CTRL_RES_NO_ADDRESS;
            else if (cr == WMR_CONNECT_NOT_ENABLED) res = WMR_CTRL_RES_NOT_ENABLED;
            break;
        }
        case WMR_CTRL_OP_DISCONNECT: {
            wmr_disconnect_result_t dr = wmr_controller_disconnect(hand);
            if (dr == WMR_DISCONNECT_OK) res = WMR_CTRL_RES_OK;
            else if (dr == WMR_DISCONNECT_NOT_CONNECTED) res = WMR_CTRL_RES_NOT_CONNECTED;
            break;
        }
        case WMR_CTRL_OP_FORGET_ADDRS: {
            wmr_forget_result_t fr = wmr_controller_forget_addresses();
            if (fr == WMR_FORGET_OK) res = WMR_CTRL_RES_OK;
            else if (fr == WMR_FORGET_BUSY) res = WMR_CTRL_RES_BUSY;
            break;
        }
        case WMR_CTRL_OP_FORGET_BOND: {
            wmr_bond_reset_result_t br = wmr_controller_forget_bond(hand);
            if (br == WMR_BOND_RESET_OK) res = WMR_CTRL_RES_OK;
            else if (br == WMR_BOND_RESET_BUSY) res = WMR_CTRL_RES_BUSY;
            else if (br == WMR_BOND_RESET_NO_ADDRESS) res = WMR_CTRL_RES_NO_ADDRESS;
            break;
        }
        default:
            res = WMR_CTRL_RES_INVALID_PARAM;
            break;
    }
    ctrl_last_result = res;
    if (ctrl_subscribed){
        ctrl_notify_pending = true;
        pump_kick();
    }
    return wmr_ctrl_res_to_att_error(res);
}

/* FF17 CCC: independent of the FF11/FF12/FF14 exclusion. */
static int ctrl_ccc_write(hci_con_handle_t handle, uint16_t transaction_mode, uint16_t offset,
                          const uint8_t *buffer, uint16_t buffer_size){
    if (transaction_mode != ATT_TRANSACTION_MODE_NONE) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (offset != 0) return ATT_ERROR_INVALID_OFFSET;
    if (buffer_size != 2) return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH;
    uint16_t value = little_endian_read_16(buffer, 0);
    if (value > 1) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (phone_handle != HCI_CON_HANDLE_INVALID && handle != phone_handle)
        return ATT_ERROR_WRITE_NOT_PERMITTED;
    phone_handle = handle;
    ctrl_subscribed = value == 1;
    if (!ctrl_subscribed){
        ctrl_notify_pending = false;
        memset(ctrl_last_notified, 0, sizeof(ctrl_last_notified));
    }
    printf("BLE control (FF17) notifications %s\n", ctrl_subscribed ? "enabled" : "disabled");
    return 0;
}

static int att_write(hci_con_handle_t handle, uint16_t attribute,
                     uint16_t transaction_mode, uint16_t offset,
                     uint8_t *buffer, uint16_t buffer_size){
    if (attribute == ECHO_VALUE_HANDLE) return echo_write(handle, buffer, buffer_size);
    if (attribute == CFG_VALUE_HANDLE){
        if (transaction_mode != ATT_TRANSACTION_MODE_NONE) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
        if (offset != 0) return ATT_ERROR_INVALID_OFFSET;
        return cfg_write(buffer, buffer_size);
    }
    if (attribute == CFG_CCC_HANDLE)
        return cfg_ccc_write(handle, transaction_mode, offset, buffer, buffer_size);
    if (attribute == ECHO_CCC_HANDLE)
        return echo_ccc_write(handle, transaction_mode, offset, buffer, buffer_size);
    if (attribute == CTRL_VALUE_HANDLE){
        if (transaction_mode != ATT_TRANSACTION_MODE_NONE) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
        if (offset != 0) return ATT_ERROR_INVALID_OFFSET;
        return ctrl_write(buffer, buffer_size);
    }
    if (attribute == CTRL_CCC_HANDLE)
        return ctrl_ccc_write(handle, transaction_mode, offset, buffer, buffer_size);
    wmr_stream_t which;
    if (!ccc_stream(attribute, &which)) return 0;
    if (transaction_mode != ATT_TRANSACTION_MODE_NONE) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (offset != 0) return ATT_ERROR_INVALID_OFFSET;
    if (buffer_size != 2) return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH;
    uint16_t value = little_endian_read_16(buffer, 0);
    if (value > 1) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (phone_handle != HCI_CON_HANDLE_INVALID && handle != phone_handle)
        return ATT_ERROR_WRITE_NOT_PERMITTED;
    /* FF11, FF12 and FF14 are mutually exclusive: enabling one while another is
     * active fails; switching means disabling the active one first. */
    wmr_stream_ccc_result_t result = wmr_stream_mode_ccc_write(stream_mode, which, value == 1);
    if (!result.accepted){
        printf("BLE %s notifications refused: %s stream is active (ATT error 0x%02x)\n",
               wmr_stream_mode_name(which), wmr_stream_mode_name(stream_mode), result.att_error);
        return result.att_error;
    }
    phone_handle = handle;
    if (result.mode != stream_mode){
        /* Subscription transitions start a fresh live stream, not a backlog. */
        queue_reset();
        stream_mode = result.mode;
    }
    printf("BLE %s notifications %s\n", wmr_stream_mode_name(which), value == 1 ? "enabled" : "disabled");
    return 0;
}

/* ATT server callback. Only ATT_EVENT_CAN_SEND_NOW is handled here. The
 * event payload layout is deliberately not parsed: there is a single phone
 * link, and the pump re-reads its own state. HCI events are handled solely
 * through wmr_transport_on_hci_event so that LE connect / disconnect are
 * processed exactly once. */
static void att_packet_handler(uint8_t packet_type, uint16_t channel,
                               uint8_t *packet, uint16_t size){
    (void)channel;
    if (packet_type != HCI_EVENT_PACKET || size < 2) return;
    const uint8_t event = hci_event_packet_get_type(packet);
    if (event == ATT_EVENT_MTU_EXCHANGE_COMPLETE){
        printf("BLE ATT MTU: %u (handle=0x%04x)\n",
               att_event_mtu_exchange_complete_get_MTU(packet),
               att_event_mtu_exchange_complete_get_handle(packet));
        return;
    }
    if (event == ATT_EVENT_CAN_SEND_NOW){
        pump_send_one();
    }
}

/* ---- 0.3 phone-link security -------------------------------------------- */

/* What the bond database holds for this link. A phone reconnects from a
 * resolvable private address; the host has to resolve it to this entry, or the
 * controller's LTK request fails with 0x06 and Android falls back to a fresh
 * pairing. The identity address and the IRK presence are therefore the two
 * facts needed to read a re-encryption failure from a field log. */
static void log_bond_state(const char *what, uint16_t handle){
    int index = sm_le_device_index(handle);
    if (index < 0){
        printf("BLE %s: no bond entry (handle=0x%04x)\n", what, handle);
        return;
    }
    int addr_type = -1;
    bd_addr_t addr;
    sm_key_t irk;
    le_device_db_info(index, &addr_type, addr, irk);
    bool irk_present = false;
    for (unsigned i = 0; i < sizeof(irk); i++){
        if (irk[i]){ irk_present = true; break; }
    }
    printf("BLE %s: bond %d identity=%s type=%s irk=%s\n", what, index,
           bd_addr_to_str(addr),
           addr_type == BD_ADDR_TYPE_LE_PUBLIC ? "public" : "random",
           irk_present ? "yes" : "no");
}

/* Act on the policy's verdict. ACCEPT keeps the phone; REFUSE disconnects it, so an
 * unbonded central cannot sit on the link and block the intended phone. */
static void secure_apply(const wmr_secure_event_t *event){
    switch (wmr_secure_decide(event)){
        case WMR_SECURE_ACCEPT:
            if (phone_secure) return;
            phone_secure = true;
            btstack_run_loop_remove_timer(&pair_timer);
            printf("BLE phone secure: bonded LE Secure Connections link\n");
            break;
        case WMR_SECURE_REFUSE:
            phone_secure = false;
            btstack_run_loop_remove_timer(&pair_timer);
            printf("BLE phone refused: no bonded LE Secure Connections link; disconnecting\n");
            if (phone_handle != HCI_CON_HANDLE_INVALID)
                gap_disconnect(phone_handle);
            break;
        case WMR_SECURE_REPAIR: {
            /* Android forgot the bridge, so our stored key cannot re-encrypt the
             * link. Drop it and stay connected: the phone's fresh pairing then
             * completes instead of being cut off by our own disconnect. */
            int index = sm_le_device_index(event->handle);
            if (index >= 0) le_device_db_remove((uint8_t) index);
            phone_secure = false;
            printf("BLE phone bond stale: deleted the stored key, waiting for a new pairing\n");
            btstack_run_loop_remove_timer(&pair_timer);
            btstack_run_loop_set_timer(&pair_timer, WMR_CFG_BLE_PAIR_TIMEOUT_MS);
            btstack_run_loop_add_timer(&pair_timer);
            break;
        }
        default:
            break;
    }
}

/* No paired + bonded link within WMR_CFG_BLE_PAIR_TIMEOUT_MS: refuse the central.
 * The actual link encryption state is queried from the controller: a bonded phone
 * whose SM event was missed can still be served, while an encrypt-only link is not. */
static void pair_timer_fire(btstack_timer_source_t *timer){
    (void) timer;
    if (phone_handle == HCI_CON_HANDLE_INVALID) return;
    wmr_secure_event_t event = wmr_secure_timeout_event(
        phone_handle, sm_le_device_index(phone_handle) >= 0,
        gap_encryption_key_size(phone_handle));
    secure_apply(&event);
}

void wmr_transport_on_sm_event(uint8_t *packet, uint16_t size){
    if (size < 2) return;
    wmr_secure_event_t event = {
        .type = WMR_SECURE_PAIRING_COMPLETE,
        .handle = WMR_SECURE_NO_HANDLE,
        .phone_handle = (phone_handle == HCI_CON_HANDLE_INVALID)
                            ? WMR_SECURE_NO_HANDLE : phone_handle,
        .success = false,
        .bonded = false,
        .encrypted = false,
    };
    switch (hci_event_packet_get_type(packet)){
        case SM_EVENT_JUST_WORKS_REQUEST: {
            /* The bridge has no display and no keypad, so LESC uses Just Works.
             * Only the current phone may pair; anyone else is declined. */
            hci_con_handle_t handle = sm_event_just_works_request_get_handle(packet);
            if (handle != phone_handle){
                sm_bonding_decline(handle);
                break;
            }
            printf("BLE pairing: Just Works confirmed (no MITM protection)\n");
            sm_just_works_confirm(handle);
            break;
        }
        case SM_EVENT_PAIRING_COMPLETE:
            event.type = WMR_SECURE_PAIRING_COMPLETE;
            event.handle = sm_event_pairing_complete_get_handle(packet);
            event.success = sm_event_pairing_complete_get_status(packet) == ERROR_CODE_SUCCESS;
            event.bonded = sm_le_device_index(event.handle) >= 0;
            if (event.success){
                printf("BLE pairing complete: %s\n", event.bonded ? "bond stored" : "no bond stored");
                if (event.bonded) log_bond_state("pairing complete", event.handle);
            } else {
                printf("BLE pairing failed: status=0x%02x reason=0x%02x\n",
                       sm_event_pairing_complete_get_status(packet),
                       sm_event_pairing_complete_get_reason(packet));
            }
            secure_apply(&event);
            break;
        case SM_EVENT_REENCRYPTION_COMPLETE:
            event.type = WMR_SECURE_REENCRYPTION_COMPLETE;
            event.handle = sm_event_reencryption_complete_get_handle(packet);
            event.success =
                sm_event_reencryption_complete_get_status(packet) == ERROR_CODE_SUCCESS;
            event.bonded = sm_le_device_index(event.handle) >= 0;
            event.stale_bond = sm_event_reencryption_complete_get_status(packet) ==
                               ERROR_CODE_PIN_OR_KEY_MISSING;
            if (!event.success){
                printf("BLE re-encryption failed: status=0x%02x\n",
                       sm_event_reencryption_complete_get_status(packet));
                log_bond_state("re-encryption failed", event.handle);
            }
            secure_apply(&event);
            break;
        default:
            break;
    }
}

static btstack_packet_callback_registration_t sm_registration;

static void sm_packet_handler(uint8_t packet_type, uint16_t channel,
                              uint8_t *packet, uint16_t size){
    (void) channel;
    if (packet_type != HCI_EVENT_PACKET) return;
    wmr_transport_on_sm_event(packet, size);
}

/* ---- HCI events --------------------------------------------------------- */

/* Ask for connectable advertising. Idempotent: BTstack ignores an enable while
 * advertising is already enabled, so this only logs when it turns advertising back on.
 * Called whenever a radio-heavy phase ends, because a phone that tries to connect while
 * the bridge advertises nothing fails with no line in the bridge log at all. */
static void advertising_enable(const char *reason){
    bool was_requested = advertising_requested;
    advertising_requested = true;
    gap_advertisements_enable(1);
    if (!was_requested){
        printf("BLE advertising: on (%s)\n", reason);
    }
}

void wmr_transport_advertising_ready(const char *reason){
    advertising_enable(reason);
}

void wmr_transport_on_hci_event(uint8_t *packet, uint16_t size){
    switch (hci_event_packet_get_type(packet)){
        case BTSTACK_EVENT_STATE:
            if (btstack_event_state_get_state(packet) == HCI_STATE_WORKING){
                advertising_requested = true;
                gap_advertisements_enable(1);
                printf("BLE advertising: %s (FF11 raw stream, FF12 decoded stream, FF14 batched decoded stream, FF15 echo, FF13 config block, FF17 control)\n",
                       WMR_CFG_BLE_NAME);
            }
            break;
        case HCI_EVENT_COMMAND_COMPLETE:
            /* LE Set Advertising Enable: a controller-side rejection would leave the phone
             * unable to connect while the device still looks advertised in a cached scan. */
            if (size >= 6 &&
                hci_event_command_complete_get_command_opcode(packet) ==
                    HCI_OPCODE_HCI_LE_SET_ADVERTISE_ENABLE){
                uint8_t status =
                    hci_event_command_complete_get_return_parameters(packet)[0];
                advertising_active = status == ERROR_CODE_SUCCESS;
                if (!advertising_active){
                    printf("BLE advertising: controller refused to enable (status=0x%02x)\n",
                           status);
                }
            }
            break;
        case HCI_EVENT_LE_META:
            /* Legacy and enhanced LE Connection Complete share these initial
             * fields: [3] status, [4..5] connection handle. */
            if (size >= 13 && packet[2] == HCI_SUBEVENT_LE_DATA_LENGTH_CHANGE){
                if (hci_subevent_le_data_length_change_get_connection_handle(packet) == phone_handle){
                    char line[100];
                    data_length_seen = true;
                    data_length_valid = true;
                    data_length_tx_octets = hci_subevent_le_data_length_change_get_max_tx_octets(packet);
                    data_length_tx_time_us = hci_subevent_le_data_length_change_get_max_tx_time(packet);
                    data_length_rx_octets = hci_subevent_le_data_length_change_get_max_rx_octets(packet);
                    data_length_rx_time_us = hci_subevent_le_data_length_change_get_max_rx_time(packet);
                    wmr_diag_format_data_length(line, sizeof(line),
                        data_length_tx_octets, data_length_tx_time_us,
                        data_length_rx_octets, data_length_rx_time_us);
                    printf("%s\n", line);
                }
                break;
            }
            if (size >= 7 && packet[2] == HCI_SUBEVENT_LE_CONNECTION_UPDATE_COMPLETE){
                if (hci_subevent_le_connection_update_complete_get_connection_handle(packet) == phone_handle){
                    if (hci_subevent_le_connection_update_complete_get_status(packet) == ERROR_CODE_SUCCESS){
                        conn_update_pending = false;
                        conn_report("updated",
                            hci_subevent_le_connection_update_complete_get_conn_interval(packet),
                            hci_subevent_le_connection_update_complete_get_conn_latency(packet),
                            hci_subevent_le_connection_update_complete_get_supervision_timeout(packet));
                    } else {
                        printf("BLE connection update failed: 0x%02x\n",
                               hci_subevent_le_connection_update_complete_get_status(packet));
                    }
                }
                break;
            }
            if (size >= 7 && (packet[2] == HCI_SUBEVENT_LE_CONNECTION_COMPLETE ||
                              packet[2] == HCI_SUBEVENT_LE_ENHANCED_CONNECTION_COMPLETE_V1)){
                if (packet[3] == ERROR_CODE_SUCCESS){
                    hci_con_handle_t new_handle = little_endian_read_16(packet, 4);
                    if (!wmr_secure_may_connect(
                            phone_handle == HCI_CON_HANDLE_INVALID
                                ? WMR_SECURE_NO_HANDLE : phone_handle,
                            new_handle)){
                        printf("BLE connection refused: phone already connected, handle=0x%04x\n",
                               new_handle);
                        gap_disconnect(new_handle);
                        break;
                    }
                    phone_handle = new_handle;
                    phone_secure = false;
                    /* The controller stops advertising for a connected central. */
                    advertising_requested = false;
                    advertising_active = false;
                    btstack_run_loop_remove_timer(&pair_timer);
                    data_length_seen = false;
                    data_length_valid = false;
                    conn_wanted_refresh();
                    queue_reset();
                    stream_mode = WMR_STREAM_NONE;
                    send_pending = false;
                    echo_subscribed = false;
                    wmr_echo_cancel(&echo);
                    cfg_reset_connection();
                    ctrl_reset_connection();
                    hid_subscribed = false;
                    hid_error_logged = false;
                    wmr_hid_reset(&hid);
                    /* Address type matters for HOGP: Android may open the HID
                     * connection from a pseudo (resolvable) address instead of
                     * the public identity it bonded on. */
                    bd_addr_t peer;
                    uint8_t peer_type;
                    if (packet[2] == HCI_SUBEVENT_LE_CONNECTION_COMPLETE){
                        hci_subevent_le_connection_complete_get_peer_address(packet, peer);
                        peer_type = hci_subevent_le_connection_complete_get_peer_address_type(packet);
                    } else {
                        hci_subevent_le_enhanced_connection_complete_v1_get_peer_addresss(packet, peer);
                        peer_type = hci_subevent_le_enhanced_connection_complete_v1_get_peer_address_type(packet);
                    }
                    /* The version is in this line on purpose: a phone capture
                     * that starts here cannot otherwise be tied to the image
                     * that produced it. */
                    printf("BLE phone connected, handle=0x%04x peer=%s (%s) fw=%s\n", phone_handle,
                           bd_addr_to_str(peer), peer_type == 1 ? "random" : "public",
                           esp_app_get_description()->version);
                    conn_update_pending = false;
                    if (packet[2] == HCI_SUBEVENT_LE_CONNECTION_COMPLETE){
                        conn_report("at connect",
                            hci_subevent_le_connection_complete_get_conn_interval(packet),
                            hci_subevent_le_connection_complete_get_conn_latency(packet),
                            hci_subevent_le_connection_complete_get_supervision_timeout(packet));
                    } else {
                        conn_report("at connect",
                            hci_subevent_le_enhanced_connection_complete_v1_get_conn_interval(packet),
                            hci_subevent_le_enhanced_connection_complete_v1_get_conn_latency(packet),
                            hci_subevent_le_enhanced_connection_complete_v1_get_supervision_timeout(packet));
                    }
                    btstack_run_loop_remove_timer(&conn_request_timer);
                    btstack_run_loop_set_timer(&conn_request_timer, WMR_CFG_CONN_REQUEST_DELAY_MS);
                    btstack_run_loop_add_timer(&conn_request_timer);
                    btstack_run_loop_set_timer(&pair_timer, WMR_CFG_BLE_PAIR_TIMEOUT_MS);
                    btstack_run_loop_add_timer(&pair_timer);
                } else {
                    bd_addr_t peer;
                    if (packet[2] == HCI_SUBEVENT_LE_CONNECTION_COMPLETE){
                        hci_subevent_le_connection_complete_get_peer_address(packet, peer);
                    } else {
                        hci_subevent_le_enhanced_connection_complete_v1_get_peer_addresss(
                            packet, peer);
                    }
                    printf("BLE connection failed: %s status=0x%02x\n",
                           bd_addr_to_str(peer), packet[3]);
                    advertising_requested = false;
                    advertising_enable("connect attempt failed");
                }
            }
            break;
        case HCI_EVENT_DISCONNECTION_COMPLETE:
            if (size >= 6 && little_endian_read_16(packet, 3) == phone_handle){
                printf("BLE phone disconnected, reason=0x%02x\n", packet[5]);
                phone_handle = HCI_CON_HANDLE_INVALID;
                phone_secure = false;
                btstack_run_loop_remove_timer(&pair_timer);
                data_length_seen = false;
                data_length_valid = false;
                stream_mode = WMR_STREAM_NONE;
                send_pending = false;
                echo_subscribed = false;
                wmr_echo_cancel(&echo);
                cfg_reset_connection();
                ctrl_reset_connection();
                hid_subscribed = false;
                hid_error_logged = false;
                wmr_hid_reset(&hid);
                conn_known = false;
                conn_update_pending = false;
                btstack_run_loop_remove_timer(&conn_request_timer);
                advertising_requested = false;
                advertising_enable("phone disconnected");
            }
            break;
        default:
            break;
    }
}

/* ---- Init --------------------------------------------------------------- */

void wmr_transport_init(void){
    wmr_queue_init(&queue);
    wmr_echo_init(&echo);
    wmr_cfgmgr_init(cfg_state_changed);
    wmr_hid_reset(&hid);
    att_server_init(profile_data, att_read, att_write);
    unsigned descriptor_size = 0;
    const uint8_t *descriptor = wmr_hid_report_descriptor(&descriptor_size);
    hids_device_init(0, descriptor, (uint16_t) descriptor_size);
    hids_device_register_packet_handler(hid_packet_handler);
    /* The bridge's BR/EDR side is page-scan connectable for controller reconnects; give
     * it the same name as the LE advertising data so a scan that does see both does not
     * show BTstack's default "BTstack <bd_addr>" beside it. */
    gap_set_local_name(WMR_CFG_BLE_NAME);
    /* BTstack defaults the Class of Device to 0x007a020c ("Smartphone"). The bridge is a
     * Classic HID *host*, not a HID device, so it must not look like a Classic gamepad:
     * use Miscellaneous. The LE appearance below still gives the gamepad icon. */
    gap_set_class_of_device(WMR_CFG_CLASS_OF_DEVICE);
    /* General discoverable, BR/EDR not supported: the bridge has a Classic radio for the
     * controllers, but it is a HID host there, so the only thing a phone can pair with is
     * the LE HID device. The flag is not enough on its own: a phone that has the bridge's
     * BR/EDR address still pages it (2026-10-28 logs), which is why the LE address is a
     * separate static random address (wmr_bridge_main.c). Built from WMR_CFG_BLE_NAME so
     * the three images need no source change. */
    uint8_t *p = advertising;
    *p++ = 2;
    *p++ = BLUETOOTH_DATA_TYPE_FLAGS;
    *p++ = 0x06;
    *p++ = (uint8_t) sizeof(WMR_CFG_BLE_NAME);   /* AD length: type + name, NUL excluded */
    *p++ = BLUETOOTH_DATA_TYPE_COMPLETE_LOCAL_NAME;
    memcpy(p, WMR_CFG_BLE_NAME, sizeof(WMR_CFG_BLE_NAME) - 1);
    p += sizeof(WMR_CFG_BLE_NAME) - 1;
    /* Complete list: HID service 0x1812 first, then the FF10 stream service. A HOGP host
     * decides from the primary advertisement that this is a HID device; with 0x1812 only in
     * the scan response, a phone that misses the scan response sees a plain vendor device. */
    *p++ = 5;
    *p++ = BLUETOOTH_DATA_TYPE_COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS;
    *p++ = 0x12;
    *p++ = 0x18;
    *p++ = 0x10;
    *p++ = 0xff;
    bd_addr_t zero_address = {0};
    gap_advertisements_set_params(WMR_CFG_ADV_INTERVAL_MIN, WMR_CFG_ADV_INTERVAL_MAX,
                                  0, 0, zero_address, WMR_CFG_ADV_CHANNEL_MAP, 0);
    gap_advertisements_set_data((uint8_t) (p - advertising), advertising);

    /* Scan response: Appearance Gamepad (0x03C4). Android uses it for the scan-list icon;
     * the HID service UUID is in the advertising data above. */
    static const uint8_t scan_response[] = {
        0x03, BLUETOOTH_DATA_TYPE_APPEARANCE, 0xC4, 0x03,
    };
    gap_scan_response_set_data((uint8_t) sizeof(scan_response), (uint8_t *) scan_response);

    pump_timer.process = pump_retry_tick;
    btstack_run_loop_set_timer(&pump_timer, WMR_CFG_PUMP_RETRY_MS);
    btstack_run_loop_add_timer(&pump_timer);
    batch_timer.process = batch_timer_fire;
    conn_request_timer.process = conn_request_fire;
    pair_timer.process = pair_timer_fire;
    status_timer.process = status_tick_handler;
    btstack_run_loop_set_timer(&status_timer, WMR_CFG_STATUS_TICK_MS);
    btstack_run_loop_add_timer(&status_timer);

    att_server_register_packet_handler(att_packet_handler);
    sm_registration.callback = sm_packet_handler;
    sm_add_event_handler(&sm_registration);
}
