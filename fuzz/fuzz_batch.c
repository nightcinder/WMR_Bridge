/*
 * libFuzzer target: the FF14 batch envelope.
 *
 * `wmr_batch_parse` splits a notification value into 1..9 fixed-size records.
 * Its contract is size == 2 + 56 x count, which is exactly the kind of
 * arithmetic a truncated or lying header attacks: count comes from the packet
 * and drives the record pointer the caller then walks.
 *
 * Build: cmake -B build-fuzz -DWMR_BUILD_FUZZERS=ON -DCMAKE_C_COMPILER=clang
 *        -DCMAKE_C_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer"
 * Run:   ./build-fuzz/fuzz_batch -max_total_time=60 fuzz/corpus/batch
 */

#include <stddef.h>
#include <stdint.h>

#include "wmr_batch.h"

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size > UINT16_MAX) {
        return 0;
    }

    unsigned count = 0;
    const uint8_t *records = NULL;
    (void)wmr_batch_parse(data, (uint16_t)size, &count, &records);

    /* Walk what the parser claimed, the way the notify path does. A parser
     * that reports a count it cannot address trips ASan here. */
    if (records != NULL) {
        for (unsigned i = 0; i < count; i++) {
            volatile uint8_t first = records[i * WMR_DECODED_SIZE];
            (void)first;
        }
    }

    return 0;
}
