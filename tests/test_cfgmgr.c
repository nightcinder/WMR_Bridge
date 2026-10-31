/*
 * Host tests for src/wmr_cfgmgr.c: the FF13 config-block manager that runs on
 * BTstack timers. A fake run loop and a fake hid_host_send_report drive the
 * real state machine, so the request sequence, retries, timeouts, disconnect,
 * READY cache and enable-streaming commands are exercised without a radio.
 *
 * Not tested here: that a controller answers these commands, the HID callback
 * plumbing and the GATT database.
 */

#include <stdio.h>
#include <string.h>
#include "btstack_config.h"
#include "btstack.h"
#include "wmr_cfg.h"
#include "wmr_cfgmgr.h"
#include "host_stubs.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static char output[8192];
static unsigned listener_calls;
static unsigned listener_last_hand;

static void listener(unsigned hand){
    listener_calls++;
    listener_last_hand = hand;
}

static void put_u32(uint8_t *out, uint32_t value){
    out[0] = (uint8_t) (value & 0xffu);
    out[1] = (uint8_t) ((value >> 8) & 0xffu);
    out[2] = (uint8_t) ((value >> 16) & 0xffu);
    out[3] = (uint8_t) ((value >> 24) & 0xffu);
}

/* One config response as BTstack delivers it: 0xA1, then the 78-byte report. */
static void feed_response(unsigned hand, uint8_t block, uint32_t remain,
                          const uint8_t *data, uint8_t length){
    uint8_t report[1 + WMR_CFG_RESP_SIZE];
    memset(report, 0, sizeof(report));
    report[0] = WMR_CFG_HID_DATA_HEADER;
    report[1] = 0x02;                       /* report ID */
    report[3] = WMR_CFG_CMD_BLOCK_READ;
    report[5] = block;
    put_u32(&report[6], remain);
    report[10] = length;
    if (length && data) memcpy(&report[11], data, length);
    wmr_cfgmgr_on_report(hand, report, (uint16_t) sizeof(report));
}

static void feed_raw(unsigned hand, const uint8_t *report, uint16_t length){
    wmr_cfgmgr_on_report(hand, report, length);
}

static void setup(unsigned hand){
    host_reset();
    listener_calls = 0;
    listener_last_hand = 99;
    wmr_cfgmgr_init(listener);
    host.status[hand].connected = true;
    host.cid[hand] = 1;
}

static void start_read(unsigned hand){
    wmr_cfgmgr_on_descriptor(hand);
    host_advance_ms(1000);                  /* CFG_START_DELAY_MS */
}

