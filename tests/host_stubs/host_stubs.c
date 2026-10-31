#define _POSIX_C_SOURCE 200809L

/* Fakes for the platform and for the firmware modules the host tests link but do not test.
 * See host_stubs.h. */

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "btstack_config.h"
#include "btstack.h"
#include "esp_app_desc.h"
#include "esp_timer.h"
#include "host_stubs.h"

host_fake_t host;
host_hid_t  host_hid;
host_hci_t  host_hci;

/* ---- fake clock and run loop -------------------------------------------- */

typedef struct {
    btstack_timer_source_t *timer;
    uint32_t due_ms;
} host_timer_entry_t;

#define HOST_MAX_TIMERS 16
static host_timer_entry_t timer_entries[HOST_MAX_TIMERS];
static unsigned timer_count;
static uint64_t now_us;

static uint32_t now_ms(void){
    return (uint32_t) (now_us / 1000u);
}

uint32_t btstack_run_loop_get_time_ms(void){
    return now_ms();
}

int64_t esp_timer_get_time(void){
    return (int64_t) now_us;
}

void btstack_run_loop_set_timer(btstack_timer_source_t *timer, uint32_t timeout_ms){
    timer->timeout_ms = timeout_ms;
    timer->due_ms = now_ms() + timeout_ms;
}

void btstack_run_loop_add_timer(btstack_timer_source_t *timer){
    for (unsigned i = 0; i < timer_count; i++){
        if (timer_entries[i].timer == timer) return;
    }
    if (timer_count >= HOST_MAX_TIMERS) return;
    timer_entries[timer_count].timer = timer;
    timer_entries[timer_count].due_ms = timer->due_ms;
    timer_count++;
    timer->in_list = true;
}

int btstack_run_loop_remove_timer(btstack_timer_source_t *timer){
    for (unsigned i = 0; i < timer_count; i++){
        if (timer_entries[i].timer != timer) continue;
        for (unsigned j = i + 1; j < timer_count; j++) timer_entries[j - 1] = timer_entries[j];
        timer_count--;
        timer->in_list = false;
        return 1;
    }
    return 0;
}

void btstack_run_loop_set_timer_context(btstack_timer_source_t *timer, void *context){
    timer->context = context;
}

void *btstack_run_loop_get_timer_context(btstack_timer_source_t *timer){
    return timer->context;
}

void host_advance_ms(uint32_t ms){
    const uint32_t target_ms = now_ms() + ms;
    unsigned guard = 0;
    while (guard++ < 100000u){
        int best = -1;
        for (unsigned i = 0; i < timer_count; i++){
            if (timer_entries[i].due_ms > target_ms) continue;
            if (best < 0 || timer_entries[i].due_ms < timer_entries[best].due_ms) best = (int) i;
        }
        if (best < 0) break;
        btstack_timer_source_t *timer = timer_entries[best].timer;
        now_us = (uint64_t) timer_entries[best].due_ms * 1000u;
        for (unsigned j = (unsigned) best + 1; j < timer_count; j++) timer_entries[j - 1] = timer_entries[j];
        timer_count--;
        timer->in_list = false;
        if (timer->process) timer->process(timer);
    }
    if ((uint64_t) target_ms * 1000u > now_us) now_us = (uint64_t) target_ms * 1000u;
}

uint64_t host_now_us(void){
    return now_us;
}

unsigned host_pending_timers(void){
    return timer_count;
}

/* ---- stdin --------------------------------------------------------------- */

static void (*stdin_handler)(char c);

void btstack_stdin_setup(void (*handler)(char c)){
    stdin_handler = handler;
}

void host_stdin_send(char c){
    if (stdin_handler) stdin_handler(c);
}

/* ---- stdout capture ------------------------------------------------------ */

static int saved_stdout_fd = -1;
static FILE *capture_file;

