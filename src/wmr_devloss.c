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

/* wmr_devloss.c - see wmr_devloss.h. */

#define BTSTACK_FILE__ "wmr_devloss.c"

#include <stdlib.h>
#include <string.h>
#include "wmr_devloss.h"

void wmr_devloss_reset(wmr_devloss_t *t){
    memset(t, 0, sizeof(*t));
}

static int cmp_u32(const void *a, const void *b){
    uint32_t x = *(const uint32_t *) a, y = *(const uint32_t *) b;
    return (x > y) - (x < y);
}

/* Robust period: median of the deltas that are at most 1.5x the 10th percentile. */
static uint32_t estimate_period(const wmr_devloss_t *t){
    uint32_t sorted[WMR_DEVLOSS_WINDOW];
    unsigned n = t->count;
    memcpy(sorted, t->window, n * sizeof(uint32_t));
    qsort(sorted, n, sizeof(uint32_t), cmp_u32);
    uint32_t ref = sorted[n / 10];
    if (ref == 0) return 0;
    uint32_t limit = ref + ref / 2;
    unsigned m = 0;
    while (m < n && sorted[m] <= limit) m++;
    if (m == 0) return 0;
    if (m & 1) return sorted[m / 2];
    return (uint32_t) (((uint64_t) sorted[m / 2 - 1] + sorted[m / 2]) / 2);
}

static uint32_t implied_missing(uint32_t delta, uint32_t period){
    uint32_t k = (uint32_t) (((uint64_t) delta + period / 2) / period);
    return k > 1 ? k - 1 : 0;
}

void wmr_devloss_add(wmr_devloss_t *t, uint32_t ticks){
    t->reports++;
    if (!t->have_prev){
        t->have_prev = 1;
        t->prev_ticks = ticks;
        return;
    }
    uint32_t delta = ticks - t->prev_ticks;           /* modulo 2^32: correct across a wrap */
    t->prev_ticks = ticks;
    if (delta == 0) return;
    if (delta > WMR_DEVLOSS_DISCONTINUITY_TICKS){
        t->discontinuities++;
        return;
    }
    t->window[t->head] = delta;
    t->head = (uint16_t) ((t->head + 1) % WMR_DEVLOSS_WINDOW);
    if (t->count < WMR_DEVLOSS_WINDOW) t->count++;
    t->since_refresh++;

    if (t->period_ticks == 0){
        if (t->count >= WMR_DEVLOSS_WARMUP){
            t->period_ticks = estimate_period(t);
            t->since_refresh = 0;
            if (t->period_ticks){
                /* Count the deltas collected during warm-up retroactively. */
                for (unsigned i = 0; i < t->count; i++) t->missing += implied_missing(t->window[i], t->period_ticks);
            }
        }
        return;
    }
    t->missing += implied_missing(delta, t->period_ticks);
    if (t->since_refresh >= WMR_DEVLOSS_REFRESH){
        uint32_t p = estimate_period(t);
        if (p) t->period_ticks = p;
        t->since_refresh = 0;
    }
}

uint32_t wmr_devloss_period(const wmr_devloss_t *t){
    return t->period_ticks;
}

uint32_t wmr_devloss_loss_permille(const wmr_devloss_t *t){
    uint64_t total = (uint64_t) t->reports + t->missing;
    if (!total) return 0;
    return (uint32_t) (((uint64_t) t->missing * 1000u + total / 2) / total);
}

int wmr_devloss_ticks_from_frame(const uint8_t *frame, uint16_t length, uint32_t *ticks){
    if (!frame || !ticks || length != 46 || frame[0] != 0xA1 || frame[1] != 0x01) return 0;
    const uint8_t *p = frame + 2 + 28;                 /* payload offset 28, see DECODING.md */
    *ticks = (uint32_t) p[0] | ((uint32_t) p[1] << 8) | ((uint32_t) p[2] << 16) | ((uint32_t) p[3] << 24);
    return 1;
}
