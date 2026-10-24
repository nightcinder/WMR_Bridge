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
#include "wmr_psvr2.h"

typedef struct {
    const char *name;
    const char *override;             /* compile-time address, or NULL */
    wmr_addr_source_t source;         /* where `address` came from; NONE = unknown */
    bd_addr_t   address;
    wmr_model_t model;                /* WMR until discovery/name says otherwise */
    uint16_t    cid;                  /* 0 = no connection tracked */
    bool        connected;
    bool        descriptor_available;
    bool        psvr2_wake_pending;   /* wake timer armed for this hand */
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

/* ---- Contention-2 V1: staggered start (docs/runs/sheets/contention-2.md) - */

#if WMR_CFG_STAGGER_FIRST_HAND
#define STAGGER_FIRST_HAND   ((unsigned) (WMR_CFG_STAGGER_FIRST_HAND - 1))
#define STAGGER_SECOND_HAND  (1u - STAGGER_FIRST_HAND)
#define STAGGER_GATE_MS      30000u
#define STAGGER_RETRY_MS     500u

/* The gate opens 30 s after the first input report of the first hand. It is latched:
 * once open, a later disconnect does not close it again. */
static bool     stagger_released;
static bool     stagger_first_reported;
static uint32_t stagger_first_report_ms;

/* A request that arrived while the gate was closed. Outgoing connects and discovery
 * connects are retried through connect_hand(); an incoming HID channel is kept queued
 * and accepted only after the gate opens. */
static bool     stagger_pending_connect;
static uint16_t stagger_pending_incoming_cid;
static bool     stagger_hold_logged[WMR_NUM_CONTROLLERS];

static btstack_timer_source_t stagger_timer;
static bool stagger_timer_armed;

static void stagger_timer_fire(btstack_timer_source_t *timer);
static void stagger_retry_pending(void);
#endif

/* ---- Contention-2 V2/V3: per-link Classic policy ------------------------- */

#if WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER
typedef enum {
    POLICY_IDLE = 0,
    POLICY_WRITE,
    POLICY_READ
} policy_state_t;

typedef struct {
    policy_state_t    state;
    hci_con_handle_t  handle;
    bool              awaiting;      /* one HCI command outstanding */
    uint32_t          sent_ms;
} link_policy_t;

static link_policy_t policies[WMR_NUM_CONTROLLERS];
static btstack_timer_source_t policy_timer;
static bool policy_timer_armed;

static void policy_timer_fire(btstack_timer_source_t *timer);
static void policy_pump(void);
static void policy_link_opened(unsigned hand, hci_con_handle_t handle, const bd_addr_t address);
#endif

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

#if WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER

#define POLICY_RETRY_MS  100u
#define POLICY_REPLY_TIMEOUT_MS 1000u

static uint16_t classic_policy_settings(void){
#if WMR_CFG_CLASSIC_FORCE_MASTER
    return LM_LINK_POLICY_DISABLE_ALL_LM_MODES;
#else
    return LM_LINK_POLICY_ENABLE_ROLE_SWITCH;
#endif
}

#if WMR_CFG_CLASSIC_FORCE_MASTER
static const char *role_name(hci_role_t role){
    switch (role){
        case HCI_ROLE_MASTER: return "master";
        case HCI_ROLE_SLAVE:  return "slave";
        default:              return "invalid";
    }
}
#endif

static const char *policy_state_name(policy_state_t state){
    switch (state){
        case POLICY_WRITE:      return "write";
        case POLICY_READ:       return "read";
        default:                return "idle";
    }
}

static bool policy_any_active(void){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (policies[i].state != POLICY_IDLE) return true;
    }
    return false;
}

static void policy_arm(void){
    if (!policy_any_active() || policy_timer_armed) return;
    policy_timer_armed = true;
    btstack_run_loop_remove_timer(&policy_timer);
    btstack_run_loop_set_timer(&policy_timer, POLICY_RETRY_MS);
    btstack_run_loop_add_timer(&policy_timer);
}

/* Send one pending policy command if the HCI command buffer allows it. The next step runs
 * from HCI_EVENT_COMMAND_COMPLETE, so write/read cannot overtake each other. */
