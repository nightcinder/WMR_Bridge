/*
 * wmr_hid.h - BLE HID-over-GATT gamepad report: descriptor, mapper and state.
 *
 * Pure C, no BTstack dependency. The report is the one in wmr-companion's
 * wmr_companion_hid_android/lib/src/hid_report.dart: report ID 1, 9 payload
 * bytes, and the same report descriptor bytes. The mapper is a port of
 * mapGamepad() from wmr_core/lib/domain/gamepad.dart, restricted to the
 * physical inputs: buttons, sticks, triggers and trackpads. Tilt is available
 * in the pure mapper but not fed by the firmware (orientation fusion stays in
 * the app); see docs/HID_GAMEPAD.md.
 *
 * The descriptor and report bytes are part of the wire contract. Change them
 * only together with the companion package and tests/vectors/hid_gamepad.txt.
 */

#ifndef WMR_HID_H
#define WMR_HID_H

#include <stdint.h>
#include <stdbool.h>

#define WMR_HID_REPORT_ID      1
#define WMR_HID_REPORT_SIZE    9      /* payload, without the report ID byte */
#define WMR_HID_HAT_CENTRED    15

/* A hand's last sample older than this counts as released. Keeps a controller
 * that drops off the link from leaving a button or stick stuck on the host. */
#define WMR_HID_STALE_MS       250
#define WMR_HID_STALE_US       (WMR_HID_STALE_MS * 1000ull)

/* At most one notification per interval; samples in between are coalesced. */
#define WMR_HID_MIN_INTERVAL_US 10000ull

typedef enum {
    WMR_HID_TRACKPAD_NONE = 0,
    WMR_HID_TRACKPAD_DPAD = 1,
    WMR_HID_TRACKPAD_FACE = 2,
} wmr_hid_trackpad_role_t;

typedef enum {
    WMR_HID_TILT_NONE = 0,
    WMR_HID_TILT_LEFT_STICK = 1,
    WMR_HID_TILT_RIGHT_STICK = 2,
} wmr_hid_tilt_target_t;

/* Defaults are GamepadConfig's defaults in the companion package. */
typedef struct {
    float trigger_full_scale;          /* raw trigger value that is full pull */
    float trigger_dead_zone;           /* 0..0.95 */
    float trigger_press_threshold;     /* 0..1, after the dead zone */
    float trigger_release_threshold;   /* <= press threshold */
    float trackpad_centre_radius;      /* 0..0.8 */
    uint8_t left_trackpad;             /* wmr_hid_trackpad_role_t */
    uint8_t right_trackpad;
    bool invert_stick_y;
    bool invert_trackpad_y;
    uint8_t tilt_target;               /* wmr_hid_tilt_target_t */
    uint8_t tilt_hand;                 /* 0 LEFT, 1 RIGHT */
    float tilt_max_degrees;
    float tilt_dead_zone_degrees;
    bool tilt_invert_x;
    bool tilt_invert_y;
} wmr_hid_config_t;

/* One hand, in the mapper's units: stick and trackpad -1..1 (y positive up),
 * trigger raw 0..255. */
typedef struct {
    bool connected;
    uint8_t buttons;                   /* WMR_BTN_* bits from wmr_decoded.h */
    float stick[2];
    uint8_t trigger;
    float trackpad[2];
    bool has_tilt;
    float roll_deg;                    /* view frame; only used when a tilt
                                          target is configured */
    float pitch_deg;
} wmr_hid_hand_t;

/* L2/R2 state of the previous frame, for the trigger Schmitt trigger. */
typedef struct {
    bool l2;
    bool r2;
} wmr_hid_history_t;

/* Decoded report, before serialisation. */
typedef struct {
    uint16_t buttons;                  /* HID button bits, see wmr_hid_pack */
    uint8_t hat;                       /* 0..7 clockwise from up, 15 centred */
    int8_t x;
    int8_t y;
    int8_t z;
    int8_t rz;
    uint8_t brake;
    uint8_t accelerator;
} wmr_hid_report_t;

/* The report descriptor, byte for byte the companion's hidReportDescriptor. */
const uint8_t *wmr_hid_report_descriptor(unsigned *size);

/* GamepadConfig defaults of the companion package. */
const wmr_hid_config_t *wmr_hid_default_config(void);

/* Map both hands onto one gamepad. `previous` is optional; when given it is
 * read for L2/R2 hysteresis and updated with this frame's state. */
void wmr_hid_map(const wmr_hid_hand_t *left, const wmr_hid_hand_t *right,
                 const wmr_hid_config_t *config, wmr_hid_history_t *previous,
                 wmr_hid_report_t *out);

/* Serialise the payload (report ID is sent separately by HID-over-GATT).
 * `out` is exactly WMR_HID_REPORT_SIZE bytes. */
void wmr_hid_pack(const wmr_hid_report_t *report, uint8_t out[WMR_HID_REPORT_SIZE]);

/* Map then pack. */
void wmr_hid_build_report(const wmr_hid_hand_t *left, const wmr_hid_hand_t *right,
                          const wmr_hid_config_t *config, wmr_hid_history_t *previous,
                          uint8_t out[WMR_HID_REPORT_SIZE]);

/* ---- Firmware state ---------------------------------------------------- */

typedef struct {
    wmr_hid_hand_t hands[2];
    uint64_t last_sample_us[2];
    wmr_hid_history_t history;
    uint8_t last_report[WMR_HID_REPORT_SIZE];
    bool have_last_report;
    bool dirty;                        /* a sample arrived since the last send */
    bool force;                        /* send even if unchanged */
    uint64_t last_send_us;
} wmr_hid_state_t;

/* Forget both hands, the trigger history and the last report. The next
 * wmr_hid_take_report() sends a neutral report. */
void wmr_hid_reset(wmr_hid_state_t *state);

/* Decode and store one controller report. Returns false if it is not a valid
 * input report. `now_us` is the receive time (esp_timer). */
bool wmr_hid_note_report(wmr_hid_state_t *state, uint8_t hand,
                         const uint8_t *report, uint16_t length, uint64_t now_us);

/* Release hands whose last sample is older than WMR_HID_STALE_US. Returns true
 * if a hand changed to released, i.e. a neutral report is owed. */
bool wmr_hid_expire(wmr_hid_state_t *state, uint64_t now_us);

/* True when a notification is due: a changed sample, a release, or a forced
 * reset, and the rate limit has elapsed. */
bool wmr_hid_report_due(const wmr_hid_state_t *state, uint64_t now_us);

/* Build the due report into `out`. Returns false when nothing is due or the
 * mapped report equals the last one. On success the report is marked sent. */
bool wmr_hid_take_report(wmr_hid_state_t *state, uint64_t now_us,
                         uint8_t out[WMR_HID_REPORT_SIZE]);

#endif /* WMR_HID_H */
