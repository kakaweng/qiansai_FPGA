#define _POSIX_C_SOURCE 200809L
#include "lane_style_async.h"
#include "lane_style_rknn.h"
#if defined(HAVE_MULTI_SCENE) || defined(HAVE_FRONT_P1)
#include "rknn_scheduler.h"
#endif
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define LANE_SOURCE_CAPACITY (1920U * 1080U * 2U)

struct lane_async {
    struct lane_rknn *engine;
    pthread_mutex_t mutex;
    pthread_cond_t ready;
    pthread_t worker;
    uint8_t *pending_pixels, *processing_pixels;
    unsigned pending_width, pending_height, max_age_ms;
    uint64_t pending_sequence, pending_timestamp_ns, submitted_sequence, submitted_timestamp_ns;
    uint64_t replaced, completed;
    uint64_t epoch;
    bool reset_pending;
    struct lane_style_view published, drawing;
    bool stopping, pending, has_result, failed, submitted;
};

static uint64_t async_now_ns(void)
{
    struct timespec timestamp;
    if (clock_gettime(CLOCK_MONOTONIC, &timestamp)) return 0;
    return (uint64_t)timestamp.tv_sec * UINT64_C(1000000000) + (uint64_t)timestamp.tv_nsec;
}

static void *lane_worker(void *context)
{
    struct lane_async *lane = context;
    for (;;) {
        unsigned width, height;
        uint64_t sequence, timestamp_ns, epoch;
        uint64_t queued_ns = 0, selected_ns;
        bool success, reset;
        uint8_t *swap;
        pthread_mutex_lock(&lane->mutex);
        while (!lane->stopping && !lane->pending) pthread_cond_wait(&lane->ready, &lane->mutex);
        if (lane->stopping) { pthread_mutex_unlock(&lane->mutex); return NULL; }
#if defined(HAVE_MULTI_SCENE) || defined(HAVE_FRONT_P1)
        pthread_mutex_unlock(&lane->mutex);
        queued_ns = async_now_ns();
        road_npu_acquire(ROAD_NPU_LANE);
        pthread_mutex_lock(&lane->mutex);
        /* Reset/stop may have removed the frame while the NPU was busy. */
        if (lane->stopping || !lane->pending) {
            bool stop = lane->stopping;
            pthread_mutex_unlock(&lane->mutex); road_npu_unlock();
            if (stop) return NULL;
            continue;
        }
#endif
        selected_ns = async_now_ns();
        if (!queued_ns) queued_ns = selected_ns;
        swap = lane->processing_pixels; lane->processing_pixels = lane->pending_pixels; lane->pending_pixels = swap;
        width = lane->pending_width; height = lane->pending_height;
        sequence = lane->pending_sequence; timestamp_ns = lane->pending_timestamp_ns;
        epoch = lane->epoch; reset = lane->reset_pending; lane->reset_pending = false;
        lane->pending = false;
        pthread_mutex_unlock(&lane->mutex);
        if (reset) lane_rknn_reset(lane->engine);
#if defined(HAVE_MULTI_SCENE) || defined(HAVE_FRONT_P1)
        success = lane_rknn_process_reserved(lane->engine, lane->processing_pixels, width, height, sequence, timestamp_ns);
#else
        success = lane_rknn_process(lane->engine, lane->processing_pixels, width, height, sequence, timestamp_ns);
#endif
        pthread_mutex_lock(&lane->mutex);
        if (epoch == lane->epoch) {
            lane->has_result = success && lane_rknn_snapshot(lane->engine, &lane->published);
            lane->failed = !lane->has_result;
        }
        lane->completed++;
        if (lane->completed <= 3 || lane->completed % 10 == 0)
            printf("lane_async completed=%" PRIu64 " result_hdmi_seq=%" PRIu64 " replaced_pending=%" PRIu64 " success=%u queue_ms=%.2f input_age_ms=%.2f result_age_ms=%.2f\n",
                   lane->completed, sequence, lane->replaced, lane->has_result, (selected_ns-queued_ns)/1e6,
                   selected_ns >= timestamp_ns ? (selected_ns-timestamp_ns)/1e6 : -1.,
                   async_now_ns() >= timestamp_ns ? (async_now_ns()-timestamp_ns)/1e6 : -1.);
        pthread_mutex_unlock(&lane->mutex);
    }
}

