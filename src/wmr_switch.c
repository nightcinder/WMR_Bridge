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

/* wmr_switch.c - Switch Joy-Con/Pro subcommand handshake, calibration and 0x30 decoder. */

#include <math.h>
#include <string.h>
#include "wmr_switch.h"

#define SWITCH_PI 3.14159265358979323846f

/* [00 01 40 40] twice: 320 Hz / 160 Hz, amplitude 0. The same bytes are safe on both
 * motors and on a Pro Controller. */
static const uint8_t switch_neutral_rumble[8] = {
    0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40
};

uint16_t wmr_switch_build_subcommand(uint8_t counter, uint8_t subcommand,
                                     const uint8_t *args, uint8_t args_len,
                                     uint8_t out[WMR_SWITCH_OUTPUT_FRAME_SIZE]){
    if (!out) return 0;
    if (args_len > WMR_SWITCH_OUTPUT_FRAME_SIZE - 11) return 0;
    memset(out, 0, WMR_SWITCH_OUTPUT_FRAME_SIZE);
    out[0] = WMR_SWITCH_OUTPUT_REPORT_ID;
    out[1] = counter;
    memcpy(&out[2], switch_neutral_rumble, sizeof(switch_neutral_rumble));
    out[10] = subcommand;
    if (args_len) memcpy(&out[11], args, args_len);
    return WMR_SWITCH_OUTPUT_FRAME_SIZE;
}

/* ---- Handshake ---------------------------------------------------------- */

void wmr_switch_handshake_init(wmr_switch_handshake_t *hs){
    if (!hs) return;
    memset(hs, 0, sizeof(*hs));
    hs->step = WMR_SWITCH_HS_IMU;
}

bool wmr_switch_handshake_ready(const wmr_switch_handshake_t *hs){
    return hs && hs->step == WMR_SWITCH_HS_DONE;
}

bool wmr_switch_handshake_failed(const wmr_switch_handshake_t *hs){
    return hs && hs->step == WMR_SWITCH_HS_FAILED;
}

void wmr_switch_handshake_abort(wmr_switch_handshake_t *hs){
    if (!hs) return;
    memset(hs, 0, sizeof(*hs));
    hs->step = WMR_SWITCH_HS_IDLE;
}

static uint8_t step_subcommand(wmr_switch_hs_step_t step){
    switch (step){
        case WMR_SWITCH_HS_IMU:       return WMR_SWITCH_SUBCMD_ENABLE_IMU;
        case WMR_SWITCH_HS_CAL_IMU:
        case WMR_SWITCH_HS_CAL_STICK: return WMR_SWITCH_SUBCMD_SPI_READ;
        case WMR_SWITCH_HS_MODE:      return WMR_SWITCH_SUBCMD_INPUT_MODE;
        default:                      return 0;
    }
}

bool wmr_switch_handshake_next(wmr_switch_handshake_t *hs,
                               uint8_t out[WMR_SWITCH_OUTPUT_FRAME_SIZE], uint16_t *out_len){
    if (!hs || !out || !out_len) return false;
    *out_len = 0;
    if (hs->waiting) return false;
    if (hs->step == WMR_SWITCH_HS_IDLE || hs->step >= WMR_SWITCH_HS_DONE) return false;

    uint8_t args[4];
    uint8_t args_len = 0;
    switch (hs->step){
        case WMR_SWITCH_HS_IMU:
            args[0] = 0x01;
            args_len = 1;
            break;
        case WMR_SWITCH_HS_CAL_IMU:
            args[0] = (uint8_t) (WMR_SWITCH_CAL_IMU_ADDR & 0xFF);
            args[1] = (uint8_t) (WMR_SWITCH_CAL_IMU_ADDR >> 8);
            args[2] = WMR_SWITCH_CAL_IMU_LEN;
            args_len = 3;
            break;
        case WMR_SWITCH_HS_CAL_STICK:
            args[0] = (uint8_t) (WMR_SWITCH_CAL_STICK_ADDR & 0xFF);
            args[1] = (uint8_t) (WMR_SWITCH_CAL_STICK_ADDR >> 8);
            args[2] = WMR_SWITCH_CAL_STICK_LEN;
            args_len = 3;
            break;
        case WMR_SWITCH_HS_MODE:
            args[0] = WMR_SWITCH_INPUT_MODE_FULL;
            args_len = 1;
            break;
        default:
            return false;
    }

    uint16_t len = wmr_switch_build_subcommand(hs->counter, step_subcommand(hs->step),
                                               args, args_len, out);
    if (!len){
        hs->step = WMR_SWITCH_HS_FAILED;
        return false;
    }
    hs->counter = (uint8_t) (hs->counter + 1);
    hs->tries = (uint8_t) (hs->tries + 1);
    hs->waiting = true;
    hs->waited_ms = 0;
    *out_len = len;
    return true;
}

