/*
 * Host tests for the FF15 echo characteristic pieces that need no BTstack: the request and
 * response formats (golden bytes), and the one-outstanding-echo state with its counters.
 *
 * What this does NOT test: the BTstack callbacks, the GATT database, the pump giving the
 * echo priority over queued stream data (att_server_notify, CAN_SEND_NOW), or anything on a
 * radio. The round-trip figures themselves can only come from a phone.
 */

#include <stdio.h>
#include <string.h>
#include "wmr_echo.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static void test_request(void){
    printf("FF15 request golden bytes\n");
    const uint8_t req[4] = {0x01, 0x00, 0x34, 0x12};
    uint16_t id = 0;
    CHECK(wmr_echo_parse_request(req, 4, &id) && id == 0x1234);
    const uint8_t reserved_set[4] = {0x01, 0xFF, 0xCD, 0xAB};
    CHECK(wmr_echo_parse_request(reserved_set, 4, &id) && id == 0xABCD);   /* byte 1 ignored */
    const uint8_t v2[4] = {0x02, 0x00, 0x34, 0x12};
    CHECK(!wmr_echo_parse_request(v2, 4, &id));
    CHECK(!wmr_echo_parse_request(req, 3, &id));
    CHECK(!wmr_echo_parse_request(req, 0, &id));
    const uint8_t longer[5] = {0x01, 0x00, 0x34, 0x12, 0x00};
    CHECK(!wmr_echo_parse_request(longer, 5, &id));
    CHECK(!wmr_echo_parse_request(NULL, 4, &id));
}

static void test_response(void){
    printf("FF15 response golden bytes\n");
    const wmr_echo_response_t r = {
        .ping_id = 0x1234,
        .rx_time_us = 0x0011223344556677ull,
        .turnaround_us = 0x000005DC,           /* 1500 us */
        .queue_depth = 0x0102,
    };
    static const uint8_t golden[WMR_ECHO_RESPONSE_SIZE] = {
        0x01, 0x00,                                     /* version 1, reserved */
        0x34, 0x12,                                     /* ping_id */
        0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11, 0x00, /* rx_time_us */
        0xDC, 0x05, 0x00, 0x00,                         /* turnaround_us */
        0x02, 0x01,                                     /* queue_depth */
        0x00, 0x00,                                     /* reserved */
    };
    uint8_t out[WMR_ECHO_RESPONSE_SIZE];
    memset(out, 0xEE, sizeof(out));
    wmr_echo_pack(&r, out);
    CHECK(memcmp(out, golden, sizeof(golden)) == 0);
    CHECK(WMR_ECHO_RESPONSE_SIZE == 20 && WMR_ECHO_MIN_MTU == 23);   /* default ATT MTU */

    wmr_echo_response_t back;
    CHECK(wmr_echo_unpack(golden, sizeof(golden), &back));
    CHECK(back.ping_id == r.ping_id && back.rx_time_us == r.rx_time_us &&
          back.turnaround_us == r.turnaround_us && back.queue_depth == r.queue_depth);
    CHECK(!wmr_echo_unpack(golden, 19, &back));
    uint8_t bad[WMR_ECHO_RESPONSE_SIZE];
    memcpy(bad, golden, sizeof(bad));
    bad[0] = 2;
    CHECK(!wmr_echo_unpack(bad, sizeof(bad), &back));
}

static void test_state(void){
    printf("FF15 one outstanding echo and counters\n");
    wmr_echo_state_t s;
    wmr_echo_init(&s);
    const uint8_t req1[4] = {0x01, 0x00, 0x01, 0x00};
    const uint8_t req2[4] = {0x01, 0x00, 0x02, 0x00};
    const uint8_t junk[3] = {0x01, 0x00, 0x02};
    uint8_t out[WMR_ECHO_RESPONSE_SIZE];
    wmr_echo_response_t r;

    CHECK(!wmr_echo_build(&s, 0, out));                                 /* nothing pending */
    CHECK(wmr_echo_on_write(&s, false, req1, 4, 1000, 0) == WMR_ECHO_UNSUBSCRIBED);
    CHECK(!s.pending && s.unsubscribed == 1);
    CHECK(wmr_echo_on_write(&s, true, junk, 3, 1000, 0) == WMR_ECHO_BAD);
    CHECK(!s.pending && s.bad == 1);

    CHECK(wmr_echo_on_write(&s, true, req1, 4, 1000, 37) == WMR_ECHO_ACCEPTED);
    CHECK(s.pending && s.accepted == 1);
    /* A second ping while the first waits is ignored; the first is unchanged. */
    CHECK(wmr_echo_on_write(&s, true, req2, 4, 1200, 40) == WMR_ECHO_BUSY);
    CHECK(s.busy == 1 && s.ping_id == 1 && s.rx_time_us == 1000);

    /* A failed notify leaves it pending; the retry restamps the turnaround. */
    CHECK(wmr_echo_build(&s, 1400, out) && wmr_echo_unpack(out, sizeof(out), &r));
    CHECK(r.ping_id == 1 && r.rx_time_us == 1000 && r.turnaround_us == 400 && r.queue_depth == 37);
    CHECK(wmr_echo_build(&s, 2500, out) && wmr_echo_unpack(out, sizeof(out), &r));
    CHECK(r.turnaround_us == 1500 && s.pending);
    wmr_echo_sent(&s);
    CHECK(!s.pending && s.sent == 1);
    wmr_echo_sent(&s);                                                  /* no double count */
    CHECK(s.sent == 1);

    /* Next ping is accepted; a disconnect cancels it. */
    CHECK(wmr_echo_on_write(&s, true, req2, 4, 3000, 0) == WMR_ECHO_ACCEPTED);
    wmr_echo_cancel(&s);
    CHECK(!s.pending && s.cancelled == 1);
    wmr_echo_cancel(&s);
    CHECK(s.cancelled == 1);
    CHECK(s.accepted == s.sent + s.cancelled + (s.pending ? 1u : 0u));

    /* Turnaround saturates instead of wrapping, and a clock that went backwards gives 0. */
    CHECK(wmr_echo_on_write(&s, true, req1, 4, 10, 0) == WMR_ECHO_ACCEPTED);
    CHECK(wmr_echo_build(&s, 10 + 0x1FFFFFFFFull, out) && wmr_echo_unpack(out, sizeof(out), &r));
    CHECK(r.turnaround_us == UINT32_MAX);
    CHECK(wmr_echo_build(&s, 5, out) && wmr_echo_unpack(out, sizeof(out), &r));
    CHECK(r.turnaround_us == 0);
}

int main(void){
    test_request();
    test_response();
    test_state();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
