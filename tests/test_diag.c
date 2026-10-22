/*
 * Host tests for src/wmr_devloss.c and src/wmr_diag.c (the pure parts of the link
 * diagnostics). They do not test the BTstack glue in wmr_linkdiag.c, which needs
 * the firmware build and hardware, and nothing here says anything about what the
 * real controllers or radio do.
 */

#include <stdio.h>
#include <string.h>
#include "wmr_config.h"
#include "wmr_devloss.h"
#include "wmr_diag.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

#define P 50000u            /* 5 ms at 100 ns per tick: the 200 Hz controller */

static void test_clean_stream(void){
    printf("devloss: clean 200 Hz stream\n");
    wmr_devloss_t t;
    wmr_devloss_reset(&t);
    uint32_t ticks = 1000;
    for (int i = 0; i < 300; i++){ wmr_devloss_add(&t, ticks); ticks += P; }
    CHECK(t.missing == 0 && t.discontinuities == 0);
    CHECK(wmr_devloss_period(&t) == P);
    CHECK(t.reports == 300 && wmr_devloss_loss_permille(&t) == 0);
}

static void test_warmup(void){
    printf("devloss: nothing counted before warm-up\n");
    wmr_devloss_t t;
    wmr_devloss_reset(&t);
    uint32_t ticks = 0;
    for (int i = 0; i < WMR_DEVLOSS_WARMUP; i++){            /* WARMUP reports = WARMUP-1 deltas */
        wmr_devloss_add(&t, ticks);
        ticks += (i % 3 == 2) ? 3 * P : P;                  /* some loss, but too early to count */
    }
    CHECK(wmr_devloss_period(&t) == 0 && t.missing == 0);
}

static void test_known_loss_pattern(void){
    printf("devloss: 3 of every 10 reports lost (runs of 3), exact count\n");
    wmr_devloss_t t;
    wmr_devloss_reset(&t);
    for (uint32_t slot = 0; slot < 1000; slot++){
        if (slot % 10 < 7) wmr_devloss_add(&t, 123456u + slot * P);
    }
    /* 100 cycles; the delta from the last present report of a cycle to the first of the next is
     * 4 periods = 3 missing; there are 99 such boundaries. Warm-up deltas are counted retroactively. */
    CHECK(wmr_devloss_period(&t) == P);
    CHECK(t.reports == 700);
    CHECK(t.missing == 99u * 3u);
    CHECK(wmr_devloss_loss_permille(&t) == (297u * 1000u + (997u / 2)) / 997u);     /* 298 */
    CHECK(t.discontinuities == 0);
}

static void test_isolated_loss(void){
    printf("devloss: one in three lost\n");
    wmr_devloss_t t;
    wmr_devloss_reset(&t);
    for (uint32_t slot = 0; slot < 900; slot++){
        if (slot % 3 != 2) wmr_devloss_add(&t, slot * P);
    }
    CHECK(wmr_devloss_period(&t) == P);
    CHECK(t.missing == 299);        /* 300 cycles, the last has nothing after it */
}

static void test_jitter(void){
    printf("devloss: timing jitter is not loss\n");
    wmr_devloss_t t;
    wmr_devloss_reset(&t);
    uint32_t ticks = 0;
    for (int i = 0; i < 500; i++){ wmr_devloss_add(&t, ticks); ticks += (i & 1) ? 51000u : 49000u; }
    CHECK(t.missing == 0);
}

static void test_wrap(void){
    printf("devloss: 32-bit tick wrap\n");
    wmr_devloss_t t;
    wmr_devloss_reset(&t);
    uint32_t ticks = 0xFFFFFFFFu - 100u * P;
    for (int i = 0; i < 400; i++){ wmr_devloss_add(&t, ticks); ticks += P; }     /* crosses 2^32 */
    CHECK(t.missing == 0 && t.discontinuities == 0 && wmr_devloss_period(&t) == P);
}

static void test_discontinuity_and_duplicates(void){
    printf("devloss: restart jump is a discontinuity; repeated tick is ignored\n");
    wmr_devloss_t t;
    wmr_devloss_reset(&t);
    uint32_t ticks = 0;
    for (int i = 0; i < 100; i++){ wmr_devloss_add(&t, ticks); ticks += P; }
    ticks += 60000000u;                                  /* 6 s gap */
    for (int i = 0; i < 100; i++){ wmr_devloss_add(&t, ticks); ticks += P; }
    CHECK(t.discontinuities == 1 && t.missing == 0);
    uint32_t before = t.reports;
    wmr_devloss_add(&t, ticks - P);                      /* same tick value as the last one */
    CHECK(t.reports == before + 1 && t.missing == 0 && t.discontinuities == 1);
}

