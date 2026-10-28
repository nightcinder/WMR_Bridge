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
 * wmr_secure.h - 0.3 phone-link security policy: LE Secure Connections with bonding
 * only, and no second central. Pure C, no BTstack dependency, host-tested.
 *
 * The transport module translates SM events and its pairing timer into
 * wmr_secure_event_t and acts on the returned wmr_secure_action_t.
 * The threat model and the residual risks are in docs/SECURITY.md.
 */

#ifndef WMR_SECURE_H
#define WMR_SECURE_H

#include <stdint.h>
#include <stdbool.h>

/* HCI_CON_HANDLE_INVALID is 0xffff in BTstack; keep this module free of BTstack. */
#define WMR_SECURE_NO_HANDLE 0xffffu

typedef enum {
    /* Link is encrypted and the central is bonded: serve it. */
    WMR_SECURE_ACCEPT = 0,
    /* Disconnect the central: it did not produce a bonded LESC link. */
    WMR_SECURE_REFUSE,
    /* Not about the phone link; leave the connection alone. */
    WMR_SECURE_IGNORE
} wmr_secure_action_t;

typedef enum {
    /* SM_EVENT_PAIRING_COMPLETE for a fresh pairing. */
    WMR_SECURE_PAIRING_COMPLETE = 0,
    /* SM_EVENT_REENCRYPTION_COMPLETE for a reconnect with stored keys. */
    WMR_SECURE_REENCRYPTION_COMPLETE,
    /* The pairing timer expired before the phone became secure. */
    WMR_SECURE_PAIR_TIMEOUT
} wmr_secure_event_type_t;

typedef struct {
    wmr_secure_event_type_t type;
    uint16_t handle;        /* connection the event is about */
    uint16_t phone_handle;  /* current phone connection, WMR_SECURE_NO_HANDLE if free */
    bool success;           /* SM status was ERROR_CODE_SUCCESS */
    bool bonded;            /* a bond exists for this handle (sm_le_device_index >= 0) */
    bool encrypted;         /* link is currently encrypted (used by the timeout) */
} wmr_secure_event_t;

/* Decide what to do with one connection. A second central is refused at connect,
 * so an event about a handle other than the phone's is ignored here. */
wmr_secure_action_t wmr_secure_decide(const wmr_secure_event_t *event);

/* Only one phone is served at a time. Accept the first central, refuse the rest. */
bool wmr_secure_may_connect(uint16_t phone_handle, uint16_t new_handle);

#endif /* WMR_SECURE_H */