struct lane_async *lane_async_open(const char *runtime, const char *model, const char *contract, unsigned max_age_ms)
{
    struct lane_async *lane;
    if (max_age_ms < 50 || max_age_ms > 2000) return NULL;
    lane = calloc(1, sizeof(*lane));
    if (!lane) return NULL;
    lane->engine = lane_rknn_open(runtime, model, contract);
    lane->pending_pixels = malloc(LANE_SOURCE_CAPACITY); lane->processing_pixels = malloc(LANE_SOURCE_CAPACITY);
    lane->max_age_ms = max_age_ms;
    if (!lane->engine || !lane->pending_pixels || !lane->processing_pixels) goto failed;
    if (pthread_mutex_init(&lane->mutex, NULL)) goto failed;
    if (pthread_cond_init(&lane->ready, NULL)) { pthread_mutex_destroy(&lane->mutex); goto failed; }
    if (pthread_create(&lane->worker, NULL, lane_worker, lane)) {
        pthread_cond_destroy(&lane->ready); pthread_mutex_destroy(&lane->mutex); goto failed;
    }
    printf("Lane async ready: latest pending frame only; max_result_age_ms=%u\n", max_age_ms);
#if defined(HAVE_MULTI_SCENE) || defined(HAVE_FRONT_P1)
    puts("Lane scheduling: lane-priority-v1; reserve before newest frame; bounded object fairness; original expiry limits");
#endif
    return lane;
failed:
    lane_rknn_close(lane->engine); free(lane->pending_pixels); free(lane->processing_pixels); free(lane); return NULL;
}

void lane_async_close(struct lane_async *lane)
{
    if (!lane) return;
    pthread_mutex_lock(&lane->mutex); lane->stopping = true; pthread_cond_signal(&lane->ready); pthread_mutex_unlock(&lane->mutex);
    pthread_join(lane->worker, NULL);
    pthread_cond_destroy(&lane->ready); pthread_mutex_destroy(&lane->mutex);
    lane_rknn_close(lane->engine); free(lane->pending_pixels); free(lane->processing_pixels); free(lane);
}

void lane_async_reset(struct lane_async *lane)
{
    if (!lane) return;
    pthread_mutex_lock(&lane->mutex);
    lane->epoch++; lane->reset_pending = true;
    lane->has_result = lane->failed = lane->pending = lane->submitted = false;
    pthread_mutex_unlock(&lane->mutex);
}

bool lane_async_submit(struct lane_async *lane, const uint8_t *source, unsigned width, unsigned height,
                        uint64_t sequence, uint64_t timestamp_ns)
{
    size_t bytes = (size_t)width * height * 2;
    if (!lane || !source || width < 4 || height < 4 || width > 1920 || height > 1080 || bytes > LANE_SOURCE_CAPACITY) return false;
    pthread_mutex_lock(&lane->mutex);
    if (lane->stopping) { pthread_mutex_unlock(&lane->mutex); return false; }
    if (lane->submitted && sequence == lane->submitted_sequence && timestamp_ns == lane->submitted_timestamp_ns) {
        pthread_mutex_unlock(&lane->mutex); return true;
    }
    if (lane->submitted && (sequence < lane->submitted_sequence || timestamp_ns <= lane->submitted_timestamp_ns ||
        timestamp_ns-lane->submitted_timestamp_ns>UINT64_C(500000000))) {
        lane->has_result=false;lane->epoch++;lane->reset_pending=true;
    }
    memcpy(lane->pending_pixels, source, bytes);
    lane->pending_width = width; lane->pending_height = height;
    lane->pending_sequence = sequence; lane->pending_timestamp_ns = timestamp_ns;
    lane->submitted_sequence = sequence; lane->submitted_timestamp_ns = timestamp_ns; lane->submitted = true;
    if (lane->pending) lane->replaced++;
    lane->pending = true; pthread_cond_signal(&lane->ready); pthread_mutex_unlock(&lane->mutex);
    return true;
}

