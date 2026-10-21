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

#define BTSTACK_FILE__ "wmr_protocol.c"

#include <string.h>
#include "wmr_protocol.h"

/* Little-endian helpers; local so this file has no BTstack dependency. */
static void put_u16(uint8_t *p, uint16_t v){
    p[0] = (uint8_t) v;
    p[1] = (uint8_t) (v >> 8);
}
static void put_u32(uint8_t *p, uint32_t v){
    p[0] = (uint8_t) v;
    p[1] = (uint8_t) (v >> 8);
    p[2] = (uint8_t) (v >> 16);
    p[3] = (uint8_t) (v >> 24);
}
static uint16_t get_u16(const uint8_t *p){
    return (uint16_t) (p[0] | (p[1] << 8));
}
static uint32_t get_u32(const uint8_t *p){
    return (uint32_t) p[0] | ((uint32_t) p[1] << 8) |
           ((uint32_t) p[2] << 16) | ((uint32_t) p[3] << 24);
}

uint16_t wmr_proto_notification_capacity(uint16_t att_mtu){
    if (att_mtu <= WMR_PROTO_HEADER_SIZE + WMR_ATT_NOTIFY_OVERHEAD) return 0;
    uint16_t capacity = att_mtu - WMR_ATT_NOTIFY_OVERHEAD;
    if (capacity > WMR_CFG_MAX_NOTIFICATION) capacity = WMR_CFG_MAX_NOTIFICATION;
    return capacity;
}

uint16_t wmr_proto_encode_fragment(const wmr_frame_t *frame, uint16_t offset,
                                   uint16_t capacity, uint8_t *out,
                                   uint16_t *chunk_length){
    if (!frame || !out || capacity <= WMR_PROTO_HEADER_SIZE) return 0;
    if (offset >= frame->length) return 0;
    uint16_t chunk = frame->length - offset;
    if (chunk > capacity - WMR_PROTO_HEADER_SIZE) chunk = capacity - WMR_PROTO_HEADER_SIZE;
    out[0] = WMR_PROTO_MAGIC;
    out[1] = frame->hand;
    put_u32(out + 2, frame->sequence);
    put_u16(out + 6, frame->length);
    put_u16(out + 8, offset);
    put_u16(out + 10, chunk);
    memcpy(out + WMR_PROTO_HEADER_SIZE, frame->data + offset, chunk);
    if (chunk_length) *chunk_length = chunk;
    return (uint16_t) (WMR_PROTO_HEADER_SIZE + chunk);
}

void wmr_reassembler_init(wmr_reassembler_t *r){
    memset(r, 0, sizeof(*r));
}

static void abandon(wmr_reassembler_t *r, unsigned hand){
    if (r->slot[hand].active){
        r->frames_incomplete[hand]++;
        r->slot[hand].active = false;
    }
}

wmr_proto_result_t wmr_reassembler_push(wmr_reassembler_t *r,
                                        const uint8_t *packet, uint16_t size,
                                        wmr_frame_t *out){
    if (size < WMR_PROTO_HEADER_SIZE){ r->errors++; return WMR_PROTO_ERR_SHORT; }
    if (packet[0] != WMR_PROTO_MAGIC){ r->errors++; return WMR_PROTO_ERR_MAGIC; }
    unsigned hand = packet[1];
    if (hand >= WMR_NUM_CONTROLLERS){ r->errors++; return WMR_PROTO_ERR_HAND; }

    uint32_t sequence = get_u32(packet + 2);
    uint16_t total    = get_u16(packet + 6);
    uint16_t offset   = get_u16(packet + 8);
    uint16_t chunk    = get_u16(packet + 10);

    /* The chunk must exactly fill the rest of the notification, and the
     * fragment must lie within a non-empty frame of supported size. */
    if (chunk != size - WMR_PROTO_HEADER_SIZE || chunk == 0 ||
        total == 0 || total > WMR_CFG_MAX_FRAME ||
        offset >= total || chunk > total - offset){
        /* A malformed fragment poisons whatever was in progress. */
        abandon(r, hand);
        r->errors++;
        return WMR_PROTO_ERR_LENGTH;
    }

    wmr_reassembly_slot_t *s = &r->slot[hand];
    if (offset == 0){
        abandon(r, hand);              /* new frame supersedes a partial one */
        s->active   = true;
        s->sequence = sequence;
        s->total    = total;
        s->received = 0;
    } else if (!s->active || s->sequence != sequence ||
               s->total != total || s->received != offset){
        abandon(r, hand);
        r->errors++;
        return WMR_PROTO_ERR_OUT_OF_ORDER;
    }

    memcpy(s->data + offset, packet + WMR_PROTO_HEADER_SIZE, chunk);
    s->received = (uint16_t) (offset + chunk);
    if (s->received < s->total) return WMR_PROTO_PARTIAL;

    s->active = false;
    r->frames_complete[hand]++;
    if (out){
        out->hand     = (uint8_t) hand;
        out->sequence = s->sequence;
        out->length   = s->total;
        memcpy(out->data, s->data, s->total);
    }
    return WMR_PROTO_FRAME_COMPLETE;
}
