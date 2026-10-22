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
 * wmr_cfg.h - controller config block (FF13, PROPOSED): HID block-read commands, response
 * parsing, block reassembly, CRC-32, and the FF13 window format served to the phone.
 *
 * Pure C, no BTstack dependency, host-tested. Specified in docs/PROTOCOL.md ("Controller
 * config block, FF13"). NOT wired into the GATT database or the HID handler, and nothing here
 * has been run against a controller or a phone: the command layout comes from Monado and is
 * unverified on this hardware.
 */

#ifndef WMR_CFG_H
#define WMR_CFG_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define WMR_CFG_CMD_SIZE        64      /* command report: 0x06, cmd, block, addr u32 LE, zeros */
#define WMR_CFG_RESP_SIZE       78      /* response report starting at its report ID */
#define WMR_CFG_RESP_DATA_MAX   68
#define WMR_CFG_CMD_BLOCK_READ  0x02
#define WMR_CFG_ADDR_FIRST      0xFFFFFFFFu
#define WMR_CFG_MAX_BLOCK       16384   /* hard upper bound on a block; larger is FAILED */
#define WMR_CFG_MAX_RETRIES     3
#define WMR_CFG_TIMEOUT_MS      250
#define WMR_CFG_SPACING_MS      10

#define WMR_CFG_HID_DATA_HEADER 0xA1    /* byte 0 of what BTstack delivers for an input report */

/* ---- CRC-32 (IEEE 802.3, reflected, poly 0xEDB88320, init and final XOR 0xFFFFFFFF) ---- */
uint32_t wmr_cfg_crc32(const uint8_t *data, size_t len);

/* ---- Report classification (done before anything is counted or queued) ------------ */
typedef enum {
    WMR_CFG_REPORT_INPUT = 0,   /* report ID 0x01: existing FF11/FF12 path */
    WMR_CFG_REPORT_CONFIG,      /* report ID 0x02 or 0x06: config reader only */
    WMR_CFG_REPORT_OTHER        /* anything else, including too short to carry an ID */
} wmr_cfg_report_class_t;

/* `data` is what BTstack delivers: byte 0 is the 0xA1 header, byte 1 the report ID. Never
 * classifies by length. */
wmr_cfg_report_class_t wmr_cfg_classify(const uint8_t *data, uint16_t size);

/* ---- Commands and responses --------------------------------------------------------- */
void wmr_cfg_pack_read_request(uint8_t block, uint32_t addr, uint8_t out[WMR_CFG_CMD_SIZE]);

/* The two commands Monado sends after its config reads to put the controller back into
 * streaming ("06 03 01 00 02" then "06 03 02 E1 02", rest zero). Step 0 or 1; other values
 * are treated as 1. Layout taken from the FF13 design notes; the bytes are unverified here. */
#define WMR_CFG_ENABLE_STEPS    2
void wmr_cfg_pack_enable_streaming(unsigned step, uint8_t out[WMR_CFG_CMD_SIZE]);

typedef struct {
    uint8_t        report_id;
    uint8_t        command;
    uint8_t        block;
    uint32_t       blk_remain;  /* bytes of the block not yet delivered */
    uint8_t        len;         /* data bytes in this response */
    const uint8_t *data;        /* points into the caller's buffer */
} wmr_cfg_response_t;

/* `buf` starts at the response report ID (no 0xA1). Requires size == 78, report ID 0x02 or
 * 0x06, and len <= 68. */
bool wmr_cfg_parse_response(const uint8_t *buf, uint16_t size, wmr_cfg_response_t *out);

/* ---- Reading one block -------------------------------------------------------------- */
typedef enum { WMR_CFG_IDLE = 0, WMR_CFG_WAIT_FIRST, WMR_CFG_WAIT_MORE, WMR_CFG_DONE,
               WMR_CFG_FAILED } wmr_cfg_reader_state_t;

typedef struct {
    wmr_cfg_reader_state_t state;
    uint8_t  block;
    uint8_t *buf;               /* caller-owned */
    uint32_t cap;
    uint32_t total;             /* block size, known after the first response */
    uint32_t got;
    uint8_t  retries;           /* consecutive timeouts or mismatches on the current request */
} wmr_cfg_reader_t;

void wmr_cfg_reader_init(wmr_cfg_reader_t *r, uint8_t *buf, uint32_t cap);
void wmr_cfg_reader_start(wmr_cfg_reader_t *r, uint8_t block);

/* Build the request to send now (first request, or the retry of the current one). Returns
 * false unless waiting for a response. */
bool wmr_cfg_reader_request(const wmr_cfg_reader_t *r, uint8_t out[WMR_CFG_CMD_SIZE]);

/* Feed a parsed response. Responses for another block or command are ignored (returns
 * false, no state change). A first response with total > min(cap, WMR_CFG_MAX_BLOCK), a
 * zero-length response while bytes remain, or a remain that disagrees with bytes received
 * fails the read. Returns true when the response was consumed. */
bool wmr_cfg_reader_on_response(wmr_cfg_reader_t *r, const wmr_cfg_response_t *resp);

/* No response within WMR_CFG_TIMEOUT_MS: retry the same request, or FAILED after
 * WMR_CFG_MAX_RETRIES retries. */
void wmr_cfg_reader_on_timeout(wmr_cfg_reader_t *r);

/* ---- FF13 characteristic ------------------------------------------------------------ */
#define WMR_CFG_FF13_VERSION      1
#define WMR_CFG_FF13_HEADER_SIZE  16
#define WMR_CFG_FF13_WINDOW_MAX   496
#define WMR_CFG_FF13_CURSOR_SIZE  5

typedef enum { WMR_CFG_NOT_READ = 0, WMR_CFG_READING = 1, WMR_CFG_READY = 2,
               WMR_CFG_BLOCK_FAILED = 3 } wmr_cfg_blob_state_t;

/* Cursor write: hand u8 (0 or 1), offset u32 LE. False (ATT error 0x80) on any other length
 * or hand. */
bool wmr_cfg_parse_cursor(const uint8_t *value, uint16_t size, uint8_t *hand, uint32_t *offset);

/* Read/notify value for `hand` at `offset`. The blob and CRC are used only when state is
 * READY (otherwise total_len and crc32 are 0 and the window is empty). `out` needs
 * WMR_CFG_FF13_HEADER_SIZE + WMR_CFG_FF13_WINDOW_MAX bytes; returns the bytes written. */
size_t wmr_cfg_pack_window(uint8_t hand, wmr_cfg_blob_state_t state, const uint8_t *blob,
                           uint32_t total, uint32_t offset, uint8_t *out);

#endif /* WMR_CFG_H */