static void test_bounded_window(void){
    printf("devloss: long run, window stays bounded and the estimate does not drift\n");
    wmr_devloss_t t;
    wmr_devloss_reset(&t);
    uint32_t slot = 0, sent = 0;
    for (int n = 0; n < 20000; n++, slot++){
        if (slot % 10 < 7){ wmr_devloss_add(&t, slot * P); sent++; }
    }
    CHECK(t.count <= WMR_DEVLOSS_WINDOW);
    CHECK(wmr_devloss_period(&t) == P);
    uint32_t pm = wmr_devloss_loss_permille(&t);
    CHECK(pm >= 295 && pm <= 301);                       /* 30 % */
    CHECK(sizeof(wmr_devloss_t) < 1200);
    (void) sent;
}

static void test_frame_ticks(void){
    printf("devloss: tick counter from a bridge frame\n");
    uint8_t f[46];
    memset(f, 0, sizeof(f));
    f[0] = 0xA1; f[1] = 0x01;
    f[2 + 28] = 0x78; f[2 + 29] = 0x56; f[2 + 30] = 0x34; f[2 + 31] = 0x12;
    uint32_t ticks = 0;
    CHECK(wmr_devloss_ticks_from_frame(f, 46, &ticks) && ticks == 0x12345678u);
    CHECK(!wmr_devloss_ticks_from_frame(f, 45, &ticks));
    CHECK(!wmr_devloss_ticks_from_frame(f, 79, &ticks));          /* a config response is not an input report */
    f[1] = 0x02;
    CHECK(!wmr_devloss_ticks_from_frame(f, 46, &ticks));
    f[1] = 0x01; f[0] = 0xA2;
    CHECK(!wmr_devloss_ticks_from_frame(f, 46, &ticks));
    CHECK(!wmr_devloss_ticks_from_frame(NULL, 46, &ticks));
}

static void test_classify(void){
    printf("diag: log classification (the silent VHCI drop)\n");
    /* The literal BTstack passes: hci_transport_esp32_vhci.c, host_recv_pkt_cb. */
    const char *vhci = "transport_recv_pkt_cb packet %u, space %u -> dropping packet";
    CHECK(wmr_diag_classify_log(true, vhci) == WMR_LOG_VHCI_DROP);
    CHECK(wmr_diag_classify_log(false, vhci) == WMR_LOG_IGNORE);
    CHECK(wmr_diag_classify_log(true, "l2cap: unexpected event %u") == WMR_LOG_OTHER_ERROR);
    CHECK(wmr_diag_classify_log(true, "dropping packet") == WMR_LOG_OTHER_ERROR);     /* needs the transport tag too */
    CHECK(wmr_diag_classify_log(false, "info message") == WMR_LOG_IGNORE);
    CHECK(wmr_diag_classify_log(true, NULL) == WMR_LOG_IGNORE);
}

static void test_mode_tracker(void){
    printf("diag: mode changes are logged once per change\n");
    wmr_mode_tracker_t m;
    memset(&m, 0xFF, sizeof(m));
    wmr_mode_tracker_reset(&m, WMR_HAND_LEFT);
    wmr_mode_tracker_reset(&m, WMR_HAND_RIGHT);
    uint8_t prev = 99;
    CHECK(!wmr_mode_tracker_update(&m, WMR_HAND_LEFT, WMR_MODE_ACTIVE, &prev));     /* already active */
    CHECK(wmr_mode_tracker_update(&m, WMR_HAND_LEFT, WMR_MODE_SNIFF, &prev) && prev == WMR_MODE_ACTIVE);
    CHECK(!wmr_mode_tracker_update(&m, WMR_HAND_LEFT, WMR_MODE_SNIFF, &prev));      /* repeat: no new line */
    CHECK(!wmr_mode_tracker_update(&m, WMR_HAND_RIGHT, WMR_MODE_ACTIVE, &prev));    /* hands are independent */
    CHECK(wmr_mode_tracker_update(&m, WMR_HAND_LEFT, WMR_MODE_ACTIVE, &prev) && prev == WMR_MODE_SNIFF);
    CHECK(!wmr_mode_tracker_update(&m, 7, WMR_MODE_SNIFF, &prev));
    wmr_mode_tracker_reset(&m, WMR_HAND_LEFT);                                     /* new link starts active */
    CHECK(!wmr_mode_tracker_update(&m, WMR_HAND_LEFT, WMR_MODE_ACTIVE, &prev));
    CHECK(strcmp(wmr_mode_name(WMR_MODE_SNIFF), "sniff") == 0 && strcmp(wmr_mode_name(9), "unknown") == 0);

    char line[128];
    wmr_mode_format(line, sizeof(line), "LEFT", WMR_MODE_ACTIVE, WMR_MODE_SNIFF, 40, 12345678u);
    CHECK(strcmp(line, "LEFT link mode: active -> sniff (interval 40 slots = 25.00 ms) at t=12.345 s") == 0);
    wmr_mode_format(line, sizeof(line), "RIGHT", WMR_MODE_SNIFF, WMR_MODE_ACTIVE, 0, 7000u);
    CHECK(strcmp(line, "RIGHT link mode: sniff -> active (interval 0 slots = 0.00 ms) at t=0.007 s") == 0);
}