static void retry_current(wmr_switch_handshake_t *hs){
    hs->waited_ms = 0;
    if (hs->tries >= WMR_SWITCH_HS_MAX_TRIES){
        hs->step = WMR_SWITCH_HS_FAILED;
        hs->waiting = false;
        return;
    }
    hs->waiting = false;
}

static void advance(wmr_switch_handshake_t *hs){
    switch (hs->step){
        case WMR_SWITCH_HS_IMU:       hs->step = WMR_SWITCH_HS_CAL_IMU; break;
        case WMR_SWITCH_HS_CAL_IMU:   hs->step = WMR_SWITCH_HS_CAL_STICK; break;
        case WMR_SWITCH_HS_CAL_STICK: hs->step = WMR_SWITCH_HS_MODE; break;
        case WMR_SWITCH_HS_MODE:      hs->step = WMR_SWITCH_HS_DONE; break;
        default:                      return;
    }
    hs->waiting = false;
    hs->tries = 0;
    hs->waited_ms = 0;
}

bool wmr_switch_handshake_on_reply(wmr_switch_handshake_t *hs,
                                   const uint8_t *frame, uint16_t length){
    if (!hs || !frame) return false;
    if (!wmr_switch_is_reply_report(frame, length)) return false;
    if (hs->step == WMR_SWITCH_HS_IDLE || hs->step >= WMR_SWITCH_HS_DONE) return false;
    if (!hs->waiting) return false;

    uint8_t ack = frame[WMR_SWITCH_REPLY_OFF_ACK];
    uint8_t subcommand = frame[WMR_SWITCH_REPLY_OFF_SUBCMD];
    if (subcommand != step_subcommand(hs->step)) return true;   /* another handshake's reply */
    if (!(ack & 0x80)){                                         /* NACK */
        retry_current(hs);
        return true;
    }

    if (hs->step == WMR_SWITCH_HS_CAL_IMU || hs->step == WMR_SWITCH_HS_CAL_STICK){
        uint8_t want = hs->step == WMR_SWITCH_HS_CAL_IMU ? WMR_SWITCH_CAL_IMU_LEN
                                                         : WMR_SWITCH_CAL_STICK_LEN;
        unsigned offset = hs->step == WMR_SWITCH_HS_CAL_IMU ? WMR_SWITCH_CAL_IMU_OFFSET
                                                            : WMR_SWITCH_CAL_STICK_OFFSET;
        if (length < (uint16_t) (WMR_SWITCH_REPLY_OFF_DATA + want)){
            retry_current(hs);
            return true;
        }
        memcpy(&hs->blob[offset], &frame[WMR_SWITCH_REPLY_OFF_DATA], want);
    }
    advance(hs);
    return true;
}

void wmr_switch_handshake_tick(wmr_switch_handshake_t *hs, uint16_t elapsed_ms){
    if (!hs || !hs->waiting) return;
    if (hs->step == WMR_SWITCH_HS_IDLE || hs->step >= WMR_SWITCH_HS_DONE) return;
    if (hs->waited_ms < WMR_SWITCH_HS_REPLY_TIMEOUT_MS){
        hs->waited_ms = (uint16_t) (hs->waited_ms + elapsed_ms);
        if (hs->waited_ms < WMR_SWITCH_HS_REPLY_TIMEOUT_MS) return;
    }
    hs->waited_ms = 0;
    retry_current(hs);
}

