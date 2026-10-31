/*
 * Host test for the WMR_CFG_FF13_READ=0 build of src/wmr_cfgmgr.c: a controller
 * descriptor must not start a config read and no HID report may be sent.
 * Built with -DWMR_CFG_FF13_READ=0; the default build is tests/test_cfgmgr.c.
 */

#include <stdio.h>
#include <string.h>
#include "btstack_config.h"
#include "btstack.h"
#include "wmr_cfgmgr.h"
#include "host_stubs.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static void listener(unsigned hand){
    (void) hand;
}

int main(void){
    printf("cfgmgr: FF13 read disabled\n");
    CHECK(WMR_CFG_FF13_READ == 0);
    host_reset();
    wmr_cfgmgr_init(listener);
    host.status[WMR_HAND_LEFT].connected = true;
    host.cid[WMR_HAND_LEFT] = 1;

    wmr_cfgmgr_on_descriptor(WMR_HAND_LEFT);
    host_advance_ms(5000);
    CHECK(host_hid.calls == 0);

    const uint8_t *blob = (const uint8_t *) 1;
    uint32_t total = 5;
    CHECK(wmr_cfgmgr_get(WMR_HAND_LEFT, &blob, &total) == WMR_CFG_NOT_READ);
    CHECK(total == 0);

    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
