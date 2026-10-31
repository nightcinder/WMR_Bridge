/*
 * Fakes for the BTstack / ESP-IDF side of the firmware, and for the firmware
 * modules a host test links against but does not test. Used by
 * tests/test_console.c, tests/test_cfgmgr.c and tests/test_linkdiag.c.
 *
 * The clock is fake: nothing sleeps, and timers only fire inside
 * host_advance_ms(). Everything is reset by host_reset().
 */

#ifndef HOST_STUBS_H
#define HOST_STUBS_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "wmr_controller.h"
#include "wmr_transport.h"

typedef struct {
    wmr_controller_status_t status[WMR_NUM_CONTROLLERS];
    uint16_t cid[WMR_NUM_CONTROLLERS];

    unsigned connect_calls[WMR_NUM_CONTROLLERS];
    unsigned disconnect_calls[WMR_NUM_CONTROLLERS];
    unsigned discover_calls;
    unsigned forget_address_calls;
    wmr_forget_result_t forget_address_result;
    unsigned forget_bond_calls;
    int      forget_bond_hand;
    wmr_bond_reset_result_t forget_bond_result;
    unsigned gap_delete_all_calls;
    unsigned le_db_remove_calls;
    int      le_db_count;
    int      le_db_max_count;

    wmr_transport_info_t info;
    wmr_stats_t stats;
    wmr_echo_state_t echo;
} host_fake_t;

extern host_fake_t host;

typedef struct {
    unsigned calls;
    uint16_t last_cid;
    uint8_t  last_report_id;
    uint16_t last_len;
    uint8_t  last_report[80];
    uint8_t  status;            /* returned to the caller; 0 = success */
} host_hid_t;

extern host_hid_t host_hid;

typedef struct {
    unsigned calls;
    uint16_t last_opcode;
    uint16_t last_handle;
    bool     can_send;
} host_hci_t;

extern host_hci_t host_hci;

/* An HCI event packet: [0] event code, [1] parameter length, [2..] parameters. */
typedef struct {
    uint8_t  data[64];
    uint16_t len;
} host_event_t;

void host_reset(void);

void     host_advance_ms(uint32_t ms);
uint64_t host_now_us(void);
unsigned host_pending_timers(void);

void host_stdin_send(char c);

/* Redirect stdout into a buffer for the duration of one assertion. */
void   host_capture_begin(void);
size_t host_capture_end(char *out, size_t cap);

/* Call the registered BTstack dump sink, as BTstack's log macro would. */
void host_emit_log(int log_level, const char *format, ...);

/* Call the registered dump sink's packet hook, as BTstack would for an HCI packet. */
void host_emit_packet(uint8_t packet_type, const uint8_t *packet, uint16_t length);

void host_event_begin(host_event_t *event, uint8_t code);
void host_event_add_u8(host_event_t *event, uint8_t value);
void host_event_add_u16(host_event_t *event, uint16_t value);
void host_event_add_bytes(host_event_t *event, const uint8_t *bytes, size_t length);
void host_event_finish(host_event_t *event);

#endif /* HOST_STUBS_H */
