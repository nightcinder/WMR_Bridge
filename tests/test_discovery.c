/*
 * Host tests for controller discovery by name (issue #2) that need no BTstack: name matching,
 * the stored address record (golden bytes), address parsing, one discovery run (inquiry
 * results with and without names, remote name requests, first match per hand, duplicates)
 * and the rule for incoming connections from unknown addresses.
 *
 * What this does NOT test: the inquiry, remote name requests, NVS storage, connecting and
 * pairing in wmr_controller.c, or anything on a radio. Real controller names have not been
 * captured through this code; the two names are the ones given in issue #2.
 */

#include <stdio.h>
#include <string.h>
#include "wmr_discovery.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static int hand(const char *s){
    return wmr_discovery_hand_for_name((const uint8_t *) s, strlen(s));
}

#define NAME(s) (const uint8_t *) (s), strlen(s)

static void test_names(void){
    printf("Name matching\n");
    CHECK(hand("Motion controller - Left") == WMR_HAND_LEFT);
    CHECK(hand("Motion controller - Right") == WMR_HAND_RIGHT);
    CHECK(hand("MOTION CONTROLLER - LEFT") == WMR_HAND_LEFT);
    CHECK(hand("motion controller - right") == WMR_HAND_RIGHT);
    CHECK(hand("Motion controller - Right  ") == WMR_HAND_RIGHT);       /* trailing spaces */
    const uint8_t padded[32] = "Motion controller - Left";             /* NUL padded field */
    CHECK(wmr_discovery_hand_for_name(padded, sizeof(padded)) == WMR_HAND_LEFT);
    /* Not NUL terminated: only `len` bytes count. */
    CHECK(wmr_discovery_hand_for_name((const uint8_t *) "Motion controller - LeftXYZ", 24) == WMR_HAND_LEFT);
    CHECK(hand("Motion controller - Lef") == -1);
    CHECK(hand("Motion controller - Leftt") == -1);
    CHECK(hand(" Motion controller - Left") == -1);                     /* leading space is not ignored */
    CHECK(hand("Motion controller") == -1);
    CHECK(hand("Xbox Wireless Controller") == -1);
    CHECK(hand("") == -1);
    CHECK(wmr_discovery_hand_for_name(NULL, 5) == -1);
#if WMR_CFG_PSVR2_SUPPORT
    CHECK(hand("PlayStation VR2 Sense Controller (L)") == WMR_HAND_LEFT);
    CHECK(hand("PlayStation VR2 Sense Controller (R)") == WMR_HAND_RIGHT);
    CHECK(hand("PLAYSTATION VR2 SENSE CONTROLLER (L)") == WMR_HAND_LEFT);
    CHECK(hand("playstation vr2 sense controller (r)  ") == WMR_HAND_RIGHT);
    CHECK(hand("PlayStation VR2 Sense Controller") == -1);              /* hand suffix is required */
    CHECK(hand("PlayStation VR2 Sense Controller (X)") == -1);
#endif
}

static void test_models(void){
    printf("Controller model by name\n");
    CHECK(wmr_model_name(WMR_MODEL_WMR)[0] == 'W');
    CHECK(strcmp(wmr_model_name(WMR_MODEL_WMR), "WMR") == 0);
#if WMR_CFG_PSVR2_SUPPORT
    CHECK(strcmp(wmr_model_name(WMR_MODEL_PSVR2), "PSVR2") == 0);
#else
    CHECK(strcmp(wmr_model_name(WMR_MODEL_PSVR2), "?") == 0);   /* no PSVR2 string in a WMR-only build */
#endif
    CHECK(strcmp(wmr_model_name(WMR_MODEL_COUNT), "?") == 0);
    CHECK(wmr_discovery_model_for_name(NAME("Motion controller - Left")) == WMR_MODEL_WMR);
    CHECK(wmr_discovery_model_for_name(NAME("Motion controller - Right")) == WMR_MODEL_WMR);
    CHECK(wmr_discovery_model_for_name(NAME("Keyboard")) == WMR_MODEL_COUNT);
    CHECK(wmr_discovery_model_for_name(NULL, 0) == WMR_MODEL_COUNT);
#if WMR_CFG_PSVR2_SUPPORT
    CHECK(wmr_discovery_model_for_name(NAME("PlayStation VR2 Sense Controller (L)")) == WMR_MODEL_PSVR2);
    CHECK(wmr_discovery_model_for_name(NAME("PlayStation VR2 Sense Controller (R)")) == WMR_MODEL_PSVR2);
#endif
}

