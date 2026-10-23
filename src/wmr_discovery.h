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
 * wmr_discovery.h - finding the two controllers by their Bluetooth name instead of compiled-in
 * addresses (issue #2): name matching, the record that persists the addresses, and the
 * bookkeeping of one discovery run (inquiry results, names still to ask for, the first match
 * per hand).
 *
 * Pure C, no BTstack dependency, host-tested. The BTstack side (inquiry, remote name
 * requests, storage, connecting) is in wmr_controller.c. Behaviour: docs/DISCOVERY.md.
 */

#ifndef WMR_DISCOVERY_H
#define WMR_DISCOVERY_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "wmr_config.h"

#define WMR_DISCOVERY_NAME_LEFT   "Motion controller - Left"
#define WMR_DISCOVERY_NAME_RIGHT  "Motion controller - Right"

/* Hand (WMR_HAND_LEFT / WMR_HAND_RIGHT) whose name this is, or -1. Case-insensitive, whole
 * name; trailing NUL bytes and spaces are ignored (names arrive as fixed-size, NUL-padded
 * fields). `name` need not be NUL-terminated. */
int wmr_discovery_hand_for_name(const uint8_t *name, size_t len);

/* ---- Stored addresses ---------------------------------------------------- */

#define WMR_ADDR_RECORD_VERSION  1
#define WMR_ADDR_RECORD_SIZE     16   /* version, valid bits, 2 x 6-byte address, 2 reserved */

typedef struct {
    bool    valid[WMR_NUM_CONTROLLERS];
    uint8_t address[WMR_NUM_CONTROLLERS][6];   /* Bluetooth order as BTstack prints it */
} wmr_addr_record_t;

void wmr_addr_record_pack(const wmr_addr_record_t *r, uint8_t out[WMR_ADDR_RECORD_SIZE]);

/* Returns false (and clears `out`) unless size == 16, the version is 1, no unknown valid bit
 * is set, and no valid address is all zero. */
bool wmr_addr_record_unpack(const uint8_t *in, size_t size, wmr_addr_record_t *out);

/* Parse "AA:BB:CC:DD:EE:FF" (hex, either case). Returns false on anything else. */
bool wmr_addr_parse(const char *text, uint8_t out[6]);

/* ---- One discovery run --------------------------------------------------- */

/* Devices found by inquiry whose name was not in the inquiry response; a name is requested for
 * each after the inquiry completes. Further devices are counted and skipped. */
#define WMR_DISCOVERY_MAX_PENDING  8
/* Inquiry reports a device several times; addresses already handled in this run are
 * remembered (up to this many) so each is handled and logged once. */
#define WMR_DISCOVERY_MAX_SEEN     16

typedef struct {
    uint8_t  address[6];
    uint8_t  page_scan_repetition_mode;
    uint16_t clock_offset;
} wmr_discovery_device_t;

typedef enum {
    WMR_DISC_IGNORED = 0,     /* not a controller name, or an address already handled in this run */
    WMR_DISC_MATCHED,         /* first device of a wanted hand: now the candidate */
    WMR_DISC_ALREADY_KNOWN,   /* name matches a hand that already has an address */
    WMR_DISC_DUPLICATE,       /* second device with the same hand name in this run: ignored */
    WMR_DISC_NAME_QUEUED,     /* no name yet: a remote name request will follow */
    WMR_DISC_QUEUE_FULL       /* no name yet and no room to remember it: skipped */
} wmr_disc_result_t;

typedef struct {
    bool     wanted[WMR_NUM_CONTROLLERS];    /* hands this build serves that had no address */
    bool     found[WMR_NUM_CONTROLLERS];
    int      bridge_hands;                   /* WMR_CFG_BRIDGE_HANDS for this run */
    uint8_t  found_address[WMR_NUM_CONTROLLERS][6];
    wmr_discovery_device_t pending[WMR_DISCOVERY_MAX_PENDING];
    unsigned pending_count;
    unsigned pending_next;                   /* next pending device to ask for its name */
    uint8_t  seen[WMR_DISCOVERY_MAX_SEEN][6];
    unsigned seen_count;
    /* Counters for this run. */
    unsigned results;                        /* inquiry results seen (repeats included) */
    unsigned duplicates;
    unsigned skipped;                        /* WMR_DISC_QUEUE_FULL */
} wmr_discovery_t;

/* Start a run that looks for every served hand with known[hand] == false. */
void wmr_discovery_start(wmr_discovery_t *d, const bool known[WMR_NUM_CONTROLLERS]);

/* Same, with an explicit hand selection; the host tests use this to cover all three
 * WMR_CFG_BRIDGE_HANDS values without rebuilding. */
void wmr_discovery_start_config(wmr_discovery_t *d, const bool known[WMR_NUM_CONTROLLERS],
                                int bridge_hands);

/* True while some wanted hand has no candidate yet. */
bool wmr_discovery_incomplete(const wmr_discovery_t *d);

/* An inquiry result. `name` is NULL (or len 0) when the response carried no name. */
wmr_disc_result_t wmr_discovery_on_inquiry_result(wmr_discovery_t *d, const wmr_discovery_device_t *dev,
                                                  const uint8_t *name, size_t len);

/* Next device to send a remote name request to (after the inquiry has completed), or NULL.
 * Devices are returned once each, in the order found; none while no hand is still wanted. */
const wmr_discovery_device_t *wmr_discovery_next_name_request(wmr_discovery_t *d);

/* The answer to a remote name request. A failed request is passed with name NULL. */
wmr_disc_result_t wmr_discovery_on_name(wmr_discovery_t *d, const uint8_t address[6],
                                        const uint8_t *name, size_t len);

/* ---- Incoming connection from an address that is not stored --------------- */

/* Decide on an incoming connection from an unknown address once its name is known (name NULL
 * if the name request failed). Accepted only if the name matches a hand this build serves
 * and that hand has no address: returns that hand, otherwise -1. */
int wmr_discovery_incoming_hand(const bool known[WMR_NUM_CONTROLLERS], const uint8_t *name, size_t len);

/* Same, with an explicit hand selection (host tests). */
int wmr_discovery_incoming_hand_config(const bool known[WMR_NUM_CONTROLLERS], int bridge_hands,
                                       const uint8_t *name, size_t len);

#endif /* WMR_DISCOVERY_H */
