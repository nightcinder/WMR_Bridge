/*
 * libFuzzer target: the FF12 decoded-record unpacker.
 *
 * `wmr_decoded_unpack` reads a 56-byte notification value straight from the
 * GATT write/notify path. The record carries a version byte, flags, a sequence
 * number and packed sensor words, all attacker-controlled as far as this code
 * knows.
 *
 * Build: cmake -B build-fuzz -DWMR_BUILD_FUZZERS=ON -DCMAKE_C_COMPILER=clang
 *        -DCMAKE_C_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer"
 * Run:   ./build-fuzz/fuzz_decoded -max_total_time=60 fuzz/corpus/decoded
 */

#include <stddef.h>
#include <stdint.h>

#include "wmr_decoded.h"

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size > UINT16_MAX) {
        return 0;
    }

    wmr_decoded_t record = {0};
    (void)wmr_decoded_unpack(data, (uint16_t)size, &record);

    return 0;
}