static void test_record(void){
    printf("Stored address record golden bytes\n");
    wmr_addr_record_t r;
    memset(&r, 0, sizeof(r));
    CHECK(wmr_addr_parse("11:22:33:44:55:66", r.address[WMR_HAND_LEFT]));
    CHECK(wmr_addr_parse("aa:BB:cc:DD:ee:FF", r.address[WMR_HAND_RIGHT]));
    r.valid[0] = r.valid[1] = true;
    static const uint8_t golden_both[WMR_ADDR_RECORD_SIZE] = {
        0x01, 0x03,
        0x11, 0x22, 0x33, 0x44, 0x55, 0x66,
        0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
        0x00, 0x00,
    };
    uint8_t out[WMR_ADDR_RECORD_SIZE];
    wmr_addr_record_pack(&r, out);
    CHECK(memcmp(out, golden_both, sizeof(out)) == 0);

    /* RIGHT only: the LEFT slot is zeros. */
    r.valid[0] = false;
    static const uint8_t golden_right[WMR_ADDR_RECORD_SIZE] = {
        0x01, 0x02,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
        0x00, 0x00,
    };
    wmr_addr_record_pack(&r, out);
    CHECK(memcmp(out, golden_right, sizeof(out)) == 0);

    wmr_addr_record_t back;
    uint8_t bad[WMR_ADDR_RECORD_SIZE];
    CHECK(wmr_addr_record_unpack(golden_both, sizeof(golden_both), &back));
    CHECK(back.valid[0] && back.valid[1] && back.address[0][0] == 0x11 && back.address[1][5] == 0xFF);
    CHECK(back.model[0] == WMR_MODEL_WMR && back.model[1] == WMR_MODEL_WMR);   /* no model byte = WMR */
    CHECK(wmr_addr_record_unpack(golden_right, sizeof(golden_right), &back));
    CHECK(!back.valid[0] && back.valid[1]);

    /* Models share the first reserved byte, two bits per hand. An invalid slot never
     * carries model bits, so a zeroed slot stays zeroed. */
    r.valid[0] = false;
    r.valid[1] = true;
    r.model[0] = WMR_MODEL_PSVR2;
    r.model[1] = WMR_MODEL_PSVR2;
    wmr_addr_record_pack(&r, out);
#if WMR_CFG_PSVR2_SUPPORT
    CHECK(out[14] == 0x04);
#else
    CHECK(out[14] == 0x00);                 /* model storage compiled out */
#endif

    r.valid[0] = r.valid[1] = true;
    r.model[0] = WMR_MODEL_WMR;
    r.model[1] = WMR_MODEL_PSVR2;
    wmr_addr_record_pack(&r, out);
#if WMR_CFG_PSVR2_SUPPORT
    CHECK(out[14] == 0x04 && out[15] == 0x00);
    CHECK(wmr_addr_record_unpack(out, sizeof(out), &back));
    CHECK(back.model[0] == WMR_MODEL_WMR && back.model[1] == WMR_MODEL_PSVR2);
    r.model[0] = WMR_MODEL_PSVR2;
    wmr_addr_record_pack(&r, out);
    CHECK(out[14] == 0x05);
    CHECK(wmr_addr_record_unpack(out, sizeof(out), &back));
    CHECK(back.model[0] == WMR_MODEL_PSVR2 && back.model[1] == WMR_MODEL_PSVR2);
#else
    CHECK(out[14] == 0x00);
    CHECK(wmr_addr_record_unpack(out, sizeof(out), &back));
    CHECK(back.model[0] == WMR_MODEL_WMR && back.model[1] == WMR_MODEL_WMR);
    /* A record written by an opt-in build must not disable the hand here. */
    memcpy(bad, golden_both, sizeof(bad)); bad[14] = 0x04;
    CHECK(wmr_addr_record_unpack(bad, sizeof(bad), &back));
    CHECK(back.valid[0] && back.valid[1]);
    CHECK(back.model[0] == WMR_MODEL_WMR && back.model[1] == WMR_MODEL_WMR);
#endif
    /* Unknown model values make the record invalid rather than guessing WMR. */
    memcpy(bad, golden_both, sizeof(bad)); bad[14] = 0x02;
    CHECK(!wmr_addr_record_unpack(bad, sizeof(bad), &back));
    memcpy(bad, golden_both, sizeof(bad)); bad[14] = 0x08;
    CHECK(!wmr_addr_record_unpack(bad, sizeof(bad), &back));

    CHECK(!wmr_addr_record_unpack(golden_both, 15, &back) && !back.valid[0] && !back.valid[1]);
    memcpy(bad, golden_both, sizeof(bad)); bad[0] = 2;
    CHECK(!wmr_addr_record_unpack(bad, sizeof(bad), &back));
    memcpy(bad, golden_both, sizeof(bad)); bad[1] = 0x07;               /* unknown valid bit */
    CHECK(!wmr_addr_record_unpack(bad, sizeof(bad), &back));
    memcpy(bad, golden_right, sizeof(bad)); bad[1] = 0x03;              /* valid but all zero */
    CHECK(!wmr_addr_record_unpack(bad, sizeof(bad), &back));
    memcpy(bad, golden_both, sizeof(bad)); bad[1] = 0x00;               /* empty record is fine */
    CHECK(wmr_addr_record_unpack(bad, sizeof(bad), &back) && !back.valid[0] && !back.valid[1]);
    CHECK(!wmr_addr_record_unpack(NULL, WMR_ADDR_RECORD_SIZE, &back));

    uint8_t a[6] = {9, 9, 9, 9, 9, 9};
    CHECK(!wmr_addr_parse("11:22:33:44:55", a));
    CHECK(!wmr_addr_parse("11-22-33-44-55-66", a));
    CHECK(!wmr_addr_parse("11:22:33:44:55:6G", a));
    CHECK(!wmr_addr_parse("", a) && !wmr_addr_parse(NULL, a));
    CHECK(a[0] == 9);                                                   /* untouched on failure */
}

