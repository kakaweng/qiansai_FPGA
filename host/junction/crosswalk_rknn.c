#define _POSIX_C_SOURCE 200809L
#include "crosswalk_rknn.h"
#ifndef CROSSWALK_CORE_ONLY
#include "rknn_api.h"
#include "lane_style_tensor.h"
#include "rknn_scheduler.h"
#include <dlfcn.h>
#include <inttypes.h>
#include <pthread.h>
#endif
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CW_SOURCE_BYTES (1920U * 1080U * 2U)

struct cw_filter { unsigned first, count; int32_t *weights; };
struct cw_preprocessor {
    unsigned width, height, resized_width, resized_height;
    struct cw_filter *columns, *rows;
    uint8_t *horizontal;
};

#ifndef CROSSWALK_CORE_ONLY
struct crosswalk_rknn {
    void *library;
    rknn_context context;
    int (*init)(rknn_context *, void *, uint32_t, uint32_t, rknn_init_extend *);
    int (*destroy)(rknn_context);
    int (*query)(rknn_context, rknn_query_cmd, void *, uint32_t);
    int (*inputs_set)(rknn_context, uint32_t, rknn_input *);
    int (*run)(rknn_context, rknn_run_extend *);
    int (*outputs_get)(rknn_context, uint32_t, rknn_output *, rknn_output_extend *);
    int (*outputs_release)(rknn_context, uint32_t, rknn_output *);
    struct cw_preprocessor *preprocessor;
    uint8_t *pending_pixels, *processing_pixels, *rgb;
    pthread_mutex_t mutex;
    pthread_cond_t ready;
    pthread_t worker;
    unsigned width, height;
    uint64_t sequence, timestamp_ns, submitted_sequence, submitted_timestamp_ns;
    uint64_t epoch, completed, replaced, last_started;
    struct crosswalk_observation published;
    bool nhwc, stopping, pending, submitted, has_result, threaded;
};

static uint64_t cw_now_ns(void)
{
    struct timespec stamp;
    if (clock_gettime(CLOCK_MONOTONIC, &stamp)) return 0;
    return (uint64_t)stamp.tv_sec * UINT64_C(1000000000) + (uint64_t)stamp.tv_nsec;
}
#endif

static void cw_geometry(unsigned width, unsigned height, unsigned *resized_width, unsigned *resized_height)
{
    double scale = fmin((double)CW_INPUT_WIDTH / width, (double)CW_INPUT_HEIGHT / height);
    *resized_width = (unsigned)nearbyint(width * scale);
    *resized_height = (unsigned)nearbyint(height * scale);
}

static void cw_free_filters(struct cw_filter *filters, unsigned count)
{
    unsigned index;
    if (!filters) return;
    for (index = 0; index < count; index++) free(filters[index].weights);
    free(filters);
}

static struct cw_filter *cw_filters(unsigned input_size, unsigned output_size)
{
    struct cw_filter *filters = calloc(output_size, sizeof(*filters));
    double scale = (double)input_size / output_size, support = fmax(1.0, scale);
    unsigned index;
    if (!filters) return NULL;
    for (index = 0; index < output_size; index++) {
        double center = (index + .5) * scale, total = 0;
        int first = (int)(center - support + .5), last = (int)(center + support + .5), offset;
        if (first < 0) first = 0;
        if (last > (int)input_size) last = (int)input_size;
        filters[index].first = (unsigned)first; filters[index].count = (unsigned)(last - first);
        filters[index].weights = calloc((size_t)(last - first), sizeof(int32_t));
        if (!filters[index].weights) { cw_free_filters(filters, output_size); return NULL; }
        for (offset = first; offset < last; offset++) total += fmax(0, 1 - fabs((offset - center + .5) / support));
        for (offset = first; offset < last; offset++) {
            double weight = fmax(0, 1 - fabs((offset - center + .5) / support)) / total;
            filters[index].weights[offset - first] = (int32_t)(weight * 4194304 + .5);
        }
    }
    return filters;
}

struct cw_preprocessor *cw_preprocessor_create(void)
{ return calloc(1, sizeof(struct cw_preprocessor)); }

