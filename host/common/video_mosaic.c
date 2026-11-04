#include <string.h>

#include "video_mosaic.h"

void video_mosaic_three_rgb565(uint8_t *output, const uint8_t *hdmi,
                               const uint8_t *camera, const uint8_t *ethernet)
{
    unsigned int row, column, tile;
    memset(output, 0, 1280U * 480U * 2U);
    for (row = 0; row < 360; row++)
        for (column = 0; column < 640; column++) {
            unsigned int destination = ((row + 60U) * 1280U + column) * 2U;
            unsigned int source = (row * 3U * 1920U + column * 3U) * 2U;
            memcpy(output + destination, hdmi + source, 2);
        }
    for (tile = 0; tile < 2; tile++) {
        const uint8_t *pixels = tile ? ethernet : camera;
        for (row = 0; row < 240; row++)
            for (column = 0; column < 320; column++) {
                unsigned int destination = ((row + tile * 240U) * 1280U + 800U + column) * 2U;
                unsigned int source = (row * 2U * 640U + column * 2U) * 2U;
                memcpy(output + destination, pixels + source, 2);
            }
    }
}

void video_mosaic_rgb565(uint8_t *output, const uint8_t *hdmi, const uint8_t *camera)
{
    unsigned int row;
    unsigned int column;

    memset(output, 0, 1280U * 480U * 2U);
    for (row = 0; row < 480; row++)
        memcpy(output + (row * 1280U + 640U) * 2U, camera + row * 640U * 2U, 640U * 2U);
    for (row = 0; row < 360; row++) {
        for (column = 0; column < 640; column++) {
            unsigned int destination = ((row + 60U) * 1280U + column) * 2U;
            unsigned int source = (row * 3U * 1920U + column * 3U) * 2U;

            output[destination] = hdmi[source];
            output[destination + 1] = hdmi[source + 1];
        }
    }
}
