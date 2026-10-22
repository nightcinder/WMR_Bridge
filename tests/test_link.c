/*
 * Host tests for src/wmr_link.c (connection-parameter helpers) and the values
 * chosen in wmr_config.h. They do not test that a phone grants the request.
 */

#include <stdio.h>
#include <string.h>
#include "wmr_config.h"
#include "wmr_link.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

static void test_units(void){
    printf("units\n");
    CHECK(wmr_conn_interval_us(6) == 7500);          /* 7.5 ms, the Bluetooth minimum */
    CHECK(wmr_conn_interval_us(12) == 15000);
    CHECK(wmr_conn_interval_us(9) == 11250);
    CHECK(wmr_conn_interval_us(3200) == 4000000);
    CHECK(wmr_conn_timeout_ms(400) == 4000);
    CHECK(wmr_conn_timeout_ms(10) == 100);
}

static void test_request_valid(void){
    printf("request validation\n");
    wmr_conn_request_t configured = { WMR_CFG_CONN_INTERVAL_MIN, WMR_CFG_CONN_INTERVAL_MAX,
                                      WMR_CFG_CONN_LATENCY, WMR_CFG_CONN_TIMEOUT };
    CHECK(wmr_conn_request_valid(&configured));       /* what the firmware really asks for */
    CHECK(configured.interval_min == 6 && configured.interval_max == 12);   /* 7.5 to 15 ms */
    CHECK(configured.latency == 0 && wmr_conn_timeout_ms(configured.timeout) == 4000);

    wmr_conn_request_t r = configured;
    r.interval_min = 5;  CHECK(!wmr_conn_request_valid(&r));   /* below 7.5 ms */
    r = configured; r.interval_min = 13; CHECK(!wmr_conn_request_valid(&r));   /* min > max */
    r = configured; r.interval_max = 3201; CHECK(!wmr_conn_request_valid(&r));
    r = configured; r.latency = 500; CHECK(!wmr_conn_request_valid(&r));
    r = configured; r.timeout = 9; CHECK(!wmr_conn_request_valid(&r));
    r = configured; r.timeout = 3201; CHECK(!wmr_conn_request_valid(&r));
    /* timeout must exceed 2 x (1 + latency) x max interval: 15 ms x 2 = 30 ms here */
    r = configured; r.timeout = 10; CHECK(wmr_conn_request_valid(&r));        /* 100 ms > 30 ms */
    r = configured; r.interval_min = 6; r.interval_max = 3200; r.timeout = 10; CHECK(!wmr_conn_request_valid(&r));
    r = configured; r.latency = 4; r.interval_max = 12; r.timeout = 10;        /* 100 ms > 5*15*2 = 150 ms? no */
    CHECK(!wmr_conn_request_valid(&r));
    CHECK(!wmr_conn_request_valid(NULL));
}

static void test_satisfy(void){
    printf("granted vs requested\n");
    wmr_conn_request_t want = { 6, 12, 0, 400 };
    wmr_conn_params_t g;
    g.interval = 6;  g.latency = 0; g.timeout = 400; CHECK(wmr_conn_params_satisfy(&g, &want));
    g.interval = 12; CHECK(wmr_conn_params_satisfy(&g, &want));                 /* at the maximum */
    g.interval = 13; CHECK(!wmr_conn_params_satisfy(&g, &want));                /* 16.25 ms */
    g.interval = 36; CHECK(!wmr_conn_params_satisfy(&g, &want));                /* 45 ms: a typical default */
    g.interval = 12; g.latency = 1; CHECK(!wmr_conn_params_satisfy(&g, &want));
    CHECK(!wmr_conn_params_satisfy(NULL, &want));
}

static void test_format(void){
    printf("formatting\n");
    char buf[48];
    wmr_conn_params_t p = { 9, 0, 400 };
    wmr_conn_format(&p, buf, sizeof(buf));
    CHECK(strcmp(buf, "11.25ms lat=0 to=4000ms") == 0);
    p.interval = 6; wmr_conn_format(&p, buf, sizeof(buf));
    CHECK(strcmp(buf, "7.50ms lat=0 to=4000ms") == 0);
    p.interval = 36; p.latency = 2; p.timeout = 600; wmr_conn_format(&p, buf, sizeof(buf));
    CHECK(strcmp(buf, "45.00ms lat=2 to=6000ms") == 0);
    char small[8];
    wmr_conn_format(&p, small, sizeof(small));
    CHECK(strlen(small) == 7);                         /* truncated, still terminated */
}

int main(void){
    test_units();
    test_request_valid();
    test_satisfy();
    test_format();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
