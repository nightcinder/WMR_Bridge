/* Host tests for the Switch Joy-Con/Pro handshake, SPI calibration packing and 0x30
 * codec. No BTstack and no hardware: frames and replies are built byte by byte. */

#include <math.h>
#include <stdio.h>
#include <string.h>
#include "wmr_controller_driver.h"
#include "wmr_decoded.h"
#include "wmr_discovery.h"
#include "wmr_switch.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

static int near(float a, float b, float eps){ return fabsf(a - b) <= eps; }

static void put16(uint8_t *p, int16_t v){
    p[0] = (uint8_t) (v & 0xFF);
    p[1] = (uint8_t) ((uint16_t) v >> 8);
}

/* Pack two 12-bit axes the way the controller does. */
static void pack12(uint8_t *p, uint16_t x, uint16_t y){
    p[0] = (uint8_t) (x & 0xFF);
    p[1] = (uint8_t) (((x >> 8) & 0x0F) | ((y & 0x0F) << 4));
    p[2] = (uint8_t) (y >> 4);
}

static void calibration_blocks(uint8_t imu[WMR_SWITCH_CAL_IMU_LEN],
                               uint8_t stick[WMR_SWITCH_CAL_STICK_LEN]){
    memset(imu, 0, WMR_SWITCH_CAL_IMU_LEN);
    for (unsigned axis = 0; axis < 3; axis++){
        put16(&imu[2 * axis], 0);            /* acc origin */
        put16(&imu[6 + 2 * axis], 16384);    /* acc sensitivity */
        put16(&imu[12 + 2 * axis], 0);       /* gyro origin */
        put16(&imu[18 + 2 * axis], 13371);   /* gyro sensitivity */
    }
    /* Left: max, center, min. Right: center, min, max. */
    pack12(&stick[0], 4095, 4095);
    pack12(&stick[3], 2048, 2048);
    pack12(&stick[6], 0, 0);
    pack12(&stick[9], 2048, 2048);
    pack12(&stick[12], 0, 0);
    pack12(&stick[15], 4095, 4095);
}

static uint16_t reply(uint8_t *f, uint8_t ack, uint8_t subcommand,
                      const uint8_t *data, uint8_t data_len){
    memset(f, 0, WMR_SWITCH_REPLY_OFF_DATA + 35);
    f[0] = WMR_SWITCH_HID_HEADER;
    f[1] = WMR_SWITCH_REPLY_REPORT_ID;
    f[WMR_SWITCH_REPLY_OFF_ACK] = ack;
    f[WMR_SWITCH_REPLY_OFF_SUBCMD] = subcommand;
    if (data_len) memcpy(&f[WMR_SWITCH_REPLY_OFF_DATA], data, data_len);
    return (uint16_t) (WMR_SWITCH_REPLY_OFF_DATA + (data_len ? data_len : 1));
}

static void test_subcommand_report(void){
    printf("output report 0x01\n");
    uint8_t out[WMR_SWITCH_OUTPUT_FRAME_SIZE];
    const uint8_t args[3] = { 0x20, 0x60, 0x18 };
    CHECK(wmr_switch_build_subcommand(7, 0x10, args, 3, out) == WMR_SWITCH_OUTPUT_FRAME_SIZE);
    CHECK(out[0] == WMR_SWITCH_OUTPUT_REPORT_ID);
    CHECK(out[1] == 7);
    CHECK(out[2] == 0x00 && out[3] == 0x01 && out[4] == 0x40 && out[5] == 0x40);
    CHECK(out[6] == 0x00 && out[7] == 0x01 && out[8] == 0x40 && out[9] == 0x40);
    CHECK(out[10] == 0x10);
    CHECK(out[11] == 0x20 && out[12] == 0x60 && out[13] == 0x18);
    CHECK(out[WMR_SWITCH_OUTPUT_FRAME_SIZE - 1] == 0);
    CHECK(wmr_switch_build_subcommand(0, 0x10, args, 0, out) == WMR_SWITCH_OUTPUT_FRAME_SIZE);
}

