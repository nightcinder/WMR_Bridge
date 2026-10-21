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
 * wmr_decode.c - OG WMR motion controller input report decoder.
 * Layout and scaling follow Monado's wmr_controller_og.c.
 * Portions derived from Monado (https://gitlab.freedesktop.org/monado/monado),
 * src/xrt/drivers/wmr/wmr_controller_og.c and wmr_protocol.h at commit
 * f50c796c32c811f5fd371f77fd8a749d2ad7df08:
 *   Copyright 2018, Philipp Zabel.
 *   Copyright 2020-2021, N Madsen.
 *   Copyright 2020-2023, Collabora, Ltd.
 *   Copyright 2020-2023, Jan Schmidt
 *   SPDX-License-Identifier: BSL-1.0 (Boost Software License 1.0; full text in
 *   docs/DECODING.md). That notice must be kept in all copies and derivative
 *   works of this file.
 */

#define BTSTACK_FILE__ "wmr_decode.c"

#include <string.h>
#include "wmr_decode.h"

/* Payload offsets, counted from the report ID byte (frame[1]) as in Monado,
 * i.e. frame[2 + n]. */

static int32_t read24s(const uint8_t *p){
    uint32_t v = (uint32_t) p[0] | ((uint32_t) p[1] << 8) | ((uint32_t) p[2] << 16);
    if (v & 0x800000u) v |= 0xFF000000u;          /* sign-extend */
    return (int32_t) v;
}

static float clamp_max1(float v){
    return v > 1.0f ? 1.0f : v;
}

bool wmr_decode_frame(const uint8_t *frame, uint16_t length, wmr_controller_state_t *out){
    if (!frame || !out || length != WMR_DECODE_FRAME_LENGTH) return false;
    if (frame[0] != 0xA1 || frame[1] != WMR_DECODE_REPORT_ID) return false;

    const uint8_t *p = frame + 2;
    wmr_controller_state_t s;
    memset(&s, 0, sizeof(s));

    uint8_t buttons = p[0];
    s.thumbstick_click = (buttons & 0x01) != 0;
    s.home             = (buttons & 0x02) != 0;
    s.menu             = (buttons & 0x04) != 0;
    s.squeeze          = (buttons & 0x08) != 0;
    s.trackpad_click   = (buttons & 0x10) != 0;
    s.bt_pairing       = (buttons & 0x20) != 0;
    s.trackpad_touch   = (buttons & 0x40) != 0;

    /* Thumbstick: two 12-bit values packed in 3 bytes. */
    int stick_x = p[1] | ((p[2] & 0x0F) << 8);
    int stick_y = (p[2] >> 4) | (p[3] << 4);
    s.thumbstick_x = clamp_max1((float) (stick_x - 0x07FF) / (float) 0x07FF);
    s.thumbstick_y = clamp_max1((float) (stick_y - 0x07FF) / (float) 0x07FF);

    s.trigger = (float) p[4] / 255.0f;

    /* Trackpad: 0x00..0x64, both 0xFF when untouched. */
    s.trackpad_x = p[5] == 0xFF ? 0.0f : (float) (p[5] - 0x32) / (float) 0x32;
    s.trackpad_y = p[6] == 0xFF ? 0.0f : (float) (p[6] - 0x32) / (float) 0x32;

    s.battery = p[7];

    for (int i = 0; i < 3; i++){
        s.accel_raw[i] = read24s(p + 8 + 3 * i);
        s.accel[i] = (float) s.accel_raw[i] / 49000.0f;
    }
    s.temperature_raw = (int16_t) (uint16_t) (p[17] | (p[18] << 8));
    for (int i = 0; i < 3; i++){
        s.gyro_raw[i] = read24s(p + 19 + 3 * i);
        s.gyro[i] = (float) s.gyro_raw[i] * 0.00001f;
    }
    s.ticks = (uint32_t) p[28] | ((uint32_t) p[29] << 8) |
              ((uint32_t) p[30] << 16) | ((uint32_t) p[31] << 24);

    *out = s;
    return true;
}

uint64_t wmr_decode_extend_ticks(uint64_t *ticks64, uint32_t ticks){
    uint32_t prev = (uint32_t) (*ticks64 & 0xFFFFFFFFu);
    uint64_t high = *ticks64 & ~UINT64_C(0xFFFFFFFF);
    if (ticks < prev) high += UINT64_C(1) << 32;   /* timer wrapped */
    *ticks64 = high | ticks;
    return *ticks64;
}
