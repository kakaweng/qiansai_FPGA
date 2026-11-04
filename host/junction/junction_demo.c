#define _POSIX_C_SOURCE 200809L
#include "junction_demo.h"
#include "junction_demo_core.h"
#include "road_object_rknn.h"
#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <poll.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

struct jd_slot {
    uint8_t *pending_pixels, *working_pixels;
    uint64_t sequence, timestamp;
    bool pending, submitted;
};
struct junction_demo {
    struct road_object_engine *model;
    struct jd_state state;
    struct jd_slot slots[2];
    pthread_mutex_t mutex;
    pthread_cond_t ready;
    pthread_t worker, control;
    bool stopping;
    unsigned cursor;
    uint64_t producer, epoch, completed[2];
    int listener;
};
static uint64_t jd_now(void)
{
    struct timespec stamp;
    clock_gettime(CLOCK_MONOTONIC, &stamp);
    return (uint64_t)stamp.tv_sec * 1000 + (uint64_t)stamp.tv_nsec / 1000000;
}
static void *jd_worker(void *context)
{
    struct junction_demo *demo = context;
    for (;;) {
        struct road_object_result result = {0};
        struct jd_slot *slot;
        uint8_t *swap;
        unsigned source, width, height;
        uint64_t sequence, timestamp, epoch, started;
        bool success;
        pthread_mutex_lock(&demo->mutex);
        while (!demo->stopping && !demo->slots[0].pending && !demo->slots[1].pending)
            pthread_cond_wait(&demo->ready, &demo->mutex);
        if (demo->stopping) { pthread_mutex_unlock(&demo->mutex); return NULL; }
        source = demo->slots[demo->cursor].pending ? demo->cursor : 1 - demo->cursor;
        demo->cursor = 1 - source;
        slot = &demo->slots[source];
        swap = slot->working_pixels; slot->working_pixels = slot->pending_pixels; slot->pending_pixels = swap;
        sequence = slot->sequence; timestamp = slot->timestamp; epoch = demo->epoch; slot->pending = false;
        pthread_mutex_unlock(&demo->mutex);
        width = source ? 640 : 1920; height = source ? 480 : 1080;
        started = jd_now();
        success = road_object_process(demo->model, slot->working_pixels, width, height, sequence, timestamp, &result);
        pthread_mutex_lock(&demo->mutex);
        if (epoch == demo->epoch) {
            jd_observe(&demo->state, source, success ? &result : NULL, slot->working_pixels, width, height, jd_now());
            demo->completed[source]++;
            if (demo->completed[source] <= 3 || demo->completed[source] % 10 == 0 || !success)
                printf("junction_live source=%s sequence=%" PRIu64 " success=%u inference_ms=%" PRIu64 " people=%u contacts=%u signal=%u phase=%s led=%u\n",
                    source ? "ETH_LEFT" : "HDMI_FRONT", sequence, success, jd_now() - started, result.people,
                    demo->state.sources[source].risk_count, demo->state.signal, demo->state.phase, demo->state.led_mode);
        }
        pthread_mutex_unlock(&demo->mutex);
    }
}

