/* A single YOLO context serves three sources. Each source owns a latest-frame
 * slot and a result; slow inference never queues old video or blocks preview. */
#define _POSIX_C_SOURCE 200809L
#include "road_object_async.h"
#include "road_object_rknn.h"
#include "rknn_scheduler.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
struct road_slot {
    uint8_t *pending_pixels, *processing_pixels;
    uint64_t sequence, timestamp, scene_ms, result_scene;
    bool pending, submitted, valid;
    struct road_object_result result;
};
struct road_object_async {
    struct road_object_engine *engine;
    pthread_mutex_t mutex;
    pthread_cond_t ready;
    pthread_t thread;
    struct road_slot slots[3];
    uint64_t epoch, completed;
    unsigned cursor, max_age_ms, consecutive_front;
    bool stopping, reset_pending;
};
static uint64_t multi_now(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000000000+(uint64_t)t.tv_nsec;
}
static void *multi_worker(void *context)
{
    static const unsigned order[4]={0,1,0,2};
    struct road_object_async *w=context;
    for (;;) {
        unsigned source=3, i;
        uint64_t epoch,sequence,timestamp,scene,queued_ns,selected_ns;
        uint8_t *swap;
        bool reset,success;
        struct road_slot *s;
        struct road_object_result result;
        pthread_mutex_lock(&w->mutex);
        while(!w->stopping && !w->slots[0].pending && !w->slots[1].pending && !w->slots[2].pending)
            pthread_cond_wait(&w->ready,&w->mutex);
        if(w->stopping) { pthread_mutex_unlock(&w->mutex); return NULL; }
        pthread_mutex_unlock(&w->mutex);
        queued_ns=multi_now();
        road_npu_acquire(ROAD_NPU_OBJECT);
        pthread_mutex_lock(&w->mutex);
        /* Choose both source and frame after the grant, so a queued side job
         * cannot pin an old frame or ignore a newly overdue front result. */
        for (i=0;!w->stopping && i<4;i++) {
            unsigned at=(w->cursor+i)%4;
            if(w->slots[order[at]].pending) {
                source=order[at];
                /* Prioritize front evidence when it falls behind, while
                 * forcing a waiting side after at most three front jobs. */
                if(source && w->slots[0].pending && w->slots[0].valid && w->consecutive_front<3 &&
                    multi_now()>w->slots[0].result.timestamp_ns+250000000) source=0;
                else w->cursor=(at+1)%4;
                break;
            }
        }
        if(w->stopping || source==3) {
            bool stop=w->stopping;
            pthread_mutex_unlock(&w->mutex); road_npu_unlock();
            if(stop) return NULL;
            continue;
        }
        selected_ns=multi_now();
        s=&w->slots[source]; swap=s->processing_pixels; s->processing_pixels=s->pending_pixels; s->pending_pixels=swap;
        w->consecutive_front=source ? 0 : w->consecutive_front+1;
        sequence=s->sequence; timestamp=s->timestamp; scene=s->scene_ms; epoch=w->epoch;
        reset=w->reset_pending; w->reset_pending=false; s->pending=false;
        pthread_mutex_unlock(&w->mutex);
        if(reset) road_object_reset(w->engine);
        success=road_object_process_reserved(w->engine,s->processing_pixels,source ? 640 : 1920,source ? 480 : 1080,sequence,timestamp,&result);
        if(success && source) road_object_vehicles_only(&result);
        pthread_mutex_lock(&w->mutex);
        if(epoch==w->epoch) { s->valid=success; if(success) { s->result=result; s->result_scene=scene; } }
        w->completed++;
        if(w->completed<=3 || w->completed%20==0 || !success)
            printf("objects_multi source=%u sequence=%llu success=%u completed=%llu queue_ms=%.2f input_age_ms=%.2f result_age_ms=%.2f\n",source,(unsigned long long)sequence,success,(unsigned long long)w->completed,
                (selected_ns-queued_ns)/1e6,selected_ns>=timestamp ? (selected_ns-timestamp)/1e6 : -1.,
                multi_now()>=timestamp ? (multi_now()-timestamp)/1e6 : -1.);
        pthread_mutex_unlock(&w->mutex);
    }
}
struct road_object_async *road_object_async_open(const char *runtime,const char *model,const char *contract,unsigned age)
{
    struct road_object_async *w;
    unsigned i;
    if(age<50 || age>2000) return NULL;
    w=calloc(1,sizeof(*w)); if(!w) return NULL;
    w->max_age_ms=age; w->engine=road_object_open(runtime,model,contract);
    for(i=0;i<3;i++) {
        size_t bytes=i ? 640U*480*2 : 1920U*1080*2;
        w->slots[i].pending_pixels=malloc(bytes); w->slots[i].processing_pixels=malloc(bytes);
        if(!w->slots[i].pending_pixels || !w->slots[i].processing_pixels) goto failed;
    }
    if(!w->engine || pthread_mutex_init(&w->mutex,NULL)) goto failed;
    if(pthread_cond_init(&w->ready,NULL)) { pthread_mutex_destroy(&w->mutex); goto failed; }
    if(pthread_create(&w->thread,NULL,multi_worker,w)) {
        pthread_cond_destroy(&w->ready); pthread_mutex_destroy(&w->mutex); goto failed;
    }
    printf("Objects multi ready: one YOLO context; front,left,front,right with bounded front priority; latest frame only; max_age_ms=%u; sides=vehicles-only; front_pad=140 side_pad=80\n",age);
    return w;
failed:
    for(i=0;i<3;i++) { free(w->slots[i].pending_pixels); free(w->slots[i].processing_pixels); }
    road_object_close(w->engine); free(w); return NULL;
}
void road_object_async_close(struct road_object_async *w)
{
    unsigned i;
    if(!w) return;
    pthread_mutex_lock(&w->mutex); w->stopping=true; pthread_cond_signal(&w->ready); pthread_mutex_unlock(&w->mutex);
    pthread_join(w->thread,NULL); pthread_cond_destroy(&w->ready); pthread_mutex_destroy(&w->mutex);
    for(i=0;i<3;i++) { free(w->slots[i].pending_pixels); free(w->slots[i].processing_pixels); }
    road_object_close(w->engine); free(w);
}
void road_object_async_reset(struct road_object_async *w)
{
    unsigned i;
    if(!w) return;
    pthread_mutex_lock(&w->mutex); w->epoch++; w->reset_pending=true;
    w->consecutive_front=0;
    for(i=0;i<3;i++) w->slots[i].valid=w->slots[i].pending=w->slots[i].submitted=false;
    pthread_mutex_unlock(&w->mutex);
}
bool road_multi_submit(struct road_object_async *w,unsigned source,const uint8_t *pixels,
                       uint64_t sequence,uint64_t timestamp,uint64_t scene)
{
    struct road_slot *s;
    if(!w || source>2 || !pixels || !timestamp) return false;
    pthread_mutex_lock(&w->mutex); s=&w->slots[source];
    if(w->stopping) { pthread_mutex_unlock(&w->mutex); return false; }
    /* Capturing a frozen source again must not refresh its evidence age. */
    if(s->submitted && sequence==s->sequence) { pthread_mutex_unlock(&w->mutex); return true; }
    if(s->submitted && ((int64_t)(sequence-s->sequence)<0 || timestamp<=s->timestamp)) {
        unsigned i; w->epoch++; w->reset_pending=true;
        for(i=0;i<3;i++) w->slots[i].valid=w->slots[i].pending=w->slots[i].submitted=false;
    }
    memcpy(s->pending_pixels,pixels,source ? 640U*480*2 : 1920U*1080*2);
    s->sequence=sequence; s->timestamp=timestamp; s->scene_ms=scene; s->pending=s->submitted=true;
    pthread_cond_signal(&w->ready); pthread_mutex_unlock(&w->mutex); return true;
}
bool road_object_async_submit(struct road_object_async *w,const uint8_t *pixels,unsigned width,unsigned height,uint64_t sequence,uint64_t timestamp)
{ return width==1920 && height==1080 && road_multi_submit(w,0,pixels,sequence,timestamp,0); }
bool road_multi_snapshot(struct road_object_async *w,unsigned source,uint64_t now,struct road_object_result *result,uint64_t *scene)
{
    bool valid;
    if(!result) return false;
    memset(result,0,sizeof(*result)); if(scene) *scene=0;
    if(!w || source>2) return false;
    pthread_mutex_lock(&w->mutex); valid=w->slots[source].valid;
    if(valid) { *result=w->slots[source].result; if(scene) *scene=w->slots[source].result_scene; }
    pthread_mutex_unlock(&w->mutex);
    if(!valid || result->timestamp_ns>now || now-result->timestamp_ns>(uint64_t)w->max_age_ms*1000000) {
        memset(result,0,sizeof(*result)); if(scene) *scene=0; return false;
    }
    if(result->signal_evidence_ns>now || now-result->signal_evidence_ns>500000000) {
        result->signal=ROAD_SIGNAL_UNKNOWN; result->signal_held=false;
    }
    return true;
}
void road_object_async_draw(struct road_object_async *w,uint8_t *output,unsigned width,unsigned height,
                             unsigned left,unsigned top,unsigned tw,unsigned th,uint64_t seq,uint64_t timestamp)
{
    struct road_object_result result; char status[112];
    uint64_t now=multi_now();
    bool valid=road_multi_snapshot(w,0,now,&result,NULL);
    valid=valid && result.sequence<=seq && result.timestamp_ns<=timestamp;
    snprintf(status,sizeof(status),"OBJ:MULTI %s AGE:%lluMS",valid ? "FRONT" : "WAIT",valid ? (unsigned long long)((now-result.timestamp_ns)/1000000) : 0);
    road_object_draw(&result,valid,status,output,width,height,left,top,tw,th);
}
void road_multi_draw(struct road_object_async *w,unsigned source,uint8_t *output,unsigned left,unsigned top,uint64_t now)
{
    struct road_object_result result; char status[96];
    bool valid=road_multi_snapshot(w,source,now,&result,NULL);
    snprintf(status,sizeof(status),"%s %s AGE:%lluMS",source==1 ? "LEFT" : "RIGHT",valid ? "CAR" : "WAIT",
        valid ? (unsigned long long)((now-result.timestamp_ns)/1000000) : 0);
    road_object_draw_sized(&result,valid,status,output,1280,480,left,top,320,240,640,480,true);
}
