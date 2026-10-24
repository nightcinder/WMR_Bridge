/*
 * Host tests for FF14 (batched decoded stream) pieces that need no BTstack: the batch
 * format (golden bytes), the minimum MTU and records-per-notification rule, the flush
 * rule, the three-way CCC exclusion, and the queue behaviour the pump relies on.
 *
 * What this does NOT test: the BTstack callbacks, the GATT database, the pump itself
 * (att_server_notify, CAN_SEND_NOW) or anything on a radio. The "send error keeps the
 * records" and "hold timer" paths in wmr_transport.c are therefore unexercised here.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "wmr_batch.h"
#include "wmr_decoded.h"
#include "wmr_queue.h"
#include "wmr_stream_mode.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

/* A stand-in 56-byte record: every byte is `fill`, except [0] = version 1 and [1] = hand. */
static void fake_record(uint8_t rec[WMR_DECODED_SIZE], uint8_t hand, uint8_t fill){
    memset(rec, fill, WMR_DECODED_SIZE);
    rec[0] = WMR_DECODED_VERSION;
    rec[1] = hand;
}

static void test_sizes(void){
    printf("FF14 sizes and MTU rule\n");
    CHECK(WMR_BATCH_VERSION == 2 && WMR_BATCH_HEADER_SIZE == 2);
    CHECK(WMR_BATCH_MIN_MTU == 61);
    CHECK(!wmr_batch_mtu_ok(0) && !wmr_batch_mtu_ok(23) && !wmr_batch_mtu_ok(59) && !wmr_batch_mtu_ok(60));
    CHECK(wmr_batch_mtu_ok(61) && wmr_batch_mtu_ok(247) && wmr_batch_mtu_ok(517));
    /* FF12 needs 59, FF14 61: they differ at 59 and 60. */
    CHECK(wmr_decoded_mtu_ok(59) && !wmr_batch_mtu_ok(59));
    CHECK(wmr_batch_max_count(0) == 0 && wmr_batch_max_count(23) == 0 && wmr_batch_max_count(60) == 0);
    CHECK(wmr_batch_max_count(61) == 1);
    CHECK(wmr_batch_max_count(116) == 1);       /* 3 + 2 + 111 */
    CHECK(wmr_batch_max_count(117) == 2);       /* 3 + 2 + 112 */
    CHECK(wmr_batch_max_count(247) == 4);       /* (247 - 5) / 56 = 4 */
    CHECK(wmr_batch_max_count(285) == 5);
    CHECK(wmr_batch_max_count(517) == 9);       /* (517 - 5) / 56 = 9 */
    CHECK(wmr_batch_max_count(65535) == WMR_BATCH_MAX_COUNT);   /* capped */
    CHECK(WMR_BATCH_BUFFER_SIZE == 506);
    /* The largest batch always fits in MTU - 3. */
    for (unsigned mtu = 61; mtu <= 517; mtu++){
        unsigned n = wmr_batch_max_count((uint16_t) mtu);
        CHECK(n >= 1 && 2 + n * 56 <= mtu - 3);
    }
}

