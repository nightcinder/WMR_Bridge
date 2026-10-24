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
 * wmr_status_led.c - WS2812 (NeoPixel) status LED on the ESP32.
 *
 * The frame is sent with the RMT peripheral: 20 MHz resolution gives 50 ns steps, so a
 * WS2812 bit is 0.40 us high + 0.85 us low (0) or 0.85 us high + 0.40 us low (1). The LED
 * is polled rather than pushed from the HID handler: the colour changes only when a
 * controller connects or disconnects, so 250 ms is fast enough and keeps the radio
 * callbacks untouched.
 */

#include <stddef.h>
#include <stdint.h>
#include "wmr_status_led.h"
#include "wmr_config.h"

/* ---- Colour mapping (also built by the host tests) ---------------------- */

wmr_status_led_color_t wmr_status_led_color_for_connected(unsigned connected){
    if (connected == 0) return WMR_STATUS_LED_RED;
    if (connected == 1) return WMR_STATUS_LED_GREEN;
    return WMR_STATUS_LED_BLUE;
}

const char *wmr_status_led_color_name(wmr_status_led_color_t color){
    switch (color){
        case WMR_STATUS_LED_RED:   return "red";
        case WMR_STATUS_LED_GREEN: return "green";
        case WMR_STATUS_LED_BLUE:  return "blue";
        default:                   return "?";
    }
}

bool wmr_status_led_gpio_allowed(int gpio){
    if (gpio < 0 || gpio > 39) return false;        /* ESP32 GPIO range */
    if (gpio >= 6 && gpio <= 11) return false;      /* SPI flash */
    if (gpio == 1 || gpio == 3) return false;       /* console UART0 */
    if (gpio >= 34) return false;                   /* input only on the ESP32 */
    return true;
}

#ifdef ESP_PLATFORM

#include <stdio.h>
#include <string.h>
#include "driver/gpio.h"
#include "driver/rmt_tx.h"
#include "esp_err.h"
#include "esp_timer.h"
#include "wmr_controller.h"

#if WMR_CFG_STATUS_LED

/* Two data lines at most: the primary pin plus the ATOM Lite's onboard LED. */
#define LED_MAX_PINS 2

/* WS2812 data, GRB order. Static because rmt_transmit reads it asynchronously. */
static uint8_t led_grb[3];

static rmt_channel_handle_t led_channels[LED_MAX_PINS];
static rmt_encoder_handle_t led_encoders[LED_MAX_PINS];
static int led_pins[LED_MAX_PINS];
static unsigned led_pin_count;
static esp_timer_handle_t led_timer;
static wmr_status_led_color_t led_color;
static bool led_color_known;
static bool led_ready;

static bool led_write(wmr_status_led_color_t color){
    const uint8_t scale = WMR_CFG_STATUS_LED_BRIGHTNESS;
    uint8_t r = 0, g = 0, b = 0;

    switch (color){
        case WMR_STATUS_LED_GREEN: g = scale; break;
        case WMR_STATUS_LED_BLUE:  b = scale; break;
        default:                   r = scale; break;
    }
    led_grb[0] = g;
    led_grb[1] = r;
    led_grb[2] = b;

    rmt_transmit_config_t tx_config = {
        .loop_count = 0,
        .flags = { .eot_level = 0 },
    };
    bool ok = true;
    for (unsigned i = 0; i < led_pin_count; i++){
        esp_err_t err = rmt_transmit(led_channels[i], led_encoders[i], led_grb,
                                     sizeof(led_grb), &tx_config);
        if (err != ESP_OK){
            printf("LED: transmit failed on GPIO %d: %s\n", led_pins[i], esp_err_to_name(err));
            ok = false;
        }
    }
    /* Leave the colour unknown on any failure so the next poll retries the same frame. */
    return ok;
}

void wmr_status_led_show(wmr_status_led_color_t color){
    if (!led_ready) return;
    if (led_color_known && color == led_color) return;
    if (!led_write(color)) return;
    led_color = color;
    led_color_known = true;
    printf("LED: %s\n", wmr_status_led_color_name(color));
}

