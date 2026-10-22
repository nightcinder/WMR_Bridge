/*
 * Host tests for the FF12 pieces that need no BTstack: the FF11/FF12 CCC
 * exclusion rule, the minimum-MTU rule, and the raw-frame -> FF12 record path.
 *
 * What this does NOT test: the BTstack callbacks, the GATT database, the pump,
 * or anything on the air. Those need the firmware build and hardware.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "wmr_decoded.h"
#include "wmr_stream_mode.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

#define RAW     WMR_STREAM_RAW
#define DEC     WMR_STREAM_DECODED
#define NONE    WMR_STREAM_NONE

static void test_ccc_rules(void){
    printf("CCC exclusion\n");
    wmr_stream_ccc_result_t r;

    /* From nothing: either stream may be enabled. */
    r = wmr_stream_mode_ccc_write(NONE, RAW, true);
    CHECK(r.accepted && r.mode == RAW && r.att_error == 0);
    r = wmr_stream_mode_ccc_write(NONE, DEC, true);
    CHECK(r.accepted && r.mode == DEC && r.att_error == 0);
    r = wmr_stream_mode_ccc_write(NONE, RAW, false);
    CHECK(r.accepted && r.mode == NONE);
    r = wmr_stream_mode_ccc_write(NONE, DEC, false);
    CHECK(r.accepted && r.mode == NONE);

    /* FF11 active: enabling FF12 is refused with 0x80 and changes nothing. */
    r = wmr_stream_mode_ccc_write(RAW, DEC, true);
    CHECK(!r.accepted && r.att_error == 0x80 && r.mode == RAW);
    CHECK(WMR_ATT_ERROR_OTHER_STREAM_ACTIVE == 0x80);
    /* Enabling it again is fine; disabling the inactive one does nothing. */
    r = wmr_stream_mode_ccc_write(RAW, RAW, true);
    CHECK(r.accepted && r.mode == RAW);
    r = wmr_stream_mode_ccc_write(RAW, DEC, false);
    CHECK(r.accepted && r.mode == RAW);

    /* FF12 active: the mirror image. */
    r = wmr_stream_mode_ccc_write(DEC, RAW, true);
    CHECK(!r.accepted && r.att_error == 0x80 && r.mode == DEC);
    r = wmr_stream_mode_ccc_write(DEC, DEC, true);
    CHECK(r.accepted && r.mode == DEC);
    r = wmr_stream_mode_ccc_write(DEC, RAW, false);
    CHECK(r.accepted && r.mode == DEC);

    /* Switching: disable the active stream first, then enable the other. */
    wmr_stream_t mode = RAW;
    r = wmr_stream_mode_ccc_write(mode, DEC, true);
    CHECK(!r.accepted);
    r = wmr_stream_mode_ccc_write(mode, RAW, false);
    CHECK(r.accepted && r.mode == NONE);
    mode = r.mode;
    r = wmr_stream_mode_ccc_write(mode, DEC, true);
    CHECK(r.accepted && r.mode == DEC);
    mode = r.mode;
    r = wmr_stream_mode_ccc_write(mode, DEC, false);
    CHECK(r.accepted && r.mode == NONE);

    /* A subscriber that tries both can never end up with both. */
    for (int a = 0; a < 2; a++){
        mode = NONE;
        wmr_stream_t first = a ? DEC : RAW, second = a ? RAW : DEC;
        mode = wmr_stream_mode_ccc_write(mode, first, true).mode;
        wmr_stream_ccc_result_t both = wmr_stream_mode_ccc_write(mode, second, true);
        CHECK(!both.accepted && both.mode == first);
        CHECK(wmr_stream_mode_ccc_enabled(both.mode, first));
        CHECK(!wmr_stream_mode_ccc_enabled(both.mode, second));
    }

    /* Invalid stream id is refused. */
    r = wmr_stream_mode_ccc_write(NONE, NONE, true);
    CHECK(!r.accepted);

    /* CCC read-back. */
    CHECK(!wmr_stream_mode_ccc_enabled(NONE, RAW) && !wmr_stream_mode_ccc_enabled(NONE, DEC));
    CHECK(wmr_stream_mode_ccc_enabled(RAW, RAW) && !wmr_stream_mode_ccc_enabled(RAW, DEC));
    CHECK(wmr_stream_mode_ccc_enabled(DEC, DEC) && !wmr_stream_mode_ccc_enabled(DEC, RAW));
    CHECK(strcmp(wmr_stream_mode_name(RAW), "raw") == 0);
    CHECK(strcmp(wmr_stream_mode_name(DEC), "decoded") == 0);
    CHECK(strcmp(wmr_stream_mode_name(NONE), "none") == 0);
}

static void test_mtu_rule(void){
    printf("minimum MTU for FF12\n");
    CHECK(WMR_DECODED_MIN_MTU == 59);
    CHECK(!wmr_decoded_mtu_ok(0));
    CHECK(!wmr_decoded_mtu_ok(23));      /* default ATT MTU */
    CHECK(!wmr_decoded_mtu_ok(58));
    CHECK(wmr_decoded_mtu_ok(59));
    CHECK(wmr_decoded_mtu_ok(247));
    CHECK(wmr_decoded_mtu_ok(517));
}

