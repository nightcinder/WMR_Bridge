/*
 * Host tests for src/wmr_linkdiag.c: the BTstack-side link diagnostics. HCI event
 * bytes are built here and fed to the real handler, and the fake run loop drives
 * the polling timers and the fake HCI command channel, so link tracking, mode
 * changes, reply parsing, the VHCI log sink and report-loss accounting are all
 * exercised without a controller or radio.
 *
 * Not tested here: what a real controller or BTstack does with these commands.
 */

#include <stdio.h>
#include <string.h>
#include "btstack_config.h"
#include "btstack.h"
#include "wmr_config.h"
#include "wmr_devloss.h"
#include "wmr_linkdiag.h"
#include "host_stubs.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static const uint8_t LEFT_ADDR[6]  = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
static const uint8_t RIGHT_ADDR[6] = {0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff};
static const uint16_t LEFT_HANDLE  = 0x0040;
static const uint16_t RIGHT_HANDLE = 0x0041;

static char output[8192];

static void send_event(host_event_t *event){
    host_event_finish(event);
    wmr_linkdiag_on_hci_event(event->data, event->len);
}

static void send_disconnect(uint16_t handle){
    host_event_t event;
    host_event_begin(&event, HCI_EVENT_DISCONNECTION_COMPLETE);
    host_event_add_u8(&event, ERROR_CODE_SUCCESS);
    host_event_add_u16(&event, handle);
    host_event_add_u8(&event, 0x13);            /* reason: remote user terminated */
    send_event(&event);
}

static void send_connect(uint8_t status, uint16_t handle, const uint8_t address[6]){
    host_event_t event;
    host_event_begin(&event, HCI_EVENT_CONNECTION_COMPLETE);
    host_event_add_u8(&event, status);
    host_event_add_u16(&event, handle);
    host_event_add_bytes(&event, address, 6);
    host_event_add_u8(&event, HCI_LINK_TYPE_ACL);
    host_event_add_u8(&event, 0x01);            /* encryption enabled */
    send_event(&event);
}

static void send_mode_change(uint8_t status, uint16_t handle, uint8_t mode, uint16_t interval){
    host_event_t event;
    host_event_begin(&event, HCI_EVENT_MODE_CHANGE);
    host_event_add_u8(&event, status);
    host_event_add_u16(&event, handle);
    host_event_add_u8(&event, mode);
    host_event_add_u16(&event, interval);
    send_event(&event);
}

static void send_command_complete(uint16_t opcode, uint8_t status, uint16_t handle,
                                  const uint8_t *value, uint8_t value_length){
    host_event_t event;
    host_event_begin(&event, HCI_EVENT_COMMAND_COMPLETE);
    host_event_add_u8(&event, 1);               /* num_hci_command_packets */
    host_event_add_u16(&event, opcode);
    host_event_add_u8(&event, status);
    host_event_add_u16(&event, handle);
    host_event_add_bytes(&event, value, value_length);
    send_event(&event);
}

static void send_command_complete_raw(uint16_t opcode, const uint8_t *ret, uint8_t ret_length){
    host_event_t event;
    host_event_begin(&event, HCI_EVENT_COMMAND_COMPLETE);
    host_event_add_u8(&event, 1);
    host_event_add_u16(&event, opcode);
    host_event_add_bytes(&event, ret, ret_length);
    send_event(&event);
}

static void make_frame(uint8_t frame[46], uint8_t report_id, uint32_t ticks){
    memset(frame, 0, 46);
    frame[0] = 0xA1;
    frame[1] = report_id;
    frame[30] = (uint8_t) (ticks & 0xffu);
    frame[31] = (uint8_t) ((ticks >> 8) & 0xffu);
    frame[32] = (uint8_t) ((ticks >> 16) & 0xffu);
    frame[33] = (uint8_t) ((ticks >> 24) & 0xffu);
}

static void linkdiag_reset(void){
    /* Drop any link a previous test left up, then reset the fakes and timers. */
    for (uint16_t handle = 0x40; handle < 0x50; handle++) send_disconnect(handle);
    host_reset();
    wmr_linkdiag_init();
}

static void connect_left(void){
    host.status[WMR_HAND_LEFT].address_source = WMR_ADDR_STORED;
    memcpy(host.status[WMR_HAND_LEFT].address, LEFT_ADDR, 6);
    send_connect(ERROR_CODE_SUCCESS, LEFT_HANDLE, LEFT_ADDR);
}

