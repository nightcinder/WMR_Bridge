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
 * wmr_status_led.h - one addressable RGB LED showing which controllers are connected:
 * red = none, yellow = left only, blue = right only, green = both. The driver and the
 * poll timer exist only on the ESP32 build; the colour mapping is plain C so the host
 * tests can check it.
 */

#ifndef WMR_STATUS_LED_H
#define WMR_STATUS_LED_H

#include <stdbool.h>

typedef enum {
    WMR_STATUS_LED_RED = 0,
    WMR_STATUS_LED_YELLOW,
    WMR_STATUS_LED_BLUE,
    WMR_STATUS_LED_GREEN,
    WMR_STATUS_LED_COUNT
} wmr_status_led_color_t;

/* Red for none, yellow for left only, blue for right only, green for both. A
 * single-hand build only ever shows red or its hand's colour. */
wmr_status_led_color_t wmr_status_led_color_for_hands(bool left, bool right);

const char *wmr_status_led_color_name(wmr_status_led_color_t color);

/* True when `gpio` can carry the LED frame: an output-capable ESP32 GPIO that is not
 * reserved for the flash (6..11) or the console UART (1, 3). Input-only pins 34..39 fail. */
bool wmr_status_led_gpio_allowed(int gpio);

/* Set up the LED (when WMR_CFG_STATUS_LED is 1) and start polling the controller
 * status every WMR_CFG_STATUS_LED_POLL_MS. On the host this is a no-op. */
void wmr_status_led_init(void);

/* Show one colour. Writes the LED only when it differs from the current colour. */
void wmr_status_led_show(wmr_status_led_color_t color);

#endif /* WMR_STATUS_LED_H */
