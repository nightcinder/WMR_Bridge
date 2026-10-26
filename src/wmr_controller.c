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
#include "wmr_log.h"
#include "wmr_psvr2.h"

typedef struct {
    const char *name;
    const char *override;             /* compile-time address, or NULL */
    wmr_addr_source_t source;         /* where `address` came from; NONE = unknown */
    bd_addr_t   address;
    wmr_model_t model;                /* WMR until discovery/name says otherwise */
    bool        address_confirmed;    /* the address has been connected to (or has a link key) */
    uint16_t    cid;                  /* 0 = no connection tracked */
    hci_con_handle_t handle;          /* HCI_CON_HANDLE_INVALID = no Classic link tracked */
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
    [WMR_HAND_LEFT]  = {.name = "LEFT",  .override = LEFT_OVERRIDE,
                        .handle = HCI_CON_HANDLE_INVALID},
    [WMR_HAND_RIGHT] = {.name = "RIGHT", .override = RIGHT_OVERRIDE,
                        .handle = HCI_CON_HANDLE_INVALID},
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
 * connects are retried through connect_hand_impl(); an incoming HID channel is kept queued
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
static unsigned disc_pair_mask;         /* hands learned in this run, not connected yet */
static unsigned disc_pair_attempts[WMR_NUM_CONTROLLERS];
static bool     disc_pair_wait;         /* the next timer tick is a pairing retry */
static btstack_timer_source_t disc_timer;
static bool disc_incoming_rejected;

/* Discovery's connect phase pages the controllers itself. The ESP32 has two Classic ACL
 * slots, and the 2026-10-26 field log shows the second outgoing pairing connect failing
 * immediately with HCI 0x09 (connection limit exceeded) while the first hand streams. The
 * other slot is most likely held by the other controller paging the bridge at the same
 * time. Reject incoming Classic links only while that phase runs, so controllers cannot
 * take the slot the pairing page needs; normal auto-reconnect resumes when the run ends. */
static int disc_accept_classic_connection(bd_addr_t address, hci_link_type_t link_type){
    (void) address;
    if (link_type != HCI_LINK_TYPE_ACL) return 1;
    if (disc_phase != DISC_CONNECT) return 1;
    if (!disc_incoming_rejected){
        disc_incoming_rejected = true;
        WMR_LOGI("pair", "rejecting incoming controller links while discovery connects the "
                 "hands (the radio has no spare ACL slot for pairing)");
    }
    return 0;
}

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

static controller_t *controller_for_handle(hci_con_handle_t handle){
    /* 0x0000 is a valid ACL handle, so only 0xFFFF means "not tracked". */
    if (handle == HCI_CON_HANDLE_INVALID) return NULL;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (controllers[i].handle == handle) return &controllers[i];
    }
    return NULL;
}

static const char *hand_or_handle(hci_con_handle_t handle, char *buffer, size_t length){
    controller_t *c = controller_for_handle(handle);
    if (c) return c->name;
    snprintf(buffer, length, "link 0x%04x", handle);
    return buffer;
}

/* The status codes a pairing session normally ends with, in words. Unknown
 * values keep the hex code; the full table is the Bluetooth Core spec. */
static const char *pair_status_text(uint8_t status){
    switch (status){
        case ERROR_CODE_SUCCESS: return "success";
        case 0x01: return "unknown HCI command";
        case 0x02: return "no connection";
        case 0x03: return "hardware failure";
        case 0x04: return "page timeout";
        case 0x05: return "authentication failed (link key mismatch)";
        case 0x06: return "PIN or key missing (controller lost the bond)";
        case 0x07: return "memory full";
        case 0x08: return "connection timeout";
        case 0x09: return "connection limit exceeded (no spare ACL slot)";
        case 0x0c: return "command disallowed";
        case 0x0d: return "connection rejected (no resources)";
        case 0x0e: return "connection rejected (security)";
        case 0x13: return "remote user terminated";
        case 0x16: return "terminated by local host";
        case 0x22: return "LMP response timeout";
        case 0x28: return "instant passed";
        case 0x3b: return "unacceptable connection parameters";
        case 0x3d: return "MIC failure (link key mismatch)";
        case 0x3e: return "connection failed to be established";
        default:   return "see HCI error code";
    }
}

/* HID handshake results (HIDP) are not HCI error codes: GET_REPORT and
 * SET_PROTOCOL responses use the values below, so keep them out of
 * pair_status_text(). */
static const char *hid_handshake_text(uint8_t status){
    switch (status){
        case HID_HANDSHAKE_PARAM_TYPE_SUCCESSFUL: return "success";
        case HID_HANDSHAKE_PARAM_TYPE_NOT_READY: return "not ready";
        case HID_HANDSHAKE_PARAM_TYPE_ERR_INVALID_REPORT_ID: return "invalid report ID";
        case HID_HANDSHAKE_PARAM_TYPE_ERR_UNSUPPORTED_REQUEST: return "unsupported request";
        case HID_HANDSHAKE_PARAM_TYPE_ERR_INVALID_PARAMETER: return "invalid parameter";
        case HID_HANDSHAKE_PARAM_TYPE_ERR_UNKNOWN: return "unknown HID handshake error";
        case HID_HANDSHAKE_PARAM_TYPE_ERR_FATAL: return "fatal HID error";
        case HID_HANDSHAKE_PARAM_TYPE_ERR_DISCONNECT: return "link closed";
        default: return "see HID handshake code";
    }
}

