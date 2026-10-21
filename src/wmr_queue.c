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

#define BTSTACK_FILE__ "wmr_queue.c"

#include <string.h>
#include "wmr_queue.h"

void wmr_queue_init(wmr_queue_t *q){
    memset(q, 0, sizeof(*q));
}

bool wmr_queue_push(wmr_queue_t *q, const wmr_frame_t *frame){
    if (q->count == WMR_CFG_QUEUE_CAPACITY) return false;
    q->frames[q->tail] = *frame;
    q->tail = (q->tail + 1) % WMR_CFG_QUEUE_CAPACITY;
    q->count++;
    if (q->count > q->high_water) q->high_water = q->count;
    return true;
}

const wmr_frame_t *wmr_queue_peek(const wmr_queue_t *q){
    return q->count ? &q->frames[q->head] : NULL;
}

void wmr_queue_pop(wmr_queue_t *q){
    if (!q->count) return;
    q->head = (q->head + 1) % WMR_CFG_QUEUE_CAPACITY;
    q->count--;
}

void wmr_queue_clear(wmr_queue_t *q, uint32_t purged[WMR_NUM_CONTROLLERS]){
    for (unsigned i = 0, index = q->head; i < q->count; i++){
        if (purged && q->frames[index].hand < WMR_NUM_CONTROLLERS){
            purged[q->frames[index].hand]++;
        }
        index = (index + 1) % WMR_CFG_QUEUE_CAPACITY;
    }
    q->head = q->tail = q->count = 0;
}
