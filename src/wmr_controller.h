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
 * wmr_controller.h - Classic Bluetooth HID host side: the two WMR
 * controllers, their identities and their per-hand connection state.
 */

#ifndef WMR_CONTROLLER_H
#define WMR_CONTROLLER_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_config.h"

/* Receives every HID input report (A1, report ID, payload...). Runs in the
 * HID callback: copy the bytes if keeping them, and return quickly. */
typedef void (*wmr_report_sink_t)(unsigned hand, const uint8_t *report, uint16_t length);

typedef struct {
    bool     connected;
    bool     descriptor_available;
    uint32_t reports;   /* reports since the current connection started */
} wmr_controller_status_t;

typedef enum {
    WMR_BOND_RESET_OK = 0,
    WMR_BOND_RESET_BUSY      /* controller connected/connecting; nothing done */
} wmr_bond_reset_result_t;

/* Parse controller addresses, init HID host, register the HID handler. */
void wmr_controller_init(wmr_report_sink_t sink);

/* HCI events: pairing responses and stack-state tracking. */
void wmr_controller_on_hci_event(uint8_t *packet, uint16_t size);

/* Outgoing connection / disconnect requests (console driven). */
void wmr_controller_connect(unsigned hand);
void wmr_controller_disconnect(unsigned hand);

/* MANUAL BOND RESET: delete only this ESP32's stored link key for `hand`.
 * Never called automatically. Refused while the controller is connected or
 * a connection attempt is pending. */
wmr_bond_reset_result_t wmr_controller_forget_bond(unsigned hand);

const char *wmr_controller_name(unsigned hand);
void wmr_controller_get_status(unsigned hand, wmr_controller_status_t *out);
/* Raw HID cid (0 if none); for the console status line. */
uint16_t wmr_controller_cid(unsigned hand);

#endif /* WMR_CONTROLLER_H */
