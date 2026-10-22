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

/* wmr_diag.c - see wmr_diag.h. */

#define BTSTACK_FILE__ "wmr_diag.c"

#include <stdio.h>
#include <string.h>
#include "wmr_diag.h"

wmr_log_class_t wmr_diag_classify_log(bool is_error_level, const char *format){
    if (!is_error_level || !format) return WMR_LOG_IGNORE;
    if (strstr(format, "dropping packet") != NULL && strstr(format, "transport_recv_pkt_cb") != NULL)
        return WMR_LOG_VHCI_DROP;
    return WMR_LOG_OTHER_ERROR;
}

void wmr_mode_tracker_reset(wmr_mode_tracker_t *t, unsigned hand){
    if (hand < WMR_NUM_CONTROLLERS) t->mode[hand] = WMR_MODE_ACTIVE;
}

bool wmr_mode_tracker_update(wmr_mode_tracker_t *t, unsigned hand, uint8_t mode, uint8_t *previous){
    if (hand >= WMR_NUM_CONTROLLERS) return false;
    if (t->mode[hand] == mode) return false;
    if (previous) *previous = t->mode[hand];
    t->mode[hand] = mode;
    return true;
}

const char *wmr_mode_name(uint8_t mode){
    switch (mode){
        case WMR_MODE_ACTIVE: return "active";
        case WMR_MODE_HOLD:   return "hold";
        case WMR_MODE_SNIFF:  return "sniff";
        case WMR_MODE_PARK:   return "park";
        default:              return "unknown";
    }
}

void wmr_mode_format(char *buf, size_t len, const char *hand_name, uint8_t previous,
                     uint8_t mode, uint16_t interval_slots, uint64_t time_us){
    if (!buf || !len) return;
    unsigned long us = (unsigned long) interval_slots * 625ul;      /* 0.625 ms per slot */
    unsigned long ms_whole = (unsigned long) (time_us / 1000u);
    snprintf(buf, len, "%s link mode: %s -> %s (interval %u slots = %lu.%02lu ms) at t=%lu.%03lu s",
             hand_name ? hand_name : "?", wmr_mode_name(previous), wmr_mode_name(mode),
             (unsigned) interval_slots, us / 1000ul, (us % 1000ul) / 10ul,
             ms_whole / 1000ul, ms_whole % 1000ul);
}

bool wmr_diag_parse_link_reply(wmr_linkcmd_t kind, const uint8_t *ret, uint16_t len,
                               uint16_t *handle, wmr_link_health_t *h){
    if (!ret || !h || kind >= WMR_LINKCMD_COUNT || len < 3) return false;
    uint8_t status = ret[0];
    if (handle) *handle = (uint16_t) (ret[1] | (ret[2] << 8));
    h->status[kind] = status;
    if (status != 0){
        h->valid[kind] = false;
        return true;
    }
    switch (kind){
        case WMR_LINKCMD_RSSI:
            if (len < 4) return false;
            h->rssi = (int8_t) ret[3];
            break;
        case WMR_LINKCMD_FAILED_CONTACT:
            if (len < 5) return false;
            h->failed_contacts = (uint16_t) (ret[3] | (ret[4] << 8));
            break;
        case WMR_LINKCMD_QUALITY:
            if (len < 4) return false;
            h->quality = ret[3];
            break;
        default:
            return false;
    }
    h->valid[kind] = true;
    h->updates++;
    return true;
}

void wmr_link_health_format(const wmr_link_health_t *h, char *buf, size_t len){
    if (!buf || !len) return;
    char rssi[20], fcc[20], lq[20];
    if (h->valid[WMR_LINKCMD_RSSI]) snprintf(rssi, sizeof(rssi), "%d", (int) h->rssi);
    else snprintf(rssi, sizeof(rssi), "n/a(0x%02x)", (unsigned) h->status[WMR_LINKCMD_RSSI]);
    if (h->valid[WMR_LINKCMD_FAILED_CONTACT]) snprintf(fcc, sizeof(fcc), "%u", (unsigned) h->failed_contacts);
    else snprintf(fcc, sizeof(fcc), "n/a(0x%02x)", (unsigned) h->status[WMR_LINKCMD_FAILED_CONTACT]);
    if (h->valid[WMR_LINKCMD_QUALITY]) snprintf(lq, sizeof(lq), "%u", (unsigned) h->quality);
    else snprintf(lq, sizeof(lq), "n/a(0x%02x)", (unsigned) h->status[WMR_LINKCMD_QUALITY]);
    snprintf(buf, len, "rssi=%s fcc=%s lq=%s", rssi, fcc, lq);
}

int wmr_diag_interval_permille(uint32_t prev_missing, uint32_t missing,
                               uint32_t prev_received, uint32_t received){
    if (missing < prev_missing) return -1;
    uint32_t dm = missing - prev_missing;
    uint32_t dr = received - prev_received;
    uint64_t expected = (uint64_t) dm + dr;
    if (!expected) return -1;
    return (int) (((uint64_t) dm * 1000u + expected / 2u) / expected);
}

void wmr_diag_percent_format(int permille, char *buf, size_t len){
    if (permille < 0) snprintf(buf, len, "n/a");
    else snprintf(buf, len, "%d.%d%%", permille / 10, permille % 10);
}
