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

/* wmr_linkdiag.c - see wmr_linkdiag.h. BTstack glue; the logic is in wmr_devloss.c and wmr_diag.c. */

#define BTSTACK_FILE__ "wmr_linkdiag.c"

#include <stdio.h>
#include <string.h>
#include "btstack_config.h"
#include "btstack.h"
#include "esp_timer.h"
#include "wmr_config.h"
#include "wmr_controller.h"
#include "wmr_devloss.h"
#include "wmr_diag.h"
#include "wmr_linkdiag.h"
#include "wmr_log.h"

typedef struct {
    bool              connected;
    hci_con_handle_t  handle;
    wmr_link_health_t health;
    wmr_devloss_t     loss;
} link_t;

static link_t links[WMR_NUM_CONTROLLERS];
static wmr_mode_tracker_t modes;

/* Written from the BT controller task (the VHCI receive callback logs there), read from
 * the BTstack run loop; atomic increments, relaxed ordering is enough for counters. */
static uint32_t vhci_drops;
static uint32_t log_errors;

/* One poll cycle: up to WMR_NUM_CONTROLLERS * WMR_LINKCMD_COUNT commands, one at a time. */
typedef struct { uint8_t hand; uint8_t kind; } job_t;
static job_t jobs[WMR_NUM_CONTROLLERS * WMR_LINKCMD_COUNT];
static unsigned job_count, job_next;
static bool awaiting;
static uint32_t awaiting_since_ms;
static job_t awaiting_job;

static btstack_timer_source_t cycle_timer;
static btstack_timer_source_t pace_timer;

/* ---- (d) log sink: count the silent VHCI receive drop --------------------- */

#if WMR_CFG_ATT_TRACE
/* Every ATT PDU on the phone link, decoded to one line. A HOGP host runs the
 * whole HID handshake over ATT (discover, read report map, write the Report
 * CCCD), so this is what separates "the phone never opened GATT" from "it read
 * the map and stopped". HCI ACL layout: handle/flags (2), length (2), L2CAP
 * length (2), CID (2), PDU. */
static uint32_t att_rx, att_tx, att_writes, att_short_writes;
static uint32_t le_smp, le_signal;

static const char *att_opcode_name(uint8_t opcode){
    switch (opcode){
        case 0x01: return "error-rsp";
        case 0x02: return "mtu-req";
        case 0x03: return "mtu-rsp";
        case 0x04: return "find-info";
        case 0x05: return "find-info-rsp";
        case 0x06: return "find-by-type";
        case 0x07: return "find-by-type-rsp";
        case 0x08: return "read-by-type";
        case 0x09: return "read-by-type-rsp";
        case 0x0a: return "read";
        case 0x0b: return "read-rsp";
        case 0x0c: return "read-blob";
        case 0x0d: return "read-blob-rsp";
        case 0x10: return "read-by-group";
        case 0x11: return "read-by-group-rsp";
        case 0x12: return "write";
        case 0x13: return "write-rsp";
        case 0x52: return "write-cmd";
        case 0x1b: return "notify";
        case 0x1d: return "indicate";
        case 0xd2: return "signed-write-cmd";
        default:   return "other";
    }
}

