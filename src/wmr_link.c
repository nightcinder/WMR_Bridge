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

/* wmr_link.c - BLE connection-parameter helpers; see wmr_link.h. */

#define BTSTACK_FILE__ "wmr_link.c"

#include <stdio.h>
#include "wmr_link.h"

uint32_t wmr_conn_interval_us(uint16_t interval_units){
    return (uint32_t) interval_units * 1250u;
}

uint32_t wmr_conn_timeout_ms(uint16_t timeout_units){
    return (uint32_t) timeout_units * 10u;
}

bool wmr_conn_request_valid(const wmr_conn_request_t *r){
    if (!r) return false;
    if (r->interval_min < 6 || r->interval_max > 3200 || r->interval_min > r->interval_max) return false;
    if (r->latency > 499) return false;
    if (r->timeout < 10 || r->timeout > 3200) return false;
    /* timeout_ms > (1 + latency) * max_interval_ms * 2, in microseconds. */
    uint64_t timeout_us = (uint64_t) r->timeout * 10000u;
    uint64_t needed_us = (uint64_t) (1u + r->latency) * wmr_conn_interval_us(r->interval_max) * 2u;
    return timeout_us > needed_us;
}

bool wmr_conn_params_satisfy(const wmr_conn_params_t *granted, const wmr_conn_request_t *wanted){
    if (!granted || !wanted) return false;
    return granted->interval <= wanted->interval_max && granted->latency <= wanted->latency;
}

void wmr_conn_format(const wmr_conn_params_t *p, char *buf, size_t len){
    if (!buf || !len) return;
    uint32_t us = wmr_conn_interval_us(p->interval);
    snprintf(buf, len, "%lu.%02lums lat=%u to=%lums",
             (unsigned long) (us / 1000u), (unsigned long) ((us % 1000u) / 10u),
             (unsigned) p->latency, (unsigned long) wmr_conn_timeout_ms(p->timeout));
}
