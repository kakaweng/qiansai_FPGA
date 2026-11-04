#define _POSIX_C_SOURCE 200809L
#include "lane_style_rknn.h"
#include "rknn_api.h"
#include "lane_style_tensor.h"
#include <dlfcn.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef HAVE_ROAD_OBJECTS
#include "rknn_scheduler.h"
#endif

struct lane_rknn {
    void *library;
    rknn_context context;
    int (*init)(rknn_context *, void *, uint32_t, uint32_t, rknn_init_extend *);
    int (*destroy)(rknn_context);
    int (*query)(rknn_context, rknn_query_cmd, void *, uint32_t);
    int (*inputs_set)(rknn_context, uint32_t, rknn_input *);
    int (*run)(rknn_context, rknn_run_extend *);
    int (*outputs_get)(rknn_context, uint32_t, rknn_output *, rknn_output_extend *);
    int (*outputs_release)(rknn_context, uint32_t, rknn_output *);
    struct lane_style_core core;
    struct lane_result result;
    struct lane_preprocessor *preprocessor;
    uint8_t rgb[LANE_MAP_PIXELS * 3];
    unsigned source_width, source_height;
    bool nhwc, valid;
    unsigned long frames;
};

static uint64_t now_ns(void)
{
    struct timespec timestamp;
    if (clock_gettime(CLOCK_MONOTONIC, &timestamp)) return 0;
    return (uint64_t)timestamp.tv_sec * UINT64_C(1000000000) + (uint64_t)timestamp.tv_nsec;
}

void lane_rknn_reset(struct lane_rknn *lane)
{ if (lane) { lane_style_reset(&lane->core); lane->valid = false; } }

static bool read_line(FILE *stream, const char *expected)
{
    char line[256];
    return fgets(line, sizeof(line), stream) && !strcmp(line, expected);
}

static uint8_t *read_model(const char *path, const char *contract, uint32_t *length)
{
    FILE *stream = NULL;
    uint8_t *data = NULL;
    uint64_t expected_size, expected_hash, hash = UINT64_C(14695981039346656037);
    size_t index;
    char line[256], extra;
    stream = fopen(contract, "rb");
    if (!stream) goto failed;
    if (!read_line(stream, "lane_style_rknn_contract_v1\n") ||
        !read_line(stream, "model_version bdd_lraspp_v1_20261013\n") ||
        !read_line(stream, "onnx_sha256 0e364fa2350f969f1943b6d83925f9e1119d209565ee17e49aef1f3b5ff21af7\n") ||
        !read_line(stream, "epoch 24\n")) goto failed;
    if (!fgets(line, sizeof(line), stream) || sscanf(line, "rknn_bytes %" SCNu64 " %c", &expected_size, &extra) != 1 || !expected_size || expected_size > UINT32_MAX) goto failed;
    if (!fgets(line, sizeof(line), stream) || sscanf(line, "rknn_fnv1a64 %" SCNx64 " %c", &expected_hash, &extra) != 1) goto failed;
    if (!read_line(stream, "labels background,lane_solid,lane_dashed,other_marking\n") ||
        !read_line(stream, "input rgb_u8_nhwc_1x384x640x3\n") || !read_line(stream, "normalization embedded_imagenet\n") ||
        !read_line(stream, "crop round_height_times_0.35_pil_bilinear\n") || !read_line(stream, "output logits_1x4x384x640_layout_query\n") ||
        !read_line(stream, "precision non_int8\n") || !read_line(stream, "decoder " LANE_STYLE_CORE_VERSION "\n") || fgetc(stream) != EOF) goto failed;
    fclose(stream); stream = fopen(path, "rb");
    if (!stream) goto failed;
    data = malloc((size_t)expected_size);
    if (!data || fread(data, 1, (size_t)expected_size, stream) != expected_size || fgetc(stream) != EOF) goto failed;
    for (index = 0; index < expected_size; index++) { hash ^= data[index]; hash *= UINT64_C(1099511628211); }
    if (hash != expected_hash) goto failed;
    fclose(stream); *length = (uint32_t)expected_size; return data;
failed:
    fprintf(stderr, "Lane model/contract validation failed: %s / %s\n", path, contract);
    if (stream) fclose(stream);
    free(data); return NULL;
}

