/*
 * libFuzzer target: the WMR input-report decoder.
 *
 * `wmr_decode_frame` takes the 46 bytes a controller sends over HID on the
 * bridge's GATT characteristic - bytes that come off the air, not from this
 * codebase. `wmr_decode_frame_is_wmr` is called first on the same bytes to
 * separate "not ours" from "ours but undecodable", so both go through the
 * fuzzer: a malformed header must not steer the length check.
 *
 * Build: cmake -B build-fuzz -DWMR_BUILD_FUZZERS=ON -DCMAKE_C_COMPILER=clang
 *        -DCMAKE_C_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer"
 * Run:   ./build-fuzz/fuzz_decode -max_total_time=60 fuzz/corpus/decode
 */

#include <stddef.h>
#include <stdint.h>

#include "wmr_decode.h"

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size > UINT16_MAX) {
        return 0;
    }

    wmr_controller_state_t state = {0};
    uint16_t length = (uint16_t)size;

    (void)wmr_decode_frame_is_wmr(data, length);
    (void)wmr_decode_frame(data, length, &state);

    return 0;
}
