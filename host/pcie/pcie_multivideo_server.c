#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <inttypes.h>
#include <netinet/tcp.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include "pcie_multivideo_protocol.h"
#include "video_mosaic.h"
#ifdef HAVE_JUNCTION_DEMO
#include "junction_demo.h"
#include "road_object_rknn.h"
#include <sys/prctl.h>
#endif
#ifdef HAVE_SYNC_SESSION
#include "sync_capture.h"
#endif

#ifdef HAVE_LANE_STYLE
#include "lane_style_rknn.h"
#include "lane_style_async.h"
#ifdef HAVE_ROAD_OBJECTS
#include "road_object_async.h"
#include "road_object_rknn.h"
#endif
#include <sys/prctl.h>
#if defined(HAVE_FRONT_P1)
#include "front_world_capture.h"
#include "crosswalk_rknn.h"
#define MULTIVIDEO_SERVER_VERSION "multivideo-hdmi-front-observed-junction-v5-fresh"
#elif defined(HAVE_FRONT_ASSIST)
#define MULTIVIDEO_SERVER_VERSION "multivideo-hdmi-front-assist-v7"
#elif defined(HAVE_MULTI_SCENE)
#define MULTIVIDEO_SERVER_VERSION "multivideo-drive-assist-v6"
#elif defined(HAVE_SYNC_SESSION)
#define MULTIVIDEO_SERVER_VERSION "multivideo-hdmi-scene-sync-v5"
#elif defined(HAVE_ROAD_OBJECTS)
#define MULTIVIDEO_SERVER_VERSION "multivideo-hdmi-scene-v4"
#elif defined(LANE_DIAGNOSTIC_BUILD)
#define MULTIVIDEO_SERVER_VERSION "multivideo-hdmi-lane-v3-diag"
#else
#define MULTIVIDEO_SERVER_VERSION "multivideo-hdmi-lane-v2"
#endif
#else
#ifdef HAVE_JUNCTION_DEMO
#define MULTIVIDEO_SERVER_VERSION "multivideo-waymo-junction-v1"
#else
#define MULTIVIDEO_SERVER_VERSION "multivideo-ethernet-v6"
#endif
#endif

_Static_assert(sizeof(struct pango_video_header) == PANGO_VIDEO_HEADER_BYTES, "video header size");

static volatile sig_atomic_t stopping;
static bool profiling;

struct video_timing {
    uint64_t header_ns[3];
    uint64_t pixels_ns[3];
    uint64_t verify_ns[3];
    uint64_t verify_wait_ns;
    uint64_t mosaic_ns;
    uint64_t checksum_ns;
};

struct video_buffers {
    uint8_t *pixels[3];
    uint8_t *mosaic;
    struct pango_video_header headers[3];
    uint64_t pairs;
    struct video_timing timing;
    bool overlap_verify;
#ifdef HAVE_JUNCTION_DEMO
    struct junction_demo *junction;
#endif
#ifdef HAVE_SYNC_SESSION
    struct sync_capture *sync;
    bool detection_enabled;
#ifdef HAVE_MULTI_SCENE
    uint64_t last_drive_report, front_scene, front_object_sequence;
#endif
#endif
#ifdef HAVE_LANE_STYLE
    struct lane_rknn *lane;
    struct lane_async *lane_async;
#ifdef HAVE_ROAD_OBJECTS
    struct road_object_async *objects;
#endif
#endif
#ifdef HAVE_FRONT_P1
    struct crosswalk_rknn *crosswalk;
    uint64_t crosswalk_sent_seq;
#endif
#ifdef HAVE_FRONT_ASSIST
    int drive_socket;
    struct sockaddr_in drive_destination;
    uint64_t last_drive_report;
#endif
};

struct video_verify_job {
    struct video_buffers *buffers;
    bool valid;
};

static uint32_t checksum_bytes(const uint8_t *pixels, size_t bytes);

static uint64_t profile_now_ns(void)
{
    struct timespec timestamp = {0};

    if (!profiling)
        return 0;
    if (clock_gettime(CLOCK_MONOTONIC, &timestamp)) {
        perror("read profiling clock");
        exit(EXIT_FAILURE);
    }
    return (uint64_t)timestamp.tv_sec * UINT64_C(1000000000) + (uint64_t)timestamp.tv_nsec;
}

#ifdef HAVE_FRONT_ASSIST
static uint64_t front_now_ns(void)
{
    struct timespec timestamp = {0};

    if (clock_gettime(CLOCK_MONOTONIC, &timestamp)) {
        perror("read front assist clock");
        return 0;
    }
    return (uint64_t)timestamp.tv_sec * UINT64_C(1000000000) + (uint64_t)timestamp.tv_nsec;
}

static void front_assist_downsample(uint8_t *output, const uint8_t *source)
{
    const unsigned output_width = 1280;
    const unsigned output_height = 480;
    const unsigned image_width = 853;
    const unsigned left = (output_width - image_width) / 2;
    unsigned y;

    memset(output, 0, PANGO_VIDEO_MOSAIC_BYTES);
    for (y = 0; y < output_height; y++) {
        unsigned source_y = y * 1080U / output_height;
        unsigned x;

        for (x = 0; x < image_width; x++) {
            unsigned source_x = x * 1920U / image_width;
            size_t source_offset = ((size_t)source_y * 1920U + source_x) * 2U;
            size_t output_offset = ((size_t)y * output_width + left + x) * 2U;

            output[output_offset] = source[source_offset];
            output[output_offset + 1] = source[source_offset + 1];
        }
    }
}

static const uint8_t *front_assist_output(struct video_buffers *buffers,
                                          struct pango_video_header *header)
{
    uint64_t sequence = le64toh(buffers->headers[0].sequence);
    uint64_t timestamp = le64toh(buffers->headers[0].timestamp_ns);

    front_assist_downsample(buffers->mosaic, buffers->pixels[0]);
#ifdef HAVE_FRONT_P1
    if (buffers->lane_async)
        lane_async_draw(buffers->lane_async, buffers->mosaic, 1280, 480, 213, 0, 853, 480, sequence, timestamp);
    if (buffers->objects)
        road_object_async_draw(buffers->objects, buffers->mosaic, 1280, 480, 213, 0, 853, 480, sequence, timestamp);
#endif
    memset(header, 0, sizeof(*header));
    header->magic = htole32(PANGO_VIDEO_MAGIC);
    header->version = htole16(PANGO_VIDEO_VERSION);
    header->header_bytes = htole16(sizeof(*header));
    header->source = htole32(PANGO_VIDEO_MOSAIC);
    header->format = htole32(PANGO_VIDEO_RGB565LE);
    header->width = htole32(1280);
    header->height = htole32(480);
    header->pixel_bytes = htole32(PANGO_VIDEO_MOSAIC_BYTES);
    header->checksum = htole32(checksum_bytes(buffers->mosaic, PANGO_VIDEO_MOSAIC_BYTES));
    header->sequence = htole64(sequence);
    header->timestamp_ns = htole64(timestamp);
    header->hdmi_timestamp_ns = htole64(timestamp);
    return buffers->mosaic;
}

