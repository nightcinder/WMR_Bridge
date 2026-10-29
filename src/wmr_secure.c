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

#define BTSTACK_FILE__ "wmr_secure.c"

#include "wmr_secure.h"

/* A connection only becomes usable once the SM reports success *and* the bond is
 * stored. "Encrypted" alone is not enough: a central can request encrypt-only
 * pairing, and a legacy link can be encrypted without LESC. BTstack's
 * SC-only mode refuses legacy pairing, so a bonded entry here implies LESC. */
static bool link_is_secure(const wmr_secure_event_t *event){
    return event->success && event->bonded;
}

wmr_secure_action_t wmr_secure_decide(const wmr_secure_event_t *event){
    if (!event) return WMR_SECURE_IGNORE;
    if (event->phone_handle == WMR_SECURE_NO_HANDLE) return WMR_SECURE_IGNORE;
    if (event->handle != event->phone_handle) return WMR_SECURE_IGNORE;

    switch (event->type){
        case WMR_SECURE_PAIRING_COMPLETE:
            return link_is_secure(event) ? WMR_SECURE_ACCEPT : WMR_SECURE_REFUSE;
        case WMR_SECURE_REENCRYPTION_COMPLETE:
            if (link_is_secure(event)) return WMR_SECURE_ACCEPT;
            /* PIN_OR_KEY_MISSING: our stored key is stale because the phone
             * forgot the bridge. Delete it and let the phone pair again, instead
             * of disconnecting in the middle of the pairing exchange. */
            return event->stale_bond ? WMR_SECURE_REPAIR : WMR_SECURE_REFUSE;
        case WMR_SECURE_PAIR_TIMEOUT:
            /* A bonded phone may have been encrypted without an SM event we saw;
             * accept only if the link says it is encrypted. */
            return (event->encrypted && event->bonded) ? WMR_SECURE_ACCEPT : WMR_SECURE_REFUSE;
        default:
            return WMR_SECURE_IGNORE;
    }
}

wmr_secure_event_t wmr_secure_timeout_event(uint16_t phone_handle, bool bonded,
                                            uint8_t encryption_key_size){
    wmr_secure_event_t event = {
        .type = WMR_SECURE_PAIR_TIMEOUT,
        .handle = phone_handle,
        .phone_handle = phone_handle,
        .success = false,
        .bonded = bonded,
        .encrypted = encryption_key_size != 0,
    };
    return event;
}

bool wmr_secure_may_connect(uint16_t phone_handle, uint16_t new_handle){
    if (phone_handle == WMR_SECURE_NO_HANDLE) return true;
    return phone_handle == new_handle;
}
