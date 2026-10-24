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
 * Host tests for the status-LED colour mapping. The WS2812 driver needs the ESP32's RMT
 * peripheral and is not exercised here; on the host the init/show calls are stubs.
 */

#include <stdio.h>
#include <string.h>
#include "wmr_status_led.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static void test_mapping(void){
    printf("controller count to colour\n");
    CHECK(wmr_status_led_color_for_connected(0) == WMR_STATUS_LED_RED);
    CHECK(wmr_status_led_color_for_connected(1) == WMR_STATUS_LED_GREEN);
    CHECK(wmr_status_led_color_for_connected(2) == WMR_STATUS_LED_BLUE);
    CHECK(wmr_status_led_color_for_connected(3) == WMR_STATUS_LED_BLUE);
}

static void test_names(void){
    printf("colour names\n");
    CHECK(strcmp(wmr_status_led_color_name(WMR_STATUS_LED_RED), "red") == 0);
    CHECK(strcmp(wmr_status_led_color_name(WMR_STATUS_LED_GREEN), "green") == 0);
    CHECK(strcmp(wmr_status_led_color_name(WMR_STATUS_LED_BLUE), "blue") == 0);
    CHECK(strcmp(wmr_status_led_color_name(WMR_STATUS_LED_COUNT), "?") == 0);
}

static void test_host_stubs(void){
    printf("host stubs\n");
    wmr_status_led_init();
    wmr_status_led_show(WMR_STATUS_LED_RED);
    wmr_status_led_show(WMR_STATUS_LED_GREEN);
}

int main(void){
    test_mapping();
    test_names();
    test_host_stubs();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