const uint8_t *wmr_switch_handshake_blob(const wmr_switch_handshake_t *hs, uint32_t *total){
    if (total) *total = 0;
    if (!hs || hs->step != WMR_SWITCH_HS_DONE) return NULL;
    if (total) *total = WMR_SWITCH_CAL_SIZE;
    return hs->blob;
}

/* ---- Calibration -------------------------------------------------------- */

static int16_t get_s16(const uint8_t *p){
    return (int16_t) (uint16_t) (p[0] | (p[1] << 8));
}

/* Decode one 3-byte group into the packed 12-bit X and Y pair. */
static void stick_group(const uint8_t *p, uint16_t out[2]){
    out[0] = (uint16_t) (p[0] | ((p[1] & 0x0F) << 8));
    out[1] = (uint16_t) ((p[1] >> 4) | (p[2] << 4));
}

static bool stick_span_ok(uint16_t min, uint16_t center, uint16_t max){
    return max > center && center > min && max - min >= 64;
}

void wmr_switch_calibration_parse(const uint8_t imu[WMR_SWITCH_CAL_IMU_LEN],
                                  const uint8_t stick[WMR_SWITCH_CAL_STICK_LEN],
                                  wmr_switch_calibration_t *out){
    if (!out) return;
    memset(out, 0, sizeof(*out));
    if (!imu || !stick) return;

    bool imu_ok = true;
    for (unsigned axis = 0; axis < 3; axis++){
        out->acc_origin[axis]  = get_s16(&imu[2 * axis]);
        out->acc_coeff[axis]   = get_s16(&imu[6 + 2 * axis]);
        out->gyro_origin[axis] = get_s16(&imu[12 + 2 * axis]);
        out->gyro_coeff[axis]  = get_s16(&imu[18 + 2 * axis]);
        if (out->acc_coeff[axis] - out->acc_origin[axis] <= 0) imu_ok = false;
        if (out->gyro_coeff[axis] - out->gyro_origin[axis] <= 0) imu_ok = false;
    }
    out->valid = imu_ok;

    uint16_t group[6][2];
    for (unsigned g = 0; g < 6; g++) stick_group(&stick[3 * g], group[g]);

    /* Left: max, center, min. Right: center, min, max (spi_flash_notes.md). */
    for (unsigned axis = 0; axis < 2; axis++){
        out->stick_max[WMR_HAND_LEFT][axis]    = group[0][axis];
        out->stick_center[WMR_HAND_LEFT][axis] = group[1][axis];
        out->stick_min[WMR_HAND_LEFT][axis]    = group[2][axis];
        out->stick_center[WMR_HAND_RIGHT][axis] = group[3][axis];
        out->stick_min[WMR_HAND_RIGHT][axis]    = group[4][axis];
        out->stick_max[WMR_HAND_RIGHT][axis]    = group[5][axis];
    }
    for (unsigned hand = 0; hand < WMR_NUM_CONTROLLERS; hand++){
        bool ok = true;
        for (unsigned axis = 0; axis < 2; axis++){
            if (!stick_span_ok(out->stick_min[hand][axis], out->stick_center[hand][axis],
                               out->stick_max[hand][axis])) ok = false;
        }
        out->stick_valid[hand] = ok;
    }
}

void wmr_switch_calibration_pack(const uint8_t imu[WMR_SWITCH_CAL_IMU_LEN],
                                 const uint8_t stick[WMR_SWITCH_CAL_STICK_LEN],
                                 uint8_t out[WMR_SWITCH_CAL_SIZE]){
    if (!out) return;
    if (imu) memcpy(&out[WMR_SWITCH_CAL_IMU_OFFSET], imu, WMR_SWITCH_CAL_IMU_LEN);
    else memset(&out[WMR_SWITCH_CAL_IMU_OFFSET], 0, WMR_SWITCH_CAL_IMU_LEN);
    if (stick) memcpy(&out[WMR_SWITCH_CAL_STICK_OFFSET], stick, WMR_SWITCH_CAL_STICK_LEN);
    else memset(&out[WMR_SWITCH_CAL_STICK_OFFSET], 0, WMR_SWITCH_CAL_STICK_LEN);
}

