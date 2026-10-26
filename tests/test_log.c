/*
 * Host tests for the firmware log line format. No BTstack needed: wmr_log.h
 * keeps the formatter pure, so the prefix, truncation and newline handling can
 * be checked on the build machine. See src/wmr_log.h.
 */

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "wmr_log.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static size_t format_line(char *out, size_t cap, unsigned long ms,
                          char level, const char *tag, const char *fmt, ...){
    va_list args;
    va_start(args, fmt);
    size_t length = wmr_log_format(out, cap, ms, level, tag, fmt, args);
    va_end(args);
    return length;
}

static void test_prefix(void){
    char line[WMR_LOG_LINE_MAX];
    size_t n = format_line(line, sizeof(line), 0, 'I', "pair", "hello");
    CHECK(n == strlen(line));
    CHECK(strcmp(line, "[     0.000] I pair   hello") == 0);

    n = format_line(line, sizeof(line), 12345, 'W', "link", "status=%u", 5u);
    CHECK(n == strlen(line));
    CHECK(strcmp(line, "[    12.345] W link   status=5") == 0);

    n = format_line(line, sizeof(line), 3661001, 'E', "ff13", "failed");
    CHECK(strcmp(line, "[  3661.001] E ff13   failed") == 0);
    CHECK(n == strlen(line));
    CHECK(strchr(line, '\n') == NULL);
}

static void test_truncation(void){
    char line[32];
    size_t n = format_line(line, sizeof(line), 1, 'I', "pair", "%s",
                           "a message that cannot fit");
    CHECK(n == sizeof(line) - 1);
    CHECK(line[sizeof(line) - 1] == '\0');
    CHECK(strncmp(line, "[     0.001] I pair   ", 22) == 0);

    char tiny[8];
    n = format_line(tiny, sizeof(tiny), 999, 'I', "pair", "x");
    CHECK(n == sizeof(tiny) - 1);
    CHECK(tiny[sizeof(tiny) - 1] == '\0');
}

static void test_empty_tag(void){
    char line[WMR_LOG_LINE_MAX];
    size_t n = format_line(line, sizeof(line), 42, 'I', NULL, "no tag");
    CHECK(n == strlen(line));
    CHECK(strcmp(line, "[     0.042] I        no tag") == 0);
}

int main(void){
    test_prefix();
    test_truncation();
    test_empty_tag();
    printf("%d checks, %d failure(s)\n", checks, failures);
    return failures ? 1 : 0;
}
