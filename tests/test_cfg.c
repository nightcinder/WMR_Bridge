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
 * Host tests for the FF13 config-block pieces that need no BTstack: CRC-32, report
 * classification, block-read commands and responses (golden bytes), block reassembly with
 * retries and failures, and the FF13 window format.
 *
 * What this does NOT test: that a controller accepts or answers these commands (unverified),
 * the HID handler, the GATT database, timers, or anything on a radio. Not yet run on hardware.
 */

#include <stdio.h>
#include <string.h>
#include "wmr_cfg.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static void test_crc(void){
    printf("CRC-32\n");
    CHECK(wmr_cfg_crc32((const uint8_t *)"123456789", 9) == 0xCBF43926u);   /* standard check value */
    CHECK(wmr_cfg_crc32(NULL, 0) == 0);
    const uint8_t z[4] = {0, 0, 0, 0};
    CHECK(wmr_cfg_crc32(z, 4) == 0x2144DF1Cu);
}

static void test_classify(void){
    printf("report classification\n");
    const uint8_t in[3]  = {0xA1, 0x01, 0x00};
    const uint8_t c2[2]  = {0xA1, 0x02};
    const uint8_t c6[79] = {0xA1, 0x06};
    const uint8_t o[3]   = {0xA1, 0x09, 0x00};
    const uint8_t nohdr[3] = {0xA2, 0x01, 0x00};
    CHECK(wmr_cfg_classify(in, 3) == WMR_CFG_REPORT_INPUT);
    CHECK(wmr_cfg_classify(c2, 2) == WMR_CFG_REPORT_CONFIG);       /* by ID, not by length */
    CHECK(wmr_cfg_classify(c6, 79) == WMR_CFG_REPORT_CONFIG);
    CHECK(wmr_cfg_classify(o, 3) == WMR_CFG_REPORT_OTHER);
    CHECK(wmr_cfg_classify(nohdr, 3) == WMR_CFG_REPORT_OTHER);
    CHECK(wmr_cfg_classify(in, 1) == WMR_CFG_REPORT_OTHER);
    CHECK(wmr_cfg_classify(NULL, 3) == WMR_CFG_REPORT_OTHER);
}

static void test_request(void){
    printf("block-read request golden bytes\n");
    uint8_t out[WMR_CFG_CMD_SIZE];
    memset(out, 0xEE, sizeof(out));
    wmr_cfg_pack_read_request(0x02, WMR_CFG_ADDR_FIRST, out);
    uint8_t golden[WMR_CFG_CMD_SIZE] = {0x06, 0x02, 0x02, 0xFF, 0xFF, 0xFF, 0xFF};
    CHECK(memcmp(out, golden, sizeof(golden)) == 0);
    wmr_cfg_pack_read_request(0x03, 0x01020304u, out);
    uint8_t golden2[WMR_CFG_CMD_SIZE] = {0x06, 0x02, 0x03, 0x04, 0x03, 0x02, 0x01};
    CHECK(memcmp(out, golden2, sizeof(golden2)) == 0);
}

/* Build a response: block `blk`, `remain` bytes left after this one, `len` bytes from `src`. */
static void make_resp(uint8_t out[WMR_CFG_RESP_SIZE], uint8_t blk, uint32_t remain,
                      const uint8_t *src, uint8_t len){
    memset(out, 0, WMR_CFG_RESP_SIZE);
    out[0] = 0x02; out[2] = WMR_CFG_CMD_BLOCK_READ; out[4] = blk;
    out[5] = (uint8_t)remain; out[6] = (uint8_t)(remain >> 8);
    out[7] = (uint8_t)(remain >> 16); out[8] = (uint8_t)(remain >> 24);
    out[9] = len;
    memcpy(&out[10], src, len);
}

static void test_response(void){
    printf("response parsing\n");
    static const uint8_t golden[WMR_CFG_RESP_SIZE] = {
        0x02, 0x00, 0x02, 0x00, 0x02,       /* id, -, command, -, block */
        0x34, 0x12, 0x00, 0x00,             /* blk_remain 0x1234 */
        0x03,                               /* len */
        0xAA, 0xBB, 0xCC,                   /* data, rest zero */
    };
    wmr_cfg_response_t r;
    CHECK(wmr_cfg_parse_response(golden, sizeof(golden), &r));
    CHECK(r.report_id == 2 && r.command == 2 && r.block == 2 && r.blk_remain == 0x1234 &&
          r.len == 3 && r.data[0] == 0xAA && r.data[2] == 0xCC);
    CHECK(!wmr_cfg_parse_response(golden, 77, &r));
    uint8_t bad[WMR_CFG_RESP_SIZE];
    memcpy(bad, golden, sizeof(bad));
    bad[0] = 0x01;
    CHECK(!wmr_cfg_parse_response(bad, sizeof(bad), &r));       /* input report */
    memcpy(bad, golden, sizeof(bad));
    bad[9] = 69;
    CHECK(!wmr_cfg_parse_response(bad, sizeof(bad), &r));       /* len beyond 68 */
}

