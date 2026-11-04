#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char path[512];
    volatile uint32_t *registers;
    int descriptor, sample;
    unsigned long count = 10;
    char *end;
    if (argc < 2 || argc > 3 || strchr(argv[1], '/')) {
        fprintf(stderr, "Usage: %s PCI_BDF [samples]\n", argv[0]);
        return 1;
    }
    if (argc == 3) {
        errno = 0;
        count = strtoul(argv[2], &end, 10);
        if (errno || !*argv[2] || *end || count < 1 || count > 3600)
            return 1;
    }
    if (snprintf(path, sizeof(path), "/sys/bus/pci/devices/%s/resource0", argv[1]) >= (int)sizeof(path))
        return 1;
    descriptor = open(path, O_RDONLY);
    if (descriptor < 0) { perror(path); return 1; }
    registers = mmap(NULL, 4096, PROT_READ, MAP_SHARED, descriptor, 0);
    close(descriptor);
    if (registers == MAP_FAILED) { perror("mmap BAR0"); return 1; }
    if (registers[0x150 / 4] != 0x4d565331 || !(registers[0x154 / 4] & 4) ||
        registers[0x12c / 4] != 614400) {
        fprintf(stderr, "Ethernet video DMA capability absent; program the third-source bitstream\n");
        munmap((void *)registers, 4096);
        return 1;
    }
    puts("Ethernet input status v1; read-only BAR0; no DMA requests");
    for (sample = 0; sample < (int)count; sample++) {
        struct timespec interval = {.tv_sec = 1, .tv_nsec = 0};
        uint32_t flags, frame_id, complete, abandoned, after;
        unsigned int retry;
        for (retry = 0; retry < 32; retry++) {
            complete = registers[0x168 / 4];
            flags = registers[0x160 / 4];
            frame_id = registers[0x164 / 4];
            abandoned = registers[0x16c / 4];
            after = registers[0x168 / 4];
            if (complete == after)
                break;
        }
        if (retry == 32) {
            fprintf(stderr, "Ethernet status changed during every read; retry the diagnostic\n");
            munmap((void *)registers, 4096);
            return 1;
        }
        printf("sample=%d available=%u dma_locked=%u input_frame_id=%u complete_frames=%u abandoned_frames=%u\n",
               sample, flags & 1, (flags >> 1) & 1, frame_id, complete, abandoned);
        fflush(stdout);
        if (sample + 1 < (int)count)
            while (nanosleep(&interval, &interval) && errno == EINTR) {}
    }
    munmap((void *)registers, 4096);
    return 0;
}