static bool load_symbol(void *library, const char *name, void *destination, size_t bytes)
{
    void *symbol;
    const char *error;
    dlerror(); symbol = dlsym(library, name); error = dlerror();
    if (error || !symbol || bytes != sizeof(symbol)) { fprintf(stderr, "Lane RKNN symbol %s: %s\n", name, error ? error : "invalid pointer"); return false; }
    memcpy(destination, &symbol, bytes); return true;
}

static bool tensor_shape(const rknn_tensor_attr *attribute, unsigned channels)
{
    if (attribute->n_dims != 4 || attribute->dims[0] != 1 || attribute->n_elems != channels * LANE_MAP_PIXELS) return false;
    if (attribute->fmt == RKNN_TENSOR_NCHW) return attribute->dims[1] == channels && attribute->dims[2] == LANE_INPUT_HEIGHT && attribute->dims[3] == LANE_INPUT_WIDTH;
    if (attribute->fmt == RKNN_TENSOR_NHWC) return attribute->dims[1] == LANE_INPUT_HEIGHT && attribute->dims[2] == LANE_INPUT_WIDTH && attribute->dims[3] == channels;
    return false;
}

static void print_tensor(const char *kind, const rknn_tensor_attr *attribute)
{
    uint32_t index;
    printf("Lane tensor %s: index=%u name=%.*s n_dims=%u shape=[", kind, attribute->index,
           (int)sizeof(attribute->name), attribute->name, attribute->n_dims);
    for (index = 0; index < attribute->n_dims && index < RKNN_MAX_DIMS; index++)
        printf("%s%u", index ? "," : "", attribute->dims[index]);
    printf("] elements=%u bytes=%u stride_bytes=%u fmt=%s(%d) type=%s(%d) qnt=%s(%d) scale=%g zp=%d\n",
           attribute->n_elems, attribute->size, attribute->size_with_stride, get_format_string(attribute->fmt),
           (int)attribute->fmt, get_type_string(attribute->type), (int)attribute->type,
           get_qnt_type_string(attribute->qnt_type), (int)attribute->qnt_type, attribute->scale, attribute->zp);
}

static bool query_attribute(struct lane_rknn *lane, rknn_query_cmd command, const char *kind, rknn_tensor_attr *attribute)
{
    int status = lane->query(lane->context, command, attribute, sizeof(*attribute));
    if (status != RKNN_SUCC) { fprintf(stderr, "Lane RKNN %s query failed: status=%d\n", kind, status); return false; }
    print_tensor(kind, attribute);
    return true;
}

