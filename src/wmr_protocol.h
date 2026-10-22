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
 * wmr_protocol.h - raw-stream wire format: fragment encoder and reassembler.
 *
 * Pure C, no BTstack dependency, so it builds on a host for unit tests.
 * The wire format is specified in docs/PROTOCOL.md.
 */

#ifndef WMR_PROTOCOL_H
#define WMR_PROTOCOL_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_config.h"

/* One controller frame as captured (A1, report ID, payload...). */
typedef struct {
    uint32_t sequence;                    /* per-hand, starts at 1 */
    uint64_t rx_time_us;                  /* esp_timer time at receipt; 0 if unknown */
    uint16_t length;
    uint8_t  hand;
    uint8_t  data[WMR_CFG_MAX_FRAME];
} wmr_frame_t;

/* ---- Encoder ----------------------------------------------------------- */

/* Largest notification value for a negotiated ATT MTU, or 0 if the MTU is
 * too small to carry at least one payload byte. */
uint16_t wmr_proto_notification_capacity(uint16_t att_mtu);

/* Encode the fragment of `frame` that starts at `offset` into `out`.
 * `capacity` is the maximum notification value size
 * (see wmr_proto_notification_capacity). Returns the total bytes written
 * to `out` (header + chunk), or 0 on invalid arguments. `*chunk_length`
 * receives the payload bytes carried. `out` must hold `capacity` bytes. */
uint16_t wmr_proto_encode_fragment(const wmr_frame_t *frame, uint16_t offset,
                                   uint16_t capacity, uint8_t *out,
                                   uint16_t *chunk_length);

/* ---- Reassembler (reference implementation of the receiver rules) ------ */

typedef enum {
    WMR_PROTO_PARTIAL = 0,        /* valid fragment, frame not complete yet */
    WMR_PROTO_FRAME_COMPLETE,     /* `out` holds a complete frame */
    WMR_PROTO_ERR_SHORT,          /* shorter than a header */
    WMR_PROTO_ERR_MAGIC,
    WMR_PROTO_ERR_HAND,
    WMR_PROTO_ERR_LENGTH,         /* total/chunk/offset fields inconsistent */
    WMR_PROTO_ERR_OUT_OF_ORDER    /* fragment does not continue the frame */
} wmr_proto_result_t;

typedef struct {
    bool     active;              /* a frame is partially received */
    uint32_t sequence;
    uint16_t total;
    uint16_t received;
    uint8_t  data[WMR_CFG_MAX_FRAME];
} wmr_reassembly_slot_t;

typedef struct {
    wmr_reassembly_slot_t slot[WMR_NUM_CONTROLLERS];
    uint32_t frames_complete[WMR_NUM_CONTROLLERS];
    /* Partial frames abandoned because a new frame started, a fragment was
     * out of order, or the sequence changed mid-frame. */
    uint32_t frames_incomplete[WMR_NUM_CONTROLLERS];
    uint32_t errors;              /* malformed fragments */
} wmr_reassembler_t;

void wmr_reassembler_init(wmr_reassembler_t *r);

/* Feed one notification value. When WMR_PROTO_FRAME_COMPLETE is returned,
 * `out` is filled. A fragment that starts a new frame (offset 0) while one
 * is in progress abandons the old one and is itself accepted. */
wmr_proto_result_t wmr_reassembler_push(wmr_reassembler_t *r,
                                        const uint8_t *packet, uint16_t size,
                                        wmr_frame_t *out);

#endif /* WMR_PROTOCOL_H */
