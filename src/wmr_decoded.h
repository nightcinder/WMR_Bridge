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
 * wmr_decoded.h - "decoded frame" record carried on characteristic FF12.
 *
 * Pure C, no BTstack dependency. This is the reference implementation of the
 * format in docs/PROTOCOL.md ("Decoded stream, FF12"). It is NOT wired into the
 * firmware yet: the spec is awaiting review.
 */

#ifndef WMR_DECODED_H
#define WMR_DECODED_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_decode.h"
#include "wmr_protocol.h"

#define WMR_DECODED_VERSION   1
#define WMR_DECODED_SIZE      56      /* bytes per notification value */

/* flags */
#define WMR_DECODED_FLAG_CALIBRATED   0x01   /* sensor values have calibration applied */

/* buttons (same bit positions as the controller report) */
#define WMR_BTN_THUMBSTICK_CLICK  0x01
#define WMR_BTN_HOME              0x02
#define WMR_BTN_MENU              0x04
#define WMR_BTN_SQUEEZE           0x08
#define WMR_BTN_TRACKPAD_CLICK    0x10
#define WMR_BTN_BT_PAIRING        0x20
#define WMR_BTN_TRACKPAD_TOUCH    0x40

typedef struct {
    uint8_t  hand;              /* 0 LEFT, 1 RIGHT */
    uint8_t  flags;
    uint8_t  buttons;
    uint32_t sequence;          /* same per-hand number as the raw FF11 frame */
    uint64_t rx_time_us;        /* ESP32 esp_timer at report receipt, us since boot */
    uint32_t device_ticks;      /* controller timer, 100 ns/tick, wraps at 2^32 */
    float    gyro[3];           /* rad/s */
    float    accel[3];          /* m/s^2 */
    int16_t  temperature_raw;
    int16_t  stick[2];          /* x, y: -32767..32767 = -1..1 */
    int16_t  trackpad[2];       /* x, y: -32767..32767 = -1..1, 0 when untouched */
    uint8_t  trigger;           /* 0..255 */
    uint8_t  battery;           /* raw */
} wmr_decoded_t;

/* Fill `out` from a decoded controller state (no calibration applied: the
 * CALIBRATED flag is left clear). */
void wmr_decoded_from_state(wmr_decoded_t *out, const wmr_controller_state_t *s,
                            uint8_t hand, uint32_t sequence, uint64_t rx_time_us);

/* Serialize to exactly WMR_DECODED_SIZE bytes, little-endian. */
void wmr_decoded_pack(const wmr_decoded_t *d, uint8_t out[WMR_DECODED_SIZE]);

/* Parse a notification value. Returns false unless size == WMR_DECODED_SIZE,
 * version == WMR_DECODED_VERSION and hand < 2. */
bool wmr_decoded_unpack(const uint8_t *packet, uint16_t size, wmr_decoded_t *out);

/* Smallest ATT MTU that can carry one record (record size + 3 bytes of ATT
 * opcode and handle). Below it nothing is sent on FF12. */
#define WMR_DECODED_MIN_MTU   (WMR_DECODED_SIZE + 3)

bool wmr_decoded_mtu_ok(uint16_t att_mtu);

/* Diagnostic decimation (WMR_CFG_FF12_DECIMATE): true if the report with this per-hand
 * sequence number is to be sent. n <= 1 keeps every report; otherwise only the reports
 * whose sequence is a multiple of n. */
bool wmr_decoded_decimate_keep(uint32_t sequence, unsigned n);

/* Decode a queued raw frame into a packed FF12 record. Uses frame->hand,
 * frame->sequence and frame->rx_time_us. Returns false (out untouched) if the
 * frame is not a valid 46-byte input report. The CALIBRATED flag stays 0. */
bool wmr_decoded_from_frame(const wmr_frame_t *frame, uint8_t out[WMR_DECODED_SIZE]);

#endif /* WMR_DECODED_H */
