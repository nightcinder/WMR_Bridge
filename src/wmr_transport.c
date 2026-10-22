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
#include "esp_timer.h"
#include "wmr_bridge.h"        /* generated from wmr_bridge.gatt: profile_data, ATT_*_HANDLE */
#include "wmr_controller.h"
#include "wmr_diag.h"
#include "wmr_linkdiag.h"
#include "wmr_link.h"
#include "wmr_decoded.h"
#include "wmr_protocol.h"
#include "wmr_queue.h"
#include "wmr_stream_mode.h"
#include "wmr_transport.h"

/* Experimental FF10 service; see wmr_bridge.gatt. The FF11 characteristic is
 * both the status read value and the raw notification stream. FF12 is the
 * notify-only decoded stream. The two are mutually exclusive per phone
 * (wmr_stream_mode.h). */
#define STREAM_VALUE_HANDLE ATT_CHARACTERISTIC_0000FF11_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
#define STREAM_CCC_HANDLE   ATT_CHARACTERISTIC_0000FF11_0000_1000_8000_00805F9B34FB_01_CLIENT_CONFIGURATION_HANDLE
#define DECODED_VALUE_HANDLE ATT_CHARACTERISTIC_0000FF12_0000_1000_8000_00805F9B34FB_01_VALUE_HANDLE
#define DECODED_CCC_HANDLE   ATT_CHARACTERISTIC_0000FF12_0000_1000_8000_00805F9B34FB_01_CLIENT_CONFIGURATION_HANDLE

#define STATUS_SIZE 16

static hci_con_handle_t phone_handle = HCI_CON_HANDLE_INVALID;
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
static uint16_t send_offset;       /* bytes of the head frame already sent */
static wmr_stats_t stats;

static btstack_timer_source_t pump_timer;
static btstack_timer_source_t status_timer;

/* ---- Connection parameters ---------------------------------------------- */

static const wmr_conn_request_t conn_wanted = {
    WMR_CFG_CONN_INTERVAL_MIN, WMR_CFG_CONN_INTERVAL_MAX,
    WMR_CFG_CONN_LATENCY, WMR_CFG_CONN_TIMEOUT
};
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

static void pump_kick(void){
    wmr_episode_note_queue(&drop_episode, queue.count);
    if (stream_mode == WMR_STREAM_NONE || phone_handle == HCI_CON_HANDLE_INVALID ||
        !queue.count || send_pending) return;
    send_pending = true;
    att_server_request_can_send_now_event(phone_handle);
}

