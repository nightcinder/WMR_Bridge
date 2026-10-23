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
 * wmr_bridge_main.c - initialization and event routing only.
 *
 * Callback routing (each event reaches each module exactly once):
 *   - HCI events      -> wmr_bridge_hci_handler  -> controller / transport / linkdiag / console
 *   - HID host events -> wmr_controller's own HID handler
 *   - ATT events      -> wmr_transport's own ATT handler (CAN_SEND_NOW only)
 * Do not register one handler for several of these sources.
 */

#define BTSTACK_FILE__ "wmr_bridge_main.c"

#include <stdio.h>
#include "btstack_config.h"
#include "btstack.h"
#include "wmr_config.h"
#include "wmr_console.h"
#include "wmr_controller.h"
#include "wmr_linkdiag.h"
#include "wmr_transport.h"

static btstack_packet_callback_registration_t hci_registration;

#if WMR_CFG_HCI_TRACE
static void trace_hci_event(uint8_t *packet, uint16_t size){
    switch (hci_event_packet_get_type(packet)){
        case HCI_EVENT_COMMAND_STATUS:
        case HCI_EVENT_CONNECTION_COMPLETE:
        case HCI_EVENT_CONNECTION_REQUEST:
        case HCI_EVENT_DISCONNECTION_COMPLETE:
        case HCI_EVENT_ROLE_CHANGE:
        case HCI_EVENT_AUTHENTICATION_COMPLETE:
        case HCI_EVENT_LINK_KEY_REQUEST:
        case HCI_EVENT_PIN_CODE_REQUEST:
        case HCI_EVENT_SIMPLE_PAIRING_COMPLETE:
        case HCI_EVENT_ENCRYPTION_CHANGE:
            printf("HCI event=0x%02x size=%u\n", hci_event_packet_get_type(packet), size);
            printf_hexdump(packet, size);
            break;
        default:
            break;
    }
}
#endif

static void wmr_bridge_hci_handler(uint8_t packet_type, uint16_t channel,
                                   uint8_t *packet, uint16_t size){
    (void)channel;
    if (packet_type != HCI_EVENT_PACKET || size < 2) return;
#if WMR_CFG_HCI_TRACE
    trace_hci_event(packet, size);
#endif
    wmr_transport_on_hci_event(packet, size);
    wmr_controller_on_hci_event(packet, size);
    wmr_linkdiag_on_hci_event(packet, size);
    wmr_console_on_hci_event(packet, size);
}

int btstack_main(int argc, const char *argv[]);
int btstack_main(int argc, const char *argv[]){
    (void)argc;
    (void)argv;

    l2cap_init();
#ifdef ENABLE_BLE
    sm_init();
#endif
    wmr_controller_init(wmr_transport_submit_report);   /* HID host + handler */
    wmr_transport_init();                               /* ATT server + handler */
    wmr_linkdiag_init();                                /* diagnostics: log sink + polling timers */

#if WMR_CFG_CLASSIC_FORCE_MASTER
    /* V3: outgoing connections must not ask for a role switch; the per-handle write in
     * wmr_controller.c disables role switch and sniff after each ACL comes up. */
    gap_set_allow_role_switch(false);
    gap_set_default_link_policy_settings(
        LM_LINK_POLICY_ENABLE_SNIFF_MODE | LM_LINK_POLICY_ENABLE_ROLE_SWITCH);
#elif WMR_CFG_CLASSIC_NO_SNIFF
    /* V2: default policy without sniff. wmr_controller.c also writes it per handle. */
    gap_set_default_link_policy_settings(LM_LINK_POLICY_ENABLE_ROLE_SWITCH);
#else
    gap_set_default_link_policy_settings(
        LM_LINK_POLICY_ENABLE_SNIFF_MODE | LM_LINK_POLICY_ENABLE_ROLE_SWITCH);
#endif
    hci_set_master_slave_policy(HCI_ROLE_MASTER);
    hci_registration.callback = wmr_bridge_hci_handler;
    hci_add_event_handler(&hci_registration);
    gap_discoverable_control(1);

    setvbuf(stdout, NULL, _IONBF, 0);
    wmr_console_init();
    hci_power_control(HCI_POWER_ON);
    return 0;
}