static void known_hands(bool known[WMR_NUM_CONTROLLERS]){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++) known[i] = controllers[i].source != WMR_ADDR_NONE;
}

static const char *role_name(hci_role_t role){
    switch (role){
        case HCI_ROLE_MASTER: return "master";
        case HCI_ROLE_SLAVE:  return "slave";
        default:              return "invalid";
    }
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
            WMR_LOGW("link", "%s %s command timed out; retrying",
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
            WMR_LOGW("link", "%s %s command not sent: 0x%02x",
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
    WMR_LOGI("link", "%s link policy: handle=0x%04x settings=0x%04x status=0x%02x "
             "sniff=%s role_switch=%s",
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
            WMR_LOGI("link", "%s link policy write: handle=0x%04x settings=0x%04x status=0x%02x",
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
    WMR_LOGI("link", "%s link opened: handle=0x%04x role=%s",
             controllers[hand].name, handle, role_name(role));
    if (role == HCI_ROLE_SLAVE){
        uint8_t status = gap_request_role(address, HCI_ROLE_MASTER);
        WMR_LOGI("link", "%s master role requested: status=%s (0x%02x)",
                 controllers[hand].name, pair_status_text(status), status);
    }
#else
    (void) address;
#endif
    policy_pump();
    policy_arm();
}

#endif /* WMR_CFG_CLASSIC_NO_SNIFF || WMR_CFG_CLASSIC_FORCE_MASTER */

/* ---- Stored addresses (BTstack TLV, which is NVS on the ESP32) ----------- */

/* The bytes currently in NVS, so a save from every connection open does not rewrite flash
 * when nothing changed. Updated by addresses_load and addresses_save. */
static uint8_t saved_addr_record[WMR_ADDR_RECORD_SIZE];
static bool    saved_addr_record_known;

/* An address may only be written to NVS once it is known to be usable. A successful
 * connection proves it; a link key (for example after `F` forgot the address but kept the
 * bond) is just as good. A freshly discovered address has neither until it has connected,
 * so a failed pairing attempt does not leave a stored address behind. */
static bool address_is_confirmed(controller_t *c){
    if (c->address_confirmed) return true;
    link_key_t link_key;
    link_key_type_t type;
    return gap_get_link_key_for_bd_addr(c->address, link_key, &type);
}

static void addresses_save(void){
    const btstack_tlv_t *tlv = NULL;
    void *context = NULL;
    btstack_tlv_get_instance(&tlv, &context);
    if (!tlv){ WMR_LOGW("boot", "no NVS storage; discovered addresses will not persist"); return; }
    wmr_addr_record_t r;
    memset(&r, 0, sizeof(r));
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!WMR_BRIDGE_HAND_ENABLED(i)) continue;
        if (controllers[i].source != WMR_ADDR_STORED) continue;
        if (!address_is_confirmed(&controllers[i])) continue;
        r.valid[i] = true;
        r.model[i] = controllers[i].model;
        memcpy(r.address[i], controllers[i].address, 6);
    }
    uint8_t value[WMR_ADDR_RECORD_SIZE];
    wmr_addr_record_pack(&r, value);
    if (saved_addr_record_known && memcmp(value, saved_addr_record, sizeof(value)) == 0) return;
    int rc = tlv->store_tag(context, WMR_CFG_ADDR_TLV_TAG, value, sizeof(value));
    if (rc == 0){
        memcpy(saved_addr_record, value, sizeof(value));
        saved_addr_record_known = true;
        WMR_LOGI("pair", "stored addresses saved (LEFT %s, RIGHT %s)",
                 r.valid[0] ? "yes" : "-", r.valid[1] ? "yes" : "-");
    } else {
        WMR_LOGE("pair", "could not save stored addresses (rc=%d)", rc);
    }
}

static void addresses_load(void){
    const btstack_tlv_t *tlv = NULL;
    void *context = NULL;
    btstack_tlv_get_instance(&tlv, &context);
    if (!tlv){ WMR_LOGW("boot", "no NVS storage; using discovery every boot"); return; }
    uint8_t value[WMR_ADDR_RECORD_SIZE];
    int size = tlv->get_tag(context, WMR_CFG_ADDR_TLV_TAG, value, sizeof(value));
    if (size <= 0) return;
    wmr_addr_record_t r;
    if (!wmr_addr_record_unpack(value, (size_t) size, &r)){
        WMR_LOGW("boot", "stored address record is invalid (size %d); ignored", size);
        return;
    }
    memcpy(saved_addr_record, value, sizeof(value));
    saved_addr_record_known = true;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!r.valid[i] || controllers[i].source == WMR_ADDR_OVERRIDE) continue;
        memcpy(controllers[i].address, r.address[i], 6);
        controllers[i].model = r.model[i];
        controllers[i].source = WMR_ADDR_STORED;
        controllers[i].address_confirmed = true;    /* it was stored because it worked */
    }
}

