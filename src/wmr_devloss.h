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
 * wmr_devloss.h - estimate how many controller reports never reached the ESP32,
 * from the controller's own 32-bit tick counter.
 *
 * The bridge numbers a report only when it receives it, so loss between the
 * controller and the ESP32 leaves no gap in the sequence numbers; the tick
 * counter (100 ns per tick) does show it. Same definition as the analyzer
 * (tools/analyze_capture.py, device_loss): each delta between consecutive
 * reports implies round(delta / period) - 1 missing reports, where `period` is the
 * median of the deltas not longer than 1.5x the 10th-percentile delta, so heavy
 * loss does not inflate it.
 *
 * Differences from the offline analyzer, because this runs online with bounded memory:
 *  - the period is estimated from a sliding window of the last WMR_DEVLOSS_WINDOW
 *    deltas (so it cannot drift with the run length) and refreshed every
 *    WMR_DEVLOSS_REFRESH reports;
 *  - nothing is counted until WMR_DEVLOSS_WARMUP deltas exist; the deltas seen so far
 *    are then counted retroactively;
 *  - a delta longer than WMR_DEVLOSS_DISCONTINUITY_TICKS (5 s) is treated as a
 *    discontinuity (controller restart, long link outage), counted separately and not
 *    added to `missing`;
 *  - a zero delta (repeated tick value) is ignored.
 * It cannot see perfectly uniform loss (every delta the same multiple of the real
 * period), which is indistinguishable from a slower controller.
 *
 * Pure C, no BTstack dependency, host-tested. Not run on hardware.
 */

#ifndef WMR_DEVLOSS_H
#define WMR_DEVLOSS_H

#include <stdint.h>

#define WMR_DEVLOSS_WINDOW               256
#define WMR_DEVLOSS_WARMUP               16
#define WMR_DEVLOSS_REFRESH              32
#define WMR_DEVLOSS_DISCONTINUITY_TICKS  50000000u     /* 5 s at 100 ns per tick */

typedef struct {
    uint32_t window[WMR_DEVLOSS_WINDOW];   /* last deltas, ring buffer */
    uint16_t count;                        /* valid entries in window */
    uint16_t head;                         /* next slot to overwrite */
    uint16_t since_refresh;
    uint32_t prev_ticks;
    uint8_t  have_prev;
    uint32_t period_ticks;                 /* 0 until estimated */
    uint32_t reports;                      /* reports seen since reset */
    uint32_t missing;                      /* estimated reports never received */
    uint32_t discontinuities;
} wmr_devloss_t;

void wmr_devloss_reset(wmr_devloss_t *t);

/* Feed the device tick counter of one received input report, in arrival order. */
void wmr_devloss_add(wmr_devloss_t *t, uint32_t ticks);

/* Estimated controller report period in ticks (0 until warmed up). */
uint32_t wmr_devloss_period(const wmr_devloss_t *t);

/* Percentage of expected reports that were missing, x10 (e.g. 463 = 46.3 %). */
uint32_t wmr_devloss_loss_permille(const wmr_devloss_t *t);

/* Pull the tick counter out of a bridge frame (A1, report ID 01, payload). Returns 0
 * if the frame is not a valid 46-byte input report. */
int wmr_devloss_ticks_from_frame(const uint8_t *frame, uint16_t length, uint32_t *ticks);

#endif /* WMR_DEVLOSS_H */
