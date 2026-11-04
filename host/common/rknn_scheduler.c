#include "rknn_scheduler.h"
#include <pthread.h>
static pthread_mutex_t npu_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t npu_ready = PTHREAD_COND_INITIALIZER;
static unsigned lane_waiters, object_waiters, crosswalk_waiters, consecutive_lanes;
static enum road_npu_priority next_auxiliary = ROAD_NPU_OBJECT;
static int npu_active;

void road_npu_acquire(enum road_npu_priority priority)
{
    int lane = priority == ROAD_NPU_LANE;
    pthread_mutex_lock(&npu_mutex);
    if (lane) lane_waiters++;
    else if (priority == ROAD_NPU_CROSSWALK) crosswalk_waiters++;
    else object_waiters++;
    while (npu_active || (lane ? (object_waiters || crosswalk_waiters) && consecutive_lanes >= 2
                                      : lane_waiters && consecutive_lanes < 2) ||
           (!lane && object_waiters && crosswalk_waiters && priority != next_auxiliary))
        pthread_cond_wait(&npu_ready, &npu_mutex);
    if (lane) {
        lane_waiters--;
        if (consecutive_lanes < 2) consecutive_lanes++;
    } else {
        if (priority == ROAD_NPU_CROSSWALK) { crosswalk_waiters--; next_auxiliary = ROAD_NPU_OBJECT; }
        else { object_waiters--; next_auxiliary = ROAD_NPU_CROSSWALK; }
        consecutive_lanes = 0;
    }
    npu_active = 1;
    pthread_mutex_unlock(&npu_mutex);
}
void road_npu_lock(void) { road_npu_acquire(ROAD_NPU_OBJECT); }
void road_npu_unlock(void)
{
    pthread_mutex_lock(&npu_mutex);
    npu_active = 0;
    pthread_cond_broadcast(&npu_ready);
    pthread_mutex_unlock(&npu_mutex);
}
