#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "wmr_decode.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

static int near(float a, float b, float eps){ return fabsf(a - b) <= eps; }

static void fill(uint8_t *f){
    memset(f, 0, WMR_DECODE_FRAME_LENGTH);
    f[0] = 0xA1;
    f[1] = 0x01;
    f[2 + 5] = 0xFF;     /* trackpad untouched */
    f[2 + 6] = 0xFF;
}

static void test_synthetic(void){
    printf("synthetic frames\n");
    uint8_t f[WMR_DECODE_FRAME_LENGTH];
    wmr_controller_state_t s;

    fill(f);
    f[2] = 0x7F;                           /* all seven button bits */
    f[2 + 1] = 0xFF; f[2 + 2] = 0xF7;      /* x = 0x7FF, y low nibble 0xF */
    f[2 + 3] = 0x7F;                       /* y = 0xF | 0x7F<<4 = 0x7FF */
    f[2 + 4] = 0xFF;                       /* trigger full */
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.thumbstick_click && s.home && s.menu && s.squeeze);
    CHECK(s.trackpad_click && s.bt_pairing && s.trackpad_touch);
    CHECK(near(s.thumbstick_x, 0.0f, 1e-6f) && near(s.thumbstick_y, 0.0f, 1e-6f));
    CHECK(near(s.trigger, 1.0f, 1e-6f));
    CHECK(s.trackpad_x == 0.0f && s.trackpad_y == 0.0f);

    fill(f);
    f[2 + 1] = 0x00; f[2 + 2] = 0x00; f[2 + 3] = 0x00;   /* stick at minimum */
    f[2 + 5] = 0x64; f[2 + 6] = 0x00;                    /* trackpad extremes */
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.thumbstick_x, -1.0f, 1e-6f) && near(s.thumbstick_y, -1.0f, 1e-6f));
    CHECK(near(s.trackpad_x, 1.0f, 1e-6f) && near(s.trackpad_y, -1.0f, 1e-6f));

    fill(f);
    f[2 + 1] = 0xFF; f[2 + 2] = 0xFF; f[2 + 3] = 0xFF;   /* 0xFFF clamps to 1 */
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.thumbstick_x == 1.0f && s.thumbstick_y == 1.0f);

    /* sign extension of 24-bit sensor words, temperature, ticks */
    fill(f);
    f[2 + 8] = 0xFF; f[2 + 9] = 0xFF; f[2 + 10] = 0xFF;      /* accel x = -1 */
    f[2 + 17] = 0x00; f[2 + 18] = 0x80;                      /* temp = -32768 */
    f[2 + 19] = 0x00; f[2 + 20] = 0x00; f[2 + 21] = 0x80;    /* gyro x = -8388608 */
    f[2 + 28] = 0x78; f[2 + 29] = 0x56; f[2 + 30] = 0x34; f[2 + 31] = 0x12;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[0] == -1 && s.temperature_raw == -32768);
    CHECK(s.gyro_raw[0] == -8388608);
    CHECK(s.ticks == 0x12345678u);
}

/* Monado deep-dive conversion tests (commits f50c796c and aa635fa7) */

