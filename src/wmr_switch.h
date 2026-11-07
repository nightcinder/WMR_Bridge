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
 * wmr_switch.h - Nintendo Switch Joy-Con (L)/(R) and Pro Controller over Classic BT HID.
 *
 * Pure C, no BTstack dependency, host-tested. This file owns three things that are easy
 * to get wrong and impossible to verify without a controller in hand:
 *
 *   1. the output report 0x01 that carries a subcommand (rumble + counter + subcommand),
 *      and the handshake that enables the IMU, reads the factory calibration from SPI
 *      and selects the 60 Hz full input mode;
 *   2. the packing of the two SPI calibration reads into the FF13 blob the phone reads;
 *   3. the 49-byte input report 0x30, which carries one button/stick snapshot and three
 *      12-byte IMU samples (5 ms apart), decoded into FF12/FF14 records.
 *
 * Protocol: dekuNukem/Nintendo_Switch_Reverse_Engineering, bluetooth_hid_notes.md,
 * spi_flash_notes.md and imu_sensor_notes.md. See docs/SWITCH.md.
 */

#ifndef WMR_SWITCH_H
#define WMR_SWITCH_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "wmr_config.h"
#include "wmr_decoded.h"

/* Frames as delivered by BTstack include the 0xA1 HID header before the report ID. */
#define WMR_SWITCH_HID_HEADER           0xA1
#define WMR_SWITCH_OUTPUT_REPORT_ID     0x01
#define WMR_SWITCH_INPUT_REPORT_ID      0x30
#define WMR_SWITCH_REPLY_REPORT_ID      0x21

/* The 0x30 report is 49 bytes including its report ID; a captured frame adds 0xA1. */
#define WMR_SWITCH_REPORT_SIZE          49
#define WMR_SWITCH_INPUT_FRAME_SIZE     (WMR_SWITCH_REPORT_SIZE + 1)

/* Output report 0x01 is 49 bytes: ID, packet counter, 8 rumble bytes, subcommand, args. */
#define WMR_SWITCH_OUTPUT_FRAME_SIZE    49

/* Subcommands used by the handshake. */
#define WMR_SWITCH_SUBCMD_ENABLE_IMU    0x40
#define WMR_SWITCH_SUBCMD_SPI_READ      0x10
#define WMR_SWITCH_SUBCMD_INPUT_MODE    0x03
#define WMR_SWITCH_INPUT_MODE_FULL      0x30

/* Factory calibration blocks read from SPI and packed, in this order, into FF13. */
#define WMR_SWITCH_CAL_IMU_ADDR         0x6020
#define WMR_SWITCH_CAL_IMU_LEN          24
#define WMR_SWITCH_CAL_STICK_ADDR       0x603D
#define WMR_SWITCH_CAL_STICK_LEN        18
#define WMR_SWITCH_CAL_IMU_OFFSET       0
#define WMR_SWITCH_CAL_STICK_OFFSET     WMR_SWITCH_CAL_IMU_LEN
#define WMR_SWITCH_CAL_SIZE             (WMR_SWITCH_CAL_IMU_LEN + WMR_SWITCH_CAL_STICK_LEN)

/* Four subcommands, each retried up to this many times on a NACK or a short reply. */
#define WMR_SWITCH_HS_MAX_TRIES         3
/* Spacing between handshake reports. The controller answers a subcommand in a few ms. */
#define WMR_SWITCH_HS_INTERVAL_MS       50
/* A missing reply counts as a failed attempt after this long. */
#define WMR_SWITCH_HS_REPLY_TIMEOUT_MS  500

/* Offsets into a captured frame (frame[0] = 0xA1, frame[1] = report ID). */
#define WMR_SWITCH_OFF_TIMER            2
#define WMR_SWITCH_OFF_BATTERY          3
#define WMR_SWITCH_OFF_BUTTONS          4       /* 3 bytes: right, shared, left */
#define WMR_SWITCH_OFF_LEFT_STICK       7       /* 3 bytes, two packed 12-bit axes */
#define WMR_SWITCH_OFF_RIGHT_STICK      10
#define WMR_SWITCH_OFF_IMU              14      /* 3 x 12 bytes */

/* Subcommand reply 0x21 offsets: report bytes 13, 14 and 15. A 0x10 reply then
 * echoes the 4-byte little-endian SPI address and 1-byte size before its data. */
