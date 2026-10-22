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
#include "btstack_tlv.h"
#include "wmr_cfg.h"
#include "wmr_cfgmgr.h"
#include "wmr_controller.h"
#include "wmr_discovery.h"
#include "wmr_linkdiag.h"

typedef struct {
    const char *name;
    const char *override;             /* compile-time address, or NULL */
    wmr_addr_source_t source;         /* where `address` came from; NONE = unknown */
    bd_addr_t   address;
    uint16_t    cid;                  /* 0 = no connection tracked */
    bool        connected;
    bool        descriptor_available;
    unsigned long reports;
    unsigned long other_reports;   /* neither input nor config reports: not forwarded */
} controller_t;

#ifdef WMR_CFG_LEFT_ADDRESS
#define LEFT_OVERRIDE WMR_CFG_LEFT_ADDRESS
#else
#define LEFT_OVERRIDE NULL
#endif
#ifdef WMR_CFG_RIGHT_ADDRESS
#define RIGHT_OVERRIDE WMR_CFG_RIGHT_ADDRESS
#else
#define RIGHT_OVERRIDE NULL
#endif

static controller_t controllers[WMR_NUM_CONTROLLERS] = {
    [WMR_HAND_LEFT]  = {.name = "LEFT",  .override = LEFT_OVERRIDE},
    [WMR_HAND_RIGHT] = {.name = "RIGHT", .override = RIGHT_OVERRIDE},
};

static uint8_t descriptor_storage[WMR_CFG_HID_DESCRIPTOR_STORAGE];
static wmr_report_sink_t report_sink;
static bool stack_ready;

/* True from the start of a connection attempt (outgoing or accepted
 * incoming) until its HID descriptor arrives or it fails. Only one attempt
 * is allowed at a time so the controllers do not race each other. */
static bool connect_pending;

/* ---- Discovery state (docs/DISCOVERY.md) -------------------------------- */

typedef enum {
    DISC_IDLE = 0,
    DISC_INQUIRY,       /* inquiry running */
    DISC_NAMES,         /* asking devices without a name in the inquiry response */
    DISC_CONNECT        /* connecting (and pairing) the controllers just found */
} disc_phase_t;

static wmr_discovery_t disc;
static disc_phase_t disc_phase;
static unsigned disc_round;
static unsigned disc_connect_mask;      /* hands found in this round, still to connect */
static btstack_timer_source_t disc_timer;

/* One remote name request at a time, for discovery or for an unknown incoming connection. */
typedef enum { NAME_NONE = 0, NAME_FOR_DISCOVERY, NAME_FOR_INCOMING } name_purpose_t;
static name_purpose_t name_purpose;
static bd_addr_t name_address;
/* Unknown incoming connection held (not accepted yet) while its name is requested. */
static uint16_t incoming_cid;
static hci_con_handle_t incoming_con_handle;

static controller_t *controller_for_cid(uint16_t cid){
    if (!cid) return NULL;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (controllers[i].cid == cid) return &controllers[i];
    }
    return NULL;
}

static controller_t *controller_for_address(const bd_addr_t address){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (controllers[i].source != WMR_ADDR_NONE &&
            memcmp(controllers[i].address, address, sizeof(bd_addr_t)) == 0) return &controllers[i];
    }
    return NULL;
}

static void known_hands(bool known[WMR_NUM_CONTROLLERS]){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++) known[i] = controllers[i].source != WMR_ADDR_NONE;
}

/* ---- Stored addresses (BTstack TLV, which is NVS on the ESP32) ----------- */