void host_capture_begin(void){
    fflush(stdout);
    capture_file = tmpfile();
    if (!capture_file) return;
    saved_stdout_fd = dup(STDOUT_FILENO);
    if (saved_stdout_fd < 0){
        fclose(capture_file);
        capture_file = NULL;
        return;
    }
    dup2(fileno(capture_file), STDOUT_FILENO);
}

size_t host_capture_end(char *out, size_t cap){
    if (cap == 0) return 0;
    out[0] = '\0';
    fflush(stdout);
    if (saved_stdout_fd >= 0){
        dup2(saved_stdout_fd, STDOUT_FILENO);
        close(saved_stdout_fd);
        saved_stdout_fd = -1;
    }
    if (!capture_file) return 0;
    fseek(capture_file, 0, SEEK_SET);
    size_t n = fread(out, 1, cap - 1, capture_file);
    out[n] = '\0';
    fclose(capture_file);
    capture_file = NULL;
    return n;
}

/* ---- HCI dump sink ------------------------------------------------------- */

static const hci_dump_t *dump_sink;

void hci_dump_init(const hci_dump_t *dump){
    dump_sink = dump;
}

void hci_dump_enable_packet_log(bool enabled){
    (void) enabled;
}

void hci_dump_enable_log_level(int log_level, bool enabled){
    (void) log_level;
    (void) enabled;
}

void host_emit_log(int log_level, const char *format, ...){
    if (!dump_sink || !dump_sink->log_message) return;
    va_list args;
    va_start(args, format);
    dump_sink->log_message(log_level, format, args);
    va_end(args);
}

void host_emit_packet(uint8_t packet_type, const uint8_t *packet, uint16_t length){
    if (!dump_sink || !dump_sink->packet) return;
    dump_sink->packet(packet_type, 1, (uint8_t *) packet, length);
}

/* ---- HCI commands -------------------------------------------------------- */

const hci_cmd_t hci_read_rssi                  = {0x1405};
const hci_cmd_t hci_read_failed_contact_counter = {0x1403};
const hci_cmd_t hci_read_link_quality          = {0x1404};
const hci_cmd_t hci_le_read_buffer_size        = {0x2002};
const hci_cmd_t hci_le_read_buffer_size_v2     = {0x2060};

bool hci_can_send_command_packet_now(void){
    return host_hci.can_send;
}

uint8_t hci_send_cmd(const hci_cmd_t *cmd, ...){
    va_list args;
    va_start(args, cmd);
    host_hci.last_handle = (uint16_t) va_arg(args, int);
    va_end(args);
    host_hci.calls++;
    host_hci.last_opcode = cmd->opcode;
    return ERROR_CODE_SUCCESS;
}

/* ---- HID host ------------------------------------------------------------ */

uint8_t hid_host_send_report(hci_con_handle_t hid_cid, uint8_t report_id,
                             const uint8_t *report, uint16_t report_len){
    host_hid.calls++;
    host_hid.last_cid = hid_cid;
    host_hid.last_report_id = report_id;
    host_hid.last_len = report_len;
    size_t copy = report_len < sizeof(host_hid.last_report) ? report_len : sizeof(host_hid.last_report);
    if (report && copy) memcpy(host_hid.last_report, report, copy);
    return host_hid.status;
}

/* ---- GAP / LE device DB -------------------------------------------------- */

void gap_delete_all_link_keys(void){
    host.gap_delete_all_calls++;
}

int le_device_db_count(void){
    return host.le_db_count;
}

int le_device_db_max_count(void){
    return host.le_db_max_count;
}

void le_device_db_remove(int index){
    (void) index;
    host.le_db_remove_calls++;
}

/* ---- ESP-IDF application description ------------------------------------- */

static const esp_app_desc_t app_desc = {
    .version = "0.3.4-hosttest",
    .project_name = "wmr_bridge",
    .time = "12:00:00",
    .date = "Oct 31 2026",
    .idf_ver = "v6.1-hosttest",
};

const esp_app_desc_t *esp_app_get_description(void){
    return &app_desc;
}