static wmr_discovery_device_t dev(uint8_t last){
    wmr_discovery_device_t d;
    memset(&d, 0, sizeof(d));
    d.address[0] = 0x2C;
    d.address[5] = last;
    d.page_scan_repetition_mode = 1;
    d.clock_offset = (uint16_t) (0x8000 | last);
    return d;
}

static void test_run_names_in_inquiry(void){
    printf("Discovery run: names in the inquiry response\n");
    const bool known[2] = {false, false};
    wmr_discovery_t d;
    wmr_discovery_start_config(&d, known, 0);   /* both hands, whatever this build selects */
    CHECK(wmr_discovery_incomplete(&d));
    wmr_discovery_device_t l = dev(1), r = dev(2), other = dev(3), l2 = dev(4);
    CHECK(wmr_discovery_on_inquiry_result(&d, &other, NAME("Some Phone")) == WMR_DISC_IGNORED);
    CHECK(wmr_discovery_on_inquiry_result(&d, &l, NAME("Motion controller - Left")) == WMR_DISC_MATCHED);
    CHECK(wmr_discovery_on_inquiry_result(&d, &l, NAME("Motion controller - Left")) == WMR_DISC_IGNORED);   /* repeat */
    CHECK(wmr_discovery_incomplete(&d));
    /* A second LEFT in range: the first one found stays the candidate. */
    CHECK(wmr_discovery_on_inquiry_result(&d, &l2, NAME("Motion controller - Left")) == WMR_DISC_DUPLICATE);
    CHECK(wmr_discovery_on_inquiry_result(&d, &l2, NAME("Motion controller - Left")) == WMR_DISC_IGNORED);  /* counted once */
    CHECK(d.duplicates == 1 && memcmp(d.found_address[0], l.address, 6) == 0);
    CHECK(wmr_discovery_on_inquiry_result(&d, &r, NAME("Motion controller - Right")) == WMR_DISC_MATCHED);
    CHECK(!wmr_discovery_incomplete(&d));
    CHECK(d.found[0] && d.found[1] && memcmp(d.found_address[1], r.address, 6) == 0);
    CHECK(wmr_discovery_next_name_request(&d) == NULL);
    CHECK(d.results == 6);
}

