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

/* wmr_controller_driver.c - the controller-family table; see wmr_controller_driver.h. */

#define BTSTACK_FILE__ "wmr_controller_driver.c"

#include <string.h>
#include "wmr_controller_driver.h"
#include "wmr_decode.h"
#include "wmr_psvr2.h"
#include "wmr_switch.h"

/* Table order matches wmr_model_t so a lookup is a bounds check and an index. */
static const wmr_controller_driver_t drivers[WMR_MODEL_COUNT] = {
    {
        .model               = WMR_MODEL_WMR,
        .label               = "WMR",
        .name                = { WMR_DISCOVERY_NAME_LEFT, WMR_DISCOVERY_NAME_RIGHT },
        .input_report_id     = WMR_DECODE_REPORT_ID,
        .wake_feature_id     = 0,
        .subcommand_handshake = false,
        .serves_config_block = true,
        .is_input_report     = wmr_decode_frame_is_wmr,
        .is_reply_report     = NULL,
        .decode_samples      = NULL,
        .decode              = wmr_decoded_from_wmr_frame,
    },
    {
        .model               = WMR_MODEL_PSVR2,
        .label               = "PSVR2",
        .name                = { WMR_DISCOVERY_NAME_PSVR2_LEFT, WMR_DISCOVERY_NAME_PSVR2_RIGHT },
        .input_report_id     = WMR_PSVR2_INPUT_REPORT_ID,
        .wake_feature_id     = WMR_PSVR2_WAKE_FEATURE_ID,
        .subcommand_handshake = false,
        .serves_config_block = false,
        .is_input_report     = wmr_psvr2_is_input_report,
        .is_reply_report     = NULL,
        .decode_samples      = NULL,
        .decode              = wmr_decoded_from_psvr2_frame,
    },
    {
        .model               = WMR_MODEL_SWITCH,
        .label               = "Switch",
        .name                = { WMR_DISCOVERY_NAME_SWITCH_LEFT, WMR_DISCOVERY_NAME_SWITCH_RIGHT },
        .solo_name           = WMR_DISCOVERY_NAME_SWITCH_PRO,
        .input_report_id     = WMR_SWITCH_INPUT_REPORT_ID,
        .wake_feature_id     = 0,
        .subcommand_handshake = true,
        .serves_config_block = false,
        .is_input_report     = wmr_switch_is_input_report,
        .is_reply_report     = wmr_switch_is_reply_report,
        .decode_samples      = wmr_switch_decode_samples,
        .decode              = wmr_decoded_from_switch_frame,
    },
};

const wmr_controller_driver_t *wmr_controller_driver(wmr_model_t model){
    if ((unsigned) model >= (unsigned) WMR_MODEL_COUNT) return NULL;
    return &drivers[model];
}

const wmr_controller_driver_t *wmr_controller_driver_for_frame(const uint8_t *frame, uint16_t length){
    for (unsigned i = 0; i < WMR_MODEL_COUNT; i++){
        if (drivers[i].is_input_report(frame, length)) return &drivers[i];
    }
    return NULL;
}

static char lower(uint8_t c){
    return (char) (c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c);
}

static bool name_equals(const uint8_t *name, size_t len, const char *want){
    while (len && (name[len - 1] == 0 || name[len - 1] == ' ')) len--;
    if (len != strlen(want)) return false;
    for (size_t i = 0; i < len; i++){
        if (lower(name[i]) != lower((uint8_t) want[i])) return false;
    }
    return true;
}

const wmr_controller_driver_t *wmr_controller_driver_for_name(const uint8_t *name, size_t len,
                                                              int *hand){
    if (hand) *hand = -1;
    if (!name) return NULL;
    for (unsigned i = 0; i < WMR_MODEL_COUNT; i++){
        for (unsigned h = 0; h < WMR_NUM_CONTROLLERS; h++){
            if (!name_equals(name, len, drivers[i].name[h])) continue;
            if (hand) *hand = (int) h;
            return &drivers[i];
        }
        /* A one-device controller (the Switch Pro Controller) has no hand in its name;
         * pair it as LEFT so both of its sticks reach the decoded stream. */
        if (drivers[i].solo_name && name_equals(name, len, drivers[i].solo_name)){
            if (hand) *hand = WMR_HAND_LEFT;
            return &drivers[i];
        }
    }
    return NULL;
}

const char *wmr_model_name(wmr_model_t model){
    const wmr_controller_driver_t *driver = wmr_controller_driver(model);
    return driver ? driver->label : "?";
}