static void print_addresses(void){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        const controller_t *c = &controllers[i];
        WMR_LOGI("boot", "%s address: %s (%s, model %s)", c->name,
                 c->source == WMR_ADDR_NONE ? "unknown" : bd_addr_to_str(c->address),
                 wmr_controller_address_source_name(c->source),
                 c->source == WMR_ADDR_NONE ? "unknown" : wmr_model_name(c->model));
    }
}

static void hid_packet_handler(uint8_t packet_type, uint16_t channel,
                               uint8_t *packet, uint16_t size);

#if WMR_CFG_PSVR2_WAKE
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
    WMR_LOGI("psvr2", "%s wake request: GET_REPORT feature 0x%02x, status=%s (0x%02x)",
             c->name, WMR_PSVR2_WAKE_FEATURE_ID, pair_status_text(status), status);
    if (status != ERROR_CODE_SUCCESS)
        WMR_LOGW("psvr2", "%s wake request refused; reconnect (l/r) to try again", c->name);
}

static void psvr2_wake_arm(controller_t *c){
    c->psvr2_wake_pending = true;
    /* Restart on every arm: both hands can become ready inside one delay window, and a
     * shared timer that kept its original deadline would give the second hand a much
     * shorter delay (possibly none) before its GET_REPORT. */
    btstack_run_loop_remove_timer(&psvr2_wake_timer);
    btstack_run_loop_set_timer(&psvr2_wake_timer, WMR_CFG_PSVR2_WAKE_DELAY_MS);
    btstack_run_loop_add_timer(&psvr2_wake_timer);
    WMR_LOGI("psvr2", "%s wake scheduled in %u ms",
             c->name, (unsigned) WMR_CFG_PSVR2_WAKE_DELAY_MS);
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
static wmr_connect_result_t connect_hand_impl(unsigned hand);
static void disc_connect_next(void);
static void disc_connect_progress(void);
static void disc_pair_failed(unsigned hand);
static void accept_incoming(controller_t *c, uint16_t cid);

#if WMR_CFG_STAGGER_FIRST_HAND
/* True once the first hand has been streaming long enough, or forever after release. */
static bool stagger_gate_open(void){
    if (stagger_released) return true;
    if (!stagger_first_reported) return false;
    if ((uint32_t) (btstack_run_loop_get_time_ms() - stagger_first_report_ms) < STAGGER_GATE_MS)
        return false;
    stagger_released = true;
    WMR_LOGI("stagger", "%s released at t=%lu s",
             controllers[STAGGER_SECOND_HAND].name,
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
        WMR_LOGI("stagger", "%s held until %s reports; first hand reports=%lu",
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
        (void) connect_hand_impl(STAGGER_SECOND_HAND);
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
        if (wmr_addr_parse(c->override, c->address)){
            c->source = WMR_ADDR_OVERRIDE;
            c->address_confirmed = true;    /* never stored, never dropped */
        } else {
            WMR_LOGW("boot", "%s compile-time address \"%s\" is not valid; ignored",
                     c->name, c->override);
        }
    }
    disc_timer.process = disc_timer_fire;
#if WMR_CFG_PSVR2_WAKE
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
    gap_register_classic_connection_filter(disc_accept_classic_connection);
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
        WMR_LOGW("pair", "%s is not served by this build (WMR_CFG_BRIDGE_HANDS=%d)",
                 c->name, WMR_CFG_BRIDGE_HANDS);
        return WMR_CONNECT_NOT_ENABLED;
    }
    if (!stack_ready){ WMR_LOGW("pair", "Bluetooth stack is not ready"); return WMR_CONNECT_BUSY; }
    if (c->source == WMR_ADDR_NONE){
        WMR_LOGW("pair", "%s has no address; put it in pairing mode and press d", c->name);
        return WMR_CONNECT_NO_ADDRESS;
    }
    if (connect_pending){
        WMR_LOGW("pair", "another connection or discovery is still in progress");
        return WMR_CONNECT_BUSY;
    }
    if (c->cid){
        WMR_LOGW("pair", "%s is already open or opening (cid=%u)", c->name, c->cid);
        return WMR_CONNECT_BUSY;
    }
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
    WMR_LOGI("pair", "connecting %s at %s", c->name, bd_addr_to_str(c->address));
    uint8_t status = hid_host_connect(c->address, HID_PROTOCOL_MODE_REPORT, &c->cid);
    if (status != ERROR_CODE_SUCCESS){
        WMR_LOGW("pair", "%s connect request failed: %s (0x%02x)",
                 c->name, pair_status_text(status), status);
        c->cid = 0;
        connect_pending = false;
        return WMR_CONNECT_BUSY;
    }
    return WMR_CONNECT_OK;
}

wmr_connect_result_t wmr_controller_connect(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return WMR_CONNECT_BUSY;
    if (disc_phase == DISC_INQUIRY || disc_phase == DISC_NAMES){
        WMR_LOGW("pair", "discovery is running; wait for it to finish before connecting");
        return WMR_CONNECT_BUSY;
    }
    return connect_hand_impl(hand);
}

wmr_disconnect_result_t wmr_controller_disconnect(unsigned hand){
    if (hand >= WMR_NUM_CONTROLLERS) return WMR_DISCONNECT_NOT_CONNECTED;
    controller_t *c = &controllers[hand];
    if (!c->cid){
        WMR_LOGW("pair", "%s has no connection to close", c->name);
        return WMR_DISCONNECT_NOT_CONNECTED;
    }
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

/* Wait before another discovery attempt. Used when an inquiry cannot start (the radio is
 * busy with a connection) and when a run ends with a hand still missing: the controller may
 * only be put in pairing mode after the run, so giving up until the next reboot is wrong.
 * Bounded, because an inquiry shares the radio with a controller that is already streaming;
 * after WMR_CFG_DISCOVERY_RETRIES extra bursts, discovery waits for the console (`d`).
 * With auto-start compiled out, discovery is only ever started by the console. */
#if WMR_CFG_DISCOVERY_AUTOSTART
static unsigned disc_retries;

static void disc_schedule_retry(void){
    if (disc_retries >= WMR_CFG_DISCOVERY_RETRIES){
        WMR_LOGW("pair", "discovery gave up after %u retry burst(s); put the controller "
                 "in pairing mode and press d", disc_retries);
        disc_retries = 0;      /* the next `d` starts with a fresh budget */
        disc_phase = DISC_IDLE;
        btstack_run_loop_remove_timer(&disc_timer);
        return;
    }
    disc_retries++;
    disc_phase = DISC_IDLE;
    btstack_run_loop_remove_timer(&disc_timer);
    btstack_run_loop_set_timer(&disc_timer, WMR_CFG_DISCOVERY_RETRY_MS);
    btstack_run_loop_add_timer(&disc_timer);
    WMR_LOGI("pair", "discovery will try again in %u s",
             (unsigned) (WMR_CFG_DISCOVERY_RETRY_MS / 1000));
}
#endif

/* One line per served hand after a discovery run, so the serial monitor says
 * what is ready and what still needs a person. */
static void disc_pair_summary(void){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!WMR_BRIDGE_HAND_ENABLED(i)) continue;
        const controller_t *c = &controllers[i];
        if (c->connected && c->descriptor_available){
            WMR_LOGI("pair", "%s ready at %s (%s)", c->name, bd_addr_to_str(c->address),
                     wmr_model_name(c->model));
        } else if (c->connected){
            WMR_LOGI("pair", "%s link is up at %s; waiting for the HID descriptor",
                     c->name, bd_addr_to_str(c->address));
        } else if (c->source != WMR_ADDR_NONE){
            WMR_LOGI("pair", "%s is known at %s (%s) but not connected; press %c to reconnect",
                     c->name, bd_addr_to_str(c->address),
                     wmr_controller_address_source_name(c->source),
                     i == WMR_HAND_LEFT ? 'l' : 'r');
        } else {
            WMR_LOGW("pair", "%s is not paired; put it in pairing mode and press d", c->name);
        }
    }
}