struct lane_rknn *lane_rknn_open(const char *runtime, const char *model, const char *contract)
{
    struct lane_rknn *lane = calloc(1, sizeof(*lane));
    uint8_t *data = NULL;
    uint32_t bytes = 0;
    rknn_input_output_num count = {0};
    rknn_tensor_attr input = {0}, output = {0};
    rknn_sdk_version sdk = {{0}, {0}};
    int status;
    if (!lane) return NULL;
    data = read_model(model, contract, &bytes);
    if (!data) goto failed;
    lane->library = dlopen(runtime, RTLD_NOW | RTLD_LOCAL);
    if (!lane->library) { fprintf(stderr, "Lane RKNN dlopen: %s\n", dlerror()); goto failed; }
#define LOAD_API(field, name) if (!load_symbol(lane->library, name, &lane->field, sizeof(lane->field))) goto failed
    LOAD_API(init, "rknn_init"); LOAD_API(destroy, "rknn_destroy"); LOAD_API(query, "rknn_query");
    LOAD_API(inputs_set, "rknn_inputs_set"); LOAD_API(run, "rknn_run");
    LOAD_API(outputs_get, "rknn_outputs_get"); LOAD_API(outputs_release, "rknn_outputs_release");
#undef LOAD_API
    status = lane->init(&lane->context, data, bytes, 0, NULL);
    if (status != RKNN_SUCC) { fprintf(stderr, "Lane RKNN init failed: status=%d\n", status); goto failed; }
    free(data); data = NULL;
    status = lane->query(lane->context, RKNN_QUERY_SDK_VERSION, &sdk, sizeof(sdk));
    printf("Lane RKNN initialized: diagnostic=tensor-details-v3 api=%s driver=%s sdk_query_status=%d attr_bytes=%zu\n", sdk.api_version, sdk.drv_version, status, sizeof(input));
    status = lane->query(lane->context, RKNN_QUERY_IN_OUT_NUM, &count, sizeof(count));
    if (status != RKNN_SUCC) { fprintf(stderr, "Lane RKNN tensor count query failed: status=%d\n", status); goto failed; }
    printf("Lane RKNN tensor count: inputs=%u outputs=%u\n", count.n_input, count.n_output);
    if (count.n_input != 1 || count.n_output != 1) { fprintf(stderr, "Lane RKNN expected exactly one input and one output\n"); goto failed; }
    if (!query_attribute(lane, RKNN_QUERY_INPUT_ATTR, "input", &input) || !query_attribute(lane, RKNN_QUERY_OUTPUT_ATTR, "output", &output)) goto failed;
    if (!tensor_shape(&input, 3)) { fprintf(stderr, "Lane RKNN input mismatch: expected NCHW [1,3,384,640] or NHWC [1,384,640,3]\n"); goto failed; }
    if (!tensor_shape(&output, 4)) { fprintf(stderr, "Lane RKNN output mismatch: expected NCHW [1,4,384,640] or NHWC [1,384,640,4]\n"); goto failed; }
    if (!lane_tensor_floating(&input) || !lane_tensor_floating(&output)) {
        fprintf(stderr, "Lane RKNN precision mismatch: expected FP16/FP32 with NONE or identity AFFINE (scale=1 zp=0) metadata\n"); goto failed;
    }
    if (input.qnt_type != RKNN_TENSOR_QNT_NONE || output.qnt_type != RKNN_TENSOR_QNT_NONE)
        puts("Lane RKNN floating-point identity AFFINE metadata accepted: scale=1 zp=0; no INT8 decoder used");
    lane->nhwc = output.fmt == RKNN_TENSOR_NHWC;
    lane->preprocessor = lane_preprocessor_create();
    if (!lane->preprocessor) goto failed;
#ifdef HAVE_FRONT_P1
    lane->core.analysis_gap_ms=1200;
    puts("Lane cadence: analysis_gap_ms=1200; source_gap_ms=500; confirmation_frames=3; original hold/expiry");
#endif
    printf("Lane model ready: model=bdd_lraspp_v1_20261013 epoch=24 decoder=%s input=%s output=%s precision=non_INT8 api=%s driver=%s\n",
           LANE_STYLE_CORE_VERSION, get_format_string(input.fmt), get_format_string(output.fmt), sdk.api_version, sdk.drv_version);
    return lane;
failed:
    free(data); lane_rknn_close(lane); return NULL;
}

void lane_rknn_close(struct lane_rknn *lane)
{
    if (!lane) return;
    if (lane->context && lane->destroy) lane->destroy(lane->context);
    if (lane->library) dlclose(lane->library);
    lane_preprocessor_destroy(lane->preprocessor);
    free(lane);
}

