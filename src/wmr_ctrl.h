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
 * wmr_ctrl.h - FF17 controller scan and status v2 characteristic.
 *
 * Writes: 1-2 bytes (opcode + optional hand).
 * Reads / Notifications: 20-byte Status v2 (default ATT MTU compatible).
 *
 * Pure C, no BTstack dependency, host-tested. Specified in docs/PROTOCOL.md
 * ("Controller scan and status v2, FF17").
 */

#ifndef WMR_CTRL_H
#define WMR_CTRL_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_config.h"

#define WMR_CTRL_VERSION        1
#define WMR_CTRL_STATUS_SIZE    20
#define WMR_CTRL_MIN_MTU        (WMR_CTRL_STATUS_SIZE + 3)   /* 23 (default ATT MTU) */

/* Opcodes for write requests */
#define WMR_CTRL_OP_NONE         0x00
#define WMR_CTRL_OP_DISCOVER     0x01
#define WMR_CTRL_OP_CONNECT      0x02
#define WMR_CTRL_OP_DISCONNECT   0x03
#define WMR_CTRL_OP_FORGET_ADDRS 0x04
#define WMR_CTRL_OP_FORGET_BOND  0x05

/* Result codes returned in status and ATT response */
#define WMR_CTRL_RES_OK            0x00
#define WMR_CTRL_RES_BUSY          0x01
#define WMR_CTRL_RES_NO_ADDRESS    0x02
#define WMR_CTRL_RES_NOTHING_TO_DO 0x03
#define WMR_CTRL_RES_NOT_CONNECTED 0x04
#define WMR_CTRL_RES_NOT_ENABLED   0x05
#define WMR_CTRL_RES_INVALID_PARAM 0x06

/* ATT application error base */
#define WMR_ATT_ERROR_CTRL_BASE    0x80

/* Discovery state */
#define WMR_CTRL_DISC_IDLE       0x00
#define WMR_CTRL_DISC_INQUIRY    0x01
#define WMR_CTRL_DISC_NAMES      0x02
#define WMR_CTRL_DISC_CONNECT    0x03

/* Per-hand status flags */
#define WMR_CTRL_FLAG_CONNECTED   (1u << 0)
#define WMR_CTRL_FLAG_CONNECTING  (1u << 1)
#define WMR_CTRL_FLAG_DESCRIPTOR  (1u << 2)

typedef struct {
    bool    connected;
    bool    connecting;
    bool    descriptor_available;
    uint8_t address_source;   /* 0: none, 1: override, 2: stored */
    uint8_t model;            /* 0: WMR, 1: PSVR2 */
    uint8_t address[6];       /* last 3 bytes (address[3..5]) packed into payload */
} wmr_ctrl_hand_status_t;

typedef struct {
    uint8_t version;
    wmr_ctrl_hand_status_t hands[WMR_NUM_CONTROLLERS];
    uint8_t discovery_state;
    uint8_t discovery_round;
    uint8_t last_command;
    uint8_t last_result;
} wmr_ctrl_status_t;

/* Parse a write command: 1 or 2 bytes.
 * Returns true if the opcode and format are valid, setting *opcode and *hand.
 * For OP_DISCOVER and OP_FORGET_ADDRS, hand defaults to 0 and may be omitted.
 * For OP_CONNECT, OP_DISCONNECT, and OP_FORGET_BOND, hand must be 0 or 1. */
bool wmr_ctrl_parse_command(const uint8_t *data, uint16_t len, uint8_t *opcode, uint8_t *hand);

/* Serialize status structure into 20 bytes (Status v2). */
void wmr_ctrl_pack_status(const wmr_ctrl_status_t *s, uint8_t out[WMR_CTRL_STATUS_SIZE]);

/* Unpack 20-byte status into status structure. Returns false if size or version is invalid. */
bool wmr_ctrl_unpack_status(const uint8_t *in, uint16_t len, wmr_ctrl_status_t *out);

/* Convert WMR_CTRL_RES_* code to ATT error status (0 for OK, 0x80 + res otherwise). */
uint8_t wmr_ctrl_res_to_att_error(uint8_t result);

#endif /* WMR_CTRL_H */
