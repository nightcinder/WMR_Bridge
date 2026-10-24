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
 * wmr_psvr2.h - frame predicates for Sony PSVR2 Sense controllers.
 *
 * Header-only so the firmware and the host tests share one definition, and no extra
 * source file is added to the firmware build. The model is identified during discovery
 * (wmr_discovery.h); this file only answers "is this frame PSVR2 input?" and leaves the
 * payload parsing to the phone until a PSVR2 decoder exists. See docs/PSVR2.md.
 */

#ifndef WMR_PSVR2_H
#define WMR_PSVR2_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_config.h"

/* True if `frame` is a PSVR2 input report as delivered by the HID host: A1 header,
 * report ID 0x31, 77 payload bytes (79 total, WMR_PSVR2_INPUT_FRAME_MAX). The length is
 * accepted up to WMR_CFG_MAX_FRAME so a firmware can be built before the exact report
 * length is confirmed on hardware; an exact-length check would be wrong here. */
static inline bool wmr_psvr2_is_input_report(const uint8_t *frame, uint16_t length){
    return frame != NULL && length >= 3 && length <= WMR_CFG_MAX_FRAME &&
           frame[0] == WMR_PSVR2_HID_HEADER && frame[1] == WMR_PSVR2_INPUT_REPORT_ID;
}

#endif /* WMR_PSVR2_H */