static bool lane_process(struct lane_rknn *lane, const uint8_t *source, unsigned width, unsigned height, uint64_t sequence, uint64_t timestamp_ns, bool reserved)
{
    uint64_t started = now_ns(), prepared, inferred, decoded;
    rknn_input input = {0};
    rknn_output output = {0};
    bool success = false;
    int status;
    if (!lane) {
#ifdef HAVE_ROAD_OBJECTS
        if (reserved) road_npu_unlock();
#endif
        return false;
    }
    lane->valid = false;
    if (!lane_preprocessor_prepare(lane->preprocessor, source, width, height, lane->rgb)) goto failed;
    prepared = now_ns();
    input.buf = lane->rgb; input.size = sizeof(lane->rgb); input.type = RKNN_TENSOR_UINT8; input.fmt = RKNN_TENSOR_NHWC;
    output.want_float = 1;
#ifdef HAVE_ROAD_OBJECTS
    if (!reserved) road_npu_lock();
#endif
    status = lane->inputs_set(lane->context, 1, &input);
    if (status == RKNN_SUCC) status = lane->run(lane->context, NULL);
    if (status == RKNN_SUCC) status = lane->outputs_get(lane->context, 1, &output, NULL);
#ifdef HAVE_ROAD_OBJECTS
    road_npu_unlock();
#endif
    reserved = false;
    if (status != RKNN_SUCC) goto failed;
    inferred = now_ns();
    if (output.buf && output.size == LANE_MAP_PIXELS * 4 * sizeof(float))
        success = lane_style_decode_logits(&lane->core, output.buf, lane->nhwc);
    status = lane->outputs_release(lane->context, 1, &output);
    if (!success || status != RKNN_SUCC || !lane_style_locate(&lane->core, width, height, sequence, timestamp_ns, &lane->result)) goto failed;
    decoded = now_ns(); lane->valid = true; lane->source_width = width; lane->source_height = height;
    lane->frames++;
    if (lane->frames <= 3 || lane->frames % 10 == 0)
        printf("lane hdmi_seq=%" PRIu64 " timestamp_ns=%" PRIu64 " prepare_ms=%.2f inference_io_ms=%.2f decode_ms=%.2f total_ms=%.2f left=%s%s right=%s%s corridor=%u\n",
               sequence, timestamp_ns, (prepared - started) / 1e6, (inferred - prepared) / 1e6, (decoded - inferred) / 1e6, (decoded - started) / 1e6,
               lane_style_type_name(lane->result.sides[0].type), lane->result.sides[0].held ? "_HOLD" : "",
               lane_style_type_name(lane->result.sides[1].type), lane->result.sides[1].held ? "_HOLD" : "", lane->result.corridor_confirmed);
    {
        const struct lane_diagnostics *diag = &lane->result.diagnostics;
        const struct lane_side *left = &lane->result.sides[0], *right = &lane->result.sides[1];
        float left_style = left->boundary_index < 0 ? 0 : lane->result.boundaries[left->boundary_index].style_fraction;
        float right_style = right->boundary_index < 0 ? 0 : lane->result.boundaries[right->boundary_index].style_fraction;
        printf("lane_diag hdmi_seq=%" PRIu64 " input_gap_ms=%.2f reset=%s raw=%u,%u supported=%u,%u runs=%u,%u paths=%u rejected=%u candidates=%u,%u boundaries=%u conflicts=%u "
               "left=%s left_observed=%s left_reason=%s left_count=%u left_score=%.3f left_style=%.3f "
               "right=%s right_observed=%s right_reason=%s right_count=%u right_score=%.3f right_style=%.3f corridor_geometry=%u corridor_count=%u\n",
               sequence, diag->input_gap_ms, lane_style_reset_name(diag->reset), diag->raw_pixels[0], diag->raw_pixels[1],
               diag->supported_pixels[0], diag->supported_pixels[1], diag->runs[0], diag->runs[1], diag->paths, diag->rejected_geometry,
               diag->candidates[0], diag->candidates[1], lane->result.boundary_count, diag->conflicts,
               lane_style_type_name(left->type), lane_style_type_name(left->observed), lane_style_reason_name(left->reason), left->confirmations, left->confidence, left_style,
               lane_style_type_name(right->type), lane_style_type_name(right->observed), lane_style_reason_name(right->reason), right->confirmations, right->confidence, right_style,
               diag->corridor_geometry_valid, diag->corridor_confirmations);
    }
    return true;
failed:
#ifdef HAVE_ROAD_OBJECTS
    if (reserved) road_npu_unlock();
#else
    (void)reserved;
#endif
    fprintf(stderr, "Lane inference failed at HDMI sequence=%" PRIu64 "; dropping frame and clearing history\n", sequence);
    lane_style_reset(&lane->core); return false;
}

bool lane_rknn_process(struct lane_rknn *lane, const uint8_t *source, unsigned width, unsigned height, uint64_t sequence, uint64_t timestamp_ns)
{ return lane_process(lane, source, width, height, sequence, timestamp_ns, false); }

bool lane_rknn_process_reserved(struct lane_rknn *lane, const uint8_t *source, unsigned width, unsigned height, uint64_t sequence, uint64_t timestamp_ns)
{ return lane_process(lane, source, width, height, sequence, timestamp_ns, true); }

void lane_rknn_draw(struct lane_rknn *lane, uint8_t *output, unsigned width, unsigned height, unsigned left, unsigned top, unsigned tile_width, unsigned tile_height)
{
    if (lane && lane->valid) lane_style_draw_rgb565(&lane->core, &lane->result, output, width, height, left, top, tile_width, tile_height, lane->source_width, lane->source_height);
    if (lane && lane->valid) lane_style_draw_reasons_rgb565(&lane->result, output, width, height, left, top, tile_width, tile_height);
}

bool lane_rknn_snapshot(const struct lane_rknn *lane, struct lane_style_view *view)
{
    if (!lane || !view || !lane->valid) return false;
    memcpy(view->classes, lane->core.classes, sizeof(view->classes));
    view->result = lane->result; view->width = lane->source_width; view->height = lane->source_height;
    return true;
}