#define WMR_SWITCH_REPLY_OFF_ACK        14
#define WMR_SWITCH_REPLY_OFF_SUBCMD     15
#define WMR_SWITCH_REPLY_OFF_DATA       16
#define WMR_SWITCH_SPI_ECHO_SIZE        5
#define WMR_SWITCH_REPLY_OFF_SPI_DATA   (WMR_SWITCH_REPLY_OFF_DATA + WMR_SWITCH_SPI_ECHO_SIZE)
#define WMR_SWITCH_REPLY_MIN_SIZE       (WMR_SWITCH_REPLY_OFF_DATA + 1)

/* Button bits, report bytes 3 (right), 4 (shared) and 5 (left). */
#define WMR_SWITCH_BTN_R_Y              0x01
#define WMR_SWITCH_BTN_R_X              0x02
#define WMR_SWITCH_BTN_R_B              0x04
#define WMR_SWITCH_BTN_R_A              0x08
#define WMR_SWITCH_BTN_R_SR             0x10
#define WMR_SWITCH_BTN_R_SL             0x20
#define WMR_SWITCH_BTN_R_R              0x40
#define WMR_SWITCH_BTN_R_ZR             0x80
#define WMR_SWITCH_BTN_MINUS            0x01
#define WMR_SWITCH_BTN_PLUS             0x02
#define WMR_SWITCH_BTN_R_STICK          0x04
#define WMR_SWITCH_BTN_L_STICK          0x08
#define WMR_SWITCH_BTN_HOME             0x10
#define WMR_SWITCH_BTN_CAPTURE          0x20
#define WMR_SWITCH_BTN_L_DOWN           0x01
#define WMR_SWITCH_BTN_L_UP             0x02
#define WMR_SWITCH_BTN_L_RIGHT          0x04
#define WMR_SWITCH_BTN_L_LEFT           0x08
#define WMR_SWITCH_BTN_L_SR             0x10
#define WMR_SWITCH_BTN_L_SL             0x20
#define WMR_SWITCH_BTN_L_L              0x40
#define WMR_SWITCH_BTN_L_ZL             0x80

/* Number of IMU samples in one 0x30 report, oldest first. */
#define WMR_SWITCH_SAMPLES_PER_REPORT   3
/* The samples are 5 ms apart; the last one belongs to the report's arrival time. */
#define WMR_SWITCH_SAMPLE_PERIOD_US     5000u

/* ---- Output report 0x01 ------------------------------------------------- */

/* Build one subcommand report. `out` receives WMR_SWITCH_OUTPUT_FRAME_SIZE bytes (the
 * neutral rumble bytes fill the tail). Returns the frame size, or 0 when the arguments do
 * not fit. `counter` is the 8-bit packet counter, incremented per report. */
uint16_t wmr_switch_build_subcommand(uint8_t counter, uint8_t subcommand,
                                     const uint8_t *args, uint8_t args_len,
                                     uint8_t out[WMR_SWITCH_OUTPUT_FRAME_SIZE]);

/* ---- Handshake ---------------------------------------------------------- */

typedef enum {
    WMR_SWITCH_HS_IDLE = 0,     /* not started, or aborted */
    WMR_SWITCH_HS_IMU,          /* 0x40 0x01: enable the 6-axis sensor */
    WMR_SWITCH_HS_CAL_IMU,      /* 0x10 @ 0x6020, 24 bytes */
    WMR_SWITCH_HS_CAL_STICK,    /* 0x10 @ 0x603D, 18 bytes */
    WMR_SWITCH_HS_MODE,         /* 0x03 0x30: 60 Hz full input reports */
    WMR_SWITCH_HS_DONE,
    WMR_SWITCH_HS_FAILED
} wmr_switch_hs_step_t;

typedef struct {
    wmr_switch_hs_step_t step;
    uint8_t counter;
    uint8_t tries;              /* sends of the current step */
    bool    waiting;            /* report emitted, reply not seen yet */
    uint16_t waited_ms;         /* time spent waiting for the current reply */
    uint8_t blob[WMR_SWITCH_CAL_SIZE];
} wmr_switch_handshake_t;

void wmr_switch_handshake_init(wmr_switch_handshake_t *hs);

/* Next report to send, or false while waiting for a reply / after DONE / after FAILED.
 * The caller sends `out[0]` as the HID report ID and `out[1..len-1]` as its data. */
