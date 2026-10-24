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

/* wmr_ctrl.c - FF17 controller scan and status v2 characteristic logic. */

#define BTSTACK_FILE__ "wmr_ctrl.c"

#include <string.h>
#include "wmr_ctrl.h"

bool wmr_ctrl_parse_command(const uint8_t *data, uint16_t len, uint8_t *opcode, uint8_t *hand){
    if (!data || len < 1 || len > 2) return false;
    uint8_t op = data[0];
    uint8_t h = len > 1 ? data[1] : 0;

    switch (op){
        case WMR_CTRL_OP_DISCOVER:
        case WMR_CTRL_OP_FORGET_ADDRS:
            *opcode = op;
            *hand = 0;
            return true;

        case WMR_CTRL_OP_CONNECT:
        case WMR_CTRL_OP_DISCONNECT:
        case WMR_CTRL_OP_FORGET_BOND:
            if (len != 2 || h >= WMR_NUM_CONTROLLERS) return false;
            *opcode = op;
            *hand = h;
            return true;

        default:
            return false;
    }
}

void wmr_ctrl_pack_status(const wmr_ctrl_status_t *s, uint8_t out[WMR_CTRL_STATUS_SIZE]){
    memset(out, 0, WMR_CTRL_STATUS_SIZE);
    out[0] = s->version ? s->version : WMR_CTRL_VERSION;

    /* LEFT hand: bytes 1..7 */
    uint8_t left_flags = 0;
    if (s->hands[WMR_HAND_LEFT].connected) left_flags |= WMR_CTRL_FLAG_CONNECTED;
    if (s->hands[WMR_HAND_LEFT].connecting) left_flags |= WMR_CTRL_FLAG_CONNECTING;
    if (s->hands[WMR_HAND_LEFT].descriptor_available) left_flags |= WMR_CTRL_FLAG_DESCRIPTOR;
    out[1] = left_flags;
    out[2] = s->hands[WMR_HAND_LEFT].address_source;
    out[3] = s->hands[WMR_HAND_LEFT].model;
    out[4] = s->hands[WMR_HAND_LEFT].address[3];
    out[5] = s->hands[WMR_HAND_LEFT].address[4];
    out[6] = s->hands[WMR_HAND_LEFT].address[5];
    out[7] = 0;

    /* RIGHT hand: bytes 8..14 */
    uint8_t right_flags = 0;
    if (s->hands[WMR_HAND_RIGHT].connected) right_flags |= WMR_CTRL_FLAG_CONNECTED;
    if (s->hands[WMR_HAND_RIGHT].connecting) right_flags |= WMR_CTRL_FLAG_CONNECTING;
    if (s->hands[WMR_HAND_RIGHT].descriptor_available) right_flags |= WMR_CTRL_FLAG_DESCRIPTOR;
    out[8]  = right_flags;
    out[9]  = s->hands[WMR_HAND_RIGHT].address_source;
    out[10] = s->hands[WMR_HAND_RIGHT].model;
    out[11] = s->hands[WMR_HAND_RIGHT].address[3];
    out[12] = s->hands[WMR_HAND_RIGHT].address[4];
    out[13] = s->hands[WMR_HAND_RIGHT].address[5];
    out[14] = 0;

    /* Global state: bytes 15..19 */
    out[15] = s->discovery_state;
    out[16] = s->discovery_round;
    out[17] = s->last_command;
    out[18] = s->last_result;
    out[19] = 0;
}

bool wmr_ctrl_unpack_status(const uint8_t *in, uint16_t len, wmr_ctrl_status_t *out){
    if (!in || len < WMR_CTRL_STATUS_SIZE) return false;
    if (in[0] != WMR_CTRL_VERSION) return false;

    memset(out, 0, sizeof(*out));
    out->version = in[0];

    /* LEFT hand */
    out->hands[WMR_HAND_LEFT].connected            = (in[1] & WMR_CTRL_FLAG_CONNECTED) != 0;
    out->hands[WMR_HAND_LEFT].connecting           = (in[1] & WMR_CTRL_FLAG_CONNECTING) != 0;
    out->hands[WMR_HAND_LEFT].descriptor_available = (in[1] & WMR_CTRL_FLAG_DESCRIPTOR) != 0;
    out->hands[WMR_HAND_LEFT].address_source       = in[2];
    out->hands[WMR_HAND_LEFT].model                = in[3];
    out->hands[WMR_HAND_LEFT].address[3]           = in[4];
    out->hands[WMR_HAND_LEFT].address[4]           = in[5];
    out->hands[WMR_HAND_LEFT].address[5]           = in[6];

    /* RIGHT hand */
    out->hands[WMR_HAND_RIGHT].connected            = (in[8] & WMR_CTRL_FLAG_CONNECTED) != 0;
    out->hands[WMR_HAND_RIGHT].connecting           = (in[8] & WMR_CTRL_FLAG_CONNECTING) != 0;
    out->hands[WMR_HAND_RIGHT].descriptor_available = (in[8] & WMR_CTRL_FLAG_DESCRIPTOR) != 0;
    out->hands[WMR_HAND_RIGHT].address_source       = in[9];
    out->hands[WMR_HAND_RIGHT].model                = in[10];
    out->hands[WMR_HAND_RIGHT].address[3]           = in[11];
    out->hands[WMR_HAND_RIGHT].address[4]           = in[12];
    out->hands[WMR_HAND_RIGHT].address[5]           = in[13];

    /* Global state */
    out->discovery_state = in[15];
    out->discovery_round = in[16];
    out->last_command    = in[17];
    out->last_result     = in[18];

    return true;
}

uint8_t wmr_ctrl_res_to_att_error(uint8_t result){
    if (result == WMR_CTRL_RES_OK) return 0;
    return (uint8_t)(WMR_ATT_ERROR_CTRL_BASE + result);
}