static void att_trace(uint8_t out, const uint8_t *packet, uint16_t len){
    if (len < 9) return;
    const uint16_t cid = little_endian_read_16(packet, 6);
    const char *dir = out ? "tx" : "rx";
    if (cid != 0x0004){
        /* LE-only fixed channels: SMP pairing and LE signalling. Their presence shows
         * the phone link carries L2CAP traffic even when no ATT PDU arrives, which
         * separates "Android never opened GATT" from "the trace is off". */
        if (cid == 0x0006){
            le_smp++;
            if (len > 8) WMR_LOGI("att", "%s smp code=0x%02x len=%u", dir,
                                  packet[8], (unsigned) (len - 8));
        } else if (cid == 0x0005){
            le_signal++;
        }
        return;
    }
    const uint8_t *pdu = packet + 8;
    uint16_t plen = (uint16_t) (len - 8);
    uint8_t opcode = pdu[0];
    if (out) att_tx++; else att_rx++;

    switch (opcode){
        case 0x01:  /* Error Response: request opcode, handle, code */
            if (plen >= 5){
                WMR_LOGI("att", "%s error req=0x%02x handle=0x%04x code=0x%02x",
                         dir, pdu[1], little_endian_read_16(pdu, 2), pdu[4]);
            } else {
                WMR_LOGI("att", "%s error len=%u", dir, plen);
            }
            break;
        case 0x08: case 0x10:  /* Read By Type / Read By Group Type */
        case 0x06:             /* Find By Type Value */
            if (plen >= 7){
                WMR_LOGI("att", "%s %s 0x%04x..0x%04x", dir, att_opcode_name(opcode),
                         little_endian_read_16(pdu, 1), little_endian_read_16(pdu, 3));
            } else {
                WMR_LOGI("att", "%s %s len=%u", dir, att_opcode_name(opcode), plen);
            }
            break;
        case 0x04:             /* Find Information */
            if (plen >= 5){
                WMR_LOGI("att", "%s find-info 0x%04x..0x%04x", dir,
                         little_endian_read_16(pdu, 1), little_endian_read_16(pdu, 3));
            }
            break;
        case 0x0a: case 0x0c:  /* Read / Read Blob */
            if (plen >= 3){
                if (opcode == 0x0c && plen >= 5){
                    WMR_LOGI("att", "%s read-blob handle=0x%04x offset=%u", dir,
                             little_endian_read_16(pdu, 1), little_endian_read_16(pdu, 3));
                } else {
                    WMR_LOGI("att", "%s read handle=0x%04x", dir,
                             little_endian_read_16(pdu, 1));
                }
            }
            break;
        case 0x12: case 0x52: case 0xd2:  /* Write Request / Command / Signed */
            if (plen >= 3){
                uint16_t handle = little_endian_read_16(pdu, 1);
                att_writes++;
                if (plen == 4){
                    att_short_writes++;
                    WMR_LOGI("att", "%s write handle=0x%04x value=0x%02x%02x", dir,
                             handle, pdu[2], pdu[3]);
                } else {
                    WMR_LOGI("att", "%s write handle=0x%04x len=%u", dir, handle,
                             (unsigned) (plen - 3));
                }
            }
            break;
        case 0x1b: case 0x1d:  /* Handle Value Notification / Indication */
            if (plen >= 3){
                WMR_LOGI("att", "%s %s handle=0x%04x len=%u", dir,
                         att_opcode_name(opcode), little_endian_read_16(pdu, 1),
                         (unsigned) (plen - 3));
            }
            break;
        default:
            WMR_LOGI("att", "%s %s len=%u", dir, att_opcode_name(opcode), plen);
            break;
    }
}
#endif

static void dump_reset(void){}
static void dump_packet(uint8_t packet_type, uint8_t in, uint8_t *packet, uint16_t len){
#if WMR_CFG_ATT_TRACE
    if (packet_type == HCI_ACL_DATA_PACKET){
        att_trace(in, packet, len);
        return;
    }
#endif
    (void)packet_type; (void)in; (void)packet; (void)len;
}
static void dump_message(int log_level, const char *format, va_list argptr){
    (void)argptr;
    switch (wmr_diag_classify_log(log_level == HCI_DUMP_LOG_LEVEL_ERROR, format)){
        case WMR_LOG_VHCI_DROP:   __atomic_fetch_add(&vhci_drops, 1, __ATOMIC_RELAXED); /* fall through */
        case WMR_LOG_OTHER_ERROR: __atomic_fetch_add(&log_errors, 1, __ATOMIC_RELAXED); break;
        default: break;
    }
}
static const hci_dump_t dump_sink = { dump_reset, dump_packet, dump_message };

uint32_t wmr_linkdiag_vhci_drops(void){ return __atomic_load_n(&vhci_drops, __ATOMIC_RELAXED); }
uint32_t wmr_linkdiag_log_errors(void){ return __atomic_load_n(&log_errors, __ATOMIC_RELAXED); }

void wmr_linkdiag_att_get(wmr_linkdiag_att_t *out){
    memset(out, 0, sizeof(*out));
#if WMR_CFG_ATT_TRACE
    out->rx              = att_rx;
    out->tx              = att_tx;
    out->writes          = att_writes;
    out->two_byte_writes = att_short_writes;
    out->smp             = le_smp;
    out->le_signal       = le_signal;
#endif
}

/* ---- helpers --------------------------------------------------------------- */

static int hand_for_handle(hci_con_handle_t handle){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (links[i].connected && links[i].handle == handle) return (int) i;
    }
    return -1;
}

static uint32_t now_ms(void){
    return (uint32_t) (esp_timer_get_time() / 1000);
}

static void link_reset(unsigned hand){
    memset(&links[hand].health, 0, sizeof(links[hand].health));
    wmr_devloss_reset(&links[hand].loss);
    wmr_mode_tracker_reset(&modes, hand);
}

