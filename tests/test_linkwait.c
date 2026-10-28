/* Host tests for the ONY-446 controller-reconnect handoff watchdog (src/wmr_linkwait.c).
 *
 * The case that matters: a controller-initiated ACL that does not encrypt on its own.
 * The host must ask for encryption once, then drop the link if no HID channel follows,
 * so the controller can page again without a reboot. */

#include <stdio.h>
#include "wmr_linkwait.h"

static int failures;

#define CHECK(cond) do { \
    if (!(cond)){ \
        fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

static void test_encrypt_once_then_drop(void){
    printf("unencrypted ACL: encrypt once, then drop\n");
    wmr_linkwait_t wait;
    wmr_linkwait_start(&wait, 1000);

    CHECK(wmr_linkwait_poll(&wait, false, 1000) == WMR_LINKWAIT_NONE);
    CHECK(wmr_linkwait_poll(&wait, false, 1000 + WMR_LINKWAIT_ENCRYPT_MS - 1) ==
          WMR_LINKWAIT_NONE);
    CHECK(wmr_linkwait_poll(&wait, false, 1000 + WMR_LINKWAIT_ENCRYPT_MS) ==
          WMR_LINKWAIT_ENCRYPT);
    /* Only one request per wait: a second one would restart an LMP procedure. */
    CHECK(wmr_linkwait_poll(&wait, false, 1000 + WMR_LINKWAIT_ENCRYPT_MS + 1) ==
          WMR_LINKWAIT_NONE);
    CHECK(wmr_linkwait_poll(&wait, false, 1000 + WMR_LINKWAIT_DROP_MS - 1) ==
          WMR_LINKWAIT_NONE);
    CHECK(wmr_linkwait_poll(&wait, false, 1000 + WMR_LINKWAIT_DROP_MS) ==
          WMR_LINKWAIT_DROP);
    /* Dropping disarms the wait; the reconnect starts a new one. */
    CHECK(!wait.armed);
    CHECK(wmr_linkwait_poll(&wait, false, 1000 + WMR_LINKWAIT_DROP_MS + 1000) ==
          WMR_LINKWAIT_NONE);
}

static void test_encrypted_but_no_hid(void){
    printf("encrypted ACL with no HID channel: no encrypt request, still drops\n");
    wmr_linkwait_t wait;
    wmr_linkwait_start(&wait, 0);

    CHECK(wmr_linkwait_poll(&wait, true, WMR_LINKWAIT_ENCRYPT_MS) == WMR_LINKWAIT_NONE);
    CHECK(wmr_linkwait_poll(&wait, true, WMR_LINKWAIT_DROP_MS - 1) == WMR_LINKWAIT_NONE);
    CHECK(wmr_linkwait_poll(&wait, true, WMR_LINKWAIT_DROP_MS) == WMR_LINKWAIT_DROP);
}

static void test_finish_and_restart(void){
    printf("finish on HID open; a later link starts clean\n");
    wmr_linkwait_t wait;
    wmr_linkwait_start(&wait, 500);
    CHECK(wmr_linkwait_poll(&wait, false, 500 + WMR_LINKWAIT_ENCRYPT_MS) ==
          WMR_LINKWAIT_ENCRYPT);

    wmr_linkwait_finish(&wait);
    CHECK(!wait.armed);
    CHECK(wmr_linkwait_poll(&wait, false, 500 + WMR_LINKWAIT_DROP_MS) ==
          WMR_LINKWAIT_NONE);

    wmr_linkwait_start(&wait, 2000);
    CHECK(wmr_linkwait_poll(&wait, false, 2000 + WMR_LINKWAIT_ENCRYPT_MS) ==
          WMR_LINKWAIT_ENCRYPT);
}

static void test_clock_wrap(void){
    printf("elapsed time across the 32-bit clock wrap\n");
    wmr_linkwait_t wait;
    wmr_linkwait_start(&wait, 0xFFFFFF00u);

    CHECK(wmr_linkwait_poll(&wait, false, 0x00000000u) == WMR_LINKWAIT_NONE);   /* 256 ms */
    CHECK(wmr_linkwait_poll(&wait, false, 0x00000100u) == WMR_LINKWAIT_NONE);   /* 512 ms */
    CHECK(wmr_linkwait_poll(&wait, false, 0x00000200u) == WMR_LINKWAIT_ENCRYPT); /* 768 ms */
    CHECK(wmr_linkwait_poll(&wait, false, 0x00000300u) == WMR_LINKWAIT_NONE);
}

int main(void){
    test_encrypt_once_then_drop();
    test_encrypted_but_no_hid();
    test_finish_and_restart();
    test_clock_wrap();

    if (failures){
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("test_linkwait: OK\n");
    return 0;
}
