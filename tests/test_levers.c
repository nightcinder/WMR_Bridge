/* Host tests for the ONY-430 LEVERS runtime state (src/wmr_levers.c).
 *
 * Built twice by CI: once with the release default and once with
 * -DWMR_CFG_TEST_LEVERS=1. The release build must return the compile-time values and
 * ignore every toggle; the LEVERS build must cycle and reset to the same defaults. */

#include <stdio.h>
#include "wmr_config.h"
#include "wmr_levers.h"

static int failures;

#define CHECK(cond) do { \
    if (!(cond)){ \
        fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

int main(void){
    wmr_levers_reset();

    CHECK(wmr_levers_stagger() == WMR_CFG_STAGGER_FIRST_HAND);
    CHECK(wmr_levers_interval_min() == WMR_CFG_CONN_INTERVAL_MIN);
    CHECK(wmr_levers_interval_max() == WMR_CFG_CONN_INTERVAL_MAX);
    CHECK(wmr_levers_hold_intervals() == WMR_CFG_BATCH_HOLD_INTERVALS);
    CHECK(wmr_levers_tx() == WMR_LEVERS_TX_DEFAULT);
    CHECK(wmr_levers_tx_name(WMR_LEVERS_TX_DEFAULT)[0] == 'd');
    CHECK(wmr_levers_tx_name(WMR_LEVERS_TX_P6)[0] == 'P');
    CHECK(wmr_levers_tx_name(WMR_LEVERS_TX_P9)[0] == 'P');

#if WMR_CFG_TEST_LEVERS
    CHECK(!wmr_levers_interval_30ms());
    CHECK(wmr_levers_stagger_cycle() == 1u);
    CHECK(wmr_levers_stagger_cycle() == 2u);
    CHECK(wmr_levers_stagger_cycle() == 0u);

    CHECK(wmr_levers_interval_toggle());
    CHECK(wmr_levers_interval_30ms());
    CHECK(wmr_levers_interval_min() == 24u);
    CHECK(wmr_levers_interval_max() == 24u);
    CHECK(wmr_levers_interval_name()[0] == '3');
    CHECK(!wmr_levers_interval_toggle());
    CHECK(wmr_levers_interval_min() == WMR_CFG_CONN_INTERVAL_MIN);

    CHECK(wmr_levers_hold_toggle());
    CHECK(wmr_levers_hold_intervals() == 2u);
    CHECK(!wmr_levers_hold_toggle());
    CHECK(wmr_levers_hold_intervals() == WMR_CFG_BATCH_HOLD_INTERVALS);

    CHECK(wmr_levers_tx_cycle() == WMR_LEVERS_TX_P6);
    CHECK(wmr_levers_tx_cycle() == WMR_LEVERS_TX_P9);
    CHECK(wmr_levers_tx_cycle() == WMR_LEVERS_TX_DEFAULT);

    wmr_levers_reset();
    CHECK(wmr_levers_stagger() == WMR_CFG_STAGGER_FIRST_HAND);
    CHECK(!wmr_levers_interval_30ms());
    CHECK(wmr_levers_hold_intervals() == WMR_CFG_BATCH_HOLD_INTERVALS);
    CHECK(wmr_levers_tx() == WMR_LEVERS_TX_DEFAULT);
#else
    /* The release image must not move any lever, whatever a caller asks for. */
    CHECK(wmr_levers_stagger_cycle() == WMR_CFG_STAGGER_FIRST_HAND);
    CHECK(!wmr_levers_interval_toggle());
    CHECK(wmr_levers_interval_min() == WMR_CFG_CONN_INTERVAL_MIN);
    CHECK(wmr_levers_interval_max() == WMR_CFG_CONN_INTERVAL_MAX);
    CHECK(wmr_levers_hold_toggle() == (WMR_CFG_BATCH_HOLD_INTERVALS == 2));
    CHECK(wmr_levers_hold_intervals() == WMR_CFG_BATCH_HOLD_INTERVALS);
    CHECK(wmr_levers_tx_cycle() == WMR_LEVERS_TX_DEFAULT);
#endif

    if (failures){
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("test_levers: OK (%s)\n",
           WMR_CFG_TEST_LEVERS ? "LEVERS image" : "release defaults");
    return 0;
}