void wmr_transport_submit_report(unsigned hand, const uint8_t *report, uint16_t length){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    wmr_linkdiag_note_report(hand, report, length);       /* reads 4 bytes, see wmr_linkdiag.h */
    uint32_t sequence = ++stats.sequence[hand];
    stats.rx[hand]++;
    if (stream_mode == WMR_STREAM_NONE || phone_handle == HCI_CON_HANDLE_INVALID){
        stats.offline[hand]++;
        return;
    }
    /* FF12 needs room for a whole record; below that nothing is sent. Count the
     * sample here instead of queueing something that can never be delivered. */
    if (stream_mode == WMR_STREAM_DECODED &&
        !wmr_decoded_mtu_ok(att_server_get_mtu(phone_handle))){
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

/* Send one fragment (FF11) or one record (FF12) of the head frame. Runs on
 * ATT_EVENT_CAN_SEND_NOW. */
static void pump_send_one(void){
    send_pending = false;
    if (stream_mode == WMR_STREAM_NONE || phone_handle == HCI_CON_HANDLE_INVALID) return;
    const wmr_frame_t *frame = wmr_queue_peek(&queue);
    if (!frame) return;
    if (stream_mode == WMR_STREAM_DECODED){
        pump_send_decoded(frame);
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

static void pump_retry_tick(btstack_timer_source_t *timer){
    pump_kick();
    btstack_run_loop_set_timer(timer, WMR_CFG_PUMP_RETRY_MS);
    btstack_run_loop_add_timer(timer);
}

static void status_tick_handler(btstack_timer_source_t *timer){
    status_tick++;
    pump_kick();
    btstack_run_loop_set_timer(timer, WMR_CFG_STATUS_TICK_MS);
    btstack_run_loop_add_timer(timer);
}

/* ---- Introspection ------------------------------------------------------ */

const wmr_stats_t *wmr_transport_stats(void){
    return &stats;
}

void wmr_transport_get_info(wmr_transport_info_t *info){
    info->phone_connected  = phone_handle != HCI_CON_HANDLE_INVALID;
    info->subscribed       = stream_mode != WMR_STREAM_NONE;
    info->stream           = stream_mode;
    info->mtu              = info->phone_connected ? att_server_get_mtu(phone_handle) : 0;
    info->queue_count      = queue.count;
    info->queue_capacity   = WMR_CFG_QUEUE_CAPACITY;
    info->queue_high_water = queue.high_water;
    info->conn_known       = conn_known;
    info->conn             = conn_now;
    info->conn_update_pending = conn_update_pending;
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

static uint16_t att_read(hci_con_handle_t handle, uint16_t attribute,
                         uint16_t offset, uint8_t *buffer, uint16_t buffer_size){
    (void)handle;
    if (attribute == STREAM_VALUE_HANDLE){
        uint8_t status[STATUS_SIZE];
        build_status(status);
        return att_read_callback_handle_blob(status, sizeof(status), offset, buffer, buffer_size);
    }
    if (attribute == STREAM_CCC_HANDLE || attribute == DECODED_CCC_HANDLE){
        wmr_stream_t which = attribute == STREAM_CCC_HANDLE ? WMR_STREAM_RAW : WMR_STREAM_DECODED;
        uint8_t ccc[2] = {wmr_stream_mode_ccc_enabled(stream_mode, which) ? 1 : 0, 0};
        return att_read_callback_handle_blob(ccc, sizeof(ccc), offset, buffer, buffer_size);
    }
    return 0;
}

static int att_write(hci_con_handle_t handle, uint16_t attribute,
                     uint16_t transaction_mode, uint16_t offset,
                     uint8_t *buffer, uint16_t buffer_size){
    wmr_stream_t which;
    if (attribute == STREAM_CCC_HANDLE)       which = WMR_STREAM_RAW;
    else if (attribute == DECODED_CCC_HANDLE) which = WMR_STREAM_DECODED;
    else return 0;
    if (transaction_mode != ATT_TRANSACTION_MODE_NONE) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (offset != 0) return ATT_ERROR_INVALID_OFFSET;
    if (buffer_size != 2) return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH;
    uint16_t value = little_endian_read_16(buffer, 0);
    if (value > 1) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    if (phone_handle != HCI_CON_HANDLE_INVALID && handle != phone_handle)
        return ATT_ERROR_WRITE_NOT_PERMITTED;
    /* FF11 and FF12 are mutually exclusive: enabling one while the other is
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
    if (packet_type == HCI_EVENT_PACKET && size >= 2 &&
        hci_event_packet_get_type(packet) == ATT_EVENT_CAN_SEND_NOW){
        pump_send_one();
    }
}

/* ---- HCI events --------------------------------------------------------- */

void wmr_transport_on_hci_event(uint8_t *packet, uint16_t size){
    switch (hci_event_packet_get_type(packet)){
        case BTSTACK_EVENT_STATE:
            if (btstack_event_state_get_state(packet) == HCI_STATE_WORKING){
                gap_advertisements_enable(1);
                printf("BLE advertising: WMR Bridge (FF11 raw stream, FF12 decoded stream)\n");
            }
            break;
        case HCI_EVENT_LE_META:
            /* Legacy and enhanced LE Connection Complete share these initial
             * fields: [3] status, [4..5] connection handle. */
            if (size >= 13 && packet[2] == HCI_SUBEVENT_LE_DATA_LENGTH_CHANGE){
                if (hci_subevent_le_data_length_change_get_connection_handle(packet) == phone_handle){
                    char line[100];
                    data_length_seen = true;
                    wmr_diag_format_data_length(line, sizeof(line),
                        hci_subevent_le_data_length_change_get_max_tx_octets(packet),
                        hci_subevent_le_data_length_change_get_max_tx_time(packet),
                        hci_subevent_le_data_length_change_get_max_rx_octets(packet),
                        hci_subevent_le_data_length_change_get_max_rx_time(packet));
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
                    phone_handle = little_endian_read_16(packet, 4);
                    data_length_seen = false;
                    queue_reset();
                    stream_mode = WMR_STREAM_NONE;
                    send_pending = false;
                    printf("BLE phone connected, handle=0x%04x\n", phone_handle);
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
                } else {
                    printf("BLE connection failed: 0x%02x\n", packet[3]);
                    gap_advertisements_enable(1);
                }
            }
            break;
        case HCI_EVENT_DISCONNECTION_COMPLETE:
            if (size >= 6 && little_endian_read_16(packet, 3) == phone_handle){
                printf("BLE phone disconnected, reason=0x%02x\n", packet[5]);
                phone_handle = HCI_CON_HANDLE_INVALID;
                stream_mode = WMR_STREAM_NONE;
                send_pending = false;
                conn_known = false;
                conn_update_pending = false;
                btstack_run_loop_remove_timer(&conn_request_timer);
                gap_advertisements_enable(1);
            }
            break;
        default:
            break;
    }
}

/* ---- Init --------------------------------------------------------------- */

void wmr_transport_init(void){
    wmr_queue_init(&queue);
    att_server_init(profile_data, att_read, att_write);
    /* General discoverable; do not claim BR/EDR is unsupported. */
    static const uint8_t advertising[] = {
        2, BLUETOOTH_DATA_TYPE_FLAGS, 0x02,
        11, BLUETOOTH_DATA_TYPE_COMPLETE_LOCAL_NAME,
        'W','M','R',' ','B','r','i','d','g','e',
        3, BLUETOOTH_DATA_TYPE_INCOMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
        0x10, 0xff
    };
    bd_addr_t zero_address = {0};
    gap_advertisements_set_params(WMR_CFG_ADV_INTERVAL_MIN, WMR_CFG_ADV_INTERVAL_MAX,
                                  0, 0, zero_address, WMR_CFG_ADV_CHANNEL_MAP, 0);
    gap_advertisements_set_data(sizeof(advertising), (uint8_t *) advertising);

    pump_timer.process = pump_retry_tick;
    btstack_run_loop_set_timer(&pump_timer, WMR_CFG_PUMP_RETRY_MS);
    btstack_run_loop_add_timer(&pump_timer);
    conn_request_timer.process = conn_request_fire;
    status_timer.process = status_tick_handler;
    btstack_run_loop_set_timer(&status_timer, WMR_CFG_STATUS_TICK_MS);
    btstack_run_loop_add_timer(&status_timer);

    att_server_register_packet_handler(att_packet_handler);
}