static void addresses_save(void){
    const btstack_tlv_t *tlv = NULL;
    void *context = NULL;
    btstack_tlv_get_instance(&tlv, &context);
    if (!tlv){ printf("Addresses: no TLV storage, not saved\n"); return; }
    wmr_addr_record_t r;
    memset(&r, 0, sizeof(r));
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (controllers[i].source != WMR_ADDR_STORED) continue;
        r.valid[i] = true;
        memcpy(r.address[i], controllers[i].address, 6);
    }
    uint8_t value[WMR_ADDR_RECORD_SIZE];
    wmr_addr_record_pack(&r, value);
    int rc = tlv->store_tag(context, WMR_CFG_ADDR_TLV_TAG, value, sizeof(value));
    printf("Addresses saved (LEFT %s, RIGHT %s)%s\n",
           r.valid[0] ? "stored" : "-", r.valid[1] ? "stored" : "-", rc == 0 ? "" : " FAILED");
}

static void addresses_load(void){
    const btstack_tlv_t *tlv = NULL;
    void *context = NULL;
    btstack_tlv_get_instance(&tlv, &context);
    if (!tlv){ printf("Addresses: no TLV storage\n"); return; }
    uint8_t value[WMR_ADDR_RECORD_SIZE];
    int size = tlv->get_tag(context, WMR_CFG_ADDR_TLV_TAG, value, sizeof(value));
    if (size <= 0) return;
    wmr_addr_record_t r;
    if (!wmr_addr_record_unpack(value, (size_t) size, &r)){
        printf("Addresses: stored record is not valid (size %d); ignored\n", size);
        return;
    }
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!r.valid[i] || controllers[i].source == WMR_ADDR_OVERRIDE) continue;
        memcpy(controllers[i].address, r.address[i], 6);
        controllers[i].source = WMR_ADDR_STORED;
    }
}

static void print_addresses(void){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        const controller_t *c = &controllers[i];
        printf("%s address: %s (%s)\n", c->name,
               c->source == WMR_ADDR_NONE ? "unknown" : bd_addr_to_str(c->address),
               wmr_controller_address_source_name(c->source));
    }
}

static void hid_packet_handler(uint8_t packet_type, uint16_t channel,
                               uint8_t *packet, uint16_t size);
static void disc_timer_fire(btstack_timer_source_t *timer);

void wmr_controller_init(wmr_report_sink_t sink){
    report_sink = sink;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        controller_t *c = &controllers[i];
        if (!c->override) continue;
        if (wmr_addr_parse(c->override, c->address)) c->source = WMR_ADDR_OVERRIDE;
        else printf("%s: compile-time address \"%s\" is not valid; ignored\n", c->name, c->override);
    }
    disc_timer.process = disc_timer_fire;
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

const char *wmr_controller_address_source_name(wmr_addr_source_t source){
    switch (source){
        case WMR_ADDR_OVERRIDE: return "compile-time override";
        case WMR_ADDR_STORED:   return "discovered, stored";
        default:                return "not discovered yet";
    }
}

void wmr_controller_get_status(unsigned hand, wmr_controller_status_t *out){
    memset(out, 0, sizeof(*out));
    if (hand >= WMR_NUM_CONTROLLERS) return;
    out->connected            = controllers[hand].connected;
    out->descriptor_available = controllers[hand].descriptor_available;
    out->reports              = (uint32_t) controllers[hand].reports;
    out->address_source       = controllers[hand].source;
    memcpy(out->address, controllers[hand].address, 6);
}

/* ---- Console-driven requests ------------------------------------------- */

/* Returns true if a connection attempt was started. */
static bool connect_hand(unsigned hand){
    controller_t *c = &controllers[hand];
    if (!stack_ready){ printf("Stack not ready\n"); return false; }
    if (c->source == WMR_ADDR_NONE){
        printf("%s address unknown: put it in pairing mode and press d (discovery)\n", c->name);
        return false;
    }
    if (connect_pending){ printf("Another connection/discovery is pending\n"); return false; }
    if (c->cid){ printf("%s already open or opening, cid=%u\n", c->name, c->cid); return false; }
    c->reports = 0;
    c->descriptor_available = false;
    connect_pending = true;
    printf("Connecting %s %s\n", c->name, bd_addr_to_str(c->address));
    uint8_t status = hid_host_connect(c->address, HID_PROTOCOL_MODE_REPORT, &c->cid);
    if (status != ERROR_CODE_SUCCESS){
        printf("%s connect request failed: 0x%02x\n", c->name, status);
        c->cid = 0;
        connect_pending = false;
        return false;
    }
    return true;
}

