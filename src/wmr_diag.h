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

/* Missing-report share over one statistics interval, in permille (0..1000):
 * dm / (dm + dr), where dm is the growth of the missing-report count and dr the growth
 * of the received-report count over the same interval. -1 = unknown: the missing count
 * went down (the tracker was reset by a reconnect) or nothing was expected. */
int wmr_diag_interval_permille(uint32_t prev_missing, uint32_t missing,
                               uint32_t prev_received, uint32_t received);

/* "21.2%" for a permille value, "n/a" for a negative one. */
void wmr_diag_percent_format(int permille, char *buf, size_t len);

/* ---- LINKLOG: one machine-readable line per hand per statistics interval ---- */

/* Printed once before the first LINKLOG line. Scripts filter on the prefix "LINKLOG,". */
#define WMR_LINKLOG_HEADER \
    "LINKLOG_FIELDS,t_ms,hand,rate_rx,dev_missing_delta,dev_missing_cum,last5s_pct,cum_pct," \
    "rssi,lq,fcc,stream,mtu,conn_interval_ms,decimated"

typedef struct {
    uint32_t    t_ms;               /* milliseconds since boot */
    const char *hand;               /* "LEFT" / "RIGHT" */
    int32_t     rate_rx_tenths;     /* reports per second x 10 over the last interval; < 0 = unknown */
    int64_t     missing_delta;      /* growth of dev_missing over the interval; < 0 = unknown */
    uint32_t    missing_cum;        /* dev_missing since the link was created */
    int         last5s_permille;    /* < 0 = unknown */
    int         cum_permille;
    const wmr_link_health_t *health;/* NULL = no Classic link (rssi, lq, fcc left empty) */
    const char *stream;             /* "none" / "raw" / "decoded" */
    uint16_t    mtu;                /* 0 = no phone */
    uint32_t    conn_interval_us;   /* 0 = unknown */
    uint32_t    decimated;          /* cumulative, see WMR_CFG_FF12_DECIMATE */
} wmr_linklog_t;

/* "LINKLOG,12345,LEFT,157.8,1124,17.7,21.2,17.7,-28,227,0,decoded,517,15.00,0". Unknown or
 * unavailable values are left empty (",,"), never "n/a", so a CSV reader sees a missing value. */
void wmr_linklog_format(const wmr_linklog_t *r, char *buf, size_t len);

/* ---- First queue-full drop of each episode -------------------------------- */

typedef struct { bool active; } wmr_episode_t;

/* Returns true only for the first drop since the queue last drained (log one line then). */
bool wmr_episode_note_drop(wmr_episode_t *e);
/* Call with the current queue depth; an empty queue ends the episode. */
void wmr_episode_note_queue(wmr_episode_t *e, unsigned queue_count);

/* "LEFT queue full: first drop of this episode, stream=raw mtu=23 q=256/256 errors=0" */
void wmr_diag_format_drop(char *buf, size_t len, const char *hand_name, const char *stream_name,
                          uint16_t mtu, unsigned queue_count, unsigned queue_capacity, uint32_t errors);

/* ---- LE buffer size and data length (read once, logged, no polling) ---------- */

/* Return parameters of LE Read Buffer Size (v1 or v2; the first four bytes agree):
 * status(1), LE ACL data packet length(2), total number of LE ACL data packets(1). */
bool wmr_diag_parse_le_buffer_size(const uint8_t *ret, uint16_t len, uint16_t *acl_length, uint8_t *acl_count);

/* "HCI LE buffers: ACL packet length 251 bytes, 12 packets" */
void wmr_diag_format_le_buffers(char *buf, size_t len, uint16_t acl_length, uint8_t acl_count);

/* "BLE data length changed: tx 251 octets / 2120 us, rx 251 octets / 2120 us" */
void wmr_diag_format_data_length(char *buf, size_t len, uint16_t tx_octets, uint16_t tx_time_us,
                                 uint16_t rx_octets, uint16_t rx_time_us);

#endif /* WMR_DIAG_H */
