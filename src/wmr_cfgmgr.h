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
 * wmr_cfgmgr.h - FF13 config block manager (BTstack side): per-hand cache, the non-blocking
 * block-read sequence on BTstack timers, and the state the FF13 characteristic serves.
 * The formats and the reader state machine are in wmr_cfg.h (host-tested); this file only
 * connects them to hid_host_send_report and the run loop. Specified in docs/PROTOCOL.md
 * ("Controller config block, FF13"). Compiled but NOT run on hardware.
 */

#ifndef WMR_CFGMGR_H
#define WMR_CFGMGR_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_cfg.h"

/* Called (in the BTstack run loop) when a hand's blob state changes. */
typedef void (*wmr_cfgmgr_listener_t)(unsigned hand);

void wmr_cfgmgr_init(wmr_cfgmgr_listener_t listener);

/* The hand's HID descriptor is available: start reading block 0x02 unless the cache already
 * holds it (READY). The cache survives disconnects. */
void wmr_cfgmgr_on_descriptor(unsigned hand);

/* A config response report (class WMR_CFG_REPORT_CONFIG), as delivered by BTstack
 * (byte 0 is the 0xA1 header). Never counted or forwarded as a sample. */
void wmr_cfgmgr_on_report(unsigned hand, const uint8_t *report, uint16_t length);

/* The hand disconnected: abandon a read in progress (state returns to NOT_READ); READY stays. */
void wmr_cfgmgr_on_closed(unsigned hand);

/* State for FF13. `blob`/`total` are valid only when state is READY. */
wmr_cfg_blob_state_t wmr_cfgmgr_get(unsigned hand, const uint8_t **blob, uint32_t *total);

/* One console line per hand. */
void wmr_cfgmgr_print(void);

#endif /* WMR_CFGMGR_H */
