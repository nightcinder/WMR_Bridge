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

/* wmr_decoded.c - pack/unpack of the FF12 decoded-frame record. */

#define BTSTACK_FILE__ "wmr_decoded.c"

#include <string.h>
#include "wmr_decoded.h"
#include "wmr_controller_driver.h"
#include "wmr_psvr2.h"
#include "wmr_switch.h"

static void put16(uint8_t *p, uint16_t v){
    p[0] = (uint8_t) (v & 0xFF);
    p[1] = (uint8_t) (v >> 8);
}

static void put32(uint8_t *p, uint32_t v){
    for (unsigned i = 0; i < 4; i++) p[i] = (uint8_t) (v >> (8 * i));
}

static void put64(uint8_t *p, uint64_t v){
    for (unsigned i = 0; i < 8; i++) p[i] = (uint8_t) (v >> (8 * i));
}

static void putf(uint8_t *p, float f){
    uint32_t v;
    memcpy(&v, &f, sizeof(v));          /* IEEE 754 binary32 */
    put32(p, v);
}

static uint16_t get16(const uint8_t *p){
    return (uint16_t) (p[0] | (p[1] << 8));
}

static uint32_t get32(const uint8_t *p){
    return (uint32_t) p[0] | ((uint32_t) p[1] << 8) | ((uint32_t) p[2] << 16) | ((uint32_t) p[3] << 24);
}

static uint64_t get64(const uint8_t *p){
    return (uint64_t) get32(p) | ((uint64_t) get32(p + 4) << 32);
}

static float getf(const uint8_t *p){
    uint32_t v = get32(p);
    float f;
    memcpy(&f, &v, sizeof(f));
    return f;
}

static int16_t to_q15(float v){
    if (v > 1.0f) v = 1.0f;
    if (v < -1.0f) v = -1.0f;
    float scaled = v * 32767.0f;
    return (int16_t) (scaled >= 0.0f ? scaled + 0.5f : scaled - 0.5f);
}

void wmr_decoded_from_state(wmr_decoded_t *out, const wmr_controller_state_t *s,
                            uint8_t hand, uint32_t sequence, uint64_t rx_time_us){
    memset(out, 0, sizeof(*out));
    out->hand = hand;
    out->sequence = sequence;
    out->rx_time_us = rx_time_us;
    out->device_ticks = s->ticks;
    out->buttons = (uint8_t) ((s->thumbstick_click ? WMR_BTN_THUMBSTICK_CLICK : 0) |
                              (s->home ? WMR_BTN_HOME : 0) |
                              (s->menu ? WMR_BTN_MENU : 0) |
                              (s->squeeze ? WMR_BTN_SQUEEZE : 0) |
                              (s->trackpad_click ? WMR_BTN_TRACKPAD_CLICK : 0) |
                              (s->bt_pairing ? WMR_BTN_BT_PAIRING : 0) |
                              (s->trackpad_touch ? WMR_BTN_TRACKPAD_TOUCH : 0));
    for (unsigned i = 0; i < 3; i++){
        out->gyro[i] = s->gyro[i];
        out->accel[i] = s->accel[i];
    }
    out->temperature_raw = s->temperature_raw;
    out->stick[0] = to_q15(s->thumbstick_x);
    out->stick[1] = to_q15(s->thumbstick_y);
    out->trackpad[0] = to_q15(s->trackpad_x);
    out->trackpad[1] = to_q15(s->trackpad_y);
    out->trigger = (uint8_t) (s->trigger * 255.0f + 0.5f);
    out->battery = s->battery;
}

void wmr_decoded_pack(const wmr_decoded_t *d, uint8_t out[WMR_DECODED_SIZE]){
    out[0] = WMR_DECODED_VERSION;
    out[1] = d->hand;
    out[2] = d->flags;
    out[3] = d->buttons;
    put32(out + 4, d->sequence);
    put64(out + 8, d->rx_time_us);
    put32(out + 16, d->device_ticks);
    for (unsigned i = 0; i < 3; i++){
        putf(out + 20 + 4 * i, d->gyro[i]);
        putf(out + 32 + 4 * i, d->accel[i]);
    }
    put16(out + 44, (uint16_t) d->temperature_raw);
    put16(out + 46, (uint16_t) d->stick[0]);
    put16(out + 48, (uint16_t) d->stick[1]);
    put16(out + 50, (uint16_t) d->trackpad[0]);
    put16(out + 52, (uint16_t) d->trackpad[1]);
    out[54] = d->trigger;
    out[55] = d->battery;
}