static void front_assist_report(struct video_buffers *buffers)
{
    struct lane_result lane_result = {0};
    struct road_object_result object_result = {0};
    uint64_t now = front_now_ns();
    uint64_t now_ms;
    uint64_t lane_ms = 0;
    uint64_t object_ms = 0;
    bool lane_valid;
    bool object_valid;
    char report[256];
    int length;

    if (buffers->drive_socket < 0 || !now ||
        (buffers->last_drive_report && now - buffers->last_drive_report < UINT64_C(100000000)))
        return;
    now_ms = now / UINT64_C(1000000);
    lane_valid = buffers->lane_async && lane_async_snapshot(buffers->lane_async, now, &lane_result);
    object_valid = buffers->objects && road_object_async_snapshot(buffers->objects, now, &object_result);
    if (lane_valid) lane_ms = lane_result.timestamp_ns / UINT64_C(1000000);
    if (object_valid) object_ms = object_result.timestamp_ns / UINT64_C(1000000);
    length = snprintf(report, sizeof(report), "FRONT %" PRIu64 " %u %u %" PRIu64 " %u %u %u %" PRIu64,
        now_ms,
        lane_valid ? (unsigned)lane_result.sides[0].decision_type : 0U,
        lane_valid ? (unsigned)lane_result.sides[1].decision_type : 0U,
        lane_ms,
        object_valid ? (unsigned)object_result.signal : 0U,
        object_valid ? object_result.vehicles : 0U,
        object_valid ? object_result.people : 0U,
        object_ms);
    if (length > 0 && (size_t)length < sizeof(report)) {
        if (sendto(buffers->drive_socket, report, (size_t)length, 0,
                   (const struct sockaddr *)&buffers->drive_destination,
                   sizeof(buffers->drive_destination)) < 0 && errno != EINTR)
            perror("send front assist report");
    }
#ifdef HAVE_FRONT_P1
    {
        static uint32_t producer;
        struct front_world world = {0};
        unsigned char wire[FW_BYTES]; size_t bytes;
        if (!producer) { producer = (uint32_t)now ^ (uint32_t)getpid(); if (!producer) producer=1; }
        world.producer=producer; world.sent=now_ms;
        world.frame_seq=le64toh(buffers->headers[0].sequence);
        world.frame_ms=le64toh(buffers->headers[0].timestamp_ns)/UINT64_C(1000000);
        world.width=1920; world.height=1080;
        fw_capture(&world,lane_valid?&lane_result:NULL,object_valid?&object_result:NULL);
        bytes=fw_pack(&world,wire,sizeof(wire));
        if (bytes && sendto(buffers->drive_socket,wire,bytes,MSG_DONTWAIT,
            (const struct sockaddr *)&buffers->drive_destination,sizeof(buffers->drive_destination))<0 && errno!=EAGAIN && errno!=EINTR)
            perror("send front world observations");
        {
            struct front_world display = {0};
            struct road_object_result history = {0};
            bool available=road_object_async_display_snapshot(buffers->objects,now,&history);
            display.producer=producer;display.sent=now_ms;
            display.frame_seq=world.frame_seq;display.frame_ms=world.frame_ms;
            display.width=1920;display.height=1080;
            fw_capture(&display,NULL,available?&history:NULL);
            bytes=fw_pack(&display,wire,sizeof(wire));
            if(bytes) {
                memcpy(wire,"FWD1",4);
                if(sendto(buffers->drive_socket,wire,bytes,MSG_DONTWAIT,
                    (const struct sockaddr *)&buffers->drive_destination,sizeof(buffers->drive_destination))<0 && errno!=EAGAIN && errno!=EINTR)
                    perror("send display-only object history");
            }
        }
        if (buffers->crosswalk) {
            struct crosswalk_observation crossing = {0};
            unsigned char crossing_wire[CW_WIRE_BYTES];
            if (cw_snapshot(buffers->crosswalk, now, &crossing) &&
                crossing.source_seq != buffers->crosswalk_sent_seq) {
                crossing.producer = producer;
                crossing.sent_ms = now_ms;
                bytes = cw_pack(&crossing, crossing_wire, sizeof(crossing_wire));
                if (bytes && sendto(buffers->drive_socket, crossing_wire, bytes, MSG_DONTWAIT,
                    (const struct sockaddr *)&buffers->drive_destination, sizeof(buffers->drive_destination)) >= 0)
                    buffers->crosswalk_sent_seq = crossing.source_seq;
            }
        }
    }
    {
        char lights[2048];
        unsigned index, count = 0;
        size_t used;
        if (object_valid) for (index = 0; index < object_result.count; index++)
            if (object_result.boxes[index].class_id == 9) count++;
        if (count > 16) count = 0;
        used = (size_t)snprintf(lights, sizeof(lights), "LIGHTS %" PRIu64 " %" PRIu64 " %u", now_ms, object_ms, count);
        if (count) for (index = 0; index < object_result.count; index++) {
            const struct road_box *box = &object_result.boxes[index];
            if (box->class_id != 9) continue;
            length = snprintf(lights + used, sizeof(lights) - used, " %u %u %u %u %u",
                (unsigned)(box->left * 10000 / 1920), (unsigned)(box->top * 10000 / 1080),
                (unsigned)(box->right * 10000 / 1920), (unsigned)(box->bottom * 10000 / 1080),
                object_result.lamp_color[index]);
            if (length <= 0 || (size_t)length >= sizeof(lights) - used) break;
            used += (size_t)length;
        }
        if (sendto(buffers->drive_socket, lights, used, 0,
                   (const struct sockaddr *)&buffers->drive_destination, sizeof(buffers->drive_destination)) < 0 && errno != EINTR)
            perror("send selected lamp evidence");
    }
#endif
    buffers->last_drive_report = now;
}
#endif

static void log_video_profile(const struct video_buffers *buffers,
                              const struct pango_video_header *header,
                              uint64_t send_ns, uint64_t total_ns)
{
    const struct video_timing *timing = &buffers->timing;

    printf("profile source=%u sequence=%" PRIu64 " hdmi_seq=%" PRIu64 " camera_seq=%" PRIu64
           " hdmi_header_us=%" PRIu64 " hdmi_pixels_us=%" PRIu64 " hdmi_verify_us=%" PRIu64
           " camera_header_us=%" PRIu64 " camera_pixels_us=%" PRIu64 " camera_verify_us=%" PRIu64
           " ethernet_header_us=%" PRIu64 " ethernet_pixels_us=%" PRIu64 " ethernet_verify_us=%" PRIu64
           " verify_wait_us=%" PRIu64
           " mosaic_us=%" PRIu64 " output_checksum_us=%" PRIu64
           " send_us=%" PRIu64 " total_us=%" PRIu64 "\n",
           le32toh(header->source), le64toh(header->sequence),
           le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[1].sequence),
           timing->header_ns[0] / 1000, timing->pixels_ns[0] / 1000, timing->verify_ns[0] / 1000,
           timing->header_ns[1] / 1000, timing->pixels_ns[1] / 1000, timing->verify_ns[1] / 1000,
           timing->header_ns[2] / 1000, timing->pixels_ns[2] / 1000, timing->verify_ns[2] / 1000,
           timing->verify_wait_ns / 1000,
           timing->mosaic_ns / 1000, timing->checksum_ns / 1000, send_ns / 1000, total_ns / 1000);
}

static void stop_server(int signal_number)
{
    (void)signal_number;
    stopping = 1;
}

static uint32_t checksum_bytes(const uint8_t *pixels, size_t bytes)
{
    uint32_t checksum = 2166136261U;
    size_t index;

    for (index = 0; index < bytes; index++)
        checksum = (checksum ^ pixels[index]) * 16777619U;
    return checksum;
}