/* ---- firmware modules the tests link but do not exercise ---------------- */

const char *wmr_controller_name(unsigned hand){
    if (hand == WMR_HAND_LEFT) return "LEFT";
    if (hand == WMR_HAND_RIGHT) return "RIGHT";
    return "?";
}

uint16_t wmr_controller_cid(unsigned hand){
    return hand < WMR_NUM_CONTROLLERS ? host.cid[hand] : 0;
}

void wmr_controller_get_status(unsigned hand, wmr_controller_status_t *out){
    if (hand < WMR_NUM_CONTROLLERS){
        *out = host.status[hand];
        return;
    }
    memset(out, 0, sizeof(*out));
}

int wmr_controller_hand_for_address(const uint8_t address[6]){
    for (unsigned i = 0; i < WMR_NUM_CONTROLLERS; i++){
        if (host.status[i].address_source == WMR_ADDR_NONE) continue;
        if (memcmp(host.status[i].address, address, 6) == 0) return (int) i;
    }
    return -1;
}

const char *wmr_controller_address_source_name(wmr_addr_source_t source){
    switch (source){
        case WMR_ADDR_OVERRIDE: return "compile-time override";
        case WMR_ADDR_STORED:   return "discovered, stored";
        default:                return "unknown";
    }
}

wmr_connect_result_t wmr_controller_connect(unsigned hand){
    if (hand < WMR_NUM_CONTROLLERS) host.connect_calls[hand]++;
    return WMR_CONNECT_OK;
}

wmr_disconnect_result_t wmr_controller_disconnect(unsigned hand){
    if (hand < WMR_NUM_CONTROLLERS) host.disconnect_calls[hand]++;
    return WMR_DISCONNECT_OK;
}

wmr_discovery_start_result_t wmr_controller_discover(void){
    host.discover_calls++;
    return WMR_DISCOVERY_STARTED;
}

wmr_forget_result_t wmr_controller_forget_addresses(void){
    host.forget_address_calls++;
    return host.forget_address_result;
}

wmr_bond_reset_result_t wmr_controller_forget_bond(unsigned hand){
    host.forget_bond_calls++;
    host.forget_bond_hand = (int) hand;
    return host.forget_bond_result;
}

const wmr_stats_t *wmr_transport_stats(void){
    return &host.stats;
}

void wmr_transport_get_info(wmr_transport_info_t *info){
    *info = host.info;
}

const wmr_echo_state_t *wmr_transport_echo(void){
    return &host.echo;
}

/* ---- HCI event builders -------------------------------------------------- */

void host_event_begin(host_event_t *event, uint8_t code){
    memset(event, 0, sizeof(*event));
    event->data[0] = code;
    event->len = 2;
}

void host_event_add_u8(host_event_t *event, uint8_t value){
    if (event->len >= sizeof(event->data)) return;
    event->data[event->len++] = value;
}

void host_event_add_u16(host_event_t *event, uint16_t value){
    host_event_add_u8(event, (uint8_t) (value & 0xffu));
    host_event_add_u8(event, (uint8_t) (value >> 8));
}

void host_event_add_bytes(host_event_t *event, const uint8_t *bytes, size_t length){
    for (size_t i = 0; i < length; i++) host_event_add_u8(event, bytes[i]);
}

void host_event_finish(host_event_t *event){
    event->data[1] = (uint8_t) (event->len - 2);
}

/* ---- reset --------------------------------------------------------------- */

void host_reset(void){
    memset(&host, 0, sizeof(host));
    memset(&host_hid, 0, sizeof(host_hid));
    memset(&host_hci, 0, sizeof(host_hci));
    memset(timer_entries, 0, sizeof(timer_entries));
    timer_count = 0;
    now_us = 0;
    stdin_handler = NULL;
    dump_sink = NULL;
    host_hci.can_send = true;
    host.le_db_max_count = 4;
    host.info.queue_capacity = 256;
}