bool wmr_decoded_unpack(const uint8_t *p, uint16_t size, wmr_decoded_t *out){
    if (!p || !out || size != WMR_DECODED_SIZE) return false;
    if (p[0] != WMR_DECODED_VERSION || p[1] >= 2) return false;
    out->hand = p[1];
    out->flags = p[2];
    out->buttons = p[3];
    out->sequence = get32(p + 4);
    out->rx_time_us = get64(p + 8);
    out->device_ticks = get32(p + 16);
    for (unsigned i = 0; i < 3; i++){
        out->gyro[i] = getf(p + 20 + 4 * i);
        out->accel[i] = getf(p + 32 + 4 * i);
    }
    out->temperature_raw = (int16_t) get16(p + 44);
    out->stick[0] = (int16_t) get16(p + 46);
    out->stick[1] = (int16_t) get16(p + 48);
    out->trackpad[0] = (int16_t) get16(p + 50);
    out->trackpad[1] = (int16_t) get16(p + 52);
    out->trigger = p[54];
    out->battery = p[55];
    return true;
}

bool wmr_decoded_mtu_ok(uint16_t att_mtu){
    return att_mtu >= WMR_DECODED_MIN_MTU;
}

bool wmr_decoded_decimate_keep(uint32_t sequence, unsigned n){
    return n <= 1 || (sequence % n) == 0;
}

bool wmr_decoded_from_frame(const wmr_frame_t *frame, uint8_t out[WMR_DECODED_SIZE]){
    if (!frame || !out || frame->hand >= 2) return false;
    const wmr_controller_driver_t *driver =
        wmr_controller_driver_for_frame(frame->data, frame->length);
    if (!driver || !driver->decode) return false;
    return driver->decode(frame, out);
}

unsigned wmr_decoded_samples_from_frame(const wmr_frame_t *frame,
                                        uint8_t out[][WMR_DECODED_SIZE], unsigned max){
    if (!frame || !out || max == 0 || frame->hand >= 2) return 0;
    const wmr_controller_driver_t *driver =
        wmr_controller_driver_for_frame(frame->data, frame->length);
    if (!driver) return 0;
    if (driver->decode_samples) return driver->decode_samples(frame, out, max);
    if (!driver->decode) return 0;
    return driver->decode(frame, out[0]) ? 1 : 0;
}

bool wmr_decoded_from_switch_frame(const wmr_frame_t *frame, uint8_t out[WMR_DECODED_SIZE]){
    uint8_t records[WMR_DECODED_MAX_SAMPLES][WMR_DECODED_SIZE];
    unsigned count = wmr_switch_decode_samples(frame, records, WMR_DECODED_MAX_SAMPLES);
    if (!count) return false;
    memcpy(out, records[count - 1], WMR_DECODED_SIZE);   /* newest sample */
    return true;
}

/* ---- PSVR2 0x31 input report -------------------------------------------- */

/* Offsets are into the frame as captured (frame[0] = 0xA1, frame[1] = report
 * ID), so one is added to Monado's pssense_input_report offsets. The public
 * layout and scales follow Monado's pssense driver:
 *   src/xrt/drivers/pssense/pssense_driver.c (PSSENSE_GYRO_SCALE_DEG,
 *   PSSENSE_ACCEL_SCALE). See docs/PSVR2.md. */