static bool read_exact(int descriptor, void *buffer, size_t bytes, const char *part)
{
    uint8_t *destination = buffer;
    size_t total_bytes = bytes;

    while (bytes && !stopping) {
        ssize_t received = read(descriptor, destination, bytes);

        if (received < 0 && errno == EINTR)
            continue;
        if (received <= 0) {
            int read_error = received < 0 ? errno : 0;

            if (read_error == EAGAIN && bytes == total_bytes)
                return false;
            fprintf(stderr, "Read failed: part=%s received=%zu/%zu errno=%d (%s)\n",
                    part, total_bytes - bytes, total_bytes, read_error,
                    received < 0 ? strerror(read_error) : "EOF");
            return false;
        }
        destination += received;
        bytes -= (size_t)received;
    }
    return bytes == 0;
}

static bool read_source(int descriptor, unsigned int source, struct video_buffers *buffers)
{
    struct pango_video_header *header = &buffers->headers[source];
    uint32_t width = source == PANGO_VIDEO_HDMI ? 1920 : 640;
    uint32_t height = source == PANGO_VIDEO_HDMI ? 1080 : 480;
    uint32_t bytes = width * height * 2;
    uint64_t started = profile_now_ns();

    if (!read_exact(descriptor, header, sizeof(*header), "device header")) {
        if (errno != EAGAIN)
            fprintf(stderr, "Capture failed: source=%u part=header\n", source);
        return false;
    }
    buffers->timing.header_ns[source] = profile_now_ns() - started;
    if (le32toh(header->magic) != PANGO_VIDEO_MAGIC ||
        le16toh(header->version) != PANGO_VIDEO_VERSION ||
        le16toh(header->header_bytes) != sizeof(*header) ||
        le32toh(header->source) != source ||
        le32toh(header->format) != PANGO_VIDEO_RGB565LE ||
        le32toh(header->width) != width || le32toh(header->height) != height ||
        le32toh(header->pixel_bytes) != bytes) {
        fprintf(stderr, "Invalid video source header (expected source=%u)\n", source);
        return false;
    }
    started = profile_now_ns();
    if (!read_exact(descriptor, buffers->pixels[source], bytes, "device pixels")) {
        fprintf(stderr, "Capture failed: source=%u part=pixels sequence=%" PRIu64 "\n",
                source, le64toh(header->sequence));
        return false;
    }
    buffers->timing.pixels_ns[source] = profile_now_ns() - started;
    return true;
}

static bool verify_source(unsigned int source, struct video_buffers *buffers)
{
    const struct pango_video_header *header = &buffers->headers[source];
    uint64_t started = profile_now_ns();
    uint32_t checksum = checksum_bytes(buffers->pixels[source], le32toh(header->pixel_bytes));

    buffers->timing.verify_ns[source] = profile_now_ns() - started;
    if (checksum != le32toh(header->checksum)) {
        fprintf(stderr, "Video source checksum mismatch: source=%u\n", source);
        return false;
    }
    return true;
}

static bool capture_source(int descriptor, unsigned int source, struct video_buffers *buffers)
{
    return read_source(descriptor, source, buffers) && verify_source(source, buffers);
}

static void *verify_hdmi_worker(void *argument)
{
    struct video_verify_job *job = argument;

    job->valid = verify_source(PANGO_VIDEO_HDMI, job->buffers);
    return NULL;
}

static bool capture_pair_overlapped(int descriptor, struct video_buffers *buffers)
{
    struct video_verify_job job = {.buffers = buffers, .valid = false};
    pthread_t worker;
    uint64_t started;
    bool camera_valid;
    int error;

    if (!read_source(descriptor, PANGO_VIDEO_HDMI, buffers))
        return false;
    error = pthread_create(&worker, NULL, verify_hdmi_worker, &job);
    if (error) {
        fprintf(stderr, "Start HDMI checksum worker failed: %s\n", strerror(error));
        return false;
    }
    camera_valid = capture_source(descriptor, PANGO_VIDEO_CAMERA, buffers);
    started = profile_now_ns();
    error = pthread_join(worker, NULL);
    if (error) {
        fprintf(stderr, "Join HDMI checksum worker failed: %s\n", strerror(error));
        exit(EXIT_FAILURE);
    }
    buffers->timing.verify_wait_ns = profile_now_ns() - started;
    return camera_valid && job.valid;
}

static const uint8_t *capture_output(int descriptor, unsigned int mode,
                                    struct video_buffers *buffers, struct pango_video_header *header)
{
    uint64_t started;

