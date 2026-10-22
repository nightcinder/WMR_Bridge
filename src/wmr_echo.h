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
 * wmr_echo.h - FF15 echo characteristic: the phone writes a 4-byte ping (write without
 * response), the ESP32 answers with a 20-byte notification ahead of any queued stream data.
 * For measuring the BLE round trip under stream load (issue #1).
 *
 * Pure C, no BTstack dependency, host-tested. Specified in docs/PROTOCOL.md
 * ("Echo, FF15").
 */

#ifndef WMR_ECHO_H
#define WMR_ECHO_H

#include <stdint.h>
#include <stdbool.h>

#define WMR_ECHO_VERSION        1
#define WMR_ECHO_REQUEST_SIZE   4       /* version, reserved, ping_id u16 */
#define WMR_ECHO_RESPONSE_SIZE  20
/* 20 + 3 bytes of ATT opcode and handle: the default ATT MTU is enough. */
#define WMR_ECHO_MIN_MTU        (WMR_ECHO_RESPONSE_SIZE + 3)

typedef struct {
    uint16_t ping_id;           /* copied from the request */
    uint64_t rx_time_us;        /* esp_timer when the request was received (FF12 clock) */
    uint32_t turnaround_us;     /* from rx_time_us to just before the notify call */
    uint16_t queue_depth;       /* stream frames queued when the request arrived */
} wmr_echo_response_t;

/* Check a request value. Returns false unless size == 4 and version == 1. Byte 1 is
 * reserved and ignored. */
bool wmr_echo_parse_request(const uint8_t *value, uint16_t size, uint16_t *ping_id);

/* Serialize to exactly WMR_ECHO_RESPONSE_SIZE bytes, little-endian. */
void wmr_echo_pack(const wmr_echo_response_t *r, uint8_t out[WMR_ECHO_RESPONSE_SIZE]);

/* Receiver side. Returns false unless size == 20 and version == 1. */
bool wmr_echo_unpack(const uint8_t *value, uint16_t size, wmr_echo_response_t *out);

/* ---- One outstanding echo per connection ----------------------------------- */

typedef enum {
    WMR_ECHO_ACCEPTED = 0,      /* held for the next send opportunity */
    WMR_ECHO_BAD,               /* wrong length or version; ignored */
    WMR_ECHO_BUSY,              /* an earlier echo is still waiting; ignored */
    WMR_ECHO_UNSUBSCRIBED       /* FF15 notifications are off; ignored */
} wmr_echo_result_t;

typedef struct {
    bool     pending;
    uint16_t ping_id;
    uint64_t rx_time_us;
    uint16_t queue_depth;
    /* Counters since boot. accepted = sent + cancelled + pending. */
    uint32_t accepted;
    uint32_t sent;              /* echo notifications accepted by the BLE stack */
    uint32_t bad;
    uint32_t busy;
    uint32_t unsubscribed;
    uint32_t cancelled;         /* pending echo discarded: unsubscribe or disconnect */
} wmr_echo_state_t;

void wmr_echo_init(wmr_echo_state_t *s);

/* A request value arrived at `now_us` while `queue_depth` stream frames were queued. */
wmr_echo_result_t wmr_echo_on_write(wmr_echo_state_t *s, bool subscribed,
                                    const uint8_t *value, uint16_t size,
                                    uint64_t now_us, uint16_t queue_depth);

/* Build the response for the pending echo, stamping the turnaround at `now_us`. Returns
 * false if nothing is pending. The echo stays pending until wmr_echo_sent. */
bool wmr_echo_build(const wmr_echo_state_t *s, uint64_t now_us, uint8_t out[WMR_ECHO_RESPONSE_SIZE]);

/* The response was accepted by the BLE stack. */
void wmr_echo_sent(wmr_echo_state_t *s);

/* Drop a pending echo (FF15 unsubscribed, phone connected or disconnected). */
void wmr_echo_cancel(wmr_echo_state_t *s);

#endif /* WMR_ECHO_H */