static void test_idle_and_bad_input(void){
    printf("linkdiag: idle state, invalid hands and invalid reports\n");
    linkdiag_reset();
    CHECK(host_pending_timers() == 1);          /* the polling cycle */
    wmr_linkdiag_hand_t hand;
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(!hand.connected && hand.mode == WMR_MODE_ACTIVE);
    CHECK(hand.reports == 0 && hand.missing == 0 && hand.health.updates == 0);

    wmr_linkdiag_get_hand(2, &hand);
    CHECK(!hand.connected && hand.reports == 0);

    uint8_t frame[46];
    make_frame(frame, 0x01, 1000);
    wmr_linkdiag_note_report(2, frame, sizeof(frame));
    wmr_linkdiag_note_report(WMR_HAND_LEFT, NULL, 46);
    wmr_linkdiag_note_report(WMR_HAND_LEFT, frame, 45);
    wmr_linkdiag_note_report(WMR_HAND_LEFT, frame, 47);
    make_frame(frame, 0x09, 1000);
    wmr_linkdiag_note_report(WMR_HAND_LEFT, frame, sizeof(frame));
    make_frame(frame, 0x01, 1000);
    frame[0] = 0xA2;
    wmr_linkdiag_note_report(WMR_HAND_LEFT, frame, sizeof(frame));
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.reports == 0 && hand.missing == 0);

    /* Empty and truncated events must not disturb the state or crash. */
    uint8_t empty[4] = {HCI_EVENT_CONNECTION_COMPLETE, 0, 0, 0};
    wmr_linkdiag_on_hci_event(empty, sizeof(empty));
    uint8_t mode_short[4] = {HCI_EVENT_MODE_CHANGE, 0, 0, 0};
    wmr_linkdiag_on_hci_event(mode_short, sizeof(mode_short));
    uint8_t complete_short[4] = {HCI_EVENT_COMMAND_COMPLETE, 0, 0, 0};
    wmr_linkdiag_on_hci_event(complete_short, sizeof(complete_short));
    uint8_t request_short[4] = {HCI_EVENT_CONNECTION_REQUEST, 0, 0, 0};
    wmr_linkdiag_on_hci_event(request_short, sizeof(request_short));
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(!hand.connected && hand.health.updates == 0);
}

static void test_connection_and_reports(void){
    printf("linkdiag: connection mapping and tick-based loss tracking\n");
    linkdiag_reset();
    connect_left();
    wmr_linkdiag_hand_t hand;
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.connected && hand.mode == WMR_MODE_ACTIVE);

    uint8_t frame[46];
    uint32_t ticks = 1000;
    for (unsigned i = 0; i < 20; i++){
        make_frame(frame, 0x01, ticks);
        wmr_linkdiag_note_report(WMR_HAND_LEFT, frame, sizeof(frame));
        ticks += 50000u;
    }
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.reports == 20 && hand.missing == 0 && hand.discontinuities == 0);
    CHECK(hand.period_ticks == 50000u && hand.loss_permille == 0);

    ticks += 50000u;                            /* one report never arrived */
    make_frame(frame, 0x01, ticks);
    wmr_linkdiag_note_report(WMR_HAND_LEFT, frame, sizeof(frame));
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.reports == 21 && hand.missing == 1);

    ticks += WMR_DEVLOSS_DISCONTINUITY_TICKS + 1u;   /* link outage, not loss */
    make_frame(frame, 0x01, ticks);
    wmr_linkdiag_note_report(WMR_HAND_LEFT, frame, sizeof(frame));
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.discontinuities == 1 && hand.missing == 1);

    /* A disconnect clears the per-link counters. */
    send_disconnect(LEFT_HANDLE);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(!hand.connected && hand.reports == 0 && hand.missing == 0);
}

static void test_connection_edges(void){
    printf("linkdiag: failed and unknown connection events\n");
    linkdiag_reset();
    host.status[WMR_HAND_LEFT].address_source = WMR_ADDR_STORED;
    memcpy(host.status[WMR_HAND_LEFT].address, LEFT_ADDR, 6);
    send_connect(0x04, LEFT_HANDLE, LEFT_ADDR);             /* page timeout */
    wmr_linkdiag_hand_t hand;
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(!hand.connected);

    send_connect(ERROR_CODE_SUCCESS, LEFT_HANDLE, RIGHT_ADDR);   /* not a known hand */
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(!hand.connected);

    send_disconnect(0x0099);                                /* unknown handle */
    send_mode_change(ERROR_CODE_SUCCESS, 0x0099, WMR_MODE_SNIFF, 40);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(!hand.connected && hand.mode == WMR_MODE_ACTIVE);

    connect_left();
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.connected);
}