static void test_handshake(void){
    printf("handshake sequence and SPI calibration pack\n");
    uint8_t imu[WMR_SWITCH_CAL_IMU_LEN], stick[WMR_SWITCH_CAL_STICK_LEN];
    calibration_blocks(imu, stick);

    wmr_switch_handshake_t hs;
    wmr_switch_handshake_init(&hs);
    uint8_t out[WMR_SWITCH_OUTPUT_FRAME_SIZE];
    uint16_t len = 0;
    uint8_t frame[64];

    CHECK(wmr_switch_handshake_next(&hs, out, &len));
    CHECK(len == WMR_SWITCH_OUTPUT_FRAME_SIZE);
    CHECK(out[10] == WMR_SWITCH_SUBCMD_ENABLE_IMU && out[11] == 0x01 && out[1] == 0);
    CHECK(!wmr_switch_handshake_next(&hs, out, &len));      /* wait for the reply */
    uint16_t flen = reply(frame, 0x80, WMR_SWITCH_SUBCMD_ENABLE_IMU, NULL, 0);
    CHECK(wmr_switch_handshake_on_reply(&hs, frame, flen));

    CHECK(wmr_switch_handshake_next(&hs, out, &len));
    CHECK(out[1] == 1);
    CHECK(out[10] == WMR_SWITCH_SUBCMD_SPI_READ);
    CHECK(out[11] == 0x20 && out[12] == 0x60 && out[13] == WMR_SWITCH_CAL_IMU_LEN);
    flen = reply(frame, 0x90, WMR_SWITCH_SUBCMD_SPI_READ, imu, WMR_SWITCH_CAL_IMU_LEN);
    CHECK(wmr_switch_handshake_on_reply(&hs, frame, flen));

    CHECK(wmr_switch_handshake_next(&hs, out, &len));
    CHECK(out[10] == WMR_SWITCH_SUBCMD_SPI_READ);
    CHECK(out[11] == 0x3D && out[12] == 0x60 && out[13] == WMR_SWITCH_CAL_STICK_LEN);
    flen = reply(frame, 0x90, WMR_SWITCH_SUBCMD_SPI_READ, stick, WMR_SWITCH_CAL_STICK_LEN);
    CHECK(wmr_switch_handshake_on_reply(&hs, frame, flen));

    CHECK(wmr_switch_handshake_next(&hs, out, &len));
    CHECK(out[10] == WMR_SWITCH_SUBCMD_INPUT_MODE && out[11] == WMR_SWITCH_INPUT_MODE_FULL);
    flen = reply(frame, 0x80, WMR_SWITCH_SUBCMD_INPUT_MODE, NULL, 0);
    CHECK(wmr_switch_handshake_on_reply(&hs, frame, flen));

    CHECK(wmr_switch_handshake_ready(&hs));
    CHECK(!wmr_switch_handshake_next(&hs, out, &len));

    uint32_t total = 0;
    const uint8_t *blob = wmr_switch_handshake_blob(&hs, &total);
    CHECK(blob != NULL && total == WMR_SWITCH_CAL_SIZE);
    if (blob){
        uint8_t packed[WMR_SWITCH_CAL_SIZE];
        wmr_switch_calibration_pack(imu, stick, packed);
        CHECK(memcmp(blob, packed, sizeof(packed)) == 0);
        CHECK(memcmp(&blob[WMR_SWITCH_CAL_IMU_OFFSET], imu, sizeof(imu)) == 0);
        CHECK(memcmp(&blob[WMR_SWITCH_CAL_STICK_OFFSET], stick, sizeof(stick)) == 0);
    }

    /* A NACK retries the same step and fails after WMR_SWITCH_HS_MAX_TRIES. */
    wmr_switch_handshake_init(&hs);
    for (unsigned i = 0; i < WMR_SWITCH_HS_MAX_TRIES; i++){
        CHECK(wmr_switch_handshake_next(&hs, out, &len));
        flen = reply(frame, 0x00, WMR_SWITCH_SUBCMD_ENABLE_IMU, NULL, 0);
        CHECK(wmr_switch_handshake_on_reply(&hs, frame, flen));
    }
    CHECK(wmr_switch_handshake_failed(&hs));
    CHECK(!wmr_switch_handshake_next(&hs, out, &len));

    /* A short SPI reply is retried instead of packing garbage. */
    wmr_switch_handshake_init(&hs);
    CHECK(wmr_switch_handshake_next(&hs, out, &len));
    flen = reply(frame, 0x80, WMR_SWITCH_SUBCMD_ENABLE_IMU, NULL, 0);
    CHECK(wmr_switch_handshake_on_reply(&hs, frame, flen));
    CHECK(wmr_switch_handshake_next(&hs, out, &len));
    flen = reply(frame, 0x90, WMR_SWITCH_SUBCMD_SPI_READ, imu, 4);
    CHECK(wmr_switch_handshake_on_reply(&hs, frame, flen));
    CHECK(wmr_switch_handshake_next(&hs, out, &len));       /* same SPI step again */
    CHECK(out[10] == WMR_SWITCH_SUBCMD_SPI_READ && out[11] == 0x20);

    /* A missing reply times out and counts as a failed attempt. */
    wmr_switch_handshake_init(&hs);
    for (unsigned i = 0; i < WMR_SWITCH_HS_MAX_TRIES; i++){
        CHECK(wmr_switch_handshake_next(&hs, out, &len));
        wmr_switch_handshake_tick(&hs, WMR_SWITCH_HS_REPLY_TIMEOUT_MS);
    }
    CHECK(wmr_switch_handshake_failed(&hs));
}