static void jd_reply(struct junction_demo *demo, int client, const char *command)
{
    struct jd_state state;
    char response[24000];
    size_t used;
    unsigned source, index;
    uint64_t now = jd_now(), producer, epoch;
    bool accepted = true;
    pthread_mutex_lock(&demo->mutex);
    jd_tick(&demo->state, now);
    if (strcmp(command, "state")) {
        accepted = jd_action(&demo->state, command, now);
        if (accepted && !strcmp(command, "reset")) {
            demo->epoch++;
            for (source = 0; source < 2; source++) demo->slots[source].pending = demo->slots[source].submitted = false;
        }
        printf("JUNCTION_ACTION action=%s accepted=%u phase=%s led=%u\n", command, accepted, demo->state.phase, demo->state.led_mode);
    }
    state = demo->state; producer = demo->producer; epoch = demo->epoch;
    pthread_mutex_unlock(&demo->mutex);
    used = (size_t)snprintf(response, sizeof(response),
        "{\"version\":1,\"profile\":\"waymo-119873-live-v1\",\"producer\":%" PRIu64 ",\"epoch\":%" PRIu64 ",\"now_ms\":%" PRIu64 ",\"accepted\":%s,\"direction\":%u,\"signal\":%u,\"phase\":\"%s\",\"led\":%u,\"speed\":%.2f,\"distance\":%.3f,\"alarm\":%s,\"armed\":%s,\"sources\":[",
        producer, epoch, now, accepted ? "true" : "false", state.direction, state.signal, state.phase, state.led_mode,
        state.speed, state.distance, state.led_mode == 6 ? "true" : "false", state.armed ? "true" : "false");
    for (source = 0; source < 2; source++) {
        const struct jd_observation *observation = &state.sources[source];
        uint64_t stamp = observation->objects.timestamp_ns / 1000000;
        bool fresh = observation->valid && now >= stamp && now - stamp <= 1800;
        used += (size_t)snprintf(response + used, sizeof(response) - used,
            "%s{\"name\":\"%s\",\"sequence\":%" PRIu64 ",\"timestamp_ms\":%" PRIu64 ",\"age_ms\":%" PRIu64 ",\"valid\":%s,\"risk\":%s,\"width\":%u,\"height\":%u,\"boxes\":[",
            source ? "," : "", source ? "ETH_LEFT" : "HDMI_FRONT", observation->sequence, stamp,
            now >= stamp ? now - stamp : 0, fresh ? "true" : "false", fresh && observation->risk_until > now ? "true" : "false",
            source ? 640 : 1920, source ? 480 : 1080);
        for (index = 0; fresh && index < observation->objects.count && index < JD_MAX_BOXES; index++) {
            const struct road_box *box = &observation->objects.boxes[index];
            used += (size_t)snprintf(response + used, sizeof(response) - used,
                "%s[%u,%.4f,%.2f,%.2f,%.2f,%.2f,%u]", index ? "," : "", box->class_id, box->score,
                box->left, box->top, box->right, box->bottom, observation->contacts[index]);
        }
        used += (size_t)snprintf(response + used, sizeof(response) - used, "]}");
    }
    used += (size_t)snprintf(response + used, sizeof(response) - used, "]}\n");
    for (size_t sent = 0; sent < used;) {
        ssize_t count = send(client, response + sent, used - sent, MSG_NOSIGNAL);
        if (count <= 0) break;
        sent += (size_t)count;
    }
}
static void *jd_control(void *context)
{
    struct junction_demo *demo = context;
    struct pollfd descriptor = {demo->listener, POLLIN, 0};
    for (;;) {
        bool stopping;
        pthread_mutex_lock(&demo->mutex); stopping = demo->stopping; pthread_mutex_unlock(&demo->mutex);
        if (stopping) return NULL;
        if (poll(&descriptor, 1, 100) <= 0) continue;
        int client = accept(demo->listener, NULL, NULL);
        if (client < 0) continue;
        struct timeval timeout = {0, 250000};
        char command[32] = {0};
        size_t used = 0;
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        uint64_t deadline = jd_now() + 500;
        while (used < sizeof(command) - 1 && jd_now() < deadline) {
            ssize_t count = recv(client, command + used, 1, 0);
            if (count <= 0) break;
            if (command[used] == '\n') { command[used] = 0; jd_reply(demo, client, command); break; }
            used++;
        }
        close(client);
    }
}
struct junction_demo *junction_demo_open(const char *runtime, const char *model, const char *contract)
{
    struct junction_demo *demo = calloc(1, sizeof(*demo));
    struct sockaddr_in address = {0};
    int enabled = 1;
    unsigned source;
    if (!demo) return NULL;
    demo->listener = -1;
    demo->model = road_object_open(runtime, model, contract);
    if (!demo->model) goto failed;
    for (source = 0; source < 2; source++) {
        size_t bytes = source ? 640U * 480 * 2 : 1920U * 1080 * 2;
        demo->slots[source].pending_pixels = malloc(bytes);
        demo->slots[source].working_pixels = malloc(bytes);
        if (!demo->slots[source].pending_pixels || !demo->slots[source].working_pixels) goto failed;
    }
    demo->listener = socket(AF_INET, SOCK_STREAM, 0);
    if (demo->listener < 0) goto failed;
    setsockopt(demo->listener, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    address.sin_family = AF_INET; address.sin_port = htons(9020); address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(demo->listener, (struct sockaddr *)&address, sizeof(address)) || listen(demo->listener, 8)) goto failed;
    if (pthread_mutex_init(&demo->mutex, NULL)) goto failed;
    if (pthread_cond_init(&demo->ready, NULL)) { pthread_mutex_destroy(&demo->mutex); goto failed; }
    jd_init(&demo->state);
    demo->producer = jd_now() * 1000 + (unsigned)getpid();
    if (pthread_create(&demo->worker, NULL, jd_worker, demo)) {
        pthread_cond_destroy(&demo->ready); pthread_mutex_destroy(&demo->mutex); goto failed;
    }
    if (pthread_create(&demo->control, NULL, jd_control, demo)) {
        pthread_mutex_lock(&demo->mutex); demo->stopping = true; pthread_cond_signal(&demo->ready); pthread_mutex_unlock(&demo->mutex);
        pthread_join(demo->worker, NULL); pthread_cond_destroy(&demo->ready); pthread_mutex_destroy(&demo->mutex); goto failed;
    }
    printf("JUNCTION_LIVE_READY port=9020 sources=HDMI_FRONT,ETH_LEFT camera=inference_off latest_frame_only=1\n");
    return demo;
failed:
    if (demo->listener >= 0) close(demo->listener);
    for (source = 0; source < 2; source++) { free(demo->slots[source].pending_pixels); free(demo->slots[source].working_pixels); }
    road_object_close(demo->model); free(demo); return NULL;
}
void junction_demo_close(struct junction_demo *demo)
{
    if (!demo) return;
    pthread_mutex_lock(&demo->mutex); demo->stopping = true; pthread_cond_signal(&demo->ready); pthread_mutex_unlock(&demo->mutex);
    pthread_join(demo->worker, NULL); pthread_join(demo->control, NULL);
    close(demo->listener); pthread_cond_destroy(&demo->ready); pthread_mutex_destroy(&demo->mutex);
    for (unsigned source = 0; source < 2; source++) { free(demo->slots[source].pending_pixels); free(demo->slots[source].working_pixels); }
    road_object_close(demo->model); free(demo);
}
bool junction_demo_submit(struct junction_demo *demo, unsigned source, const uint8_t *pixels,
                          uint64_t sequence, uint64_t timestamp)
{
    if (!demo || source > 1 || !pixels || !timestamp) return false;
    pthread_mutex_lock(&demo->mutex);
    struct jd_slot *slot = &demo->slots[source];
    if (demo->stopping) { pthread_mutex_unlock(&demo->mutex); return false; }
    if (slot->submitted && sequence == slot->sequence) { pthread_mutex_unlock(&demo->mutex); return true; }
    memcpy(slot->pending_pixels, pixels, source ? 640U * 480 * 2 : 1920U * 1080 * 2);
    slot->sequence = sequence; slot->timestamp = timestamp; slot->pending = slot->submitted = true;
    pthread_cond_signal(&demo->ready); pthread_mutex_unlock(&demo->mutex); return true;
}