void cw_preprocessor_destroy(struct cw_preprocessor *processor)
{
    if (!processor) return;
    cw_free_filters(processor->columns, processor->resized_width);
    cw_free_filters(processor->rows, processor->resized_height);
    free(processor->horizontal); free(processor);
}

bool cw_preprocessor_prepare(struct cw_preprocessor *processor, const uint8_t *source,
                             unsigned width, unsigned height, uint8_t *rgb)
{
    unsigned resized_width, resized_height, left, top, row, column, channel;
    if (!processor || !source || !rgb || width < 4 || height < 4 || width > 1920 || height > 1080) return false;
    cw_geometry(width, height, &resized_width, &resized_height);
    if (processor->width != width || processor->height != height) {
        struct cw_filter *columns = cw_filters(width, resized_width), *rows = cw_filters(height, resized_height);
        uint8_t *horizontal = malloc((size_t)height * resized_width * 3);
        if (!columns || !rows || !horizontal) {
            cw_free_filters(columns, resized_width); cw_free_filters(rows, resized_height); free(horizontal); return false;
        }
        cw_free_filters(processor->columns, processor->resized_width);
        cw_free_filters(processor->rows, processor->resized_height); free(processor->horizontal);
        processor->columns = columns; processor->rows = rows; processor->horizontal = horizontal;
        processor->width = width; processor->height = height;
        processor->resized_width = resized_width; processor->resized_height = resized_height;
    }
    left = (CW_INPUT_WIDTH - resized_width) / 2; top = (CW_INPUT_HEIGHT - resized_height) / 2;
    memset(rgb, 114, CW_INPUT_PIXELS * 3);
    for (row = 0; row < height; row++) for (column = 0; column < resized_width; column++) {
        const struct cw_filter *filter = &processor->columns[column];
        int64_t sums[3] = {2097152, 2097152, 2097152};
        unsigned offset;
        for (offset = 0; offset < filter->count; offset++) {
            size_t at = ((size_t)row * width + filter->first + offset) * 2;
            uint16_t packed = (uint16_t)(source[at] | ((uint16_t)source[at + 1] << 8));
            int red = (packed >> 11) & 31, green = (packed >> 5) & 63, blue = packed & 31;
            int32_t weight = filter->weights[offset];
            sums[0] += ((red << 3) | (red >> 2)) * (int64_t)weight;
            sums[1] += ((green << 2) | (green >> 4)) * (int64_t)weight;
            sums[2] += ((blue << 3) | (blue >> 2)) * (int64_t)weight;
        }
        for (channel = 0; channel < 3; channel++)
            processor->horizontal[((size_t)row * resized_width + column) * 3 + channel] = (uint8_t)(sums[channel] >> 22);
    }
    for (row = 0; row < resized_height; row++) for (column = 0; column < resized_width; column++) {
        const struct cw_filter *filter = &processor->rows[row];
        for (channel = 0; channel < 3; channel++) {
            int64_t sum = 2097152;
            unsigned offset;
            for (offset = 0; offset < filter->count; offset++)
                sum += processor->horizontal[((size_t)(filter->first + offset) * resized_width + column) * 3 + channel] * (int64_t)filter->weights[offset];
            rgb[((size_t)(row + top) * CW_INPUT_WIDTH + column + left) * 3 + channel] = (uint8_t)(sum >> 22);
        }
    }
    return true;
}