void wmr_linkdiag_note_report(unsigned hand, const uint8_t *report, uint16_t length){
    uint32_t ticks;
    if (hand >= WMR_NUM_CONTROLLERS) return;
    if (wmr_devloss_ticks_from_frame(report, length, &ticks)) wmr_devloss_add(&links[hand].loss, ticks);
}

void wmr_linkdiag_get_hand(unsigned hand, wmr_linkdiag_hand_t *out){
    memset(out, 0, sizeof(*out));
    if (hand >= WMR_NUM_CONTROLLERS) return;
    const link_t *l = &links[hand];
    out->connected       = l->connected;
    out->mode            = modes.mode[hand];
    out->health          = l->health;
    out->reports         = l->loss.reports;
    out->missing         = l->loss.missing;
    out->discontinuities = l->loss.discontinuities;
    out->period_ticks    = wmr_devloss_period(&l->loss);
    out->loss_permille   = wmr_devloss_loss_permille(&l->loss);
}

/* ---- (c) polling ------------------------------------------------------------ */

static const hci_cmd_t *command_for(unsigned kind){
    switch (kind){
        case WMR_LINKCMD_RSSI:           return &hci_read_rssi;
        case WMR_LINKCMD_FAILED_CONTACT: return &hci_read_failed_contact_counter;
        default:                         return &hci_read_link_quality;
    }
}

static void pace_arm(void){
    btstack_run_loop_remove_timer(&pace_timer);
    btstack_run_loop_set_timer(&pace_timer, WMR_CFG_LINKDIAG_PACE_MS);
    btstack_run_loop_add_timer(&pace_timer);
}

/* Send at most one command per tick, and only when the previous one has been answered
 * (or WMR_CFG_LINKDIAG_REPLY_TIMEOUT_MS has passed). */
static void pace_tick(btstack_timer_source_t *timer){
    (void)timer;
    if (awaiting && now_ms() - awaiting_since_ms > WMR_CFG_LINKDIAG_REPLY_TIMEOUT_MS) awaiting = false;
    if (!awaiting && job_next < job_count && hci_can_send_command_packet_now()){
        job_t j = jobs[job_next];
        if (links[j.hand].connected){
            const hci_cmd_t *cmd = command_for(j.kind);
            if (hci_send_cmd(cmd, links[j.hand].handle) == ERROR_CODE_SUCCESS){
                awaiting = true;
                awaiting_job = j;
                awaiting_since_ms = now_ms();
            }
        }
        job_next++;
    }
    if (job_next < job_count || awaiting) pace_arm();
}

static void cycle_tick(btstack_timer_source_t *timer){
    job_count = 0;
    job_next = 0;
    for (unsigned h = 0; h < WMR_NUM_CONTROLLERS; h++){
        if (!links[h].connected) continue;
        for (unsigned k = 0; k < WMR_LINKCMD_COUNT; k++){
            jobs[job_count].hand = (uint8_t) h;
            jobs[job_count].kind = (uint8_t) k;
            job_count++;
        }
    }
    if (job_count) pace_arm();
    btstack_run_loop_set_timer(timer, WMR_CFG_LINKDIAG_INTERVAL_MS);
    btstack_run_loop_add_timer(timer);
}

/* ---- events ------------------------------------------------------------------ */

void wmr_linkdiag_link_identified(unsigned hand, uint16_t con_handle){
    if (hand >= WMR_NUM_CONTROLLERS || (links[hand].connected && links[hand].handle == con_handle)) return;
    links[hand].connected = true;
    links[hand].handle = con_handle;
    link_reset(hand);
}

