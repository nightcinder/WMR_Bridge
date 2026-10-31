/*
 * Host-test stand-in for BTstack's run loop: a fake clock and a timer list.
 * tests/host_stubs/host_stubs.c drives it; see host_stubs.h.
 */

#ifndef HOST_STUBS_BTSTACK_RUN_LOOP_H
#define HOST_STUBS_BTSTACK_RUN_LOOP_H

#include <stdint.h>
#include <stdbool.h>

typedef struct btstack_timer_source {
    struct btstack_timer_source *next;
    void (*process)(struct btstack_timer_source *ts);
    void *context;
    uint32_t timeout_ms;
    uint32_t due_ms;
    bool     in_list;
} btstack_timer_source_t;

uint32_t btstack_run_loop_get_time_ms(void);

void btstack_run_loop_set_timer(btstack_timer_source_t *timer, uint32_t timeout_ms);
void btstack_run_loop_add_timer(btstack_timer_source_t *timer);
int  btstack_run_loop_remove_timer(btstack_timer_source_t *timer);
void btstack_run_loop_set_timer_context(btstack_timer_source_t *timer, void *context);
void *btstack_run_loop_get_timer_context(btstack_timer_source_t *timer);

#endif /* HOST_STUBS_BTSTACK_RUN_LOOP_H */
