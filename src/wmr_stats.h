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
 * wmr_stats.h - bridge counters. Meanings and limits: docs/COUNTERS.md.
 *
 * All counters are free-running uint32_t that only reset on reboot.
 * "Per hand" arrays are indexed by WMR_HAND_LEFT / WMR_HAND_RIGHT.
 */

#ifndef WMR_STATS_H
#define WMR_STATS_H

#include <stdint.h>
#include "wmr_config.h"

typedef struct {
    uint32_t rx[WMR_NUM_CONTROLLERS];       /* frames received from controller */
    uint32_t tx[WMR_NUM_CONTROLLERS];       /* frames fully handed to BLE */
    uint32_t drop[WMR_NUM_CONTROLLERS];     /* queue full or bad length */
    uint32_t offline[WMR_NUM_CONTROLLERS];  /* no subscribed phone */
    uint32_t purged[WMR_NUM_CONTROLLERS];   /* queued, then discarded on reset */
    uint32_t sequence[WMR_NUM_CONTROLLERS]; /* last sequence number assigned */
    uint32_t mtu_skipped[WMR_NUM_CONTROLLERS];   /* FF12 only: ATT MTU < 59, sample not sent */
    uint32_t decode_errors[WMR_NUM_CONTROLLERS]; /* FF12 only: frame not a valid input report */
    uint32_t notifications;                 /* BLE notifications accepted */
    uint32_t errors;                        /* att_server_notify failures */
} wmr_stats_t;

#endif /* WMR_STATS_H */
