#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <unistd.h>
#include <stdint.h>
#include <sys/ioctl.h>
static ssize_t fixture_read(int descriptor, void *buffer, size_t bytes);
static int fixture_ioctl(int descriptor, unsigned long request, ...);
#define read fixture_read
#define ioctl fixture_ioctl
#define main multivideo_program_main
#include "pcie_multivideo_server.c"
#undef main
#undef ioctl
#undef read
static off_t missing_offset = -1;
static unsigned int mode_resets;
static const char *fixture_directory;
static ssize_t fixture_read(int descriptor, void *buffer, size_t bytes)
{
    if (bytes == PANGO_VIDEO_HEADER_BYTES && lseek(descriptor, 0, SEEK_CUR) == missing_offset) {
        errno = EAGAIN;
        return -1;
    }
    return read(descriptor, buffer, bytes);
}
static int fixture_ioctl(int descriptor, unsigned long request, ...)
{
    (void)descriptor;
    assert(request == PANGO_VIDEO_SET_MODE);
    mode_resets++;
    return 0;
}
static FILE *make_fixture(struct video_buffers *buffers, unsigned int failure)
{
    char path[1024];
    int descriptor;
    FILE *stream;
    unsigned int source;
    assert(snprintf(path,sizeof(path),"%s/ethernet_fixture_XXXXXX",fixture_directory)<(int)sizeof(path));
    descriptor=mkstemp(path);
    assert(descriptor>=0 && !unlink(path));
    stream=fdopen(descriptor,"w+");
    assert(stream);
    missing_offset = -1;
    for (source=0; source<3; source++) {
        uint32_t bytes=source?PANGO_VIDEO_CAMERA_BYTES:PANGO_VIDEO_HDMI_BYTES;
        struct pango_video_header header={
            .magic=htole32(PANGO_VIDEO_MAGIC), .version=htole16(PANGO_VIDEO_VERSION),
            .header_bytes=htole16(PANGO_VIDEO_HEADER_BYTES), .source=htole32(source),
            .format=htole32(PANGO_VIDEO_RGB565LE), .width=htole32(source?640:1920),
            .height=htole32(source?480:1080), .pixel_bytes=htole32(bytes),
            .checksum=htole32(checksum_bytes(buffers->pixels[source],bytes)),
            .sequence=htole64(20+source), .timestamp_ns=htole64(1000000+source*1000)
        };
        if (source==2 && failure==1) {
            missing_offset=ftell(stream);
            break;
        }
        if (source==2 && failure==2) header.checksum ^= htole32(1);
        assert(fwrite(&header,1,sizeof(header),stream)==sizeof(header));
        if (source==2 && failure==3) bytes/=2;
        assert(fwrite(buffers->pixels[source],1,bytes,stream)==bytes);
    }
    rewind(stream);
    return stream;
}
int main(int argc, char **argv)
{
    struct video_buffers buffers={0};
    unsigned int source, overlap, failure;
    uint8_t *expected=malloc(PANGO_VIDEO_MOSAIC_BYTES);
    assert(argc<=2);
    fixture_directory=argc==2?argv[1]:"/data/local/tmp";
    buffers.mosaic=malloc(PANGO_VIDEO_MOSAIC_BYTES);
    assert(expected && buffers.mosaic);
    for(source=0; source<3; source++) {
        size_t bytes=source?PANGO_VIDEO_CAMERA_BYTES:PANGO_VIDEO_HDMI_BYTES, i;
        buffers.pixels[source]=malloc(bytes);
        assert(buffers.pixels[source]);
        for(i=0;i<bytes;i++) buffers.pixels[source][i]=(uint8_t)(i*37+(i>>9)+source);
    }
    for(overlap=0; overlap<2; overlap++) for(failure=0;failure<4;failure++) {
        FILE *stream=make_fixture(&buffers,failure);
        struct pango_video_header header;
        const uint8_t *output;
        buffers.overlap_verify=overlap;
        mode_resets=0;
        if(failure==1) memset(&buffers.headers[2],0,sizeof(buffers.headers[2]));
        output=capture_output(fileno(stream),PANGO_VIDEO_ALL,&buffers,&header);
        assert((output!=NULL)==(failure<2));
        if(output) {
            video_mosaic_three_rgb565(expected,buffers.pixels[0],buffers.pixels[1],buffers.pixels[2]);
            assert(!memcmp(expected,output,PANGO_VIDEO_MOSAIC_BYTES));
            assert(le32toh(header.source)==PANGO_VIDEO_TRIPLE && valid_stream_header(&header));
            assert(le32toh(header.checksum)==checksum_bytes(output,PANGO_VIDEO_MOSAIC_BYTES));
            assert(mode_resets==(failure==1));
            if(failure==1) assert(!le32toh(buffers.headers[2].magic));
        }
        assert(!fclose(stream));
        printf("PASS: triple overlap=%u fixture=%u integrity/fallback\n",overlap,failure);
    }
    for(source=0;source<3;source++) free(buffers.pixels[source]);
    free(buffers.mosaic); free(expected);
    puts("PASS: triple capture, Ethernet absent/corrupt/truncated, protocol and serial/overlap layout");
    return 0;
}
