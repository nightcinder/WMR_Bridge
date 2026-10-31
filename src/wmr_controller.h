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
 * controllers, their identities (found by name, docs/DISCOVERY.md) and their
 * per-hand connection state.
 */

#ifndef WMR_CONTROLLER_H
#define WMR_CONTROLLER_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_config.h"
#include "wmr_discovery.h"

/* Receives every HID input report (A1, report ID, payload...). Runs in the
 * HID callback: copy the bytes if keeping them, and return quickly. */
typedef void (*wmr_report_sink_t)(unsigned hand, const uint8_t *report, uint16_t length);

/* Where a hand's Bluetooth address came from. */
typedef enum {
    WMR_ADDR_NONE = 0,       /* unknown: not discovered yet */
    WMR_ADDR_OVERRIDE,       /* WMR_CFG_LEFT_ADDRESS / WMR_CFG_RIGHT_ADDRESS */
    WMR_ADDR_STORED          /* found by name and stored in NVS */
} wmr_addr_source_t;

typedef struct {
    bool     connected;
    bool     connecting;
    bool     descriptor_available;
    uint32_t reports;   /* reports since the current connection started */
    uint32_t other_reports;   /* neither input nor config reports; dropped, not forwarded */
    wmr_addr_source_t address_source;
    uint8_t  address[6];         /* valid unless address_source is WMR_ADDR_NONE */
    wmr_model_t model;           /* WMR or PSVR2; WMR_MODEL_WMR until identified */
} wmr_controller_status_t;

typedef enum {
    WMR_CONNECT_OK = 0,
    WMR_CONNECT_BUSY,
    WMR_CONNECT_NO_ADDRESS,
    WMR_CONNECT_NOT_ENABLED
} wmr_connect_result_t;

typedef enum {
    WMR_DISCONNECT_OK = 0,
    WMR_DISCONNECT_NOT_CONNECTED
} wmr_disconnect_result_t;

/* What FF17 CONNECT does for a hand that already has a HID channel. Separated from
 * wmr_controller.c so the host test covers the OK-vs-BUSY choice without a radio. */
typedef enum {
    WMR_CONNECT_GATE_START = 0,     /* no channel: start a new connection attempt */
    WMR_CONNECT_GATE_ACK,           /* open or opening: answer OK, start nothing */
    WMR_CONNECT_GATE_CLOSING        /* closing after a disconnect: answer BUSY */
} wmr_connect_gate_t;

static inline wmr_connect_gate_t wmr_controller_connect_gate(bool has_cid, bool disconnecting){
    if (!has_cid) return WMR_CONNECT_GATE_START;
    return disconnecting ? WMR_CONNECT_GATE_CLOSING : WMR_CONNECT_GATE_ACK;
}

typedef enum {
    WMR_BOND_RESET_OK = 0,
    WMR_BOND_RESET_BUSY,     /* controller connected/connecting; nothing done */
    WMR_BOND_RESET_NO_ADDRESS    /* the hand has no address, so there is no key to delete */
} wmr_bond_reset_result_t;

typedef enum {
    WMR_DISCOVERY_STARTED = 0,
    WMR_DISCOVERY_NOTHING_TO_DO,     /* both hands have an address */
    WMR_DISCOVERY_BUSY               /* stack not ready, already running, or a connection pending */
} wmr_discovery_start_result_t;

typedef enum {
    WMR_FORGET_OK = 0,
    WMR_FORGET_BUSY          /* a controller is connected or connecting, or discovery runs */
} wmr_forget_result_t;

/* Apply compile-time address overrides, init HID host, register the HID handler. Stored
 * addresses are loaded when the stack is up. */
void wmr_controller_init(wmr_report_sink_t sink);

/* HCI events: pairing responses and stack-state tracking. */
void wmr_controller_on_hci_event(uint8_t *packet, uint16_t size);

/* Outgoing connection / disconnect requests. */
wmr_connect_result_t wmr_controller_connect(unsigned hand);
wmr_disconnect_result_t wmr_controller_disconnect(unsigned hand);

/* MANUAL BOND RESET: delete only this ESP32's stored link key for `hand`.
 * Never called automatically. Refused while the controller is connected or
 * a connection attempt is pending. */
wmr_bond_reset_result_t wmr_controller_forget_bond(unsigned hand);

/* Look for every hand without an address: bounded inquiry rounds, remote name requests for
 * devices without a name in the inquiry response, then connect (and pair) what was found.
 * Also started by itself after boot (WMR_CFG_DISCOVERY_AUTOSTART). */
wmr_discovery_start_result_t wmr_controller_discover(void);

/* Delete the stored (discovered) addresses of both hands; overrides stay. Link keys are NOT
 * deleted (that is the bond reset). Refused while a controller is connected or connecting. */
wmr_forget_result_t wmr_controller_forget_addresses(void);

void wmr_controller_print_addresses(void);
const char *wmr_controller_address_source_name(wmr_addr_source_t source);

const char *wmr_controller_name(unsigned hand);
/* Model of `hand` (WMR or PSVR2); WMR_MODEL_WMR when nothing has identified it yet. */
wmr_model_t wmr_controller_model(unsigned hand);
/* Which hand has this Bluetooth address, or -1. */
int wmr_controller_hand_for_address(const uint8_t address[6]);
void wmr_controller_get_status(unsigned hand, wmr_controller_status_t *out);
/* Raw HID cid (0 if none); for the console status line. */
uint16_t wmr_controller_cid(unsigned hand);

/* LEVERS `g`: drop the V1 gate/hold state after the runtime stagger mode changes. The
 * caller must ensure both controllers are disconnected. No-op in the release build. */
void wmr_controller_reset_stagger(void);

/* Discovery state and round (for FF17 status). */
uint8_t wmr_controller_discovery_phase(void);
uint8_t wmr_controller_discovery_round(void);

#endif /* WMR_CONTROLLER_H */
