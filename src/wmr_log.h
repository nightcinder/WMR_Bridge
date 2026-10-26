/*
 * Copyright (C) 2017 BlueKitchen GmbH
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holders nor the names of
 *    contributors may be used to endorse or promote products derived
 *    from this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY BLUEKITCHEN GMBH AND CONTRIBUTORS
 * ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL BLUEKITCHEN
 * GMBH OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 * BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
 * OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
 * AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF
 * THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 *
 * Please inquire about commercial licensing options at
 * contact@bluekitchen-gmbh.com
 *
 */

/*
 * wmr_log.h - the firmware's one log format.
 *
 * Every human-facing line is
 *
 *   [   12.345] I pair   LEFT ready at AA:BB:CC:DD:EE:FF (WMR)
 *
 * with milliseconds since boot, a one-letter level, and a short tag. The
 * pairing tag is the one to read when a controller will not pair: it records
 * discovery, link up, authentication/encryption, descriptor, first report and
 * the reason a step failed. Tags are left-aligned to six characters so the
 * message column stays put in a serial monitor.
 *
 * This header is deliberately free of BTstack includes: the formatter is a
 * pure function and is covered by the host tests. wmr_log.c supplies the
 * timestamp and writes the line.
 */

#ifndef WMR_LOG_H
#define WMR_LOG_H

#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>

/* Rendered prefix plus message; longer messages are truncated at a newline. */
#define WMR_LOG_LINE_MAX 192

/* Render one complete line without its trailing newline. Returns the length
 * written (excluding the NUL), capped at cap - 1. */
static inline size_t wmr_log_format(char *out, size_t cap, unsigned long ms,
                                    char level, const char *tag,
                                    const char *fmt, va_list args){
    if (!out || cap == 0) return 0;
    int prefix = snprintf(out, cap, "[%6lu.%03lu] %c %-6s ",
                          ms / 1000ul, ms % 1000ul, level, tag ? tag : "");
    if (prefix < 0){
        out[0] = '\0';
        return 0;
    }
    size_t used = (size_t) prefix;
    if (used >= cap){
        out[cap - 1] = '\0';
        return cap - 1;
    }
    int body = vsnprintf(out + used, cap - used, fmt, args);
    if (body < 0) return used;
    used += (size_t) body;
    if (used >= cap) return cap - 1;
    return used;
}

void wmr_log(char level, const char *tag, const char *fmt, ...);

#define WMR_LOGI(tag, ...) wmr_log('I', tag, __VA_ARGS__)
#define WMR_LOGW(tag, ...) wmr_log('W', tag, __VA_ARGS__)
#define WMR_LOGE(tag, ...) wmr_log('E', tag, __VA_ARGS__)

#endif /* WMR_LOG_H */
