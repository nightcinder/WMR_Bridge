/*
 * Host tests for src/wmr_console.c: the serial console command dispatch, the
 * firmware banner and the periodic statistics block. The fake run loop fires
 * the stats timer and the fake firmware modules record what a key asked for.
 *
 * Not tested here: the real UART, BTstack's stdin driver or a real controller.
 */

#include <stdio.h>
#include <string.h>
#include "btstack_config.h"
#include "btstack.h"
#include "wmr_config.h"
#include "wmr_console.h"
#include "host_stubs.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static char output[32768];

static void start(void){
    host_reset();
    host_capture_begin();
    wmr_console_init();
    host_capture_end(output, sizeof(output));
}

static void press(char key){
    host_stdin_send(key);
}

static void press_capture(char key){
    host_capture_begin();
    press(key);
    host_capture_end(output, sizeof(output));
}

static unsigned count_text(const char *text){
    unsigned count = 0;
    const size_t length = strlen(text);
    for (const char *p = output; (p = strstr(p, text)) != NULL; p += length) count++;
    return count;
}

static void send_state_event(uint8_t type, uint8_t state){
    host_event_t event;
    host_event_begin(&event, type);
    host_event_add_u8(&event, state);
    host_event_finish(&event);
    wmr_console_on_hci_event(event.data, event.len);
}

static void test_banner_and_usage(void){
    printf("console: banner on init, usage once the stack is working\n");
    start();
    CHECK(strstr(output, "WMR Bridge firmware 0.3.4-hosttest") != NULL);
    CHECK(strstr(output, "serving LEFT and RIGHT") != NULL);
    CHECK(strstr(output, "WMR bridge console") == NULL);
    CHECK(host_pending_timers() == 1);          /* the statistics timer */

    host_capture_begin();
    send_state_event(BTSTACK_EVENT_STATE, HCI_STATE_INITIALIZING);
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "WMR bridge console") == NULL);

    host_capture_begin();
    send_state_event(0x31, HCI_STATE_WORKING);  /* not the stack-state event */
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "WMR bridge console") == NULL);

    host_capture_begin();
    send_state_event(BTSTACK_EVENT_STATE, HCI_STATE_WORKING);
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "WMR bridge console") != NULL);
    CHECK(strstr(output, "k: BOND RESET LEFT") != NULL);
}

static void test_connect_and_disconnect_keys(void){
    printf("console: connect, disconnect and help keys\n");
    start();
    press('l');
    press('c');
    CHECK(host.connect_calls[WMR_HAND_LEFT] == 2);
    press('r');
    CHECK(host.connect_calls[WMR_HAND_RIGHT] == 1);
    press('L');
    CHECK(host.disconnect_calls[WMR_HAND_LEFT] == 1);
    press('R');
    CHECK(host.disconnect_calls[WMR_HAND_RIGHT] == 1);
    press('\r');
    press('\n');
    CHECK(host.connect_calls[WMR_HAND_LEFT] == 2);

    press_capture('?');
    CHECK(strstr(output, "s: status and statistics") != NULL);
    press_capture('Z');
    CHECK(strstr(output, "WMR bridge console") != NULL);
}

static void test_discovery_and_forget(void){
    printf("console: discovery, FORGET and the bond-reset keys\n");
    start();
    press('d');
    CHECK(host.discover_calls == 1);

    host.forget_address_result = WMR_FORGET_BUSY;
    press_capture('F');
    CHECK(strstr(output, "FORGET refused") != NULL);
    CHECK(host.discover_calls == 1);
    host.forget_address_result = WMR_FORGET_OK;
    press_capture('F');
    CHECK(strstr(output, "stored controller addresses deleted") != NULL);
    CHECK(host.discover_calls == 2);

    host.forget_bond_result = WMR_BOND_RESET_NO_ADDRESS;
    press_capture('k');
    CHECK(strstr(output, "LEFT has no address yet") != NULL);
    CHECK(host.forget_bond_hand == WMR_HAND_LEFT);
    host.forget_bond_result = WMR_BOND_RESET_OK;
    press_capture('k');
    CHECK(strstr(output, "removed ESP32's stored LEFT link key") != NULL);
    press_capture('K');
    CHECK(host.forget_bond_hand == WMR_HAND_RIGHT);
    host.forget_bond_result = WMR_BOND_RESET_BUSY;
    press_capture('K');
    CHECK(strstr(output, "BOND RESET refused") != NULL);

    press_capture('b');
    CHECK(host.gap_delete_all_calls == 1);
    CHECK(strstr(output, "deleted all stored link keys") != NULL);
}

static void test_phone_bond_reset(void){
    printf("console: phone bond reset is refused while the phone is connected\n");
    start();
    host.info.phone_connected = true;
    press_capture('P');
    CHECK(strstr(output, "PHONE BOND RESET refused") != NULL);
    CHECK(host.le_db_remove_calls == 0);

    host.info.phone_connected = false;
    host.le_db_count = 3;
    host.le_db_max_count = 4;
    press_capture('P');
    CHECK(host.le_db_remove_calls == 4);
    CHECK(strstr(output, "deleted 3 bonded phone(s)") != NULL);
}

