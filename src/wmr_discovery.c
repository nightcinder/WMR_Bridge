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

/* wmr_discovery.c - controller discovery by name; see wmr_discovery.h. */

#define BTSTACK_FILE__ "wmr_discovery.c"

#include <string.h>
#include "wmr_discovery.h"

static char lower(uint8_t c){
    return (char) (c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c);
}

static bool name_equals(const uint8_t *name, size_t len, const char *want){
    while (len && (name[len - 1] == 0 || name[len - 1] == ' ')) len--;
    if (len != strlen(want)) return false;
    for (size_t i = 0; i < len; i++){
        if (lower(name[i]) != lower((uint8_t) want[i])) return false;
    }
    return true;
}

typedef struct {
    const char *name;
    int         hand;
    wmr_model_t model;
} controller_name_t;

/* The names are matched in order. A WMR and a PSVR2 controller of the same hand therefore
 * both map to that hand; the first one found in a discovery run wins, exactly as two WMR
 * controllers of one hand already did. */
static const controller_name_t controller_names[] = {
    { WMR_DISCOVERY_NAME_LEFT,   WMR_HAND_LEFT,  WMR_MODEL_WMR   },
    { WMR_DISCOVERY_NAME_RIGHT,  WMR_HAND_RIGHT, WMR_MODEL_WMR   },
    { WMR_DISCOVERY_NAME_PSVR2_LEFT,  WMR_HAND_LEFT,  WMR_MODEL_PSVR2 },
    { WMR_DISCOVERY_NAME_PSVR2_RIGHT, WMR_HAND_RIGHT, WMR_MODEL_PSVR2 },
};

#define CONTROLLER_NAME_COUNT (sizeof(controller_names) / sizeof(controller_names[0]))

const char *wmr_model_name(wmr_model_t model){
    switch (model){
        case WMR_MODEL_WMR:   return "WMR";
        case WMR_MODEL_PSVR2: return "PSVR2";
        default:              return "?";
    }
}

/* Hand and model for a name; hand -1 and model WMR_MODEL_COUNT when unknown. */
static int identify_name(const uint8_t *name, size_t len, wmr_model_t *model){
    if (!name) return -1;
    for (unsigned i = 0; i < CONTROLLER_NAME_COUNT; i++){
        if (!name_equals(name, len, controller_names[i].name)) continue;
        if (model) *model = controller_names[i].model;
        return controller_names[i].hand;
    }
    return -1;
}

int wmr_discovery_hand_for_name(const uint8_t *name, size_t len){
    return identify_name(name, len, NULL);
}

wmr_model_t wmr_discovery_model_for_name(const uint8_t *name, size_t len){
    wmr_model_t model = WMR_MODEL_COUNT;
    if (identify_name(name, len, &model) < 0) return WMR_MODEL_COUNT;
    return model;
}

/* ---- Stored addresses ---------------------------------------------------- */

static bool all_zero(const uint8_t a[6]){
    for (unsigned i = 0; i < 6; i++) if (a[i]) return false;
    return true;
}

/* Layout: [0] version, [1] bit0 LEFT valid, bit1 RIGHT valid, [2..7] LEFT, [8..13] RIGHT,
 * [14] 2 model bits per hand, [15] reserved 0. An invalid slot is stored as zeros. */
void wmr_addr_record_pack(const wmr_addr_record_t *r, uint8_t out[WMR_ADDR_RECORD_SIZE]){
    memset(out, 0, WMR_ADDR_RECORD_SIZE);
    out[0] = WMR_ADDR_RECORD_VERSION;
    for (unsigned h = 0; h < WMR_NUM_CONTROLLERS; h++){
        if (!r->valid[h]) continue;             /* invalid slots stay all-zero, model bits included */
        unsigned model = (unsigned) r->model[h];
        out[14] = (uint8_t) (out[14] | ((model & 0x3u) << (2 * h)));
        out[1] = (uint8_t) (out[1] | (1u << h));
        memcpy(&out[2 + 6 * h], r->address[h], 6);
    }
}

bool wmr_addr_record_unpack(const uint8_t *in, size_t size, wmr_addr_record_t *out){
    memset(out, 0, sizeof(*out));
    if (!in || size != WMR_ADDR_RECORD_SIZE || in[0] != WMR_ADDR_RECORD_VERSION) return false;
    if (in[1] & ~((1u << WMR_NUM_CONTROLLERS) - 1u)) return false;
    wmr_addr_record_t r;
    memset(&r, 0, sizeof(r));
    for (unsigned h = 0; h < WMR_NUM_CONTROLLERS; h++){
        unsigned model = (in[14] >> (2 * h)) & 0x3u;
        if (model >= WMR_MODEL_COUNT) return false;
        r.model[h] = (wmr_model_t) model;
        if (!(in[1] & (1u << h))) continue;
        if (all_zero(&in[2 + 6 * h])) return false;
        r.valid[h] = true;
        memcpy(r.address[h], &in[2 + 6 * h], 6);
    }
    *out = r;
    return true;
}