static void test_run_psvr2(void){
    const bool none[2] = {false, false};
    wmr_discovery_t d;
#if WMR_CFG_PSVR2_SUPPORT
    printf("Discovery run: PSVR2 names and per-hand models\n");
    wmr_discovery_start_config(&d, none, 0);
    wmr_discovery_device_t l = dev(31), r = dev(32), l2 = dev(33);
    CHECK(wmr_discovery_on_inquiry_result(&d, &l, NAME("PlayStation VR2 Sense Controller (L)"))
          == WMR_DISC_MATCHED);
    CHECK(d.found_model[WMR_HAND_LEFT] == WMR_MODEL_PSVR2);
    /* A second LEFT, this time a WMR controller, is still a duplicate hand: first wins. */
    CHECK(wmr_discovery_on_inquiry_result(&d, &l2, NAME("Motion controller - Left"))
          == WMR_DISC_DUPLICATE);
    CHECK(d.found_model[WMR_HAND_LEFT] == WMR_MODEL_PSVR2);
    CHECK(wmr_discovery_on_inquiry_result(&d, &r, NAME("Motion controller - Right"))
          == WMR_DISC_MATCHED);
    CHECK(d.found_model[WMR_HAND_RIGHT] == WMR_MODEL_WMR);
    CHECK(!wmr_discovery_incomplete(&d));

    /* Remote-name path: a PSVR2 name answers a queued name request. */
    wmr_discovery_start_config(&d, none, 2);   /* RIGHT only */
    CHECK(wmr_discovery_on_inquiry_result(&d, &r, NULL, 0) == WMR_DISC_NAME_QUEUED);
    const wmr_discovery_device_t *q = wmr_discovery_next_name_request(&d);
    CHECK(q && memcmp(q->address, r.address, 6) == 0);
    CHECK(wmr_discovery_on_name(&d, r.address, NAME("PlayStation VR2 Sense Controller (R)"))
          == WMR_DISC_MATCHED);
    CHECK(d.found_model[WMR_HAND_RIGHT] == WMR_MODEL_PSVR2);

    CHECK(wmr_discovery_incoming_hand_config(none, 0, NAME("PlayStation VR2 Sense Controller (L)"))
          == WMR_HAND_LEFT);
    CHECK(wmr_discovery_incoming_hand_config(none, 2, NAME("PlayStation VR2 Sense Controller (L)")) == -1);
#else
    printf("Discovery run: PSVR2 support compiled out\n");
    wmr_discovery_start_config(&d, none, 0);
    wmr_discovery_device_t l = dev(31);
    CHECK(wmr_discovery_on_inquiry_result(&d, &l, NAME("PlayStation VR2 Sense Controller (L)"))
          == WMR_DISC_IGNORED);
    CHECK(wmr_discovery_incomplete(&d));
#endif
}