static void test_golden(void){
    printf("FF14 golden bytes\n");
    uint8_t recs[WMR_BATCH_MAX_COUNT][WMR_DECODED_SIZE];
    for (unsigned i = 0; i < WMR_BATCH_MAX_COUNT; i++) fake_record(recs[i], (uint8_t) (i & 1), (uint8_t) (0x10 + i));

    /* batch of 1 */
    wmr_batch_t b;
    wmr_batch_init(&b, 9);
    CHECK(b.count == 0);
    CHECK(wmr_batch_add(&b, recs[0]));
    CHECK(wmr_batch_size(&b) == 58);
    CHECK(b.data[0] == 0x02 && b.data[1] == 0x01);
    CHECK(b.data[2] == 0x01 && b.data[3] == 0x00 && b.data[4] == 0x10 && b.data[57] == 0x10);   /* record 0 unchanged */
    CHECK(memcmp(&b.data[2], recs[0], WMR_DECODED_SIZE) == 0);

    /* batch of 2: both hands, oldest first */
    CHECK(wmr_batch_add(&b, recs[1]));
    CHECK(wmr_batch_size(&b) == 114);
    CHECK(b.data[0] == 0x02 && b.data[1] == 0x02);
    CHECK(b.data[58] == 0x01 && b.data[59] == 0x01 && b.data[60] == 0x11 && b.data[113] == 0x11);
    CHECK(memcmp(&b.data[2], recs[0], WMR_DECODED_SIZE) == 0);
    CHECK(memcmp(&b.data[58], recs[1], WMR_DECODED_SIZE) == 0);

    /* maximum (9, MTU 517) */
    wmr_batch_init(&b, wmr_batch_max_count(517));
    for (unsigned i = 0; i < 9; i++) CHECK(wmr_batch_add(&b, recs[i]));
    CHECK(!wmr_batch_add(&b, recs[0]));                 /* a tenth does not fit */
    CHECK(b.count == 9);
    CHECK(wmr_batch_size(&b) == 506);
    CHECK(b.data[0] == 0x02 && b.data[1] == 0x09);
    for (unsigned i = 0; i < 9; i++)
        CHECK(memcmp(&b.data[2 + i * 56], recs[i], WMR_DECODED_SIZE) == 0);
    CHECK(b.data[505] == 0x18);                         /* last byte of the ninth record */

    /* A smaller MTU limits the capacity, not the buffer. */
    wmr_batch_init(&b, wmr_batch_max_count(247));
    for (unsigned i = 0; i < 4; i++) CHECK(wmr_batch_add(&b, recs[i]));
    CHECK(!wmr_batch_add(&b, recs[4]));
    CHECK(wmr_batch_size(&b) == 226 && 226 <= 247 - 3);

    /* Receiver side round trip, and what it rejects. */
    unsigned count = 0;
    const uint8_t *records = NULL;
    CHECK(wmr_batch_parse(b.data, wmr_batch_size(&b), &count, &records));
    CHECK(count == 4 && records == &b.data[2]);
    wmr_decoded_t d;
    CHECK(wmr_decoded_unpack(records + 56, WMR_DECODED_SIZE, &d) && d.hand == 1);
    CHECK(!wmr_batch_parse(b.data, 0, &count, &records));
    CHECK(!wmr_batch_parse(b.data, 57, &count, &records));
    CHECK(!wmr_batch_parse(b.data, (uint16_t) (wmr_batch_size(&b) - 1), &count, &records));
    uint8_t bad[WMR_BATCH_BUFFER_SIZE];
    memcpy(bad, b.data, sizeof(bad));
    bad[0] = 1; CHECK(!wmr_batch_parse(bad, wmr_batch_size(&b), &count, &records));    /* a v1 record, not a batch */
    memcpy(bad, b.data, sizeof(bad));
    bad[1] = 0; CHECK(!wmr_batch_parse(bad, wmr_batch_size(&b), &count, &records));
    bad[1] = 5; CHECK(!wmr_batch_parse(bad, wmr_batch_size(&b), &count, &records));    /* count disagrees with size */
    /* A self-consistent batch with more than the format maximum is still invalid. */
    uint8_t over[WMR_BATCH_HEADER_SIZE + (WMR_BATCH_MAX_COUNT + 1) * WMR_DECODED_SIZE];
    memset(over, 0, sizeof(over));
    over[0] = WMR_BATCH_VERSION;
    over[1] = WMR_BATCH_MAX_COUNT + 1;
    CHECK(!wmr_batch_parse(over, sizeof(over), &count, &records));
    /* An FF12 v1 record is not a valid batch and a batch is not a valid v1 record. */
    CHECK(!wmr_batch_parse(recs[0], WMR_DECODED_SIZE, &count, &records));
    CHECK(!wmr_decoded_unpack(b.data, wmr_batch_size(&b), &d));
}

static void test_real_record(void){
    printf("FF14 carries real FF12 records unchanged\n");
    wmr_frame_t f;
    memset(&f, 0, sizeof(f));
    f.hand = 1; f.sequence = 77; f.rx_time_us = 123456; f.length = 46;
    f.data[0] = 0xA1; f.data[1] = 0x01; f.data[2] = 0x0C;
    f.data[2 + 28] = 0x10; f.data[2 + 29] = 0x27;
    uint8_t rec[WMR_DECODED_SIZE];
    CHECK(wmr_decoded_from_frame(&f, rec));
    wmr_batch_t b;
    wmr_batch_init(&b, 9);
    CHECK(wmr_batch_add(&b, rec));
    unsigned count;
    const uint8_t *records;
    CHECK(wmr_batch_parse(b.data, wmr_batch_size(&b), &count, &records));
    wmr_decoded_t d;
    CHECK(count == 1 && wmr_decoded_unpack(records, WMR_DECODED_SIZE, &d));
    CHECK(d.hand == 1 && d.sequence == 77 && d.rx_time_us == 123456 && d.device_ticks == 10000);
}

