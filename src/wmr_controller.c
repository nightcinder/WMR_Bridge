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

#define BTSTACK_FILE__ "wmr_controller.c"

#include <stdio.h>
#include <string.h>
#include "btstack_config.h"
#include "btstack.h"
#include "wmr_controller.h"

typedef struct {
    const char *name;
    const char *address_string;
    bd_addr_t   address;
    uint16_t    cid;                  /* 0 = no connection tracked */
    bool        connected;
    bool        descriptor_available;
    unsigned long reports;
} controller_t;

static controller_t controllers[WMR_NUM_CONTROLLERS] = {
    [WMR_HAND_LEFT]  = {.name = "LEFT",  .address_string = WMR_CFG_LEFT_ADDRESS},
    [WMR_HAND_RIGHT] = {.name = "RIGHT", .address_string = WMR_CFG_RIGHT_ADDRESS},
};

static uint8_t descriptor_storage[WMR_CFG_HID_DESCRIPTOR_STORAGE];
static wmr_report_sink_t report_sink;
static bool stack_ready;

/* True from the start of a connection attempt (outgoing or accepted
 * incoming) until its HID descriptor arrives or it fails. Only one attempt
 * is allowed at a time so the controllers do not race each other. */
static bool connect_pending;

static controller_t *controller_for_cid(uint16_t cid){
    if (!cid) return NULL;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (controllers[i].cid == cid) return &controllers[i];
    }
    return NULL;
}

static controller_t *controller_for_address(const bd_addr_t address){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (memcmp(controllers[i].address, address, sizeof(bd_addr_t)) == 0) return &controllers[i];
    }
    return NULL;
}

static void hid_packet_handler(uint8_t packet_type, uint16_t channel,
                               uint8_t *packet, uint16_t size);

void wmr_controller_init(wmr_report_sink_t sink){
    report_sink = sink;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        sscanf_bd_addr(controllers[i].address_string, controllers[i].address);
    }
    hid_host_init(descriptor_storage, sizeof(descriptor_storage));
    hid_host_register_packet_handler(hid_packet_handler);
}

int wmr_controller_hand_for_address(const uint8_t address[6]){
    controller_t *c = controller_for_address(address);
    return c ? (int) (c - controllers) : -1;
}

const char *wmr_controller_name(unsigned hand){
    return hand < WMR_NUM_CONTROLLERS ? controllers[hand].name : "?";
}

uint16_t wmr_controller_cid(unsigned hand){
    return hand < WMR_NUM_CONTROLLERS ? controllers[hand].cid : 0;
}

void wmr_controller_get_status(unsigned hand, wmr_controller_status_t *out){
    memset(out, 0, sizeof(*out));
    if (hand >= WMR_NUM_CONTROLLERS) return;
    out->connected            = controllers[hand].connected;
    out->descriptor_available = controllers[hand].descriptor_available;
    out->reports              = (uint32_t) controllers[hand].reports;
}

/* ---- Console-driven requests ------------------------------------------- */

void wmr_controller_connect(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    controller_t *c = &controllers[hand];
    if (!stack_ready){ printf("Stack not ready\n"); return; }
    if (connect_pending){ printf("Another connection/discovery is pending\n"); return; }
    if (c->cid){ printf("%s already open or opening, cid=%u\n", c->name, c->cid); return; }
    c->reports = 0;
    c->descriptor_available = false;
    connect_pending = true;
    printf("Connecting %s %s\n", c->name, c->address_string);
    uint8_t status = hid_host_connect(c->address, HID_PROTOCOL_MODE_REPORT, &c->cid);
    if (status != ERROR_CODE_SUCCESS){
        printf("%s connect request failed: 0x%02x\n", c->name, status);
        c->cid = 0;
        connect_pending = false;
    }
}

void wmr_controller_disconnect(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    controller_t *c = &controllers[hand];
    if (!c->cid){ printf("%s has no connection\n", c->name); return; }
    hid_host_disconnect(c->cid);
}

wmr_bond_reset_result_t wmr_controller_forget_bond(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return WMR_BOND_RESET_BUSY;
    controller_t *c = &controllers[hand];
    if (connect_pending || c->cid != 0 || c->connected) return WMR_BOND_RESET_BUSY;
    gap_drop_link_key_for_bd_addr(c->address);
    return WMR_BOND_RESET_OK;
}

/* ---- HCI events --------------------------------------------------------- */

void wmr_controller_on_hci_event(uint8_t *packet, uint16_t size){
    (void)size;
    bd_addr_t address;
    switch (hci_event_packet_get_type(packet)){
        case BTSTACK_EVENT_STATE:
            stack_ready = btstack_event_state_get_state(packet) == HCI_STATE_WORKING;
            break;
        case HCI_EVENT_PIN_CODE_REQUEST:
            hci_event_pin_code_request_get_bd_addr(packet, address);
            printf("Legacy PIN request rejected\n");
            gap_pin_code_negative(address);
            break;
        case HCI_EVENT_USER_CONFIRMATION_REQUEST:
            hci_event_user_confirmation_request_get_bd_addr(packet, address);
            printf("Accepting SSP confirmation from %s (test setup)\n", bd_addr_to_str(address));
            gap_ssp_confirmation_response(address);
            break;
        default:
            break;
    }
}

