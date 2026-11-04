#ifndef PCIE_MULTIVIDEO_PROTOCOL_H
#define PCIE_MULTIVIDEO_PROTOCOL_H

#ifdef __KERNEL__
#include <linux/ioctl.h>
#include <linux/types.h>
typedef __le16 pango_video_u16;
typedef __le32 pango_video_u32;
typedef __le64 pango_video_u64;
#else
#include <stdint.h>
#include <sys/ioctl.h>
typedef uint16_t pango_video_u16;
typedef uint32_t pango_video_u32;
typedef uint64_t pango_video_u64;
#endif

#define PANGO_VIDEO_MAGIC 0x31465650U
#define PANGO_VIDEO_VERSION 1
#define PANGO_VIDEO_HEADER_BYTES 64
#define PANGO_VIDEO_HDMI 0
#define PANGO_VIDEO_CAMERA 1
#define PANGO_VIDEO_ETHERNET 2
#define PANGO_VIDEO_BOTH 2
#define PANGO_VIDEO_MODE_ETHERNET 3
#define PANGO_VIDEO_ALL 4
#define PANGO_VIDEO_MOSAIC 0xffffffffU
#define PANGO_VIDEO_TRIPLE 0xfffffffeU
#define PANGO_VIDEO_RGB565LE 2
#define PANGO_VIDEO_HDMI_BYTES (1920U * 1080U * 2U)
#define PANGO_VIDEO_CAMERA_BYTES (640U * 480U * 2U)
#define PANGO_VIDEO_ETHERNET_BYTES PANGO_VIDEO_CAMERA_BYTES
#define PANGO_VIDEO_MOSAIC_BYTES (1280U * 480U * 2U)
#define PANGO_VIDEO_SET_MODE _IOW('P', 0x31, pango_video_u32)

struct pango_video_header {
    pango_video_u32 magic;
    pango_video_u16 version;
    pango_video_u16 header_bytes;
    pango_video_u32 source;
    pango_video_u32 format;
    pango_video_u32 width;
    pango_video_u32 height;
    pango_video_u32 pixel_bytes;
    pango_video_u32 checksum;
    pango_video_u64 sequence;
    pango_video_u64 timestamp_ns;
    pango_video_u64 hdmi_timestamp_ns;
    pango_video_u64 camera_timestamp_ns;
} __attribute__((packed));

#endif
