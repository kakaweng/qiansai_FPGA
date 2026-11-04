#ifndef LANE_STYLE_ASYNC_H
#define LANE_STYLE_ASYNC_H

#include "lane_style_core.h"

struct lane_async;
struct lane_async *lane_async_open(const char *runtime, const char *model, const char *contract, unsigned max_age_ms);
void lane_async_close(struct lane_async *lane);
void lane_async_reset(struct lane_async *lane);
bool lane_async_snapshot(struct lane_async *lane, uint64_t now_ns, struct lane_result *result);
bool lane_async_submit(struct lane_async *lane, const uint8_t *source, unsigned width, unsigned height,
                        uint64_t sequence, uint64_t timestamp_ns);
void lane_async_draw(struct lane_async *lane, uint8_t *output, unsigned width, unsigned height,
                      unsigned left, unsigned top, unsigned tile_width, unsigned tile_height,
                      uint64_t video_sequence, uint64_t video_timestamp_ns);

#endif