void wmr_controller_connect(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    if (disc_phase == DISC_INQUIRY || disc_phase == DISC_NAMES){
        printf("Discovery is running; wait for it to finish\n");
        return;
    }
    connect_hand(hand);
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
    if (c->source == WMR_ADDR_NONE) return WMR_BOND_RESET_NO_ADDRESS;
    gap_drop_link_key_for_bd_addr(c->address);
    return WMR_BOND_RESET_OK;
}

/* ---- Discovery ----------------------------------------------------------- */

static void disc_finish(void){
    disc_phase = DISC_IDLE;
    btstack_run_loop_remove_timer(&disc_timer);
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (disc.wanted[i] && controllers[i].source == WMR_ADDR_NONE)
            printf("Discovery: %s not found after %u round(s). Put it in pairing mode and press d\n",
                   controllers[i].name, disc_round);
    }
    printf("Discovery finished\n");
}

static void disc_inquiry_round(void){
    disc_round++;
    disc_phase = DISC_INQUIRY;
    printf("Discovery round %u/%u: inquiry for %u x 1.28 s, looking for", disc_round,
           WMR_CFG_DISCOVERY_ROUNDS, WMR_CFG_DISCOVERY_INQUIRY_LEN);
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (disc.wanted[i] && !disc.found[i]) printf(" %s", controllers[i].name);
    }
    printf(" (put it in pairing mode)\n");
    int rc = gap_inquiry_start(WMR_CFG_DISCOVERY_INQUIRY_LEN);
    if (rc != 0){
        printf("Discovery: inquiry could not start (%d)\n", rc);
        disc_finish();
    }
}

/* After a round's connections are done: another round if a hand is still missing. */
static void disc_after_connects(void){
    if (wmr_discovery_incomplete(&disc) && disc_round < WMR_CFG_DISCOVERY_ROUNDS){
        disc_inquiry_round();
        return;
    }
    disc_finish();
}

/* Connect the hands found in this round one at a time (the controller is in pairing mode,
 * so this also pairs it). Runs again from the timer whenever an attempt ends. */
static void disc_connect_next(void){
    if (disc_phase != DISC_CONNECT || connect_pending) return;
    while (disc_connect_mask){
        unsigned hand = disc_connect_mask & 1u ? 0u : 1u;
        disc_connect_mask &= ~(1u << hand);
        if (controllers[hand].cid || controllers[hand].connected) continue;
        if (connect_hand(hand)) return;
    }
    disc_after_connects();
}

static void disc_timer_fire(btstack_timer_source_t *timer){
    (void)timer;
    if (disc_phase == DISC_CONNECT){
        disc_connect_next();
        return;
    }
    /* Automatic start after boot. */
    bool known[WMR_NUM_CONTROLLERS];
    known_hands(known);
    if (disc_phase == DISC_IDLE && (!known[0] || !known[1])) wmr_controller_discover();
}

/* Called whenever a connection attempt ends (descriptor, failure, close). */
static void disc_connect_progress(void){
    if (disc_phase != DISC_CONNECT) return;
    btstack_run_loop_remove_timer(&disc_timer);
    btstack_run_loop_set_timer(&disc_timer, 100);
    btstack_run_loop_add_timer(&disc_timer);
}

/* The round's inquiry and name requests are done: keep what was found and connect it. */
static void disc_round_done(void){
    disc_connect_mask = 0;
    bool changed = false;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!disc.found[i] || controllers[i].source != WMR_ADDR_NONE) continue;
        memcpy(controllers[i].address, disc.found_address[i], 6);
        controllers[i].source = WMR_ADDR_STORED;
        disc_connect_mask |= 1u << i;
        changed = true;
    }
    if (changed) addresses_save();
    disc_phase = DISC_CONNECT;
    disc_connect_next();
}