bool cw_decode_logits(const float *logits, bool nhwc, unsigned width, unsigned height,
                      unsigned char mask[CW_MASK_BYTES])
{
    float *probability;
    unsigned resized_width, resized_height, left, top, row, column;
    size_t index;
    if (!logits || !mask || width < 4 || height < 4 || width > 1920 || height > 1080) return false;
    memset(mask, 0, CW_MASK_BYTES);
    probability = malloc(CW_INPUT_PIXELS * sizeof(*probability));
    if (!probability) return false;
    for (index = 0; index < CW_INPUT_PIXELS; index++) {
        float background = logits[nhwc ? index * 2 : index];
        float foreground = logits[nhwc ? index * 2 + 1 : CW_INPUT_PIXELS + index];
        double delta;
        if (!isfinite(background) || !isfinite(foreground)) { free(probability); return false; }
        delta = (double)foreground - background;
        probability[index] = (float)(delta >= 0 ? 1.0 / (1.0 + exp(-delta)) : exp(delta) / (1.0 + exp(delta)));
    }
    cw_geometry(width, height, &resized_width, &resized_height);
    left = (CW_INPUT_WIDTH - resized_width) / 2; top = (CW_INPUT_HEIGHT - resized_height) / 2;
    for (row = 0; row < CW_PLANE_HEIGHT; row++) {
        double source_y = fmax(0, fmin(resized_height - 1., (row + .5) * resized_height / CW_PLANE_HEIGHT - .5));
        unsigned low_y = (unsigned)source_y, high_y = low_y + 1 < resized_height ? low_y + 1 : low_y;
        double fraction_y = source_y - low_y;
        for (column = 0; column < CW_PLANE_WIDTH; column++) {
            double source_x = fmax(0, fmin(resized_width - 1., (column + .5) * resized_width / CW_PLANE_WIDTH - .5));
            unsigned low_x = (unsigned)source_x, high_x = low_x + 1 < resized_width ? low_x + 1 : low_x;
            double fraction_x = source_x - low_x;
            double upper = probability[(top + low_y) * CW_INPUT_WIDTH + left + low_x] * (1 - fraction_x) + probability[(top + low_y) * CW_INPUT_WIDTH + left + high_x] * fraction_x;
            double lower = probability[(top + high_y) * CW_INPUT_WIDTH + left + low_x] * (1 - fraction_x) + probability[(top + high_y) * CW_INPUT_WIDTH + left + high_x] * fraction_x;
            if (upper * (1 - fraction_y) + lower * fraction_y > .5) {
                index = (size_t)row * CW_PLANE_WIDTH + column;
                mask[index / 8] |= (unsigned char)(1U << (index % 8));
            }
        }
    }
    free(probability); return true;
}

#ifndef CROSSWALK_CORE_ONLY
static bool cw_line(FILE *stream, const char *expected)
{ char line[256]; return fgets(line, sizeof(line), stream) && !strcmp(line, expected); }

static uint8_t *cw_model(const char *path, const char *contract, uint32_t *length)
{
    FILE *stream = fopen(contract, "rb");
    uint8_t *data = NULL;
    uint64_t expected_size, expected_hash, hash = UINT64_C(14695981039346656037);
    size_t index;
    char line[256], extra;
    if (!stream) goto failed;
    if (!cw_line(stream, "crosswalk_rknn_contract_v1\n") ||
        !cw_line(stream, "model_version " CW_MODEL_VERSION "\n") ||
        !cw_line(stream, "onnx_sha256 6a7cded06c7279c21283619e31718102ed08ce3567d67c9845926140e94a50cc\n") ||
        !cw_line(stream, "epoch 11\n")) goto failed;
    if (!fgets(line, sizeof(line), stream) || sscanf(line, "rknn_bytes %" SCNu64 " %c", &expected_size, &extra) != 1 || !expected_size || expected_size > UINT32_MAX) goto failed;
    if (!fgets(line, sizeof(line), stream) || sscanf(line, "rknn_fnv1a64 %" SCNx64 " %c", &expected_hash, &extra) != 1) goto failed;
    if (!cw_line(stream, "labels background,crosswalk_zebra\n") ||
        !cw_line(stream, "input rgb_u8_nhwc_1x384x640x3\n") ||
        !cw_line(stream, "normalization embedded_imagenet\n") ||
        !cw_line(stream, "resize full_frame_letterbox_pil_bilinear_rgb114\n") ||
        !cw_line(stream, "output logits_1x2x384x640_layout_query\n") ||
        !cw_line(stream, "precision non_int8\n") ||
        !cw_line(stream, "decoder " CW_DECODER_VERSION "\n") || fgetc(stream) != EOF) goto failed;
    fclose(stream); stream = fopen(path, "rb");
    if (!stream) goto failed;
    data = malloc((size_t)expected_size);
    if (!data || fread(data, 1, (size_t)expected_size, stream) != expected_size || fgetc(stream) != EOF) goto failed;
    for (index = 0; index < expected_size; index++) { hash ^= data[index]; hash *= UINT64_C(1099511628211); }
    if (hash != expected_hash) goto failed;
    fclose(stream); *length = (uint32_t)expected_size; return data;
failed:
    fprintf(stderr, "Crosswalk model/contract validation failed: %s / %s\n", path, contract);
    if (stream) fclose(stream);
    free(data); return NULL;
}