static void policy_pump(void){
    if (!policy_any_active()) return;
    uint32_t now = btstack_run_loop_get_time_ms();
    for (unsigned hand = 0; hand < WMR_NUM_CONTROLLERS; hand++){
        link_policy_t *p = &policies[hand];
        if (p->state == POLICY_IDLE) continue;
        if (p->awaiting){
            if ((uint32_t) (now - p->sent_ms) < POLICY_REPLY_TIMEOUT_MS){
                policy_arm();
                return;
            }
            printf("POLICY: %s %s command timed out; retrying\n",
                   controllers[hand].name, policy_state_name(p->state));
            p->awaiting = false;
        }
        if (!hci_can_send_command_packet_now()){
            policy_arm();
            return;
        }
        uint8_t status;
        if (p->state == POLICY_WRITE){
            status = hci_send_cmd(&hci_write_link_policy_settings, p->handle,
                                  classic_policy_settings());
        } else {
            status = hci_send_cmd(&hci_read_link_policy_settings, p->handle);
        }
        if (status == ERROR_CODE_SUCCESS){
            p->awaiting = true;
            p->sent_ms = now;
        } else {
            printf("POLICY: %s %s command not sent: 0x%02x\n",
                   controllers[hand].name, policy_state_name(p->state), status);
        }
        policy_arm();
        return;
    }
}

static void policy_timer_fire(btstack_timer_source_t *timer){
    (void) timer;
    policy_timer_armed = false;
    policy_pump();
    policy_arm();
}

/* Log the read-back in the same terms as the run sheet: sniff and role-switch bits. */
static void policy_log_read(const char *name, uint8_t status, uint16_t handle, uint16_t settings){
    printf("POLICY: %s read handle=0x%04x settings=0x%04x status=0x%02x "
           "sniff=%s role_switch=%s\n",
           name, handle, settings, status,
           (settings & LM_LINK_POLICY_ENABLE_SNIFF_MODE) ? "on" : "clear",
           (settings & LM_LINK_POLICY_ENABLE_ROLE_SWITCH) ? "on" : "off");
}

static void policy_on_command_complete(const uint8_t *packet){
    uint16_t opcode = hci_event_command_complete_get_command_opcode(packet);
    if (opcode != HCI_OPCODE_HCI_WRITE_LINK_POLICY_SETTINGS &&
        opcode != HCI_OPCODE_HCI_READ_LINK_POLICY_SETTINGS) return;
    const uint8_t *ret = hci_event_command_complete_get_return_parameters(packet);
    uint8_t status = ret[0];
    uint16_t handle = little_endian_read_16(ret, 1);
    for (unsigned hand = 0; hand < WMR_NUM_CONTROLLERS; hand++){
        link_policy_t *p = &policies[hand];
        if (!p->awaiting || p->handle != handle) continue;
        if (opcode == HCI_OPCODE_HCI_WRITE_LINK_POLICY_SETTINGS && p->state == POLICY_WRITE){
            printf("POLICY: %s write handle=0x%04x settings=0x%04x status=0x%02x\n",
                   controllers[hand].name, handle, classic_policy_settings(), status);
            p->state = POLICY_READ;
        } else if (opcode == HCI_OPCODE_HCI_READ_LINK_POLICY_SETTINGS && p->state == POLICY_READ){
            uint16_t settings = little_endian_read_16(ret, 3);
            policy_log_read(controllers[hand].name, status, handle, settings);
            p->state = POLICY_IDLE;
        } else {
            continue;
        }
        p->awaiting = false;
        policy_pump();
        policy_arm();
        return;
    }
}

static void policy_link_down(hci_con_handle_t handle){
    for (unsigned hand = 0; hand < WMR_NUM_CONTROLLERS; hand++){
        if (policies[hand].handle != handle) continue;
        policies[hand].state = POLICY_IDLE;
        policies[hand].awaiting = false;
    }
}