static void test_run_name_requests(void){
    printf("Discovery run: names by remote name request, one hand already known\n");
    const bool known[2] = {true, false};                                /* LEFT stored */
    wmr_discovery_t d;
    wmr_discovery_start_config(&d, known, 0);   /* both hands, whatever this build selects */
    wmr_discovery_device_t a = dev(10), b = dev(11), c = dev(12);
    CHECK(wmr_discovery_on_inquiry_result(&d, &a, NULL, 0) == WMR_DISC_NAME_QUEUED);
    CHECK(wmr_discovery_on_inquiry_result(&d, &a, NULL, 0) == WMR_DISC_IGNORED);    /* repeat */
    CHECK(wmr_discovery_on_inquiry_result(&d, &b, NULL, 0) == WMR_DISC_NAME_QUEUED);
    CHECK(wmr_discovery_on_inquiry_result(&d, &c, (const uint8_t *) "x", 0) == WMR_DISC_NAME_QUEUED);   /* empty name */
    CHECK(d.pending_count == 3);

    const wmr_discovery_device_t *q = wmr_discovery_next_name_request(&d);
    CHECK(q && memcmp(q->address, a.address, 6) == 0 && q->clock_offset == a.clock_offset);
    /* LEFT is already known: its name is not taken. */
    CHECK(wmr_discovery_on_name(&d, a.address, NAME("Motion controller - Left")) == WMR_DISC_ALREADY_KNOWN);
    q = wmr_discovery_next_name_request(&d);
    CHECK(q && memcmp(q->address, b.address, 6) == 0);
    CHECK(wmr_discovery_on_name(&d, b.address, NULL, 0) == WMR_DISC_IGNORED);       /* request failed */
    q = wmr_discovery_next_name_request(&d);
    CHECK(q && memcmp(q->address, c.address, 6) == 0);
    CHECK(wmr_discovery_on_name(&d, c.address, NAME("Motion controller - Right")) == WMR_DISC_MATCHED);
    CHECK(!wmr_discovery_incomplete(&d) && !d.found[0] && d.found[1]);
    CHECK(wmr_discovery_next_name_request(&d) == NULL);
}

static void test_run_rounds(void){
    printf("Discovery run: every inquiry round retries devices and name requests\n");
    const bool known[2] = {false, false};
    wmr_discovery_t d;
    wmr_discovery_start_config(&d, known, 0);   /* both hands, whatever this build selects */
    wmr_discovery_device_t a = dev(0x51), l = dev(0x52);
    /* Round 1: the remote name request fails, so the hand stays missing. */
    CHECK(wmr_discovery_on_inquiry_result(&d, &a, NULL, 0) == WMR_DISC_NAME_QUEUED);
    const wmr_discovery_device_t *q = wmr_discovery_next_name_request(&d);
    CHECK(q && memcmp(q->address, a.address, 6) == 0);
    CHECK(wmr_discovery_on_name(&d, a.address, NULL, 0) == WMR_DISC_IGNORED);
    CHECK(wmr_discovery_next_name_request(&d) == NULL);
    CHECK(wmr_discovery_incomplete(&d));

    /* Round 2: the same device is asked again instead of being remembered as handled. */
    wmr_discovery_begin_round(&d);
    CHECK(d.pending_count == 0 && d.pending_next == 0 && d.seen_count == 0);
    CHECK(wmr_discovery_on_inquiry_result(&d, &a, NULL, 0) == WMR_DISC_NAME_QUEUED);
    q = wmr_discovery_next_name_request(&d);
    CHECK(q && memcmp(q->address, a.address, 6) == 0);
    CHECK(wmr_discovery_on_name(&d, a.address, NAME("Motion controller - Left")) == WMR_DISC_MATCHED);

    /* A match survives the reset; another device of that hand is still a duplicate. */
    wmr_discovery_begin_round(&d);
    CHECK(d.found[WMR_HAND_LEFT]);
    CHECK(memcmp(d.found_address[WMR_HAND_LEFT], a.address, 6) == 0);
    CHECK(wmr_discovery_on_inquiry_result(&d, &l, NAME("Motion controller - Left")) == WMR_DISC_DUPLICATE);
    CHECK(memcmp(d.found_address[WMR_HAND_LEFT], a.address, 6) == 0);
    CHECK(wmr_discovery_incomplete(&d));       /* RIGHT is still missing */
}

