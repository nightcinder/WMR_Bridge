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

#define BTSTACK_FILE__ "wmr_cfgmgr.c"

#include <stdio.h>
#include <string.h>
#include "btstack_config.h"
#include "btstack.h"
#include "wmr_config.h"
#include "wmr_cfgmgr.h"
#include "wmr_controller.h"

#define CFG_BLOCK_ID        0x02    /* the obfuscated JSON config block */
#define CFG_START_DELAY_MS  1000    /* let the input stream settle before the first request */

typedef enum { PHASE_NONE = 0, PHASE_START, PHASE_SPACING, PHASE_TIMEOUT, PHASE_ENABLE } phase_t;

typedef struct {
    wmr_cfg_blob_state_t state;
    wmr_cfg_reader_t     reader;
    uint8_t              blob[WMR_CFG_MAX_BLOCK];
    uint32_t             total;
    uint32_t             crc;
    phase_t              phase;
    btstack_timer_source_t timer;
    unsigned             hand;
    uint32_t             send_errors;
    unsigned             enable_step;    /* next streaming-enable command to send */
} hand_t;

static hand_t hands[WMR_NUM_CONTROLLERS];
static wmr_cfgmgr_listener_t listener;

static const char *hand_name(unsigned hand){
    return hand == WMR_HAND_LEFT ? "LEFT" : "RIGHT";
}

static void set_state(hand_t *h, wmr_cfg_blob_state_t state){
    if (h->state == state) return;
    h->state = state;
    if (listener) listener(h->hand);
}

static void arm(hand_t *h, phase_t phase, uint32_t ms){
    h->phase = phase;
    btstack_run_loop_remove_timer(&h->timer);
    btstack_run_loop_set_timer(&h->timer, ms);
    btstack_run_loop_add_timer(&h->timer);
}

static void disarm(hand_t *h){
    h->phase = PHASE_NONE;
    btstack_run_loop_remove_timer(&h->timer);
}

/* Send the reader's current request and wait up to the timeout for the answer. A send error
 * is handled like a missing answer: the timeout counts a retry. */
static void send_request(hand_t *h){
    uint8_t command[WMR_CFG_CMD_SIZE];
    if (!wmr_cfg_reader_request(&h->reader, command)) return;
    uint16_t cid = wmr_controller_cid(h->hand);
    uint8_t status = cid ? hid_host_send_report(cid, command[0], &command[1], WMR_CFG_CMD_SIZE - 1)
                         : ERROR_CODE_COMMAND_DISALLOWED;
    if (status != ERROR_CODE_SUCCESS){
        h->send_errors++;
        printf("FF13 %s: config request not sent (0x%02x)\n", hand_name(h->hand), status);
    }
    arm(h, PHASE_TIMEOUT, WMR_CFG_TIMEOUT_MS);
}

/* Block reads make the controller stop sending input reports (seen on hardware after PR #17:
 * a few hundred reports, then none). Monado puts it back into streaming afterwards with two
 * enable commands; send them after every read, successful or not. */
static void enable_streaming(hand_t *h){
    if (h->enable_step >= WMR_CFG_ENABLE_STEPS) return;
    uint8_t command[WMR_CFG_CMD_SIZE];
    wmr_cfg_pack_enable_streaming(h->enable_step, command);
    h->enable_step++;
    uint16_t cid = wmr_controller_cid(h->hand);
    uint8_t status = cid ? hid_host_send_report(cid, command[0], &command[1], WMR_CFG_CMD_SIZE - 1)
                         : ERROR_CODE_COMMAND_DISALLOWED;
    if (status != ERROR_CODE_SUCCESS){
        h->send_errors++;
        printf("FF13 %s: streaming-enable command not sent (0x%02x)\n", hand_name(h->hand), status);
    }
    if (h->enable_step < WMR_CFG_ENABLE_STEPS) arm(h, PHASE_ENABLE, WMR_CFG_SPACING_MS);
}

