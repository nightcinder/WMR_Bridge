/* Host-test stand-in for ESP-IDF's esp_app_desc.h: only the fields the console prints. */

#ifndef HOST_STUBS_ESP_APP_DESC_H
#define HOST_STUBS_ESP_APP_DESC_H

#include <stdint.h>

typedef struct {
    uint32_t magic_word;
    uint32_t secure_version;
    uint32_t reserv1[2];
    char version[32];
    char project_name[32];
    char time[16];
    char date[16];
    char idf_ver[32];
} esp_app_desc_t;

const esp_app_desc_t *esp_app_get_description(void);

#endif /* HOST_STUBS_ESP_APP_DESC_H */
