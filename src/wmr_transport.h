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
 * wmr_transport.h - BLE peripheral side: advertising, the FF10 GATT service
 * (FF11 raw stream, FF12 decoded stream, FF14 batched stream, FF15 echo), the frame
 * queue and the notification pump.
 */

#ifndef WMR_TRANSPORT_H
#define WMR_TRANSPORT_H

#include <stdint.h>
#include <stdbool.h>
#include "wmr_echo.h"
#include "wmr_stats.h"
#include "wmr_link.h"
#include "wmr_stream_mode.h"

typedef struct {
    bool     phone_connected;
    bool     subscribed;       /* phone enabled FF11 or FF12 notifications */
    wmr_stream_t stream;       /* which one (NONE, RAW or DECODED) */
    uint16_t mtu;              /* negotiated ATT MTU; 0 if no phone */
    unsigned queue_count;
    unsigned queue_capacity;
    unsigned queue_high_water;
    bool     conn_known;       /* connection parameters have been reported */
    wmr_conn_params_t conn;    /* interval/latency/timeout currently in use */
    bool     conn_update_pending;   /* requested, no update event received yet */
} wmr_transport_info_t;

/* Init ATT server, advertising data, timers; register the ATT handler. */
void wmr_transport_init(void);

/* HCI events: advertising start, LE connect/disconnect of the phone. */
void wmr_transport_on_hci_event(uint8_t *packet, uint16_t size);

/* SM events: pairing/re-encryption results and the Just Works confirmation. */
void wmr_transport_on_sm_event(uint8_t *packet, uint16_t size);

/* Report sink for the controller module. Assigns the per-hand sequence
 * number, then queues the frame or counts why it was not queued. */
void wmr_transport_submit_report(unsigned hand, const uint8_t *report, uint16_t length);

const wmr_stats_t *wmr_transport_stats(void);
void wmr_transport_get_info(wmr_transport_info_t *info);
/* Print the last LE data-length-change values seen on the phone link (LEVERS `t`). */
void wmr_transport_print_dle(void);
/* FF15 echo counters and pending state. */
const wmr_echo_state_t *wmr_transport_echo(void);

#endif /* WMR_TRANSPORT_H */
