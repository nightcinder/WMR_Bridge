# Internal static codec library and its host tests. This is not the firmware build:
# tools/firmware/build.py builds the ESP-IDF project inside the BTstack port. There is no
# install(), shared-library or pkg-config target here: wmr_codec is for this repository's
# own host tests only.
#
#   cmake -B build && cmake --build build && ctest --test-dir build --output-on-failure

cmake_minimum_required(VERSION 3.16)
project(wmr_codec C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    set(WMR_TEST_WARNINGS -Wall -Wextra -Wconversion -Werror)
else()
    set(WMR_TEST_WARNINGS)
endif()

add_library(wmr_codec STATIC
    src/wmr_decode.c
    src/wmr_decoded.c
    src/wmr_controller_driver.c
    src/wmr_switch.c
    src/wmr_batch.c
    src/wmr_protocol.c
    src/wmr_hid.c
)
target_include_directories(wmr_codec PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_compile_options(wmr_codec PRIVATE ${WMR_TEST_WARNINGS})
if(NOT MSVC)
    target_link_libraries(wmr_codec PUBLIC m)
endif()

enable_testing()

# Adds a host-test executable linked against wmr_codec. The test is registered separately
# so its arguments stay out of the source list.
function(wmr_test_target name)
    add_executable(${name} ${ARGN})
    target_include_directories(${name} PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/host_stubs")
    target_compile_options(${name} PRIVATE ${WMR_TEST_WARNINGS})
    target_link_libraries(${name} PRIVATE wmr_codec)
endfunction()

# test_protocol and test_batch also exercise the queue and runtime levers, which are
# firmware modules and stay outside the codec library.
wmr_test_target(test_protocol tests/test_protocol.c src/wmr_queue.c)
add_test(NAME test_protocol
    COMMAND test_protocol "${CMAKE_CURRENT_SOURCE_DIR}/samples/controller_frames.txt")

wmr_test_target(test_decode tests/test_decode.c)
add_test(NAME test_decode
    COMMAND test_decode "${CMAKE_CURRENT_SOURCE_DIR}/samples/controller_frames.txt")

wmr_test_target(test_switch tests/test_switch.c src/wmr_discovery.c)
add_test(NAME test_switch COMMAND test_switch)

wmr_test_target(test_stream_mode tests/test_stream_mode.c src/wmr_stream_mode.c)
add_test(NAME test_stream_mode
    COMMAND test_stream_mode "${CMAKE_CURRENT_SOURCE_DIR}/samples/controller_frames.txt")

wmr_test_target(test_batch tests/test_batch.c
    src/wmr_levers.c src/wmr_stream_mode.c src/wmr_queue.c)
add_test(NAME test_batch COMMAND test_batch)

wmr_test_target(test_batch_on tests/test_batch.c
    src/wmr_levers.c src/wmr_stream_mode.c src/wmr_queue.c)
target_compile_definitions(test_batch_on PRIVATE WMR_CFG_TEST_LEVERS=1)
add_test(NAME test_batch_on COMMAND test_batch_on)

wmr_test_target(test_hid tests/test_hid.c)
add_test(NAME test_hid
    COMMAND test_hid
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/vectors/hid_gamepad.txt"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/vectors/hid_report_descriptor.txt")

# libFuzzer targets over the byte-level entry points the GATT notify path feeds:
# the 46-byte HID frame, the 56-byte FF12 record and the FF14 batch envelope.
# Host-only and opt-in: the firmware build never sees these, and libFuzzer needs
# clang. The library is built with the same sanitizers so a fault inside
# wmr_codec is caught, not just one in the harness:
#
#   cmake -B build-fuzz -DWMR_BUILD_FUZZERS=ON -DCMAKE_C_COMPILER=clang \
#     -DCMAKE_C_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer" \
#     -DCMAKE_BUILD_TYPE=RelWithDebInfo
#   cmake --build build-fuzz --parallel
#   ./build-fuzz/fuzz_decode -max_total_time=60 fuzz/corpus/decode
option(WMR_BUILD_FUZZERS "Build the libFuzzer targets (requires clang)" OFF)
if(WMR_BUILD_FUZZERS)
    if(NOT CMAKE_C_COMPILER_ID MATCHES "Clang")
        message(FATAL_ERROR "WMR_BUILD_FUZZERS requires clang")
    endif()
    foreach(fuzz_target decode decoded batch)
        add_executable(fuzz_${fuzz_target} fuzz/fuzz_${fuzz_target}.c)
        target_include_directories(fuzz_${fuzz_target} PRIVATE
            "${CMAKE_CURRENT_SOURCE_DIR}/src")
        target_compile_options(fuzz_${fuzz_target} PRIVATE
            ${WMR_TEST_WARNINGS}
            -fsanitize=fuzzer,address,undefined -fno-omit-frame-pointer -g)
        target_link_options(fuzz_${fuzz_target} PRIVATE
            -fsanitize=fuzzer,address,undefined)
        target_link_libraries(fuzz_${fuzz_target} PRIVATE wmr_codec)
    endforeach()
endif()