static void test_battery_temperature(void){
    printf("battery and temperature conversions\n");
    uint8_t f[WMR_DECODE_FRAME_LENGTH];
    wmr_controller_state_t s;

    /* Battery byte is passed through raw from offset 7 (Monado wmr_controller_og.c:295). */
    const uint8_t test_batt_vals[] = {0, 1, 100, 128, 131, 133, 156, 255};
    for (size_t i = 0; i < sizeof(test_batt_vals); i++){
        fill(f);
        f[2 + 7] = test_batt_vals[i];
        CHECK(wmr_decode_frame(f, sizeof(f), &s));
        CHECK(s.battery == test_batt_vals[i]);
    }

    /* Temperature word is little-endian signed 16-bit (Monado wmr_controller_og.c:314,
     * wmr_protocol.h:130-137 read16). */
    struct {
        uint8_t lo;
        uint8_t hi;
        int16_t expected;
    } temp_cases[] = {
        {0x00, 0x00, 0},
        {0x01, 0x00, 1},
        {0xFF, 0xFF, -1},
        {0xFE, 0xFF, -2},
        {0x00, 0x80, -32768},                       /* min s16 */
        {0xFF, 0x7F, 32767},                        /* max s16 */
        {0x81, 0x05, 1409},                         /* sessionA-0 transient capture */
        {0xBF, 0x0B, 3007},                         /* sessionA-1 capture */
        {0xD3, 0x0B, 3027},                         /* sessionA-2 capture */
        {0xDA, 0x0B, 3034},                         /* sessionA-3 capture */
        {0xE0, 0x0B, 3040},                         /* sessionA-4 capture */
        {0xD7, 0x0B, 3031},                         /* sessionB-0 capture */
    };
    for (size_t i = 0; i < sizeof(temp_cases) / sizeof(temp_cases[0]); i++){
        fill(f);
        f[2 + 17] = temp_cases[i].lo;
        f[2 + 18] = temp_cases[i].hi;
        CHECK(wmr_decode_frame(f, sizeof(f), &s));
        CHECK(s.temperature_raw == temp_cases[i].expected);
    }
}

static void test_accel_scale(void){
    printf("accelerometer scale conversions (Monado 49000 divisor)\n");
    uint8_t f[WMR_DECODE_FRAME_LENGTH];
    wmr_controller_state_t s;

    /* Monado wmr_controller_og.c:226-234 divides signed 24-bit raw acceleration by
     * 49000 (98000 / 2) based on Reverb G1 1g ~ 490,000 raw counts observation.
     * 490,000 raw counts maps to 10.0 m/s^2, while 480,690 raw counts would be 9.81 m/s^2. */

    /* Zero acceleration */
    fill(f);
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[0] == 0 && s.accel_raw[1] == 0 && s.accel_raw[2] == 0);
    CHECK(s.accel[0] == 0.0f && s.accel[1] == 0.0f && s.accel[2] == 0.0f);

    /* +49000 raw -> exactly 1.0 m/s^2 (49000 = 0x00BF68: 68 BF 00) */
    fill(f);
    f[2 + 8] = 0x68; f[2 + 9] = 0xBF; f[2 + 10] = 0x00;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[0] == 49000);
    CHECK(near(s.accel[0], 1.0f, 1e-6f));

    /* -49000 raw -> exactly -1.0 m/s^2 (-49000 = 0xFF4098 in 24-bit: 98 40 FF) */
    fill(f);
    f[2 + 8] = 0x98; f[2 + 9] = 0x40; f[2 + 10] = 0xFF;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[0] == -49000);
    CHECK(near(s.accel[0], -1.0f, 1e-6f));

    /* +490000 raw (Monado 1g observation) -> 10.0 m/s^2 (490000 = 0x077A10: 10 7A 07) */
    fill(f);
    f[2 + 8] = 0x10; f[2 + 9] = 0x7A; f[2 + 10] = 0x07;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[0] == 490000);
    CHECK(near(s.accel[0], 10.0f, 1e-6f));

    /* -490000 raw -> -10.0 m/s^2 (-490000 = 0xF885F0 in 24-bit: F0 85 F8) */
    fill(f);
    f[2 + 8] = 0xF0; f[2 + 9] = 0x85; f[2 + 10] = 0xF8;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[0] == -490000);
    CHECK(near(s.accel[0], -10.0f, 1e-6f));

    /* 480690 raw (nominal 9.81 m/s^2 on Monado scale: 480690 = 0x0755B2: B2 55 07) */
    fill(f);
    f[2 + 11] = 0xB2; f[2 + 12] = 0x55; f[2 + 13] = 0x07;     /* y axis */
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[1] == 480690);
    CHECK(near(s.accel[1], 480690.0f / 49000.0f, 1e-6f));
    CHECK(near(s.accel[1], 9.81f, 0.001f));

    /* Extreme 24-bit bounds */
    /* Max positive 24-bit: 0x7FFFFF = 8388607 (FF FF 7F) */
    fill(f);
    f[2 + 14] = 0xFF; f[2 + 15] = 0xFF; f[2 + 16] = 0x7F;     /* z axis */
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[2] == 8388607);
    CHECK(near(s.accel[2], 8388607.0f / 49000.0f, 1e-5f));

    /* Min negative 24-bit: 0x800000 = -8388608 (00 00 80) */
    fill(f);
    f[2 + 14] = 0x00; f[2 + 15] = 0x00; f[2 + 16] = 0x80;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.accel_raw[2] == -8388608);
    CHECK(near(s.accel[2], -8388608.0f / 49000.0f, 1e-5f));
}

