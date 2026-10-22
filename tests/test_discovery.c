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
    CHECK(wmr_addr_record_unpack(golden_both, sizeof(golden_both), &back));
    CHECK(back.valid[0] && back.valid[1] && back.address[0][0] == 0x11 && back.address[1][5] == 0xFF);
    CHECK(wmr_addr_record_unpack(golden_right, sizeof(golden_right), &back));
    CHECK(!back.valid[0] && back.valid[1]);

    uint8_t bad[WMR_ADDR_RECORD_SIZE];
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

#define NAME(s) (const uint8_t *) (s), strlen(s)

static void test_run_names_in_inquiry(void){
    printf("Discovery run: names in the inquiry response\n");
    const bool known[2] = {false, false};
    wmr_discovery_t d;
    wmr_discovery_start(&d, known);
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

static void test_run_name_requests(void){
    printf("Discovery run: names by remote name request, one hand already known\n");
    const bool known[2] = {true, false};                                /* LEFT stored */
    wmr_discovery_t d;
    wmr_discovery_start(&d, known);
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

static void test_run_limits(void){
    printf("Discovery run: pending list full, stop asking once complete\n");
    const bool known[2] = {false, false};
    wmr_discovery_t d;
    wmr_discovery_start(&d, known);
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
    wmr_discovery_start(&d, both);
    CHECK(!wmr_discovery_incomplete(&d));
    wmr_discovery_device_t y = dev(0x70);
    CHECK(wmr_discovery_on_inquiry_result(&d, &y, NAME("Motion controller - Left")) == WMR_DISC_ALREADY_KNOWN);
}

static void test_incoming(void){
    printf("Incoming connection from an unknown address\n");
    const bool none[2] = {false, false}, left[2] = {true, false}, both[2] = {true, true};
    CHECK(wmr_discovery_incoming_hand(none, NAME("Motion controller - Left")) == WMR_HAND_LEFT);
    CHECK(wmr_discovery_incoming_hand(left, NAME("Motion controller - Left")) == -1);   /* never replaces */
    CHECK(wmr_discovery_incoming_hand(left, NAME("Motion controller - Right")) == WMR_HAND_RIGHT);
    CHECK(wmr_discovery_incoming_hand(both, NAME("Motion controller - Right")) == -1);
    CHECK(wmr_discovery_incoming_hand(none, NAME("Keyboard")) == -1);
    CHECK(wmr_discovery_incoming_hand(none, NULL, 0) == -1);                            /* name request failed */
}

int main(void){
    test_names();
    test_record();
    test_run_names_in_inquiry();
    test_run_name_requests();
    test_run_limits();
    test_incoming();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
