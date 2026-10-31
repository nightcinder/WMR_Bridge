/*
 * Host-test stand-in for the firmware's btstack_config.h.
 *
 * Only the switches the host-tested firmware modules look at are defined here.
 * ENABLE_STDIN_INPUT is BTstack's own name; the firmware checks
 * HAVE_BTSTACK_STDIN, which the ESP-IDF port normally sets.
 */

#ifndef HOST_STUBS_BTSTACK_CONFIG_H
#define HOST_STUBS_BTSTACK_CONFIG_H

#define HAVE_BTSTACK_STDIN

#endif /* HOST_STUBS_BTSTACK_CONFIG_H */