static void disc_next_name(void){
    if (name_purpose != NAME_NONE) return;     /* an incoming check is using the slot */
    const wmr_discovery_device_t *dev = wmr_discovery_next_name_request(&disc);
    if (!dev){
        disc_round_done();
        return;
    }
    memcpy(name_address, dev->address, 6);
    name_purpose = NAME_FOR_DISCOVERY;
    /* Bit 15 marks the clock offset from the inquiry result as valid. */
    if (gap_remote_name_request(name_address, dev->page_scan_repetition_mode,
                                (uint16_t) (dev->clock_offset | 0x8000u)) != 0){
        name_purpose = NAME_NONE;
        wmr_discovery_on_name(&disc, name_address, NULL, 0);
        disc_next_name();
    }
}

static void disc_log(wmr_disc_result_t r, const bd_addr_t address, const char *how){
    switch (r){
        case WMR_DISC_MATCHED:
            for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
                if (disc.found[i] && memcmp(disc.found_address[i], address, 6) == 0)
                    printf("Discovery: %s is %s (%s)\n", controllers[i].name, bd_addr_to_str(address), how);
            }
            break;
        case WMR_DISC_DUPLICATE:
            printf("Discovery: another device with a controller name at %s ignored; the first one found is used. "
                   "Keep only one controller of each hand in pairing mode\n", bd_addr_to_str(address));
            break;
        case WMR_DISC_ALREADY_KNOWN:
            printf("Discovery: %s has a controller name but that hand already has an address; ignored\n",
                   bd_addr_to_str(address));
            break;
        case WMR_DISC_QUEUE_FULL:
            printf("Discovery: too many unnamed devices in range; %s skipped\n", bd_addr_to_str(address));
            break;
        default:
            break;
    }
}

static void disc_on_inquiry_result(const uint8_t *packet){
    if (disc_phase != DISC_INQUIRY) return;
    wmr_discovery_device_t dev;
    gap_event_inquiry_result_get_bd_addr(packet, dev.address);
    dev.page_scan_repetition_mode = gap_event_inquiry_result_get_page_scan_repetition_mode(packet);
    dev.clock_offset = gap_event_inquiry_result_get_clock_offset(packet);
    const uint8_t *name = NULL;
    size_t len = 0;
    if (gap_event_inquiry_result_get_name_available(packet)){
        name = gap_event_inquiry_result_get_name(packet);
        len = gap_event_inquiry_result_get_name_len(packet);
    }
    disc_log(wmr_discovery_on_inquiry_result(&disc, &dev, name, len), dev.address, "name in inquiry response");
    if (!wmr_discovery_incomplete(&disc)) gap_inquiry_stop();     /* INQUIRY_COMPLETE follows */
}

wmr_discovery_start_result_t wmr_controller_discover(void){
    if (!stack_ready){ printf("Stack not ready\n"); return WMR_DISCOVERY_BUSY; }
    if (disc_phase != DISC_IDLE){ printf("Discovery is already running\n"); return WMR_DISCOVERY_BUSY; }
    if (connect_pending || name_purpose != NAME_NONE){
        printf("A connection attempt is pending; try discovery again when it ends\n");
        return WMR_DISCOVERY_BUSY;
    }
    bool known[WMR_NUM_CONTROLLERS];
    known_hands(known);
    wmr_discovery_start(&disc, known);
    if (!wmr_discovery_incomplete(&disc)){
        printf("Both controller addresses are known; nothing to discover (F forgets the stored ones)\n");
        return WMR_DISCOVERY_NOTHING_TO_DO;
    }
    disc_round = 0;
    disc_inquiry_round();
    return WMR_DISCOVERY_STARTED;
}

