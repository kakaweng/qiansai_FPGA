#ifndef RKNN_SCHEDULER_H
#define RKNN_SCHEDULER_H
/* v6 workers reserve a turn BEFORE taking their newest pending frame. A
 * reserved process call consumes the turn, including on preprocessing failure.
 * Never wait here while holding a worker's frame mutex. */
enum road_npu_priority { ROAD_NPU_OBJECT, ROAD_NPU_LANE, ROAD_NPU_CROSSWALK };
void road_npu_acquire(enum road_npu_priority priority);
/* Compatibility entry point for the original synchronous/v4/v5 engines. */
void road_npu_lock(void);
void road_npu_unlock(void);
#endif
