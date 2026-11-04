#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define INFO_OFFSET 0x100U
#define CAMERA_STATUS_OFFSET 0x130U
#define SNAPSHOT_OFFSET 0x140U
#define MAP_BYTES 0x150U
#define STATUS_SIGNATURE 0xc101U
#define PROGRAM_VERSION "video-source-rate-v2"

struct source_descriptor {
    uint32_t source;
    uint32_t geometry;
    uint32_t format;
    uint32_t bytes;
};

struct camera_snapshot {
    uint32_t flags;
    uint32_t good;
    uint32_t bad;
    uint32_t pixels;
    uint32_t lines;
    uint32_t pixel_sum;
    uint32_t endpoint_pixels;
    uint32_t sequence;
};

static uint32_t read_register(volatile uint8_t *registers, size_t offset)
{
    uint32_t value = *(volatile uint32_t *)(registers + offset);
#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    value = __builtin_bswap32(value);
#endif
    return value;
}

static bool read_snapshot(volatile uint8_t *registers, struct camera_snapshot *snapshot)
{
    unsigned int attempt;

    for (attempt = 0; attempt < 64; attempt++) {
        uint32_t sequence = read_register(registers, SNAPSHOT_OFFSET + 12);

        snapshot->flags = read_register(registers, CAMERA_STATUS_OFFSET);
        snapshot->good = read_register(registers, CAMERA_STATUS_OFFSET + 4);
        snapshot->bad = read_register(registers, CAMERA_STATUS_OFFSET + 8);
        snapshot->pixels = read_register(registers, CAMERA_STATUS_OFFSET + 12);
        snapshot->lines = read_register(registers, SNAPSHOT_OFFSET);
        snapshot->pixel_sum = read_register(registers, SNAPSHOT_OFFSET + 4);
        snapshot->endpoint_pixels = read_register(registers, SNAPSHOT_OFFSET + 8);
        snapshot->sequence = read_register(registers, SNAPSHOT_OFFSET + 12);
        if (sequence == snapshot->sequence && snapshot->good + snapshot->bad == sequence)
            return true;
    }
    return false;
}

static bool parse_number(const char *text, unsigned long maximum, unsigned long *value)
{
    char *end;

    errno = 0;
    *value = strtoul(text, &end, 10);
    return !errno && text[0] >= '0' && text[0] <= '9' && !*end && *value && *value <= maximum;
}

static bool valid_bdf(const char *bdf)
{
    unsigned int domain;
    unsigned int bus;
    unsigned int device;
    unsigned int function;
    int consumed = 0;

    return strlen(bdf) == 12 &&
        sscanf(bdf, "%4x:%2x:%2x.%1x%n", &domain, &bus, &device, &function, &consumed) == 4 &&
        consumed == 12 && domain <= 0xffff && bus <= 0xff && device <= 0x1f && function <= 7;
}

static bool print_descriptors(volatile uint8_t *registers)
{
    static const char *const names[] = {"HDMI", "OV5640", "Ethernet"};
    unsigned int index;

    for (index = 0; index < 3; index++) {
        size_t offset = INFO_OFFSET + index * 16;
        struct source_descriptor descriptor;
        uint32_t width;
        uint32_t height;

        descriptor.source = read_register(registers, offset);
        descriptor.geometry = read_register(registers, offset + 4);
        descriptor.format = read_register(registers, offset + 8);
        descriptor.bytes = read_register(registers, offset + 12);
        width = descriptor.geometry & 0xffffU;
        height = descriptor.geometry >> 16;
        printf("%-8s source=%u size=%ux%u format=%u bytes=%u\n", names[index],
               descriptor.source, width, height, descriptor.format, descriptor.bytes);
        if (descriptor.source != index)
            return false;
        if (index == 0 && (width != 1920 || height != 1080 ||
            !((descriptor.format == 1 && descriptor.bytes == 2073600) ||
              (descriptor.format == 2 && descriptor.bytes == 4147200))))
            return false;
        if (index == 1 && (width != 640 || height != 480 || descriptor.format != 2 || descriptor.bytes != 614400))
            return false;
        if (index == 2 && (descriptor.geometry || descriptor.format || descriptor.bytes))
            return false;
    }
    return true;
}

