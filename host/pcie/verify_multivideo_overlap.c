#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

#ifdef NDEBUG
#undef NDEBUG
#endif

#include <assert.h>
#include <limits.h>
#include <pthread.h>
#include <unistd.h>

static ssize_t checked_read(int descriptor, void *buffer, size_t bytes);
static int checked_create(pthread_t *thread, const pthread_attr_t *attributes,
                          void *(*entry)(void *), void *argument);
static int checked_join(pthread_t thread, void **result);

#define read checked_read
#define pthread_create checked_create
#define pthread_join checked_join
#define main multivideo_program_main
#include "pcie_multivideo_server.c"
#undef main
#undef pthread_join
#undef pthread_create
#undef read

static pthread_mutex_t gate_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t gate_condition = PTHREAD_COND_INITIALIZER;
static bool gated;
static bool worker_started;
static bool camera_entered;
static bool worker_completed;
static bool worker_joined;
static bool reject_create;
static void *(*worker_entry)(void *);
static const char *fixture_directory;

static void *gated_worker(void *argument)
{
    void *result;

    assert(!pthread_mutex_lock(&gate_mutex));
    worker_started = true;
    assert(!pthread_cond_broadcast(&gate_condition));
    while (gated && !camera_entered)
        assert(!pthread_cond_wait(&gate_condition, &gate_mutex));
    assert(!pthread_mutex_unlock(&gate_mutex));
    result = worker_entry(argument);
    worker_completed = true;
    return result;
}

static int checked_create(pthread_t *thread, const pthread_attr_t *attributes,
                          void *(*entry)(void *), void *argument)
{
    if (reject_create)
        return EAGAIN;
    worker_entry = entry;
    return pthread_create(thread, attributes, gated_worker, argument);
}

static int checked_join(pthread_t thread, void **result)
{
    int error = pthread_join(thread, result);

    if (!error)
        worker_joined = true;
    return error;
}

static ssize_t checked_read(int descriptor, void *buffer, size_t bytes)
{
    if (gated && bytes == PANGO_VIDEO_HEADER_BYTES &&
        lseek(descriptor, 0, SEEK_CUR) == PANGO_VIDEO_HEADER_BYTES + PANGO_VIDEO_HDMI_BYTES) {
        assert(!pthread_mutex_lock(&gate_mutex));
        while (!worker_started)
            assert(!pthread_cond_wait(&gate_condition, &gate_mutex));
        camera_entered = true;
        assert(!pthread_cond_broadcast(&gate_condition));
        assert(!pthread_mutex_unlock(&gate_mutex));
    }
    return read(descriptor, buffer, bytes);
}

enum fixture_case {
    FRAME_GOOD,
    HDMI_CORRUPT,
    CAMERA_CORRUPT,
    CAMERA_TRUNCATED,
    CAMERA_BAD_HEADER,
    WORKER_UNAVAILABLE
};

static FILE *open_fixture_stream(void)
{
    char path[PATH_MAX];
    FILE *stream;
    int descriptor;
    int length = snprintf(path, sizeof(path), "%s/multivideo_overlap_XXXXXX", fixture_directory);

    if (length < 0 || (size_t)length >= sizeof(path)) {
        fprintf(stderr, "Fixture directory path too long: %s\n", fixture_directory);
        return NULL;
    }
    descriptor = mkstemp(path);
    if (descriptor < 0) {
        fprintf(stderr, "Create fixture file failed: directory=%s errno=%d (%s)\n",
                fixture_directory, errno, strerror(errno));
        return NULL;
    }
    if (unlink(path)) {
        fprintf(stderr, "Unlink fixture file failed: path=%s errno=%d (%s)\n",
                path, errno, strerror(errno));
        close(descriptor);
        return NULL;
    }
    stream = fdopen(descriptor, "w+");
    if (!stream) {
        perror("Open fixture stream");
        close(descriptor);
    }
    return stream;
}

