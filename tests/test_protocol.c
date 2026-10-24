/*
 * Host unit tests for wmr_protocol (encoder + reassembler) and wmr_queue.
 * No BTstack or ESP-IDF needed. Build and run: tests/run_tests.ps1.
 *
 * Part of wmr_bridge, derived from BlueKitchen BTstack; the license in
 * ../license_header.txt applies.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "wmr_protocol.h"
#include "wmr_queue.h"
#include "wmr_decoded.h"
#include "wmr_psvr2.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

/* Representative frame: A1, report ID 1, then a position-dependent payload. */
static void make_frame(wmr_frame_t *f, uint8_t hand, uint32_t sequence, uint16_t length){
    memset(f, 0, sizeof(*f));
    f->hand = hand;
    f->sequence = sequence;
    f->length = length;
    for (unsigned i = 0; i < length; i++){
        f->data[i] = (uint8_t) (i == 0 ? 0xA1 : i == 1 ? 0x01 : i * 7 + hand);
    }
}

static int frames_equal(const wmr_frame_t *a, const wmr_frame_t *b){
    return a->hand == b->hand && a->sequence == b->sequence && a->length == b->length &&
           memcmp(a->data, b->data, a->length) == 0;
}

/* Encode `f` at `capacity` and push every fragment; returns the result of
 * the last push and the number of fragments through *count. */
static wmr_proto_result_t send_frame(wmr_reassembler_t *r, const wmr_frame_t *f,
                                     uint16_t capacity, wmr_frame_t *out, unsigned *count){
    uint8_t packet[WMR_CFG_MAX_NOTIFICATION];
    uint16_t offset = 0, chunk = 0;
    wmr_proto_result_t result = WMR_PROTO_PARTIAL;
    *count = 0;
    while (offset < f->length){
        uint16_t size = wmr_proto_encode_fragment(f, offset, capacity, packet, &chunk);
        CHECK(size == WMR_PROTO_HEADER_SIZE + chunk);
        result = wmr_reassembler_push(r, packet, size, out);
        offset = (uint16_t) (offset + chunk);
        (*count)++;
    }
    return result;
}

static void test_capacity(void){
    printf("capacity\n");
    CHECK(wmr_proto_notification_capacity(23) == 20);      /* default ATT MTU */
    CHECK(wmr_proto_notification_capacity(15) == 0);       /* header + overhead only */
    CHECK(wmr_proto_notification_capacity(16) == 13);      /* one payload byte */
    CHECK(wmr_proto_notification_capacity(247) == 244);
    CHECK(wmr_proto_notification_capacity(517) == 244);    /* clamped */
    CHECK(wmr_proto_notification_capacity(0) == 0);
}