bool wmr_switch_handshake_next(wmr_switch_handshake_t *hs,
                               uint8_t out[WMR_SWITCH_OUTPUT_FRAME_SIZE], uint16_t *out_len);

/* Feed one 0x21 reply frame. Returns true when it belonged to this handshake and was
 * consumed, false for any other frame. A NACK or a short SPI reply retries the step;
 * WMR_SWITCH_HS_MAX_TRIES exhausted sets FAILED. */
bool wmr_switch_handshake_on_reply(wmr_switch_handshake_t *hs,
                                   const uint8_t *frame, uint16_t length);

/* Advance the reply wait by `elapsed_ms`. A wait longer than
 * WMR_SWITCH_HS_REPLY_TIMEOUT_MS retries the step (and sets FAILED after the last try).
 * Call it from the caller's timer only while a report is waiting. */
void wmr_switch_handshake_tick(wmr_switch_handshake_t *hs, uint16_t elapsed_ms);

bool wmr_switch_handshake_ready(const wmr_switch_handshake_t *hs);
bool wmr_switch_handshake_failed(const wmr_switch_handshake_t *hs);

/* Stop the handshake (a disconnect). No reports are emitted until init is called again. */
void wmr_switch_handshake_abort(wmr_switch_handshake_t *hs);

/* Calibration blob for FF13: the 24 IMU bytes then the 18 stick bytes, exactly as read.
 * Valid only when the handshake is DONE. */
const uint8_t *wmr_switch_handshake_blob(const wmr_switch_handshake_t *hs, uint32_t *total);

/* ---- Calibration -------------------------------------------------------- */

typedef struct {
    bool     valid;                       /* IMU calibration usable */
    int16_t  acc_origin[3];
    int16_t  acc_coeff[3];
    int16_t  gyro_origin[3];
    int16_t  gyro_coeff[3];
    bool     stick_valid[WMR_NUM_CONTROLLERS];
    uint16_t stick_center[WMR_NUM_CONTROLLERS][2];
    uint16_t stick_min[WMR_NUM_CONTROLLERS][2];
    uint16_t stick_max[WMR_NUM_CONTROLLERS][2];
} wmr_switch_calibration_t;

/* Parse the 24-byte IMU and 18-byte stick blocks as read from SPI. The stick
 * groups hold deltas around the centre; stick_min/stick_max receive the absolute
 * wire values. Out-of-range values leave the affected section invalid instead of
 * making up axes; the decoder then uses the documented default scales. */
void wmr_switch_calibration_parse(const uint8_t imu[WMR_SWITCH_CAL_IMU_LEN],
                                  const uint8_t stick[WMR_SWITCH_CAL_STICK_LEN],
                                  wmr_switch_calibration_t *out);

/* Copy the two raw blocks into the FF13 blob (IMU first, then stick). */
void wmr_switch_calibration_pack(const uint8_t imu[WMR_SWITCH_CAL_IMU_LEN],
                                 const uint8_t stick[WMR_SWITCH_CAL_STICK_LEN],
                                 uint8_t out[WMR_SWITCH_CAL_SIZE]);

/* Per-hand calibration used by the decoders. `hand` is WMR_HAND_LEFT/RIGHT. A hand with
 * no calibration decodes with the default scales and leaves CALIBRATED clear. */
void wmr_switch_set_calibration(unsigned hand, const wmr_switch_calibration_t *cal);
const wmr_switch_calibration_t *wmr_switch_get_calibration(unsigned hand);

/* ---- Input report 0x30 -------------------------------------------------- */

/* True for a 50-byte captured frame: 0xA1, report ID 0x30, 49-byte report. */
bool wmr_switch_is_input_report(const uint8_t *frame, uint16_t length);

/* True for a captured subcommand reply: 0xA1, report ID 0x21. */
bool wmr_switch_is_reply_report(const uint8_t *frame, uint16_t length);

/* Decode all three IMU samples of a 0x30 report, oldest first, into packed FF12 records.
 * Returns the number written (0 when the frame is not a 0x30 report, hand >= 2, or the
 * frame is shorter than 50 bytes, or `max` is 0). Each record carries the same sequence
 * number as the raw frame; rx_time_us is stepped back 5 ms per older sample. */
unsigned wmr_switch_decode_samples(const wmr_frame_t *frame,
                                   uint8_t out[][WMR_DECODED_SIZE], unsigned max);

#endif /* WMR_SWITCH_H */