static void disc_finish(void){
    disc_phase = DISC_IDLE;
    btstack_run_loop_remove_timer(&disc_timer);
    bool missing = false;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        /* A hand that regained its address some other way during the run (an incoming
         * connection sets WMR_ADDR_STORED) is not missing: disc.found[] stays false for it,
         * but reporting it and scheduling a retry would invite a needless `F` that wipes a
         * working bond. A hand actually dropped by disc_pair_failed() has source NONE here
         * and is still reported. */
        if (controllers[i].source != WMR_ADDR_NONE) continue;
        if (!wmr_discovery_hand_missing(&disc, (int) i)) continue;
        if (disc.found[i]){
            WMR_LOGW("pair", "%s was found but did not pair after %u attempt(s)",
                     controllers[i].name, disc_pair_attempts[i]);
        } else {
            WMR_LOGW("pair", "%s was not found after %u inquiry round(s)",
                     controllers[i].name, disc_round);
        }
        missing = true;
    }
    WMR_LOGI("pair", "discovery finished");
    disc_pair_summary();
#if WMR_CFG_DISCOVERY_AUTOSTART
    if (missing){
        disc_schedule_retry();
    } else {
        disc_retries = 0;
    }
#else
    (void) missing;
#endif
}

static void disc_inquiry_round(void){
    disc_round++;
    wmr_discovery_begin_round(&disc);
    disc_phase = DISC_INQUIRY;
    char wanted[32] = "";
    size_t used = 0;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!disc.wanted[i] || disc.found[i]) continue;
        int written = snprintf(wanted + used, sizeof(wanted) - used, "%s%s",
                               used ? "/" : "", controllers[i].name);
        if (written < 0) break;
        used += (size_t) written;
        if (used >= sizeof(wanted)) break;
    }
    WMR_LOGI("pair", "inquiry round %u/%u (%u x 1.28 s), looking for %s; "
             "put the controller in pairing mode",
             disc_round, WMR_CFG_DISCOVERY_ROUNDS, WMR_CFG_DISCOVERY_INQUIRY_LEN,
             wanted[0] ? wanted : "no hand");
    int rc = gap_inquiry_start(WMR_CFG_DISCOVERY_INQUIRY_LEN);
    if (rc != 0){
        WMR_LOGW("pair", "inquiry could not start (rc=%d)", rc);
#if WMR_CFG_DISCOVERY_AUTOSTART
        disc_schedule_retry();
#else
        disc_finish();
#endif
    }
}

