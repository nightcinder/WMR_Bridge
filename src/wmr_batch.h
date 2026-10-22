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
 * wmr_batch.h - FF14 "batched decoded" notification: a 2-byte header followed by
 * 1..N complete FF12 version 1 records, unchanged.
 *
 * Pure C, no BTstack dependency, host-tested. Specified in docs/PROTOCOL.md
 * ("Batched decoded stream, FF14").
 */

#ifndef WMR_BATCH_H
#define WMR_BATCH_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_decoded.h"

#define WMR_BATCH_VERSION      2
#define WMR_BATCH_HEADER_SIZE  2       /* version, count */

/* Smallest ATT MTU that can carry one record: 3 (ATT opcode and handle) + 2 + 56. Below it
 * nothing is sent on FF14. */
#define WMR_BATCH_MIN_MTU      (WMR_ATT_NOTIFY_OVERHEAD + WMR_BATCH_HEADER_SIZE + WMR_DECODED_SIZE)

/* Records per notification never exceed this, whatever the MTU: (517 - 3 - 2) / 56 = 9, and
 * 517 is the largest ATT MTU. */
#define WMR_BATCH_MAX_COUNT    9
#define WMR_BATCH_BUFFER_SIZE  (WMR_BATCH_HEADER_SIZE + WMR_BATCH_MAX_COUNT * WMR_DECODED_SIZE)

bool wmr_batch_mtu_ok(uint16_t att_mtu);

/* Records that fit one notification at this MTU: (MTU - 3 - 2) / 56, capped at
 * WMR_BATCH_MAX_COUNT; 0 below WMR_BATCH_MIN_MTU. */
unsigned wmr_batch_max_count(uint16_t att_mtu);

typedef struct {
    uint8_t  data[WMR_BATCH_BUFFER_SIZE];
    unsigned count;
    unsigned capacity;      /* records this batch may hold */
} wmr_batch_t;

/* Empty batch able to hold `capacity` records (clamped to WMR_BATCH_MAX_COUNT). */
void wmr_batch_init(wmr_batch_t *b, unsigned capacity);

/* Append one 56-byte FF12 record. Returns false (and stores nothing) if the batch is full. */
bool wmr_batch_add(wmr_batch_t *b, const uint8_t record[WMR_DECODED_SIZE]);

/* Bytes to send: 2 + 56 x count. */
uint16_t wmr_batch_size(const wmr_batch_t *b);

/* Receiver side: check a notification value and find its records. Returns false unless
 * size >= 2 + 56, version == 2, count >= 1 and size == 2 + 56 x count. */
bool wmr_batch_parse(const uint8_t *packet, uint16_t size, unsigned *count, const uint8_t **records);

/* ---- When to send ---------------------------------------------------------- */

typedef enum {
    WMR_BATCH_FLUSH_NO = 0,     /* keep collecting */
    WMR_BATCH_FLUSH_FULL,       /* enough queued records to fill a notification */
    WMR_BATCH_FLUSH_TIMEOUT     /* the oldest record has waited the hold time */
} wmr_batch_flush_t;

/* How long the oldest queued record may wait before a partial batch is sent: one connection
 * interval, or WMR_CFG_BATCH_HOLD_DEFAULT_US while the interval is unknown (0). The bound
 * documented is this plus one controller report period (about 5 ms), which covers the
 * next arrival check. */
uint32_t wmr_batch_hold_us(uint32_t conn_interval_us);

/* Flush when `queued` records fill a notification (`max_count` > 0), or when the oldest
 * has waited `hold_us`. A zero `max_count` (MTU too small) flushes at once so the pump
 * can discard what can never be sent. Nothing queued never flushes. */
wmr_batch_flush_t wmr_batch_flush_reason(unsigned queued, unsigned max_count,
                                         uint32_t oldest_age_us, uint32_t hold_us);

/* Milliseconds (rounded up, at least 1) until the oldest record reaches `hold_us`. */
uint32_t wmr_batch_timer_ms(uint32_t oldest_age_us, uint32_t hold_us);

#endif /* WMR_BATCH_H */