static FILE *make_fixture(struct video_buffers *buffers, enum fixture_case fixture)
{
    FILE *stream = open_fixture_stream();
    unsigned int source;

    if (!stream)
        return NULL;
    for (source = 0; source < 2; source++) {
        uint32_t bytes = source ? PANGO_VIDEO_CAMERA_BYTES : PANGO_VIDEO_HDMI_BYTES;
        struct pango_video_header header = {
            .magic = htole32(PANGO_VIDEO_MAGIC),
            .version = htole16(PANGO_VIDEO_VERSION),
            .header_bytes = htole16(PANGO_VIDEO_HEADER_BYTES),
            .source = htole32(source),
            .format = htole32(PANGO_VIDEO_RGB565LE),
            .width = htole32(source ? 640 : 1920),
            .height = htole32(source ? 480 : 1080),
            .pixel_bytes = htole32(bytes),
            .checksum = htole32(checksum_bytes(buffers->pixels[source], bytes)),
            .sequence = htole64(20 + source),
            .timestamp_ns = htole64(1000000 + source * 1000)
        };
        size_t written;
        bool corrupt = (source == 0 && fixture == HDMI_CORRUPT) ||
                       (source == 1 && fixture == CAMERA_CORRUPT);

        if (source && fixture == CAMERA_TRUNCATED)
            break;
        if (source && fixture == CAMERA_BAD_HEADER)
            header.width = htole32(639);
        if (fwrite(&header, 1, sizeof(header), stream) != sizeof(header))
            goto io_failed;
        if (corrupt)
            buffers->pixels[source][bytes / 2] ^= 1;
        written = fwrite(buffers->pixels[source], 1, bytes, stream);
        if (corrupt)
            buffers->pixels[source][bytes / 2] ^= 1;
        if (written != bytes)
            goto io_failed;
    }
    if (fseek(stream, 0, SEEK_SET))
        goto io_failed;
    return stream;
io_failed:
    perror("Write or rewind fixture file");
    fclose(stream);
    return NULL;
}

int main(int argc, char **argv)
{
    struct video_buffers buffers = {0};
    struct pango_video_header reference_header = {0};
    uint8_t *reference = NULL;
    enum fixture_case fixture;
    unsigned int overlapped;
    unsigned int source;
    int result = EXIT_FAILURE;

    if (argc > 2 || (argc == 2 && !argv[1][0])) {
        fprintf(stderr, "Usage: %s [fixture-directory=/data/local/tmp]\n", argv[0]);
        return 2;
    }
    fixture_directory = argc == 2 ? argv[1] : "/data/local/tmp";
    reference = malloc(PANGO_VIDEO_MOSAIC_BYTES);
    alarm(30);
    printf("Overlap fixture version=overlap-fixture-v2 temp_dir=%s\n", fixture_directory);
    puts("Offline fixture tests: no PCIe access; checksum, EOF and worker errors below are injected.");
    buffers.pixels[0] = malloc(PANGO_VIDEO_HDMI_BYTES);
    buffers.pixels[1] = malloc(PANGO_VIDEO_CAMERA_BYTES);
    buffers.mosaic = malloc(PANGO_VIDEO_MOSAIC_BYTES);
    assert(reference && buffers.pixels[0] && buffers.pixels[1] && buffers.mosaic);
    profiling = true;
    for (overlapped = 0; overlapped < 2; overlapped++) {
        for (fixture = FRAME_GOOD; fixture <= WORKER_UNAVAILABLE; fixture++) {
            struct pango_video_header header = {0};
            const uint8_t *pixels;
            FILE *stream;

            if (!overlapped && fixture == WORKER_UNAVAILABLE)
                continue;
            for (source = 0; source < 2; source++) {
                size_t bytes = source ? PANGO_VIDEO_CAMERA_BYTES : PANGO_VIDEO_HDMI_BYTES;
                size_t index;

                for (index = 0; index < bytes; index++)
                    buffers.pixels[source][index] = (uint8_t)(index * 37U + (index >> 9) + source);
            }
            stream = make_fixture(&buffers, fixture);
            if (!stream)
                goto cleanup;
            buffers.pairs = 0;
            buffers.overlap_verify = overlapped;
            gated = overlapped;
            worker_started = camera_entered = worker_completed = worker_joined = false;
            reject_create = fixture == WORKER_UNAVAILABLE;
            pixels = capture_output(fileno(stream), PANGO_VIDEO_BOTH, &buffers, &header);
            assert((pixels != NULL) == (fixture == FRAME_GOOD));
            if (overlapped && fixture != WORKER_UNAVAILABLE) {
                assert(camera_entered && worker_completed && worker_joined);
            }
            if (fixture == FRAME_GOOD) {
                assert(le32toh(header.checksum) == checksum_bytes(pixels, PANGO_VIDEO_MOSAIC_BYTES));
                if (!overlapped) {
                    memcpy(reference, pixels, PANGO_VIDEO_MOSAIC_BYTES);
                    reference_header = header;
                } else {
                    assert(!memcmp(reference, pixels, PANGO_VIDEO_MOSAIC_BYTES));
                    assert(!memcmp(&reference_header, &header, sizeof(header)));
                }
            }
            assert(!fclose(stream));
            printf("PASS: overlap=%u fixture=%u integrity and worker lifetime\n", overlapped, fixture);
        }
    }
    puts("PASS: serial/overlapped output equality, damaged frames, truncated capture and worker failure");
    result = EXIT_SUCCESS;
cleanup:
    free(reference);
    free(buffers.pixels[0]);
    free(buffers.pixels[1]);
    free(buffers.mosaic);
    return result;
}
