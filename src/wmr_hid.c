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
 * wmr_hid.c - see wmr_hid.h. The report descriptor and the mapping rules are
 * copied from the companion package so both sides agree byte for byte; the
 * shared vectors in tests/vectors/hid_gamepad.txt keep them honest.
 */

#define BTSTACK_FILE__ "wmr_hid.c"

#include <math.h>
#include <string.h>
#include "wmr_decode.h"
#include "wmr_decoded.h"
#include "wmr_hid.h"

/* Button bit positions in the HID report, which are also the Button usage
 * numbers the host sees (bit 0 is usage 1). Android maps a gamepad's Button
 * usages onto Linux BTN_A.. in order (see Generic.kl): A, B, C, X, Y, Z, L1,
 * R1, L2, R2, Select, Start, Mode, ThumbL, ThumbR. C and Z have no WMR
 * control but still occupy their slots, so X..R3 sit two bits later than the
 * companion's GamepadButton order. */
enum {
    GP_A = 0,
    GP_B,
    GP_C,
    GP_X,
    GP_Y,
    GP_Z,
    GP_L1,
    GP_R1,
    GP_L2,
    GP_R2,
    GP_SELECT,
    GP_START,
    GP_GUIDE,
    GP_L3,
    GP_R3,
    GP_DPAD_UP,
    GP_DPAD_DOWN,
    GP_DPAD_LEFT,
    GP_DPAD_RIGHT,
};

#define GP_MASK_REPORT  0x7FFFu   /* bits 0..14 are the 15 used HID buttons */
#define GP_DIRECTIONAL  ((1u << GP_DPAD_UP) | (1u << GP_DPAD_DOWN) | \
                         (1u << GP_DPAD_LEFT) | (1u << GP_DPAD_RIGHT))

static const uint8_t hid_report_descriptor[] = {
    0x05, 0x01,        /* Usage Page (Generic Desktop) */
    0x09, 0x05,        /* Usage (Game Pad) */
    0xA1, 0x01,        /* Collection (Application) */
    0x85, WMR_HID_REPORT_ID, /* Report ID */
    /* 16 buttons */
    0x05, 0x09,        /*   Usage Page (Button) */
    0x19, 0x01,        /*   Usage Minimum (1) */
    0x29, 0x10,        /*   Usage Maximum (16) */
    0x15, 0x00,        /*   Logical Minimum (0) */
    0x25, 0x01,        /*   Logical Maximum (1) */
    0x75, 0x01,        /*   Report Size (1) */
    0x95, 0x10,        /*   Report Count (16) */
    0x81, 0x02,        /*   Input (Data, Variable, Absolute) */
    /* Hat switch, 4 bits, null state outside 0..7 */
    0x05, 0x01,        /*   Usage Page (Generic Desktop) */
    0x09, 0x39,        /*   Usage (Hat switch) */
    0x15, 0x00,        /*   Logical Minimum (0) */
    0x25, 0x07,        /*   Logical Maximum (7) */
    0x35, 0x00,        /*   Physical Minimum (0) */
    0x46, 0x3B, 0x01,  /*   Physical Maximum (315) */
    0x65, 0x14,        /*   Unit (degrees) */
    0x75, 0x04,        /*   Report Size (4) */
    0x95, 0x01,        /*   Report Count (1) */
    0x81, 0x42,        /*   Input (Data, Variable, Absolute, Null state) */
    0x65, 0x00,        /*   Unit (none) */
    0x75, 0x04,        /*   Report Size (4) */
    0x95, 0x01,        /*   Report Count (1) */
    0x81, 0x03,        /*   Input (Constant): padding */
    /* Sticks: X, Y, Z, Rz */
    0x05, 0x01,        /*   Usage Page (Generic Desktop) */
    0x09, 0x30,        /*   Usage (X) */
    0x09, 0x31,        /*   Usage (Y) */
    0x09, 0x32,        /*   Usage (Z) */
    0x09, 0x35,        /*   Usage (Rz) */
    0x15, 0x81,        /*   Logical Minimum (-127) */
    0x25, 0x7F,        /*   Logical Maximum (127) */
    0x75, 0x08,        /*   Report Size (8) */
    0x95, 0x04,        /*   Report Count (4) */
    0x81, 0x02,        /*   Input (Data, Variable, Absolute) */
    /* Triggers: Brake, Accelerator */
    0x05, 0x02,        /*   Usage Page (Simulation Controls) */
    0x09, 0xC5,        /*   Usage (Brake) */
    0x09, 0xC4,        /*   Usage (Accelerator) */
    0x15, 0x00,        /*   Logical Minimum (0) */
    0x26, 0xFF, 0x00,  /*   Logical Maximum (255) */
    0x75, 0x08,        /*   Report Size (8) */
    0x95, 0x02,        /*   Report Count (2) */
    0x81, 0x02,        /*   Input (Data, Variable, Absolute) */
    0xC0,              /* End Collection */
};