/* Called for every successful ACL connection, outgoing or incoming. */
static void policy_link_opened(unsigned hand, hci_con_handle_t handle, const bd_addr_t address){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    link_policy_t *p = &policies[hand];
    p->handle = handle;
    p->awaiting = false;
    p->state = POLICY_WRITE;
#if WMR_CFG_CLASSIC_FORCE_MASTER
    hci_role_t role = gap_get_role(handle);
    printf("ROLE: %s handle=0x%04x role=%s\n", controllers[hand].name, handle, role_name(role));
    if (role == HCI_ROLE_SLAVE){
        uint8_t status = gap_request_role(address, HCI_ROLE_MASTER);
        printf("ROLE: %s switch to master requested: status=0x%02x\n",
               controllers[hand].name, status);
    }
#else
    (void) address;
#endif
    policy_pump();
    policy_arm();
}

#endif /* WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER */

/* ---- Stored addresses (BTstack TLV, which is NVS on the ESP32) ----------- */

static void addresses_save(void){
    const btstack_tlv_t *tlv = NULL;
    void *context = NULL;
    btstack_tlv_get_instance(&tlv, &context);
    if (!tlv){ printf("Addresses: no TLV storage, not saved\n"); return; }
    wmr_addr_record_t r;
    memset(&r, 0, sizeof(r));
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!WMR_BRIDGE_HAND_ENABLED(i)) continue;
        if (controllers[i].source != WMR_ADDR_STORED) continue;
        r.valid[i] = true;
        r.model[i] = controllers[i].model;
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
        controllers[i].model = r.model[i];
        controllers[i].source = WMR_ADDR_STORED;
    }
}

static void print_addresses(void){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        const controller_t *c = &controllers[i];
        printf("%s address: %s (%s, model %s)\n", c->name,
               c->source == WMR_ADDR_NONE ? "unknown" : bd_addr_to_str(c->address),
               wmr_controller_address_source_name(c->source),
               c->source == WMR_ADDR_NONE ? "unknown" : wmr_model_name(c->model));
    }
}

static void hid_packet_handler(uint8_t packet_type, uint16_t channel,
                               uint8_t *packet, uint16_t size);

#if WMR_CFG_PSVR2_SUPPORT && WMR_CFG_PSVR2_WAKE
static btstack_timer_source_t psvr2_wake_timer;
static void psvr2_wake_timer_fire(btstack_timer_source_t *timer);

/* PSVR2 Sense controllers stay silent until the host reads HID Feature Report 0x05 over
 * the control channel. Do not send it from inside DESCRIPTOR_AVAILABLE: on an incoming
 * or reconnect link BTstack still has SET_PROTOCOL(report) queued, and starting a
 * control-channel transaction there skips it. A short delay lets SET_PROTOCOL finish
 * first, and works the same for outgoing links (which do not send it). One attempt per
 * connection; the response is logged by HID_SUBEVENT_GET_REPORT_RESPONSE. Unverified on
 * hardware (wmr_psvr2.h, docs/PSVR2.md). */
static void psvr2_wake(controller_t *c){
    uint8_t status = hid_host_send_get_report(c->cid, HID_REPORT_TYPE_FEATURE,
                                             WMR_PSVR2_WAKE_FEATURE_ID);
    printf("PSVR2: %s wake: GET_REPORT feature 0x%02x status=0x%02x\n",
           c->name, WMR_PSVR2_WAKE_FEATURE_ID, status);
    if (status != ERROR_CODE_SUCCESS)
        printf("PSVR2: %s wake request refused; reconnect (l/r) to try again\n", c->name);
}

static void psvr2_wake_arm(controller_t *c){
    c->psvr2_wake_pending = true;
    /* Restart on every arm: both hands can become ready inside one delay window, and a
     * shared timer that kept its original deadline would give the second hand a much
     * shorter delay (possibly none) before its GET_REPORT. */
    btstack_run_loop_remove_timer(&psvr2_wake_timer);
    btstack_run_loop_set_timer(&psvr2_wake_timer, WMR_CFG_PSVR2_WAKE_DELAY_MS);
    btstack_run_loop_add_timer(&psvr2_wake_timer);
    printf("PSVR2: %s wake scheduled in %u ms\n", c->name,
           (unsigned) WMR_CFG_PSVR2_WAKE_DELAY_MS);
}