static void switch_frame(uint8_t f[WMR_SWITCH_INPUT_FRAME_SIZE]){
    memset(f, 0, WMR_SWITCH_INPUT_FRAME_SIZE);
    f[0] = WMR_SWITCH_HID_HEADER;
    f[1] = WMR_SWITCH_INPUT_REPORT_ID;
    f[WMR_SWITCH_OFF_TIMER] = 0x10;
    f[WMR_SWITCH_OFF_BATTERY] = 0x80;                       /* full, wired */
    f[WMR_SWITCH_OFF_BUTTONS] = WMR_SWITCH_BTN_R_A;
    f[WMR_SWITCH_OFF_BUTTONS + 1] = WMR_SWITCH_BTN_HOME | WMR_SWITCH_BTN_PLUS;
    f[WMR_SWITCH_OFF_BUTTONS + 2] = WMR_SWITCH_BTN_L_ZL;
    pack12(&f[WMR_SWITCH_OFF_LEFT_STICK], 3072, 2048);      /* x half right */
    pack12(&f[WMR_SWITCH_OFF_RIGHT_STICK], 2048, 2048);
    /* Three IMU samples: accel X 0, 2048, 4096; gyro Z 0, 1000, 2000 dps-ish */
    for (unsigned i = 0; i < WMR_SWITCH_SAMPLES_PER_REPORT; i++){
        uint8_t *imu = &f[WMR_SWITCH_OFF_IMU + 12 * i];
        put16(&imu[0], (int16_t) (2048 * (int) i));
        put16(&imu[2], 0);
        put16(&imu[4], 4096);
        put16(&imu[6], 0);
        put16(&imu[8], 0);
        put16(&imu[10], (int16_t) (1638 * (int) i));
    }
}

static void test_decode(void){
    printf("0x30 report: three IMU samples, buttons and sticks\n");
    uint8_t imu[WMR_SWITCH_CAL_IMU_LEN], stick[WMR_SWITCH_CAL_STICK_LEN];
    calibration_blocks(imu, stick);
    wmr_switch_calibration_t cal;
    wmr_switch_calibration_parse(imu, stick, &cal);
    CHECK(cal.valid);
    CHECK(cal.stick_valid[WMR_HAND_LEFT] && cal.stick_valid[WMR_HAND_RIGHT]);
    wmr_switch_set_calibration(WMR_HAND_LEFT, &cal);

    wmr_frame_t frame;
    memset(&frame, 0, sizeof(frame));
    frame.sequence = 11;
    frame.rx_time_us = 1000000;
    frame.length = WMR_SWITCH_INPUT_FRAME_SIZE;
    frame.hand = WMR_HAND_LEFT;
    switch_frame(frame.data);

    uint8_t records[WMR_DECODED_MAX_SAMPLES][WMR_DECODED_SIZE];
    unsigned count = wmr_decoded_samples_from_frame(&frame, records, WMR_DECODED_MAX_SAMPLES);
    CHECK(count == WMR_SWITCH_SAMPLES_PER_REPORT);

    for (unsigned i = 0; i < count; i++){
        wmr_decoded_t r;
        CHECK(wmr_decoded_unpack(records[i], WMR_DECODED_SIZE, &r));
        CHECK(r.hand == WMR_HAND_LEFT);
        CHECK(r.sequence == 11);
        CHECK((r.flags & WMR_DECODED_FLAG_CALIBRATED) != 0);
        CHECK(near(r.accel[0], 9.80665f * 0.5f * (float) i, 0.02f));
        CHECK(near(r.accel[2], 9.80665f, 0.02f));
        CHECK(near(r.gyro[2], 1.74533f * (float) i, 0.02f));
        CHECK(r.device_ticks == 0);
        CHECK(r.battery == 8);
        CHECK((r.buttons & WMR_BTN_HOME) != 0);
        CHECK((r.buttons & WMR_BTN_MENU) != 0);
        CHECK((r.buttons & WMR_BTN_SQUEEZE) != 0);
        CHECK(r.stick[0] > 16000 && r.stick[0] < 16600);    /* 0.5 * 32767 */
        CHECK(near((float) r.rx_time_us, 1000000.0f - 5000.0f * (float) (2 - i), 1.0f));
    }

    /* Single-record path (the driver's decode) keeps the newest sample. */
    uint8_t latest[WMR_DECODED_SIZE];
    CHECK(wmr_decoded_from_frame(&frame, latest));
    wmr_decoded_t r;
    CHECK(wmr_decoded_unpack(latest, WMR_DECODED_SIZE, &r));
    CHECK(near(r.accel[0], 9.80665f, 0.02f));

    /* Uncalibrated decode: default scales and no CALIBRATED flag. */
    wmr_switch_set_calibration(WMR_HAND_LEFT, NULL);
    count = wmr_decoded_samples_from_frame(&frame, records, WMR_DECODED_MAX_SAMPLES);
    CHECK(count == WMR_SWITCH_SAMPLES_PER_REPORT);
    CHECK(wmr_decoded_unpack(records[2], WMR_DECODED_SIZE, &r));
    CHECK((r.flags & WMR_DECODED_FLAG_CALIBRATED) == 0);
    CHECK(near(r.accel[0], 4096.0f * 0.000244f * 9.80665f, 0.05f));

    /* Wrong report ID, length or hand is rejected. */
    frame.data[1] = 0x21;
    CHECK(wmr_decoded_samples_from_frame(&frame, records, WMR_DECODED_MAX_SAMPLES) == 0);
    frame.data[1] = WMR_SWITCH_INPUT_REPORT_ID;
    frame.length--;
    CHECK(!wmr_switch_is_input_report(frame.data, frame.length));
    CHECK(wmr_decoded_samples_from_frame(&frame, records, WMR_DECODED_MAX_SAMPLES) == 0);
    frame.length++;
    frame.hand = 2;
    CHECK(wmr_decoded_samples_from_frame(&frame, records, WMR_DECODED_MAX_SAMPLES) == 0);
}

