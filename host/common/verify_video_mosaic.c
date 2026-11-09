#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "video_mosaic.h"

static uint16_t pattern(unsigned int column, unsigned int row, unsigned int salt)
{
    return (uint16_t)(((column * 37U) ^ (row * 173U) ^ salt) | 1U);
}

static void store_pixel(uint8_t *buffer, size_t index, uint16_t value)
{
    buffer[index * 2] = (uint8_t)value;
    buffer[index * 2 + 1] = (uint8_t)(value >> 8);
}

static uint16_t load_pixel(const uint8_t *buffer, size_t index)
{
    return (uint16_t)(buffer[index * 2] | ((uint16_t)buffer[index * 2 + 1] << 8));
}

int main(void)
{
    const size_t output_bytes = 1280U * 480U * 2U;
    uint8_t *hdmi = malloc(1920U * 1080U * 2U);
    uint8_t *camera = malloc(640U * 480U * 2U);
    uint8_t *guarded_output = malloc(output_bytes + 64);
    uint8_t *output;
    unsigned int row;
    unsigned int column;
    int result = 1;

    if (!hdmi || !camera || !guarded_output)
        goto cleanup;
    output = guarded_output + 32;
    memset(guarded_output, 0xa5, output_bytes + 64);
    for (row = 0; row < 1080; row++)
        for (column = 0; column < 1920; column++)
            store_pixel(hdmi, row * 1920U + column, pattern(column, row, 0xf800));
    for (row = 0; row < 480; row++)
        for (column = 0; column < 640; column++)
            store_pixel(camera, row * 640U + column, pattern(column, row, 0x001f));
    video_mosaic_rgb565(output, hdmi, camera);
    for (row = 0; row < 480; row++) {
        for (column = 0; column < 1280; column++) {
            uint16_t expected = 0;
            uint16_t actual = load_pixel(output, row * 1280U + column);

            if (column >= 640)
                expected = pattern(column - 640, row, 0x001f);
            else if (row >= 60 && row < 420)
                expected = pattern(column * 3, (row - 60) * 3, 0xf800);
            if (actual != expected) {
                fprintf(stderr, "Mosaic mismatch at (%u,%u): %04x != %04x\n",
                        column, row, actual, expected);
                goto cleanup;
            }
        }
    }
    for (column = 0; column < 32; column++) {
        if (guarded_output[column] != 0xa5 || output[output_bytes + column] != 0xa5) {
            fprintf(stderr, "Mosaic wrote outside its output buffer\n");
            goto cleanup;
        }
    }
    puts("PASS: all 1280x480 RGB565 mosaic pixels, HDMI aspect ratio, black bars and output guards");
    result = 0;
cleanup:
    free(hdmi);
    free(camera);
    free(guarded_output);
    return result;
}