static void test_full_read(void){
    printf("cfgmgr: full read, READY cache and enable commands\n");
    setup(WMR_HAND_LEFT);
    const uint8_t *blob = NULL;
    uint32_t total = 0;
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_NOT_READ);
    CHECK(blob != NULL && total == 0);

    wmr_cfgmgr_on_descriptor(WMR_HAND_LEFT);
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_READING);
    CHECK(listener_calls == 1 && listener_last_hand == WMR_HAND_LEFT);
    CHECK(host_hid.calls == 0);
    host_advance_ms(999);
    CHECK(host_hid.calls == 0);             /* the start delay is real */
    host_advance_ms(1);
    CHECK(host_hid.calls == 1);
    CHECK(host_hid.last_cid == 1 && host_hid.last_report_id == 0x06);
    CHECK(host_hid.last_report[0] == WMR_CFG_CMD_BLOCK_READ);
    CHECK(host_hid.last_report[1] == 0x02);                         /* block 0x02 */
    CHECK(host_hid.last_report[2] == 0xFF && host_hid.last_report[5] == 0xFF);   /* ADDR_FIRST */

    uint8_t first[60];
    for (unsigned i = 0; i < sizeof(first); i++) first[i] = (uint8_t) (i + 1);
    feed_response(WMR_HAND_LEFT, 0x02, 40, first, (uint8_t) sizeof(first));
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_READING);
    CHECK(host_hid.calls == 1);             /* the 10 ms spacing has not elapsed yet */
    host_advance_ms(10);
    CHECK(host_hid.calls == 2);
    CHECK(host_hid.last_report[2] == 40 && host_hid.last_report[3] == 0 &&
          host_hid.last_report[4] == 0 && host_hid.last_report[5] == 0);

    uint8_t second[40];
    for (unsigned i = 0; i < sizeof(second); i++) second[i] = (uint8_t) (100 + i);
    feed_response(WMR_HAND_LEFT, 0x02, 0, second, (uint8_t) sizeof(second));
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_READY);
    CHECK(total == 100);
    CHECK(listener_calls == 2 && listener_last_hand == WMR_HAND_LEFT);
    CHECK(memcmp(blob, first, sizeof(first)) == 0);
    CHECK(memcmp(blob + sizeof(first), second, sizeof(second)) == 0);

    host_capture_begin();
    wmr_cfgmgr_print();
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "FF13 LEFT state=2 total=100") != NULL);
    CHECK(strstr(output, "send_errors=0") != NULL);

    /* Two enable-streaming commands, spaced by WMR_CFG_SPACING_MS. */
    const unsigned before = host_hid.calls;
    host_advance_ms(10);
    CHECK(host_hid.calls == before + 1);
    CHECK(host_hid.last_report[0] == 0x03 && host_hid.last_report[1] == 0x01);
    host_advance_ms(10);
    CHECK(host_hid.calls == before + 2);
    CHECK(host_hid.last_report[0] == 0x03 && host_hid.last_report[1] == 0x02 &&
          host_hid.last_report[2] == 0xE1);
    host_advance_ms(100);
    CHECK(host_hid.calls == before + 2);    /* exactly two */

    /* READY survives a descriptor event and a disconnect. */
    wmr_cfgmgr_on_descriptor(WMR_HAND_LEFT);
    host_advance_ms(2000);
    CHECK(host_hid.calls == before + 2);
    wmr_cfgmgr_on_closed(WMR_HAND_LEFT);
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_READY);
    CHECK(total == 100);
    wmr_cfgmgr_on_descriptor(WMR_HAND_LEFT);
    host_advance_ms(2000);
    CHECK(host_hid.calls == before + 2);
}

static void test_disconnect_mid_read(void){
    printf("cfgmgr: disconnect abandons the read, next descriptor starts over\n");
    setup(WMR_HAND_RIGHT);
    start_read(WMR_HAND_RIGHT);
    CHECK(host_hid.calls == 1);
    wmr_cfgmgr_on_closed(WMR_HAND_RIGHT);
    const uint8_t *blob = NULL;
    uint32_t total = 123;
    CHECK(wmr_cfgmgr_get(WMR_HAND_RIGHT, &blob, &total) == WMR_CFG_NOT_READ);
    CHECK(total == 0);
    host_advance_ms(1000);
    CHECK(host_hid.calls == 1);             /* timer disarmed */

    start_read(WMR_HAND_RIGHT);
    CHECK(host_hid.calls == 2);
    CHECK(host_hid.last_report[2] == 0xFF); /* a fresh read starts at ADDR_FIRST */
}

static void test_timeouts(void){
    printf("cfgmgr: three retries then FAILED, enables still sent\n");
    setup(WMR_HAND_LEFT);
    start_read(WMR_HAND_LEFT);
    CHECK(host_hid.calls == 1);
    for (unsigned retry = 0; retry < WMR_CFG_MAX_RETRIES; retry++){
        host_advance_ms(WMR_CFG_TIMEOUT_MS);
        CHECK(host_hid.calls == retry + 2);
        CHECK(host_hid.last_report[2] == 0xFF);     /* the same request is retried */
    }
    host_advance_ms(WMR_CFG_TIMEOUT_MS);
    CHECK(host_hid.calls == WMR_CFG_MAX_RETRIES + 1);   /* no fifth read request */
    const uint8_t *blob = NULL;
    uint32_t total = 99;
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_BLOCK_FAILED);
    CHECK(total == 0);
    CHECK(listener_calls >= 2);

    const unsigned before = host_hid.calls;
    host_advance_ms(WMR_CFG_SPACING_MS);
    host_advance_ms(WMR_CFG_SPACING_MS);
    CHECK(host_hid.calls == before + 2);    /* both enable commands after a failure */
}