static void psvr2_wake_timer_fire(btstack_timer_source_t *timer){
    (void) timer;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        controller_t *c = &controllers[i];
        if (!c->psvr2_wake_pending) continue;
        c->psvr2_wake_pending = false;
        if (!c->cid || !c->descriptor_available || c->model != WMR_MODEL_PSVR2) continue;
        psvr2_wake(c);
    }
}
#endif
static void disc_timer_fire(btstack_timer_source_t *timer);
static bool connect_hand(unsigned hand);
static void disc_connect_next(void);
static void accept_incoming(controller_t *c, uint16_t cid);

#if WMR_CFG_STAGGER_FIRST_HAND
/* True once the first hand has been streaming long enough, or forever after release. */
static bool stagger_gate_open(void){
    if (stagger_released) return true;
    if (!stagger_first_reported) return false;
    if ((uint32_t) (btstack_run_loop_get_time_ms() - stagger_first_report_ms) < STAGGER_GATE_MS)
        return false;
    stagger_released = true;
    printf("STAGGER: %s released at t=%lu\n", controllers[STAGGER_SECOND_HAND].name,
           (unsigned long) (btstack_run_loop_get_time_ms() / 1000u));
    return true;
}

static bool stagger_blocks(unsigned hand){
    return hand == STAGGER_SECOND_HAND && !stagger_gate_open();
}

static void stagger_arm(void){
    if (stagger_released || stagger_timer_armed) return;
    stagger_timer_armed = true;
    btstack_run_loop_remove_timer(&stagger_timer);
    btstack_run_loop_set_timer(&stagger_timer, STAGGER_RETRY_MS);
    btstack_run_loop_add_timer(&stagger_timer);
}

/* Log the hold once per hand and make sure a retry timer is running. */
static void stagger_hold(unsigned hand){
    if (!stagger_hold_logged[hand]){
        stagger_hold_logged[hand] = true;
        printf("STAGGER: %s held, first=%s reports=%lu\n",
               controllers[hand].name, controllers[STAGGER_FIRST_HAND].name,
               controllers[STAGGER_FIRST_HAND].reports);
    }
    stagger_arm();
}

static bool stagger_pending(void){
    if (stagger_pending_connect || stagger_pending_incoming_cid) return true;
    return disc_phase == DISC_CONNECT &&
           (disc_connect_mask & (1u << STAGGER_SECOND_HAND)) != 0;
}

/* Called after the gate opens (or on every retry tick): resume the request that was
 * held. Incoming is preferred because the controller is already waiting. */
static void stagger_retry_pending(void){
    if (!stagger_gate_open()) return;
    if (stagger_pending_incoming_cid){
        if (connect_pending) return;
        uint16_t cid = stagger_pending_incoming_cid;
        stagger_pending_incoming_cid = 0;
        controller_t *c = &controllers[STAGGER_SECOND_HAND];
        if (c->cid == 0) accept_incoming(c, cid);
        else hid_host_decline_connection(cid);
        return;
    }
    if (disc_phase == DISC_CONNECT &&
        (disc_connect_mask & (1u << STAGGER_SECOND_HAND)) != 0){
        disc_connect_next();
        return;
    }
    if (stagger_pending_connect){
        if (connect_pending) return;
        stagger_pending_connect = false;
        connect_hand(STAGGER_SECOND_HAND);
    }
}

static void stagger_timer_fire(btstack_timer_source_t *timer){
    (void) timer;
    stagger_timer_armed = false;
    if (!stagger_gate_open()){
        stagger_arm();
        return;
    }
    stagger_retry_pending();
    if (stagger_pending()) stagger_arm();
}
#endif

void wmr_controller_init(wmr_report_sink_t sink){
    report_sink = sink;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        controller_t *c = &controllers[i];
        if (!c->override) continue;
        if (wmr_addr_parse(c->override, c->address)) c->source = WMR_ADDR_OVERRIDE;
        else printf("%s: compile-time address \"%s\" is not valid; ignored\n", c->name, c->override);
    }
    disc_timer.process = disc_timer_fire;
#if WMR_CFG_PSVR2_SUPPORT && WMR_CFG_PSVR2_WAKE
    psvr2_wake_timer.process = psvr2_wake_timer_fire;
#endif
#if WMR_CFG_STAGGER_FIRST_HAND
    stagger_timer.process = stagger_timer_fire;