static void test_trigger_trackpad_ranges(void){
    printf("trigger and trackpad input ranges and conversions\n");
    uint8_t f[WMR_DECODE_FRAME_LENGTH];
    wmr_controller_state_t s;

    /* Trigger: Monado wmr_controller_og.c:287 divides byte 4 by 255.0 (0xFF). */
    struct {
        uint8_t raw;
        float expected;
    } trigger_cases[] = {
        {0x00, 0.0f},
        {0x40, 64.0f / 255.0f},
        {0x7F, 127.0f / 255.0f},
        {0x80, 128.0f / 255.0f},
        {0xC0, 192.0f / 255.0f},
        {0xFF, 1.0f},
    };
    for (size_t i = 0; i < sizeof(trigger_cases) / sizeof(trigger_cases[0]); i++){
        fill(f);
        f[2 + 4] = trigger_cases[i].raw;
        CHECK(wmr_decode_frame(f, sizeof(f), &s));
        CHECK(near(s.trigger, trigger_cases[i].expected, 1e-6f));
    }

    /* Trackpad: Monado wmr_controller_og.c:289-294.
     * Raw range 0x00..0x64 (0..100 decimal), center 0x32 (50).
     * Untouched sentinel: 0xFF on both axes maps to (0.0f, 0.0f).
     * Normalization formula: (raw - 0x32) / 0x32.0f -> [-1.0, 1.0]. */

    /* Untouched sentinel */
    fill(f);
    f[2 + 5] = 0xFF; f[2 + 6] = 0xFF;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.trackpad_x == 0.0f && s.trackpad_y == 0.0f);

    /* Asymmetric untouched: if one axis is untouched (0xFF), that axis is 0.0f */
    fill(f);
    f[2 + 5] = 0xFF; f[2 + 6] = 0x64;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.trackpad_x == 0.0f && near(s.trackpad_y, 1.0f, 1e-6f));

    fill(f);
    f[2 + 5] = 0x00; f[2 + 6] = 0xFF;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.trackpad_x, -1.0f, 1e-6f) && s.trackpad_y == 0.0f);

    /* Center (0x32 = 50) -> 0.0f */
    fill(f);
    f[2 + 5] = 0x32; f[2 + 6] = 0x32;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.trackpad_x, 0.0f, 1e-6f) && near(s.trackpad_y, 0.0f, 1e-6f));

    /* Minimum (0x00 = 0) -> -1.0f */
    fill(f);
    f[2 + 5] = 0x00; f[2 + 6] = 0x00;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.trackpad_x, -1.0f, 1e-6f) && near(s.trackpad_y, -1.0f, 1e-6f));

    /* Maximum (0x64 = 100) -> +1.0f */
    fill(f);
    f[2 + 5] = 0x64; f[2 + 6] = 0x64;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.trackpad_x, 1.0f, 1e-6f) && near(s.trackpad_y, 1.0f, 1e-6f));

    /* Intermediate coordinates:
     * 0x19 = 25 -> (25 - 50) / 50 = -0.5f
     * 0x4B = 75 -> (75 - 50) / 50 = +0.5f */
    fill(f);
    f[2 + 5] = 0x19; f[2 + 6] = 0x4B;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.trackpad_x, -0.5f, 1e-6f));
    CHECK(near(s.trackpad_y, 0.5f, 1e-6f));
}

