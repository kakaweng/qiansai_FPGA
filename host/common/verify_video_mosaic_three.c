#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "video_mosaic.h"

int main(void)
{
    uint16_t *hdmi = malloc(1920U*1080U*2U);
    uint16_t *camera = malloc(640U*480U*2U);
    uint16_t *ethernet = calloc(640U*480U, 2U);
    uint16_t *output = malloc(1280U*480U*2U);
    unsigned int x, y, trial;
    assert(hdmi && camera && ethernet && output);
    for (y=0; y<1080; y++) for (x=0; x<1920; x++) hdmi[y*1920+x]=(uint16_t)(x*7+y*11+1);
    for (y=0; y<480; y++) for (x=0; x<640; x++) camera[y*640+x]=(uint16_t)(x*13+y*17+2);
    for (trial=0; trial<2; trial++) {
        if (trial) for (y=0; y<480; y++) for (x=0; x<640; x++) ethernet[y*640+x]=(uint16_t)(x*19+y*23+3);
        video_mosaic_three_rgb565((uint8_t *)output,(uint8_t *)hdmi,(uint8_t *)camera,(uint8_t *)ethernet);
        for(y=0; y<480; y++) for(x=0; x<1280; x++) {
            uint16_t expected=0;
            if(x<640 && y>=60 && y<420) expected=hdmi[(y-60)*3*1920+x*3];
            else if(x>=800 && x<1120) expected=(y<240?camera:ethernet)[(y%240)*2*640+(x-800)*2];
            assert(output[y*1280+x]==expected);
        }
    }
    free(hdmi); free(camera); free(ethernet); free(output);
    puts("PASS: every triple-canvas pixel, HDMI/camera/Ethernet scaling, black borders and missing Ethernet black tile");
    return 0;
}