static void fill_status(void){
    host.status[WMR_HAND_LEFT].connected = true;
    host.status[WMR_HAND_LEFT].descriptor_available = true;
    host.status[WMR_HAND_LEFT].reports = 42;
    host.status[WMR_HAND_LEFT].other_reports = 3;
    host.status[WMR_HAND_LEFT].address_source = WMR_ADDR_STORED;
    const uint8_t address[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
    memcpy(host.status[WMR_HAND_LEFT].address, address, 6);
    host.cid[WMR_HAND_LEFT] = 7;
    host.status[WMR_HAND_RIGHT].model = WMR_MODEL_PSVR2;

    host.info.phone_connected = true;
    host.info.advertising = false;
    host.info.subscribed = true;
    host.info.hid_subscribed = true;
    host.info.stream = WMR_STREAM_DECODED;
    host.info.mtu = 517;
    host.info.queue_count = 3;
    host.info.queue_capacity = 256;
    host.info.queue_high_water = 9;
    host.info.conn_known = true;
    host.info.conn.interval = 12;
    host.info.conn.latency = 0;
    host.info.conn.timeout = 400;
    host.info.conn_update_pending = true;

    host.stats.notifications = 1234;
    host.stats.errors = 2;
    host.stats.batches_tx = 98;
    host.stats.records_in_batches = 471;
    host.stats.batch_max = 9;
    host.stats.batch_full_flushes = 1;
    host.stats.hid_reports = 55;
    host.echo.accepted = 5;
    host.echo.sent = 4;
    host.echo.pending = true;
}

static void test_status_snapshot(void){
    printf("console: 's' prints controllers and the statistics block\n");
    start();
    fill_status();
    press_capture('s');
    CHECK(strstr(output, "LEFT cid=7 connected=1 descriptor=1 model=WMR reports=42 other=3 "
                         "address=11:22:33:44:55:66 (discovered, stored)") != NULL);
    CHECK(strstr(output, "RIGHT cid=0 connected=0") != NULL);
    CHECK(strstr(output, "model=PSVR2") != NULL);
    CHECK(strstr(output, "STREAM phone=1 adv=off hid=on sub=1 stream=decoded mtu=517 "
                         "conn=[15.00ms lat=0 to=4000ms] (update requested, not granted yet) "
                         "q=3/256 high=9 notify=1234 errors=2 bridge_hands=0") != NULL);
    CHECK(strstr(output, "FF14 batches_tx=98 records_in_batches=471 mean=4.81 max=9 "
                         "batch_full_flushes=1") != NULL);
    CHECK(strstr(output, "HID reports=55 errors=0 decode_errors=0") != NULL);
    CHECK(strstr(output, "FF15 echo accepted=5 sent=4 pending=1 busy=0 bad=0 "
                         "unsubscribed=0 cancelled=0") != NULL);
    CHECK(strstr(output, "FF13 LEFT state=0 total=0 crc32=00000000 send_errors=0") != NULL);
    CHECK(strstr(output, "vhci_drop=0 log_errors=0") != NULL);
    CHECK(strstr(output, "LEFT link: n/a (no ACL link) mode=active") != NULL);
    CHECK(strstr(output, "rate_rx=") == NULL);      /* rates only on the periodic call */
    CHECK(strstr(output, "LINKLOG_FIELDS,") == NULL);
}

static void test_periodic_stats(void){
    printf("console: the stats timer prints rates and one LINKLOG header\n");
    start();
    fill_status();
    host_capture_begin();
    host_advance_ms(WMR_CFG_STATS_INTERVAL_MS);
    host_capture_end(output, sizeof(output));
    CHECK(count_text("LINKLOG_FIELDS,") == 1);
    CHECK(count_text("LINKLOG,") == 2);             /* one line per hand */
    CHECK(strstr(output, "LEFT rx=0 tx=0") != NULL);

    host.stats.rx[WMR_HAND_LEFT] = 500;
    host.stats.tx[WMR_HAND_LEFT] = 480;
    host.stats.rx[WMR_HAND_RIGHT] = 250;
    host.stats.tx[WMR_HAND_RIGHT] = 250;
    host_capture_begin();
    host_advance_ms(WMR_CFG_STATS_INTERVAL_MS);
    host_capture_end(output, sizeof(output));
    CHECK(count_text("LINKLOG_FIELDS,") == 0);      /* the header is printed once */
    CHECK(count_text("LINKLOG,") == 2);
    CHECK(strstr(output, "rate_rx=100.0/s rate_tx=96.0/s") != NULL);
    CHECK(strstr(output, "rate_rx=50.0/s rate_tx=50.0/s") != NULL);
}

int main(void){
    test_banner_and_usage();
    test_connect_and_disconnect_keys();
    test_discovery_and_forget();
    test_phone_bond_reset();
    test_status_snapshot();
    test_periodic_stats();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
