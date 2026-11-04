#ifndef ROAD_OBJECT_CORE_H
#define ROAD_OBJECT_CORE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define ROAD_OBJECT_CAPACITY 128
#define ROAD_YOLO_CANDIDATES 4096
#define ROAD_OBJECT_DECODER "yolov5-coco80-road-v1"
#ifdef HAVE_FRONT_P1
#define ROAD_SIGNAL_VERSION "yolo-box-hsv-green-symbols-p1-bloom-v3"
#else
#define ROAD_SIGNAL_VERSION "yolo-box-hsv-green-symbols-c-v2"
#endif
enum road_data_type { ROAD_F32, ROAD_I8, ROAD_U8 };
enum road_signal { ROAD_SIGNAL_UNKNOWN, ROAD_SIGNAL_RED, ROAD_SIGNAL_YELLOW, ROAD_SIGNAL_GREEN };
struct road_box { float left, top, right, bottom, score; unsigned class_id; };
struct road_head {
    const void *data;
    size_t bytes;
    unsigned grid;
    enum road_data_type type;
    float scale;
    int zero_point;
};
struct road_signal_history {
    enum road_signal pending, state;
    unsigned confirmations;
    uint64_t timestamp_ns, evidence_ns;
    bool has_timestamp, has_evidence;
};
struct road_object_result {
    struct road_box boxes[ROAD_OBJECT_CAPACITY];
    unsigned lamp_color[ROAD_OBJECT_CAPACITY];
    unsigned count, vehicles, people, lamps;
    enum road_signal signal, raw_signal;
    unsigned signal_confirmations;
    bool signal_conflict, signal_held;
    uint64_t sequence, timestamp_ns, signal_evidence_ns;
};
bool road_object_prepare(const uint8_t *rgb565, unsigned width, unsigned height, uint8_t *rgb);
bool road_object_decode(const struct road_head heads[3], struct road_box *workspace, struct road_object_result *result);
bool road_object_decode_sized(const struct road_head heads[3], struct road_box *workspace, struct road_object_result *result,
                               unsigned source_width, unsigned source_height);
void road_object_vehicles_only(struct road_object_result *result);
void road_object_draw_sized(const struct road_object_result *result, bool usable, const char *status,
                             uint8_t *output, unsigned width, unsigned height,
                             unsigned left, unsigned top, unsigned tile_width, unsigned tile_height,
                             unsigned source_width, unsigned source_height, bool side_view);
void road_signal_update(struct road_signal_history *history, const uint8_t *rgb565, unsigned width, unsigned height,
                         uint64_t timestamp_ns, struct road_object_result *result);
const char *road_object_label(unsigned class_id);
const char *road_signal_name(enum road_signal signal);
void road_object_draw(const struct road_object_result *result, bool usable, const char *status,
                       uint8_t *output, unsigned width, unsigned height,
                       unsigned left, unsigned top, unsigned tile_width, unsigned tile_height);
#endif
