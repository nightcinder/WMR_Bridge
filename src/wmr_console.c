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
#include "esp_timer.h"
#include "wmr_cfgmgr.h"
#include "wmr_console.h"
#include "wmr_controller.h"
#include "wmr_diag.h"
#include "wmr_linkdiag.h"
#include "wmr_link.h"
#include "wmr_transport.h"

static btstack_timer_source_t stats_timer;
static uint32_t last_rx[WMR_NUM_CONTROLLERS];
static uint32_t last_tx[WMR_NUM_CONTROLLERS];
static uint32_t last_missing[WMR_NUM_CONTROLLERS];

void wmr_console_show_usage(void){
    printf("\nWMR bridge console\n");
    printf("l (or c): connect LEFT        r: connect RIGHT\n");
    printf("L: disconnect LEFT            R: disconnect RIGHT\n");
    printf("s: status and statistics      ?: help\n");
    printf("d: discovery - find controllers without an address by name (pairing mode)\n");
    printf("F: FORGET stored controller addresses (not link keys), then run discovery\n");
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
        char address[18] = "unknown";
        if (s.address_source != WMR_ADDR_NONE)
            snprintf(address, sizeof(address), "%02X:%02X:%02X:%02X:%02X:%02X", s.address[0], s.address[1],
                     s.address[2], s.address[3], s.address[4], s.address[5]);
        printf("%s cid=%u connected=%u descriptor=%u reports=%lu other=%lu address=%s (%s)\n",
               wmr_controller_name(i), wmr_controller_cid(i), s.connected,
               s.descriptor_available, (unsigned long) s.reports, (unsigned long) s.other_reports, address,
               wmr_controller_address_source_name(s.address_source));
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
    printf("STREAM phone=%u sub=%u stream=%s mtu=%u conn=[%s]%s q=%u/%u high=%u notify=%lu errors=%lu bridge_hands=%d\n",
           info.phone_connected, info.subscribed, wmr_stream_mode_name(info.stream), info.mtu,
           conn, info.conn_update_pending ? " (update requested, not granted yet)" : "",
           info.queue_count, info.queue_capacity, info.queue_high_water,
           (unsigned long) st->notifications, (unsigned long) st->errors, WMR_CFG_BRIDGE_HANDS);
    char batch_mean[16];
    wmr_diag_batch_mean_format(st->records_in_batches, st->batches_tx, batch_mean, sizeof(batch_mean));
    printf("  FF14 batches_tx=%lu records_in_batches=%lu mean=%s max=%lu batch_full_flushes=%lu\n",
           (unsigned long) st->batches_tx, (unsigned long) st->records_in_batches,
           batch_mean[0] ? batch_mean : "n/a", (unsigned long) st->batch_max,
           (unsigned long) st->batch_full_flushes);
    const wmr_echo_state_t *echo = wmr_transport_echo();
    printf("  FF15 echo accepted=%lu sent=%lu pending=%u busy=%lu bad=%lu unsubscribed=%lu cancelled=%lu\n",
           (unsigned long) echo->accepted, (unsigned long) echo->sent, echo->pending ? 1u : 0u,
           (unsigned long) echo->busy, (unsigned long) echo->bad,
           (unsigned long) echo->unsubscribed, (unsigned long) echo->cancelled);
    wmr_cfgmgr_print();
    printf("  vhci_drop=%lu log_errors=%lu\n",
           (unsigned long) wmr_linkdiag_vhci_drops(), (unsigned long) wmr_linkdiag_log_errors());
#if WMR_CFG_FF12_DECIMATE > 1
    printf("  DIAGNOSTIC BUILD: FF12 sends every %dth report per hand (WMR_CFG_FF12_DECIMATE)\n",
           WMR_CFG_FF12_DECIMATE);
#endif
    const double seconds = WMR_CFG_STATS_INTERVAL_MS / 1000.0;
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        wmr_linkdiag_hand_t ld;
        wmr_linkdiag_get_hand(i, &ld);
        wmr_controller_status_t cs;
        wmr_controller_get_status(i, &cs);
        /* Rates and the interval figures are deltas, so only the periodic call prints
         * them and moves the baselines; `s` leaves both alone. */
        char rates[64] = "";
        char last5s[16] = "";
        char rate_rx_copy[24] = "";
        wmr_linklog_t rec;
        char log_line[160] = "";
        if (periodic){
            const double rate_rx = (st->rx[i] - last_rx[i]) / seconds;
            snprintf(rates, sizeof(rates), " rate_rx=%.1f/s rate_tx=%.1f/s", rate_rx,
                     (st->tx[i] - last_tx[i]) / seconds);
            const int interval_permille = wmr_diag_interval_permille(last_missing[i], ld.missing,
                                                                     last_rx[i], st->rx[i]);
            wmr_diag_percent_format(interval_permille, last5s, sizeof(last5s));
            rec.t_ms             = (uint32_t) (esp_timer_get_time() / 1000);
            rec.hand             = wmr_controller_name(i);
            rec.rate_rx_tenths   = (int32_t) (((uint64_t) (st->rx[i] - last_rx[i]) * 10000u +
                                               WMR_CFG_STATS_INTERVAL_MS / 2u) / WMR_CFG_STATS_INTERVAL_MS);
            rec.missing_delta    = ld.missing >= last_missing[i] ? (int64_t) (ld.missing - last_missing[i]) : -1;
            rec.missing_cum      = ld.missing;
            rec.last5s_permille  = interval_permille;
            rec.cum_permille     = (int) ld.loss_permille;
            rec.health           = ld.connected ? &ld.health : NULL;
            rec.stream           = wmr_stream_mode_name(info.stream);
            rec.mtu              = info.mtu;
            rec.conn_interval_us = info.conn_known ? wmr_conn_interval_us(info.conn.interval) : 0;
            rec.decimated        = st->decimated[i];
            rec.batches_tx          = st->batches_tx;
            rec.records_in_batches  = st->records_in_batches;
            rec.batch_max           = st->batch_max;
            rec.batch_full_flushes  = st->batch_full_flushes;
            wmr_linklog_format(&rec, log_line, sizeof(log_line));
            snprintf(rate_rx_copy, sizeof(rate_rx_copy), " rate_rx=%.1f/s", rate_rx);
            last_rx[i] = st->rx[i];
            last_tx[i] = st->tx[i];
            last_missing[i] = ld.missing;
        }
        printf("  %s rx=%lu tx=%lu drop=%lu offline=%lu purged=%lu mtu_skip=%lu dec_err=%lu decimated=%lu other=%lu%s\n",
               wmr_controller_name(i), (unsigned long) st->rx[i],
               (unsigned long) st->tx[i], (unsigned long) st->drop[i],
               (unsigned long) st->offline[i], (unsigned long) st->purged[i],
               (unsigned long) st->mtu_skipped[i], (unsigned long) st->decode_errors[i],
               (unsigned long) st->decimated[i], (unsigned long) cs.other_reports, rates);
        char health[64];
        wmr_link_health_format(&ld.health, health, sizeof(health));
        unsigned long period_us = (unsigned long) ld.period_ticks / 10ul;      /* 100 ns ticks */
        /* last5s = missing share of this interval, cum = since the link was created; shown
         * next to rate_rx so a drift is visible without arithmetic. */
        printf("  %s link: %s mode=%s%s dev_missing=%lu last5s=%s cum=%lu.%lu%% period=%lu.%02lums discont=%lu\n",
               wmr_controller_name(i), ld.connected ? health : "n/a (no ACL link)", wmr_mode_name(ld.mode),
               rate_rx_copy, (unsigned long) ld.missing, periodic ? last5s : "-",
               (unsigned long) (ld.loss_permille / 10), (unsigned long) (ld.loss_permille % 10),
               period_us / 1000ul, (period_us % 1000ul) / 10ul, (unsigned long) ld.discontinuities);
        if (periodic){
            /* Machine-readable copy of the same numbers, for pasting into a script. */
            static bool header_printed;
            if (!header_printed){
                printf("%s\n", WMR_LINKLOG_HEADER);
                header_printed = true;
            }
            printf("%s\n", log_line);
        }
    }
}

static void stats_tick(btstack_timer_source_t *timer){
    print_stats(true);
    btstack_run_loop_set_timer(timer, WMR_CFG_STATS_INTERVAL_MS);
    btstack_run_loop_add_timer(timer);
}

#ifdef HAVE_BTSTACK_STDIN
static void forget_addresses(void){
    if (wmr_controller_forget_addresses() != WMR_FORGET_OK){
        printf("FORGET refused: disconnect both controllers (L, R) and wait for discovery or "
               "connection attempts to end\n");
        return;
    }
    printf("FORGET: stored controller addresses deleted. Put the controllers in pairing mode.\n");
    wmr_controller_discover();
}

static void bond_reset(unsigned hand){
    wmr_bond_reset_result_t result = wmr_controller_forget_bond(hand);
    if (result == WMR_BOND_RESET_NO_ADDRESS){
        printf("BOND RESET: %s has no address yet; nothing to delete\n", wmr_controller_name(hand));
        return;
    }
    if (result == WMR_BOND_RESET_OK){
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
        case 'd': wmr_controller_discover(); break;
        case 'F': forget_addresses(); break;
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
