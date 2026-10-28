/*
 * Host tests for the 0.3 phone-link security policy in src/wmr_secure.c: which SM
 * results lead to serving or disconnecting a central, and whether a second central
 * may connect. These are the decisions the transport makes; the BTstack callbacks,
 * the GATT permissions and the radio behavior are not covered here.
 */

#include <stdio.h>
#include "wmr_secure.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static wmr_secure_event_t event(wmr_secure_event_type_t type, uint16_t handle,
                                bool success, bool bonded){
    wmr_secure_event_t e = {
        .type = type,
        .handle = handle,
        .phone_handle = handle,
        .success = success,
        .bonded = bonded,
        .encrypted = success,
    };
    return e;
}

static void test_pairing(void){
    printf("pairing result decides accept/refuse\n");
    wmr_secure_event_t e = event(WMR_SECURE_PAIRING_COMPLETE, 0x0040, true, true);
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_ACCEPT);

    /* Success without a stored bond is not enough: a central can ask for
     * encrypt-only pairing and still get an encrypted link. */
    e = event(WMR_SECURE_PAIRING_COMPLETE, 0x0040, true, false);
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);

    /* Failed pairing, including a legacy (non-LESC) attempt rejected by SC-only mode. */
    e = event(WMR_SECURE_PAIRING_COMPLETE, 0x0040, false, false);
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);

    /* A bond can exist from an earlier session even after this pairing failed;
     * a failed attempt still means the link is not secure now. */
    e = event(WMR_SECURE_PAIRING_COMPLETE, 0x0040, false, true);
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);
}

static void test_reencryption(void){
    printf("re-encryption with stored keys decides accept/refuse\n");
    wmr_secure_event_t e = event(WMR_SECURE_REENCRYPTION_COMPLETE, 0x0040, true, true);
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_ACCEPT);

    e = event(WMR_SECURE_REENCRYPTION_COMPLETE, 0x0040, false, false);
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);

    /* Stale key on one side: re-encryption fails, the phone has to pair again. */
    e = event(WMR_SECURE_REENCRYPTION_COMPLETE, 0x0040, false, true);
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);

    e = event(WMR_SECURE_REENCRYPTION_COMPLETE, 0x0040, true, false);
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);
}

static void test_timeout(void){
    printf("pairing timeout refuses a central that never became secure\n");
    wmr_secure_event_t e = event(WMR_SECURE_PAIR_TIMEOUT, 0x0040, false, false);
    e.encrypted = false;
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);

    /* Bonded and already encrypted but the SM event was missed: serve it. */
    e = event(WMR_SECURE_PAIR_TIMEOUT, 0x0040, false, true);
    e.encrypted = true;
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_ACCEPT);

    /* A bond from an earlier session is not enough: the timer now reports the
     * link's real state, which is unencrypted until the phone re-encrypts. */
    e = event(WMR_SECURE_PAIR_TIMEOUT, 0x0040, false, true);
    e.encrypted = false;
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);

    /* Encrypted without a bond: refuse. Encryption alone is not the policy. */
    e = event(WMR_SECURE_PAIR_TIMEOUT, 0x0040, false, false);
    e.encrypted = true;
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_REFUSE);
}

static void test_other_handle(void){
    printf("events about another connection are ignored\n");
    wmr_secure_event_t e = event(WMR_SECURE_PAIRING_COMPLETE, 0x0041, false, false);
    e.phone_handle = 0x0040;
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_IGNORE);

    e.phone_handle = WMR_SECURE_NO_HANDLE;
    e.handle = WMR_SECURE_NO_HANDLE;
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_IGNORE);

    CHECK(wmr_secure_decide(NULL) == WMR_SECURE_IGNORE);

    e.type = (wmr_secure_event_type_t) 99;
    e.phone_handle = 0x0040;
    e.handle = 0x0040;
    CHECK(wmr_secure_decide(&e) == WMR_SECURE_IGNORE);
}

static void test_single_central(void){
    printf("only one phone at a time\n");
    CHECK(wmr_secure_may_connect(WMR_SECURE_NO_HANDLE, 0x0040));
    CHECK(wmr_secure_may_connect(0x0040, 0x0040));   /* same connection, not a second one */
    CHECK(!wmr_secure_may_connect(0x0040, 0x0041));
    CHECK(!wmr_secure_may_connect(0x0040, WMR_SECURE_NO_HANDLE));
}

int main(void){
    test_pairing();
    test_reencryption();
    test_timeout();
    test_other_handle();
    test_single_central();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