static void test_thumbstick_and_buttons(void){
    printf("thumbstick and individual button bit parsing\n");
    uint8_t f[WMR_DECODE_FRAME_LENGTH];
    wmr_controller_state_t s;

    /* Monado wmr_controller_og.c:259-266 button bitmask:
     * 0x01: thumbstick click
     * 0x02: home
     * 0x04: menu
     * 0x08: squeeze (click)
     * 0x10: trackpad click
     * 0x20: bt pairing
     * 0x40: trackpad touch */

    /* No buttons */
    fill(f);
    f[2] = 0x00;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(!s.thumbstick_click && !s.home && !s.menu && !s.squeeze &&
          !s.trackpad_click && !s.bt_pairing && !s.trackpad_touch);

    /* Individual button checks */
    fill(f); f[2] = 0x01; CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.thumbstick_click && !s.home && !s.menu && !s.squeeze &&
          !s.trackpad_click && !s.bt_pairing && !s.trackpad_touch);

    fill(f); f[2] = 0x02; CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(!s.thumbstick_click && s.home && !s.menu && !s.squeeze &&
          !s.trackpad_click && !s.bt_pairing && !s.trackpad_touch);

    fill(f); f[2] = 0x04; CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(!s.thumbstick_click && !s.home && s.menu && !s.squeeze &&
          !s.trackpad_click && !s.bt_pairing && !s.trackpad_touch);

    fill(f); f[2] = 0x08; CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(!s.thumbstick_click && !s.home && !s.menu && s.squeeze &&
          !s.trackpad_click && !s.bt_pairing && !s.trackpad_touch);

    fill(f); f[2] = 0x10; CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(!s.thumbstick_click && !s.home && !s.menu && !s.squeeze &&
          s.trackpad_click && !s.bt_pairing && !s.trackpad_touch);

    fill(f); f[2] = 0x20; CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(!s.thumbstick_click && !s.home && !s.menu && !s.squeeze &&
          !s.trackpad_click && s.bt_pairing && !s.trackpad_touch);

    fill(f); f[2] = 0x40; CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(!s.thumbstick_click && !s.home && !s.menu && !s.squeeze &&
          !s.trackpad_click && !s.bt_pairing && s.trackpad_touch);

    /* Thumbstick: Monado wmr_controller_og.c:270-284.
     * 12-bit unsigned: 0x000..0xFFF (0..4095).
     * Center: 0x07FF (2047).
     * Formula: clamp_max1((stick - 0x07FF) / 0x07FF). */

    /* Center (0x7FF) -> 0.0f */
    fill(f);
    f[2 + 1] = 0xFF; f[2 + 2] = 0xF7; f[2 + 3] = 0x7F;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.thumbstick_x, 0.0f, 1e-6f));
    CHECK(near(s.thumbstick_y, 0.0f, 1e-6f));

    /* Minimum (0x000) -> -1.0f */
    fill(f);
    f[2 + 1] = 0x00; f[2 + 2] = 0x00; f[2 + 3] = 0x00;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.thumbstick_x, -1.0f, 1e-6f));
    CHECK(near(s.thumbstick_y, -1.0f, 1e-6f));

    /* Maximum (0xFFF = 4095) -> clamped to 1.0f (raw is 2048/2047 = 1.000488f) */
    fill(f);
    f[2 + 1] = 0xFF; f[2 + 2] = 0xFF; f[2 + 3] = 0xFF;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(s.thumbstick_x == 1.0f && s.thumbstick_y == 1.0f);

    /* Halfway negative: stick = 1024 (0x400)
     * (1024 - 2047) / 2047 = -1023 / 2047 ≈ -0.49975574f */
    fill(f);
    /* stick_x = 0x400 -> byte1 = 0x00, byte2 low nibble = 0x04
     * stick_y = 0x400 -> byte2 high nibble = 0x00, byte3 = 0x40 */
    f[2 + 1] = 0x00; f[2 + 2] = 0x04; f[2 + 3] = 0x40;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    CHECK(near(s.thumbstick_x, -1023.0f / 2047.0f, 1e-6f));
    CHECK(near(s.thumbstick_y, -1023.0f / 2047.0f, 1e-6f));
}