static void test_bad_responses_ignored(void){
    printf("cfgmgr: wrong block, wrong command and short reports change nothing\n");
    setup(WMR_HAND_LEFT);
    start_read(WMR_HAND_LEFT);
    const unsigned requests = host_hid.calls;

    feed_raw(WMR_HAND_LEFT, NULL, 0);
    const uint8_t short_report[] = {WMR_CFG_HID_DATA_HEADER};
    feed_raw(WMR_HAND_LEFT, short_report, sizeof(short_report));
    uint8_t wrong_id[1 + WMR_CFG_RESP_SIZE];
    memset(wrong_id, 0, sizeof(wrong_id));
    wrong_id[0] = WMR_CFG_HID_DATA_HEADER;
    wrong_id[1] = 0x09;
    feed_raw(WMR_HAND_LEFT, wrong_id, (uint16_t) sizeof(wrong_id));
    feed_response(WMR_HAND_LEFT, 0x03, 10, NULL, 10);      /* another block */
    /* Same block, wrong command: rewrite the command byte by hand. */
    uint8_t wrong_command[1 + WMR_CFG_RESP_SIZE];
    memset(wrong_command, 0, sizeof(wrong_command));
    wrong_command[0] = WMR_CFG_HID_DATA_HEADER;
    wrong_command[1] = 0x02;
    wrong_command[3] = 0x03;
    wrong_command[5] = 0x02;
    wrong_command[10] = 10;
    feed_raw(WMR_HAND_LEFT, wrong_command, (uint16_t) sizeof(wrong_command));

    const uint8_t *blob = NULL;
    uint32_t total = 0;
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_READING);
    CHECK(host_hid.calls == requests);
    /* The read is still alive: a good response completes it. */
    uint8_t data[10] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
    feed_response(WMR_HAND_LEFT, 0x02, 0, data, (uint8_t) sizeof(data));
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_READY);
    CHECK(total == 10);
}

static void test_failures(void){
    printf("cfgmgr: oversized block and empty progress fail the read\n");
    setup(WMR_HAND_LEFT);
    start_read(WMR_HAND_LEFT);
    feed_response(WMR_HAND_LEFT, 0x02, WMR_CFG_MAX_BLOCK, NULL, 1);
    const uint8_t *blob = NULL;
    uint32_t total = 7;
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_BLOCK_FAILED);
    CHECK(total == 0);

    setup(WMR_HAND_LEFT);
    start_read(WMR_HAND_LEFT);
    feed_response(WMR_HAND_LEFT, 0x02, 100, NULL, 0);   /* zero bytes while 100 remain */
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_BLOCK_FAILED);
}

static void test_send_error(void){
    printf("cfgmgr: a failed HID send still arms the timeout and is counted\n");
    setup(WMR_HAND_LEFT);
    host_hid.status = ERROR_CODE_COMMAND_DISALLOWED;
    wmr_cfgmgr_on_descriptor(WMR_HAND_LEFT);
    host_advance_ms(1000);
    CHECK(host_hid.calls == 1);
    CHECK(host_pending_timers() > 0);
    host_capture_begin();
    wmr_cfgmgr_print();
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "send_errors=1") != NULL);
    host_advance_ms(WMR_CFG_TIMEOUT_MS);
    CHECK(host_hid.calls == 2);             /* the timeout retried */
}

static void test_invalid_hand(void){
    printf("cfgmgr: hand out of range is a no-op\n");
    setup(WMR_HAND_LEFT);
    wmr_cfgmgr_on_descriptor(2);
    wmr_cfgmgr_on_closed(2);
    const uint8_t report[] = {WMR_CFG_HID_DATA_HEADER, 0x02};
    wmr_cfgmgr_on_report(2, report, (uint16_t) sizeof(report));
    host_advance_ms(5000);
    CHECK(host_hid.calls == 0);
    const uint8_t *blob = (const uint8_t *) 1;
    uint32_t total = 5;
    CHECK(wmr_cfgmgr_get(2, &blob, &total) == WMR_CFG_NOT_READ);
    CHECK(blob == NULL && total == 0);
}

int main(void){
    test_full_read();
    test_disconnect_mid_read();
    test_timeouts();
    test_bad_responses_ignored();
    test_failures();
    test_send_error();
    test_invalid_hand();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