static bool cw_symbol(void *library, const char *name, void *destination, size_t bytes)
{
    void *symbol;
    const char *error;
    dlerror(); symbol = dlsym(library, name); error = dlerror();
    if (error || !symbol || bytes != sizeof(symbol)) return false;
    memcpy(destination, &symbol, bytes); return true;
}

static bool cw_shape(const rknn_tensor_attr *attribute, unsigned channels)
{
    if (attribute->n_dims != 4 || attribute->dims[0] != 1 || attribute->n_elems != channels * CW_INPUT_PIXELS) return false;
    if (attribute->fmt == RKNN_TENSOR_NCHW) return attribute->dims[1] == channels && attribute->dims[2] == CW_INPUT_HEIGHT && attribute->dims[3] == CW_INPUT_WIDTH;
    if (attribute->fmt == RKNN_TENSOR_NHWC) return attribute->dims[1] == CW_INPUT_HEIGHT && attribute->dims[2] == CW_INPUT_WIDTH && attribute->dims[3] == channels;
    return false;
}

static void cw_print_tensor(const char *kind, const rknn_tensor_attr *attribute)
{
    unsigned index;
    printf("Crosswalk tensor %s: shape=[", kind);
    for (index = 0; index < attribute->n_dims && index < RKNN_MAX_DIMS; index++)
        printf("%s%u", index ? "," : "", attribute->dims[index]);
    printf("] elements=%u fmt=%s type=%s quantization=%s scale=%g zp=%d\n", attribute->n_elems,
        get_format_string(attribute->fmt), get_type_string(attribute->type),
        get_qnt_type_string(attribute->qnt_type), attribute->scale, attribute->zp);
}

static void cw_engine_close(struct crosswalk_rknn *crosswalk)
{
    if (crosswalk->context && crosswalk->destroy) crosswalk->destroy(crosswalk->context);
    if (crosswalk->library) dlclose(crosswalk->library);
    cw_preprocessor_destroy(crosswalk->preprocessor);
    free(crosswalk->pending_pixels); free(crosswalk->processing_pixels); free(crosswalk->rgb);
    free(crosswalk);
}