wmr_forget_result_t wmr_controller_forget_addresses(void){
    if (disc_phase != DISC_IDLE || connect_pending || name_purpose != NAME_NONE) return WMR_FORGET_BUSY;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (controllers[i].cid || controllers[i].connected) return WMR_FORGET_BUSY;
    }
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (controllers[i].source != WMR_ADDR_STORED) continue;
        controllers[i].source = WMR_ADDR_NONE;
        memset(controllers[i].address, 0, sizeof(bd_addr_t));
    }
    const btstack_tlv_t *tlv = NULL;
    void *context = NULL;
    btstack_tlv_get_instance(&tlv, &context);
    if (tlv) tlv->delete_tag(context, WMR_CFG_ADDR_TLV_TAG);
    print_addresses();
    return WMR_FORGET_OK;
}

void wmr_controller_print_addresses(void){
    print_addresses();
}

/* ---- Unknown incoming connections ---------------------------------------- */

static void accept_incoming(controller_t *c, uint16_t cid){
    c->cid = cid;
    c->connected = false;
    c->descriptor_available = false;
    c->reports = 0;
    connect_pending = true;
    printf("%s accepting incoming HID connection, cid=%u\n", c->name, cid);
    hid_host_accept_connection(cid, HID_PROTOCOL_MODE_REPORT);
}

static void incoming_resolved(const uint8_t *name, size_t len){
    uint16_t cid = incoming_cid;
    incoming_cid = 0;
    connect_pending = false;
    disc_connect_progress();                    /* a discovery connect may have been waiting */
    if (!cid) return;                           /* closed while the name was requested */
    bool known[WMR_NUM_CONTROLLERS];
    known_hands(known);
    int hand = wmr_discovery_incoming_hand(known, name, len);
    if (hand < 0){
        printf("Incoming %s: name \"%.*s\" is not a controller without an address; declining\n",
               bd_addr_to_str(name_address), name ? (int) len : 0, name ? (const char *) name : "");
        hid_host_decline_connection(cid);
        return;
    }
    controller_t *c = &controllers[hand];
    memcpy(c->address, name_address, 6);
    c->source = WMR_ADDR_STORED;
    printf("Incoming: %s is %s (by name)\n", c->name, bd_addr_to_str(c->address));
    addresses_save();
    /* The ACL link came up before the address was known, so link diagnostics missed it. */
    wmr_linkdiag_link_identified((unsigned) hand, incoming_con_handle);
    accept_incoming(c, cid);
}

static void on_remote_name(const uint8_t *packet){
    bd_addr_t address;
    hci_event_remote_name_request_complete_get_bd_addr(packet, address);
    if (name_purpose == NAME_NONE || memcmp(address, name_address, 6) != 0) return;
    const name_purpose_t purpose = name_purpose;
    name_purpose = NAME_NONE;
    const uint8_t *name = NULL;
    size_t len = 0;
    if (hci_event_remote_name_request_complete_get_status(packet) == ERROR_CODE_SUCCESS){
        name = (const uint8_t *) hci_event_remote_name_request_complete_get_remote_name(packet);
        while (len < 248 && name[len]) len++;
    }
    if (purpose == NAME_FOR_INCOMING){
        incoming_resolved(name, len);
        if (disc_phase == DISC_NAMES) disc_next_name();
        return;
    }
    disc_log(wmr_discovery_on_name(&disc, address, name, len), address, "remote name");
    if (disc_phase == DISC_NAMES) disc_next_name();
}

/* ---- HCI events --------------------------------------------------------- */