static void feed(wmr_cfg_reader_t *r, const uint8_t *blob, uint32_t total, uint8_t blk){
    /* Play the controller: answer each request with up to 68 bytes. */
    for (int guard = 0; guard < 1000 && (r->state == WMR_CFG_WAIT_FIRST || r->state == WMR_CFG_WAIT_MORE); guard++){
        uint8_t req[WMR_CFG_CMD_SIZE], resp[WMR_CFG_RESP_SIZE];
        wmr_cfg_response_t p;
        if (!wmr_cfg_reader_request(r, req)) return;
        uint32_t done = total - (r->state == WMR_CFG_WAIT_FIRST ? total : r->total - r->got);
        uint32_t left = total - done;
        uint8_t len = left > WMR_CFG_RESP_DATA_MAX ? WMR_CFG_RESP_DATA_MAX : (uint8_t)left;
        if (r->state == WMR_CFG_WAIT_MORE){
            /* the request must carry the bytes still to read */
            CHECK(req[3] == (uint8_t)left && req[4] == (uint8_t)(left >> 8));
        }
        make_resp(resp, blk, left - len, &blob[done], len);
        CHECK(wmr_cfg_parse_response(resp, sizeof(resp), &p));
        wmr_cfg_reader_on_response(r, &p);
    }
}

static void test_reader(void){
    printf("block reassembly\n");
    static uint8_t blob[1000], buf[WMR_CFG_MAX_BLOCK];
    for (unsigned i = 0; i < sizeof(blob); i++) blob[i] = (uint8_t)(i * 7 + 3);
    wmr_cfg_reader_t r;
    uint8_t req[WMR_CFG_CMD_SIZE];

    wmr_cfg_reader_init(&r, buf, sizeof(buf));
    CHECK(!wmr_cfg_reader_request(&r, req));                          /* idle */
    wmr_cfg_reader_start(&r, 2);
    CHECK(wmr_cfg_reader_request(&r, req) && req[1] == 2 && req[2] == 2 && req[3] == 0xFF);
    feed(&r, blob, sizeof(blob), 2);
    CHECK(r.state == WMR_CFG_DONE && r.total == 1000 && r.got == 1000);
    CHECK(memcmp(buf, blob, sizeof(blob)) == 0);
    CHECK(!wmr_cfg_reader_request(&r, req));

    /* single short response */
    wmr_cfg_reader_start(&r, 3);
    feed(&r, blob, 16, 3);
    CHECK(r.state == WMR_CFG_DONE && r.total == 16 && memcmp(buf, blob, 16) == 0);

    /* response for another block or command is ignored */
    uint8_t resp[WMR_CFG_RESP_SIZE];
    wmr_cfg_response_t p;
    wmr_cfg_reader_start(&r, 2);
    make_resp(resp, 3, 0, blob, 4);
    CHECK(wmr_cfg_parse_response(resp, sizeof(resp), &p));
    CHECK(!wmr_cfg_reader_on_response(&r, &p) && r.state == WMR_CFG_WAIT_FIRST);
    make_resp(resp, 2, 0, blob, 4);
    resp[2] = 0x09;
    CHECK(wmr_cfg_parse_response(resp, sizeof(resp), &p));
    CHECK(!wmr_cfg_reader_on_response(&r, &p) && r.state == WMR_CFG_WAIT_FIRST);

    /* block larger than the buffer */
    uint8_t small[32];
    wmr_cfg_reader_init(&r, small, sizeof(small));
    wmr_cfg_reader_start(&r, 2);
    make_resp(resp, 2, 100, blob, 68);
    CHECK(wmr_cfg_parse_response(resp, sizeof(resp), &p));
    CHECK(wmr_cfg_reader_on_response(&r, &p) && r.state == WMR_CFG_FAILED);

    /* the hard bound caps even a larger buffer */
    static uint8_t huge[WMR_CFG_MAX_BLOCK + 100];
    wmr_cfg_reader_init(&r, huge, sizeof(huge));
    CHECK(r.cap == WMR_CFG_MAX_BLOCK);

    /* controller reports an inconsistent remain mid-block */
    wmr_cfg_reader_init(&r, buf, sizeof(buf));
    wmr_cfg_reader_start(&r, 2);
    make_resp(resp, 2, 100, blob, 68);
    CHECK(wmr_cfg_parse_response(resp, sizeof(resp), &p));
    wmr_cfg_reader_on_response(&r, &p);
    CHECK(r.state == WMR_CFG_WAIT_MORE && r.got == 68 && r.total == 168);
    make_resp(resp, 2, 50, blob, 68);                                 /* should be 32 */
    CHECK(wmr_cfg_parse_response(resp, sizeof(resp), &p));
    wmr_cfg_reader_on_response(&r, &p);
    CHECK(r.state == WMR_CFG_FAILED);

    /* zero-length response while bytes remain */
    wmr_cfg_reader_start(&r, 2);
    make_resp(resp, 2, 100, blob, 0);
    CHECK(wmr_cfg_parse_response(resp, sizeof(resp), &p));
    wmr_cfg_reader_on_response(&r, &p);
    CHECK(r.state == WMR_CFG_FAILED);
}