int main(int argc, char **argv)
{
    unsigned long samples = 5;
    unsigned long interval_ms = 1000;
    unsigned long sample_index;
    char resource_path[160];
    struct stat resource_stat;
    struct camera_snapshot snapshot;
    struct timespec delay;
    struct timespec first_sample_time = {0};
    struct timespec sample_time = {0};
    volatile uint8_t *registers;
    uint32_t initial_good = 0;
    uint32_t initial_bad = 0;
    uint32_t previous_good = 0;
    uint32_t previous_bad = 0;
    bool passed = false;
    int descriptor;
    int result = 1;

    if (argc == 2 && !strcmp(argv[1], "--version")) {
        puts(PROGRAM_VERSION);
        return 0;
    }
    if (argc < 2 || argc > 4 || !valid_bdf(argv[1]) ||
        (argc >= 3 && !parse_number(argv[2], 1000000, &samples)) ||
        (argc >= 4 && !parse_number(argv[3], 60000, &interval_ms))) {
        fprintf(stderr, "Usage: %s PCI_BDF [samples=5] [interval_ms=1000]\n", argv[0]);
        return 1;
    }
    snprintf(resource_path, sizeof(resource_path), "/sys/bus/pci/devices/%s/resource0", argv[1]);
    descriptor = open(resource_path, O_RDONLY | O_SYNC);
    if (descriptor < 0) {
        perror(resource_path);
        return 1;
    }
    if (fstat(descriptor, &resource_stat) < 0 || resource_stat.st_size < MAP_BYTES) {
        fprintf(stderr, "BAR0 is unavailable or smaller than 0x150 bytes\n");
        close(descriptor);
        return 1;
    }
    registers = mmap(NULL, MAP_BYTES, PROT_READ, MAP_SHARED, descriptor, 0);
    close(descriptor);
    if (registers == MAP_FAILED) {
        perror("mmap BAR0 (run with root privileges)");
        return 1;
    }
    if (!print_descriptors(registers)) {
        fprintf(stderr, "Source metadata mismatch: FPGA source descriptors do not match this diagnostic\n");
        goto cleanup;
    }
    printf("Camera input diagnostics version=%s; read-only BAR0, no pixel DMA requests.\n",
           PROGRAM_VERSION);
    delay.tv_sec = (time_t)(interval_ms / 1000);
    delay.tv_nsec = (long)(interval_ms % 1000) * 1000000L;
    for (sample_index = 0; sample_index < samples; sample_index++) {
        if (!read_snapshot(registers, &snapshot)) {
            fprintf(stderr, "Camera snapshot changed during read; retry\n");
            goto cleanup;
        }
        if (clock_gettime(CLOCK_MONOTONIC, &sample_time) < 0) {
            perror("clock_gettime");
            goto cleanup;
        }
        if (snapshot.flags && snapshot.flags >> 16 != STATUS_SIGNATURE) {
            fprintf(stderr, "Unknown camera status version: 0x%08" PRIx32 "\n", snapshot.flags);
            goto cleanup;
        }
        if (sample_index == 0) {
            initial_good = snapshot.good;
            initial_bad = snapshot.bad;
            first_sample_time = sample_time;
        } else if (snapshot.good < previous_good || snapshot.bad < previous_bad) {
            fprintf(stderr, "Camera counters decreased (reset or wrap); repeat the input rate measurement\n");
            goto cleanup;
        }
        previous_good = snapshot.good;
        previous_bad = snapshot.bad;
        printf("sample=%lu init=%u seen=%u valid=%u active=%u errors=0x%02x "
               "good=%u bad=%u pixels=%u lines=%u line_bytes=%u "
               "pixel_sum=0x%08" PRIx32 " first=0x%04x last=0x%04x seq=%u\n",
               sample_index, snapshot.flags & 1U, (snapshot.flags >> 1) & 1U,
               (snapshot.flags >> 2) & 1U, (snapshot.flags >> 3) & 1U,
               (snapshot.flags >> 8) & 0xffU, snapshot.good, snapshot.bad,
               snapshot.pixels, snapshot.lines & 0xffffU, snapshot.lines >> 16,
               snapshot.pixel_sum, snapshot.endpoint_pixels & 0xffffU,
               snapshot.endpoint_pixels >> 16, snapshot.sequence);
        fflush(stdout);
        passed = (snapshot.flags & 7U) == 7U && !(snapshot.flags & 0xff00U) &&
                 snapshot.pixels == 307200 && snapshot.lines == ((1280U << 16) | 480U) &&
                 snapshot.good > 0 && (samples == 1 || snapshot.good != initial_good);
        if (sample_index + 1 < samples) {
            struct timespec remaining = delay;

            while (nanosleep(&remaining, &remaining) < 0) {
                if (errno != EINTR) {
                    perror("nanosleep");
                    goto cleanup;
                }
            }
        }
    }
    if (samples > 1) {
        double elapsed_seconds = (double)(sample_time.tv_sec - first_sample_time.tv_sec) +
            (double)(sample_time.tv_nsec - first_sample_time.tv_nsec) / 1000000000.0;
        uint32_t good_frames = snapshot.good - initial_good;
        uint32_t bad_frames = snapshot.bad - initial_bad;

        if (elapsed_seconds <= 0) {
            fprintf(stderr, "Invalid input rate measurement duration\n");
            goto cleanup;
        }
        printf("INPUT_RATE samples=%lu elapsed_seconds=%.3f good_frames=%" PRIu32
               " bad_frames=%" PRIu32 " good_fps=%.3f",
               samples, elapsed_seconds, good_frames, bad_frames,
               (double)good_frames / elapsed_seconds);
        if (good_frames)
            printf(" mean_good_period_ms=%.3f\n", elapsed_seconds * 1000.0 / good_frames);
        else
            printf(" mean_good_period_ms=unavailable\n");
    }
    if (passed && snapshot.bad == initial_bad) {
        printf("PASS: new complete 640x480 RGB565 frames arrived without malformed frames.\n");
        result = 0;
    } else {
        fprintf(stderr, "FAIL: check camera initialization/PCLK/VSYNC/HREF and error flags.\n");
        result = 2;
    }

cleanup:
    munmap((void *)registers, MAP_BYTES);
    return result;
}
