#ifndef LANE_STYLE_CORE_H
#define LANE_STYLE_CORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define LANE_CONFIRMED_HOLD_MS 550U
#define LANE_UNKNOWN_TOLERANCE 1U

#define LANE_INPUT_WIDTH 640
#define LANE_INPUT_HEIGHT 384
#define LANE_MAP_PIXELS (LANE_INPUT_WIDTH * LANE_INPUT_HEIGHT)
#define LANE_MAX_BOUNDARIES 64
#define LANE_CURVE_POINTS 32
#define LANE_STYLE_CORE_VERSION "lane-style-curves-c-v1"

enum lane_line_type { LANE_UNKNOWN, LANE_SOLID, LANE_DASHED };
enum lane_reason {
    LANE_REASON_OK, LANE_REASON_NO_PIXELS, LANE_REASON_LOW_SCORE, LANE_REASON_NO_RUNS,
    LANE_REASON_NO_CURVE, LANE_REASON_NO_SIDE, LANE_REASON_MIXED, LANE_REASON_CONFLICT,
    LANE_REASON_CONFIRM, LANE_REASON_MOVED, LANE_REASON_TYPE, LANE_REASON_HOLD,
    LANE_REASON_EXPIRED, LANE_REASON_WAIT, LANE_REASON_STALE, LANE_REASON_ERROR
};
enum lane_reset_reason { LANE_RESET_NONE, LANE_RESET_SIZE, LANE_RESET_CLOCK, LANE_RESET_GAP };

struct lane_diagnostics {
    /* Counts cover the rows scanned by the boundary decoder, not the whole image. */
    unsigned raw_pixels[2], supported_pixels[2], runs[2], candidates[2];
    unsigned paths, rejected_geometry, conflicts, corridor_confirmations;
    double input_gap_ms;
    enum lane_reset_reason reset;
    bool corridor_geometry_valid;
};

struct lane_point { float x, y; };

struct lane_boundary {
    struct lane_point curve[LANE_CURVE_POINTS];
    double coefficients[3];
    float bottom, top_x, top_y, quality, style_fraction;
    enum lane_line_type observed;
    bool style_conflict;
};

struct lane_side {
    enum lane_line_type type, decision_type, observed;
    bool held;
    unsigned confirmations;
    float confidence, age_ms;
    int boundary_index;
    enum lane_reason reason;
};

struct lane_result {
    struct lane_boundary boundaries[LANE_MAX_BOUNDARIES];
    unsigned boundary_count;
    struct lane_side sides[2];
    bool corridor_confirmed;
    float offset;
    uint64_t sequence, timestamp_ns;
    struct lane_diagnostics diagnostics;
};

struct lane_style_view {
    uint8_t classes[LANE_MAP_PIXELS];
    struct lane_result result;
    unsigned width, height;
};

struct lane_history {
    struct lane_boundary boundary;
    struct lane_boundary confirmed_boundary;
    enum lane_line_type pending, confirmed;
    unsigned count, unknown_count;
    uint64_t observed_ns, confirmed_ns;
    bool active, has_confirmation;
};

struct lane_style_core {
    uint8_t classes[LANE_MAP_PIXELS];
    float confidence[LANE_MAP_PIXELS], margin[LANE_MAP_PIXELS];
    struct lane_history history[2];
    uint64_t last_timestamp_ns;
    unsigned analysis_gap_ms; /* Zero preserves the original 500ms default. */
    bool has_timestamp;
    unsigned corridor_count;
    struct lane_boundary corridor_pair[2];
    unsigned source_width, source_height;
};

bool lane_style_prepare_rgb565(const uint8_t *source, unsigned width, unsigned height, uint8_t *rgb);
struct lane_preprocessor;
struct lane_preprocessor *lane_preprocessor_create(void);
void lane_preprocessor_destroy(struct lane_preprocessor *processor);
bool lane_preprocessor_prepare(struct lane_preprocessor *processor, const uint8_t *source,
                               unsigned width, unsigned height, uint8_t *rgb);
bool lane_style_decode_logits(struct lane_style_core *core, const float *logits, bool nhwc);
void lane_style_reset(struct lane_style_core *core);
bool lane_style_locate(struct lane_style_core *core, unsigned width, unsigned height,
                       uint64_t sequence, uint64_t timestamp_ns, struct lane_result *result);
void lane_style_draw_rgb565(const struct lane_style_core *core, const struct lane_result *result,
                            uint8_t *output, unsigned output_width, unsigned output_height,
                            unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height,
                            unsigned source_width, unsigned source_height);
const char *lane_style_type_name(enum lane_line_type type);
const char *lane_style_reason_name(enum lane_reason reason);
const char *lane_style_reset_name(enum lane_reset_reason reason);
void lane_style_draw_view_rgb565(const struct lane_style_view *view, uint8_t *output,
                                 unsigned output_width, unsigned output_height,
                                 unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height);
void lane_style_draw_status_rgb565(uint8_t *output, unsigned output_width, unsigned output_height,
                                   unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height,
                                   const char *text);
void lane_style_draw_reasons_rgb565(const struct lane_result *result, uint8_t *output,
                                    unsigned output_width, unsigned output_height,
                                    unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height);

#endif
