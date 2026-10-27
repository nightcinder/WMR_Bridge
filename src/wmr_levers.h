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
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL BLUEKITCHEN
 * GMBH OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *
 * Please inquire about commercial licensing options at
 * contact@bluekitchen-gmbh.com
 *
 */

/*
 * wmr_levers.h - runtime state for the one-flash ONY-430 LEVERS image.
 *
 * WMR_CFG_TEST_LEVERS=1 replaces the compile-time contention variants with runtime
 * toggles for the four firmware levers (V1 stagger, BLE interval, FF14 hold, Classic TX
 * power). With the flag at its default 0 every getter returns the compile-time value and
 * the setters are no-ops, so the release build is unchanged.
 *
 * Pure C, no BTstack or ESP-IDF dependency, host-tested in tests/test_levers.c.
 */

#ifndef WMR_LEVERS_H
#define WMR_LEVERS_H

#include <stdbool.h>
#include "wmr_config.h"

/* TX power modes cycled by the console `p` key. */
typedef enum {
    WMR_LEVERS_TX_DEFAULT = 0,  /* restore the range read at boot (N0..P3 by default) */
    WMR_LEVERS_TX_P6      = 1,  /* request max ESP_PWR_LVL_P6 */
    WMR_LEVERS_TX_P9      = 2,  /* request max ESP_PWR_LVL_P9 */
    WMR_LEVERS_TX_COUNT
} wmr_levers_tx_t;

/* Reset every runtime lever to its compile-time default. */
void wmr_levers_reset(void);

/* V1 staggered connect: 0 = off, 1 = LEFT first, 2 = RIGHT first. */
unsigned wmr_levers_stagger(void);
unsigned wmr_levers_stagger_cycle(void);

/* Requested phone connection interval in 1.25 ms units. Default is
 * WMR_CFG_CONN_INTERVAL_MIN/MAX; the 30 ms variant requests 24..24. */
unsigned wmr_levers_interval_min(void);
unsigned wmr_levers_interval_max(void);
bool     wmr_levers_interval_30ms(void);
bool     wmr_levers_interval_toggle(void);

/* FF14 hold in connection intervals: compile-time default, or 2 when toggled. */
unsigned wmr_levers_hold_intervals(void);
bool     wmr_levers_hold_2(void);
bool     wmr_levers_hold_toggle(void);

/* Classic TX power mode: default, P6 or P9. */
unsigned wmr_levers_tx(void);
unsigned wmr_levers_tx_cycle(void);
const char *wmr_levers_tx_name(unsigned mode);

/* Human-readable request labels for the boot/`x` state line. */
const char *wmr_levers_interval_name(void);

#endif /* WMR_LEVERS_H */
