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

/* wmr_txpower.c - see wmr_txpower.h. */

#include <stdio.h>
#include "esp_bt.h"
#include "esp_err.h"
#include "wmr_levers.h"
#include "wmr_txpower.h"

static bool            range_known;
static esp_power_level_t boot_min = ESP_PWR_LVL_N0;
static esp_power_level_t boot_max = ESP_PWR_LVL_P3;
static int             last_set_rc = -1;

static const char *level_name(esp_power_level_t level){
    switch (level){
        case ESP_PWR_LVL_N12: return "N12";
        case ESP_PWR_LVL_N9:  return "N9";
        case ESP_PWR_LVL_N6:  return "N6";
        case ESP_PWR_LVL_N3:  return "N3";
        case ESP_PWR_LVL_N0:  return "N0";
        case ESP_PWR_LVL_P3:  return "P3";
        case ESP_PWR_LVL_P6:  return "P6";
        case ESP_PWR_LVL_P9:  return "P9";
        default:               return "?";
    }
}

void wmr_txpower_init(void){
    if (range_known) return;
    esp_power_level_t min_level;
    esp_power_level_t max_level;
    esp_err_t rc = esp_bredr_tx_power_get(&min_level, &max_level);
    if (rc == ESP_OK){
        boot_min = min_level;
        boot_max = max_level;
        range_known = true;
        printf("TXPOWER: boot range min=%s max=%s\n", level_name(boot_min), level_name(boot_max));
    } else {
        printf("TXPOWER: boot read failed rc=%d\n", rc);
    }
}

void wmr_txpower_print(void){
    wmr_txpower_init();
    esp_power_level_t min_level;
    esp_power_level_t max_level;
    esp_err_t rc = esp_bredr_tx_power_get(&min_level, &max_level);
    if (rc == ESP_OK){
        printf("TXPOWER: readback min=%s max=%s last_set_rc=%d\n",
               level_name(min_level), level_name(max_level), last_set_rc);
    } else {
        printf("TXPOWER: readback failed rc=%d last_set_rc=%d\n", rc, last_set_rc);
    }
}

int wmr_txpower_apply(unsigned mode){
    wmr_txpower_init();
    esp_power_level_t min_level;
    esp_power_level_t max_level;
    switch (mode){
        case WMR_LEVERS_TX_P6:
            min_level = ESP_PWR_LVL_N0;
            max_level = ESP_PWR_LVL_P6;
            break;
        case WMR_LEVERS_TX_P9:
            min_level = ESP_PWR_LVL_N0;
            max_level = ESP_PWR_LVL_P9;
            break;
        default:
            min_level = boot_min;
            max_level = boot_max;
            break;
    }
    esp_err_t rc = esp_bredr_tx_power_set(min_level, max_level);
    last_set_rc = rc;
    printf("TXPOWER: requested %s (min=%s max=%s) set_rc=%d\n",
           wmr_levers_tx_name(mode), level_name(min_level), level_name(max_level), rc);
    wmr_txpower_print();
    return rc;
}