static void finish(hand_t *h){
    disarm(h);
    h->enable_step = 0;
    arm(h, PHASE_ENABLE, WMR_CFG_SPACING_MS);
    if (h->reader.state == WMR_CFG_DONE){
        h->total = h->reader.total;
        h->crc = wmr_cfg_crc32(h->blob, h->total);
        printf("FF13 %s: config block READY, %lu bytes, crc32=%08lx\n", hand_name(h->hand),
               (unsigned long) h->total, (unsigned long) h->crc);
        set_state(h, WMR_CFG_READY);
    } else {
        printf("FF13 %s: config block read FAILED (got %lu of %lu bytes)\n", hand_name(h->hand),
               (unsigned long) h->reader.got, (unsigned long) h->reader.total);
        h->total = 0;
        set_state(h, WMR_CFG_BLOCK_FAILED);
    }
}

static void timer_fire(btstack_timer_source_t *timer){
    hand_t *h = (hand_t *) btstack_run_loop_get_timer_context(timer);
    phase_t phase = h->phase;
    h->phase = PHASE_NONE;
    if (phase == PHASE_ENABLE){
        enable_streaming(h);
        return;
    }
    if (h->state != WMR_CFG_READING) return;
    switch (phase){
        case PHASE_START:
            wmr_cfg_reader_start(&h->reader, CFG_BLOCK_ID);
            send_request(h);
            break;
        case PHASE_SPACING:
            send_request(h);
            break;
        case PHASE_TIMEOUT:
            wmr_cfg_reader_on_timeout(&h->reader);
            if (h->reader.state == WMR_CFG_FAILED) finish(h);
            else send_request(h);
            break;
        default:
            break;
    }
}

void wmr_cfgmgr_init(wmr_cfgmgr_listener_t l){
    listener = l;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        hand_t *h = &hands[i];
        memset(h, 0, sizeof(*h));
        h->hand = i;
        wmr_cfg_reader_init(&h->reader, h->blob, sizeof(h->blob));
        h->timer.process = timer_fire;
        btstack_run_loop_set_timer_context(&h->timer, h);
    }
}

void wmr_cfgmgr_on_descriptor(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    if (!WMR_CFG_FF13_READ) return;
    hand_t *h = &hands[hand];
    if (h->state == WMR_CFG_READY || h->state == WMR_CFG_READING) return;
    /* NOT_READ, or FAILED on an earlier connection: try again. */
    printf("FF13 %s: reading config block in %u ms\n", hand_name(hand), CFG_START_DELAY_MS);
    set_state(h, WMR_CFG_READING);
    arm(h, PHASE_START, CFG_START_DELAY_MS);
}

void wmr_cfgmgr_on_report(unsigned hand, const uint8_t *report, uint16_t length){
    if (hand >= WMR_NUM_CONTROLLERS || length < 2) return;
    hand_t *h = &hands[hand];
    if (h->state != WMR_CFG_READING) return;
    wmr_cfg_response_t resp;
    /* Skip the 0xA1 header; the response starts at its report ID. */
    if (!wmr_cfg_parse_response(&report[1], (uint16_t) (length - 1), &resp)) return;
    if (!wmr_cfg_reader_on_response(&h->reader, &resp)) return;
    if (h->reader.state == WMR_CFG_DONE || h->reader.state == WMR_CFG_FAILED){
        finish(h);
        return;
    }
    arm(h, PHASE_SPACING, WMR_CFG_SPACING_MS);
}

void wmr_cfgmgr_on_closed(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    hand_t *h = &hands[hand];
    disarm(h);
    if (h->state == WMR_CFG_READING) set_state(h, WMR_CFG_NOT_READ);
}

wmr_cfg_blob_state_t wmr_cfgmgr_get(unsigned hand, const uint8_t **blob, uint32_t *total){
    if (hand >= WMR_NUM_CONTROLLERS){
        *blob = NULL;
        *total = 0;
        return WMR_CFG_NOT_READ;
    }
    hand_t *h = &hands[hand];
    *blob = h->blob;
    *total = h->state == WMR_CFG_READY ? h->total : 0;
    return h->state;
}

void wmr_cfgmgr_print(void){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        const hand_t *h = &hands[i];
        printf("  FF13 %s state=%u total=%lu crc32=%08lx send_errors=%lu\n", hand_name(i),
               (unsigned) h->state, (unsigned long) h->total, (unsigned long) h->crc,
               (unsigned long) h->send_errors);
    }
}