static void test_link_replies(void){
    printf("diag: Read RSSI / Failed Contact Counter / Link Quality replies\n");
    wmr_link_health_t h;
    memset(&h, 0, sizeof(h));
    uint16_t handle = 0;
    static const uint8_t rssi_ok[]  = {0x00, 0x2A, 0x00, 0xF6};              /* -10 */
    static const uint8_t fcc_ok[]   = {0x00, 0x2A, 0x00, 0x34, 0x12};        /* 0x1234 */
    static const uint8_t lq_ok[]    = {0x00, 0x2A, 0x00, 0xFF};
    CHECK(wmr_diag_parse_link_reply(WMR_LINKCMD_RSSI, rssi_ok, sizeof(rssi_ok), &handle, &h) && handle == 0x2A);
    CHECK(h.valid[WMR_LINKCMD_RSSI] && h.rssi == -10);
    CHECK(wmr_diag_parse_link_reply(WMR_LINKCMD_FAILED_CONTACT, fcc_ok, sizeof(fcc_ok), &handle, &h));
    CHECK(h.valid[WMR_LINKCMD_FAILED_CONTACT] && h.failed_contacts == 0x1234);
    CHECK(wmr_diag_parse_link_reply(WMR_LINKCMD_QUALITY, lq_ok, sizeof(lq_ok), &handle, &h));
    CHECK(h.valid[WMR_LINKCMD_QUALITY] && h.quality == 255 && h.updates == 3);

    char text[80];
    wmr_link_health_format(&h, text, sizeof(text));
    CHECK(strcmp(text, "rssi=-10 fcc=4660 lq=255") == 0);

    /* The controller refuses a command: recorded and shown as n/a, not as a value. */
    static const uint8_t refused[] = {0x01, 0x2A, 0x00};                    /* unknown command */
    CHECK(wmr_diag_parse_link_reply(WMR_LINKCMD_QUALITY, refused, sizeof(refused), &handle, &h));
    CHECK(!h.valid[WMR_LINKCMD_QUALITY] && h.status[WMR_LINKCMD_QUALITY] == 0x01);
    wmr_link_health_format(&h, text, sizeof(text));
    CHECK(strcmp(text, "rssi=-10 fcc=4660 lq=n/a(0x01)") == 0);

    /* Too short, wrong kind. */
    static const uint8_t shorty[] = {0x00, 0x2A};
    CHECK(!wmr_diag_parse_link_reply(WMR_LINKCMD_RSSI, shorty, sizeof(shorty), &handle, &h));
    static const uint8_t nofcc[] = {0x00, 0x2A, 0x00, 0x01};
    CHECK(!wmr_diag_parse_link_reply(WMR_LINKCMD_FAILED_CONTACT, nofcc, sizeof(nofcc), &handle, &h));
    CHECK(!wmr_diag_parse_link_reply(WMR_LINKCMD_COUNT, rssi_ok, sizeof(rssi_ok), &handle, &h));
    CHECK(!wmr_diag_parse_link_reply(WMR_LINKCMD_RSSI, NULL, 4, &handle, &h));

    memset(&h, 0, sizeof(h));
    wmr_link_health_format(&h, text, sizeof(text));
    CHECK(strcmp(text, "rssi=n/a(0x00) fcc=n/a(0x00) lq=n/a(0x00)") == 0);       /* nothing polled yet */
}

static void test_command_rate(void){
    printf("diag: extra HCI traffic added by the polling\n");
    CHECK(WMR_LINKDIAG_COMMANDS_PER_LINK == 3);
    /* two links, 3 commands each, once per interval: commands per second x 10 */
    unsigned per_10_seconds = (WMR_NUM_CONTROLLERS * WMR_LINKDIAG_COMMANDS_PER_LINK * 10u * 1000u) / WMR_CFG_LINKDIAG_INTERVAL_MS;
    CHECK(per_10_seconds == 12);                   /* 1.2 commands/s (and as many Command Complete events) */
    CHECK(WMR_CFG_LINKDIAG_PACE_MS >= 100);        /* never more than 10 commands/s even while a cycle runs */
}

int main(void){
    test_clean_stream();
    test_warmup();
    test_known_loss_pattern();
    test_isolated_loss();
    test_jitter();
    test_wrap();
    test_discontinuity_and_duplicates();
    test_bounded_window();
    test_frame_ticks();
    test_classify();
    test_mode_tracker();
    test_link_replies();
    test_command_rate();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
