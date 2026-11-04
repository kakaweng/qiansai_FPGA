#define _POSIX_C_SOURCE 200809L
#include "road_object_async.h"
#include "road_object_rknn.h"
#ifdef HAVE_FRONT_P1
#include "rknn_scheduler.h"
#endif
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define OBJECT_SOURCE_BYTES (1920U * 1080U * 2U)
struct road_object_async {
    struct road_object_engine *engine;
    pthread_t thread;
    pthread_mutex_t mutex;
    pthread_cond_t ready;
    uint8_t *pending_pixels, *processing_pixels;
    uint64_t sequence, timestamp_ns, epoch, completed, replaced;
    unsigned max_age_ms;
    bool stopping, pending, submitted, valid, failed, reset_pending;
    struct road_object_result published;
};
static uint64_t object_async_now(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*UINT64_C(1000000000)+t.tv_nsec;
}
static void *object_worker(void *context)
{
    struct road_object_async *worker=context;
    for (;;) {
        struct road_object_result result;
        uint8_t *swap;
        uint64_t sequence,timestamp,epoch,queued,selected;
        bool success,reset;
        pthread_mutex_lock(&worker->mutex);
        while (!worker->pending && !worker->stopping) pthread_cond_wait(&worker->ready,&worker->mutex);
        if (worker->stopping) { pthread_mutex_unlock(&worker->mutex); return NULL; }
        queued=object_async_now();
#ifdef HAVE_FRONT_P1
        pthread_mutex_unlock(&worker->mutex);
        road_npu_acquire(ROAD_NPU_OBJECT);
        pthread_mutex_lock(&worker->mutex);
        if(worker->stopping || !worker->pending) {
            bool stop=worker->stopping;
            pthread_mutex_unlock(&worker->mutex);road_npu_unlock();
            if(stop)return NULL;
            continue;
        }
#endif
        selected=object_async_now();
        swap=worker->processing_pixels; worker->processing_pixels=worker->pending_pixels; worker->pending_pixels=swap;
        sequence=worker->sequence; timestamp=worker->timestamp_ns; epoch=worker->epoch;
        reset=worker->reset_pending; worker->reset_pending=false;
        worker->pending=false; pthread_mutex_unlock(&worker->mutex);
        if(reset) road_object_reset(worker->engine);
#ifdef HAVE_FRONT_P1
        success=road_object_process_reserved(worker->engine,worker->processing_pixels,1920,1080,sequence,timestamp,&result);
#else
        success=road_object_process(worker->engine,worker->processing_pixels,1920,1080,sequence,timestamp,&result);
#endif
        pthread_mutex_lock(&worker->mutex);
        if (epoch==worker->epoch) {
            worker->valid=success; worker->failed=!success;
            if (success) worker->published=result;
        }
        worker->completed++;
        if (worker->completed<=3 || worker->completed%10==0 || !success)
            printf("objects_async completed=%" PRIu64 " result_hdmi_seq=%" PRIu64 " replaced_pending=%" PRIu64 " success=%u queue_ms=%.2f input_age_ms=%.2f result_age_ms=%.2f\n",worker->completed,sequence,worker->replaced,success,
                   (selected-queued)/1e6,selected>=timestamp?(selected-timestamp)/1e6:-1.,
                   object_async_now()>=timestamp?(object_async_now()-timestamp)/1e6:-1.);
        pthread_mutex_unlock(&worker->mutex);
    }
}
struct road_object_async *road_object_async_open(const char *runtime, const char *model, const char *contract, unsigned max_age_ms)
{
    struct road_object_async *worker;
    if (max_age_ms<50 || max_age_ms>2000) return NULL;
    worker=calloc(1,sizeof(*worker));
    if (!worker) return NULL;
    worker->engine=road_object_open(runtime,model,contract);
    worker->pending_pixels=malloc(OBJECT_SOURCE_BYTES); worker->processing_pixels=malloc(OBJECT_SOURCE_BYTES); worker->max_age_ms=max_age_ms;
    if (!worker->engine || !worker->pending_pixels || !worker->processing_pixels) goto failed;
    if (pthread_mutex_init(&worker->mutex,NULL)) goto failed;
    if (pthread_cond_init(&worker->ready,NULL)) { pthread_mutex_destroy(&worker->mutex); goto failed; }
    if (pthread_create(&worker->thread,NULL,object_worker,worker)) { pthread_cond_destroy(&worker->ready); pthread_mutex_destroy(&worker->mutex); goto failed; }
    printf("Objects async ready: HDMI only; latest pending frame only; max_result_age_ms=%u signal_evidence_timeout_ms=500\n",max_age_ms);
#ifdef HAVE_FRONT_P1
    puts("Objects scheduling: reserve-before-latest-v2; queue delay excluded from source age; display_hold_ms=1500");
#endif
    return worker;
failed:
    road_object_close(worker->engine); free(worker->pending_pixels); free(worker->processing_pixels); free(worker); return NULL;
}
void road_object_async_close(struct road_object_async *worker)
{
    if (!worker) return;
    pthread_mutex_lock(&worker->mutex); worker->stopping=true; pthread_cond_signal(&worker->ready); pthread_mutex_unlock(&worker->mutex);
    pthread_join(worker->thread,NULL); pthread_cond_destroy(&worker->ready); pthread_mutex_destroy(&worker->mutex);
    road_object_close(worker->engine); free(worker->pending_pixels); free(worker->processing_pixels); free(worker);
}
void road_object_async_reset(struct road_object_async *worker)
{
    if(!worker) return;
    pthread_mutex_lock(&worker->mutex);
    worker->epoch++; worker->reset_pending=true;
    worker->valid=worker->failed=worker->pending=worker->submitted=false;
    pthread_mutex_unlock(&worker->mutex);
}
bool road_object_async_submit(struct road_object_async *worker, const uint8_t *pixels, unsigned width, unsigned height, uint64_t sequence, uint64_t timestamp)
{
    if (!worker || !pixels || width!=1920 || height!=1080) return false;
    pthread_mutex_lock(&worker->mutex);
    if (worker->stopping) { pthread_mutex_unlock(&worker->mutex); return false; }
    if (worker->submitted && sequence==worker->sequence && timestamp==worker->timestamp_ns) { pthread_mutex_unlock(&worker->mutex); return true; }
    if (worker->submitted && (sequence<=worker->sequence || timestamp<=worker->timestamp_ns)) {
        worker->epoch++; worker->valid=false;
    }
    memcpy(worker->pending_pixels,pixels,OBJECT_SOURCE_BYTES);
    worker->sequence=sequence; worker->timestamp_ns=timestamp; worker->submitted=true;
    if (worker->pending) worker->replaced++;
    worker->pending=true; pthread_cond_signal(&worker->ready); pthread_mutex_unlock(&worker->mutex); return true;
}
void road_object_async_draw(struct road_object_async *worker, uint8_t *output, unsigned width, unsigned height,
                             unsigned left, unsigned top, unsigned tile_width, unsigned tile_height,
                             uint64_t video_sequence, uint64_t video_timestamp)
{
    struct road_object_result result={0};
    uint64_t age=0,now=object_async_now();
    bool valid,failed,usable;
    unsigned display_age_ms=worker?worker->max_age_ms:0;
    char status[112];
    if (!worker) return;
#ifdef HAVE_FRONT_P1
    display_age_ms=1500; /* Drawing only; decision snapshots retain max_age_ms. */
#endif
    pthread_mutex_lock(&worker->mutex); valid=worker->valid; failed=worker->failed;
    if (valid) result=worker->published;
    pthread_mutex_unlock(&worker->mutex);
    usable=valid && result.sequence<=video_sequence && result.timestamp_ns<=video_timestamp && result.timestamp_ns<=now;
    if (usable) {
        age=now-result.timestamp_ns;
        if (video_timestamp-result.timestamp_ns>age) age=video_timestamp-result.timestamp_ns;
        usable=age<=(uint64_t)display_age_ms*UINT64_C(1000000);
    }
    if (usable) {
        if (result.signal!=ROAD_SIGNAL_UNKNOWN && (result.signal_evidence_ns>now || now-result.signal_evidence_ns>UINT64_C(500000000))) {
            result.signal=ROAD_SIGNAL_UNKNOWN; result.signal_held=false;
        }
        snprintf(status,sizeof(status),"OBJ:%s AGE:%" PRIu64 "MS FRAME:%" PRIu64,
                 age>(uint64_t)worker->max_age_ms*UINT64_C(1000000)?"HOLD":"ASYNC",age/UINT64_C(1000000),result.sequence);
    } else snprintf(status,sizeof(status),"OBJ:ASYNC %s",failed ? "ERROR" : valid ? "STALE" : "WAIT");
    road_object_draw(&result,usable,status,output,width,height,left,top,tile_width,tile_height);
}

