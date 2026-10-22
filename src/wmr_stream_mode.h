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
 * wmr_stream_mode.h - which BLE stream (raw FF11, decoded FF12 or batched FF14) a phone
 * has subscribed to, and the rule that at most one is active.
 *
 * Pure C, no BTstack dependency, host-tested. Specified in docs/PROTOCOL.md
 * ("Subscription: FF11, FF12 and FF14 are mutually exclusive").
 */

#ifndef WMR_STREAM_MODE_H
#define WMR_STREAM_MODE_H

#include <stdint.h>
#include <stdbool.h>

/* ATT application error returned when enabling a stream while the other is
 * active. */
#define WMR_ATT_ERROR_OTHER_STREAM_ACTIVE 0x80

typedef enum {
    WMR_STREAM_NONE = 0,
    WMR_STREAM_RAW,         /* FF11: raw fragmented frames */
    WMR_STREAM_DECODED,     /* FF12: 56-byte decoded records */
    WMR_STREAM_BATCHED      /* FF14: 2-byte header + 1..9 FF12 records */
} wmr_stream_t;

typedef struct {
    bool         accepted;  /* false: the CCC write must fail with att_error */
    wmr_stream_t mode;      /* the active stream after the write */
    uint8_t      att_error; /* 0 when accepted */
} wmr_stream_ccc_result_t;

/* A phone wrote the CCC of stream `which` (RAW, DECODED or BATCHED) with notifications
 * `enable`d or disabled, while `current` was active.
 *  - enabling the active stream again is accepted (no change);
 *  - enabling a stream while ANOTHER is active is refused with
 *    WMR_ATT_ERROR_OTHER_STREAM_ACTIVE and changes nothing;
 *  - disabling the active stream leaves NONE;
 *  - disabling a stream that is not active is accepted and changes nothing. */
wmr_stream_ccc_result_t wmr_stream_mode_ccc_write(wmr_stream_t current,
                                                  wmr_stream_t which, bool enable);

/* Value to return when the phone reads the CCC of stream `which`. */
bool wmr_stream_mode_ccc_enabled(wmr_stream_t current, wmr_stream_t which);

const char *wmr_stream_mode_name(wmr_stream_t mode);

#endif /* WMR_STREAM_MODE_H */
