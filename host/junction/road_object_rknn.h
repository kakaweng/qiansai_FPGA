#ifndef ROAD_OBJECT_RKNN_H
#define ROAD_OBJECT_RKNN_H
#include "road_object_core.h"
struct road_object_engine;
struct road_object_engine *road_object_open(const char *runtime, const char *model, const char *contract);
void road_object_close(struct road_object_engine *engine);
void road_object_reset(struct road_object_engine *engine);
bool road_object_process(struct road_object_engine *engine, const uint8_t *source, unsigned width, unsigned height,
                          uint64_t sequence, uint64_t timestamp_ns, struct road_object_result *result);
/* Consumes a road_npu_acquire turn, including invalid input / prepare failure. */
bool road_object_process_reserved(struct road_object_engine *engine, const uint8_t *source, unsigned width, unsigned height,
                                   uint64_t sequence, uint64_t timestamp_ns, struct road_object_result *result);
#endif