static void test_run_limits(void){
    printf("Discovery run: pending list full, stop asking once complete\n");
    const bool known[2] = {false, false};
    wmr_discovery_t d;
    wmr_discovery_start_config(&d, known, 0);   /* both hands, whatever this build selects */
    for (unsigned i = 0; i < WMR_DISCOVERY_MAX_PENDING; i++){
        wmr_discovery_device_t x = dev((uint8_t) (0x40 + i));
        CHECK(wmr_discovery_on_inquiry_result(&d, &x, NULL, 0) == WMR_DISC_NAME_QUEUED);
    }
    wmr_discovery_device_t extra = dev(0x60);
    CHECK(wmr_discovery_on_inquiry_result(&d, &extra, NULL, 0) == WMR_DISC_QUEUE_FULL);
    CHECK(d.skipped == 1);
    /* Both hands found via the first two names: no further name requests. */
    const wmr_discovery_device_t *q = wmr_discovery_next_name_request(&d);
    CHECK(q && wmr_discovery_on_name(&d, q->address, NAME("Motion controller - Right")) == WMR_DISC_MATCHED);
    q = wmr_discovery_next_name_request(&d);
    CHECK(q && wmr_discovery_on_name(&d, q->address, NAME("motion controller - left")) == WMR_DISC_MATCHED);
    CHECK(wmr_discovery_next_name_request(&d) == NULL);

    /* Nothing wanted: complete from the start. */
    const bool both[2] = {true, true};
    wmr_discovery_start_config(&d, both, 0);
    CHECK(!wmr_discovery_incomplete(&d));
    wmr_discovery_device_t y = dev(0x70);
    CHECK(wmr_discovery_on_inquiry_result(&d, &y, NAME("Motion controller - Left")) == WMR_DISC_ALREADY_KNOWN);
}

static void test_incoming(void){
    printf("Incoming connection from an unknown address\n");
    const bool none[2] = {false, false}, left[2] = {true, false}, both[2] = {true, true};
    /* Both-hands behaviour, independent of this build's WMR_CFG_BRIDGE_HANDS. */
    CHECK(wmr_discovery_incoming_hand_config(none, 0, NAME("Motion controller - Left")) == WMR_HAND_LEFT);
    CHECK(wmr_discovery_incoming_hand_config(left, 0, NAME("Motion controller - Left")) == -1);   /* never replaces */
    CHECK(wmr_discovery_incoming_hand_config(left, 0, NAME("Motion controller - Right")) == WMR_HAND_RIGHT);
    CHECK(wmr_discovery_incoming_hand_config(both, 0, NAME("Motion controller - Right")) == -1);
    CHECK(wmr_discovery_incoming_hand_config(none, 0, NAME("Keyboard")) == -1);
    CHECK(wmr_discovery_incoming_hand_config(none, 0, NULL, 0) == -1);                            /* name request failed */
}

