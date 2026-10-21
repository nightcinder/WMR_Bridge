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

#define BTSTACK_FILE__ "wmr_console.c"

#include <stdio.h>
#include <stdbool.h>
#include "btstack_config.h"
#include "btstack.h"
#include "wmr_console.h"
#include "wmr_controller.h"
#include "wmr_link.h"
#include "wmr_transport.h"

static btstack_timer_source_t stats_timer;
static uint32_t last_rx[WMR_NUM_CONTROLLERS];
static uint32_t last_tx[WMR_NUM_CONTROLLERS];

void wmr_console_show_usage(void){
    printf("\nWMR bridge console\n");
    printf("l (or c): connect LEFT        r: connect RIGHT\n");
    printf("L: disconnect LEFT            R: disconnect RIGHT\n");
    printf("s: status and statistics      ?: help\n");
    printf("k: BOND RESET LEFT  - delete the ESP32's stored link key for LEFT only\n");
    printf("K: BOND RESET RIGHT - delete the ESP32's stored link key for RIGHT only\n");
    printf("   (manual; controller must be disconnected. Put it in pairing mode afterwards.)\n");
    printf("Connect one controller at a time; wait for descriptor result.\n");
}

void wmr_console_on_hci_event(uint8_t *packet, uint16_t size){
    (void)size;
    if (hci_event_packet_get_type(packet) == BTSTACK_EVENT_STATE &&
        btstack_event_state_get_state(packet) == HCI_STATE_WORKING){
        wmr_console_show_usage();
    }
}

static void print_controllers(void){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        wmr_controller_status_t s;
        wmr_controller_get_status(i, &s);
        printf("%s cid=%u connected=%u descriptor=%u reports=%lu\n",
               wmr_controller_name(i), wmr_controller_cid(i), s.connected,
               s.descriptor_available, (unsigned long) s.reports);
    }
}

/* Rates are deltas since the previous periodic call divided by the nominal
 * interval, so only the periodic call prints (and updates) them. */
static void print_stats(bool periodic){
    wmr_transport_info_t info;
    wmr_transport_get_info(&info);
    const wmr_stats_t *st = wmr_transport_stats();
    char conn[48] = "n/a";
    if (info.conn_known) wmr_conn_format(&info.conn, conn, sizeof(conn));
    printf("STREAM phone=%u sub=%u stream=%s mtu=%u conn=[%s]%s q=%u/%u high=%u notify=%lu errors=%lu\n",
           info.phone_connected, info.subscribed, wmr_stream_mode_name(info.stream), info.mtu,
           conn, info.conn_update_pending ? " (update requested, not granted yet)" : "",
           info.queue_count, info.queue_capacity, info.queue_high_water,
           (unsigned long) st->notifications, (unsigned long) st->errors);
    const double seconds = WMR_CFG_STATS_INTERVAL_MS / 1000.0;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        printf("  %s rx=%lu tx=%lu drop=%lu offline=%lu purged=%lu mtu_skip=%lu dec_err=%lu rate_rx=%.1f/s rate_tx=%.1f/s\n",
               wmr_controller_name(i), (unsigned long) st->rx[i],
               (unsigned long) st->tx[i], (unsigned long) st->drop[i],
               (unsigned long) st->offline[i], (unsigned long) st->purged[i],
               (unsigned long) st->mtu_skipped[i], (unsigned long) st->decode_errors[i],
               (st->rx[i] - last_rx[i]) / seconds,
               (st->tx[i] - last_tx[i]) / seconds);
        last_rx[i] = st->rx[i];
        last_tx[i] = st->tx[i];
    }
}

static void stats_tick(btstack_timer_source_t *timer){
    print_stats(true);
    btstack_run_loop_set_timer(timer, WMR_CFG_STATS_INTERVAL_MS);
    btstack_run_loop_add_timer(timer);
}

#ifdef HAVE_BTSTACK_STDIN
static void bond_reset(unsigned hand){
    if (wmr_controller_forget_bond(hand) == WMR_BOND_RESET_OK){
        printf("BOND RESET: removed ESP32's stored %s link key only. "
               "Put %s into pairing mode, then press %c.\n",
               wmr_controller_name(hand), wmr_controller_name(hand),
               hand == WMR_HAND_LEFT ? 'l' : 'r');
    } else {
        printf("BOND RESET refused: wait until %s is disconnected and "
               "no connection attempt is pending\n", wmr_controller_name(hand));
    }
}

static void stdin_process(char cmd){
    switch (cmd){
        case 'c': case 'l': wmr_controller_connect(WMR_HAND_LEFT); break;
        case 'r': wmr_controller_connect(WMR_HAND_RIGHT); break;
        case 'L': wmr_controller_disconnect(WMR_HAND_LEFT); break;
        case 'R': wmr_controller_disconnect(WMR_HAND_RIGHT); break;
        case 's': print_controllers(); print_stats(false); break;
        case 'k': bond_reset(WMR_HAND_LEFT); break;
        case 'K': bond_reset(WMR_HAND_RIGHT); break;
        case '\r': case '\n': break;
        default: wmr_console_show_usage(); break;
    }
}
#endif

void wmr_console_init(void){
#ifdef HAVE_BTSTACK_STDIN
    btstack_stdin_setup(stdin_process);
#endif
    stats_timer.process = stats_tick;
    btstack_run_loop_set_timer(&stats_timer, WMR_CFG_STATS_INTERVAL_MS);
    btstack_run_loop_add_timer(&stats_timer);
}