static void test_rejects(void){
    printf("rejects bad frames\n");
    uint8_t f[WMR_DECODE_FRAME_LENGTH];
    wmr_controller_state_t s;
    fill(f);
    CHECK(!wmr_decode_frame(f, WMR_DECODE_FRAME_LENGTH - 1, &s));
    CHECK(!wmr_decode_frame(f, WMR_DECODE_FRAME_LENGTH + 1, &s));
    CHECK(!wmr_decode_frame(NULL, WMR_DECODE_FRAME_LENGTH, &s));
    CHECK(!wmr_decode_frame(f, WMR_DECODE_FRAME_LENGTH, NULL));
    f[0] = 0xA2; CHECK(!wmr_decode_frame(f, sizeof(f), &s));
    fill(f); f[1] = 0x02; CHECK(!wmr_decode_frame(f, sizeof(f), &s));
}

static void test_ticks(void){
    printf("tick extension\n");
    uint64_t t = 0;
    CHECK(wmr_decode_extend_ticks(&t, 100) == 100);
    CHECK(wmr_decode_extend_ticks(&t, 0xFFFFFFF0u) == 0xFFFFFFF0u);
    CHECK(wmr_decode_extend_ticks(&t, 0x10) == 0x100000010ull);
    CHECK(wmr_decode_extend_ticks(&t, 0x20) == 0x100000020ull);
}

/* Real captures: accel magnitude should be about 1 g (~9.8 on the 49000
 * scale), temperature plausible, and ticks should rise within a session. */
static void test_samples(const char *path){
    printf("sample frames from %s\n", path);
    FILE *fp = fopen(path, "r");
    CHECK(fp != NULL);
    if (!fp) return;
    char line[512];
    char prev_session[32] = "";
    uint32_t prev_ticks = 0;
    unsigned n = 0;
    while (fgets(line, sizeof(line), fp)){
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        char *label = strtok(line, " \t\r\n");
        uint8_t f[64];
        unsigned len = 0;
        char *tok;
        while ((tok = strtok(NULL, " \t\r\n")) != NULL && len < sizeof(f)) f[len++] = (uint8_t) strtoul(tok, NULL, 16);
        wmr_controller_state_t s;
        CHECK(wmr_decode_frame(f, (uint16_t) len, &s));
        float mag = sqrtf(s.accel[0]*s.accel[0] + s.accel[1]*s.accel[1] + s.accel[2]*s.accel[2]);
        printf("  %-10s acc=(%6.2f %6.2f %6.2f) |a|=%5.2f gyro=(%6.3f %6.3f %6.3f) temp=%d batt=%u ticks=%lu\n",
               label, (double) s.accel[0], (double) s.accel[1], (double) s.accel[2], (double) mag,
               (double) s.gyro[0], (double) s.gyro[1], (double) s.gyro[2],
               s.temperature_raw, s.battery, (unsigned long) s.ticks);
        CHECK(mag > 7.0f && mag < 13.0f);
        char session[32];
        strncpy(session, label, sizeof(session) - 1);
        session[sizeof(session) - 1] = 0;
        char *dash = strchr(session, '-'); if (dash) *dash = 0;
        if (strcmp(session, prev_session) == 0) CHECK(s.ticks > prev_ticks);
        strcpy(prev_session, session);
        prev_ticks = s.ticks;
        n++;
    }
    fclose(fp);
    CHECK(n >= 2);
}

int main(int argc, char **argv){
    test_synthetic();
    test_battery_temperature();
    test_accel_scale();
    test_trigger_trackpad_ranges();
    test_thumbstick_and_buttons();
    test_rejects();
    test_ticks();
    if (argc > 1) test_samples(argv[1]);
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