static void test_flush_rule(void){
    printf("FF14 flush rule\n");
    const uint32_t hold = 15000;
    CHECK(wmr_batch_hold_us(15000) == 15000);
    CHECK(wmr_batch_hold_us(7500) == 7500);
    CHECK(wmr_batch_hold_us(0) == WMR_CFG_BATCH_HOLD_DEFAULT_US);
    /* nothing queued: never */
    CHECK(wmr_batch_flush_reason(0, 9, 999999, hold) == WMR_BATCH_FLUSH_NO);
    /* partial and young: keep collecting */
    CHECK(wmr_batch_flush_reason(1, 9, 0, hold) == WMR_BATCH_FLUSH_NO);
    CHECK(wmr_batch_flush_reason(8, 9, 14999, hold) == WMR_BATCH_FLUSH_NO);
    /* partial flush on timeout */
    CHECK(wmr_batch_flush_reason(1, 9, 15000, hold) == WMR_BATCH_FLUSH_TIMEOUT);
    CHECK(wmr_batch_flush_reason(3, 9, 20000, hold) == WMR_BATCH_FLUSH_TIMEOUT);
    /* full batch flushes at once, however young; so does a backlog */
    CHECK(wmr_batch_flush_reason(9, 9, 0, hold) == WMR_BATCH_FLUSH_FULL);
    CHECK(wmr_batch_flush_reason(200, 9, 0, hold) == WMR_BATCH_FLUSH_FULL);
    CHECK(wmr_batch_flush_reason(2, 2, 0, hold) == WMR_BATCH_FLUSH_FULL);
    /* MTU too small (max 0): flush so the pump can discard */
    CHECK(wmr_batch_flush_reason(1, 0, 0, hold) == WMR_BATCH_FLUSH_FULL);
    /* timer */
    CHECK(wmr_batch_timer_ms(0, hold) == 15);
    CHECK(wmr_batch_timer_ms(5000, hold) == 10);
    CHECK(wmr_batch_timer_ms(14001, hold) == 1);
    CHECK(wmr_batch_timer_ms(14500, hold) == 1);        /* rounds up */
    CHECK(wmr_batch_timer_ms(15000, hold) == 1);        /* already due: at least 1 ms */
    CHECK(wmr_batch_timer_ms(99999, hold) == 1);
    CHECK(wmr_batch_timer_ms(0, UINT32_MAX) == 4294968u);   /* no 32-bit wrap before rounding */

    /* Simulation: records arrive every 5 ms (one hand, 200/s); the pump checks every 1 ms. The
     * oldest record must never wait more than the hold time plus one report period (5 ms), and
     * the batch must hold more than one record. */
    uint32_t oldest_arrival = 0, queued = 0, max_age = 0, sent = 0, batches = 0;
    for (uint32_t t = 0; t < 2000000u; t += 1000u){
        if (t % 5000u == 0){ if (!queued) oldest_arrival = t; queued++; }
        wmr_batch_flush_t r = wmr_batch_flush_reason(queued, 9, queued ? t - oldest_arrival : 0, hold);
        if (r != WMR_BATCH_FLUSH_NO){
            uint32_t age = t - oldest_arrival;
            if (age > max_age) max_age = age;
            sent += queued; batches++; queued = 0;
        }
    }
    CHECK(max_age <= hold + 5000u);
    CHECK(max_age == hold);                             /* with a 1 ms check it is exactly the hold time */
    CHECK(batches > 0 && sent / batches >= 2);
    CHECK(sent + queued == 400);                        /* nothing dropped by batching */
}

static void test_exclusion(void){
    printf("FF14 CCC exclusion, all orderings\n");
    const wmr_stream_t all[3] = { WMR_STREAM_RAW, WMR_STREAM_DECODED, WMR_STREAM_BATCHED };
    CHECK(strcmp(wmr_stream_mode_name(WMR_STREAM_BATCHED), "batched") == 0);
    /* All six orderings (first, second): the second enable is refused with 0x80 and the first stays. */
    unsigned orderings = 0;
    for (unsigned a = 0; a < 3; a++){
        for (unsigned b = 0; b < 3; b++){
            if (a == b) continue;
            orderings++;
            wmr_stream_ccc_result_t r = wmr_stream_mode_ccc_write(WMR_STREAM_NONE, all[a], true);
            CHECK(r.accepted && r.mode == all[a]);
            wmr_stream_ccc_result_t second = wmr_stream_mode_ccc_write(r.mode, all[b], true);
            CHECK(!second.accepted && second.att_error == 0x80 && second.mode == all[a]);
            CHECK(wmr_stream_mode_ccc_enabled(second.mode, all[a]));
            CHECK(!wmr_stream_mode_ccc_enabled(second.mode, all[b]));
            /* write 0x0000 to the active one first, then the other is accepted */
            r = wmr_stream_mode_ccc_write(second.mode, all[a], false);
            CHECK(r.accepted && r.mode == WMR_STREAM_NONE);
            r = wmr_stream_mode_ccc_write(r.mode, all[b], true);
            CHECK(r.accepted && r.mode == all[b]);
            /* Disabling a stream that is not active changes nothing. */
            unsigned c = 3 - a - b;
            wmr_stream_ccc_result_t off = wmr_stream_mode_ccc_write(r.mode, all[c], false);
            CHECK(off.accepted && off.mode == all[b]);
        }
    }
    CHECK(orderings == 6);
    /* Re-enabling the active stream is accepted. */
    CHECK(wmr_stream_mode_ccc_write(WMR_STREAM_BATCHED, WMR_STREAM_BATCHED, true).accepted);
    /* Disabling FF14 leaves NONE. */
    CHECK(wmr_stream_mode_ccc_write(WMR_STREAM_BATCHED, WMR_STREAM_BATCHED, false).mode == WMR_STREAM_NONE);
    /* NONE is not a stream. */
    CHECK(!wmr_stream_mode_ccc_write(WMR_STREAM_NONE, WMR_STREAM_NONE, true).accepted);
}

