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

/*
 * wmr_linkwait.h - watchdog for a Classic ACL that came up without encryption/HID.
 *
 * A controller that reconnects on its own normally encrypts the ACL about 80 ms after
 * the link comes up, and only then opens its HID channel. After the discovery handoff
 * the 2026-10-28 field log (ONY-446) shows the ACL staying up with neither for 18 s,
 * until the ESP32 was reset. This module is the host's side of that handoff: it asks
 * the controller to encrypt the link when the controller does not, and drops a link
 * that never produces a HID channel so the controller pages again.
 *
 * Pure state: the caller owns the BTstack timer and the HCI commands, so this can be
 * host-tested without a radio.
 */

#ifndef WMR_LINKWAIT_H
#define WMR_LINKWAIT_H

#include <stdbool.h>
#include <stdint.h>

/* Poll interval for the caller's timer. */
#define WMR_LINKWAIT_POLL_MS     250u
/* How long an unencrypted ACL waits for the controller to start encryption itself
 * before the host requests it. */
#define WMR_LINKWAIT_ENCRYPT_MS  750u
/* How long an ACL may stay unusable (no encryption, or encryption but no HID channel)
 * before it is dropped. */
#define WMR_LINKWAIT_DROP_MS     4000u

typedef enum {
    WMR_LINKWAIT_NONE = 0,
    WMR_LINKWAIT_ENCRYPT,     /* ask the controller to enable encryption */
    WMR_LINKWAIT_DROP         /* give up on this ACL; the controller should page again */
} wmr_linkwait_action_t;

typedef struct {
    bool     armed;
    bool     encrypt_sent;
    uint32_t link_up_ms;
} wmr_linkwait_t;

/* Start waiting on a newly opened ACL. */
void wmr_linkwait_start(wmr_linkwait_t *wait, uint32_t now_ms);

/* Stop waiting: the HID channel opened, or the link is gone. */
void wmr_linkwait_finish(wmr_linkwait_t *wait);

/* What the host should do next for this ACL. `encrypted` and `hid_open` are the current
 * link state; `hid_open` is true only once the HID channel is open, not while an incoming
 * connection is merely accepted. A wait whose channel opened is finished here.
 * WMR_LINKWAIT_ENCRYPT is returned once per wait; WMR_LINKWAIT_DROP disarms the wait. */
wmr_linkwait_action_t wmr_linkwait_poll(wmr_linkwait_t *wait, bool encrypted, bool hid_open,
                                        uint32_t now_ms);

#endif