void wmr_linkdiag_on_hci_event(uint8_t *packet, uint16_t size){
    (void)size;
    switch (hci_event_packet_get_type(packet)){
        case HCI_EVENT_CONNECTION_REQUEST: {
#if WMR_CFG_ATT_TRACE
            /* A phone that decides this is a Classic HID device pages the BR/EDR side.
             * Log it: that attempt, not the LE link, is where such a phone stops. */
            bd_addr_t address;
            hci_event_connection_request_get_bd_addr(packet, address);
            WMR_LOGI("att", "incoming %s link from %s",
                     hci_event_connection_request_get_link_type(packet) == HCI_LINK_TYPE_ACL
                         ? "ACL" : "SCO",
                     bd_addr_to_str(address));
#endif
            break;
        }
        case HCI_EVENT_CONNECTION_COMPLETE: {
            if (hci_event_connection_complete_get_status(packet) != ERROR_CODE_SUCCESS) break;
            bd_addr_t address;
            hci_event_connection_complete_get_bd_addr(packet, address);
            int hand = wmr_controller_hand_for_address(address);
            if (hand < 0) break;
            links[hand].connected = true;
            links[hand].handle = hci_event_connection_complete_get_connection_handle(packet);
            link_reset((unsigned) hand);
            break;
        }
        case HCI_EVENT_DISCONNECTION_COMPLETE: {
            int hand = hand_for_handle(hci_event_disconnection_complete_get_connection_handle(packet));
            if (hand < 0) break;
            links[hand].connected = false;
            link_reset((unsigned) hand);
            break;
        }
        case HCI_EVENT_MODE_CHANGE: {
            if (hci_event_mode_change_get_status(packet) != ERROR_CODE_SUCCESS) break;
            int hand = hand_for_handle(hci_event_mode_change_get_handle(packet));
            if (hand < 0) break;
            uint8_t mode = hci_event_mode_change_get_mode(packet);
            uint8_t previous = WMR_MODE_ACTIVE;
            if (wmr_mode_tracker_update(&modes, (unsigned) hand, mode, &previous)){
                char line[120];
                wmr_mode_format(line, sizeof(line), wmr_controller_name((unsigned) hand), previous, mode,
                                hci_event_mode_change_get_interval(packet), (uint64_t) esp_timer_get_time());
                printf("%s\n", line);
            }
            break;
        }
        case HCI_EVENT_COMMAND_COMPLETE: {
            uint16_t opcode = hci_event_command_complete_get_command_opcode(packet);
            if (opcode == hci_le_read_buffer_size.opcode || opcode == hci_le_read_buffer_size_v2.opcode){
                /* Read once during stack start-up; logged once, nothing is polled. */
                uint16_t acl_length;
                uint8_t acl_count;
                if (packet[1] > 3 &&
                    wmr_diag_parse_le_buffer_size(hci_event_command_complete_get_return_parameters(packet),
                                                  (uint16_t) (packet[1] - 3), &acl_length, &acl_count)){
                    char line[80];
                    wmr_diag_format_le_buffers(line, sizeof(line), acl_length, acl_count);
                    printf("%s\n", line);
                }
                break;
            }
            if (!awaiting) break;
            if (opcode != command_for(awaiting_job.kind)->opcode) break;
            uint16_t handle = 0;
            const uint8_t *ret = hci_event_command_complete_get_return_parameters(packet);
            uint16_t ret_len = (uint16_t) (packet[1] > 3 ? packet[1] - 3 : 0);
            wmr_diag_parse_link_reply((wmr_linkcmd_t) awaiting_job.kind, ret, ret_len, &handle,
                                      &links[awaiting_job.hand].health);
            awaiting = false;
            break;
        }
        default:
            break;
    }
}

void wmr_linkdiag_init(void){
#ifndef CONFIG_BTSTACK_HCI_DUMP_STDOUT
    /* No other dump sink is installed in this configuration. Only error-level messages
     * reach the sink and packet logging is off, so the cost is one function call per
     * error message and one flag test per HCI packet. */
    hci_dump_init(&dump_sink);
    hci_dump_enable_packet_log(false);
    hci_dump_enable_log_level(HCI_DUMP_LOG_LEVEL_DEBUG, 0);
    hci_dump_enable_log_level(HCI_DUMP_LOG_LEVEL_INFO, 0);
    hci_dump_enable_log_level(HCI_DUMP_LOG_LEVEL_ERROR, 1);
#endif
#if WMR_CFG_ATT_TRACE && !defined(CONFIG_BTSTACK_HCI_DUMP_STDOUT)
    /* The ATT PDU trace needs packet logging; dump_packet() keeps only the ATT CID. */
    hci_dump_enable_packet_log(true);
    WMR_LOGI("att", "ATT trace on: one line per ATT/SMP PDU on the phone link");
#elif WMR_CFG_ATT_TRACE
    WMR_LOGI("att", "ATT trace compiled in but packet logging is disabled by "
                    "CONFIG_BTSTACK_HCI_DUMP_STDOUT; expect no PDU lines");
#endif
    cycle_timer.process = cycle_tick;
    btstack_run_loop_set_timer(&cycle_timer, WMR_CFG_LINKDIAG_INTERVAL_MS);
    btstack_run_loop_add_timer(&cycle_timer);
    pace_timer.process = pace_tick;
}
