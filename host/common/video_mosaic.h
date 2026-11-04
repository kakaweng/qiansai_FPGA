#ifndef VIDEO_MOSAIC_H
#define VIDEO_MOSAIC_H

#include <stdint.h>

void video_mosaic_three_rgb565(uint8_t *output, const uint8_t *hdmi,
                               const uint8_t *camera, const uint8_t *ethernet);

void video_mosaic_rgb565(uint8_t *output, const uint8_t *hdmi, const uint8_t *camera);

#endif