static struct crosswalk_rknn *cw_engine_open(const char *runtime, const char *model, const char *contract)
{
    struct crosswalk_rknn *crosswalk = calloc(1, sizeof(*crosswalk));
    uint8_t *data;
    uint32_t length = 0;
    rknn_input_output_num count = {0};
    rknn_tensor_attr input = {0}, output = {0};
    rknn_sdk_version sdk = {{0}, {0}};
    int status;
    if (!crosswalk) return NULL;
    data = cw_model(model, contract, &length);
    if (!data) goto failed;
    crosswalk->library = dlopen(runtime, RTLD_NOW | RTLD_LOCAL);
    if (!crosswalk->library) { fprintf(stderr, "Crosswalk dlopen: %s\n", dlerror()); goto failed; }
#define CW_LOAD(field, name) if (!cw_symbol(crosswalk->library, name, &crosswalk->field, sizeof(crosswalk->field))) goto failed
    CW_LOAD(init, "rknn_init"); CW_LOAD(destroy, "rknn_destroy"); CW_LOAD(query, "rknn_query");
    CW_LOAD(inputs_set, "rknn_inputs_set"); CW_LOAD(run, "rknn_run");
    CW_LOAD(outputs_get, "rknn_outputs_get"); CW_LOAD(outputs_release, "rknn_outputs_release");
#undef CW_LOAD
    status = crosswalk->init(&crosswalk->context, data, length, 0, NULL);
    if (status != RKNN_SUCC) { fprintf(stderr, "Crosswalk RKNN init failed: %d\n", status); goto failed; }
    free(data); data = NULL;
    crosswalk->query(crosswalk->context, RKNN_QUERY_SDK_VERSION, &sdk, sizeof(sdk));
    if (crosswalk->query(crosswalk->context, RKNN_QUERY_IN_OUT_NUM, &count, sizeof(count)) != RKNN_SUCC || count.n_input != 1 || count.n_output != 1) {
        fprintf(stderr, "Crosswalk RKNN expected one input and one output; received %u/%u\n", count.n_input, count.n_output); goto failed;
    }
    if (crosswalk->query(crosswalk->context, RKNN_QUERY_INPUT_ATTR, &input, sizeof(input)) != RKNN_SUCC ||
        crosswalk->query(crosswalk->context, RKNN_QUERY_OUTPUT_ATTR, &output, sizeof(output)) != RKNN_SUCC) {
        fprintf(stderr, "Crosswalk RKNN tensor attribute query failed\n"); goto failed;
    }
    cw_print_tensor("input", &input); cw_print_tensor("output", &output);
    if (!cw_shape(&input, 3) || !cw_shape(&output, 2) || !lane_tensor_floating(&input) || !lane_tensor_floating(&output)) {
        fprintf(stderr, "Crosswalk tensor mismatch: expected 1x384x640 RGB input, two floating-point raw-logit outputs, NONE or identity AFFINE; INT8 is unsupported\n"); goto failed;
    }
    if (input.qnt_type != RKNN_TENSOR_QNT_NONE || output.qnt_type != RKNN_TENSOR_QNT_NONE)
        puts("Crosswalk floating-point identity AFFINE metadata accepted: scale=1 zp=0; no INT8 decoder used");
    crosswalk->nhwc = output.fmt == RKNN_TENSOR_NHWC;
    crosswalk->preprocessor = cw_preprocessor_create();
    crosswalk->rgb = malloc(CW_INPUT_PIXELS * 3);
    crosswalk->pending_pixels = malloc(CW_SOURCE_BYTES); crosswalk->processing_pixels = malloc(CW_SOURCE_BYTES);
    if (!crosswalk->preprocessor || !crosswalk->rgb || !crosswalk->pending_pixels || !crosswalk->processing_pixels) goto failed;
    printf("Crosswalk model ready: model=%s epoch=11 input=%s output=%s precision=non_INT8 decoder=%s api=%s driver=%s\n",
        CW_MODEL_VERSION, get_format_string(input.fmt), get_format_string(output.fmt), CW_DECODER_VERSION, sdk.api_version, sdk.drv_version);
    return crosswalk;
failed:
    free(data); cw_engine_close(crosswalk); return NULL;
}

bool cw_inspect(const char *runtime, const char *model, const char *contract)
{
    struct crosswalk_rknn *crosswalk = cw_engine_open(runtime, model, contract);
    if (!crosswalk) return false;
    cw_engine_close(crosswalk); puts("CROSSWALK_INSPECT_PASS"); return true;
}

static bool cw_process(struct crosswalk_rknn *crosswalk, unsigned width, unsigned height,
                       struct crosswalk_observation *result)
{
    rknn_input input = {0};
    rknn_output output = {0};
    int status;
    bool success = false;
    if (!cw_preprocessor_prepare(crosswalk->preprocessor, crosswalk->processing_pixels, width, height, crosswalk->rgb)) {
        road_npu_unlock(); return false;
    }
    input.buf = crosswalk->rgb; input.size = CW_INPUT_PIXELS * 3;
    input.type = RKNN_TENSOR_UINT8; input.fmt = RKNN_TENSOR_NHWC;
    output.want_float = 1;
    status = crosswalk->inputs_set(crosswalk->context, 1, &input);
    if (status == RKNN_SUCC) status = crosswalk->run(crosswalk->context, NULL);
    if (status == RKNN_SUCC) status = crosswalk->outputs_get(crosswalk->context, 1, &output, NULL);
    road_npu_unlock();
    if (status != RKNN_SUCC) return false;
    if (output.buf && output.size == CW_INPUT_PIXELS * 2 * sizeof(float))
        success = cw_decode_logits(output.buf, crosswalk->nhwc, width, height, result->mask);
    if (crosswalk->outputs_release(crosswalk->context, 1, &output) != RKNN_SUCC) success = false;
    if (!success) memset(result->mask, 0, CW_MASK_BYTES);
    return success;
}

