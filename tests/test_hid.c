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

/* Host tests for wmr_hid: the shared input->report vectors, the descriptor
 * bytes, and the firmware's stale/release and coalescing state machine. */

#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "wmr_hid.h"
#include "wmr_decoded.h"

static int failures;

#define CHECK(cond, ...) do { \
    if (!(cond)){ \
        failures++; \
        printf("FAIL %s:%d: ", __FILE__, __LINE__); \
        printf(__VA_ARGS__); \
        printf("\n"); \
    } \
} while (0)

static void free_tokens(char **tokens, int count){
    for (int i = 0; i < count; i++) free(tokens[i]);
}

static int split(char *line, char **tokens, int max){
    int count = 0;
    char *save = NULL;
    for (char *p = strtok_r(line, " \t\r\n", &save); p && count < max;
         p = strtok_r(NULL, " \t\r\n", &save)){
        tokens[count] = strdup(p);
        if (!tokens[count]) return count;
        count++;
    }
    return count;
}

static void read_vectors(const char *path){
    FILE *f = fopen(path, "r");
    CHECK(f != NULL, "open %s", path);
    if (!f) return;

    char line[1024];
    int cases = 0;
    while (fgets(line, sizeof(line), f)){
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        char *t[40];
        const int n = split(line, t, 40);
        if (n != 30){
            failures++;
            printf("FAIL vectors: expected 30 fields, got %d in %s", n, line);
            free_tokens(t, n);
            continue;
        }
        double num[30] = {
            atof(t[1]), atof(t[2]), atof(t[3]), atof(t[4]), atof(t[5]),
            atof(t[6]), atof(t[7]),
            atof(t[8]), atof(t[9]), atof(t[10]), atof(t[11]), atof(t[12]),
            atof(t[13]), atof(t[14]),
            atof(t[15]), atof(t[16]), atof(t[17]), atof(t[18]),
            atof(t[19]), atof(t[20]),
        };
        wmr_hid_hand_t left = {
            .connected = num[0] != 0.0,
            .buttons = (uint8_t) num[1],
            .stick = {(float) num[2], (float) num[3]},
            .trigger = (uint8_t) num[4],
            .trackpad = {(float) num[5], (float) num[6]},
        };
        wmr_hid_hand_t right = {
            .connected = num[7] != 0.0,
            .buttons = (uint8_t) num[8],
            .stick = {(float) num[9], (float) num[10]},
            .trigger = (uint8_t) num[11],
            .trackpad = {(float) num[12], (float) num[13]},
        };
        for (unsigned i = 0; i < WMR_HID_REPORT_SIZE; i++)
            num[21 + i] = atof(t[21 + i]);
        wmr_hid_config_t config = *wmr_hid_default_config();
        config.invert_stick_y = num[16] != 0.0;
        config.invert_trackpad_y = num[17] != 0.0;
        config.left_trackpad = (uint8_t) num[18];
        config.right_trackpad = (uint8_t) num[19];
        wmr_hid_history_t previous = { num[14] != 0.0, num[15] != 0.0 };

        uint8_t report[WMR_HID_REPORT_SIZE];
        wmr_hid_build_report(&left, &right, &config, &previous, report);
        for (unsigned i = 0; i < WMR_HID_REPORT_SIZE; i++){
            const uint8_t want = (uint8_t) num[21 + i];
            CHECK(report[i] == want, "%s byte %u: got 0x%02x want 0x%02x",
                  t[0], i, report[i], want);
        }
        cases++;
        free_tokens(t, n);
    }
    fclose(f);
    CHECK(cases >= 20, "only %d vectors read", cases);
}

static void read_descriptor(const char *path){
    FILE *f = fopen(path, "r");
    CHECK(f != NULL, "open %s", path);
    if (!f) return;
    uint8_t expected[256];
    unsigned count = 0;
    char token[8];
    while (fscanf(f, "%7s", token) == 1){
        if (token[0] == '#'){       /* comment to end of line */
            int ch;
            while ((ch = fgetc(f)) != EOF && ch != '\n') {}
            continue;
        }
        if (count >= sizeof(expected)) break;
        expected[count++] = (uint8_t) strtoul(token, NULL, 16);
    }
    fclose(f);

    unsigned actual_size = 0;
    const uint8_t *actual = wmr_hid_report_descriptor(&actual_size);
    CHECK(actual_size == count, "descriptor size %u, vector says %u", actual_size, count);
    CHECK(memcmp(actual, expected, actual_size < count ? actual_size : count) == 0,
          "descriptor bytes differ from the companion's");
}