static wmr_switch_calibration_t switch_calibration[WMR_NUM_CONTROLLERS];

void wmr_switch_set_calibration(unsigned hand, const wmr_switch_calibration_t *cal){
    if (hand >= WMR_NUM_CONTROLLERS) return;
    if (!cal){
        memset(&switch_calibration[hand], 0, sizeof(switch_calibration[hand]));
        return;
    }
    switch_calibration[hand] = *cal;
}

const wmr_switch_calibration_t *wmr_switch_get_calibration(unsigned hand){
    return hand < WMR_NUM_CONTROLLERS ? &switch_calibration[hand] : NULL;
}

/* ---- Input report 0x30 -------------------------------------------------- */

bool wmr_switch_is_input_report(const uint8_t *frame, uint16_t length){
    return frame != NULL && length == WMR_SWITCH_INPUT_FRAME_SIZE &&
           frame[0] == WMR_SWITCH_HID_HEADER && frame[1] == WMR_SWITCH_INPUT_REPORT_ID;
}

bool wmr_switch_is_reply_report(const uint8_t *frame, uint16_t length){
    return frame != NULL && length >= WMR_SWITCH_REPLY_MIN_SIZE &&
           frame[0] == WMR_SWITCH_HID_HEADER && frame[1] == WMR_SWITCH_REPLY_REPORT_ID;
}

static float stick_axis(uint16_t raw, uint16_t center, uint16_t min, uint16_t max){
    float value;
    if (raw >= center) value = max > center ? (float) (raw - center) / (float) (max - center) : 0.0f;
    else               value = center > min ? -((float) (center - raw) / (float) (center - min)) : 0.0f;
    if (value > 1.0f) value = 1.0f;
    if (value < -1.0f) value = -1.0f;
    return value;
}

static void decode_stick(const uint8_t *p, unsigned hand, const wmr_switch_calibration_t *cal,
                         float *x, float *y){
    uint16_t raw_x = (uint16_t) (p[0] | ((p[1] & 0x0F) << 8));
    uint16_t raw_y = (uint16_t) ((p[1] >> 4) | (p[2] << 4));
    uint16_t center[2] = { 2048, 2048 };
    uint16_t min[2] = { 0, 0 };
    uint16_t max[2] = { 4095, 4095 };

    if (cal && cal->stick_valid[hand]){
        for (unsigned axis = 0; axis < 2; axis++){
            center[axis] = cal->stick_center[hand][axis];
            min[axis] = cal->stick_min[hand][axis];
            max[axis] = cal->stick_max[hand][axis];
        }
    }
    *x = stick_axis(raw_x, center[0], min[0], max[0]);
    *y = stick_axis(raw_y, center[1], min[1], max[1]);
}

static float decode_accel(int16_t raw, unsigned axis, const wmr_switch_calibration_t *cal){
    float g;
    if (cal && cal->valid){
        float span = (float) cal->acc_coeff[axis] - (float) cal->acc_origin[axis];
        if (span > 0.0f) g = (float) raw * (4.0f / span);
        else             g = (float) raw * 0.000244f;
    } else {
        g = (float) raw * 0.000244f;
    }
    return g * 9.80665f;
}

static float decode_gyro(int16_t raw, unsigned axis, const wmr_switch_calibration_t *cal){
    float dps;
    if (cal && cal->valid){
        float span = (float) cal->gyro_coeff[axis] - (float) cal->gyro_origin[axis];
        if (span > 0.0f) dps = (float) (raw - cal->gyro_origin[axis]) * (816.0f / span);
        else             dps = (float) raw * 0.06103f;
    } else {
        dps = (float) raw * 0.06103f;
    }
    return dps * (SWITCH_PI / 180.0f);
}

