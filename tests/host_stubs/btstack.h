/*
 * Host-test stand-in for the small part of BTstack's public API that the
 * BTstack-side firmware modules use. The packet layout matches BTstack's:
 * an event buffer is [0] event code, [1] parameter length, [2..] parameters.
 * Accessors are inline so a test can build an event with plain bytes.
 */

#ifndef HOST_STUBS_BTSTACK_H
#define HOST_STUBS_BTSTACK_H

#include <stdint.h>
#include <stdbool.h>
#include <stdarg.h>
#include <string.h>
#include "btstack_run_loop.h"

typedef uint16_t hci_con_handle_t;
typedef uint8_t  bd_addr_t[6];

typedef struct {
    uint16_t opcode;
} hci_cmd_t;

/* ---- constants ---------------------------------------------------------- */

#define BTSTACK_EVENT_STATE                 0x30

#define HCI_STATE_OFF                       0
#define HCI_STATE_INITIALIZING              1
#define HCI_STATE_WORKING                   2
#define HCI_STATE_FALLING_ASLEEP            3
#define HCI_STATE_SLEEPING                  4
#define HCI_STATE_HALTING                   5
#define HCI_STATE_HALTED                    6

#define HCI_EVENT_CONNECTION_COMPLETE       0x03
#define HCI_EVENT_CONNECTION_REQUEST        0x04
#define HCI_EVENT_DISCONNECTION_COMPLETE    0x05
#define HCI_EVENT_COMMAND_COMPLETE          0x0e
#define HCI_EVENT_MODE_CHANGE               0x14

#define HCI_LINK_TYPE_ACL                   0x01

#define HCI_COMMAND_DATA_PACKET             0x01
#define HCI_ACL_DATA_PACKET                 0x02
#define HCI_EVENT_PACKET                    0x04

#define ERROR_CODE_SUCCESS                  0x00
#define ERROR_CODE_COMMAND_DISALLOWED       0x0c

#define HCI_DUMP_LOG_LEVEL_DEBUG            0
#define HCI_DUMP_LOG_LEVEL_INFO             1
#define HCI_DUMP_LOG_LEVEL_ERROR            2
#define HCI_DUMP_LOG_LEVEL_OFF              3

/* ---- commands ----------------------------------------------------------- */

extern const hci_cmd_t hci_read_rssi;
extern const hci_cmd_t hci_read_failed_contact_counter;
extern const hci_cmd_t hci_read_link_quality;
extern const hci_cmd_t hci_le_read_buffer_size;
extern const hci_cmd_t hci_le_read_buffer_size_v2;

bool     hci_can_send_command_packet_now(void);
uint8_t  hci_send_cmd(const hci_cmd_t *cmd, ...);

/* ---- HID host ----------------------------------------------------------- */

uint8_t hid_host_send_report(hci_con_handle_t hid_cid, uint8_t report_id,
                             const uint8_t *report, uint16_t report_len);

/* ---- GAP / LE device DB ------------------------------------------------- */

void gap_delete_all_link_keys(void);
int  le_device_db_count(void);
int  le_device_db_max_count(void);
void le_device_db_remove(int index);

/* ---- stdin -------------------------------------------------------------- */

void btstack_stdin_setup(void (*stdin_handler)(char c));

/* ---- HCI dump ----------------------------------------------------------- */

typedef struct {
    void (*reset)(void);
    void (*packet)(uint8_t packet_type, uint8_t in, uint8_t *packet, uint16_t len);
    void (*log_message)(int log_level, const char *format, va_list argptr);
} hci_dump_t;

void hci_dump_init(const hci_dump_t *dump);
void hci_dump_enable_packet_log(bool enabled);
void hci_dump_enable_log_level(int log_level, bool enabled);

/* ---- event accessors ---------------------------------------------------- */

static inline uint8_t hci_event_packet_get_type(const uint8_t *packet){
    return packet[0];
}

static inline uint8_t btstack_event_state_get_state(const uint8_t *packet){
    return packet[2];
}

static inline uint8_t hci_event_connection_complete_get_status(const uint8_t *packet){
    return packet[2];
}

static inline hci_con_handle_t hci_event_connection_complete_get_connection_handle(const uint8_t *packet){
    return (hci_con_handle_t) (packet[3] | (packet[4] << 8));
}

static inline void hci_event_connection_complete_get_bd_addr(const uint8_t *packet, bd_addr_t address){
    memcpy(address, &packet[5], 6);
}

static inline uint8_t hci_event_connection_complete_get_link_type(const uint8_t *packet){
    return packet[11];
}

static inline void hci_event_connection_request_get_bd_addr(const uint8_t *packet, bd_addr_t address){
    memcpy(address, &packet[2], 6);
}

static inline uint8_t hci_event_connection_request_get_link_type(const uint8_t *packet){
    return packet[11];
}

static inline hci_con_handle_t hci_event_disconnection_complete_get_connection_handle(const uint8_t *packet){
    return (hci_con_handle_t) (packet[3] | (packet[4] << 8));
}

static inline uint8_t hci_event_mode_change_get_status(const uint8_t *packet){
    return packet[2];
}

static inline hci_con_handle_t hci_event_mode_change_get_handle(const uint8_t *packet){
    return (hci_con_handle_t) (packet[3] | (packet[4] << 8));
}

static inline uint8_t hci_event_mode_change_get_mode(const uint8_t *packet){
    return packet[5];
}

static inline uint16_t hci_event_mode_change_get_interval(const uint8_t *packet){
    return (uint16_t) (packet[6] | (packet[7] << 8));
}

static inline uint16_t hci_event_command_complete_get_command_opcode(const uint8_t *packet){
    return (uint16_t) (packet[3] | (packet[4] << 8));
}

static inline const uint8_t *hci_event_command_complete_get_return_parameters(const uint8_t *packet){
    return &packet[5];
}

static inline const char *bd_addr_to_str(const bd_addr_t address){
    static char text[18];
    static const char hex[] = "0123456789ABCDEF";
    for (unsigned i = 0; i < 6; i++){
        text[i * 3]     = hex[address[i] >> 4];
        text[i * 3 + 1] = hex[address[i] & 0x0f];
        text[i * 3 + 2] = i == 5 ? '\0' : ':';
    }
    return text;
}

#endif /* HOST_STUBS_BTSTACK_H */