static void test_timeout(void){
    printf("timeouts and retries\n");
    static uint8_t buf[256];
    wmr_cfg_reader_t r;
    wmr_cfg_reader_init(&r, buf, sizeof(buf));
    wmr_cfg_reader_start(&r, 2);
    for (int i = 0; i < WMR_CFG_MAX_RETRIES; i++){
        wmr_cfg_reader_on_timeout(&r);
        CHECK(r.state == WMR_CFG_WAIT_FIRST);
    }
    wmr_cfg_reader_on_timeout(&r);
    CHECK(r.state == WMR_CFG_FAILED);
    wmr_cfg_reader_on_timeout(&r);                                    /* no effect once failed */
    CHECK(r.state == WMR_CFG_FAILED);

    /* a response resets the retry count */
    uint8_t resp[WMR_CFG_RESP_SIZE], d[68] = {0};
    wmr_cfg_response_t p;
    wmr_cfg_reader_start(&r, 2);
    wmr_cfg_reader_on_timeout(&r);
    wmr_cfg_reader_on_timeout(&r);
    make_resp(resp, 2, 10, d, 68);
    CHECK(wmr_cfg_parse_response(resp, sizeof(resp), &p));
    wmr_cfg_reader_on_response(&r, &p);
    CHECK(r.state == WMR_CFG_WAIT_MORE && r.retries == 0);
}

static void test_ff13(void){
    printf("FF13 window golden bytes\n");
    uint8_t cur_ok[5] = {0x01, 0xF0, 0x01, 0x00, 0x00};               /* RIGHT, offset 496 */
    uint8_t hand = 9;
    uint32_t off = 0;
    CHECK(wmr_cfg_parse_cursor(cur_ok, 5, &hand, &off) && hand == 1 && off == 496);
    uint8_t cur_bad[5] = {0x02, 0, 0, 0, 0};
    CHECK(!wmr_cfg_parse_cursor(cur_bad, 5, &hand, &off));            /* invalid hand: ATT 0x80 */
    CHECK(!wmr_cfg_parse_cursor(cur_ok, 4, &hand, &off));
    CHECK(!wmr_cfg_parse_cursor(cur_ok, 6, &hand, &off));

    uint8_t blob[600], out[WMR_CFG_FF13_HEADER_SIZE + WMR_CFG_FF13_WINDOW_MAX];
    for (unsigned i = 0; i < sizeof(blob); i++) blob[i] = (uint8_t)i;
    uint32_t crc = wmr_cfg_crc32(blob, sizeof(blob));

    /* READY, first window: 496 data bytes */
    size_t n = wmr_cfg_pack_window(1, WMR_CFG_READY, blob, 600, 0, out);
    CHECK(n == 16 + 496);
    const uint8_t hdr[8] = {0x01, 0x01, 0x02, 0x00, 0x58, 0x02, 0x00, 0x00};   /* total 600 = 0x258 */
    CHECK(memcmp(out, hdr, 8) == 0);
    CHECK(out[8] == 0 && out[9] == 0 && out[10] == 0 && out[11] == 0);
    CHECK(out[12] == (uint8_t)crc && out[13] == (uint8_t)(crc >> 8) &&
          out[14] == (uint8_t)(crc >> 16) && out[15] == (uint8_t)(crc >> 24));
    CHECK(memcmp(&out[16], blob, 496) == 0);

    /* second window: the remaining 104 bytes, same whole-blob CRC */
    n = wmr_cfg_pack_window(1, WMR_CFG_READY, blob, 600, 496, out);
    CHECK(n == 16 + 104);
    CHECK(out[8] == 0xF0 && out[9] == 0x01);
    CHECK(out[12] == (uint8_t)crc);
    CHECK(memcmp(&out[16], &blob[496], 104) == 0);

    /* offset at or beyond the end: header only */
    CHECK(wmr_cfg_pack_window(1, WMR_CFG_READY, blob, 600, 600, out) == 16);
    CHECK(wmr_cfg_pack_window(1, WMR_CFG_READY, blob, 600, 5000, out) == 16);

    /* not READY: total_len and crc32 are 0, no data; also the notify form */
    static const wmr_cfg_blob_state_t states[3] = {WMR_CFG_NOT_READ, WMR_CFG_READING, WMR_CFG_BLOCK_FAILED};
    for (int i = 0; i < 3; i++){
        n = wmr_cfg_pack_window(0, states[i], blob, 600, 0, out);
        CHECK(n == 16 && out[0] == 1 && out[1] == 0 && out[2] == (uint8_t)states[i]);
        CHECK(out[4] == 0 && out[5] == 0 && out[12] == 0 && out[13] == 0 && out[14] == 0 && out[15] == 0);
    }
    CHECK(WMR_CFG_FF13_HEADER_SIZE + WMR_CFG_FF13_WINDOW_MAX == 512);   /* the ATT value limit */
}

int main(void){
    test_crc();
    test_classify();
    test_request();
    test_response();
    test_reader();
    test_timeout();
    test_ff13();
    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
