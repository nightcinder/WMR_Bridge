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
 * wmr_linkdiag.h - diagnostics for reports lost between the controllers and the
 * ESP32 (docs/DECODING.md, "Investigation notes: upstream report loss"):
 *   (a) per-hand missing-report estimate from the controller tick counter,
 *   (b) one log line per Classic link mode change (active / sniff ...),
 *   (c) RSSI, failed-contact counter and link quality per Classic link every
 *       WMR_CFG_LINKDIAG_INTERVAL_MS,
 *   (d) a count of packets BTstack's ESP32 VHCI transport drops silently, seen
 *       through a log sink (no BTstack file is modified).
 *
 * Adds, in total, 3 HCI commands per connected link every 5 s = 1.2 commands/s and as
 * many Command Complete events for two links. None of it has run on hardware.
 */

#ifndef WMR_LINKDIAG_H
#define WMR_LINKDIAG_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_diag.h"

typedef struct {
    bool     connected;            /* Classic ACL link up */
    uint8_t  mode;                 /* WMR_MODE_* */
    wmr_link_health_t health;
    uint32_t reports;              /* input reports seen by the tracker since the link came up */
    uint32_t missing;              /* estimated reports never received (tick deltas) */
    uint32_t discontinuities;
    uint32_t period_ticks;         /* 0 until warmed up */
    uint32_t loss_permille;
} wmr_linkdiag_hand_t;

/* Register the log sink and the timers. Call once from btstack_main. */
void wmr_linkdiag_init(void);

/* HCI events: ACL connect/disconnect, mode change, command complete. */
void wmr_linkdiag_on_hci_event(uint8_t *packet, uint16_t size);

/* A Classic link whose address was not known at connection-complete time (an incoming
 * connection identified by name, docs/DISCOVERY.md) now belongs to `hand`. */
void wmr_linkdiag_link_identified(unsigned hand, uint16_t con_handle);

/* Every report received from a controller, before any queueing. Reads 4 bytes. */
void wmr_linkdiag_note_report(unsigned hand, const uint8_t *report, uint16_t length);

void wmr_linkdiag_get_hand(unsigned hand, wmr_linkdiag_hand_t *out);

/* Packets dropped by the VHCI receive buffer, and other error-level BTstack log messages. */
uint32_t wmr_linkdiag_vhci_drops(void);
uint32_t wmr_linkdiag_log_errors(void);

#endif /* WMR_LINKDIAG_H */