static void make_frame(wmr_frame_t *f, uint8_t hand, uint32_t seq){
    memset(f, 0, sizeof(*f));
    f->hand = hand; f->sequence = seq; f->rx_time_us = 1000u * seq; f->length = 46;
    f->data[0] = 0xA1; f->data[1] = 0x01;
}

static void test_queue_behaviour(void){
    printf("FF14 queue behaviour\n");
    static wmr_queue_t q;
    wmr_queue_init(&q);
    wmr_frame_t f;
    /* peek_at walks the queue oldest first, across the ring wrap, both hands mixed */
    for (unsigned i = 0; i < WMR_CFG_QUEUE_CAPACITY; i++){ make_frame(&f, (uint8_t) (i & 1), i + 1); CHECK(wmr_queue_push(&q, &f)); }
    for (unsigned i = 0; i < 10; i++) wmr_queue_pop(&q);
    for (unsigned i = 0; i < 10; i++){ make_frame(&f, (uint8_t) (i & 1), 1000 + i); CHECK(wmr_queue_push(&q, &f)); }
    CHECK(q.count == WMR_CFG_QUEUE_CAPACITY);
    CHECK(wmr_queue_peek_at(&q, 0)->sequence == 11);
    CHECK(wmr_queue_peek_at(&q, 1)->sequence == 12);
    CHECK(wmr_queue_peek_at(&q, WMR_CFG_QUEUE_CAPACITY - 11)->sequence == WMR_CFG_QUEUE_CAPACITY);
    CHECK(wmr_queue_peek_at(&q, WMR_CFG_QUEUE_CAPACITY - 10)->sequence == 1000);   /* wrapped */
    CHECK(wmr_queue_peek_at(&q, WMR_CFG_QUEUE_CAPACITY - 1)->sequence == 1009);
    CHECK(wmr_queue_peek_at(&q, WMR_CFG_QUEUE_CAPACITY) == NULL);

    /* Queue full: the NEWEST report is refused (counted as drop by the transport) and nothing
     * already queued is lost or reordered, with batching exactly as with FF12. */
    make_frame(&f, 0, 5000);
    CHECK(!wmr_queue_push(&q, &f));
    CHECK(q.count == WMR_CFG_QUEUE_CAPACITY && wmr_queue_peek_at(&q, 0)->sequence == 11);

    /* Drain in maximum-size batches (9): every queued record is delivered once, in order. */
    unsigned delivered = 0, batches = 0;
    uint32_t last_seq[2] = {0, 0};
    unsigned total = q.count;
    while (q.count){
        unsigned take = q.count < 9 ? q.count : 9;
        wmr_batch_t b;
        wmr_batch_init(&b, 9);
        for (unsigned i = 0; i < take; i++){
            const wmr_frame_t *fr = wmr_queue_peek_at(&q, i);
            uint8_t rec[WMR_DECODED_SIZE];
            CHECK(wmr_decoded_from_frame(fr, rec) && wmr_batch_add(&b, rec));
        }
        for (unsigned i = 0; i < take; i++){
            const wmr_frame_t *fr = wmr_queue_peek(&q);
            CHECK(fr->sequence > last_seq[fr->hand] || last_seq[fr->hand] == 0 || fr->sequence >= 1000);
            last_seq[fr->hand] = fr->sequence;
            wmr_queue_pop(&q);
            delivered++;
        }
        batches++;
    }
    CHECK(delivered == total);
    CHECK(batches == (total + 8) / 9);

    /* A failed notify must lose nothing: the batch is built without popping. */
    for (unsigned i = 0; i < 12; i++){ make_frame(&f, (uint8_t) (i & 1), 1 + i); wmr_queue_push(&q, &f); }
    wmr_batch_t b;
    wmr_batch_init(&b, 9);
    for (unsigned i = 0; i < 9; i++){ uint8_t rec[WMR_DECODED_SIZE]; wmr_decoded_from_frame(wmr_queue_peek_at(&q, i), rec); wmr_batch_add(&b, rec); }
    CHECK(q.count == 12 && wmr_queue_peek(&q)->sequence == 1);
}

int main(void){
    test_sizes();
    test_golden();
    test_real_record();
    test_flush_rule();
    test_exclusion();
    test_queue_behaviour();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
