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

/* wmr_echo.c - FF15 echo request/response and the one-slot state; see wmr_echo.h. */

#define BTSTACK_FILE__ "wmr_echo.c"

#include <string.h>
#include "wmr_echo.h"

static void put_u16(uint8_t *p, uint16_t v){
    p[0] = (uint8_t) v;
    p[1] = (uint8_t) (v >> 8);
}

static void put_u32(uint8_t *p, uint32_t v){
    for (unsigned i = 0; i < 4; i++) p[i] = (uint8_t) (v >> (8 * i));
}

static void put_u64(uint8_t *p, uint64_t v){
    for (unsigned i = 0; i < 8; i++) p[i] = (uint8_t) (v >> (8 * i));
}

static uint16_t get_u16(const uint8_t *p){
    return (uint16_t) (p[0] | (p[1] << 8));
}

static uint32_t get_u32(const uint8_t *p){
    uint32_t v = 0;
    for (unsigned i = 0; i < 4; i++) v |= (uint32_t) p[i] << (8 * i);
    return v;
}

static uint64_t get_u64(const uint8_t *p){
    uint64_t v = 0;
    for (unsigned i = 0; i < 8; i++) v |= (uint64_t) p[i] << (8 * i);
    return v;
}

bool wmr_echo_parse_request(const uint8_t *value, uint16_t size, uint16_t *ping_id){
    if (!value || size != WMR_ECHO_REQUEST_SIZE || value[0] != WMR_ECHO_VERSION) return false;
    *ping_id = get_u16(&value[2]);
    return true;
}

/* Layout: [0] version, [1] reserved 0, [2..3] ping_id, [4..11] rx_time_us,
 * [12..15] turnaround_us, [16..17] queue_depth, [18..19] reserved 0. */
void wmr_echo_pack(const wmr_echo_response_t *r, uint8_t out[WMR_ECHO_RESPONSE_SIZE]){
    memset(out, 0, WMR_ECHO_RESPONSE_SIZE);
    out[0] = WMR_ECHO_VERSION;
    put_u16(&out[2], r->ping_id);
    put_u64(&out[4], r->rx_time_us);
    put_u32(&out[12], r->turnaround_us);
    put_u16(&out[16], r->queue_depth);
}

bool wmr_echo_unpack(const uint8_t *value, uint16_t size, wmr_echo_response_t *out){
    if (!value || size != WMR_ECHO_RESPONSE_SIZE || value[0] != WMR_ECHO_VERSION) return false;
    out->ping_id       = get_u16(&value[2]);
    out->rx_time_us    = get_u64(&value[4]);
    out->turnaround_us = get_u32(&value[12]);
    out->queue_depth   = get_u16(&value[16]);
    return true;
}

void wmr_echo_init(wmr_echo_state_t *s){
    memset(s, 0, sizeof(*s));
}

wmr_echo_result_t wmr_echo_on_write(wmr_echo_state_t *s, bool subscribed,
                                    const uint8_t *value, uint16_t size,
                                    uint64_t now_us, uint16_t queue_depth){
    uint16_t ping_id;
    if (!wmr_echo_parse_request(value, size, &ping_id)){ s->bad++; return WMR_ECHO_BAD; }
    if (!subscribed){ s->unsubscribed++; return WMR_ECHO_UNSUBSCRIBED; }
    if (s->pending){ s->busy++; return WMR_ECHO_BUSY; }
    s->pending = true;
    s->ping_id = ping_id;
    s->rx_time_us = now_us;
    s->queue_depth = queue_depth;
    s->accepted++;
    return WMR_ECHO_ACCEPTED;
}

bool wmr_echo_build(const wmr_echo_state_t *s, uint64_t now_us, uint8_t out[WMR_ECHO_RESPONSE_SIZE]){
    if (!s->pending) return false;
    const uint64_t turnaround = now_us > s->rx_time_us ? now_us - s->rx_time_us : 0;
    wmr_echo_response_t r = {
        .ping_id       = s->ping_id,
        .rx_time_us    = s->rx_time_us,
        .turnaround_us = turnaround > UINT32_MAX ? UINT32_MAX : (uint32_t) turnaround,
        .queue_depth   = s->queue_depth,
    };
    wmr_echo_pack(&r, out);
    return true;
}

void wmr_echo_sent(wmr_echo_state_t *s){
    if (!s->pending) return;
    s->pending = false;
    s->sent++;
}

void wmr_echo_cancel(wmr_echo_state_t *s){
    if (!s->pending) return;
    s->pending = false;
    s->cancelled++;
}