static void cw_delay(struct crosswalk_rknn *crosswalk, uint64_t delay_ns)
{
    struct timespec deadline;
    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += (time_t)(delay_ns / UINT64_C(1000000000));
    deadline.tv_nsec += (long)(delay_ns % UINT64_C(1000000000));
    if (deadline.tv_nsec >= 1000000000L) { deadline.tv_sec++; deadline.tv_nsec -= 1000000000L; }
    pthread_cond_timedwait(&crosswalk->ready, &crosswalk->mutex, &deadline);
}

static void *cw_worker(void *context)
{
    struct crosswalk_rknn *crosswalk = context;
    for (;;) {
        struct crosswalk_observation result = {0};
        uint64_t now, epoch, started;
        unsigned width, height;
        uint8_t *swap;
        pthread_mutex_lock(&crosswalk->mutex);
        for (;;) {
            if (crosswalk->stopping) { pthread_mutex_unlock(&crosswalk->mutex); return NULL; }
            if (!crosswalk->pending) { pthread_cond_wait(&crosswalk->ready, &crosswalk->mutex); continue; }
            now = cw_now_ns();
            if (crosswalk->last_started && now >= crosswalk->last_started && now - crosswalk->last_started < CW_MIN_INTERVAL_MS * UINT64_C(1000000)) {
                cw_delay(crosswalk, CW_MIN_INTERVAL_MS * UINT64_C(1000000) - (now - crosswalk->last_started)); continue;
            }
            break;
        }
        pthread_mutex_unlock(&crosswalk->mutex);
        road_npu_acquire(ROAD_NPU_CROSSWALK);
        pthread_mutex_lock(&crosswalk->mutex);
        if (crosswalk->stopping || !crosswalk->pending) {
            bool stop = crosswalk->stopping;
            pthread_mutex_unlock(&crosswalk->mutex); road_npu_unlock();
            if (stop) return NULL;
            continue;
        }
        started = cw_now_ns(); crosswalk->last_started = started;
        epoch = crosswalk->epoch; width = crosswalk->width; height = crosswalk->height;
        result.width = width; result.height = height;
        result.source_seq = crosswalk->sequence; result.source_ms = crosswalk->timestamp_ns / UINT64_C(1000000);
        swap = crosswalk->processing_pixels; crosswalk->processing_pixels = crosswalk->pending_pixels; crosswalk->pending_pixels = swap;
        crosswalk->pending = false;
        pthread_mutex_unlock(&crosswalk->mutex);
        result.valid = cw_process(crosswalk, width, height, &result);
        pthread_mutex_lock(&crosswalk->mutex);
        if (epoch == crosswalk->epoch) { crosswalk->published = result; crosswalk->has_result = true; }
        crosswalk->completed++;
        if (crosswalk->completed <= 3 || crosswalk->completed % 10 == 0)
            printf("crosswalk_async completed=%" PRIu64 " source_seq=%" PRIu64 " replaced=%" PRIu64 " valid=%u total_ms=%.2f age_ms=%.2f\n",
                crosswalk->completed, result.source_seq, crosswalk->replaced, (unsigned)result.valid,
                (cw_now_ns() - started) / 1e6, cw_now_ns() / 1e6 - result.source_ms);
        pthread_mutex_unlock(&crosswalk->mutex);
    }
}