static void test_discovery(void){
    printf("discovery names and model\n");
    int hand = -1;
    const uint8_t joycon_l[] = "Joy-Con (L)";
    const uint8_t joycon_r[] = "Joy-Con (R)";
    const uint8_t pro[] = "Pro Controller";

    const wmr_controller_driver_t *d =
        wmr_controller_driver_for_name(joycon_l, sizeof(joycon_l) - 1, &hand);
    CHECK(d && d->model == WMR_MODEL_SWITCH && hand == WMR_HAND_LEFT);
    CHECK(wmr_discovery_model_for_name(joycon_l, sizeof(joycon_l) - 1) == WMR_MODEL_SWITCH);
    d = wmr_controller_driver_for_name(joycon_r, sizeof(joycon_r) - 1, &hand);
    CHECK(d && d->model == WMR_MODEL_SWITCH && hand == WMR_HAND_RIGHT);
    d = wmr_controller_driver_for_name(pro, sizeof(pro) - 1, &hand);
    CHECK(d && d->model == WMR_MODEL_SWITCH && hand == WMR_HAND_LEFT);
    CHECK(strcmp(wmr_model_name(WMR_MODEL_SWITCH), "Switch") == 0);
    CHECK(wmr_controller_driver_for_name((const uint8_t *) "Keyboard", 8, &hand) == NULL);
    CHECK(hand == -1);

    /* The stored address record still round-trips a Switch model in either hand. */
    wmr_addr_record_t record, back;
    memset(&record, 0, sizeof(record));
    record.valid[WMR_HAND_LEFT] = true;
    record.model[WMR_HAND_LEFT] = WMR_MODEL_SWITCH;
    record.address[WMR_HAND_LEFT][0] = 0xAA;
    record.valid[WMR_HAND_RIGHT] = true;
    record.model[WMR_HAND_RIGHT] = WMR_MODEL_WMR;
    record.address[WMR_HAND_RIGHT][0] = 0xBB;
    uint8_t packed[WMR_ADDR_RECORD_SIZE];
    wmr_addr_record_pack(&record, packed);
    CHECK(wmr_addr_record_unpack(packed, sizeof(packed), &back));
    CHECK(back.model[WMR_HAND_LEFT] == WMR_MODEL_SWITCH);
    CHECK(back.model[WMR_HAND_RIGHT] == WMR_MODEL_WMR);
}

int main(void){
    test_subcommand_report();
    test_handshake();
    test_decode();
    test_discovery();
    printf("%s: %d checks, %d failure(s)\n", failures ? "FAIL" : "ok", checks, failures);
    return failures ? 1 : 0;
}
