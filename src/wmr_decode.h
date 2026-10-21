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
 * wmr_decode.h - decode a WMR (original/"OG") motion controller input report.
 *
 * Pure C, no BTstack dependency; meant for the receiving side (phone/host)
 * and for host unit tests, not for the ESP32 firmware, which stays a raw pipe.
 *
 * The field layout and scale factors follow Monado's OG WMR controller driver
 * (src/xrt/drivers/wmr/wmr_controller_og.c, BSL-1.0, by N Madsen, Collabora and
 * Jan Schmidt, checked at monado commit f50c796c32c8). Values are NOT
 * calibrated: the per-device sensor mix matrix, bias and orientation come from
 * the controller's firmware config block, which this bridge does not read.
 */

#ifndef WMR_DECODE_H
#define WMR_DECODE_H

#include <stdint.h>
#include <stdbool.h>

#define WMR_DECODE_FRAME_LENGTH   46    /* A1, report ID, 44 payload bytes */
#define WMR_DECODE_REPORT_ID      0x01

typedef struct {
    bool  thumbstick_click;
    bool  home;
    bool  menu;
    bool  squeeze;            /* squeeze click */
    bool  trackpad_click;
    bool  bt_pairing;
    bool  trackpad_touch;

    float trigger;            /* 0..1 */
    float thumbstick_x;       /* -1..1 (12-bit, centre 0x7FF) */
    float thumbstick_y;
    float trackpad_x;         /* -1..1, 0 when not touched */
    float trackpad_y;

    uint8_t battery;          /* raw byte, scale not established */

    /* Raw sensor words (24-bit signed, sign-extended) */
    int32_t accel_raw[3];
    int32_t gyro_raw[3];
    int16_t temperature_raw;

    /* Uncalibrated physical units */
    float accel[3];           /* ~m/s^2: raw / 49000 (Monado: 1 g is about 490000 raw) */
    float gyro[3];            /* rad/s, raw * 1e-5 */

    uint32_t ticks;           /* 32-bit device timer, 100 ns per tick; wraps */
} wmr_controller_state_t;

/* Decode one frame as delivered by the bridge (46 bytes, A1 01 ...).
 * Returns false (and leaves `out` untouched) if the length, transaction
 * header or report ID is wrong. */
bool wmr_decode_frame(const uint8_t *frame, uint16_t length, wmr_controller_state_t *out);

/* Extend a wrapping 32-bit tick counter to 64 bits. Keep `*ticks64` per hand
 * and pass each new `ticks` in arrival order. Returns the 64-bit value. */
uint64_t wmr_decode_extend_ticks(uint64_t *ticks64, uint32_t ticks);

#endif /* WMR_DECODE_H */
