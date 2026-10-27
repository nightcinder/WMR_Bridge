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

/* wmr_batch.c - FF14 batch builder and flush rule; see wmr_batch.h. */

#define BTSTACK_FILE__ "wmr_batch.c"

#include <string.h>
#include "wmr_batch.h"
#include "wmr_levers.h"

bool wmr_batch_mtu_ok(uint16_t att_mtu){
    return att_mtu >= WMR_BATCH_MIN_MTU;
}

unsigned wmr_batch_max_count(uint16_t att_mtu){
    if (!wmr_batch_mtu_ok(att_mtu)) return 0;
    unsigned n = (unsigned) (att_mtu - WMR_ATT_NOTIFY_OVERHEAD - WMR_BATCH_HEADER_SIZE) / WMR_DECODED_SIZE;
    return n > WMR_BATCH_MAX_COUNT ? WMR_BATCH_MAX_COUNT : n;
}

void wmr_batch_init(wmr_batch_t *b, unsigned capacity){
    memset(b, 0, sizeof(*b));
    b->capacity = capacity > WMR_BATCH_MAX_COUNT ? WMR_BATCH_MAX_COUNT : capacity;
    b->data[0] = WMR_BATCH_VERSION;
}

bool wmr_batch_add(wmr_batch_t *b, const uint8_t record[WMR_DECODED_SIZE]){
    if (b->count >= b->capacity) return false;
    memcpy(&b->data[WMR_BATCH_HEADER_SIZE + b->count * WMR_DECODED_SIZE], record, WMR_DECODED_SIZE);
    b->count++;
    b->data[1] = (uint8_t) b->count;
    return true;
}

uint16_t wmr_batch_size(const wmr_batch_t *b){
    return (uint16_t) (WMR_BATCH_HEADER_SIZE + b->count * WMR_DECODED_SIZE);
}

bool wmr_batch_parse(const uint8_t *packet, uint16_t size, unsigned *count, const uint8_t **records){
    if (size < WMR_BATCH_HEADER_SIZE + WMR_DECODED_SIZE) return false;
    if (packet[0] != WMR_BATCH_VERSION || packet[1] < 1 ||
        packet[1] > WMR_BATCH_MAX_COUNT) return false;
    if (size != WMR_BATCH_HEADER_SIZE + packet[1] * WMR_DECODED_SIZE) return false;
    *count = packet[1];
    *records = packet + WMR_BATCH_HEADER_SIZE;
    return true;
}

uint32_t wmr_batch_hold_us(uint32_t conn_interval_us){
    uint32_t interval_us = conn_interval_us ? conn_interval_us : WMR_CFG_BATCH_HOLD_DEFAULT_US;
    return interval_us * (uint32_t) wmr_levers_hold_intervals();
}

wmr_batch_flush_t wmr_batch_flush_reason(unsigned queued, unsigned max_count,
                                         uint32_t oldest_age_us, uint32_t hold_us){
    if (!queued) return WMR_BATCH_FLUSH_NO;
    if (!max_count || queued >= max_count) return WMR_BATCH_FLUSH_FULL;
    if (oldest_age_us >= hold_us) return WMR_BATCH_FLUSH_TIMEOUT;
    return WMR_BATCH_FLUSH_NO;
}

uint32_t wmr_batch_timer_ms(uint32_t oldest_age_us, uint32_t hold_us){
    if (oldest_age_us >= hold_us) return 1;
    uint32_t ms = (uint32_t) (((uint64_t) (hold_us - oldest_age_us) + 999u) / 1000u);
    return ms ? ms : 1;
}
