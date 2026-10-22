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
 * wmr_queue.h - bounded FIFO of captured controller frames.
 *
 * Pure C. Single context only: all calls come from the BTstack run loop,
 * so there is no locking.
 */

#ifndef WMR_QUEUE_H
#define WMR_QUEUE_H

#include <stdbool.h>
#include "wmr_protocol.h"

typedef struct {
    wmr_frame_t frames[WMR_CFG_QUEUE_CAPACITY];
    unsigned head;
    unsigned tail;
    unsigned count;
    unsigned high_water;   /* peak count since init */
} wmr_queue_t;

void wmr_queue_init(wmr_queue_t *q);

/* Copy `frame` onto the tail. Returns false (and stores nothing) if full. */
bool wmr_queue_push(wmr_queue_t *q, const wmr_frame_t *frame);

/* Oldest frame, or NULL if empty. Valid until the next pop/clear. */
const wmr_frame_t *wmr_queue_peek(const wmr_queue_t *q);

/* The frame `index` places behind the oldest (0 = oldest), or NULL if index >= count. */
const wmr_frame_t *wmr_queue_peek_at(const wmr_queue_t *q, unsigned index);

/* Remove the oldest frame. No-op if empty. */
void wmr_queue_pop(wmr_queue_t *q);

/* Discard everything. Adds the number of frames discarded per hand to
 * purged[] (if non-NULL). */
void wmr_queue_clear(wmr_queue_t *q, uint32_t purged[WMR_NUM_CONTROLLERS]);

#endif /* WMR_QUEUE_H */