struct crosswalk_rknn *cw_open(const char *runtime, const char *model, const char *contract)
{
    struct crosswalk_rknn *crosswalk = cw_engine_open(runtime, model, contract);
    if (!crosswalk) return NULL;
    if (pthread_mutex_init(&crosswalk->mutex, NULL)) { cw_engine_close(crosswalk); return NULL; }
    if (pthread_cond_init(&crosswalk->ready, NULL)) { pthread_mutex_destroy(&crosswalk->mutex); cw_engine_close(crosswalk); return NULL; }
    if (pthread_create(&crosswalk->worker, NULL, cw_worker, crosswalk)) {
        pthread_cond_destroy(&crosswalk->ready); pthread_mutex_destroy(&crosswalk->mutex); cw_engine_close(crosswalk); return NULL;
    }
    crosswalk->threaded = true;
    printf("Crosswalk async ready: HDMI only; latest frame; interval_ms=%u max_age_ms=%u\n", CW_MIN_INTERVAL_MS, CW_MAX_AGE_MS);
    return crosswalk;
}

void cw_reset(struct crosswalk_rknn *crosswalk)
{
    if (!crosswalk) return;
    pthread_mutex_lock(&crosswalk->mutex); crosswalk->epoch++;
    crosswalk->pending = crosswalk->submitted = crosswalk->has_result = false;
    memset(&crosswalk->published, 0, sizeof(crosswalk->published));
    pthread_cond_signal(&crosswalk->ready); pthread_mutex_unlock(&crosswalk->mutex);
}

bool cw_submit(struct crosswalk_rknn *crosswalk, const uint8_t *rgb565, unsigned width, unsigned height,
               uint64_t sequence, uint64_t timestamp_ns)
{
    size_t bytes = (size_t)width * height * 2;
    if (!crosswalk || !rgb565 || width < 4 || height < 4 || width > 1920 || height > 1080 || bytes > CW_SOURCE_BYTES || !timestamp_ns) return false;
    pthread_mutex_lock(&crosswalk->mutex);
    if (crosswalk->stopping) { pthread_mutex_unlock(&crosswalk->mutex); return false; }
    if (crosswalk->submitted && sequence == crosswalk->submitted_sequence && timestamp_ns == crosswalk->submitted_timestamp_ns) {
        pthread_mutex_unlock(&crosswalk->mutex); return true;
    }
    if (crosswalk->submitted && (sequence <= crosswalk->submitted_sequence || timestamp_ns <= crosswalk->submitted_timestamp_ns ||
        width != crosswalk->width || height != crosswalk->height)) { crosswalk->epoch++; crosswalk->has_result = false; }
    memcpy(crosswalk->pending_pixels, rgb565, bytes);
    crosswalk->width = width; crosswalk->height = height; crosswalk->sequence = sequence; crosswalk->timestamp_ns = timestamp_ns;
    crosswalk->submitted_sequence = sequence; crosswalk->submitted_timestamp_ns = timestamp_ns; crosswalk->submitted = true;
    if (crosswalk->pending) crosswalk->replaced++;
    crosswalk->pending = true; pthread_cond_signal(&crosswalk->ready); pthread_mutex_unlock(&crosswalk->mutex);
    return true;
}

bool cw_snapshot(struct crosswalk_rknn *crosswalk, uint64_t now, struct crosswalk_observation *out)
{
    bool has_result;
    uint64_t now_ms = now / UINT64_C(1000000);
    if (!crosswalk || !out) return false;
    memset(out, 0, sizeof(*out));
    pthread_mutex_lock(&crosswalk->mutex);
    has_result = crosswalk->has_result;
    if (has_result) *out = crosswalk->published;
    pthread_mutex_unlock(&crosswalk->mutex);
    if (!has_result || out->source_ms > now_ms || now_ms - out->source_ms > CW_MAX_AGE_MS) {
        memset(out, 0, sizeof(*out)); return false;
    }
    return true;
}

void cw_close(struct crosswalk_rknn *crosswalk)
{
    if (!crosswalk) return;
    if (crosswalk->threaded) {
        pthread_mutex_lock(&crosswalk->mutex); crosswalk->stopping = true;
        pthread_cond_signal(&crosswalk->ready); pthread_mutex_unlock(&crosswalk->mutex);
        pthread_join(crosswalk->worker, NULL);
        pthread_cond_destroy(&crosswalk->ready); pthread_mutex_destroy(&crosswalk->mutex);
    }
    cw_engine_close(crosswalk);
}
#endif