/* After a round's connections are done: another round if a hand is still missing. A hand
 * that acquired its address from an incoming connection during the round is not worth
 * another inquiry even though disc.found[] stayed false, so it is skipped here as well as
 * in disc_finish() (wmr_discovery_incomplete() itself only describes the discovery state). */
static void disc_after_connects(void){
    bool missing = false;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!disc.wanted[i] || disc.found[i]) continue;
        if (controllers[i].source == WMR_ADDR_NONE) missing = true;
    }
    if (missing && disc_round < WMR_CFG_DISCOVERY_ROUNDS){
        disc_inquiry_round();
        return;
    }
    disc_finish();
}

/* Connect the hands found in this round one at a time (the controller is in pairing mode,
 * so this also pairs it). Runs again from the timer whenever an attempt ends. */
static void disc_connect_next(void){
    if (disc_phase != DISC_CONNECT || connect_pending) return;
    disc_pair_wait = false;
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
        wmr_connect_result_t r = connect_hand_impl(hand);
        if (r == WMR_CONNECT_OK) return;               /* progress arrives as HID events */
        if (r == WMR_CONNECT_NOT_ENABLED || r == WMR_CONNECT_NO_ADDRESS) continue;
        /* connect_pending was false on entry and the request was refused outright: count
         * this as a failed pairing attempt instead of silently ending the round. */
        disc_pair_failed(hand);
        if (disc_pair_mask & (1u << hand)){
            disc_connect_progress();                   /* retry after the pairing delay */
            return;
        }
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
    if (disc_phase == DISC_IDLE && need_discovery){
        if (wmr_controller_discover() == WMR_DISCOVERY_BUSY){
#if WMR_CFG_DISCOVERY_AUTOSTART
            /* A connection (or an incoming name check) still holds the radio; the retry
             * must not be the last one, or a hand would stay missing until the next boot. */
            disc_schedule_retry();
#endif
        }
    }
}

/* Called whenever a connection attempt ends (descriptor, failure, close). A pairing failure
 * waits WMR_CFG_PAIR_RETRY_MS before the next attempt, so the controller can leave the
 * failed link; other progress uses the short delay as before. */
static void disc_connect_progress(void){
    if (disc_phase != DISC_CONNECT) return;
    btstack_run_loop_remove_timer(&disc_timer);
    btstack_run_loop_set_timer(&disc_timer,
                               disc_pair_wait ? WMR_CFG_PAIR_RETRY_MS : 100);
    btstack_run_loop_add_timer(&disc_timer);
}

/* A connect attempt for a hand learned in this run did not end in a connection. Retry the
 * same address a bounded number of times; if that does not work, drop an address that has
 * no link key (it never paired) and mark the hand unusable so this run ends and a later run
 * can look for it again. An address with a link key is kept: it is paired, just not
 * connected. */
static void disc_pair_failed(unsigned hand){
    if (disc_phase != DISC_CONNECT) return;
    if (!(disc_pair_mask & (1u << hand))) return;
    controller_t *c = &controllers[hand];
    disc_pair_attempts[hand]++;
    if (disc_pair_attempts[hand] < (unsigned) WMR_CFG_PAIR_ATTEMPTS){
        WMR_LOGW("pair", "%s did not connect (attempt %u/%u); retrying %s in %u ms",
                 c->name, disc_pair_attempts[hand], (unsigned) WMR_CFG_PAIR_ATTEMPTS,
                 bd_addr_to_str(c->address), (unsigned) WMR_CFG_PAIR_RETRY_MS);
        disc_connect_mask |= 1u << hand;
        disc_pair_wait = true;
        return;
    }
    disc_pair_mask &= ~(1u << hand);
    if (address_is_confirmed(c)){
        WMR_LOGW("pair", "%s did not connect after %u attempt(s); stored address kept",
                 c->name, disc_pair_attempts[hand]);
        addresses_save();
        return;
    }
    WMR_LOGE("pair", "%s did not pair after %u attempt(s); address forgotten. "
             "Put it in pairing mode and press d", c->name, disc_pair_attempts[hand]);
    c->source = WMR_ADDR_NONE;
    c->model = WMR_MODEL_WMR;
    memset(c->address, 0, sizeof(bd_addr_t));
    /* Keep the match on record so this run ends instead of spending the remaining inquiry
     * rounds on a controller that is not in pairing mode; the end-of-run retry and `d`
     * start a fresh run that looks for it again. */
    wmr_discovery_hand_unusable(&disc, (int) hand);
}

/* The round's inquiry and name requests are done: keep what was found and connect it. The
 * address is not stored until it is confirmed (see address_is_confirmed). */
