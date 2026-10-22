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
 * wmr_diag.h - pure helpers for the link diagnostics (docs/DECODING.md,
 * "Investigation notes: upstream report loss"):
 *   - classify a BTstack log message (to count the silent VHCI receive drop),
 *   - track Classic link mode changes (active / sniff ...) once per change,
 *   - parse and format the replies to Read RSSI / Read Failed Contact Counter /
 *     Read Link Quality.
 *
 * Pure C, no BTstack dependency, host-tested. None of this has run on hardware.
 */

#ifndef WMR_DIAG_H
#define WMR_DIAG_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "wmr_config.h"

/* ---- Log classification (d) -------------------------------------------- */

typedef enum {
    WMR_LOG_IGNORE = 0,        /* not an error message */
    WMR_LOG_VHCI_DROP,         /* hci_transport_esp32_vhci.c: "... -> dropping packet" */
    WMR_LOG_OTHER_ERROR        /* any other error-level message */
} wmr_log_class_t;

/* `format` is the printf format string BTstack passes to the dump sink (the literal,
 * not the formatted text). Matches the message in BTstack's ESP32 VHCI transport:
 * "transport_recv_pkt_cb packet %u, space %u -> dropping packet". If BTstack changes
 * that wording the drop silently stops being counted as VHCI_DROP (it is still counted
 * as OTHER_ERROR); see docs/DECODING.md. */
wmr_log_class_t wmr_diag_classify_log(bool is_error_level, const char *format);

/* ---- Mode changes (b) --------------------------------------------------- */

#define WMR_MODE_ACTIVE 0
#define WMR_MODE_HOLD   1
#define WMR_MODE_SNIFF  2
#define WMR_MODE_PARK   3

typedef struct {
    uint8_t mode[WMR_NUM_CONTROLLERS];
} wmr_mode_tracker_t;

/* A link is assumed active when it is created. */
void wmr_mode_tracker_reset(wmr_mode_tracker_t *t, unsigned hand);

/* Record a Mode Change event. Returns true (and fills *previous) only when the mode
 * differs from the last known one, so a log line is printed once per change. */
bool wmr_mode_tracker_update(wmr_mode_tracker_t *t, unsigned hand, uint8_t mode, uint8_t *previous);

const char *wmr_mode_name(uint8_t mode);

/* "LEFT link mode: active -> sniff (interval 40 slots = 25.00 ms) at t=12.345 s" */
void wmr_mode_format(char *buf, size_t len, const char *hand_name, uint8_t previous,
                     uint8_t mode, uint16_t interval_slots, uint64_t time_us);

/* ---- Link health (c) ----------------------------------------------------- */

typedef enum { WMR_LINKCMD_RSSI = 0, WMR_LINKCMD_FAILED_CONTACT, WMR_LINKCMD_QUALITY, WMR_LINKCMD_COUNT } wmr_linkcmd_t;

/* Commands issued per connected link every WMR_CFG_LINKDIAG_INTERVAL_MS. */
#define WMR_LINKDIAG_COMMANDS_PER_LINK  ((unsigned) WMR_LINKCMD_COUNT)

typedef struct {
    bool     valid[WMR_LINKCMD_COUNT];
    uint8_t  status[WMR_LINKCMD_COUNT];    /* last HCI status; non-zero = the controller refused */
    int8_t   rssi;
    uint16_t failed_contacts;
    uint8_t  quality;
    uint32_t updates;                      /* replies parsed since the link came up */
} wmr_link_health_t;

/* Parse the return parameters of a Command Complete for `kind`:
 * status(1), handle(2), then rssi (int8) / failed contacts (u16 LE) / quality (u8).
 * Returns false if the reply is too short. A non-zero status is recorded in
 * h->status[kind] and h->valid[kind] cleared (the call still returns true). */
bool wmr_diag_parse_link_reply(wmr_linkcmd_t kind, const uint8_t *ret, uint16_t len,
                               uint16_t *handle, wmr_link_health_t *h);

/* "rssi=0 fcc=0 lq=255", with "n/a(0x..)" for anything not available. */
void wmr_link_health_format(const wmr_link_health_t *h, char *buf, size_t len);

#endif /* WMR_DIAG_H */