static bool object_snapshot_age(struct road_object_async *worker, uint64_t now,
                                struct road_object_result *result, unsigned maximum_ms)
{
    bool valid;
    if (!worker || !result) return false;
    memset(result, 0, sizeof(*result));
    pthread_mutex_lock(&worker->mutex);
    valid = worker->valid;
    if (valid) *result = worker->published;
    pthread_mutex_unlock(&worker->mutex);
    if (!valid || result->timestamp_ns > now || now - result->timestamp_ns >
        (uint64_t)maximum_ms * UINT64_C(1000000)) {
        memset(result, 0, sizeof(*result));
        return false;
    }
    if (result->signal_evidence_ns > now || now - result->signal_evidence_ns > UINT64_C(500000000)) {
        result->signal = ROAD_SIGNAL_UNKNOWN;
        result->signal_held = false;
    }
    return true;
}

bool road_object_async_snapshot(struct road_object_async *worker, uint64_t now, struct road_object_result *result)
{ return object_snapshot_age(worker,now,result,worker?worker->max_age_ms:0); }

bool road_object_async_display_snapshot(struct road_object_async *worker, uint64_t now, struct road_object_result *result)
{
    bool valid=object_snapshot_age(worker,now,result,2500);
    if(valid) { result->signal=ROAD_SIGNAL_UNKNOWN;result->signal_held=false; }
    return valid;
}