static void disc_round_done(void){
    disc_connect_mask = 0;
    bool changed = false;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (!WMR_BRIDGE_HAND_ENABLED(i)) continue;
        if (!disc.found[i] || controllers[i].source != WMR_ADDR_NONE) continue;
        memcpy(controllers[i].address, disc.found_address[i], 6);
        controllers[i].model = disc.found_model[i];
        controllers[i].source = WMR_ADDR_STORED;
        controllers[i].address_confirmed = false;
        disc_connect_mask |= 1u << i;
        disc_pair_mask |= 1u << i;
        disc_pair_attempts[i] = 0;
        changed = true;
    }
    if (changed) addresses_save();      /* only hands with a link key are stored */
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
                    WMR_LOGI("pair", "found %s at %s (%s, model %s)", controllers[i].name,
                             bd_addr_to_str(address), how, wmr_model_name(disc.found_model[i]));
            }
            break;
        case WMR_DISC_DUPLICATE:
            WMR_LOGW("pair", "another controller at %s has the same name; using the first one. "
                     "Keep only one controller of each hand in pairing mode", bd_addr_to_str(address));
            break;
        case WMR_DISC_ALREADY_KNOWN:
            WMR_LOGW("pair", "%s has a controller name but that hand already has an address; ignored",
                     bd_addr_to_str(address));
            break;
        case WMR_DISC_QUEUE_FULL:
            WMR_LOGW("pair", "too many unnamed devices in range; skipped %s",
                     bd_addr_to_str(address));
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
    if (!stack_ready){
        WMR_LOGW("pair", "Bluetooth stack is not ready; cannot start discovery");
        return WMR_DISCOVERY_BUSY;
    }
    if (disc_phase != DISC_IDLE){
        WMR_LOGI("pair", "discovery is already running");
        return WMR_DISCOVERY_BUSY;
    }
    if (connect_pending || name_purpose != NAME_NONE){
        WMR_LOGW("pair", "a connection attempt is pending; try discovery again when it ends");
        return WMR_DISCOVERY_BUSY;
    }
    bool known[WMR_NUM_CONTROLLERS];
    known_hands(known);
    wmr_discovery_start(&disc, known);
    if (!wmr_discovery_incomplete(&disc)){
        if (WMR_CFG_BRIDGE_HANDS == 0){
            WMR_LOGI("pair", "LEFT and RIGHT addresses are known; nothing to discover "
                     "(F forgets the stored addresses)");
        } else {
            WMR_LOGI("pair", "the served controller address is known; nothing to discover "
                     "(F forgets the stored address)");
        }
        return WMR_DISCOVERY_NOTHING_TO_DO;
    }
    disc_round = 0;
    disc_pair_mask = 0;
    disc_pair_wait = false;
    disc_incoming_rejected = false;
    memset(disc_pair_attempts, 0, sizeof(disc_pair_attempts));
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
        controllers[i].address_confirmed = false;
        memset(controllers[i].address, 0, sizeof(bd_addr_t));
    }
    const btstack_tlv_t *tlv = NULL;
    void *context = NULL;
    btstack_tlv_get_instance(&tlv, &context);
    if (tlv) tlv->delete_tag(context, WMR_CFG_ADDR_TLV_TAG);
    saved_addr_record_known = false;
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
        WMR_LOGW("pair", "%s is not served by this build (WMR_CFG_BRIDGE_HANDS=%d); declining",
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
    WMR_LOGI("pair", "%s accepts the controller's incoming HID connection (cid=%u)",
             c->name, cid);
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
        WMR_LOGW("pair", "incoming %s has name \"%.*s\", which is not a controller "
                 "without an address; declining", bd_addr_to_str(name_address),
                 name ? (int) len : 0, name ? (const char *) name : "");
        hid_host_decline_connection(cid);
        return;
    }
    controller_t *c = &controllers[hand];
    memcpy(c->address, name_address, 6);
    c->handle = incoming_con_handle;
    c->model = wmr_discovery_model_for_name(name, len);
    c->source = WMR_ADDR_STORED;
    c->address_confirmed = true;    /* the ACL came up from this address: it is usable */
    WMR_LOGI("pair", "incoming controller identified as %s at %s (model %s)",
             c->name, bd_addr_to_str(c->address), wmr_model_name(c->model));
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
static void log_link_role(const bd_addr_t address, uint8_t status, hci_role_t role,
                          const char *event){
    int hand = wmr_controller_hand_for_address(address);
    WMR_LOGI("link", "%s role: address=%s role=%s status=0x%02x event=%s t_ms=%lu",
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
            controller_t *c = controller_for_address(address);
            if (status == ERROR_CODE_SUCCESS){
                role = gap_get_role(handle);
                if (c) c->handle = handle;
                WMR_LOGI("pair", "%s link up at %s (handle=0x%04x, role=%s)",
                         c ? c->name : "unknown controller", bd_addr_to_str(address),
                         handle, role_name(role));
            } else {
                WMR_LOGW("pair", "link to %s failed: %s (0x%02x)",
                         c ? c->name : bd_addr_to_str(address),
                         pair_status_text(status), status);
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
            WMR_LOGI("link", "%s link role changed to %s (status=%s)",
                     hand >= 0 ? controllers[hand].name : bd_addr_to_str(address),
                     role_name(role), pair_status_text(status));
#endif
            break;
        }
        case HCI_EVENT_AUTHENTICATION_COMPLETE: {
            uint8_t status = hci_event_authentication_complete_get_status(packet);
            hci_con_handle_t handle = hci_event_authentication_complete_get_connection_handle(packet);
            char who[24];
            const char *name = hand_or_handle(handle, who, sizeof(who));
            if (status == ERROR_CODE_SUCCESS){
                WMR_LOGI("pair", "%s authenticated", name);
            } else {
                WMR_LOGW("pair", "%s authentication failed: %s (0x%02x); "
                         "remove the controller's old pairing or press k/K for a bond reset",
                         name, pair_status_text(status), status);
            }
            break;
        }
        case HCI_EVENT_ENCRYPTION_CHANGE: {
            uint8_t status = hci_event_encryption_change_get_status(packet);
            hci_con_handle_t handle = hci_event_encryption_change_get_connection_handle(packet);
            uint8_t enabled = hci_event_encryption_change_get_encryption_enabled(packet);
            char who[24];
            const char *name = hand_or_handle(handle, who, sizeof(who));
            if (status == ERROR_CODE_SUCCESS && enabled){
                WMR_LOGI("pair", "%s link encrypted", name);
            } else if (status != ERROR_CODE_SUCCESS){
                WMR_LOGW("pair", "%s encryption failed: %s (0x%02x)",
                         name, pair_status_text(status), status);
            } else {
                WMR_LOGW("pair", "%s encryption was disabled", name);
            }
            break;
        }
        case HCI_EVENT_SIMPLE_PAIRING_COMPLETE: {
            uint8_t status = hci_event_simple_pairing_complete_get_status(packet);
            hci_event_simple_pairing_complete_get_bd_addr(packet, address);
            controller_t *c = controller_for_address(address);
            if (status == ERROR_CODE_SUCCESS){
                WMR_LOGI("pair", "%s pairing completed at %s",
                         c ? c->name : "controller", bd_addr_to_str(address));
            } else {
                WMR_LOGW("pair", "%s pairing failed at %s: %s (0x%02x)",
                         c ? c->name : "controller", bd_addr_to_str(address),
                         pair_status_text(status), status);
            }
            break;
        }
        case HCI_EVENT_PIN_CODE_REQUEST:
            hci_event_pin_code_request_get_bd_addr(packet, address);
            WMR_LOGW("pair", "legacy PIN request from %s rejected; use SSP pairing",
                     bd_addr_to_str(address));
            gap_pin_code_negative(address);
            break;
        case HCI_EVENT_USER_CONFIRMATION_REQUEST:
            hci_event_user_confirmation_request_get_bd_addr(packet, address);
            WMR_LOGI("pair", "accepting SSP confirmation from %s (test setup)",
                     bd_addr_to_str(address));
            gap_ssp_confirmation_response(address);
            break;
        case HCI_EVENT_DISCONNECTION_COMPLETE: {
            hci_con_handle_t handle = hci_event_disconnection_complete_get_connection_handle(packet);
            uint8_t status = hci_event_disconnection_complete_get_status(packet);
            uint8_t reason = hci_event_disconnection_complete_get_reason(packet);
            char who[24];
            const char *name = hand_or_handle(handle, who, sizeof(who));
            WMR_LOGI("pair", "%s link closed: %s (status=0x%02x reason=0x%02x)",
                     name, pair_status_text(reason), status, reason);
            controller_t *c = controller_for_handle(handle);
            if (c) c->handle = HCI_CON_HANDLE_INVALID;
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
    WMR_LOGI("pair", "incoming HID connection from %s (cid=%u)",
             bd_addr_to_str(address), cid);
    if (status != ERROR_CODE_SUCCESS){
        WMR_LOGW("pair", "incoming HID connection from %s failed: %s (0x%02x)",
                 bd_addr_to_str(address), pair_status_text(status), status);
        return;
    }

    controller_t *c = controller_for_address(address);
    if (c && !WMR_BRIDGE_HAND_ENABLED((unsigned) (c - controllers))){
        /* A known address of the excluded hand (for example stored by an earlier
         * both-hands build) is treated exactly like a device that is not a controller. */
        WMR_LOGW("pair", "incoming %s is not served by this build (WMR_CFG_BRIDGE_HANDS=%d); declining",
                 c->name, WMR_CFG_BRIDGE_HANDS);
        hid_host_decline_connection(cid);
        return;
    }
    if (!c){
        bool known[WMR_NUM_CONTROLLERS];
        known_hands(known);
        if (known[0] && known[1]){
            WMR_LOGW("pair", "incoming device %s is not LEFT or RIGHT; declining",
                     bd_addr_to_str(address));
            hid_host_decline_connection(cid);
            return;
        }
        if (name_purpose != NAME_NONE || connect_pending){
            WMR_LOGW("pair", "incoming unknown device %s arrived while another check "
                     "or connection is pending; declining", bd_addr_to_str(address));
            hid_host_decline_connection(cid);
            return;
        }
        memcpy(name_address, address, 6);
        name_purpose = NAME_FOR_INCOMING;
        incoming_cid = cid;
        incoming_con_handle = hid_subevent_incoming_connection_get_handle(packet);
        connect_pending = true;
        WMR_LOGI("pair", "incoming unknown device %s; asking its name before accepting",
                 bd_addr_to_str(address));
        if (gap_remote_name_request(name_address, 1, 0) != 0){
            name_purpose = NAME_NONE;
            incoming_resolved(NULL, 0);
        }
        return;
    }
    if (c->cid != 0 && c->cid != cid){
        WMR_LOGW("pair", "%s is already tracked with cid=%u; not accepting cid=%u",
                 c->name, c->cid, cid);
        return;
    }
    c->handle = hid_subevent_incoming_connection_get_handle(packet);
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
    if (c->model == WMR_MODEL_PSVR2){
        if (!wmr_psvr2_is_input_report(report, length)){
            c->other_reports++;
            return;
        }
    } else
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
        WMR_LOGI("pair", "%s is streaming: first input report (cid=%u, %u bytes, "
                 "descriptor %s)", c->name, cid, length,
                 c->descriptor_available ? "available" : "not available");
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
                WMR_LOGW("pair", "untracked HID open (cid=%u): %s (0x%02x)",
                         cid, pair_status_text(status), status);
                if (status == ERROR_CODE_SUCCESS) hid_host_disconnect(cid);
                connect_pending = false;
                break;
            }
            c->psvr2_wake_pending = false;
            if (status != ERROR_CODE_SUCCESS){
                WMR_LOGW("pair", "%s HID connection failed: %s (0x%02x)",
                         c->name, pair_status_text(status), status);
                disc_pair_failed((unsigned) (c - controllers));
                c->cid = 0;
                c->connected = false;
                connect_pending = false;
                disc_connect_progress();
                break;
            }
            c->connected = true;
            c->descriptor_available = false;
            /* The connection (and its pairing, if it needed one) succeeded: this address
             * can now be stored. No-op when it already is. */
            c->address_confirmed = true;
            disc_pair_mask &= ~(1u << (unsigned) (c - controllers));
            disc_pair_attempts[(unsigned) (c - controllers)] = 0;
            if (c->source == WMR_ADDR_STORED) addresses_save();
            WMR_LOGI("pair", "%s HID channel open (cid=%u); waiting for the descriptor",
                     c->name, cid);
            break;
        case HID_SUBEVENT_DESCRIPTOR_AVAILABLE:
            cid = hid_subevent_descriptor_available_get_hid_cid(packet);
            status = hid_subevent_descriptor_available_get_status(packet);
            c = controller_for_cid(cid);
            if (!c) break;
            c->descriptor_available = status == ERROR_CODE_SUCCESS;
            if (c->descriptor_available){
                WMR_LOGI("pair", "%s ready: HID descriptor received (model %s)",
                         c->name, wmr_model_name(c->model));
            } else {
                WMR_LOGW("pair", "%s HID descriptor failed: %s (0x%02x)",
                         c->name, pair_status_text(status), status);
            }
            if (c->descriptor_available){
                if (c->model == WMR_MODEL_PSVR2){
#if WMR_CFG_PSVR2_WAKE
                    psvr2_wake_arm(c);
#else
                    WMR_LOGI("pair", "PSVR2 %s connected; wake request disabled in this build",
                             c->name);
#endif
                } else
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
            WMR_LOGI("ff13", "%s GET_REPORT response: %s (0x%02x), %u bytes",
                     c ? c->name : "?", hid_handshake_text(status), status, report_len);
#if WMR_CFG_REPORT_HEXDUMP
            if (status == HID_HANDSHAKE_PARAM_TYPE_SUCCESSFUL && report_len)
                printf_hexdump(hid_subevent_get_report_response_get_report(packet), report_len);
#endif
            break;
        }
        case HID_SUBEVENT_REPORT:
            handle_report(packet);
            break;
        case HID_SUBEVENT_SET_PROTOCOL_RESPONSE:
            status = hid_subevent_set_protocol_response_get_handshake_status(packet);
            if (status == HID_HANDSHAKE_PARAM_TYPE_SUCCESSFUL){
                WMR_LOGI("pair", "HID report protocol selected");
            } else {
                WMR_LOGW("pair", "HID SET_PROTOCOL failed: %s (0x%02x)",
                         hid_handshake_text(status), status);
            }
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
            if (!c->connected) disc_pair_failed((unsigned) (c - controllers));
#if WMR_CFG_STAGGER_FIRST_HAND
            if ((unsigned) (c - controllers) == STAGGER_FIRST_HAND && !stagger_released){
                /* Start the 30 s again after the first hand reconnects. */
                stagger_first_reported = false;
                stagger_first_report_ms = 0;
            }
            if ((unsigned) (c - controllers) == STAGGER_SECOND_HAND)
                stagger_hold_logged[STAGGER_SECOND_HAND] = false;
#endif
            WMR_LOGI("pair", "%s disconnected after %lu input report(s) and %lu other report(s)",
                     c->name, c->reports, c->other_reports);
            wmr_cfgmgr_on_closed((unsigned) (c - controllers));
            if (!c->connected || !c->descriptor_available) connect_pending = false;
            c->cid = 0;
            c->handle = HCI_CON_HANDLE_INVALID;
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
