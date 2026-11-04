#ifndef CROSSWALK_RKNN_H
#define CROSSWALK_RKNN_H

#include "crosswalk_observation.h"

#define CW_INPUT_WIDTH 640U
#define CW_INPUT_HEIGHT 384U
#define CW_INPUT_PIXELS (CW_INPUT_WIDTH * CW_INPUT_HEIGHT)
#define CW_MIN_INTERVAL_MS 700U
#define CW_DECODER_VERSION "crosswalk-zebra-bitmask-v1"

struct crosswalk_rknn;
struct cw_preprocessor;

struct cw_preprocessor *cw_preprocessor_create(void);
void cw_preprocessor_destroy(struct cw_preprocessor *processor);
bool cw_preprocessor_prepare(struct cw_preprocessor *processor, const uint8_t *source,
                             unsigned width, unsigned height, uint8_t *rgb);
bool cw_decode_logits(const float *logits, bool nhwc, unsigned width, unsigned height,
                      unsigned char mask[CW_MASK_BYTES]);

struct crosswalk_rknn *cw_open(const char *runtime, const char *model, const char *contract);
bool cw_inspect(const char *runtime, const char *model, const char *contract);
void cw_reset(struct crosswalk_rknn *crosswalk);
bool cw_submit(struct crosswalk_rknn *crosswalk, const uint8_t *rgb565, unsigned width, unsigned height,
               uint64_t sequence, uint64_t timestamp_ns);
bool cw_snapshot(struct crosswalk_rknn *crosswalk, uint64_t now_ns, struct crosswalk_observation *out);
void cw_close(struct crosswalk_rknn *crosswalk);

#endif
