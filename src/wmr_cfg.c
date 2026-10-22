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

#include <string.h>
#include "wmr_cfg.h"

uint32_t wmr_cfg_crc32(const uint8_t *data, size_t len){
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++){
        crc ^= data[i];
        for (int b = 0; b < 8; b++){
            crc = (crc & 1u) ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
        }
    }
    return ~crc;
}

wmr_cfg_report_class_t wmr_cfg_classify(const uint8_t *data, uint16_t size){
    if (data == NULL || size < 2 || data[0] != WMR_CFG_HID_DATA_HEADER) return WMR_CFG_REPORT_OTHER;
    switch (data[1]){
        case 0x01: return WMR_CFG_REPORT_INPUT;
        case 0x02:
        case 0x06: return WMR_CFG_REPORT_CONFIG;
        default:   return WMR_CFG_REPORT_OTHER;
    }
}

static void put_u32(uint8_t *p, uint32_t v){
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

static uint32_t get_u32(const uint8_t *p){
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

void wmr_cfg_pack_read_request(uint8_t block, uint32_t addr, uint8_t out[WMR_CFG_CMD_SIZE]){
    memset(out, 0, WMR_CFG_CMD_SIZE);
    out[0] = 0x06;
    out[1] = WMR_CFG_CMD_BLOCK_READ;
    out[2] = block;
    put_u32(&out[3], addr);
}

bool wmr_cfg_parse_response(const uint8_t *buf, uint16_t size, wmr_cfg_response_t *out){
    if (buf == NULL || out == NULL || size != WMR_CFG_RESP_SIZE) return false;
    if (buf[0] != 0x02 && buf[0] != 0x06) return false;
    if (buf[9] > WMR_CFG_RESP_DATA_MAX) return false;
    out->report_id = buf[0];
    out->command = buf[2];
    out->block = buf[4];
    out->blk_remain = get_u32(&buf[5]);
    out->len = buf[9];
    out->data = &buf[10];
    return true;
}

void wmr_cfg_reader_init(wmr_cfg_reader_t *r, uint8_t *buf, uint32_t cap){
    memset(r, 0, sizeof(*r));
    r->buf = buf;
    r->cap = cap < WMR_CFG_MAX_BLOCK ? cap : WMR_CFG_MAX_BLOCK;
}

void wmr_cfg_reader_start(wmr_cfg_reader_t *r, uint8_t block){
    r->state = WMR_CFG_WAIT_FIRST;
    r->block = block;
    r->total = 0;
    r->got = 0;
    r->retries = 0;
}

bool wmr_cfg_reader_request(const wmr_cfg_reader_t *r, uint8_t out[WMR_CFG_CMD_SIZE]){
    if (r->state == WMR_CFG_WAIT_FIRST){
        wmr_cfg_pack_read_request(r->block, WMR_CFG_ADDR_FIRST, out);
        return true;
    }
    if (r->state == WMR_CFG_WAIT_MORE){
        wmr_cfg_pack_read_request(r->block, r->total - r->got, out);   /* bytes still to read */
        return true;
    }
    return false;
}

bool wmr_cfg_reader_on_response(wmr_cfg_reader_t *r, const wmr_cfg_response_t *resp){
    if (r->state != WMR_CFG_WAIT_FIRST && r->state != WMR_CFG_WAIT_MORE) return false;
    if (resp->command != WMR_CFG_CMD_BLOCK_READ || resp->block != r->block) return false;

    if (r->state == WMR_CFG_WAIT_FIRST){
        uint64_t total = (uint64_t)resp->blk_remain + resp->len;
        if (total == 0 || total > r->cap){ r->state = WMR_CFG_FAILED; return true; }
        r->total = (uint32_t)total;
    } else if (resp->blk_remain != r->total - r->got - resp->len
               || resp->len > r->total - r->got){
        r->state = WMR_CFG_FAILED;      /* the controller disagrees with what was received */
        return true;
    }
    if (resp->len == 0 && r->got < r->total){ r->state = WMR_CFG_FAILED; return true; }

    memcpy(&r->buf[r->got], resp->data, resp->len);
    r->got += resp->len;
    r->retries = 0;
    r->state = (r->got == r->total) ? WMR_CFG_DONE : WMR_CFG_WAIT_MORE;
    return true;
}

void wmr_cfg_reader_on_timeout(wmr_cfg_reader_t *r){
    if (r->state != WMR_CFG_WAIT_FIRST && r->state != WMR_CFG_WAIT_MORE) return;
    if (r->retries >= WMR_CFG_MAX_RETRIES){ r->state = WMR_CFG_FAILED; return; }
    r->retries++;
}

bool wmr_cfg_parse_cursor(const uint8_t *value, uint16_t size, uint8_t *hand, uint32_t *offset){
    if (value == NULL || size != WMR_CFG_FF13_CURSOR_SIZE || value[0] > 1) return false;
    *hand = value[0];
    *offset = get_u32(&value[1]);
    return true;
}

size_t wmr_cfg_pack_window(uint8_t hand, wmr_cfg_blob_state_t state, const uint8_t *blob,
                           uint32_t total, uint32_t offset, uint8_t *out){
    bool ready = (state == WMR_CFG_READY);
    memset(out, 0, WMR_CFG_FF13_HEADER_SIZE);
    out[0] = WMR_CFG_FF13_VERSION;
    out[1] = hand;
    out[2] = (uint8_t)state;
    put_u32(&out[4], ready ? total : 0);
    put_u32(&out[8], offset);
    put_u32(&out[12], ready ? wmr_cfg_crc32(blob, total) : 0);
    size_t n = 0;
    if (ready && offset < total){
        n = total - offset;
        if (n > WMR_CFG_FF13_WINDOW_MAX) n = WMR_CFG_FF13_WINDOW_MAX;
        memcpy(&out[WMR_CFG_FF13_HEADER_SIZE], &blob[offset], n);
    }
    return WMR_CFG_FF13_HEADER_SIZE + n;
}