static void decode_sample(const wmr_frame_t *frame, unsigned hand, unsigned index,
                          const wmr_switch_calibration_t *cal,
                          const wmr_controller_state_t *base, uint8_t out[WMR_DECODED_SIZE]){
    wmr_controller_state_t state = *base;
    const uint8_t *imu = &frame->data[WMR_SWITCH_OFF_IMU + 12 * index];
    for (unsigned axis = 0; axis < 3; axis++){
        state.accel[axis] = decode_accel(get_s16(&imu[2 * axis]), axis, cal);
        state.gyro[axis] = decode_gyro(get_s16(&imu[6 + 2 * axis]), axis, cal);
    }
    uint64_t back = (uint64_t) (WMR_SWITCH_SAMPLES_PER_REPORT - 1 - index) *
                    WMR_SWITCH_SAMPLE_PERIOD_US;
    uint64_t rx_time = frame->rx_time_us > back ? frame->rx_time_us - back : 0;
    wmr_decoded_t record;
    wmr_decoded_from_state(&record, &state, (uint8_t) hand, frame->sequence, rx_time);
    if (cal && cal->valid) record.flags |= WMR_DECODED_FLAG_CALIBRATED;
    wmr_decoded_pack(&record, out);
}

unsigned wmr_switch_decode_samples(const wmr_frame_t *frame,
                                   uint8_t out[][WMR_DECODED_SIZE], unsigned max){
    if (!frame || !out || max == 0) return 0;
    if (frame->hand >= WMR_NUM_CONTROLLERS) return 0;
    if (!wmr_switch_is_input_report(frame->data, frame->length)) return 0;

    unsigned hand = frame->hand;
    const wmr_switch_calibration_t *cal = &switch_calibration[hand];
    uint8_t b3 = frame->data[WMR_SWITCH_OFF_BUTTONS];
    uint8_t b4 = frame->data[WMR_SWITCH_OFF_BUTTONS + 1];
    uint8_t b5 = frame->data[WMR_SWITCH_OFF_BUTTONS + 2];

    wmr_controller_state_t base;
    memset(&base, 0, sizeof(base));
    base.home             = (b4 & WMR_SWITCH_BTN_HOME) != 0;
    base.menu             = (b4 & (WMR_SWITCH_BTN_MINUS | WMR_SWITCH_BTN_PLUS)) != 0;
    base.thumbstick_click = (b4 & (WMR_SWITCH_BTN_L_STICK | WMR_SWITCH_BTN_R_STICK)) != 0;
    base.trackpad_click   = (b4 & WMR_SWITCH_BTN_CAPTURE) != 0;
    base.squeeze          = ((b5 & (WMR_SWITCH_BTN_L_ZL | WMR_SWITCH_BTN_L_L)) != 0) ||
                            ((b3 & (WMR_SWITCH_BTN_R_ZR | WMR_SWITCH_BTN_R_R)) != 0);
    base.trigger          = base.squeeze ? 1.0f : 0.0f;
    base.battery          = (uint8_t) (frame->data[WMR_SWITCH_OFF_BATTERY] >> 4);

    /* A Joy-Con only has its own stick. A Pro Controller pairs as LEFT and carries both,
     * so LEFT exposes the right stick as the trackpad pair. */
    const uint8_t *stick = hand == WMR_HAND_LEFT ? &frame->data[WMR_SWITCH_OFF_LEFT_STICK]
                                                 : &frame->data[WMR_SWITCH_OFF_RIGHT_STICK];
    decode_stick(stick, hand, cal, &base.thumbstick_x, &base.thumbstick_y);
    if (hand == WMR_HAND_LEFT){
        /* A Joy-Con (L) has no right stick and leaves those bytes zero; do not turn that
         * into a stuck trackpad. A Pro Controller's right stick is never all zero at rest. */
        const uint8_t *right = &frame->data[WMR_SWITCH_OFF_RIGHT_STICK];
        if ((right[0] | right[1] | right[2]) != 0)
            decode_stick(right, WMR_HAND_RIGHT, cal,
                         &base.trackpad_x, &base.trackpad_y);
    }

    unsigned count = WMR_SWITCH_SAMPLES_PER_REPORT;
    if (count > max) count = max;
    for (unsigned i = 0; i < count; i++)
        decode_sample(frame, hand, i, cal, &base, out[i]);
    return count;
}