static void test_polling(void){
    printf("linkdiag: one command at a time, replies parsed per hand\n");
    linkdiag_reset();
    connect_left();
    host_advance_ms(WMR_CFG_LINKDIAG_INTERVAL_MS);
    CHECK(host_hci.calls == 0);                 /* the pace timer runs first */
    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 1);
    CHECK(host_hci.last_opcode == hci_read_rssi.opcode);
    CHECK(host_hci.last_handle == LEFT_HANDLE);

    const uint8_t rssi = (uint8_t) (int8_t) -30;
    send_command_complete(hci_read_rssi.opcode, ERROR_CODE_SUCCESS, LEFT_HANDLE, &rssi, 1);
    wmr_linkdiag_hand_t hand;
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.valid[WMR_LINKCMD_RSSI] && hand.health.rssi == -30);
    CHECK(hand.health.updates == 1);

    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 2);
    CHECK(host_hci.last_opcode == hci_read_failed_contact_counter.opcode);
    const uint8_t failed[2] = {7, 0};
    send_command_complete(hci_read_failed_contact_counter.opcode, ERROR_CODE_SUCCESS,
                          LEFT_HANDLE, failed, 2);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.valid[WMR_LINKCMD_FAILED_CONTACT] && hand.health.failed_contacts == 7);

    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 3);
    CHECK(host_hci.last_opcode == hci_read_link_quality.opcode);
    const uint8_t quality = 200;
    send_command_complete(hci_read_link_quality.opcode, ERROR_CODE_SUCCESS, LEFT_HANDLE, &quality, 1);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.valid[WMR_LINKCMD_QUALITY] && hand.health.quality == 200);
    CHECK(hand.health.updates == 3);

    /* The cycle is done: no more commands until the next 5 s tick. */
    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 3);
    host_advance_ms(WMR_CFG_LINKDIAG_INTERVAL_MS);
    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 4);

    /* A reply for the wrong command is ignored; the right one is accepted. */
    send_command_complete(hci_read_link_quality.opcode, ERROR_CODE_SUCCESS, LEFT_HANDLE, &quality, 1);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.updates == 3);
    send_command_complete(hci_read_rssi.opcode, ERROR_CODE_SUCCESS, LEFT_HANDLE, &rssi, 1);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.updates == 4);

    /* A truncated reply is consumed but records nothing. */
    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 5);
    CHECK(host_hci.last_opcode == hci_read_failed_contact_counter.opcode);
    send_command_complete(hci_read_failed_contact_counter.opcode, ERROR_CODE_SUCCESS,
                          LEFT_HANDLE, NULL, 0);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.updates == 4);
    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 6);                 /* polling continued */

    /* A controller that refuses the command keeps the previous value. */
    send_command_complete(hci_read_link_quality.opcode, 0x01, LEFT_HANDLE, NULL, 0);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(!hand.health.valid[WMR_LINKCMD_QUALITY]);
    CHECK(hand.health.status[WMR_LINKCMD_QUALITY] == 0x01);

    /* A stray reply with nothing outstanding is ignored. */
    send_command_complete(hci_read_rssi.opcode, ERROR_CODE_SUCCESS, LEFT_HANDLE, &rssi, 1);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.updates == 4);
}

static void test_polling_gate(void){
    printf("linkdiag: no command while the controller cannot take one\n");
    linkdiag_reset();
    connect_left();
    host_hci.can_send = false;
    host_advance_ms(WMR_CFG_LINKDIAG_INTERVAL_MS);
    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS * 4u);
    CHECK(host_hci.calls == 0);
    host_hci.can_send = true;
    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 1);
}

static void test_modes(void){
    printf("linkdiag: mode changes logged once\n");
    linkdiag_reset();
    connect_left();

    host_capture_begin();
    send_mode_change(ERROR_CODE_SUCCESS, LEFT_HANDLE, WMR_MODE_SNIFF, 40);
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "LEFT link mode: active -> sniff") != NULL);
    wmr_linkdiag_hand_t hand;
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.mode == WMR_MODE_SNIFF);

    host_capture_begin();
    send_mode_change(ERROR_CODE_SUCCESS, LEFT_HANDLE, WMR_MODE_SNIFF, 40);
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "link mode") == NULL);

    send_mode_change(0x01, LEFT_HANDLE, WMR_MODE_ACTIVE, 0);    /* failed command */
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.mode == WMR_MODE_SNIFF);

    host_capture_begin();
    send_mode_change(ERROR_CODE_SUCCESS, LEFT_HANDLE, WMR_MODE_ACTIVE, 0);
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "sniff -> active") != NULL);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.mode == WMR_MODE_ACTIVE);
}