static void test_decimate(void){
    printf("FF12 decimation rule\n");
    for (uint32_t seq = 0; seq < 20; seq++){
        CHECK(wmr_decoded_decimate_keep(seq, 0));       /* off */
        CHECK(wmr_decoded_decimate_keep(seq, 1));       /* off (the default) */
    }
    unsigned kept2 = 0, kept4 = 0;
    for (uint32_t seq = 1; seq <= 1000; seq++){
        kept2 += wmr_decoded_decimate_keep(seq, 2);
        kept4 += wmr_decoded_decimate_keep(seq, 4);
    }
    CHECK(kept2 == 500);
    CHECK(kept4 == 250);
    CHECK(!wmr_decoded_decimate_keep(1, 4) && !wmr_decoded_decimate_keep(3, 4));
    CHECK(wmr_decoded_decimate_keep(4, 4) && wmr_decoded_decimate_keep(8, 4));
    CHECK(wmr_decoded_decimate_keep(0xFFFFFFFCu, 4));   /* wrap region of the 32-bit sequence */
    CHECK(!wmr_decoded_decimate_keep(0xFFFFFFFFu, 4));
}

static void make_input_frame(wmr_frame_t *f, uint8_t hand, uint32_t seq, uint64_t t_us){
    memset(f, 0, sizeof(*f));
    f->hand = hand;
    f->sequence = seq;
    f->rx_time_us = t_us;
    f->length = 46;
    f->data[0] = 0xA1;
    f->data[1] = 0x01;
    f->data[2] = 0x0C;                         /* menu + squeeze */
    f->data[2 + 1] = 0xFF; f->data[2 + 2] = 0xF7; f->data[2 + 3] = 0x7F;   /* stick centred */
    f->data[2 + 4] = 0x80;                     /* trigger */
    f->data[2 + 5] = 0xFF; f->data[2 + 6] = 0xFF;
    f->data[2 + 7] = 0x55;                     /* battery */
    f->data[2 + 28] = 0x10; f->data[2 + 29] = 0x27;   /* ticks 10000 */
}

static void test_from_frame(void){
    printf("raw frame -> FF12 record\n");
    wmr_frame_t f;
    uint8_t rec[WMR_DECODED_SIZE];
    wmr_decoded_t d;

    make_input_frame(&f, 1, 0x01020304u, UINT64_C(0x1122334455667788));
    CHECK(wmr_decoded_from_frame(&f, rec));
    CHECK(wmr_decoded_unpack(rec, sizeof(rec), &d));
    CHECK(rec[0] == WMR_DECODED_VERSION && rec[1] == 1 && rec[2] == 0);     /* version, hand, flags=0 */
    CHECK(d.sequence == 0x01020304u);
    CHECK(d.rx_time_us == UINT64_C(0x1122334455667788));
    CHECK(d.device_ticks == 10000 && d.battery == 0x55 && d.trigger == 0x80);
    CHECK(d.buttons == (WMR_BTN_MENU | WMR_BTN_SQUEEZE));
    CHECK(d.stick[0] == 0 && d.stick[1] == 0);

    /* Anything that is not a valid 46-byte input report is rejected. */
    make_input_frame(&f, 0, 1, 1); f.length = 45;
    CHECK(!wmr_decoded_from_frame(&f, rec));
    make_input_frame(&f, 0, 1, 1); f.length = 47;
    CHECK(!wmr_decoded_from_frame(&f, rec));
    make_input_frame(&f, 0, 1, 1); f.data[1] = 0x02;      /* a config response, not an input report */
    CHECK(!wmr_decoded_from_frame(&f, rec));
    make_input_frame(&f, 0, 1, 1); f.data[0] = 0xA2;
    CHECK(!wmr_decoded_from_frame(&f, rec));
    make_input_frame(&f, 2, 1, 1);                         /* invalid hand */
    CHECK(!wmr_decoded_from_frame(&f, rec));
    CHECK(!wmr_decoded_from_frame(NULL, rec));
}

static void test_sample_file(const char *path){
    printf("sample frames through the FF12 path (%s)\n", path);
    FILE *fp = fopen(path, "r");
    CHECK(fp != NULL);
    if (!fp) return;
    char line[512];
    unsigned n = 0;
    while (fgets(line, sizeof(line), fp)){
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        strtok(line, " \t\r\n");                      /* label */
        wmr_frame_t f;
        memset(&f, 0, sizeof(f));
        char *tok;
        while ((tok = strtok(NULL, " \t\r\n")) != NULL && f.length < WMR_CFG_MAX_FRAME)
            f.data[f.length++] = (uint8_t) strtoul(tok, NULL, 16);
        f.hand = (uint8_t) (n & 1);
        f.sequence = 100 + n;
        f.rx_time_us = 1000u * n;
        uint8_t rec[WMR_DECODED_SIZE];
        wmr_decoded_t d;
        CHECK(wmr_decoded_from_frame(&f, rec));
        CHECK(wmr_decoded_unpack(rec, sizeof(rec), &d));
        CHECK(d.hand == f.hand && d.sequence == f.sequence && d.rx_time_us == f.rx_time_us);
        n++;
    }
    fclose(fp);
    CHECK(n >= 2);
}

int main(int argc, char **argv){
    test_ccc_rules();
    test_mtu_rule();
    test_decimate();
    test_from_frame();
    if (argc > 1) test_sample_file(argv[1]);
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