const uint8_t *wmr_hid_report_descriptor(unsigned *size){
    if (size) *size = (unsigned) sizeof(hid_report_descriptor);
    return hid_report_descriptor;
}

static const wmr_hid_config_t default_config = {
    .trigger_full_scale = 255.0f,
    .trigger_dead_zone = 0.05f,
    .trigger_press_threshold = 0.55f,
    .trigger_release_threshold = 0.45f,
    .trackpad_centre_radius = 0.3f,
    .left_trackpad = WMR_HID_TRACKPAD_DPAD,
    .right_trackpad = WMR_HID_TRACKPAD_FACE,
    .invert_stick_y = false,
    .invert_trackpad_y = false,
    .tilt_target = WMR_HID_TILT_NONE,
    .tilt_hand = 1,
    .tilt_max_degrees = 35.0f,
    .tilt_dead_zone_degrees = 3.0f,
    .tilt_invert_x = false,
    .tilt_invert_y = false,
};

const wmr_hid_config_t *wmr_hid_default_config(void){
    return &default_config;
}

static float finite_or_zero(float v){
    return isfinite(v) ? v : 0.0f;
}

static float clampf(float v, float lo, float hi){
    v = finite_or_zero(v);
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/* Raw trigger to 0..1: scale, remove the dead zone, rescale so full is 1. */
static float trigger_value(uint8_t raw, const wmr_hid_config_t *config){
    const float full = config->trigger_full_scale > 1.0f ? config->trigger_full_scale : 1.0f;
    const float v = clampf((float) raw / full, 0.0f, 1.0f);
    const float dz = clampf(config->trigger_dead_zone, 0.0f, 0.95f);
    if (v <= dz) return 0.0f;
    return clampf((v - dz) / (1.0f - dz), 0.0f, 1.0f);
}

/* Schmitt trigger: see triggerPressed() in the companion's gamepad.dart. */
static bool trigger_pressed(float value, const wmr_hid_config_t *config, bool was_pressed){
    if (!(value > 0.0f)) return false;
    const float press = clampf(config->trigger_press_threshold, 0.0f, 1.0f);
    const float release = clampf(config->trigger_release_threshold, 0.0f, press);
    return was_pressed ? value > release : value >= press;
}

/* sin(22.5 deg): a component above this share of the radius presses its
 * direction, which gives 8 equal sectors. */
static const float diagonal = 0.38268343236508984f;

static uint32_t trackpad_buttons(float x, float y, uint8_t role, float centre_radius){
    if (!isfinite(x) || !isfinite(y)) return 0;
    const float r = sqrtf(x * x + y * y);
    if (r < centre_radius || r == 0.0f) return 0;
    switch (role){
        case WMR_HID_TRACKPAD_DPAD: {
            uint32_t out = 0;
            if (y / r > diagonal) out |= 1u << GP_DPAD_UP;
            if (y / r < -diagonal) out |= 1u << GP_DPAD_DOWN;
            if (x / r > diagonal) out |= 1u << GP_DPAD_RIGHT;
            if (x / r < -diagonal) out |= 1u << GP_DPAD_LEFT;
            return out;
        }
        case WMR_HID_TRACKPAD_FACE:
            if (fabsf(x) > fabsf(y))
                return 1u << (x > 0.0f ? GP_B : GP_X);
            return 1u << (y > 0.0f ? GP_Y : GP_A);
        default:
            return 0;
    }
}

static float tilt_axis(float degrees, const wmr_hid_config_t *config){
    if (!isfinite(degrees)) return 0.0f;
    const float dz = config->tilt_dead_zone_degrees > 0.0f
                         ? config->tilt_dead_zone_degrees : 0.0f;
    const float span = config->tilt_max_degrees - dz;
    if (span <= 0.0f) return 0.0f;
    const float a = fabsf(degrees);
    if (a <= dz) return 0.0f;
    const float v = (a - dz) / span;
    return (degrees < 0.0f ? -1.0f : 1.0f) * (v > 1.0f ? 1.0f : v);
}

static void tilt_stick(float roll_deg, float pitch_deg,
                       const wmr_hid_config_t *config, float out[2]){
    /* Leaning right is a negative rotation about Z (toward the user); tipping
     * away is a negative rotation about X (right). */
    float x = tilt_axis(-roll_deg, config);
    float y = tilt_axis(-pitch_deg, config);
    if (config->tilt_invert_x) x = -x;
    if (config->tilt_invert_y) y = -y;
    out[0] = x;
    out[1] = y;
}

static float larger(float a, float b){
    return fabsf(a) >= fabsf(b) ? a : b;
}

static uint8_t hat(bool up, bool down, bool left, bool right){
    const int h = (right ? 1 : 0) - (left ? 1 : 0);
    const int v = (up ? 1 : 0) - (down ? 1 : 0);
    switch (h * 3 + v){
        case 0 * 3 + 1: return 0;
        case 1 * 3 + 1: return 1;
        case 1 * 3 + 0: return 2;
        case 1 * 3 - 1: return 3;
        case 0 * 3 - 1: return 4;
        case -1 * 3 - 1: return 5;
        case -1 * 3 + 0: return 6;
        case -1 * 3 + 1: return 7;
        default: return WMR_HID_HAT_CENTRED;
    }
}

void wmr_hid_map(const wmr_hid_hand_t *left, const wmr_hid_hand_t *right,
                 const wmr_hid_config_t *config, wmr_hid_history_t *previous,
                 wmr_hid_report_t *out){
    if (!config) config = &default_config;
    if (!out) return;
    memset(out, 0, sizeof(*out));

    const wmr_hid_hand_t *hands[2] = { left, right };
    uint32_t bits = 0;
    float sticks[2][2] = {{0.0f, 0.0f}, {0.0f, 0.0f}};
    float triggers[2] = {0.0f, 0.0f};

    for (unsigned h = 0; h < 2; h++){
        const wmr_hid_hand_t *input = hands[h];
        if (!input || !input->connected) continue;
        const bool is_left = (h == 0);
        if (input->buttons & WMR_BTN_SQUEEZE) bits |= 1u << (is_left ? GP_L1 : GP_R1);
        if (input->buttons & WMR_BTN_THUMBSTICK_CLICK) bits |= 1u << (is_left ? GP_L3 : GP_R3);
        if (input->buttons & WMR_BTN_MENU) bits |= 1u << (is_left ? GP_SELECT : GP_START);
        if (input->buttons & WMR_BTN_HOME) bits |= 1u << GP_GUIDE;

        const float t = trigger_value(input->trigger, config);
        triggers[h] = t;
        const bool was = previous ? (is_left ? previous->l2 : previous->r2) : false;
        if (trigger_pressed(t, config, was)) bits |= 1u << (is_left ? GP_L2 : GP_R2);

        const float sy = config->invert_stick_y ? -finite_or_zero(input->stick[1]) : finite_or_zero(input->stick[1]);
        sticks[h][0] = clampf(input->stick[0], -1.0f, 1.0f);
        sticks[h][1] = clampf(sy, -1.0f, 1.0f);

        if ((input->buttons & WMR_BTN_TRACKPAD_CLICK) &&
            (input->buttons & WMR_BTN_TRACKPAD_TOUCH)){
            const float ty = config->invert_trackpad_y
                                 ? -finite_or_zero(input->trackpad[1]) : finite_or_zero(input->trackpad[1]);
            bits |= trackpad_buttons(finite_or_zero(input->trackpad[0]), ty,
                                     is_left ? config->left_trackpad : config->right_trackpad,
                                     config->trackpad_centre_radius);
        }
    }

    if (config->tilt_target != WMR_HID_TILT_NONE){
        const unsigned hand = config->tilt_hand < 2 ? config->tilt_hand : 1;
        const wmr_hid_hand_t *input = hands[hand];
        if (input && input->connected && input->has_tilt){
            float v[2];
            tilt_stick(input->roll_deg, input->pitch_deg, config, v);
            const unsigned i = (config->tilt_target == WMR_HID_TILT_LEFT_STICK) ? 0 : 1;
            sticks[i][0] = larger(sticks[i][0], v[0]);
            sticks[i][1] = larger(sticks[i][1], v[1]);
        }
    }

    out->buttons = (uint16_t) (bits & GP_MASK_REPORT);
    out->hat = hat((bits & (1u << GP_DPAD_UP)) != 0, (bits & (1u << GP_DPAD_DOWN)) != 0,
                   (bits & (1u << GP_DPAD_LEFT)) != 0, (bits & (1u << GP_DPAD_RIGHT)) != 0);

    /* The model has y positive up and HID has Y positive down. */
    const float axes[4] = { sticks[0][0], -sticks[0][1], sticks[1][0], -sticks[1][1] };
    int8_t *encoded = &out->x;
    for (unsigned i = 0; i < 4; i++){
        const float scaled = clampf(axes[i], -1.0f, 1.0f) * 127.0f;
        encoded[i] = (int8_t) (scaled >= 0.0f
                                   ? (int) (scaled + 0.5f)
                                   : (int) (scaled - 0.5f));
    }
    out->brake = (uint8_t) (triggers[0] * 255.0f + 0.5f);
    out->accelerator = (uint8_t) (triggers[1] * 255.0f + 0.5f);

    if (previous){
        previous->l2 = (bits & (1u << GP_L2)) != 0;
        previous->r2 = (bits & (1u << GP_R2)) != 0;
    }
}

void wmr_hid_pack(const wmr_hid_report_t *report, uint8_t out[WMR_HID_REPORT_SIZE]){
    out[0] = (uint8_t) (report->buttons & 0xFFu);
    out[1] = (uint8_t) ((report->buttons >> 8) & 0xFFu);
    out[2] = (uint8_t) (report->hat & 0x0Fu);
    out[3] = (uint8_t) report->x;
    out[4] = (uint8_t) report->y;
    out[5] = (uint8_t) report->z;
    out[6] = (uint8_t) report->rz;
    out[7] = report->brake;
    out[8] = report->accelerator;
}

void wmr_hid_build_report(const wmr_hid_hand_t *left, const wmr_hid_hand_t *right,
                          const wmr_hid_config_t *config, wmr_hid_history_t *previous,
                          uint8_t out[WMR_HID_REPORT_SIZE]){
    wmr_hid_report_t report;
    wmr_hid_map(left, right, config, previous, &report);
    wmr_hid_pack(&report, out);
}

/* ---- Firmware state ---------------------------------------------------- */

void wmr_hid_reset(wmr_hid_state_t *state){
    if (!state) return;
    memset(state, 0, sizeof(*state));
    state->dirty = true;
    state->force = true;
}

bool wmr_hid_note_report(wmr_hid_state_t *state, uint8_t hand,
                         const uint8_t *report, uint16_t length, uint64_t now_us){
    if (!state || !report || hand >= 2) return false;
    wmr_controller_state_t controller;
    if (!wmr_decode_frame(report, length, &controller)) return false;

    wmr_decoded_t decoded;
    wmr_decoded_from_state(&decoded, &controller, hand, 0, now_us);

    wmr_hid_hand_t *input = &state->hands[hand];
    input->connected = true;
    input->buttons = decoded.buttons;
    input->stick[0] = clampf((float) decoded.stick[0] / 32767.0f, -1.0f, 1.0f);
    input->stick[1] = clampf((float) decoded.stick[1] / 32767.0f, -1.0f, 1.0f);
    input->trigger = decoded.trigger;
    input->trackpad[0] = clampf((float) decoded.trackpad[0] / 32767.0f, -1.0f, 1.0f);
    input->trackpad[1] = clampf((float) decoded.trackpad[1] / 32767.0f, -1.0f, 1.0f);
    /* No orientation fusion in the firmware: tilt stays in the app. */
    input->has_tilt = false;
    input->roll_deg = 0.0f;
    input->pitch_deg = 0.0f;

    state->last_sample_us[hand] = now_us;
    state->dirty = true;
    return true;
}

bool wmr_hid_expire(wmr_hid_state_t *state, uint64_t now_us){
    if (!state) return false;
    bool released = false;
    for (unsigned hand = 0; hand < 2; hand++){
        if (!state->hands[hand].connected) continue;
        if (now_us <= state->last_sample_us[hand]) continue;
        if (now_us - state->last_sample_us[hand] <= WMR_HID_STALE_US) continue;
        state->hands[hand].connected = false;
        state->dirty = true;
        released = true;
    }
    return released;
}

bool wmr_hid_report_due(const wmr_hid_state_t *state, uint64_t now_us){
    if (!state) return false;
    if (state->force) return true;
    if (!state->dirty) return false;
    if (now_us > state->last_send_us &&
        now_us - state->last_send_us < WMR_HID_MIN_INTERVAL_US) return false;
    return true;
}

bool wmr_hid_take_report(wmr_hid_state_t *state, uint64_t now_us,
                         uint8_t out[WMR_HID_REPORT_SIZE]){
    if (!state || !out || !wmr_hid_report_due(state, now_us)) return false;

    uint8_t report[WMR_HID_REPORT_SIZE];
    wmr_hid_build_report(&state->hands[0], &state->hands[1],
                         &default_config, &state->history, report);

    state->dirty = false;
    if (!state->force && state->have_last_report &&
        memcmp(report, state->last_report, sizeof(report)) == 0){
        return false;
    }
    memcpy(state->last_report, report, sizeof(report));
    memcpy(out, report, sizeof(report));
    state->have_last_report = true;
    state->force = false;
    state->last_send_us = now_us;
    return true;
}