/* A neutral 46-byte WMR input report: sticks centred, no buttons. */
static void neutral_frame(uint8_t frame[WMR_DECODE_FRAME_LENGTH]){
    memset(frame, 0, WMR_DECODE_FRAME_LENGTH);
    frame[0] = 0xA1;
    frame[1] = WMR_DECODE_REPORT_ID;
    frame[3] = 0xFF;              /* stick x low 8 */
    frame[4] = 0xF7;              /* stick x high nibble + y low nibble */
    frame[5] = 0x7F;              /* stick y high 8 */
    frame[7] = 0xFF;              /* trackpad x untouched */
    frame[8] = 0xFF;              /* trackpad y untouched */
}

static void test_state(void){
    uint8_t frame[WMR_DECODE_FRAME_LENGTH];
    neutral_frame(frame);

    const uint8_t neutral[WMR_HID_REPORT_SIZE] = {0, 0, 15, 0, 0, 0, 0, 0, 0};
    wmr_hid_state_t state;
    wmr_hid_reset(&state);

    uint8_t report[WMR_HID_REPORT_SIZE];
    CHECK(wmr_hid_report_due(&state, 0), "a reset must owe a neutral report");
    CHECK(wmr_hid_take_report(&state, 0, report), "first report after reset");
    CHECK(memcmp(report, neutral, sizeof(neutral)) == 0, "reset report is neutral");
    CHECK(!wmr_hid_report_due(&state, 1000), "nothing new, nothing due");

    /* A changed sample is sent at the next tick, then rate-limited. */
    frame[2] = 0x08;              /* LEFT squeeze */
    CHECK(wmr_hid_note_report(&state, 0, frame, sizeof(frame), 20000), "squeeze sample");
    CHECK(wmr_hid_take_report(&state, 20000, report), "squeeze report");
    CHECK(report[0] == 0x10, "LEFT squeeze is L1, got 0x%02x", report[0]);

    frame[2] = 0x00;
    CHECK(wmr_hid_note_report(&state, 0, frame, sizeof(frame), 21000), "release sample");
    CHECK(!wmr_hid_report_due(&state, 21500), "rate limit holds the release");
    CHECK(wmr_hid_report_due(&state, 30001), "release due after the interval");
    CHECK(wmr_hid_take_report(&state, 30001, report), "release report");
    CHECK(memcmp(report, neutral, sizeof(neutral)) == 0, "release is neutral");

    frame[2] = 0x08;
    CHECK(wmr_hid_note_report(&state, 0, frame, sizeof(frame), 31000), "second squeeze");
    CHECK(wmr_hid_take_report(&state, 41002, report), "second squeeze report");
    CHECK(report[0] == 0x10, "second LEFT squeeze is L1, got 0x%02x", report[0]);

    /* No sample for WMR_HID_STALE_MS: release the hand once. */
    CHECK(wmr_hid_expire(&state, 41002 + WMR_HID_STALE_US + 1), "hand must expire");
    CHECK(wmr_hid_take_report(&state, 41002 + WMR_HID_STALE_US + 1, report), "release report");
    CHECK(memcmp(report, neutral, sizeof(neutral)) == 0, "stale release is neutral");
    CHECK(!wmr_hid_expire(&state, 41002 + 2 * WMR_HID_STALE_US), "release is once only");

    /* The other hand still reports after the first one released. */
    frame[2] = 0x08;
    CHECK(wmr_hid_note_report(&state, 1, frame, sizeof(frame), 900000), "right sample");
    CHECK(wmr_hid_take_report(&state, 900000, report), "right report");
    CHECK(report[0] == 0x20, "RIGHT squeeze is R1, got 0x%02x", report[0]);

    /* Bad frames are ignored and do not touch the state. */
    CHECK(!wmr_hid_note_report(&state, 2, frame, sizeof(frame), 910000), "hand 2 rejected");
    CHECK(!wmr_hid_note_report(&state, 0, frame, 45, 910000), "short frame rejected");
    frame[0] = 0xA0;
    CHECK(!wmr_hid_note_report(&state, 0, frame, sizeof(frame), 910000), "bad header rejected");
}

int main(int argc, char **argv){
    if (argc != 3){
        fprintf(stderr, "usage: %s <hid_gamepad.txt> <hid_report_descriptor.txt>\n", argv[0]);
        return 2;
    }
    read_vectors(argv[1]);
    read_descriptor(argv[2]);
    test_state();
    if (failures){
        printf("%d FAILURES\n", failures);
        return 1;
    }
    printf("hid: all vectors and state tests passed\n");
    return 0;
}