static void test_identified_link(void){
    printf("linkdiag: a link identified after connection\n");
    linkdiag_reset();
    wmr_linkdiag_link_identified(WMR_HAND_RIGHT, RIGHT_HANDLE);
    wmr_linkdiag_hand_t hand;
    wmr_linkdiag_get_hand(WMR_HAND_RIGHT, &hand);
    CHECK(hand.connected && hand.mode == WMR_MODE_ACTIVE);

    uint8_t frame[46];
    for (unsigned i = 0; i < 20; i++){
        make_frame(frame, 0x01, 1000u + i * 50000u);
        wmr_linkdiag_note_report(WMR_HAND_RIGHT, frame, sizeof(frame));
    }
    wmr_linkdiag_get_hand(WMR_HAND_RIGHT, &hand);
    CHECK(hand.reports == 20);

    wmr_linkdiag_link_identified(WMR_HAND_RIGHT, RIGHT_HANDLE);     /* same link: no reset */
    wmr_linkdiag_get_hand(WMR_HAND_RIGHT, &hand);
    CHECK(hand.reports == 20);

    wmr_linkdiag_link_identified(WMR_HAND_RIGHT, 0x0042);           /* new link: reset */
    wmr_linkdiag_get_hand(WMR_HAND_RIGHT, &hand);
    CHECK(hand.connected && hand.reports == 0 && hand.missing == 0);

    wmr_linkdiag_link_identified(2, 0x0043);                        /* out of range: no-op */
    wmr_linkdiag_get_hand(2, &hand);
    CHECK(!hand.connected);
}

static void test_le_buffer_event(void){
    printf("linkdiag: LE buffer-size reply is logged, not treated as poll data\n");
    linkdiag_reset();
    connect_left();
    const uint8_t buffers[4] = {0x00, 0xfb, 0x00, 0x0c};
    host_capture_begin();
    send_command_complete_raw(hci_le_read_buffer_size.opcode, buffers, 4);
    host_capture_end(output, sizeof(output));
    CHECK(strstr(output, "HCI LE buffers: ACL packet length 251 bytes, 12 packets") != NULL);
    wmr_linkdiag_hand_t hand;
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.updates == 0);

    /* A poll command outstanding is not consumed by the buffer-size reply. */
    host_advance_ms(WMR_CFG_LINKDIAG_INTERVAL_MS);
    host_advance_ms(WMR_CFG_LINKDIAG_PACE_MS);
    CHECK(host_hci.calls == 1);
    send_command_complete_raw(hci_le_read_buffer_size.opcode, buffers, 4);
    const uint8_t rssi = (uint8_t) (int8_t) -5;
    send_command_complete(hci_read_rssi.opcode, ERROR_CODE_SUCCESS, LEFT_HANDLE, &rssi, 1);
    wmr_linkdiag_get_hand(WMR_HAND_LEFT, &hand);
    CHECK(hand.health.updates == 1 && hand.health.rssi == -5);
}

static void test_dump_sink(void){
    printf("linkdiag: VHCI drop and error log classification\n");
    linkdiag_reset();
    CHECK(wmr_linkdiag_vhci_drops() == 0 && wmr_linkdiag_log_errors() == 0);

    host_emit_log(HCI_DUMP_LOG_LEVEL_ERROR,
                  "transport_recv_pkt_cb packet %u, space %u -> dropping packet");
    CHECK(wmr_linkdiag_vhci_drops() == 1 && wmr_linkdiag_log_errors() == 1);

    host_emit_log(HCI_DUMP_LOG_LEVEL_ERROR, "some other error");
    CHECK(wmr_linkdiag_vhci_drops() == 1 && wmr_linkdiag_log_errors() == 2);

    host_emit_log(HCI_DUMP_LOG_LEVEL_INFO,
                  "transport_recv_pkt_cb packet %u, space %u -> dropping packet");
    CHECK(wmr_linkdiag_vhci_drops() == 1 && wmr_linkdiag_log_errors() == 2);

    const uint8_t pdu[9] = {0};
    host_emit_packet(HCI_ACL_DATA_PACKET, pdu, sizeof(pdu));
    host_emit_packet(HCI_EVENT_PACKET, pdu, sizeof(pdu));
    CHECK(wmr_linkdiag_vhci_drops() == 1 && wmr_linkdiag_log_errors() == 2);
}

int main(void){
    test_idle_and_bad_input();
    test_connection_and_reports();
    test_connection_edges();
    test_polling();
    test_polling_gate();
    test_modes();
    test_identified_link();
    test_le_buffer_event();
    test_dump_sink();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
