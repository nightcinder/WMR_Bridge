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
    test_rejects();
    test_ticks();
    if (argc > 1) test_samples(argv[1]);
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
