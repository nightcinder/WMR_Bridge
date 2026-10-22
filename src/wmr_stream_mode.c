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

/* wmr_stream_mode.c - FF11/FF12/FF14 mutual exclusion; see wmr_stream_mode.h. */

#define BTSTACK_FILE__ "wmr_stream_mode.c"

#include "wmr_stream_mode.h"

wmr_stream_ccc_result_t wmr_stream_mode_ccc_write(wmr_stream_t current,
                                                  wmr_stream_t which, bool enable){
    wmr_stream_ccc_result_t r = { true, current, 0 };
    if (which != WMR_STREAM_RAW && which != WMR_STREAM_DECODED && which != WMR_STREAM_BATCHED){
        r.accepted = false;
        r.att_error = WMR_ATT_ERROR_OTHER_STREAM_ACTIVE;
        return r;
    }
    if (enable){
        if (current != WMR_STREAM_NONE && current != which){
            r.accepted = false;
            r.att_error = WMR_ATT_ERROR_OTHER_STREAM_ACTIVE;
            return r;
        }
        r.mode = which;
    } else if (current == which){
        r.mode = WMR_STREAM_NONE;
    }
    return r;
}

bool wmr_stream_mode_ccc_enabled(wmr_stream_t current, wmr_stream_t which){
    return current == which && which != WMR_STREAM_NONE;
}

const char *wmr_stream_mode_name(wmr_stream_t mode){
    switch (mode){
        case WMR_STREAM_RAW:     return "raw";
        case WMR_STREAM_DECODED: return "decoded";
        case WMR_STREAM_BATCHED: return "batched";
        default:                 return "none";
    }
}