static void test_golden_packet(void){
    printf("golden packet\n");
    wmr_frame_t f;
    make_frame(&f, WMR_HAND_RIGHT, 0x01020304, 46);
    uint8_t out[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk = 0;
    /* capacity 44 -> 32 payload bytes in the first fragment */
    uint16_t size = wmr_proto_encode_fragment(&f, 0, 44, out, &chunk);
    static const uint8_t header[12] = {0x52, 0x01, 0x04, 0x03, 0x02, 0x01,
                                       0x2E, 0x00, 0x00, 0x00, 0x20, 0x00};
    CHECK(size == 44 && chunk == 32);
    CHECK(memcmp(out, header, sizeof(header)) == 0);
    CHECK(memcmp(out + 12, f.data, 32) == 0);
    /* second fragment: offset 32, 14 bytes left */
    size = wmr_proto_encode_fragment(&f, 32, 44, out, &chunk);
    static const uint8_t header2[12] = {0x52, 0x01, 0x04, 0x03, 0x02, 0x01,
                                        0x2E, 0x00, 0x20, 0x00, 0x0E, 0x00};
    CHECK(size == 26 && chunk == 14);
    CHECK(memcmp(out, header2, sizeof(header2)) == 0);
    CHECK(memcmp(out + 12, f.data + 32, 14) == 0);
}

static void test_encoder_arguments(void){
    printf("encoder argument checks\n");
    wmr_frame_t f;
    make_frame(&f, 0, 1, 46);
    uint8_t out[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk = 99;
    CHECK(wmr_proto_encode_fragment(&f, 0, 12, out, &chunk) == 0);    /* no room for payload */
    CHECK(wmr_proto_encode_fragment(&f, 46, 44, out, &chunk) == 0);   /* offset at end */
    CHECK(wmr_proto_encode_fragment(&f, 100, 44, out, &chunk) == 0);  /* offset beyond end */
    CHECK(wmr_proto_encode_fragment(NULL, 0, 44, out, &chunk) == 0);
}

static void test_roundtrip_all_capacities(void){
    printf("round trip, every capacity from 13 to 244, both hands\n");
    int ok = 1;
    for (uint16_t capacity = 13; capacity <= 244; capacity++){
        for (uint8_t hand = 0; hand < WMR_NUM_CONTROLLERS; hand++){
            wmr_reassembler_t r;
            wmr_reassembler_init(&r);
            wmr_frame_t in, out;
            make_frame(&in, hand, 1000u + capacity, 46);
            memset(&out, 0, sizeof(out));
            unsigned count;
            wmr_proto_result_t res = send_frame(&r, &in, capacity, &out, &count);
            unsigned per_fragment = capacity - 12u;
            unsigned expected = (46u + per_fragment - 1u) / per_fragment;
            if (res != WMR_PROTO_FRAME_COMPLETE || !frames_equal(&in, &out) ||
                count != expected || r.frames_incomplete[hand] || r.errors){
                ok = 0;
                printf("    failed at capacity=%u hand=%u\n", capacity, hand);
            }
        }
    }
    CHECK(ok);
}

static void test_single_fragment(void){
    printf("whole frame in one fragment (large MTU)\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t in, out;
    make_frame(&in, WMR_HAND_LEFT, 7, 46);
    unsigned count;
    CHECK(send_frame(&r, &in, 244, &out, &count) == WMR_PROTO_FRAME_COMPLETE);
    CHECK(count == 1 && frames_equal(&in, &out));
}

static void test_fragmented_stream(void){
    printf("many fragmented frames, both hands, back to back\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    for (uint32_t seq = 1; seq <= 100; seq++){
        for (uint8_t hand = 0; hand < 2; hand++){
            wmr_frame_t in, out;
            make_frame(&in, hand, seq, 46);
            unsigned count;
            CHECK(send_frame(&r, &in, 20, &out, &count) == WMR_PROTO_FRAME_COMPLETE);
            CHECK(frames_equal(&in, &out));
        }
    }
    CHECK(r.frames_complete[0] == 100 && r.frames_complete[1] == 100);
    CHECK(r.frames_incomplete[0] == 0 && r.frames_incomplete[1] == 0 && r.errors == 0);
}

static void test_interleaved_hands(void){
    printf("fragments of the two hands interleaved\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t l, rt, out;
    make_frame(&l, 0, 5, 46);
    make_frame(&rt, 1, 9, 46);
    uint8_t pl[2][WMR_CFG_MAX_NOTIFICATION], pr[2][WMR_CFG_MAX_NOTIFICATION];
    uint16_t sl[2], sr[2], chunk;
    sl[0] = wmr_proto_encode_fragment(&l, 0, 44, pl[0], &chunk);
    sl[1] = wmr_proto_encode_fragment(&l, 32, 44, pl[1], &chunk);
    sr[0] = wmr_proto_encode_fragment(&rt, 0, 44, pr[0], &chunk);
    sr[1] = wmr_proto_encode_fragment(&rt, 32, 44, pr[1], &chunk);
    CHECK(wmr_reassembler_push(&r, pl[0], sl[0], &out) == WMR_PROTO_PARTIAL);
    CHECK(wmr_reassembler_push(&r, pr[0], sr[0], &out) == WMR_PROTO_PARTIAL);
    CHECK(wmr_reassembler_push(&r, pl[1], sl[1], &out) == WMR_PROTO_FRAME_COMPLETE);
    CHECK(frames_equal(&l, &out));
    CHECK(wmr_reassembler_push(&r, pr[1], sr[1], &out) == WMR_PROTO_FRAME_COMPLETE);
    CHECK(frames_equal(&rt, &out));
    CHECK(r.frames_incomplete[0] == 0 && r.frames_incomplete[1] == 0);
}

static void test_incomplete_frame_then_new_frame(void){
    printf("incomplete frame is abandoned when the next frame starts\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t a, b, out;
    make_frame(&a, 0, 1, 46);
    make_frame(&b, 0, 2, 46);
    uint8_t packet[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk;
    uint16_t size = wmr_proto_encode_fragment(&a, 0, 44, packet, &chunk);   /* fragment 1 of 2 only */
    CHECK(wmr_reassembler_push(&r, packet, size, &out) == WMR_PROTO_PARTIAL);
    unsigned count;
    CHECK(send_frame(&r, &b, 44, &out, &count) == WMR_PROTO_FRAME_COMPLETE);
    CHECK(frames_equal(&b, &out));       /* the new frame is delivered intact */
    CHECK(r.frames_incomplete[0] == 1);  /* the old one is counted, never emitted */
    CHECK(r.frames_complete[0] == 1);
    CHECK(r.errors == 0);
}

static void test_missing_first_fragment(void){
    printf("continuation fragment with no frame in progress\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t a, out;
    make_frame(&a, 1, 3, 46);
    uint8_t packet[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk;
    uint16_t size = wmr_proto_encode_fragment(&a, 32, 44, packet, &chunk);
    CHECK(wmr_reassembler_push(&r, packet, size, &out) == WMR_PROTO_ERR_OUT_OF_ORDER);
    CHECK(r.frames_complete[1] == 0 && r.errors == 1);
}

static void test_lost_middle_fragment(void){
    printf("lost middle fragment: frame never completes\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t a, out;
    make_frame(&a, 0, 4, 46);
    uint8_t p0[WMR_CFG_MAX_NOTIFICATION], p2[WMR_CFG_MAX_NOTIFICATION], p3[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk;
    /* capacity 24 -> 12-byte chunks at offsets 0, 12, 24, 36; offset 12 is lost */
    uint16_t s0 = wmr_proto_encode_fragment(&a, 0, 24, p0, &chunk);
    uint16_t s2 = wmr_proto_encode_fragment(&a, 24, 24, p2, &chunk);
    uint16_t s3 = wmr_proto_encode_fragment(&a, 36, 24, p3, &chunk);
    CHECK(wmr_reassembler_push(&r, p0, s0, &out) == WMR_PROTO_PARTIAL);
    CHECK(wmr_reassembler_push(&r, p2, s2, &out) == WMR_PROTO_ERR_OUT_OF_ORDER);
    CHECK(r.frames_incomplete[0] == 1 && r.frames_complete[0] == 0);
    /* a later in-order fragment must not resurrect the abandoned frame */
    CHECK(wmr_reassembler_push(&r, p3, s3, &out) == WMR_PROTO_ERR_OUT_OF_ORDER);
    CHECK(r.frames_complete[0] == 0);
}

static void test_sequence_change_mid_frame(void){
    printf("continuation with a different sequence number\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t a, b, out;
    make_frame(&a, 0, 10, 46);
    make_frame(&b, 0, 11, 46);
    uint8_t p0[WMR_CFG_MAX_NOTIFICATION], p1[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk;
    uint16_t s0 = wmr_proto_encode_fragment(&a, 0, 44, p0, &chunk);
    uint16_t s1 = wmr_proto_encode_fragment(&b, 32, 44, p1, &chunk);
    CHECK(wmr_reassembler_push(&r, p0, s0, &out) == WMR_PROTO_PARTIAL);
    CHECK(wmr_reassembler_push(&r, p1, s1, &out) == WMR_PROTO_ERR_OUT_OF_ORDER);
    CHECK(r.frames_incomplete[0] == 1 && r.frames_complete[0] == 0);
}

static void test_duplicate_fragment(void){
    printf("duplicate continuation fragment\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t a, out;
    make_frame(&a, 0, 12, 46);
    uint8_t p0[WMR_CFG_MAX_NOTIFICATION], p1[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk;
    uint16_t s0 = wmr_proto_encode_fragment(&a, 0, 24, p0, &chunk);
    uint16_t s1 = wmr_proto_encode_fragment(&a, 12, 24, p1, &chunk);
    CHECK(wmr_reassembler_push(&r, p0, s0, &out) == WMR_PROTO_PARTIAL);
    CHECK(wmr_reassembler_push(&r, p1, s1, &out) == WMR_PROTO_PARTIAL);
    CHECK(wmr_reassembler_push(&r, p1, s1, &out) == WMR_PROTO_ERR_OUT_OF_ORDER);
    CHECK(r.frames_complete[0] == 0 && r.frames_incomplete[0] == 1);
}

static void test_malformed(void){
    printf("malformed packets\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t f, out;
    make_frame(&f, 0, 1, 46);
    uint8_t good[WMR_CFG_MAX_NOTIFICATION], bad[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk;
    uint16_t size = wmr_proto_encode_fragment(&f, 0, 244, good, &chunk);   /* complete in one */

    CHECK(wmr_reassembler_push(&r, good, 11, &out) == WMR_PROTO_ERR_SHORT);
    CHECK(wmr_reassembler_push(&r, good, 0, &out) == WMR_PROTO_ERR_SHORT);

    memcpy(bad, good, size); bad[0] = 0x51;
    CHECK(wmr_reassembler_push(&r, bad, size, &out) == WMR_PROTO_ERR_MAGIC);

    memcpy(bad, good, size); bad[1] = 2;
    CHECK(wmr_reassembler_push(&r, bad, size, &out) == WMR_PROTO_ERR_HAND);

    /* chunk_len no longer matches the bytes present */
    CHECK(wmr_reassembler_push(&r, good, (uint16_t) (size - 1), &out) == WMR_PROTO_ERR_LENGTH);

    memcpy(bad, good, size); bad[6] = 0; bad[7] = 0;                       /* total = 0 */
    CHECK(wmr_reassembler_push(&r, bad, size, &out) == WMR_PROTO_ERR_LENGTH);

    memcpy(bad, good, size); bad[6] = WMR_CFG_MAX_FRAME + 1; bad[7] = 0;   /* total too big */
    CHECK(wmr_reassembler_push(&r, bad, size, &out) == WMR_PROTO_ERR_LENGTH);

    memcpy(bad, good, size); bad[8] = 5;                                   /* offset 5 + chunk 46 > 46 */
    CHECK(wmr_reassembler_push(&r, bad, size, &out) == WMR_PROTO_ERR_LENGTH);

    memcpy(bad, good, 12); bad[10] = 0; bad[11] = 0;                       /* chunk = 0 */
    CHECK(wmr_reassembler_push(&r, bad, 12, &out) == WMR_PROTO_ERR_LENGTH);

    CHECK(r.frames_complete[0] == 0);
    /* the receiver still works afterwards */
    CHECK(wmr_reassembler_push(&r, good, size, &out) == WMR_PROTO_FRAME_COMPLETE);
    CHECK(frames_equal(&f, &out));
}

static void test_malformed_poisons_partial(void){
    printf("malformed fragment abandons the partial frame\n");
    wmr_reassembler_t r;
    wmr_reassembler_init(&r);
    wmr_frame_t a, out;
    make_frame(&a, 0, 20, 46);
    uint8_t p0[WMR_CFG_MAX_NOTIFICATION], p1[WMR_CFG_MAX_NOTIFICATION];
    uint16_t chunk;
    uint16_t s0 = wmr_proto_encode_fragment(&a, 0, 44, p0, &chunk);
    uint16_t s1 = wmr_proto_encode_fragment(&a, 32, 44, p1, &chunk);
    CHECK(wmr_reassembler_push(&r, p0, s0, &out) == WMR_PROTO_PARTIAL);
    p1[10] = 3;                          /* chunk_len now disagrees with the payload */
    CHECK(wmr_reassembler_push(&r, p1, s1, &out) == WMR_PROTO_ERR_LENGTH);
    CHECK(r.frames_incomplete[0] == 1);
}

#if WMR_CFG_PSVR2_SUPPORT
static void test_psvr2_frame(void){
    printf("PSVR2-length frame: predicate, round-trip, decoder boundary\n");
    CHECK(WMR_CFG_MAX_FRAME >= WMR_PSVR2_INPUT_FRAME_MAX);
    wmr_frame_t f;
    make_frame(&f, WMR_HAND_RIGHT, 7, WMR_PSVR2_INPUT_FRAME_MAX);
    f.data[0] = WMR_PSVR2_HID_HEADER;
    f.data[1] = WMR_PSVR2_INPUT_REPORT_ID;
    CHECK(wmr_psvr2_is_input_report(f.data, f.length));
    CHECK(!wmr_psvr2_is_input_report(f.data, 2));           /* header + report ID only */
    CHECK(!wmr_psvr2_is_input_report(NULL, f.length));
    f.data[0] = 0xA2;
    CHECK(!wmr_psvr2_is_input_report(f.data, f.length));
    f.data[0] = WMR_PSVR2_HID_HEADER;
    f.data[1] = 0x01;
    CHECK(!wmr_psvr2_is_input_report(f.data, f.length));    /* a WMR report ID is not PSVR2 */
    f.data[1] = WMR_PSVR2_INPUT_REPORT_ID;

    static const uint16_t capacities[] = { 44, 244 };
    for (unsigned i = 0; i < sizeof(capacities) / sizeof(capacities[0]); i++){
        wmr_reassembler_t r;
        wmr_reassembler_init(&r);
        wmr_frame_t out;
        unsigned count;
        CHECK(send_frame(&r, &f, capacities[i], &out, &count) == WMR_PROTO_FRAME_COMPLETE);
        CHECK(frames_equal(&f, &out) && count >= 1);
    }

    /* The decoded stream has no PSVR2 parser yet: FF12/FF14 must reject the frame
     * rather than half-decode it. It is counted as a decode error there. */
    uint8_t record[WMR_DECODED_SIZE];
    CHECK(!wmr_decoded_from_frame(&f, record));
}
#endif

static void test_queue(void){
    printf("queue\n");
    static wmr_queue_t q;                /* large; keep off the stack */
    wmr_queue_init(&q);
    CHECK(wmr_queue_peek(&q) == NULL);
    wmr_queue_pop(&q);                   /* no-op when empty */

    wmr_frame_t f;
    for (unsigned i = 0; i < WMR_CFG_QUEUE_CAPACITY; i++){
        make_frame(&f, (uint8_t) (i & 1), i + 1, 46);
        CHECK(wmr_queue_push(&q, &f));
    }
    CHECK(q.count == WMR_CFG_QUEUE_CAPACITY && q.high_water == WMR_CFG_QUEUE_CAPACITY);
    make_frame(&f, 0, 9999, 46);
    CHECK(!wmr_queue_push(&q, &f));      /* full: the newest frame is rejected */
    CHECK(q.count == WMR_CFG_QUEUE_CAPACITY);

    CHECK(wmr_queue_peek(&q)->sequence == 1);   /* FIFO order; oldest kept */
    wmr_queue_pop(&q);
    CHECK(wmr_queue_peek(&q)->sequence == 2);
    CHECK(wmr_queue_push(&q, &f));       /* wraps around */
    CHECK(q.count == WMR_CFG_QUEUE_CAPACITY);

    uint32_t purged[WMR_NUM_CONTROLLERS] = {0, 0};
    wmr_queue_clear(&q, purged);
    CHECK(q.count == 0 && wmr_queue_peek(&q) == NULL);
    /* 256 frames alternated LEFT/RIGHT (128 each); one LEFT was popped and
     * one LEFT (sequence 9999) pushed, so 128 each are purged. */
    CHECK(purged[0] == 128 && purged[1] == 128);
    CHECK(q.high_water == WMR_CFG_QUEUE_CAPACITY);   /* peak survives clear */
    CHECK(wmr_queue_push(&q, &f));
    CHECK(wmr_queue_peek(&q)->sequence == 9999);
    wmr_queue_clear(&q, NULL);           /* NULL purge counters are allowed */
    CHECK(q.count == 0);
}

/* Round-trip every captured sample frame at every capacity. The hand is
 * alternated only to exercise both reassembly slots; samples are unattributed. */
static void test_sample_file(const char *path){
    printf("sample frames from %s\n", path);
    FILE *fp = fopen(path, "r");
    CHECK(fp != NULL);
    if (!fp) return;
    char line[512];
    unsigned samples = 0;
    int ok = 1;
    while (fgets(line, sizeof(line), fp)){
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        char *token = strtok(line, " \t\r\n");        /* label */
        wmr_frame_t in;
        memset(&in, 0, sizeof(in));
        in.sequence = 100 + samples;
        in.hand = (uint8_t) (samples & 1);
        while ((token = strtok(NULL, " \t\r\n")) != NULL && in.length < WMR_CFG_MAX_FRAME){
            in.data[in.length++] = (uint8_t) strtoul(token, NULL, 16);
        }
        CHECK(in.length == 46 && in.data[0] == 0xA1);
        for (uint16_t capacity = 13; capacity <= 244; capacity++){
            wmr_reassembler_t r;
            wmr_reassembler_init(&r);
            wmr_frame_t out;
            unsigned count;
            if (send_frame(&r, &in, capacity, &out, &count) != WMR_PROTO_FRAME_COMPLETE ||
                !frames_equal(&in, &out)) ok = 0;
        }
        samples++;
    }
    fclose(fp);
    CHECK(ok);
    CHECK(samples >= 2);
    printf("  %u sample frames\n", samples);
}

/* ---- Decoded stream (FF12) -------------------------------------------- */

static const uint8_t decoded_golden[WMR_DECODED_SIZE] = {
    0x01, 0x01, 0x00, 0x06,                                     /* version, hand, flags, buttons */
    0x04, 0x03, 0x02, 0x01,                                     /* sequence 0x01020304 */
    0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11,             /* rx_time_us */
    0xDD, 0xCC, 0xBB, 0xAA,                                     /* device_ticks */
    0x00, 0x00, 0x80, 0x3F, 0x00, 0x00, 0x00, 0xC0, 0x00, 0x00, 0x00, 0x3F,   /* gyro 1.0 -2.0 0.5 */
    0x00, 0x00, 0x80, 0x40, 0x00, 0x00, 0x00, 0xC1, 0x00, 0x00, 0x00, 0x00,   /* accel 4.0 -8.0 0.0 */
    0xFE, 0xFF,                                                 /* temperature -2 */
    0xFF, 0x7F, 0x01, 0x80,                                     /* stick 32767 -32767 */
    0x00, 0x00, 0x00, 0x40,                                     /* trackpad 0 16384 */
    0xFF, 0x7B                                                  /* trigger, battery */
};

static void test_decoded_golden(void){
    printf("decoded frame (FF12) golden packet\n");
    wmr_decoded_t d;
    memset(&d, 0, sizeof(d));
    d.hand = 1; d.buttons = 0x06; d.sequence = 0x01020304;
    d.rx_time_us = UINT64_C(0x1122334455667788); d.device_ticks = 0xAABBCCDDu;
    d.gyro[0] = 1.0f; d.gyro[1] = -2.0f; d.gyro[2] = 0.5f;
    d.accel[0] = 4.0f; d.accel[1] = -8.0f; d.accel[2] = 0.0f;
    d.temperature_raw = -2;
    d.stick[0] = 32767; d.stick[1] = -32767;
    d.trackpad[0] = 0; d.trackpad[1] = 16384;
    d.trigger = 0xFF; d.battery = 0x7B;
    uint8_t out[WMR_DECODED_SIZE];
    wmr_decoded_pack(&d, out);
    CHECK(memcmp(out, decoded_golden, WMR_DECODED_SIZE) == 0);

    wmr_decoded_t u;
    CHECK(wmr_decoded_unpack(decoded_golden, WMR_DECODED_SIZE, &u));
    CHECK(u.hand == 1 && u.buttons == 0x06 && u.sequence == 0x01020304);
    CHECK(u.rx_time_us == UINT64_C(0x1122334455667788) && u.device_ticks == 0xAABBCCDDu);
    CHECK(u.gyro[0] == 1.0f && u.gyro[1] == -2.0f && u.gyro[2] == 0.5f);
    CHECK(u.accel[0] == 4.0f && u.accel[1] == -8.0f && u.accel[2] == 0.0f);
    CHECK(u.temperature_raw == -2 && u.stick[0] == 32767 && u.stick[1] == -32767);
    CHECK(u.trackpad[0] == 0 && u.trackpad[1] == 16384 && u.trigger == 0xFF && u.battery == 0x7B);
}

static void test_decoded_rejects(void){
    printf("decoded frame (FF12) rejects bad values\n");
    uint8_t p[WMR_DECODED_SIZE];
    wmr_decoded_t u;
    memcpy(p, decoded_golden, sizeof(p));
    CHECK(!wmr_decoded_unpack(p, WMR_DECODED_SIZE - 1, &u));
    CHECK(!wmr_decoded_unpack(p, WMR_DECODED_SIZE + 1, &u));
    p[0] = 2; CHECK(!wmr_decoded_unpack(p, WMR_DECODED_SIZE, &u));   /* unknown version */
    memcpy(p, decoded_golden, sizeof(p));
    p[1] = 2; CHECK(!wmr_decoded_unpack(p, WMR_DECODED_SIZE, &u));   /* bad hand */
    CHECK(!wmr_decoded_unpack(NULL, WMR_DECODED_SIZE, &u));
}

/* Raw frame -> decoder -> FF12 record -> bytes -> record. */
static void test_decoded_from_frame(void){
    printf("decoded frame (FF12) from a raw frame\n");
    uint8_t f[WMR_DECODE_FRAME_LENGTH];
    memset(f, 0, sizeof(f));
    f[0] = 0xA1; f[1] = 0x01;
    f[2] = 0x0C;                         /* menu + squeeze */
    f[2 + 1] = 0xFF; f[2 + 2] = 0xF7; f[2 + 3] = 0x7F;   /* stick centred */
    f[2 + 4] = 0x80;                     /* trigger */
    f[2 + 5] = 0xFF; f[2 + 6] = 0xFF;    /* trackpad untouched */
    f[2 + 7] = 0x55;                     /* battery */
    f[2 + 28] = 0x10; f[2 + 29] = 0x27;  /* ticks 10000 */
    wmr_controller_state_t s;
    CHECK(wmr_decode_frame(f, sizeof(f), &s));
    wmr_decoded_t d, u;
    wmr_decoded_from_state(&d, &s, 0, 42, 123456);
    CHECK(d.flags == 0);                 /* uncalibrated */
    uint8_t out[WMR_DECODED_SIZE];
    wmr_decoded_pack(&d, out);
    CHECK(wmr_decoded_unpack(out, sizeof(out), &u));
    CHECK(u.hand == 0 && u.sequence == 42 && u.rx_time_us == 123456);
    CHECK(u.buttons == (WMR_BTN_MENU | WMR_BTN_SQUEEZE));
    CHECK(u.stick[0] == 0 && u.stick[1] == 0 && u.trackpad[0] == 0 && u.trackpad[1] == 0);
    CHECK(u.trigger == 0x80 && u.battery == 0x55 && u.device_ticks == 10000);
}

int main(int argc, char **argv){
    test_capacity();
    test_golden_packet();
    test_encoder_arguments();
    test_roundtrip_all_capacities();
    test_single_fragment();
    test_fragmented_stream();
    test_interleaved_hands();
    test_incomplete_frame_then_new_frame();
    test_missing_first_fragment();
    test_lost_middle_fragment();
    test_sequence_change_mid_frame();
    test_duplicate_fragment();
    test_malformed();
    test_malformed_poisons_partial();
#if WMR_CFG_PSVR2_SUPPORT
    test_psvr2_frame();
#endif
    test_queue();
    test_decoded_golden();
    test_decoded_rejects();
    test_decoded_from_frame();
    if (argc > 1) test_sample_file(argv[1]);
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
