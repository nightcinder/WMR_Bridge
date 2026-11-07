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
 * wmr_controller_driver.h - the controller-family dispatch table.
 *
 * One entry per model owns everything that differs between families: the
 * Bluetooth names per hand (plus an optional name for a one-device controller),
 * the HID input report predicate, the reply predicate and subcommand handshake,
 * the FF12/FF14 decoder(s), the wake feature report and whether the model speaks
 * the Microsoft FF13 config protocol. Discovery, the HID report handler and the
 * decoded stream look the entry up instead of branching on wmr_model_t.
 *
 * Pure C, no BTstack dependency, host-tested. Behaviour: docs/PSVR2.md,
 * docs/SWITCH.md and docs/PROTOCOL.md.
 */

#ifndef WMR_CONTROLLER_DRIVER_H
#define WMR_CONTROLLER_DRIVER_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "wmr_config.h"
#include "wmr_decoded.h"
#include "wmr_discovery.h"

typedef struct wmr_controller_driver {
    wmr_model_t model;
    const char *label;                            /* short name for logs and FF17 */
    const char *name[WMR_NUM_CONTROLLERS];        /* Bluetooth name per hand */
    const char *solo_name;                        /* extra name for a one-device controller; paired as LEFT */
    uint8_t     input_report_id;                  /* second byte of the HID frame */
    uint8_t     wake_feature_id;                  /* HID Feature Report that starts streaming; 0 = none */
    bool        subcommand_handshake;             /* output report 0x01 handshake after the descriptor */
    bool        serves_config_block;              /* model answers the Microsoft FF13 config read */
    bool      (*is_input_report)(const uint8_t *frame, uint16_t length);
    bool      (*is_reply_report)(const uint8_t *frame, uint16_t length);   /* non-input replies */
    unsigned  (*decode_samples)(const wmr_frame_t *frame,
                                uint8_t out[][WMR_DECODED_SIZE], unsigned max); /* optional */
    bool      (*decode)(const wmr_frame_t *frame, uint8_t out[WMR_DECODED_SIZE]);
} wmr_controller_driver_t;

/* Entry for `model`, or NULL for WMR_MODEL_COUNT / an out-of-range value. */
const wmr_controller_driver_t *wmr_controller_driver(wmr_model_t model);

/* Entry whose input predicate accepts `frame`, or NULL. The predicate only checks the
 * report shape; the decoder still validates the full frame. */
const wmr_controller_driver_t *wmr_controller_driver_for_frame(const uint8_t *frame, uint16_t length);

/* Entry whose Bluetooth name matches. Sets *hand (when non-NULL) to WMR_HAND_LEFT/RIGHT, or
 * -1 when the name is not a recognised controller. */
const wmr_controller_driver_t *wmr_controller_driver_for_name(const uint8_t *name, size_t len,
                                                              int *hand);

#endif /* WMR_CONTROLLER_DRIVER_H */
