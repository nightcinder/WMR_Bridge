/* Host-test stand-in for ESP-IDF's esp_timer.h. The fake clock lives in host_stubs.c. */

#ifndef HOST_STUBS_ESP_TIMER_H
#define HOST_STUBS_ESP_TIMER_H

#include <stdint.h>

int64_t esp_timer_get_time(void);

#endif /* HOST_STUBS_ESP_TIMER_H */
