#ifndef LANE_STYLE_RKNN_H
#define LANE_STYLE_RKNN_H

#include "lane_style_core.h"

struct lane_rknn;
struct lane_rknn *lane_rknn_open(const char *runtime, const char *model, const char *contract);
void lane_rknn_close(struct lane_rknn *lane);
void lane_rknn_reset(struct lane_rknn *lane);
bool lane_rknn_process(struct lane_rknn *lane, const uint8_t *source, unsigned width, unsigned height,
                       uint64_t sequence, uint64_t timestamp_ns);
/* Consumes a road_npu_acquire turn; releases it before CPU decode or on error. */
bool lane_rknn_process_reserved(struct lane_rknn *lane, const uint8_t *source, unsigned width, unsigned height,
                                uint64_t sequence, uint64_t timestamp_ns);
void lane_rknn_draw(struct lane_rknn *lane, uint8_t *output, unsigned width, unsigned height,
                    unsigned left, unsigned top, unsigned tile_width, unsigned tile_height);
bool lane_rknn_snapshot(const struct lane_rknn *lane, struct lane_style_view *view);

#endif
