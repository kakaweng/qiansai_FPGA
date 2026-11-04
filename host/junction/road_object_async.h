#ifndef ROAD_OBJECT_ASYNC_H
#define ROAD_OBJECT_ASYNC_H
#include "road_object_core.h"
struct road_object_async;
struct road_object_async *road_object_async_open(const char *runtime, const char *model, const char *contract, unsigned max_age_ms);
void road_object_async_close(struct road_object_async *worker);
void road_object_async_reset(struct road_object_async *worker);
/* Multi-source implementation (v6): 0=front, 1=left OV5640, 2=right Ethernet.
 * One model/context; weighted front,left,front,right scheduling. */
bool road_multi_submit(struct road_object_async *worker, unsigned source, const uint8_t *pixels,
                       uint64_t sequence, uint64_t timestamp_ns, uint64_t scene_ms);
bool road_multi_snapshot(struct road_object_async *worker, unsigned source, uint64_t now_ns,
                         struct road_object_result *result, uint64_t *scene_ms);
void road_multi_draw(struct road_object_async *worker, unsigned source, uint8_t *output,
                     unsigned left, unsigned top, uint64_t now_ns);
bool road_object_async_submit(struct road_object_async *worker, const uint8_t *pixels, unsigned width, unsigned height, uint64_t sequence, uint64_t timestamp_ns);
bool road_object_async_snapshot(struct road_object_async *worker, uint64_t now_ns,
                                struct road_object_result *result);
bool road_object_async_display_snapshot(struct road_object_async *worker, uint64_t now_ns,
                                        struct road_object_result *result);
void road_object_async_draw(struct road_object_async *worker, uint8_t *output, unsigned width, unsigned height,
                             unsigned left, unsigned top, unsigned tile_width, unsigned tile_height,
                             uint64_t video_sequence, uint64_t video_timestamp_ns);
#endif
