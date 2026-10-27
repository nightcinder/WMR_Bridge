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

/* wmr_levers.c - see wmr_levers.h. */

#include "wmr_levers.h"

#if WMR_CFG_TEST_LEVERS
static unsigned stagger_mode   = WMR_CFG_STAGGER_FIRST_HAND;
static bool     interval_30ms;
static bool     hold_2;
static unsigned tx_mode        = WMR_LEVERS_TX_DEFAULT;
#endif

void wmr_levers_reset(void){
#if WMR_CFG_TEST_LEVERS
    stagger_mode  = WMR_CFG_STAGGER_FIRST_HAND;
    interval_30ms = false;
    hold_2        = false;
    tx_mode       = WMR_LEVERS_TX_DEFAULT;
#endif
}

unsigned wmr_levers_stagger(void){
#if WMR_CFG_TEST_LEVERS
    return stagger_mode;
#else
    return WMR_CFG_STAGGER_FIRST_HAND;
#endif
}

unsigned wmr_levers_stagger_cycle(void){
#if WMR_CFG_TEST_LEVERS
    stagger_mode = (stagger_mode + 1u) % 3u;
#endif
    return wmr_levers_stagger();
}

unsigned wmr_levers_interval_min(void){
#if WMR_CFG_TEST_LEVERS
    return interval_30ms ? 24u : WMR_CFG_CONN_INTERVAL_MIN;
#else
    return WMR_CFG_CONN_INTERVAL_MIN;
#endif
}

unsigned wmr_levers_interval_max(void){
#if WMR_CFG_TEST_LEVERS
    return interval_30ms ? 24u : WMR_CFG_CONN_INTERVAL_MAX;
#else
    return WMR_CFG_CONN_INTERVAL_MAX;
#endif
}

bool wmr_levers_interval_30ms(void){
#if WMR_CFG_TEST_LEVERS
    return interval_30ms;
#else
    return false;
#endif
}

bool wmr_levers_interval_toggle(void){
#if WMR_CFG_TEST_LEVERS
    interval_30ms = !interval_30ms;
#endif
    return wmr_levers_interval_30ms();
}

unsigned wmr_levers_hold_intervals(void){
#if WMR_CFG_TEST_LEVERS
    return hold_2 ? 2u : (unsigned) WMR_CFG_BATCH_HOLD_INTERVALS;
#else
    return (unsigned) WMR_CFG_BATCH_HOLD_INTERVALS;
#endif
}

bool wmr_levers_hold_2(void){
#if WMR_CFG_TEST_LEVERS
    return hold_2;
#else
    return WMR_CFG_BATCH_HOLD_INTERVALS == 2;
#endif
}

bool wmr_levers_hold_toggle(void){
#if WMR_CFG_TEST_LEVERS
    hold_2 = !hold_2;
#endif
    return wmr_levers_hold_2();
}

unsigned wmr_levers_tx(void){
#if WMR_CFG_TEST_LEVERS
    return tx_mode;
#else
    return WMR_LEVERS_TX_DEFAULT;
#endif
}

unsigned wmr_levers_tx_cycle(void){
#if WMR_CFG_TEST_LEVERS
    tx_mode = (tx_mode + 1u) % WMR_LEVERS_TX_COUNT;
#endif
    return wmr_levers_tx();
}

const char *wmr_levers_tx_name(unsigned mode){
    switch (mode){
        case WMR_LEVERS_TX_P6: return "P6";
        case WMR_LEVERS_TX_P9: return "P9";
        default:               return "default";
    }
}

const char *wmr_levers_interval_name(void){
    return wmr_levers_interval_30ms() ? "30ms" : "15ms";
}