static int hex_digit(char c){
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool wmr_addr_parse(const char *text, uint8_t out[6]){
    if (!text || strlen(text) != 17) return false;
    uint8_t a[6];
    for (unsigned i = 0; i < 6; i++){
        int hi = hex_digit(text[3 * i]), lo = hex_digit(text[3 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        if (i < 5 && text[3 * i + 2] != ':') return false;
        a[i] = (uint8_t) (hi * 16 + lo);
    }
    memcpy(out, a, 6);
    return true;
}

/* ---- One discovery run --------------------------------------------------- */

void wmr_discovery_start_config(wmr_discovery_t *d, const bool known[WMR_NUM_CONTROLLERS],
                                int bridge_hands){
    memset(d, 0, sizeof(*d));
    d->bridge_hands = bridge_hands;
    for (unsigned h = 0; h < WMR_NUM_CONTROLLERS; h++){
        d->wanted[h] = wmr_bridge_hand_enabled(bridge_hands, (int) h) && !known[h];
    }
}

void wmr_discovery_start(wmr_discovery_t *d, const bool known[WMR_NUM_CONTROLLERS]){
    wmr_discovery_start_config(d, known, WMR_CFG_BRIDGE_HANDS);
}

void wmr_discovery_begin_round(wmr_discovery_t *d){
    /* The per-round bookkeeping is deliberately thrown away: a device that did not answer
     * the previous inquiry, or whose remote name request failed, must be handled again in
     * this round. Matches already made (found[]) stay, so the first controller per hand
     * still wins for the whole discovery. */
    d->pending_count = 0;
    d->pending_next = 0;
    d->seen_count = 0;
}

bool wmr_discovery_reserve_radio(bool enabled, unsigned round, bool known_address){
    return enabled && round == 1 && known_address;
}

bool wmr_discovery_incomplete(const wmr_discovery_t *d){
    for (unsigned h = 0; h < WMR_NUM_CONTROLLERS; h++){
        if (d->wanted[h] && !d->found[h]) return true;
    }
    return false;
}

void wmr_discovery_hand_unusable(wmr_discovery_t *d, int hand){
    if (hand < 0 || hand >= WMR_NUM_CONTROLLERS) return;
    d->unusable[hand] = true;
}

bool wmr_discovery_hand_missing(const wmr_discovery_t *d, int hand){
    if (hand < 0 || hand >= WMR_NUM_CONTROLLERS) return false;
    return d->wanted[hand] && (!d->found[hand] || d->unusable[hand]);
}

static bool is_candidate(const wmr_discovery_t *d, const uint8_t address[6]){
    for (unsigned h = 0; h < WMR_NUM_CONTROLLERS; h++){
        if (d->found[h] && memcmp(d->found_address[h], address, 6) == 0) return true;
    }
    return false;
}

static int pending_index(const wmr_discovery_t *d, const uint8_t address[6]){
    for (unsigned i = 0; i < d->pending_count; i++){
        if (memcmp(d->pending[i].address, address, 6) == 0) return (int) i;
    }
    return -1;
}

/* A device of `hand`/`model` (hand -1 for a non-controller) was identified. */
static wmr_disc_result_t classify(wmr_discovery_t *d, const uint8_t address[6], int hand,
                                  wmr_model_t model){
    /* A hand this build does not serve is treated exactly like a non-controller device:
     * its name is ignored during inquiry and remote name requests. */
    if (!wmr_bridge_hand_enabled(d->bridge_hands, hand)) return WMR_DISC_IGNORED;
    if (!d->wanted[hand]) return WMR_DISC_ALREADY_KNOWN;
    if (d->found[hand]){
        if (memcmp(d->found_address[hand], address, 6) == 0) return WMR_DISC_IGNORED;
        d->duplicates++;
        return WMR_DISC_DUPLICATE;
    }
    d->found[hand] = true;
    d->found_model[hand] = model;
    memcpy(d->found_address[hand], address, 6);
    return WMR_DISC_MATCHED;
}

wmr_disc_result_t wmr_discovery_on_inquiry_result(wmr_discovery_t *d, const wmr_discovery_device_t *dev,
                                                  const uint8_t *name, size_t len){
    d->results++;
    /* Inquiry reports a device repeatedly; handle each address once. */
    if (is_candidate(d, dev->address) || pending_index(d, dev->address) >= 0) return WMR_DISC_IGNORED;
    for (unsigned i = 0; i < d->seen_count; i++){
        if (memcmp(d->seen[i], dev->address, 6) == 0) return WMR_DISC_IGNORED;
    }
    if (name && len){
        if (d->seen_count < WMR_DISCOVERY_MAX_SEEN) memcpy(d->seen[d->seen_count++], dev->address, 6);
        wmr_model_t model = WMR_MODEL_COUNT;
        int hand = identify_name(name, len, &model);
        return classify(d, dev->address, hand, model);
    }
    if (d->pending_count == WMR_DISCOVERY_MAX_PENDING){
        d->skipped++;
        return WMR_DISC_QUEUE_FULL;
    }
    d->pending[d->pending_count++] = *dev;
    return WMR_DISC_NAME_QUEUED;
}

const wmr_discovery_device_t *wmr_discovery_next_name_request(wmr_discovery_t *d){
    while (d->pending_next < d->pending_count && wmr_discovery_incomplete(d)){
        const wmr_discovery_device_t *dev = &d->pending[d->pending_next++];
        if (!is_candidate(d, dev->address)) return dev;
    }
    return NULL;
}

wmr_disc_result_t wmr_discovery_on_name(wmr_discovery_t *d, const uint8_t address[6],
                                        const uint8_t *name, size_t len){
    wmr_model_t model = WMR_MODEL_COUNT;
    int hand = identify_name(name, len, &model);
    return classify(d, address, hand, model);
}

int wmr_discovery_incoming_hand_config(const bool known[WMR_NUM_CONTROLLERS], int bridge_hands,
                                       const uint8_t *name, size_t len){
    int hand = wmr_discovery_hand_for_name(name, len);
    if (hand < 0 || !wmr_bridge_hand_enabled(bridge_hands, hand) || known[hand]) return -1;
    return hand;
}

int wmr_discovery_incoming_hand(const bool known[WMR_NUM_CONTROLLERS], const uint8_t *name, size_t len){
    return wmr_discovery_incoming_hand_config(known, WMR_CFG_BRIDGE_HANDS, name, len);
}