static void led_poll(void *arg){
    (void) arg;
    unsigned connected = 0;
    for (unsigned hand = 0; hand < WMR_NUM_CONTROLLERS; hand++){
        if (!WMR_BRIDGE_HAND_ENABLED(hand)) continue;
        wmr_controller_status_t status;
        wmr_controller_get_status(hand, &status);
        if (status.connected) connected++;
    }
    wmr_status_led_show(wmr_status_led_color_for_connected(connected));
}

static bool led_add_pin(int gpio){
    if (led_pin_count >= LED_MAX_PINS) return false;

    rmt_tx_channel_config_t channel_config = {
        .gpio_num = (gpio_num_t) gpio,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 20000000,          /* 50 ns per tick */
        .mem_block_symbols = 64,            /* ESP32 requires even and >= 64 */
        .trans_queue_depth = 4,
        .intr_priority = 0,
        .flags = {
            .invert_out = 0,
            .with_dma = 0,
            .allow_pd = 0,
            .init_level = 0,
        },
    };
    rmt_channel_handle_t channel = NULL;
    esp_err_t err = rmt_new_tx_channel(&channel_config, &channel);
    if (err != ESP_OK){
        printf("LED: RMT channel init failed on GPIO %d: %s\n", gpio, esp_err_to_name(err));
        return false;
    }

    rmt_bytes_encoder_config_t encoder_config = {
        .bit0 = { .level0 = 1, .duration0 = 8,  .level1 = 0, .duration1 = 17 },
        .bit1 = { .level0 = 1, .duration0 = 17, .level1 = 0, .duration1 = 8  },
        .flags = { .msb_first = 1 },
    };
    rmt_encoder_handle_t encoder = NULL;
    err = rmt_new_bytes_encoder(&encoder_config, &encoder);
    if (err != ESP_OK){
        printf("LED: RMT encoder init failed: %s\n", esp_err_to_name(err));
        rmt_del_channel(channel);
        return false;
    }
    err = rmt_enable(channel);
    if (err != ESP_OK){
        printf("LED: RMT enable failed on GPIO %d: %s\n", gpio, esp_err_to_name(err));
        rmt_del_encoder(encoder);
        rmt_del_channel(channel);
        return false;
    }

    led_channels[led_pin_count] = channel;
    led_encoders[led_pin_count] = encoder;
    led_pins[led_pin_count] = gpio;
    led_pin_count++;
    return true;
}

void wmr_status_led_init(void){
    const int configured[LED_MAX_PINS] = { WMR_CFG_STATUS_LED_GPIO, WMR_CFG_STATUS_LED_GPIO_ALT };
    for (unsigned i = 0; i < LED_MAX_PINS; i++){
        const int gpio = configured[i];
        if (gpio < 0) continue;                     /* -1 disables this entry */
        if (!wmr_status_led_gpio_allowed(gpio)){
            printf("LED: GPIO %d is reserved or input-only; skipped\n", gpio);
            continue;
        }
        (void) led_add_pin(gpio);
    }
    if (led_pin_count == 0){
        printf("LED: no usable data pin; status LED off\n");
        return;
    }
    led_ready = true;
    printf("LED: WS2812 on GPIO");
    for (unsigned i = 0; i < led_pin_count; i++) printf(" %d", led_pins[i]);
    printf("\n");
    wmr_status_led_show(WMR_STATUS_LED_RED);

    const esp_timer_create_args_t timer_args = {
        .callback = led_poll,
        .arg = NULL,
        .name = "status_led",
    };
    esp_err_t err = esp_timer_create(&timer_args, &led_timer);
    if (err == ESP_OK){
        err = esp_timer_start_periodic(led_timer, (uint64_t) WMR_CFG_STATUS_LED_POLL_MS * 1000);
    }
    if (err != ESP_OK){
        printf("LED: poll timer failed: %s\n", esp_err_to_name(err));
    }
}

#else

void wmr_status_led_init(void){ }
void wmr_status_led_show(wmr_status_led_color_t color){ (void) color; }

#endif /* WMR_CFG_STATUS_LED */

#else

void wmr_status_led_init(void){ }
void wmr_status_led_show(wmr_status_led_color_t color){ (void) color; }

#endif /* ESP_PLATFORM */
