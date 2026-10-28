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
 *    contributors may be used to endorse or promote products derived from
 *    this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY BLUEKITCHEN GMBH AND CONTRIBUTORS
 * ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL BLUEKITCHEN
 * GMBH OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *
 * Please inquire about commercial licensing options at 
 * contact@bluekitchen-gmbh.com
 *
 */

/* wmr_linkwait.c - see wmr_linkwait.h. */

#define BTSTACK_FILE__ "wmr_linkwait.c"

#include <string.h>
#include "wmr_linkwait.h"

void wmr_linkwait_start(wmr_linkwait_t *wait, uint32_t now_ms){
    memset(wait, 0, sizeof(*wait));
    wait->armed = true;
    wait->link_up_ms = now_ms;
}

void wmr_linkwait_finish(wmr_linkwait_t *wait){
    wait->armed = false;
    wait->encrypt_sent = false;
    wait->link_up_ms = 0;
}

wmr_linkwait_action_t wmr_linkwait_poll(wmr_linkwait_t *wait, bool encrypted, uint32_t now_ms){
    if (!wait->armed) return WMR_LINKWAIT_NONE;
    /* Unsigned subtraction keeps the elapsed time correct across the 32-bit wrap. */
    uint32_t elapsed = now_ms - wait->link_up_ms;
    if (!encrypted && !wait->encrypt_sent && elapsed >= WMR_LINKWAIT_ENCRYPT_MS){
        wait->encrypt_sent = true;
        return WMR_LINKWAIT_ENCRYPT;
    }
    if (elapsed >= WMR_LINKWAIT_DROP_MS){
        wait->armed = false;
        return WMR_LINKWAIT_DROP;
    }
    return WMR_LINKWAIT_NONE;
}