#endif
#if WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER
    policy_timer.process = policy_timer_fire;
#endif
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

wmr_model_t wmr_controller_model(unsigned hand){
    return hand < WMR_NUM_CONTROLLERS ? controllers[hand].model : WMR_MODEL_COUNT;
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
    out->connecting           = (controllers[hand].cid != 0 && !controllers[hand].connected) ||
                                (disc_phase == DISC_CONNECT && (disc_connect_mask & (1u << hand)) != 0);
    out->descriptor_available = controllers[hand].descriptor_available;
    out->reports              = (uint32_t) controllers[hand].reports;
    out->other_reports        = (uint32_t) controllers[hand].other_reports;
    out->address_source       = controllers[hand].source;
    out->model                = controllers[hand].model;
    memcpy(out->address, controllers[hand].address, 6);
}

/* ---- Console-driven requests ------------------------------------------- */

static wmr_connect_result_t connect_hand_impl(unsigned hand){
    controller_t *c = &controllers[hand];
    if (!WMR_BRIDGE_HAND_ENABLED(hand)){
        printf("%s is not served by this build (WMR_CFG_BRIDGE_HANDS=%d)\n",
               c->name, WMR_CFG_BRIDGE_HANDS);
        return WMR_CONNECT_NOT_ENABLED;
    }
    if (!stack_ready){ printf("Stack not ready\n"); return WMR_CONNECT_BUSY; }
    if (c->source == WMR_ADDR_NONE){
        printf("%s address unknown: put it in pairing mode and press d (discovery)\n", c->name);
        return WMR_CONNECT_NO_ADDRESS;
    }
    if (connect_pending){ printf("Another connection/discovery is pending\n"); return WMR_CONNECT_BUSY; }
    if (c->cid){ printf("%s already open or opening, cid=%u\n", c->name, c->cid); return WMR_CONNECT_BUSY; }
#if WMR_CFG_STAGGER_FIRST_HAND
    if (stagger_blocks(hand)){
        stagger_hold(hand);
        stagger_pending_connect = true;
        return WMR_CONNECT_BUSY;
    }
    if (hand == STAGGER_SECOND_HAND) stagger_pending_connect = false;
#endif
    c->reports = 0;
    c->other_reports = 0;
    c->descriptor_available = false;
    connect_pending = true;
    printf("Connecting %s %s\n", c->name, bd_addr_to_str(c->address));
    uint8_t status = hid_host_connect(c->address, HID_PROTOCOL_MODE_REPORT, &c->cid);
    if (status != ERROR_CODE_SUCCESS){
        printf("%s connect request failed: 0x%02x\n", c->name, status);
        c->cid = 0;
        connect_pending = false;
        return WMR_CONNECT_BUSY;
    }
    return WMR_CONNECT_OK;
}

/* Returns true if a connection attempt was started. */
static bool connect_hand(unsigned hand){
    return connect_hand_impl(hand) == WMR_CONNECT_OK;
}

wmr_connect_result_t wmr_controller_connect(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return WMR_CONNECT_BUSY;
    if (disc_phase == DISC_INQUIRY || disc_phase == DISC_NAMES){
        printf("Discovery is running; wait for it to finish\n");
        return WMR_CONNECT_BUSY;
    }
    return connect_hand_impl(hand);
}