void wmr_controller_on_hci_event(uint8_t *packet, uint16_t size){
    (void)size;
    bd_addr_t address;
    switch (hci_event_packet_get_type(packet)){
        case BTSTACK_EVENT_STATE:
            stack_ready = btstack_event_state_get_state(packet) == HCI_STATE_WORKING;
            if (stack_ready){
                addresses_load();
                print_addresses();
#if WMR_CFG_DISCOVERY_AUTOSTART
                btstack_run_loop_remove_timer(&disc_timer);
                btstack_run_loop_set_timer(&disc_timer, WMR_CFG_DISCOVERY_START_DELAY_MS);
                btstack_run_loop_add_timer(&disc_timer);
#endif
            }
            break;
        case GAP_EVENT_INQUIRY_RESULT:
            disc_on_inquiry_result(packet);
            break;
        case GAP_EVENT_INQUIRY_COMPLETE:
            if (disc_phase == DISC_INQUIRY){
                disc_phase = DISC_NAMES;
                disc_next_name();
            }
            break;
        case HCI_EVENT_REMOTE_NAME_REQUEST_COMPLETE:
            on_remote_name(packet);
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
 * hand. Removing this path breaks controller reconnects.
 * An address that is not stored is not accepted blindly: its name is requested first, and it
 * is accepted only as a hand that has no address yet (docs/DISCOVERY.md). */
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
        bool known[WMR_NUM_CONTROLLERS];
        known_hands(known);
        if (known[0] && known[1]){
            printf("Incoming device is not LEFT or RIGHT; declining\n");
            hid_host_decline_connection(cid);
            return;
        }
        if (name_purpose != NAME_NONE || connect_pending){
            printf("Incoming unknown device while another check or connection is pending; declining\n");
            hid_host_decline_connection(cid);
            return;
        }
        memcpy(name_address, address, 6);
        name_purpose = NAME_FOR_INCOMING;
        incoming_cid = cid;
        incoming_con_handle = hid_subevent_incoming_connection_get_handle(packet);
        connect_pending = true;
        printf("Incoming unknown device: requesting its name before accepting\n");
        if (gap_remote_name_request(name_address, 1, 0) != 0){
            name_purpose = NAME_NONE;
            incoming_resolved(NULL, 0);
        }
        return;
    }
    if (c->cid != 0 && c->cid != cid){
        printf("%s already tracked with cid=%u; not accepting different cid=%u\n",
               c->name, c->cid, cid);
        return;
    }
    accept_incoming(c, cid);
}
static void handle_report(const uint8_t *packet){
    uint16_t cid = hid_subevent_report_get_hid_cid(packet);
    controller_t *c = controller_for_cid(cid);
    if (!c) return;
    const uint8_t *report = hid_subevent_report_get_report(packet);
    uint16_t length = hid_subevent_report_get_report_len(packet);
    /* Classify by report ID before anything is counted or queued (docs/PROTOCOL.md, FF13):
     * config command responses go to the config reader only and never take a sequence
     * number or enter the sample stream. */
    switch (wmr_cfg_classify(report, length)){
        case WMR_CFG_REPORT_CONFIG:
            wmr_cfgmgr_on_report((unsigned) (c - controllers), report, length);
            return;
        case WMR_CFG_REPORT_OTHER:
            c->other_reports++;
            return;
        case WMR_CFG_REPORT_INPUT:
        default:
            break;
    }
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
                disc_connect_progress();
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
            if (c->descriptor_available) wmr_cfgmgr_on_descriptor((unsigned) (c - controllers));
            connect_pending = false;
            disc_connect_progress();
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
            if (cid && cid == incoming_cid){
                /* Gave up while its name was being requested; the name answer is then ignored. */
                incoming_cid = 0;
                break;
            }
            c = controller_for_cid(cid);
            if (!c) break;
            printf("%s disconnected, total reports=%lu other_reports=%lu\n", c->name, c->reports,
                   c->other_reports);
            wmr_cfgmgr_on_closed((unsigned) (c - controllers));
            if (!c->connected || !c->descriptor_available) connect_pending = false;
            c->cid = 0;
            c->connected = false;
            c->descriptor_available = false;
            disc_connect_progress();
            break;
        default:
            break;
    }
}
