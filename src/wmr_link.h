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
 * wmr_link.h - BLE connection-parameter helpers: unit conversion, validation of
 * a parameter request, and formatting for the statistics line.
 *
 * Pure C, no BTstack dependency, host-tested. Units are the Bluetooth ones:
 * interval in 1.25 ms, supervision timeout in 10 ms.
 */

#ifndef WMR_LINK_H
#define WMR_LINK_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

typedef struct {
    uint16_t interval;      /* 1.25 ms units */
    uint16_t latency;       /* connection events the peripheral may skip */
    uint16_t timeout;       /* 10 ms units */
} wmr_conn_params_t;

typedef struct {
    uint16_t interval_min;  /* 1.25 ms units */
    uint16_t interval_max;
    uint16_t latency;
    uint16_t timeout;       /* 10 ms units */
} wmr_conn_request_t;

/* Interval in microseconds / timeout in milliseconds. */
uint32_t wmr_conn_interval_us(uint16_t interval_units);
uint32_t wmr_conn_timeout_ms(uint16_t timeout_units);

/* Is this a request the Bluetooth specification allows? Interval 6..3200 with
 * min <= max, latency 0..499, timeout 10..3200, and the timeout longer than
 * twice the effective interval: timeout_ms > (1 + latency) * max_interval_ms * 2. */
bool wmr_conn_request_valid(const wmr_conn_request_t *r);

/* Do the granted parameters satisfy what was asked for? True if the interval is
 * no longer than the requested maximum and the latency no larger than requested. */
bool wmr_conn_params_satisfy(const wmr_conn_params_t *granted, const wmr_conn_request_t *wanted);

/* "11.25ms lat=0 to=4000ms" into buf (always NUL terminated for len > 0). */
void wmr_conn_format(const wmr_conn_params_t *p, char *buf, size_t len);

#endif /* WMR_LINK_H */