#define PSVR2_OFF_STICK_X       3
#define PSVR2_OFF_STICK_Y       4
#define PSVR2_OFF_TRIGGER       5
#define PSVR2_OFF_BUTTONS       10      /* 3 bytes */
#define PSVR2_OFF_GYRO          18      /* 3 x i16 LE */
#define PSVR2_OFF_ACCEL         24      /* 3 x i16 LE */
#define PSVR2_OFF_IMU_TICKS     30      /* u32 LE */
#define PSVR2_OFF_TEMPERATURE   34
#define PSVR2_OFF_BATTERY       44      /* raw: high nibble charge state, low nibble level */

/* 180/1024 degrees per raw count, converted to rad/s: pi/1024. */
static const float psvr2_gyro_scale = 3.14159265358979323846f / 1024.0f;
/* +-32768 counts covers +-8 g. */
static const float psvr2_accel_scale = 9.80665f / 4096.0f;
/* Monado treats one IMU tick as 333 ns; device_ticks is in 100 ns units. */
static uint32_t psvr2_ticks_to_100ns(uint32_t ticks){
    return (uint32_t) (((uint64_t) ticks * 333u) / 100u);
}

static int16_t get_s16(const uint8_t *p){
    return (int16_t) get16(p);
}

bool wmr_decoded_from_psvr2_frame(const wmr_frame_t *frame, uint8_t out[WMR_DECODED_SIZE]){
    if (!frame || !out || frame->hand >= 2 ||
        frame->length != WMR_PSVR2_INPUT_FRAME_MAX) return false;
    if (!wmr_psvr2_is_input_report(frame->data, frame->length)) return false;

    const uint8_t *f = frame->data;
    wmr_decoded_t record;
    memset(&record, 0, sizeof(record));
    record.hand = frame->hand;
    record.sequence = frame->sequence;
    record.rx_time_us = frame->rx_time_us;
    record.device_ticks = psvr2_ticks_to_100ns(get32(f + PSVR2_OFF_IMU_TICKS));
    for (unsigned i = 0; i < 3; i++){
        record.gyro[i] = (float) get_s16(f + PSVR2_OFF_GYRO + 2 * i) * psvr2_gyro_scale;
        record.accel[i] = (float) get_s16(f + PSVR2_OFF_ACCEL + 2 * i) * psvr2_accel_scale;
    }
    record.temperature_raw = (int16_t) f[PSVR2_OFF_TEMPERATURE];
    record.stick[0] = to_q15(((float) f[PSVR2_OFF_STICK_X] - 128.0f) / 128.0f);
    record.stick[1] = to_q15((128.0f - (float) f[PSVR2_OFF_STICK_Y]) / 128.0f);
    record.trigger = f[PSVR2_OFF_TRIGGER];
    record.battery = f[PSVR2_OFF_BATTERY];

    /* The WMR button byte cannot carry every PSVR2 face button or capacitive
     * touch. Map the shared controls; the rest stay clear. */
    uint8_t b0 = f[PSVR2_OFF_BUTTONS];
    uint8_t b1 = f[PSVR2_OFF_BUTTONS + 1];
    if (b1 & 0x10) record.buttons |= WMR_BTN_HOME;                       /* PS */
    if (b1 & 0x03) record.buttons |= WMR_BTN_MENU;                       /* Share/Options */
    if (b0 & (frame->hand == WMR_HAND_LEFT ? 0x10 : 0x20))
        record.buttons |= WMR_BTN_SQUEEZE;                               /* L1/R1 grip */
    if (b1 & (frame->hand == WMR_HAND_LEFT ? 0x04 : 0x08))
        record.buttons |= WMR_BTN_THUMBSTICK_CLICK;                      /* L3/R3 */

    wmr_decoded_pack(&record, out);
    return true;
}

bool wmr_decoded_from_wmr_frame(const wmr_frame_t *frame, uint8_t out[WMR_DECODED_SIZE]){
    wmr_controller_state_t state;
    wmr_decoded_t record;
    if (!frame || !out || frame->hand >= 2) return false;
    if (!wmr_decode_frame(frame->data, frame->length, &state)) return false;
    wmr_decoded_from_state(&record, &state, frame->hand, frame->sequence, frame->rx_time_us);
    wmr_decoded_pack(&record, out);
    return true;
}