    memset(&buffers->timing, 0, sizeof(buffers->timing));
    if (mode == PANGO_VIDEO_MODE_ETHERNET) {
        if (!capture_source(descriptor, PANGO_VIDEO_ETHERNET, buffers)) {
            if (errno == EAGAIN)
                fprintf(stderr, "No complete Ethernet frame available; start the EVF1 sender first\n");
            return NULL;
        }
        *header = buffers->headers[2];
        return buffers->pixels[2];
    }
    if ((mode == PANGO_VIDEO_BOTH || mode == PANGO_VIDEO_ALL) && buffers->overlap_verify) {
        if (!capture_pair_overlapped(descriptor, buffers))
            return NULL;
    } else {
        if (mode != PANGO_VIDEO_CAMERA && !capture_source(descriptor, PANGO_VIDEO_HDMI, buffers))
            return NULL;
        if (mode != PANGO_VIDEO_HDMI && !capture_source(descriptor, PANGO_VIDEO_CAMERA, buffers))
            return NULL;
    }
    if (mode == PANGO_VIDEO_ALL) {
        struct pango_video_header previous_header = buffers->headers[2];
        errno = 0;
        if (!capture_source(descriptor, PANGO_VIDEO_ETHERNET, buffers)) {
            if (errno != EAGAIN || ioctl(descriptor, PANGO_VIDEO_SET_MODE, &mode))
                return NULL;
            buffers->headers[2] = previous_header;
        }
    }
#ifdef HAVE_JUNCTION_DEMO
    if (buffers->junction) {
        if (!junction_demo_submit(buffers->junction, 0, buffers->pixels[0],
                le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
        if (le32toh(buffers->headers[2].magic) == PANGO_VIDEO_MAGIC &&
            !junction_demo_submit(buffers->junction, 1, buffers->pixels[2],
                le64toh(buffers->headers[2].sequence), le64toh(buffers->headers[2].timestamp_ns))) return NULL;
    }
#endif
#ifdef HAVE_SYNC_SESSION
    if (buffers->sync) {
        uint32_t previous_session=sync_capture_session(buffers->sync);
        bool was_enabled=buffers->detection_enabled;
        buffers->detection_enabled=sync_capture_update(buffers->sync,buffers->pixels[0],buffers->pixels[2]);
        if(previous_session!=sync_capture_session(buffers->sync) || (was_enabled && !buffers->detection_enabled)) {
            lane_async_reset(buffers->lane_async);
            road_object_async_reset(buffers->objects);
#ifdef HAVE_MULTI_SCENE
            buffers->front_scene=buffers->front_object_sequence=0;
#endif
        }
    }
#endif
    if (mode != PANGO_VIDEO_BOTH && mode != PANGO_VIDEO_ALL) {
        *header = buffers->headers[mode];
#ifdef HAVE_ROAD_OBJECTS
        if (buffers->objects && mode == PANGO_VIDEO_HDMI && !road_object_async_submit(buffers->objects, buffers->pixels[0], 1920, 1080,
                le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
#endif
#ifdef HAVE_LANE_STYLE
#ifdef HAVE_FRONT_P1
        if (mode == PANGO_VIDEO_HDMI) {
            if (buffers->crosswalk && !cw_submit(buffers->crosswalk, buffers->pixels[0], 1920, 1080,
                    le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
            if (buffers->lane_async && !lane_async_submit(buffers->lane_async, buffers->pixels[0], 1920, 1080,
                    le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
            front_assist_report(buffers);
            return front_assist_output(buffers, header);
        }
#endif
        if (buffers->lane_async && mode == PANGO_VIDEO_HDMI) {
            if (!lane_async_submit(buffers->lane_async, buffers->pixels[0], 1920, 1080,
                                    le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
            lane_async_draw(buffers->lane_async, buffers->pixels[0], 1920, 1080, 0, 0, 1920, 1080,
                            le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns));
            header->checksum = htole32(checksum_bytes(buffers->pixels[0], PANGO_VIDEO_HDMI_BYTES));
        }
        if (buffers->lane && mode == PANGO_VIDEO_HDMI) {
            if (!lane_rknn_process(buffers->lane, buffers->pixels[0], 1920, 1080,
                                   le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
            lane_rknn_draw(buffers->lane, buffers->pixels[0], 1920, 1080, 0, 0, 1920, 1080);
            header->checksum = htole32(checksum_bytes(buffers->pixels[0], PANGO_VIDEO_HDMI_BYTES));
        }
#endif
#ifdef HAVE_ROAD_OBJECTS
        if (buffers->objects && mode == PANGO_VIDEO_HDMI) {
            road_object_async_draw(buffers->objects, buffers->pixels[0], 1920, 1080, 0, 0, 1920, 1080,
                le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns));
            header->checksum = htole32(checksum_bytes(buffers->pixels[0], PANGO_VIDEO_HDMI_BYTES));
        }
#endif
#ifdef HAVE_FRONT_ASSIST
        if (mode == PANGO_VIDEO_HDMI) {
            front_assist_report(buffers);
            return front_assist_output(buffers, header);
        }
#endif
        return buffers->pixels[mode];
    }
#ifdef HAVE_LANE_STYLE
#ifdef HAVE_SYNC_SESSION
    if (buffers->detection_enabled) {
#endif
#ifdef HAVE_MULTI_SCENE
    struct sync_mark source_mark;
    bool front_new=sync_capture_source(buffers->sync,1,&source_mark);
    if(front_new) buffers->front_scene=source_mark.scene_ms;
    if (front_new && buffers->lane_async && !lane_async_submit(buffers->lane_async, buffers->pixels[0], 1920, 1080,
#else
    if (buffers->lane_async && !lane_async_submit(buffers->lane_async, buffers->pixels[0], 1920, 1080,
#endif
                                                 le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
    if (buffers->lane && !lane_rknn_process(buffers->lane, buffers->pixels[0], 1920, 1080,
                                            le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
#endif
#ifdef HAVE_ROAD_OBJECTS
#ifdef HAVE_MULTI_SCENE
    if(buffers->objects) {
        if(front_new) {
            buffers->front_object_sequence=source_mark.wire;
            if(!road_multi_submit(buffers->objects,0,buffers->pixels[0],source_mark.wire,
                le64toh(buffers->headers[0].timestamp_ns),source_mark.scene_ms)) return NULL;
        }
        if(!road_multi_submit(buffers->objects,1,buffers->pixels[1],le64toh(buffers->headers[1].sequence),
            le64toh(buffers->headers[1].timestamp_ns),0)) return NULL;
        if(mode==PANGO_VIDEO_ALL && sync_capture_source(buffers->sync,2,&source_mark) &&
            !road_multi_submit(buffers->objects,2,buffers->pixels[2],source_mark.wire,
                le64toh(buffers->headers[2].timestamp_ns),source_mark.scene_ms)) return NULL;
    }
#else
    if (buffers->objects && !road_object_async_submit(buffers->objects, buffers->pixels[0], 1920, 1080,
            le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns))) return NULL;
#endif
#endif
#ifdef HAVE_SYNC_SESSION
    }
#endif
    started = profile_now_ns();
    if (mode == PANGO_VIDEO_ALL)
        video_mosaic_three_rgb565(buffers->mosaic, buffers->pixels[0], buffers->pixels[1], buffers->pixels[2]);
    else video_mosaic_rgb565(buffers->mosaic, buffers->pixels[0], buffers->pixels[1]);
#ifdef HAVE_LANE_STYLE
#ifdef HAVE_SYNC_SESSION
    if (buffers->detection_enabled) {
#endif
    if (buffers->lane) lane_rknn_draw(buffers->lane, buffers->mosaic, 1280, 480, 0, 60, 640, 360);
    if (buffers->lane_async) lane_async_draw(buffers->lane_async, buffers->mosaic, 1280, 480, 0, 60, 640, 360,
                                            le64toh(buffers->headers[0].sequence), le64toh(buffers->headers[0].timestamp_ns));
#endif
#ifdef HAVE_ROAD_OBJECTS
    if (buffers->objects) road_object_async_draw(buffers->objects, buffers->mosaic, 1280, 480, 0, 60, 640, 360,
#ifdef HAVE_MULTI_SCENE
            buffers->front_object_sequence,
#else
            le64toh(buffers->headers[0].sequence),
#endif
            le64toh(buffers->headers[0].timestamp_ns));
#ifdef HAVE_MULTI_SCENE
    if(buffers->objects && mode==PANGO_VIDEO_ALL) {
        road_multi_draw(buffers->objects,1,buffers->mosaic,800,0,profile_now_ns());
        road_multi_draw(buffers->objects,2,buffers->mosaic,800,240,profile_now_ns());
    }
    if(buffers->sync && profile_now_ns()-buffers->last_drive_report>=UINT64_C(100000000)) {
        struct lane_result lane_result;
        struct road_object_result objects[3]={0};
        uint64_t now=profile_now_ns(), scene[3]={0};
        unsigned i;
        char report[512];
        bool lane_valid=lane_async_snapshot(buffers->lane_async,now,&lane_result);
        for(i=0;i<3;i++) road_multi_snapshot(buffers->objects,i,now,&objects[i],&scene[i]);
        snprintf(report,sizeof(report),"DRIVE %u %llu %u %u %llu %u %u %u %u %u %llu %llu %llu %llu %llu",
            sync_capture_session(buffers->sync),(unsigned long long)(now/1000000),
            lane_valid ? (unsigned)lane_result.sides[0].decision_type : 0,lane_valid ? (unsigned)lane_result.sides[1].decision_type : 0,
            (unsigned long long)(lane_valid ? lane_result.timestamp_ns/1000000 : 0),(unsigned)objects[0].signal,
            objects[0].vehicles,objects[1].vehicles,objects[2].vehicles,objects[0].people,
            (unsigned long long)(objects[0].timestamp_ns/1000000),(unsigned long long)(objects[1].timestamp_ns/1000000),
            (unsigned long long)(objects[2].timestamp_ns/1000000),(unsigned long long)buffers->front_scene,(unsigned long long)scene[2]);
        sync_capture_report(buffers->sync,report); buffers->last_drive_report=now;
    }
#endif
#endif
#ifdef HAVE_SYNC_SESSION
    } else lane_style_draw_status_rgb565(buffers->mosaic,1280,480,0,60,640,360,"WAIT: PRESS START ON RK PANEL");
#endif
    buffers->timing.mosaic_ns = profile_now_ns() - started;
    memset(header, 0, sizeof(*header));
    header->magic = htole32(PANGO_VIDEO_MAGIC);
    header->version = htole16(PANGO_VIDEO_VERSION);
    header->header_bytes = htole16(sizeof(*header));
    header->source = htole32(mode == PANGO_VIDEO_ALL ? PANGO_VIDEO_TRIPLE : PANGO_VIDEO_MOSAIC);
    header->format = htole32(PANGO_VIDEO_RGB565LE);
    header->width = htole32(1280);
    header->height = htole32(480);
    header->pixel_bytes = htole32(PANGO_VIDEO_MOSAIC_BYTES);
    started = profile_now_ns();
    header->checksum = htole32(checksum_bytes(buffers->mosaic, PANGO_VIDEO_MOSAIC_BYTES));
    buffers->timing.checksum_ns = profile_now_ns() - started;
    header->sequence = htole64(++buffers->pairs);
    header->timestamp_ns = buffers->headers[1].timestamp_ns;
    header->hdmi_timestamp_ns = buffers->headers[0].timestamp_ns;
    header->camera_timestamp_ns = buffers->headers[1].timestamp_ns;
    return buffers->mosaic;
}

static bool write_raw(const char *prefix, const char *suffix, const uint8_t *pixels, size_t bytes)
{
    char path[1024];
    FILE *output;
    bool success;
    int length = snprintf(path, sizeof(path), "%s_%s.rgb565", prefix, suffix);

    if (length < 0 || (size_t)length >= sizeof(path))
        return false;
    output = fopen(path, "wb");
    if (!output) {
        perror(path);
        return false;
    }
    success = fwrite(pixels, 1, bytes, output) == bytes;
    if (fclose(output))
        success = false;
    if (success)
        printf("Saved %s (%zu bytes)\n", path, bytes);
    return success;
}

static bool write_ppm(const char *prefix, const uint8_t *pixels, unsigned int width, unsigned int height)
{
    char path[1024];
    uint8_t *row = malloc(width * 3U);
    FILE *output;
    unsigned int row_index;
    bool success = false;
    int length = snprintf(path, sizeof(path), "%s.ppm", prefix);

    if (!row || length < 0 || (size_t)length >= sizeof(path)) {
        free(row);
        return false;
    }
    output = fopen(path, "wb");
    if (!output) {
        perror(path);
        free(row);
        return false;
    }
    if (fprintf(output, "P6\n%u %u\n255\n", width, height) < 0)
        goto cleanup;
    for (row_index = 0; row_index < height; row_index++) {
        unsigned int column;

        for (column = 0; column < width; column++) {
            size_t offset = ((size_t)row_index * width + column) * 2;
            unsigned int pixel = pixels[offset] | ((unsigned int)pixels[offset + 1] << 8);

            row[column * 3] = (uint8_t)(((pixel >> 11) & 31) * 255 / 31);
            row[column * 3 + 1] = (uint8_t)(((pixel >> 5) & 63) * 255 / 63);
            row[column * 3 + 2] = (uint8_t)((pixel & 31) * 255 / 31);
        }
        if (fwrite(row, 3, width, output) != width)
            goto cleanup;
    }
    success = true;
cleanup:
    if (fclose(output))
        success = false;
    free(row);
    if (success)
        printf("Saved %s (%ux%u)\n", path, width, height);
    return success;
}

static bool send_exact(int descriptor, const void *buffer, size_t bytes,
                       const char *part, uint64_t sequence)
{
    const uint8_t *source = buffer;
    size_t total_bytes = bytes;

    while (bytes && !stopping) {
        ssize_t sent = send(descriptor, source, bytes, MSG_NOSIGNAL);

        if (sent < 0 && errno == EINTR)
            continue;
        if (sent <= 0) {
            int send_error = sent < 0 ? errno : EPIPE;

            fprintf(stderr, "Video send failed: part=%s sequence=%" PRIu64
                    " sent=%zu/%zu errno=%d (%s)\n",
                    part, sequence, total_bytes - bytes, total_bytes,
                    send_error, strerror(send_error));
            return false;
        }
        source += sent;
        bytes -= (size_t)sent;
    }
    return bytes == 0;
}

static int open_listener(const char *bind_address, unsigned long port)
{
    struct sockaddr_in address = {0};
    int enabled = 1;
    int descriptor = socket(AF_INET, SOCK_STREAM, 0);

    if (descriptor < 0)
        return -1;
    setsockopt(descriptor, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, bind_address, &address.sin_addr) != 1 ||
        bind(descriptor, (struct sockaddr *)&address, sizeof(address)) || listen(descriptor, 1)) {
        close(descriptor);
        return -1;
    }
    return descriptor;
}

static int detach_server(const char *log_path)
{
    int log_descriptor = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    int input_descriptor;
    pid_t process;

    if (log_descriptor < 0)
        return -1;
    process = fork();
    if (process < 0) {
        close(log_descriptor);
        return -1;
    }
    if (process > 0) {
        printf("Multivideo server started: pid=%ld log=%s\n", (long)process, log_path);
        close(log_descriptor);
        return 1;
    }
    if (setsid() < 0)
        _exit(1);
    input_descriptor = open("/dev/null", O_RDONLY);
    if (input_descriptor < 0 || dup2(input_descriptor, STDIN_FILENO) < 0 ||
        dup2(log_descriptor, STDOUT_FILENO) < 0 || dup2(log_descriptor, STDERR_FILENO) < 0)
        _exit(1);
    if (input_descriptor > STDERR_FILENO)
        close(input_descriptor);
    if (log_descriptor > STDERR_FILENO)
        close(log_descriptor);
    return 0;
}

static bool parse_positive(const char *text, unsigned long maximum, unsigned long *value)
{
    char *end;

    errno = 0;
    *value = strtoul(text, &end, 10);
    return !errno && text[0] >= '0' && text[0] <= '9' && !*end && *value && *value <= maximum;
}

static bool valid_stream_header(const struct pango_video_header *header)
{
    uint32_t source = le32toh(header->source);
    uint32_t width;
    uint32_t height;

    if (source == PANGO_VIDEO_HDMI) {
        width = 1920;
        height = 1080;
    } else if (source == PANGO_VIDEO_CAMERA || source == PANGO_VIDEO_ETHERNET) {
        width = 640;
        height = 480;
    } else if (source == PANGO_VIDEO_MOSAIC || source == PANGO_VIDEO_TRIPLE) {
        width = 1280;
        height = 480;
    } else {
        return false;
    }
    return le32toh(header->magic) == PANGO_VIDEO_MAGIC &&
           le16toh(header->version) == PANGO_VIDEO_VERSION &&
           le16toh(header->header_bytes) == sizeof(*header) &&
           le32toh(header->format) == PANGO_VIDEO_RGB565LE &&
           le32toh(header->width) == width && le32toh(header->height) == height &&
           le32toh(header->pixel_bytes) == width * height * 2 &&
           le64toh(header->sequence) && le64toh(header->timestamp_ns);
}

static int check_video_stream(unsigned long port, unsigned long count)
{
    struct sockaddr_in address = {
        .sin_family = AF_INET,
        .sin_port = htons((uint16_t)port),
        .sin_addr = {.s_addr = htonl(INADDR_LOOPBACK)}
    };
    struct timeval receive_timeout = {.tv_sec = 60};
    uint8_t *pixels = malloc(PANGO_VIDEO_HDMI_BYTES);
    uint64_t previous_sequence = 0;
    uint32_t previous_source = 0;
    unsigned long completed;
    int descriptor = -1;
    int result = 1;

    if (!pixels)
        goto cleanup;
    descriptor = socket(AF_INET, SOCK_STREAM, 0);
    if (descriptor < 0 ||
        setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &receive_timeout, sizeof(receive_timeout)) ||
        connect(descriptor, (struct sockaddr *)&address, sizeof(address))) {
        perror("connect local video server");
        goto cleanup;
    }
    printf("Local stream check: 127.0.0.1:%lu count=%lu version=%s\n",
           port, count, MULTIVIDEO_SERVER_VERSION);
    for (completed = 0; completed < count && !stopping; completed++) {
        struct pango_video_header header;
        uint32_t bytes;
        uint32_t source;
        uint64_t sequence;

        if (!read_exact(descriptor, &header, sizeof(header), "socket header"))
            goto cleanup;
        if (!valid_stream_header(&header)) {
            fprintf(stderr, "Invalid stream header: frame=%lu\n", completed + 1);
            goto cleanup;
        }
        bytes = le32toh(header.pixel_bytes);
        source = le32toh(header.source);
        sequence = le64toh(header.sequence);
        if (completed && source == previous_source && sequence <= previous_sequence) {
            fprintf(stderr, "Non-increasing stream sequence: frame=%lu sequence=%" PRIu64 "\n",
                    completed + 1, sequence);
            goto cleanup;
        }
        if (!read_exact(descriptor, pixels, bytes, "socket pixels")) {
            fprintf(stderr, "Stream check failed: frame=%lu source=%u sequence=%" PRIu64 "\n",
                    completed + 1, source, sequence);
            goto cleanup;
        }
        if (checksum_bytes(pixels, bytes) != le32toh(header.checksum)) {
            fprintf(stderr, "Stream checksum mismatch: frame=%lu source=%u sequence=%" PRIu64 "\n",
                    completed + 1, source, sequence);
            goto cleanup;
        }
        previous_sequence = sequence;
        previous_source = source;
        if (completed < 3 || (completed + 1) % 10 == 0 || completed + 1 == count)
            printf("checked=%lu source=%u size=%ux%u sequence=%" PRIu64 " bytes=%u\n",
                   completed + 1, source, le32toh(header.width), le32toh(header.height), sequence, bytes);
    }
    if (completed == count) {
        printf("PASS: local stream checked %lu frames\n", completed);
        result = 0;
    } else {
        fprintf(stderr, "Local stream check stopped: checked=%lu/%lu\n", completed, count);
    }
cleanup:
    if (descriptor >= 0) close(descriptor);
    free(pixels);
    return result;
}

static void usage(const char *program)
{
    fprintf(stderr, "Usage: %s --device /dev/pango_pcie_multivideo_BDF\n"
            "  [--source both|hdmi|camera|ethernet|all] --snapshot PREFIX\n"
            "  [--source both|hdmi|camera|ethernet|all] --serve [--bind 127.0.0.1] [--port 9002]\n"
            "  [--send-timeout-ms 30000] [--count N] [--profile] [--overlap-verify (both/all)]\n"
            "  [--daemon --log /data/local/tmp/pcie_multivideo.log] [--version]\n"
            "  %s --check-stream [--port 9002] [--count 100] [--daemon --log PATH]\n",
            program, program);
#ifdef HAVE_LANE_STYLE
    fprintf(stderr, "  HDMI lane options: --lane-model FILE --lane-contract FILE [--lane-runtime LIB]\n"
            "  --lane-async [--lane-max-age-ms 750] (live preview, separate latest-frame inference)\n"
            "  --lane-inspect (load/query model without opening PCIe device)\n");
#endif
#ifdef HAVE_ROAD_OBJECTS
    fprintf(stderr, "  --object-model FILE --object-contract FILE [--object-max-age-ms 750] (requires lane async)\n");
#endif
}

int main(int argc, char **argv)
{
    static const struct option options[] = {
        {"device", required_argument, NULL, 'd'}, {"source", required_argument, NULL, 's'},
        {"snapshot", required_argument, NULL, 'o'}, {"serve", no_argument, NULL, 'S'},
        {"bind", required_argument, NULL, 'b'}, {"port", required_argument, NULL, 'p'},
        {"count", required_argument, NULL, 'c'}, {"daemon", no_argument, NULL, 'D'},
        {"log", required_argument, NULL, 'l'}, {"help", no_argument, NULL, 'h'},
        {"send-timeout-ms", required_argument, NULL, 't'},
        {"version", no_argument, NULL, 'v'},
        {"check-stream", no_argument, NULL, 'C'},
        {"profile", no_argument, NULL, 'P'},
        {"overlap-verify", no_argument, NULL, 'V'},
#ifdef HAVE_JUNCTION_DEMO
        {"demo-model", required_argument, NULL, 1020},
        {"demo-contract", required_argument, NULL, 1021},
        {"demo-runtime", required_argument, NULL, 1022},
        {"demo-inspect", no_argument, NULL, 1023},
#endif
#ifdef HAVE_SYNC_SESSION
        {"sync-state", required_argument, NULL, 1010},
#endif
#ifdef HAVE_LANE_STYLE
        {"lane-model", required_argument, NULL, 1000},
        {"lane-contract", required_argument, NULL, 1001},
        {"lane-runtime", required_argument, NULL, 1002},
        {"lane-inspect", no_argument, NULL, 1003},
        {"lane-async", no_argument, NULL, 1004},
        {"lane-max-age-ms", required_argument, NULL, 1005},
#endif
#ifdef HAVE_ROAD_OBJECTS
        {"object-model", required_argument, NULL, 1006},
        {"object-contract", required_argument, NULL, 1007},
        {"object-max-age-ms", required_argument, NULL, 1008},
#endif
#ifdef HAVE_FRONT_P1
        {"crosswalk-model", required_argument, NULL, 1011},
        {"crosswalk-contract", required_argument, NULL, 1012},
#endif
        {NULL, 0, NULL, 0}
    };
    const char *device_path = NULL;
    const char *snapshot_prefix = NULL;
    const char *bind_address = "127.0.0.1";
    const char *log_path = "/data/local/tmp/pcie_multivideo.log";
    struct video_buffers buffers = {0};
#ifdef HAVE_FRONT_ASSIST
    buffers.drive_socket = -1;
#endif
    struct sigaction stop_action = {0};
    unsigned int mode = PANGO_VIDEO_BOTH;
    unsigned long port = 9002;
    unsigned long count = 0;
    unsigned long sent_frames = 0;
    unsigned long connections = 0;
    unsigned long send_timeout_ms = 30000;
    bool serve = false;
    bool background = false;
    bool check_stream = false;
    int descriptor = -1;
    int listener = -1;
    int result = 1;
    int option;
    unsigned int source;
#ifdef HAVE_JUNCTION_DEMO
    const char *demo_model = NULL, *demo_contract = NULL;
    const char *demo_runtime = "/data/local/tmp/rknn-yolov5-test/lib/librknnrt.so";
    bool demo_inspect = false;
    if (prctl(PR_SET_NAME, "pcie_multivideo", 0, 0, 0)) return 1;
#endif
#ifdef HAVE_SYNC_SESSION
    const char *sync_state=NULL;
#endif
#ifdef HAVE_ROAD_OBJECTS
    const char *object_model = NULL, *object_contract = NULL;
    unsigned long object_max_age_ms = 750;
#endif
#ifdef HAVE_FRONT_P1
    const char *crosswalk_model = NULL, *crosswalk_contract = NULL;
#endif
#ifdef HAVE_LANE_STYLE
    const char *lane_model = NULL, *lane_contract = NULL;
    const char *lane_runtime = "/data/local/tmp/rknn-yolov5-test/lib/librknnrt.so";
    bool lane_inspect = false;
    bool lane_async_mode = false;
    unsigned long lane_max_age_ms = 750;
    if (prctl(PR_SET_NAME, "pcie_multivideo", 0, 0, 0)) { perror("set multivideo process name"); return 1; }
#endif

    while ((option = getopt_long(argc, argv, "", options, NULL)) != -1) {
        switch (option) {
        case 'd': device_path = optarg; break;
        case 's':
            if (!strcmp(optarg, "both")) mode = PANGO_VIDEO_BOTH;
            else if (!strcmp(optarg, "hdmi")) mode = PANGO_VIDEO_HDMI;
            else if (!strcmp(optarg, "camera")) mode = PANGO_VIDEO_CAMERA;
            else if (!strcmp(optarg, "ethernet")) mode = PANGO_VIDEO_MODE_ETHERNET;
            else if (!strcmp(optarg, "all")) mode = PANGO_VIDEO_ALL;
            else { usage(argv[0]); return 1; }
            break;
        case 'o': snapshot_prefix = optarg; break;
        case 'S': serve = true; break;
        case 'b': bind_address = optarg; break;
        case 'p': if (!parse_positive(optarg, 65535, &port)) return 1; break;
        case 'c': if (!parse_positive(optarg, 1000000, &count)) return 1; break;
        case 'D': background = true; break;
        case 'l': log_path = optarg; break;
        case 't': if (!parse_positive(optarg, 600000, &send_timeout_ms)) return 1; break;
        case 'v': puts(MULTIVIDEO_SERVER_VERSION); return 0;
        case 'C': check_stream = true; break;
        case 'P': profiling = true; break;
        case 'V': buffers.overlap_verify = true; break;
#ifdef HAVE_JUNCTION_DEMO
        case 1020: demo_model = optarg; break;
        case 1021: demo_contract = optarg; break;
        case 1022: demo_runtime = optarg; break;
        case 1023: demo_inspect = true; break;
#endif
#ifdef HAVE_SYNC_SESSION
        case 1010: sync_state=optarg; break;
#endif
#ifdef HAVE_LANE_STYLE
        case 1000: lane_model = optarg; break;
        case 1001: lane_contract = optarg; break;
        case 1002: lane_runtime = optarg; break;
        case 1003: lane_inspect = true; break;
        case 1004: lane_async_mode = true; break;
        case 1005: if (!parse_positive(optarg, 2000, &lane_max_age_ms) || lane_max_age_ms < 50) return 1; break;
#endif
#ifdef HAVE_ROAD_OBJECTS
        case 1006: object_model = optarg; break;
        case 1007: object_contract = optarg; break;
        case 1008: if (!parse_positive(optarg, 2000, &object_max_age_ms) || object_max_age_ms < 50) return 1; break;
#endif
#ifdef HAVE_FRONT_P1
        case 1011: crosswalk_model = optarg; break;
        case 1012: crosswalk_contract = optarg; break;
#endif
        case 'h': usage(argv[0]); return 0;
        default: usage(argv[0]); return 1;
        }
    }
#ifdef HAVE_LANE_STYLE
#ifdef HAVE_FRONT_P1
    if ((crosswalk_model == NULL) != (crosswalk_contract == NULL) ||
        (crosswalk_model && (!object_model || !lane_model || (!lane_inspect && (!lane_async_mode || mode != PANGO_VIDEO_HDMI))))) {
        usage(argv[0]); return 1;
    }
#endif
#ifdef HAVE_ROAD_OBJECTS
    if ((object_model == NULL) != (object_contract == NULL) || (object_model && (!lane_model || (!lane_inspect && !lane_async_mode)))) {
        usage(argv[0]); return 1;
    }
#endif
    if ((lane_model == NULL) != (lane_contract == NULL) || (lane_async_mode && (!lane_model || lane_inspect || snapshot_prefix || check_stream)) ||
        (lane_inspect && (!lane_model || device_path || serve || snapshot_prefix || check_stream || background)) ||
        (lane_model && (check_stream || (!lane_inspect && mode != PANGO_VIDEO_HDMI && mode != PANGO_VIDEO_BOTH && mode != PANGO_VIDEO_ALL)))) {
        usage(argv[0]); return 1;
    }
    if (lane_inspect) {
        if (optind != argc) { usage(argv[0]); return 1; }
#ifdef HAVE_FRONT_P1
        if (crosswalk_model && !cw_inspect(lane_runtime, crosswalk_model, crosswalk_contract)) return 1;
#endif
        buffers.lane = lane_rknn_open(lane_runtime, lane_model, lane_contract);
        if (!buffers.lane) return 1;
#ifdef HAVE_ROAD_OBJECTS
        if (object_model) {
            struct road_object_engine *checked = road_object_open(lane_runtime, object_model, object_contract);
            if (!checked) { lane_rknn_close(buffers.lane); return 1; }
            road_object_close(checked);
        }
#endif
        lane_rknn_close(buffers.lane); puts("LANE_INSPECT_PASS"); return 0;
    }
#endif
#ifdef HAVE_JUNCTION_DEMO
    if (!demo_model || !demo_contract) return 1;
    if (demo_inspect) {
        struct road_object_engine *checked = road_object_open(demo_runtime, demo_model, demo_contract);
        if (!checked) return 1;
        road_object_close(checked); puts("JUNCTION_INSPECT_PASS"); return 0;
    }
    if (mode != PANGO_VIDEO_ALL || !serve) return 1;
#endif
    if (optind != argc || (buffers.overlap_verify && (check_stream || (mode != PANGO_VIDEO_BOTH && mode != PANGO_VIDEO_ALL))) ||
        (check_stream ? (device_path || snapshot_prefix || serve) :
         (!device_path || serve == (snapshot_prefix != NULL) || (background && !serve)))) {
        usage(argv[0]);
        return 1;
    }
    stop_action.sa_handler = stop_server;
    sigemptyset(&stop_action.sa_mask);
    if (sigaction(SIGINT, &stop_action, NULL) || sigaction(SIGTERM, &stop_action, NULL)) {
        perror("install stop handler");
        return 1;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
#ifdef HAVE_SYNC_SESSION
    if(sync_state && (!serve || mode!=PANGO_VIDEO_ALL || !lane_async_mode)) { usage(argv[0]); return 1; }
#endif
    if (check_stream) {
        if (background) {
            int detached = detach_server(log_path);

            if (detached < 0) {
                perror("detach stream check");
                return 1;
            }
            if (detached > 0)
                return 0;
        }
        return check_video_stream(port, count ? count : 100);
    }
#ifdef HAVE_FRONT_ASSIST
    buffers.drive_socket = socket(AF_INET, SOCK_DGRAM, 0);
    if (buffers.drive_socket < 0) {
        perror("open front assist report socket");
        goto cleanup;
    }
    memset(&buffers.drive_destination, 0, sizeof(buffers.drive_destination));
    buffers.drive_destination.sin_family = AF_INET;
    buffers.drive_destination.sin_port = htons(9013);
    if (inet_pton(AF_INET, "127.0.0.1", &buffers.drive_destination.sin_addr) != 1) {
        fprintf(stderr, "configure front assist report address failed\n");
        goto cleanup;
    }
#endif
    descriptor = open(device_path, O_RDONLY);
    if (descriptor < 0) {
        perror(device_path);
        goto cleanup;
    }
    if (ioctl(descriptor, PANGO_VIDEO_SET_MODE, &mode)) {
        perror("select video source mode");
        goto cleanup;
    }
    for (source = 0; source < 3; source++) {
        if (mode == PANGO_VIDEO_ALL || (mode == PANGO_VIDEO_BOTH && source < 2) ||
            (mode < 2 && mode == source) || (mode == PANGO_VIDEO_MODE_ETHERNET && source == 2)) {
            buffers.pixels[source] = calloc(1, source ? PANGO_VIDEO_CAMERA_BYTES : PANGO_VIDEO_HDMI_BYTES);
            if (!buffers.pixels[source])
                goto cleanup;
        }
    }
    if (mode == PANGO_VIDEO_BOTH || mode == PANGO_VIDEO_ALL
#ifdef HAVE_FRONT_ASSIST
        || mode == PANGO_VIDEO_HDMI
#endif
    ) {
        buffers.mosaic = malloc(PANGO_VIDEO_MOSAIC_BYTES);
        if (!buffers.mosaic)
            goto cleanup;
    }
    if (snapshot_prefix) {
#ifdef HAVE_LANE_STYLE
        if (lane_model) {
            buffers.lane = lane_rknn_open(lane_runtime, lane_model, lane_contract);
            if (!buffers.lane) goto cleanup;
        }
#endif
        struct pango_video_header header;
        const uint8_t *pixels = capture_output(descriptor, mode, &buffers, &header);

        if (!pixels)
            goto cleanup;
        for (source = 0; source < 3; source++) {
            if (buffers.pixels[source] && !write_raw(snapshot_prefix, source == 2 ? "ethernet" : source ? "camera" : "hdmi",
                    buffers.pixels[source], source ? PANGO_VIDEO_CAMERA_BYTES : PANGO_VIDEO_HDMI_BYTES))
                goto cleanup;
        }
        if (!write_ppm(snapshot_prefix, pixels, le32toh(header.width), le32toh(header.height)))
            goto cleanup;
        result = 0;
        goto cleanup;
    }
    listener = open_listener(bind_address, port);
    if (listener < 0) {
        perror("listen video socket");
        goto cleanup;
    }
    if (background) {
        int detached = detach_server(log_path);

        if (detached < 0) {
            perror("detach video server");
            goto cleanup;
        }
        if (detached > 0) {
            result = 0;
            goto cleanup;
        }
    }
#ifdef HAVE_LANE_STYLE
    if (lane_model) {
        if (lane_async_mode) {
            buffers.lane_async = lane_async_open(lane_runtime, lane_model, lane_contract, (unsigned)lane_max_age_ms);
            if (!buffers.lane_async) goto cleanup;
        } else {
            buffers.lane = lane_rknn_open(lane_runtime, lane_model, lane_contract);
            if (!buffers.lane) goto cleanup;
        }
    }
#endif
#ifdef HAVE_JUNCTION_DEMO
    buffers.junction = junction_demo_open(demo_runtime, demo_model, demo_contract);
    if (!buffers.junction) goto cleanup;
#endif
    /* Both inference workers consume immutable copies of the original HDMI frame. */
#ifdef HAVE_ROAD_OBJECTS
    if (object_model) {
        buffers.objects = road_object_async_open(lane_runtime, object_model, object_contract, (unsigned)object_max_age_ms);
        if (!buffers.objects) goto cleanup;
    }
#endif
    /* Sync receiver starts after daemonization; source pixels are observed before drawing. */
#ifdef HAVE_FRONT_P1
    if (crosswalk_model) {
        buffers.crosswalk = cw_open(lane_runtime, crosswalk_model, crosswalk_contract);
        if (!buffers.crosswalk) goto cleanup;
    }
#endif
#ifdef HAVE_SYNC_SESSION
    if(sync_state) { buffers.sync=sync_capture_open(sync_state); if(!buffers.sync) goto cleanup; }
    else buffers.detection_enabled=true;
#endif
    printf("Multivideo listening on %s:%lu mode=%u version=%s send_timeout_ms=%lu profile=%s overlap_verify=%s; "
           "composition runs on RK3568\n", bind_address, port, mode,
           MULTIVIDEO_SERVER_VERSION, send_timeout_ms, profiling ? "on" : "off",
           buffers.overlap_verify ? "on" : "off");
    while (!stopping) {
        int client = accept(listener, NULL, NULL);
        int enabled = 1;
        unsigned long connection_frames = 0;
        struct timeval send_timeout = {
            .tv_sec = (time_t)(send_timeout_ms / 1000),
            .tv_usec = (suseconds_t)((send_timeout_ms % 1000) * 1000)
        };

        if (client < 0) {
            if (errno == EINTR)
                continue;
            perror("accept video client");
            goto cleanup;
        }
        if (setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled)))
            perror("set TCP_NODELAY");
        if (setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &send_timeout, sizeof(send_timeout))) {
            perror("set video send timeout");
            close(client);
            continue;
        }
        printf("Client connected: connection=%lu send_timeout_ms=%lu\n",
               ++connections, send_timeout_ms);
        while (!stopping) {
            struct pango_video_header header;
            uint64_t frame_started = profile_now_ns();
            const uint8_t *pixels = capture_output(descriptor, mode, &buffers, &header);
            uint64_t send_started;
            uint64_t finished;

            if (!pixels) {
                fprintf(stderr, "Capture stopped: connection=%lu completed=%lu; closing server\n",
                        connections, connection_frames);
                close(client);
                goto cleanup;
            }
            send_started = profile_now_ns();
            if (!send_exact(client, &header, sizeof(header), "header", le64toh(header.sequence)) ||
                !send_exact(client, pixels, le32toh(header.pixel_bytes), "pixels", le64toh(header.sequence)))
                break;
            finished = profile_now_ns();
            sent_frames++;
            connection_frames++;
            if (profiling && (connection_frames <= 3 || connection_frames % 10 == 0))
                log_video_profile(&buffers, &header, finished - send_started, finished - frame_started);
            if (sent_frames <= 3 || sent_frames % 10 == 0)
                printf("sent=%lu source=%u size=%ux%u hdmi_seq=%" PRIu64 " camera_seq=%" PRIu64 " ethernet_capture_seq=%" PRIu64 " ethernet_available=%u\n",
                       sent_frames, le32toh(header.source), le32toh(header.width), le32toh(header.height),
                       le64toh(buffers.headers[0].sequence), le64toh(buffers.headers[1].sequence),
                       le64toh(buffers.headers[2].sequence), le32toh(buffers.headers[2].magic) == PANGO_VIDEO_MAGIC);
            if (count && sent_frames >= count)
                stopping = 1;
        }
        close(client);
        printf("Client closed: connection=%lu completed=%lu total_sent=%lu; %s\n",
               connections, connection_frames, sent_frames,
               stopping ? "server stopping" : "waiting for next client");
    }
    result = 0;
cleanup:
#ifdef HAVE_JUNCTION_DEMO
    junction_demo_close(buffers.junction);
#endif
#ifdef HAVE_FRONT_P1
    cw_close(buffers.crosswalk);
#endif
#ifdef HAVE_SYNC_SESSION
    sync_capture_close(buffers.sync);
#endif
#ifdef HAVE_LANE_STYLE
#ifdef HAVE_ROAD_OBJECTS
    road_object_async_close(buffers.objects);
#endif
    lane_async_close(buffers.lane_async);
    lane_rknn_close(buffers.lane);
#endif
    if (listener >= 0) close(listener);
    if (descriptor >= 0) close(descriptor);
#ifdef HAVE_FRONT_ASSIST
    if (buffers.drive_socket >= 0) close(buffers.drive_socket);
#endif
    free(buffers.pixels[0]);
    free(buffers.pixels[1]);
    free(buffers.pixels[2]);
    free(buffers.mosaic);
    return result;
}