static void test_bridge_hands(void){
    printf("Bridge hand selection (WMR_CFG_BRIDGE_HANDS)\n");
    /* 0 = both, 1 = LEFT only, 2 = RIGHT only. The default build is 0; the test is also
     * compiled with 1 and 2 manually to check the compile-time path. */
    CHECK(WMR_BRIDGE_HAND_ENABLED(WMR_HAND_LEFT) == (WMR_CFG_BRIDGE_HANDS != 2));
    CHECK(WMR_BRIDGE_HAND_ENABLED(WMR_HAND_RIGHT) == (WMR_CFG_BRIDGE_HANDS != 1));
    for (int cfg = 0; cfg <= 2; cfg++){
        for (int h = 0; h < WMR_NUM_CONTROLLERS; h++){
            const bool want = cfg == 0 || h + 1 == cfg;
            CHECK(wmr_bridge_hand_enabled(cfg, h) == want);
        }
        CHECK(!wmr_bridge_hand_enabled(cfg, -1));
        CHECK(!wmr_bridge_hand_enabled(cfg, WMR_NUM_CONTROLLERS));
    }
    CHECK(!wmr_bridge_hand_enabled(3, WMR_HAND_LEFT));   /* invalid selection serves nothing */

    const bool none[2] = {false, false};
    wmr_discovery_device_t l = dev(0x21), r = dev(0x22);

    /* Discovery: an excluded hand's name is ignored in the inquiry response. */
    for (int cfg = 0; cfg <= 2; cfg++){
        wmr_discovery_t d;
        wmr_discovery_start_config(&d, none, cfg);
        CHECK(d.bridge_hands == cfg);
        CHECK(d.wanted[WMR_HAND_LEFT] == (cfg == 0 || cfg == 1));
        CHECK(d.wanted[WMR_HAND_RIGHT] == (cfg == 0 || cfg == 2));
        CHECK(wmr_discovery_incomplete(&d));
        CHECK(wmr_discovery_on_inquiry_result(&d, &l, NAME("Motion controller - Left"))
              == (cfg == 2 ? WMR_DISC_IGNORED : WMR_DISC_MATCHED));
        CHECK(wmr_discovery_on_inquiry_result(&d, &r, NAME("Motion controller - Right"))
              == (cfg == 1 ? WMR_DISC_IGNORED : WMR_DISC_MATCHED));
        CHECK(!wmr_discovery_incomplete(&d));
        CHECK(wmr_discovery_next_name_request(&d) == NULL);
        /* The excluded hand is never recorded as a candidate. */
        CHECK(d.found[WMR_HAND_LEFT] == (cfg != 2));
        CHECK(d.found[WMR_HAND_RIGHT] == (cfg != 1));
    }

    /* Remote-name path: asking for an excluded hand's name does not make it a candidate. */
    {
        wmr_discovery_t d;
        wmr_discovery_start_config(&d, none, 1);
        CHECK(wmr_discovery_on_inquiry_result(&d, &r, NULL, 0) == WMR_DISC_NAME_QUEUED);
        const wmr_discovery_device_t *q = wmr_discovery_next_name_request(&d);
        CHECK(q && memcmp(q->address, r.address, 6) == 0);
        CHECK(wmr_discovery_on_name(&d, r.address, NAME("Motion controller - Right")) == WMR_DISC_IGNORED);
        CHECK(!d.found[WMR_HAND_RIGHT] && wmr_discovery_incomplete(&d));
    }

    /* Incoming connection: the excluded hand is declined exactly like a non-controller. */
    for (int cfg = 0; cfg <= 2; cfg++){
        CHECK(wmr_discovery_incoming_hand_config(none, cfg, NAME("Motion controller - Left"))
              == (cfg == 0 || cfg == 1 ? WMR_HAND_LEFT : -1));
        CHECK(wmr_discovery_incoming_hand_config(none, cfg, NAME("Motion controller - Right"))
              == (cfg == 0 || cfg == 2 ? WMR_HAND_RIGHT : -1));
        CHECK(wmr_discovery_incoming_hand_config(none, cfg, NAME("Keyboard")) == -1);
        CHECK(wmr_discovery_incoming_hand_config(none, cfg, NULL, 0) == -1);
    }
    /* The default wrapper follows the compile-time selection. */
    CHECK(wmr_discovery_incoming_hand(none, NAME("Motion controller - Left"))
          == (WMR_CFG_BRIDGE_HANDS == 2 ? -1 : WMR_HAND_LEFT));
    CHECK(wmr_discovery_incoming_hand(none, NAME("Motion controller - Right"))
          == (WMR_CFG_BRIDGE_HANDS == 1 ? -1 : WMR_HAND_RIGHT));
    /* A hand that already has an address is declined whether or not it is selected. */
    const bool both[2] = {true, true};
    CHECK(wmr_discovery_incoming_hand_config(both, 1, NAME("Motion controller - Left")) == -1);
    CHECK(wmr_discovery_incoming_hand_config(both, 2, NAME("Motion controller - Right")) == -1);
}

int main(void){
    test_names();
    test_models();
    test_record();
    test_run_names_in_inquiry();
    test_run_psvr2();
    test_run_name_requests();
    test_run_rounds();
    test_run_limits();
    test_incoming();
    test_bridge_hands();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