/* ---- HID host events ---------------------------------------------------- */

/* Incoming connections are how a controller reconnects on its own after it
 * wakes or is power-cycled. The CID must be recorded BEFORE accepting so the
 * open/descriptor/report events that follow are attributed to the right
 * hand. Removing this path breaks controller reconnects. */
static void handle_incoming_connection(const uint8_t *packet){
    uint16_t cid    = hid_subevent_incoming_connection_get_hid_cid(packet);
    uint8_t  status = hid_subevent_incoming_connection_get_status(packet);
    bd_addr_t address;
    hid_subevent_incoming_connection_get_address(packet, address);
    printf("Incoming HID: address=%s cid=%u status=0x%02x\n",
           bd_addr_to_str(address), cid, status);
    if (status != ERROR_CODE_SUCCESS) return;

    controller_t *c = controller_for_address(address);
    if (!c){
        printf("Incoming device is not LEFT or RIGHT; not accepting\n");
        return;
    }
    if (c->cid != 0 && c->cid != cid){
        printf("%s already tracked with cid=%u; not accepting different cid=%u\n",
               c->name, c->cid, cid);
        return;
    }
    c->cid = cid;
    c->connected = false;
    c->descriptor_available = false;
    c->reports = 0;
    connect_pending = true;
    printf("%s accepting incoming HID connection, cid=%u\n", c->name, cid);
    hid_host_accept_connection(cid, HID_PROTOCOL_MODE_REPORT);
}

static void handle_report(const uint8_t *packet){
    uint16_t cid = hid_subevent_report_get_hid_cid(packet);
    controller_t *c = controller_for_cid(cid);
    if (!c) return;
    const uint8_t *report = hid_subevent_report_get_report(packet);
    uint16_t length = hid_subevent_report_get_report_len(packet);
    c->reports++;
    /* Hand EVERY report over before any logging. The report buffer is only
     * valid during this callback; the sink copies what it keeps. */
    if (report_sink) report_sink((unsigned) (c - controllers), report, length);
    if (c->reports == 1){
        printf("%s first report: cid=%u frame_len=%u descriptor=%s\n",
               c->name, cid, length, c->descriptor_available ? "available" : "not available");
#if WMR_CFG_REPORT_HEXDUMP
        printf_hexdump(report, length);
#endif
    }
}

static void hid_packet_handler(uint8_t packet_type, uint16_t channel,
                               uint8_t *packet, uint16_t size){
    (void)channel;
    (void)size;
    if (packet_type != HCI_EVENT_PACKET) return;
    if (hci_event_packet_get_type(packet) != HCI_EVENT_HID_META) return;
    uint16_t cid;
    uint8_t status;
    controller_t *c;
    switch (hci_event_hid_meta_get_subevent_code(packet)){
        case HID_SUBEVENT_INCOMING_CONNECTION:
            handle_incoming_connection(packet);
            break;
        case HID_SUBEVENT_CONNECTION_OPENED:
            cid = hid_subevent_connection_opened_get_hid_cid(packet);
            status = hid_subevent_connection_opened_get_status(packet);
            c = controller_for_cid(cid);
            if (!c){
                printf("Untracked HID open cid=%u status=0x%02x\n", cid, status);
                if (status == ERROR_CODE_SUCCESS) hid_host_disconnect(cid);
                connect_pending = false;
                break;
            }
            if (status != ERROR_CODE_SUCCESS){
                printf("%s connection failed: 0x%02x\n", c->name, status);
                c->cid = 0;
                c->connected = false;
                connect_pending = false;
                break;
            }
            c->connected = true;
            c->descriptor_available = false;
            printf("%s connected, cid=%u; waiting for descriptor\n", c->name, cid);
            break;
        case HID_SUBEVENT_DESCRIPTOR_AVAILABLE:
            cid = hid_subevent_descriptor_available_get_hid_cid(packet);
            status = hid_subevent_descriptor_available_get_status(packet);
            c = controller_for_cid(cid);
            if (!c) break;
            c->descriptor_available = status == ERROR_CODE_SUCCESS;
            printf("%s descriptor status=0x%02x\n", c->name, status);
            connect_pending = false;
            break;
        case HID_SUBEVENT_REPORT:
            handle_report(packet);
            break;
        case HID_SUBEVENT_SET_PROTOCOL_RESPONSE:
            status = hid_subevent_set_protocol_response_get_handshake_status(packet);
            printf("SET_PROTOCOL handshake status=0x%02x\n", status);
            break;
        case HID_SUBEVENT_CONNECTION_CLOSED:
            cid = hid_subevent_connection_closed_get_hid_cid(packet);
            c = controller_for_cid(cid);
            if (!c) break;
            printf("%s disconnected, total reports=%lu\n", c->name, c->reports);
            if (!c->connected || !c->descriptor_available) connect_pending = false;
            c->cid = 0;
            c->connected = false;
            c->descriptor_available = false;
            break;
        default:
            break;
    }
}