bool lane_async_snapshot(struct lane_async *lane, uint64_t now, struct lane_result *result)
{
    bool valid;
    unsigned side;
    uint64_t age;
    if (!lane || !result) return false;
    memset(result,0,sizeof(*result));
    pthread_mutex_lock(&lane->mutex);
    valid=lane->has_result;
    if (valid) *result=lane->published.result;
    pthread_mutex_unlock(&lane->mutex);
    if (!valid || result->timestamp_ns>now || now-result->timestamp_ns>(uint64_t)lane->max_age_ms*1000000) {
        memset(result,0,sizeof(*result)); return false;
    }
    age=now-result->timestamp_ns;
    for(side=0;side<2;side++) if(result->sides[side].held && result->sides[side].age_ms+age/1e6>LANE_CONFIRMED_HOLD_MS) {
        result->sides[side].type=LANE_UNKNOWN; result->sides[side].held=false;
        result->sides[side].reason=LANE_REASON_EXPIRED;
    }
    return true;
}
void lane_async_draw(struct lane_async *lane, uint8_t *output, unsigned width, unsigned height,
                      unsigned left, unsigned top, unsigned tile_width, unsigned tile_height,
                      uint64_t video_sequence, uint64_t video_timestamp_ns)
{
    bool has_result, failed, usable;
    uint64_t age_ns = 0, now = async_now_ns();
    unsigned side;
    char status[112];
    if (!lane) return;
    pthread_mutex_lock(&lane->mutex);
    has_result = lane->has_result; failed = lane->failed;
    if (has_result) lane->drawing = lane->published;
    pthread_mutex_unlock(&lane->mutex);
    usable = has_result && lane->drawing.result.sequence <= video_sequence && lane->drawing.result.timestamp_ns <= video_timestamp_ns && lane->drawing.result.timestamp_ns <= now;
    if (usable) {
        age_ns = now - lane->drawing.result.timestamp_ns;
        if (video_timestamp_ns - lane->drawing.result.timestamp_ns > age_ns) age_ns = video_timestamp_ns - lane->drawing.result.timestamp_ns;
        usable = age_ns <= (uint64_t)lane->max_age_ms * UINT64_C(1000000);
    }
    if (usable) {
        for (side = 0; side < 2; side++) if (lane->drawing.result.sides[side].held && lane->drawing.result.sides[side].age_ms + age_ns / 1e6 > LANE_CONFIRMED_HOLD_MS) {
            lane->drawing.result.sides[side].type = LANE_UNKNOWN; lane->drawing.result.sides[side].held = false;
            lane->drawing.result.sides[side].reason = LANE_REASON_EXPIRED;
        }
        snprintf(status, sizeof(status), "AI:ASYNC AGE:%" PRIu64 "MS FRAME:%" PRIu64, age_ns / UINT64_C(1000000), lane->drawing.result.sequence);
    } else {
        memset(&lane->drawing, 0, sizeof(lane->drawing));
        lane->drawing.width = tile_width; lane->drawing.height = tile_height;
        lane->drawing.result.sides[0].boundary_index = lane->drawing.result.sides[1].boundary_index = -1;
        for (side = 0; side < 2; side++) lane->drawing.result.sides[side].reason = failed ? LANE_REASON_ERROR : has_result ? LANE_REASON_STALE : LANE_REASON_WAIT;
        snprintf(status, sizeof(status), "AI:ASYNC %s", failed ? "ERROR" : has_result ? "STALE" : "WAIT");
    }
    lane_style_draw_view_rgb565(&lane->drawing, output, width, height, left, top, tile_width, tile_height);
#ifdef HAVE_FRONT_P1
    /* Diagnostics use the black margin, not the signal-light region. */
    if (left == 213 && top == 0 && tile_width == 853 && tile_height == 480) {
        lane_style_draw_status_rgb565(output, width, height, 0, 60, 210, 480-60, status);
        return;
    }
#endif
    lane_style_draw_status_rgb565(output, width, height, left, top, tile_width, tile_height, status);
    lane_style_draw_reasons_rgb565(&lane->drawing.result, output, width, height, left, top, tile_width, tile_height);
}