wmr_disconnect_result_t wmr_controller_disconnect(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return WMR_DISCONNECT_NOT_CONNECTED;
    controller_t *c = &controllers[hand];
    if (!c->cid){ printf("%s has no connection\n", c->name); return WMR_DISCONNECT_NOT_CONNECTED; }
    hid_host_disconnect(c->cid);
    return WMR_DISCONNECT_OK;
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
        unsigned hand;
#if WMR_CFG_STAGGER_FIRST_HAND
        /* With the gate closed, connect the first hand even when it is RIGHT and the
         * normal round order would have picked LEFT (the held hand) first. */
        if (!stagger_released && (disc_connect_mask & (1u << STAGGER_FIRST_HAND)) != 0){
            hand = STAGGER_FIRST_HAND;
        } else {
            hand = disc_connect_mask & 1u ? 0u : 1u;
        }
#else
        hand = disc_connect_mask & 1u ? 0u : 1u;
#endif
        if (!WMR_BRIDGE_HAND_ENABLED(hand)){
            /* The excluded hand is never a discovery candidate, but keep the mask clean if
             * a mask bit ever arrives from another path. */
            disc_connect_mask &= ~(1u << hand);
            continue;
        }
#if WMR_CFG_STAGGER_FIRST_HAND
        if (stagger_blocks(hand)){
            /* Leave the bit in the mask; the stagger timer resumes this round. */
            stagger_hold(hand);
            stagger_pending_connect = true;
            return;
        }
#endif
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
    /* Automatic start after boot, for a served hand that has no address. */
    bool known[WMR_NUM_CONTROLLERS];
    known_hands(known);
    bool need_discovery = false;
    for (unsigned h = 0; h < WMR_NUM_CONTROLLERS; h++){
        if (WMR_BRIDGE_HAND_ENABLED(h) && !known[h]) need_discovery = true;
    }
    if (disc_phase == DISC_IDLE && need_discovery) wmr_controller_discover();
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
        if (!WMR_BRIDGE_HAND_ENABLED(i)) continue;
        if (!disc.found[i] || controllers[i].source != WMR_ADDR_NONE) continue;
        memcpy(controllers[i].address, disc.found_address[i], 6);
        controllers[i].model = disc.found_model[i];
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
                    printf("Discovery: %s is %s (%s, model %s)\n", controllers[i].name,
                           bd_addr_to_str(address), how, wmr_model_name(disc.found_model[i]));
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
        if (WMR_CFG_BRIDGE_HANDS == 0){
            printf("Both controller addresses are known; nothing to discover (F forgets the stored ones)\n");
        } else {
            printf("The selected controller address is known; nothing to discover (F forgets the stored one)\n");
        }
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
    unsigned hand = (unsigned) (c - controllers);
    if (!WMR_BRIDGE_HAND_ENABLED(hand)){
        printf("%s is excluded by this build (WMR_CFG_BRIDGE_HANDS=%d); declining\n",
               c->name, WMR_CFG_BRIDGE_HANDS);
        hid_host_decline_connection(cid);
        return;
    }
#if WMR_CFG_STAGGER_FIRST_HAND
    if (stagger_blocks(hand)){
        /* Keep the HID channel queued, but do not call hid_host_accept_connection():
         * the controller must not start its report/reconnect sequence before the gate. */
        stagger_hold(hand);
        stagger_pending_incoming_cid = cid;
        return;
    }
    if (hand == STAGGER_SECOND_HAND){
        stagger_pending_incoming_cid = 0;
        stagger_pending_connect = false;
    }
#endif
    c->cid = cid;
    c->connected = false;
    c->descriptor_available = false;
    c->reports = 0;
    c->other_reports = 0;
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
    c->model = wmr_discovery_model_for_name(name, len);
    c->source = WMR_ADDR_STORED;
    printf("Incoming: %s is %s (by name, model %s)\n", c->name, bd_addr_to_str(c->address),
           wmr_model_name(c->model));
    addresses_save();
    /* The ACL link came up before the address was known, so link diagnostics missed it. */
    wmr_linkdiag_link_identified((unsigned) hand, incoming_con_handle);
#if WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER
    /* The HCI connection-complete event could not name this hand yet. */
    policy_link_opened((unsigned) hand, incoming_con_handle, c->address);
#endif
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

/* ---- Classic link role logging ------------------------------------------ */

/* One parseable line per link, for the contention-2 REF runs (ONY-14). The
 * role is whatever BTstack currently has cached for the handle: at connection
 * complete that can still be unknown, and the role-change event fills it in.
 * Logging only: no policy, role request or link state is changed here. */
static const char *role_name(hci_role_t role){
    switch (role){
        case HCI_ROLE_MASTER: return "master";
        case HCI_ROLE_SLAVE:  return "slave";
        default:              return "unknown";
    }
}

static void log_link_role(const bd_addr_t address, uint8_t status, hci_role_t role,
                          const char *event){
    int hand = wmr_controller_hand_for_address(address);
    printf("%s role: address=%s role=%s status=0x%02x event=%s t_ms=%lu\n",
           hand >= 0 ? wmr_controller_name((unsigned) hand) : "?",
           bd_addr_to_str(address), role_name(role), status, event,
           (unsigned long) btstack_run_loop_get_time_ms());
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
        case HCI_EVENT_CONNECTION_COMPLETE: {
            uint8_t status = hci_event_connection_complete_get_status(packet);
            hci_event_connection_complete_get_bd_addr(packet, address);
            hci_con_handle_t handle = hci_event_connection_complete_get_connection_handle(packet);
            hci_role_t role = HCI_ROLE_INVALID;
            if (status == ERROR_CODE_SUCCESS){
                role = gap_get_role(handle);
            }
            log_link_role(address, status, role, "conn_complete");
#if WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER
            if (status == ERROR_CODE_SUCCESS){
                int hand = wmr_controller_hand_for_address(address);
                if (hand >= 0){
                    policy_link_opened((unsigned) hand, handle, address);
                }
            }
#endif
            break;
        }
        case HCI_EVENT_ROLE_CHANGE: {
            uint8_t status = hci_event_role_change_get_status(packet);
            hci_event_role_change_get_bd_addr(packet, address);
            hci_role_t role = HCI_ROLE_INVALID;
            if (status == ERROR_CODE_SUCCESS){
                role = (hci_role_t) hci_event_role_change_get_role(packet);
            }
            log_link_role(address, status, role, "role_change");
#if WMR_CFG_CLASSIC_FORCE_MASTER
            int hand = wmr_controller_hand_for_address(address);
            printf("ROLE: %s change status=0x%02x role=%s\n",
                   hand >= 0 ? controllers[hand].name : bd_addr_to_str(address),
                   status,
                   role_name(role));
#endif
            break;
        }
        case HCI_EVENT_AUTHENTICATION_COMPLETE: {
            uint8_t status = hci_event_authentication_complete_get_status(packet);
            hci_con_handle_t handle = hci_event_authentication_complete_get_connection_handle(packet);
            printf("HCI AUTH_COMPLETE: handle=0x%04x status=0x%02x\n", handle, status);
            break;
        }
        case HCI_EVENT_ENCRYPTION_CHANGE: {
            uint8_t status = hci_event_encryption_change_get_status(packet);
            hci_con_handle_t handle = hci_event_encryption_change_get_connection_handle(packet);
            uint8_t enabled = hci_event_encryption_change_get_encryption_enabled(packet);
            printf("HCI ENCRYPTION_CHANGE: handle=0x%04x status=0x%02x enabled=%u\n", handle, status, enabled);
            break;
        }
        case HCI_EVENT_SIMPLE_PAIRING_COMPLETE: {
            uint8_t status = hci_event_simple_pairing_complete_get_status(packet);
            hci_event_simple_pairing_complete_get_bd_addr(packet, address);
            printf("HCI SIMPLE_PAIRING_COMPLETE: address=%s status=0x%02x\n", bd_addr_to_str(address), status);
            break;
        }
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
        case HCI_EVENT_DISCONNECTION_COMPLETE: {
            hci_con_handle_t handle = hci_event_disconnection_complete_get_connection_handle(packet);
            uint8_t status = hci_event_disconnection_complete_get_status(packet);
            uint8_t reason = hci_event_disconnection_complete_get_reason(packet);
            printf("HCI DISCONN_COMPLETE: handle=0x%04x status=0x%02x reason=0x%02x\n", handle, status, reason);
#if WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER
            policy_link_down(handle);
#endif
            break;
        }
#if WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER
        case HCI_EVENT_COMMAND_COMPLETE:
            policy_on_command_complete(packet);
            break;
#endif
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
    if (c && !WMR_BRIDGE_HAND_ENABLED((unsigned) (c - controllers))){
        /* A known address of the excluded hand (for example stored by an earlier
         * both-hands build) is treated exactly like a device that is not a controller. */
        printf("Incoming %s is excluded by this build (WMR_CFG_BRIDGE_HANDS=%d); declining\n",
               c->name, WMR_CFG_BRIDGE_HANDS);
        hid_host_decline_connection(cid);
        return;
    }
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
     * number or enter the sample stream. PSVR2 controllers have their own report ID and do
     * not speak the Microsoft config protocol, so only 0x31 input frames are forwarded. */
#if WMR_CFG_PSVR2_SUPPORT
    if (c->model == WMR_MODEL_PSVR2){
        if (!wmr_psvr2_is_input_report(report, length)){
            c->other_reports++;
            return;
        }
    } else
#endif
    {
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
    }
    c->reports++;
    /* Hand EVERY report over before any logging. The report buffer is only
     * valid during this callback; the sink copies what it keeps. */
    if (report_sink) report_sink((unsigned) (c - controllers), report, length);
    if (c->reports == 1){
#if WMR_CFG_STAGGER_FIRST_HAND
        if ((unsigned) (c - controllers) == STAGGER_FIRST_HAND){
            /* The gate is timed from report flow, not from ACL setup. */
            stagger_first_reported = true;
            stagger_first_report_ms = btstack_run_loop_get_time_ms();
        }
#endif
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
            c->psvr2_wake_pending = false;
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
            printf("%s descriptor status=0x%02x (model %s)\n", c->name, status,
                   wmr_model_name(c->model));
            if (c->descriptor_available){
#if WMR_CFG_PSVR2_SUPPORT
                if (c->model == WMR_MODEL_PSVR2){
#if WMR_CFG_PSVR2_WAKE
                    psvr2_wake_arm(c);
#else
                    printf("PSVR2: %s connected; wake request disabled in this build\n", c->name);
#endif
                } else
#endif
                {
                    wmr_cfgmgr_on_descriptor((unsigned) (c - controllers));
                }
            }
            connect_pending = false;
            disc_connect_progress();
            break;
        case HID_SUBEVENT_GET_REPORT_RESPONSE: {
            cid = hid_subevent_get_report_response_get_hid_cid(packet);
            status = hid_subevent_get_report_response_get_handshake_status(packet);
            c = controller_for_cid(cid);
            uint16_t report_len = hid_subevent_get_report_response_get_report_len(packet);
            printf("%s GET_REPORT response: status=0x%02x len=%u\n",
                   c ? c->name : "?", status, report_len);
#if WMR_CFG_REPORT_HEXDUMP
            if (status == ERROR_CODE_SUCCESS && report_len)
                printf_hexdump(hid_subevent_get_report_response_get_report(packet), report_len);
#endif
            break;
        }
        case HID_SUBEVENT_REPORT:
            handle_report(packet);
            break;
        case HID_SUBEVENT_SET_PROTOCOL_RESPONSE:
            status = hid_subevent_set_protocol_response_get_handshake_status(packet);
            printf("SET_PROTOCOL handshake status=0x%02x\n", status);
            break;
        case HID_SUBEVENT_CONNECTION_CLOSED:
            cid = hid_subevent_connection_closed_get_hid_cid(packet);
#if WMR_CFG_STAGGER_FIRST_HAND
            if (cid && cid == stagger_pending_incoming_cid){
                /* The held incoming HID channel went away before the gate opened. */
                stagger_pending_incoming_cid = 0;
                stagger_hold_logged[STAGGER_SECOND_HAND] = false;
                break;
            }
#endif
            if (cid && cid == incoming_cid){
                /* Gave up while its name was being requested; the name answer is then ignored. */
                incoming_cid = 0;
                break;
            }
            c = controller_for_cid(cid);
            if (!c) break;
            c->psvr2_wake_pending = false;
#if WMR_CFG_STAGGER_FIRST_HAND
            if ((unsigned) (c - controllers) == STAGGER_FIRST_HAND && !stagger_released){
                /* Start the 30 s again after the first hand reconnects. */
                stagger_first_reported = false;
                stagger_first_report_ms = 0;
            }
            if ((unsigned) (c - controllers) == STAGGER_SECOND_HAND)
                stagger_hold_logged[STAGGER_SECOND_HAND] = false;
#endif
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

uint8_t wmr_controller_discovery_phase(void){
    return (uint8_t) disc_phase;
}

uint8_t wmr_controller_discovery_round(void){
    return (uint8_t) disc_round;
}
