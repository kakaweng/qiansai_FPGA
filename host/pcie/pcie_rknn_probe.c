#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <float.h>
#include <inttypes.h>
#include <math.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include "rknn_api.h"

#define FRAME_WIDTH 1920U
#define FRAME_HEIGHT 1080U
#define FRAME_BYTES (FRAME_WIDTH * FRAME_HEIGHT)
#define RGB565_BYTES (FRAME_BYTES * 2U)
#define PIXEL_FORMAT_GRAY8 1U
#define PIXEL_FORMAT_RGB565 2U
#define HEADER_BYTES 40U
#define FRAME_MAGIC 0x50475246U
#define MAX_MODEL_BYTES (512U * 1024U * 1024U)
#define MAX_INPUT_BYTES (64U * 1024U * 1024U)
#define MAX_OUTPUTS 64U
#define YOLO_CLASS_COUNT 80U
#define YOLO_VALUES_PER_ANCHOR (YOLO_CLASS_COUNT + 5U)
#define YOLO_MAX_DETECTIONS 4096U
#define YOLO_CONFIDENCE_THRESHOLD 0.25f
#define YOLO_NMS_THRESHOLD 0.45f
#define RESULT_VERSION 4U
#define RESULT_HEADER_BYTES 88U
#define DETECTION_BYTES 24U
#define SEND_CHUNK_BYTES (64U * 1024U)
#define RESULT_FLAG_DETECTION_ONLY 1U
#define RESULT_FLAG_MATCHED 2U

static volatile sig_atomic_t stopping;
static _Thread_local bool verbose_frame = true;
static bool output_stats;

struct yolo_detection {
	float x;
	float y;
	float width;
	float height;
	float score;
	uint32_t class_id;
};

static const int yolo_anchors[3][6] = {
	{ 10, 13, 16, 30, 33, 23 },
	{ 30, 61, 62, 45, 59, 119 },
	{ 116, 90, 156, 198, 373, 326 }
};

static const char *const coco_labels[YOLO_CLASS_COUNT] = {
	"person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
	"boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
	"bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
	"giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
	"skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
	"skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
	"knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli",
	"carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
	"bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
	"cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
	"clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
};

struct rknn_functions {
	void *library;
	int (*init)(rknn_context *, void *, uint32_t, uint32_t, rknn_init_extend *);
	int (*query)(rknn_context, rknn_query_cmd, void *, uint32_t);
	int (*inputs_set)(rknn_context, uint32_t, rknn_input *);
	int (*run)(rknn_context, rknn_run_extend *);
	int (*outputs_get)(rknn_context, uint32_t, rknn_output *, rknn_output_extend *);
	int (*outputs_release)(rknn_context, uint32_t, rknn_output *);
	int (*destroy)(rknn_context);
};

struct frame_info {
	uint64_t sequence;
	uint64_t timestamp_ns;
	uint32_t checksum;
	uint32_t pixel_bytes;
	uint32_t pixel_format;
};

struct frame_timing {
	uint64_t read_ns;
	uint64_t preprocess_ns;
	uint64_t inputs_ns;
	uint64_t run_ns;
	uint64_t outputs_ns;
	uint64_t postprocess_ns;
};

struct inference_engine {
	struct rknn_functions functions;
	rknn_context context;
	rknn_input_output_num counts;
	rknn_tensor_attr input_attribute;
	rknn_tensor_attr *output_attributes;
	rknn_output *outputs;
	struct yolo_detection *detections;
	uint8_t *model_input;
	uint32_t input_bytes;
	uint32_t model_width;
	uint32_t model_height;
};

struct result_pipeline;

static int result_pipeline_publish_raw(struct result_pipeline *pipeline,
				       const struct frame_info *frame, const uint8_t *pixels);

struct capture_slot {
	struct frame_info frame;
	uint8_t *pixels;
	bool capturing;
	bool ready;
	bool processing;
};

struct capture_pipeline {
	int source;
	pthread_t thread;
	pthread_mutex_t lock;
	pthread_cond_t changed;
	struct capture_slot slots[2];
	struct result_pipeline *output;
	bool stopping;
	int error;
};

struct result_slot {
	struct frame_info frame;
	struct frame_timing timing;
	uint8_t *pixels;
	struct yolo_detection *detections;
	size_t count;
	uint64_t completed_ns;
	bool ready;
	bool sending;
};

struct result_pipeline {
	int client;
	bool matched_preview;
	pthread_t thread;
	pthread_mutex_t lock;
	pthread_cond_t changed;
	struct result_slot slots[2];
	struct result_slot raw_slots[2];
	bool stopping;
	int error;
	uint64_t replaced;
	uint64_t raw_replaced;
};

static uint64_t monotonic_ns(void)
{
	struct timespec timestamp;

	if (clock_gettime(CLOCK_MONOTONIC, &timestamp) < 0) {
		perror("clock_gettime");
		exit(EXIT_FAILURE);
	}
	return (uint64_t)timestamp.tv_sec * 1000000000ULL + timestamp.tv_nsec;
}

static uint64_t decode_le(const uint8_t *bytes, size_t count)
{
	uint64_t value = 0;
	size_t index;

	for (index = 0; index < count; index++)
		value |= (uint64_t)bytes[index] << (index * 8);
	return value;
}

static uint32_t frame_checksum(const uint8_t *pixels, size_t bytes)
{
	uint32_t checksum = 2166136261U;
	size_t index;

	for (index = 0; index < bytes; index++)
		checksum = (checksum ^ pixels[index]) * 16777619U;
	return checksum;
}

static int read_exact(int descriptor, void *buffer, size_t bytes)
{
	size_t received = 0;
	uint64_t deadline = monotonic_ns() + 30000000000ULL;

	while (received < bytes && !stopping) {
		struct pollfd event = { .fd = descriptor, .events = POLLIN };
		int ready = poll(&event, 1, 500);
		ssize_t result;

		if (ready < 0 && errno == EINTR)
			continue;
		if (ready < 0)
			return -1;
		if (!ready) {
			if (monotonic_ns() >= deadline) {
				errno = ETIMEDOUT;
				return -1;
			}
			continue;
		}
		result = read(descriptor, (uint8_t *)buffer + received, bytes - received);
		if (result < 0 && errno == EINTR)
			continue;
		if (result <= 0) {
			if (!result)
				errno = EPROTO;
			return -1;
		}
		received += (size_t)result;
	}
	if (stopping) {
		errno = EINTR;
		return -1;
	}
	return 0;
}

static int read_frame_record(int descriptor, uint8_t *pixels, struct frame_info *frame)
{
	uint8_t header[HEADER_BYTES];
	uint32_t expected;
	uint32_t actual;
	if (read_exact(descriptor, header, sizeof(header)) < 0) {
		perror("read frame header");
		return -1;
	}
	if (decode_le(header, 4) != FRAME_MAGIC || decode_le(header + 4, 2) != 1 ||
	    decode_le(header + 6, 2) != HEADER_BYTES ||
	    decode_le(header + 8, 4) != FRAME_WIDTH ||
	    decode_le(header + 12, 4) != FRAME_HEIGHT ||
	    (decode_le(header + 16, 4) != FRAME_BYTES &&
	     decode_le(header + 16, 4) != RGB565_BYTES)) {
		fprintf(stderr, "Input is not a version-1 1920x1080 GRAY8 or RGB565LE PCIe stream record\n");
		return -1;
	}
	frame->pixel_bytes = (uint32_t)decode_le(header + 16, 4);
	frame->pixel_format = frame->pixel_bytes == RGB565_BYTES ? PIXEL_FORMAT_RGB565 : PIXEL_FORMAT_GRAY8;
	if (read_exact(descriptor, pixels, frame->pixel_bytes) < 0) {
		perror("read frame pixels");
		return -1;
	}
	expected = (uint32_t)decode_le(header + 20, 4);
	actual = frame_checksum(pixels, frame->pixel_bytes);
	if (actual != expected) {
		fprintf(stderr, "Frame checksum mismatch: expected=0x%08" PRIx32
			" actual=0x%08" PRIx32 "\n", expected, actual);
		return -1;
	}
	frame->sequence = decode_le(header + 24, 8);
	frame->timestamp_ns = decode_le(header + 32, 8);
	frame->checksum = actual;
	if (verbose_frame)
		printf("PCIe frame verified: sequence=%" PRIu64 " bytes=%u checksum=0x%08" PRIx32 "\n",
		       frame->sequence, frame->pixel_bytes, actual);
	return 0;
}

static void *read_model(const char *path, uint32_t *size)
{
	FILE *model = fopen(path, "rb");
	void *data = NULL;
	long bytes;

	if (!model) {
		perror(path);
		return NULL;
	}
	if (fseek(model, 0, SEEK_END) || (bytes = ftell(model)) <= 0 ||
	    (unsigned long)bytes > MAX_MODEL_BYTES || fseek(model, 0, SEEK_SET)) {
		fprintf(stderr, "Cannot read model, or model exceeds %u bytes\n", MAX_MODEL_BYTES);
		goto close_model;
	}
	data = malloc((size_t)bytes);
	if (!data) {
		perror("allocate model");
		goto close_model;
	}
	if (fread(data, 1, (size_t)bytes, model) != (size_t)bytes) {
		fprintf(stderr, "Incomplete model read\n");
		free(data);
		data = NULL;
	} else {
		*size = (uint32_t)bytes;
	}
close_model:
	fclose(model);
	return data;
}

static int load_symbol(void *library, const char *name, void *function, size_t bytes)
{
	void *symbol;
	const char *error;

	dlerror();
	symbol = dlsym(library, name);
	error = dlerror();
	if (error || !symbol || bytes != sizeof(symbol)) {
		fprintf(stderr, "Cannot load %s: %s\n", name, error ? error : "invalid function pointer");
		return -1;
	}
	memcpy(function, &symbol, bytes);
	return 0;
}

static int load_runtime(struct rknn_functions *functions, const char *path)
{
	functions->library = dlopen(path, RTLD_NOW | RTLD_LOCAL);
	if (!functions->library) {
		fprintf(stderr, "Cannot load RKNN runtime %s: %s\n", path, dlerror());
		return -1;
	}
	if (load_symbol(functions->library, "rknn_init", &functions->init, sizeof(functions->init)) ||
	    load_symbol(functions->library, "rknn_query", &functions->query, sizeof(functions->query)) ||
	    load_symbol(functions->library, "rknn_inputs_set", &functions->inputs_set, sizeof(functions->inputs_set)) ||
	    load_symbol(functions->library, "rknn_run", &functions->run, sizeof(functions->run)) ||
	    load_symbol(functions->library, "rknn_outputs_get", &functions->outputs_get, sizeof(functions->outputs_get)) ||
	    load_symbol(functions->library, "rknn_outputs_release", &functions->outputs_release, sizeof(functions->outputs_release)) ||
	    load_symbol(functions->library, "rknn_destroy", &functions->destroy, sizeof(functions->destroy)))
		return -1;
	return 0;
}

static void print_tensor(const char *kind, const rknn_tensor_attr *attribute)
{
	uint32_t index;

	printf("%s[%u] name=%.*s fmt=%d type=%d qnt=%d scale=%g zp=%d elements=%u shape=[", kind,
	       attribute->index, (int)sizeof(attribute->name), attribute->name,
	       (int)attribute->fmt, (int)attribute->type, (int)attribute->qnt_type,
	       attribute->scale, attribute->zp, attribute->n_elems);
	for (index = 0; index < attribute->n_dims && index < RKNN_MAX_DIMS; index++)
		printf("%s%u", index ? "," : "", attribute->dims[index]);
	printf("]\n");
}

static float detection_iou(const struct yolo_detection *first,
			   const struct yolo_detection *second)
{
	float first_right = first->x + first->width;
	float first_bottom = first->y + first->height;
	float second_right = second->x + second->width;
	float second_bottom = second->y + second->height;
	float overlap_width = fmaxf(0.0f, fminf(first_right, second_right) -
						fmaxf(first->x, second->x));
	float overlap_height = fmaxf(0.0f, fminf(first_bottom, second_bottom) -
						 fmaxf(first->y, second->y));
	float intersection = overlap_width * overlap_height;
	float union_area = first->width * first->height +
			   second->width * second->height - intersection;

	return union_area > 0.0f ? intersection / union_area : 0.0f;
}

static void sort_detections(struct yolo_detection *detections, size_t count)
{
	size_t index;

	for (index = 1; index < count; index++) {
		struct yolo_detection current = detections[index];
		size_t position = index;

		while (position > 0 && detections[position - 1].score < current.score) {
			detections[position] = detections[position - 1];
			position--;
		}
		detections[position] = current;
	}
}

static inline float output_value(const rknn_tensor_attr *attribute,
			  const rknn_output *output, uint32_t index)
{
	int64_t raw;

	if (output->want_float)
		return ((const float *)output->buf)[index];
	switch (attribute->type) {
	case RKNN_TENSOR_FLOAT32:
		return ((const float *)output->buf)[index];
	case RKNN_TENSOR_INT8:
		raw = ((const int8_t *)output->buf)[index];
		break;
	case RKNN_TENSOR_UINT8:
		raw = ((const uint8_t *)output->buf)[index];
		break;
	case RKNN_TENSOR_INT16:
		raw = ((const int16_t *)output->buf)[index];
		break;
	case RKNN_TENSOR_UINT16:
		raw = ((const uint16_t *)output->buf)[index];
		break;
	case RKNN_TENSOR_INT32:
		raw = ((const int32_t *)output->buf)[index];
		break;
	case RKNN_TENSOR_UINT32:
		raw = ((const uint32_t *)output->buf)[index];
		break;
	default:
		return NAN;
	}
	if (attribute->qnt_type == RKNN_TENSOR_QNT_AFFINE_ASYMMETRIC)
		return ((float)raw - (float)attribute->zp) * attribute->scale;
	if (attribute->qnt_type == RKNN_TENSOR_QNT_DFP)
		return ldexpf((float)raw, -attribute->fl);
	return (float)raw;
}

static bool output_supports_native(const rknn_tensor_attr *attribute)
{
	if (attribute->type == RKNN_TENSOR_FLOAT32)
		return true;
	if (attribute->type != RKNN_TENSOR_INT8 && attribute->type != RKNN_TENSOR_UINT8 &&
	    attribute->type != RKNN_TENSOR_INT16 && attribute->type != RKNN_TENSOR_UINT16 &&
	    attribute->type != RKNN_TENSOR_INT32 && attribute->type != RKNN_TENSOR_UINT32)
		return false;
	return attribute->qnt_type == RKNN_TENSOR_QNT_NONE ||
	       attribute->qnt_type == RKNN_TENSOR_QNT_DFP ||
	       (attribute->qnt_type == RKNN_TENSOR_QNT_AFFINE_ASYMMETRIC &&
		isfinite(attribute->scale) && attribute->scale > 0.0f);
}

static bool output_buffer_valid(const rknn_tensor_attr *attribute, const rknn_output *output)
{
	size_t element_bytes;

	if (output->want_float || attribute->type == RKNN_TENSOR_FLOAT32)
		element_bytes = sizeof(float);
	else if (!output_supports_native(attribute))
		return false;
	else if (attribute->type == RKNN_TENSOR_INT8 || attribute->type == RKNN_TENSOR_UINT8)
		element_bytes = 1;
	else if (attribute->type == RKNN_TENSOR_INT16 || attribute->type == RKNN_TENSOR_UINT16)
		element_bytes = 2;
	else
		element_bytes = 4;
	return output->buf && attribute->n_elems &&
	       (uint64_t)attribute->n_elems * element_bytes <= output->size;
}

static size_t decode_yolov5(const rknn_tensor_attr *attributes,
				const rknn_output *outputs, uint32_t output_count,
				uint32_t model_width, uint32_t model_height,
				struct yolo_detection *detections)
{
	size_t detection_count = 0;
	size_t objectness_candidates = 0;
	float maximum_objectness = 0.0f;
	float maximum_class_probability = 0.0f;
	float maximum_score = 0.0f;
	uint32_t output_index;

	if (output_count != 3) {
		fprintf(stderr, "YOLO decode requires three NCHW outputs, got %u\n", output_count);
		return SIZE_MAX;
	}
	for (output_index = 0; output_index < output_count; output_index++) {
		const rknn_output *output = &outputs[output_index];
		const rknn_tensor_attr *attribute = &attributes[output_index];
		uint32_t grid_height;
		uint32_t grid_width;
		uint32_t grid_size;
		uint32_t stride;
		uint32_t anchor_index;
		uint32_t row;
		uint32_t column;

		if (!output_buffer_valid(attribute, output) || attribute->n_dims != 4 ||
		    attribute->fmt != RKNN_TENSOR_NCHW || attribute->dims[0] != 1 ||
		    attribute->dims[1] != YOLO_VALUES_PER_ANCHOR * 3U) {
			fprintf(stderr, "Unsupported YOLO output[%u]: expected NCHW [1,255,H,W]\n", output_index);
			return SIZE_MAX;
		}
		grid_height = attribute->dims[2];
		grid_width = attribute->dims[3];
		if (!grid_height || !grid_width ||
		    (uint64_t)grid_height * grid_width * YOLO_VALUES_PER_ANCHOR * 3U != attribute->n_elems ||
		    model_height % grid_height ||
		    model_width % grid_width || model_height / grid_height != model_width / grid_width) {
			fprintf(stderr, "Unsupported YOLO grid: output=%u grid=%ux%u model=%ux%u\n",
				output_index, grid_width, grid_height, model_width, model_height);
			return SIZE_MAX;
		}
		grid_size = grid_height * grid_width;
		stride = model_height / grid_height;
		for (anchor_index = 0; anchor_index < 3; anchor_index++) {
			for (row = 0; row < grid_height; row++) {
				for (column = 0; column < grid_width; column++) {
					uint32_t cell_offset = anchor_index * YOLO_VALUES_PER_ANCHOR * grid_size +
							      row * grid_width + column;
					float objectness = output_value(attribute, output, cell_offset + 4U * grid_size);
					float class_probability;
					float score;
					uint32_t class_id = 0;
					uint32_t candidate;

					if (!isfinite(objectness))
						return SIZE_MAX;
					if (objectness > maximum_objectness)
						maximum_objectness = objectness;
					if (objectness < YOLO_CONFIDENCE_THRESHOLD)
						continue;
					class_probability = output_value(attribute, output, cell_offset + 5U * grid_size);
					if (!isfinite(class_probability))
						return SIZE_MAX;
					for (candidate = 1; candidate < YOLO_CLASS_COUNT; candidate++) {
						float probability = output_value(attribute, output,
										cell_offset + (5U + candidate) * grid_size);

						if (!isfinite(probability))
							return SIZE_MAX;
						if (probability > class_probability) {
							class_probability = probability;
							class_id = candidate;
						}
					}
					if (class_probability > maximum_class_probability)
						maximum_class_probability = class_probability;
					score = objectness * class_probability;
					if (score > maximum_score)
						maximum_score = score;
					objectness_candidates++;
					if (score < YOLO_CONFIDENCE_THRESHOLD)
						continue;
					if (detection_count >= YOLO_MAX_DETECTIONS)
						continue;
					float center_x = output_value(attribute, output, cell_offset) * 2.0f -
							 0.5f + (float)column;
					float center_y = output_value(attribute, output, cell_offset + grid_size) * 2.0f -
							 0.5f + (float)row;
					float width_value = output_value(attribute, output, cell_offset + 2U * grid_size) * 2.0f;
					float height_value = output_value(attribute, output, cell_offset + 3U * grid_size) * 2.0f;
					if (!isfinite(center_x) || !isfinite(center_y) ||
					    !isfinite(width_value) || !isfinite(height_value))
						return SIZE_MAX;
					detections[detection_count].x = center_x * (float)stride;
					detections[detection_count].y = center_y * (float)stride;
					detections[detection_count].width = width_value * width_value *
						(float)yolo_anchors[output_index][anchor_index * 2U];
					detections[detection_count].height = height_value * height_value *
						(float)yolo_anchors[output_index][anchor_index * 2U + 1U];
					detections[detection_count].x -= detections[detection_count].width / 2.0f;
					detections[detection_count].y -= detections[detection_count].height / 2.0f;
					detections[detection_count].score = objectness * class_probability;
					detections[detection_count].class_id = class_id;
					detection_count++;
				}
			}
		}
	}
	if (verbose_frame)
		printf("YOLO decode: max_objectness=%.4f max_class=%.4f max_score=%.4f objectness_candidates=%zu\n",
		       maximum_objectness, maximum_class_probability, maximum_score, objectness_candidates);
	return detection_count;
}

static size_t suppress_detections(struct yolo_detection *detections, size_t count)
{
	uint8_t *suppressed;
	size_t kept = 0;
	size_t index;

	if (!count)
		return 0;
	suppressed = calloc(count, sizeof(*suppressed));
	if (!suppressed)
		return SIZE_MAX;
	sort_detections(detections, count);
	for (index = 0; index < count; index++) {
		size_t compare;

		if (suppressed[index])
			continue;
		for (compare = index + 1; compare < count; compare++) {
			if (!suppressed[compare] && detections[index].class_id == detections[compare].class_id &&
			    detection_iou(&detections[index], &detections[compare]) > YOLO_NMS_THRESHOLD)
				suppressed[compare] = 1;
		}
		if (kept != index)
			detections[kept] = detections[index];
		kept++;
	}
	free(suppressed);
	return kept;
}

static void map_detections(struct yolo_detection *detections, size_t count,
			   uint32_t model_width, uint32_t model_height)
{
	float scale = fminf((float)model_width / FRAME_WIDTH,
			   (float)model_height / FRAME_HEIGHT);
	float pad_left = ((float)model_width - FRAME_WIDTH * scale) / 2.0f;
	float pad_top = ((float)model_height - FRAME_HEIGHT * scale) / 2.0f;
	size_t index;
	for (index = 0; index < count; index++) {
		float left = (detections[index].x - pad_left) / scale;
		float top = (detections[index].y - pad_top) / scale;
		float right = (detections[index].x + detections[index].width - pad_left) / scale;
		float bottom = (detections[index].y + detections[index].height - pad_top) / scale;

		left = fmaxf(0.0f, fminf((float)FRAME_WIDTH, left));
		top = fmaxf(0.0f, fminf((float)FRAME_HEIGHT, top));
		right = fmaxf(0.0f, fminf((float)FRAME_WIDTH, right));
		bottom = fmaxf(0.0f, fminf((float)FRAME_HEIGHT, bottom));
		detections[index].x = left;
		detections[index].y = top;
		detections[index].width = right - left;
		detections[index].height = bottom - top;
	}
}

static void print_detections(const struct yolo_detection *detections, size_t count, size_t raw_count)
{
	size_t index;

	printf("YOLO detections: raw=%zu kept=%zu confidence=%.2f nms=%.2f\n",
	       raw_count, count, YOLO_CONFIDENCE_THRESHOLD, YOLO_NMS_THRESHOLD);
	for (index = 0; index < count; index++) {
		printf("Detection[%zu]: class=%u label=%s score=%.4f box=%.0f,%.0f,%.0f,%.0f\n",
		       index, detections[index].class_id, coco_labels[detections[index].class_id],
		       detections[index].score, detections[index].x, detections[index].y,
		       detections[index].x + detections[index].width,
		       detections[index].y + detections[index].height);
	}
}

static void rgb565_pixel(const uint8_t *pixels, uint8_t *rgb)
{
	uint16_t packed = (uint16_t)pixels[0] | (uint16_t)pixels[1] << 8;
	uint8_t red = (packed >> 11) & 31U;
	uint8_t green = (packed >> 5) & 63U;
	uint8_t blue = packed & 31U;

	rgb[0] = (red << 3) | (red >> 2);
	rgb[1] = (green << 2) | (green >> 4);
	rgb[2] = (blue << 3) | (blue >> 2);
}

static uint8_t *prepare_input(const uint8_t *pixels, const struct frame_info *frame,
			      const rknn_tensor_attr *attribute,
			      uint32_t *input_bytes, uint8_t *input)
{
	uint32_t width;
	uint32_t height;
	uint32_t channels;
	uint32_t resized_width;
	uint32_t resized_height;
	uint32_t offset_left;
	uint32_t offset_top;
	uint32_t row;
	uint32_t column;
	uint64_t bytes;

	if (attribute->n_dims != 4 || attribute->dims[0] != 1) {
		fprintf(stderr, "Only one batch of a 4D image tensor is supported\n");
		return NULL;
	}
	if (attribute->fmt == RKNN_TENSOR_NHWC) {
		height = attribute->dims[1];
		width = attribute->dims[2];
		channels = attribute->dims[3];
	} else if (attribute->fmt == RKNN_TENSOR_NCHW) {
		channels = attribute->dims[1];
		height = attribute->dims[2];
		width = attribute->dims[3];
	} else {
		fprintf(stderr, "Unsupported model input layout: %d\n", (int)attribute->fmt);
		return NULL;
	}
	bytes = (uint64_t)width * height * channels;
	if (!width || !height || width > 8192 || height > 8192 ||
	    (channels != 1 && channels != 3) || bytes > MAX_INPUT_BYTES) {
		fprintf(stderr, "Unsupported image input: %ux%u channels=%u\n", width, height, channels);
		return NULL;
	}
	if (!input)
		input = malloc((size_t)bytes);
	if (!input) {
		perror("allocate model input");
		return NULL;
	}
	memset(input, 114, (size_t)bytes);
	if ((uint64_t)width * FRAME_HEIGHT <= (uint64_t)height * FRAME_WIDTH) {
		resized_width = width;
		resized_height = ((uint64_t)FRAME_HEIGHT * width + FRAME_WIDTH / 2) / FRAME_WIDTH;
	} else {
		resized_height = height;
		resized_width = ((uint64_t)FRAME_WIDTH * height + FRAME_HEIGHT / 2) / FRAME_HEIGHT;
	}
	if (!resized_width)
		resized_width = 1;
	if (!resized_height)
		resized_height = 1;
	offset_left = (width - resized_width) / 2;
	offset_top = (height - resized_height) / 2;
	for (row = 0; row < resized_height; row++) {
		uint32_t source_row = ((uint64_t)row * 2 + 1) * FRAME_HEIGHT / ((uint64_t)resized_height * 2);

		for (column = 0; column < resized_width; column++) {
			uint32_t source_column = ((uint64_t)column * 2 + 1) * FRAME_WIDTH / ((uint64_t)resized_width * 2);
			size_t source = (size_t)source_row * FRAME_WIDTH + source_column;
			size_t destination = ((size_t)(offset_top + row) * width + offset_left + column) * channels;

			if (frame->pixel_format == PIXEL_FORMAT_RGB565) {
				uint8_t rgb[3];

				rgb565_pixel(pixels + source * 2, rgb);
				if (channels == 3)
					memcpy(input + destination, rgb, 3);
				else
					input[destination] = (77U * rgb[0] + 150U * rgb[1] + 29U * rgb[2]) >> 8;
			} else {
				memset(input + destination, pixels[source], channels);
			}
		}
	}
	*input_bytes = (uint32_t)bytes;
	if (verbose_frame)
		printf("Preprocess: %s -> NHWC UINT8 %ux%u channels=%u, letterbox=%ux%u pad=%u,%u\n",
		       frame->pixel_format == PIXEL_FORMAT_RGB565 ? "RGB565LE/RGB" : "GRAY8",
		       width, height, channels, resized_width, resized_height, offset_left, offset_top);
	return input;
}

static int save_frame(const char *path, const uint8_t *pixels, const struct frame_info *frame)
{
	FILE *image = fopen(path, "wb");
	int result = 0;

	if (!image) {
		perror(path);
		return -1;
	}
	if (fprintf(image, "%s\n%u %u\n255\n",
		    frame->pixel_format == PIXEL_FORMAT_RGB565 ? "P6" : "P5",
		    FRAME_WIDTH, FRAME_HEIGHT) < 0)
		result = -1;
	if (frame->pixel_format == PIXEL_FORMAT_RGB565) {
		uint8_t row[FRAME_WIDTH * 3];
		uint32_t row_index;
		uint32_t column;

		for (row_index = 0; row_index < FRAME_HEIGHT && !result; row_index++) {
			for (column = 0; column < FRAME_WIDTH; column++)
				rgb565_pixel(pixels + ((size_t)row_index * FRAME_WIDTH + column) * 2,
					     row + column * 3);
			if (fwrite(row, 1, sizeof(row), image) != sizeof(row))
				result = -1;
		}
	} else if (fwrite(pixels, 1, frame->pixel_bytes, image) != frame->pixel_bytes) {
		result = -1;
	}
	if (fclose(image))
		result = -1;
	if (result)
		fprintf(stderr, "Failed to save %s\n", path);
	return result;
}

static int infer_frame(struct inference_engine *engine, const uint8_t *pixels,
			const struct frame_info *frame,
			struct frame_timing *timing, size_t *kept_count, bool trace_stages)
{
	rknn_input input = { 0 };
	uint64_t started_ns = monotonic_ns();
	uint64_t previous_ns;
	uint32_t index;
	size_t raw_count;
	int status;
	int result = -1;

	engine->model_input = prepare_input(pixels, frame, &engine->input_attribute,
					    &engine->input_bytes, engine->model_input);
	if (!engine->model_input)
		return -1;
	previous_ns = monotonic_ns();
	timing->preprocess_ns = previous_ns - started_ns;
	input.buf = engine->model_input;
	input.size = engine->input_bytes;
	input.type = RKNN_TENSOR_UINT8;
	input.fmt = RKNN_TENSOR_NHWC;
	if (trace_stages)
		printf("RKNN stage: preprocess complete; setting inputs\n");
	status = engine->functions.inputs_set(engine->context, 1, &input);
	if (status) {
		fprintf(stderr, "rknn_inputs_set failed: %d\n", status);
		return -1;
	}
	started_ns = monotonic_ns();
	timing->inputs_ns = started_ns - previous_ns;
	if (trace_stages)
		printf("RKNN stage: inputs complete; running NPU\n");
	status = engine->functions.run(engine->context, NULL);
	if (status) {
		fprintf(stderr, "rknn_run failed: %d\n", status);
		return -1;
	}
	previous_ns = monotonic_ns();
	timing->run_ns = previous_ns - started_ns;
	if (trace_stages)
		printf("RKNN stage: NPU complete; getting outputs\n");
	status = engine->functions.outputs_get(engine->context, engine->counts.n_output,
					       engine->outputs, NULL);
	if (status) {
		fprintf(stderr, "rknn_outputs_get failed: %d\n", status);
		return -1;
	}
	started_ns = monotonic_ns();
	timing->outputs_ns = started_ns - previous_ns;
	if (trace_stages)
		printf("RKNN stage: outputs complete; decoding YOLO\n");
	for (index = 0; index < engine->counts.n_output; index++) {
		const rknn_tensor_attr *attribute = &engine->output_attributes[index];
		const rknn_output *output = &engine->outputs[index];
		uint32_t elements = engine->output_attributes[index].n_elems;
		uint32_t element;
		float minimum = FLT_MAX;
		float maximum = -FLT_MAX;

		if (!output_buffer_valid(attribute, output)) {
			fprintf(stderr, "Incomplete output: index=%u size=%u elements=%u\n",
				index, output->size, elements);
			goto release_outputs;
		}
		if (output_stats) {
			for (element = 0; element < elements; element++) {
				float value = output_value(attribute, output, element);

				if (!isfinite(value)) {
					fprintf(stderr, "Non-finite output: index=%u element=%u\n", index, element);
					goto release_outputs;
				}
				if (value < minimum)
					minimum = value;
				if (value > maximum)
					maximum = value;
			}
			printf("Output[%u] %s elements=%u min=%g max=%g first=", index,
			       output->want_float ? "FP32" : "native", elements, minimum, maximum);
			for (element = 0; element < elements && element < 8; element++)
				printf("%s%g", element ? "," : "", output_value(attribute, output, element));
			printf("\n");
		}
	}
	raw_count = decode_yolov5(engine->output_attributes, engine->outputs,
				  engine->counts.n_output, engine->model_width,
				  engine->model_height, engine->detections);
	if (raw_count == SIZE_MAX)
		goto release_outputs;
	*kept_count = suppress_detections(engine->detections, raw_count);
	if (*kept_count == SIZE_MAX) {
		perror("allocate NMS state");
		goto release_outputs;
	}
	map_detections(engine->detections, *kept_count, engine->model_width, engine->model_height);
	if (verbose_frame)
		print_detections(engine->detections, *kept_count, raw_count);
	result = 0;
release_outputs:
	status = engine->functions.outputs_release(engine->context, engine->counts.n_output, engine->outputs);
	if (status) {
		fprintf(stderr, "rknn_outputs_release failed: %d\n", status);
		result = -1;
	}
	for (index = 0; index < engine->counts.n_output; index++) {
		engine->outputs[index].buf = NULL;
		engine->outputs[index].size = 0;
	}
	timing->postprocess_ns = monotonic_ns() - started_ns;
	return result;
}

static void encode_le(uint8_t *buffer, uint64_t value, size_t bytes)
{
	size_t index;

	for (index = 0; index < bytes; index++)
		buffer[index] = (uint8_t)(value >> (index * 8));
}

static void stop_server(int signal_number)
{
	(void)signal_number;
	stopping = 1;
}

static int start_background(const char *log_path)
{
	int log_file = open(log_path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
	int input;
	pid_t process;

	if (log_file < 0) {
		perror(log_path);
		return -1;
	}
	process = fork();
	if (process < 0) {
		perror("fork");
		close(log_file);
		return -1;
	}
	if (process > 0) {
		printf("RKNN server starting: pid=%ld log=%s\n", (long)process, log_path);
		close(log_file);
		return 1;
	}
	if (setsid() < 0) {
		perror("setsid");
		close(log_file);
		return -1;
	}
	input = open("/dev/null", O_RDONLY);
	if (input < 0 || dup2(input, STDIN_FILENO) < 0 ||
	    dup2(log_file, STDOUT_FILENO) < 0 || dup2(log_file, STDERR_FILENO) < 0) {
		perror("Background standard streams");
		if (input >= 0)
			close(input);
		close(log_file);
		return -1;
	}
	if (input > STDERR_FILENO)
		close(input);
	if (log_file > STDERR_FILENO)
		close(log_file);
	return 0;
}

static int send_exact(int descriptor, const uint8_t *buffer, size_t bytes)
{
	size_t sent = 0;

	while (sent < bytes && !stopping) {
		size_t chunk = bytes - sent;
		ssize_t result;

		if (chunk > SEND_CHUNK_BYTES)
			chunk = SEND_CHUNK_BYTES;
		result = send(descriptor, buffer + sent, chunk, MSG_NOSIGNAL);
		if (result < 0 && errno == EINTR)
			continue;
		if (result <= 0) {
			if (!result)
				errno = EPIPE;
			return -1;
		}
		sent += (size_t)result;
	}
	if (stopping) {
		errno = EINTR;
		return -1;
	}
	return 0;
}

static void fill_result_header(uint8_t *header, const struct frame_info *frame,
				       const struct frame_timing *timing, size_t pixel_bytes,
				       size_t count, uint32_t flags, uint64_t completed_ns)
{
	uint64_t durations[] = { timing ? timing->read_ns : 0,
				 timing ? timing->preprocess_ns : 0,
				 timing ? timing->inputs_ns : 0,
				 timing ? timing->run_ns : 0,
				 timing ? timing->outputs_ns : 0,
				 timing ? timing->postprocess_ns : 0 };
	size_t index;

	memset(header, 0, RESULT_HEADER_BYTES);
	encode_le(header, FRAME_MAGIC, 4);
	encode_le(header + 4, RESULT_VERSION, 2);
	encode_le(header + 6, RESULT_HEADER_BYTES, 2);
	encode_le(header + 8, FRAME_WIDTH, 4);
	encode_le(header + 12, FRAME_HEIGHT, 4);
	encode_le(header + 16, pixel_bytes, 4);
	encode_le(header + 20, frame->checksum, 4);
	encode_le(header + 24, frame->sequence, 8);
	encode_le(header + 32, frame->timestamp_ns, 8);
	encode_le(header + 40, count, 4);
	encode_le(header + 44, DETECTION_BYTES, 4);
	for (index = 0; index < sizeof(durations) / sizeof(durations[0]); index++)
		encode_le(header + 48 + index * 4, durations[index] / 1000, 4);
	encode_le(header + 72, completed_ns, 8);
	encode_le(header + 80, flags, 4);
	encode_le(header + 84, frame->pixel_format, 4);
}

static void fill_detection_entries(uint8_t *entries,
				   const struct yolo_detection *detections, size_t count)
{
	size_t index;

	for (index = 0; index < count; index++) {
		uint8_t *entry = entries + index * DETECTION_BYTES;

		encode_le(entry, detections[index].class_id, 4);
		encode_le(entry + 4, (uint32_t)lroundf(detections[index].score * 1000000.0f), 4);
		encode_le(entry + 8, (uint32_t)lroundf(detections[index].x), 4);
		encode_le(entry + 12, (uint32_t)lroundf(detections[index].y), 4);
		encode_le(entry + 16, (uint32_t)lroundf(detections[index].x + detections[index].width), 4);
		encode_le(entry + 20, (uint32_t)lroundf(detections[index].y + detections[index].height), 4);
	}
}

static int send_raw_frame(int client, const struct frame_info *frame, const uint8_t *pixels)
{
	uint8_t header[RESULT_HEADER_BYTES];
	struct frame_timing timing = { 0 };

	fill_result_header(header, frame, &timing, frame->pixel_bytes, 0, 0, monotonic_ns());
	return send_exact(client, header, sizeof(header)) || send_exact(client, pixels, frame->pixel_bytes);
}

static int send_detection_result(int client, const struct frame_info *frame,
				 const struct frame_timing *timing,
				 const struct yolo_detection *detections, size_t count,
				 const uint8_t *pixels, uint64_t completed_ns)
{
	uint8_t header[RESULT_HEADER_BYTES];
	uint8_t entries[YOLO_MAX_DETECTIONS * DETECTION_BYTES];

	fill_result_header(header, frame, timing, pixels ? frame->pixel_bytes : 0, count,
			   pixels ? RESULT_FLAG_MATCHED : RESULT_FLAG_DETECTION_ONLY, completed_ns);
	fill_detection_entries(entries, detections, count);
	return send_exact(client, header, sizeof(header)) ||
	       (pixels && send_exact(client, pixels, frame->pixel_bytes)) ||
	       send_exact(client, entries, count * DETECTION_BYTES);
}

static struct capture_slot *capture_pipeline_write_slot(struct capture_pipeline *pipeline)
{
	struct capture_slot *slot = NULL;
	unsigned int index;

	for (index = 0; index < 2; index++) {
		struct capture_slot *candidate = &pipeline->slots[index];

		if (candidate->capturing || candidate->processing)
			continue;
		if (!candidate->ready)
			return candidate;
		if (!slot || candidate->frame.sequence < slot->frame.sequence)
			slot = candidate;
	}
	return slot;
}

static void *capture_thread_main(void *data)
{
	struct capture_pipeline *pipeline = data;

	verbose_frame = false;
	while (!stopping) {
		struct capture_slot *slot = NULL;

		pthread_mutex_lock(&pipeline->lock);
		while (!pipeline->stopping && !stopping) {
			slot = capture_pipeline_write_slot(pipeline);
			if (slot)
				break;
			pthread_cond_wait(&pipeline->changed, &pipeline->lock);
		}
		if (pipeline->stopping || stopping) {
			pthread_mutex_unlock(&pipeline->lock);
			break;
		}
		slot->capturing = true;
		slot->ready = false;
		pthread_mutex_unlock(&pipeline->lock);

		if (read_frame_record(pipeline->source, slot->pixels, &slot->frame)) {
			pthread_mutex_lock(&pipeline->lock);
			slot->capturing = false;
			pipeline->error = errno ? errno : EPROTO;
			pipeline->stopping = true;
			pthread_cond_broadcast(&pipeline->changed);
			pthread_mutex_unlock(&pipeline->lock);
			break;
		}
		if (result_pipeline_publish_raw(pipeline->output, &slot->frame, slot->pixels)) {
			if (errno != EBUSY) {
				pthread_mutex_lock(&pipeline->lock);
				slot->capturing = false;
				pipeline->error = errno ? errno : EPIPE;
				pipeline->stopping = true;
				pthread_cond_broadcast(&pipeline->changed);
				pthread_mutex_unlock(&pipeline->lock);
				break;
			}
		}
		pthread_mutex_lock(&pipeline->lock);
		slot->capturing = false;
		slot->ready = true;
		pthread_cond_broadcast(&pipeline->changed);
		pthread_mutex_unlock(&pipeline->lock);
	}
	pthread_mutex_lock(&pipeline->lock);
	pipeline->stopping = true;
	pthread_cond_broadcast(&pipeline->changed);
	pthread_mutex_unlock(&pipeline->lock);
	return NULL;
}

static int capture_pipeline_start(struct capture_pipeline *pipeline, const char *device,
				  struct result_pipeline *output)
{
	unsigned int index;

	memset(pipeline, 0, sizeof(*pipeline));
	pipeline->output = output;
	pipeline->source = open(device, O_RDONLY | O_CLOEXEC);
	if (pipeline->source < 0) {
		perror(device);
		return -1;
	}
	if (pthread_mutex_init(&pipeline->lock, NULL) ||
	    pthread_cond_init(&pipeline->changed, NULL)) {
		perror("capture pipeline synchronization");
		close(pipeline->source);
		return -1;
	}
	for (index = 0; index < 2; index++) {
		pipeline->slots[index].pixels = malloc(RGB565_BYTES);
		if (!pipeline->slots[index].pixels) {
			perror("capture pipeline frame buffer");
			pipeline->stopping = true;
			break;
		}
	}
	if (pipeline->stopping || pthread_create(&pipeline->thread, NULL,
						 capture_thread_main, pipeline)) {
		if (!pipeline->stopping)
			perror("capture pipeline thread");
		for (index = 0; index < 2; index++)
			free(pipeline->slots[index].pixels);
		pthread_cond_destroy(&pipeline->changed);
		pthread_mutex_destroy(&pipeline->lock);
		close(pipeline->source);
		return -1;
	}
	return 0;
}

static void capture_pipeline_stop(struct capture_pipeline *pipeline)
{
	unsigned int index;

	pthread_mutex_lock(&pipeline->lock);
	pipeline->stopping = true;
	pthread_cond_broadcast(&pipeline->changed);
	pthread_mutex_unlock(&pipeline->lock);
	close(pipeline->source);
	pthread_join(pipeline->thread, NULL);
	for (index = 0; index < 2; index++)
		free(pipeline->slots[index].pixels);
	pthread_cond_destroy(&pipeline->changed);
	pthread_mutex_destroy(&pipeline->lock);
}

static int capture_pipeline_take(struct capture_pipeline *pipeline,
				 struct capture_slot **slot_out)
{
	struct capture_slot *slot = NULL;
	unsigned int index;

	pthread_mutex_lock(&pipeline->lock);
	while (!pipeline->stopping && !stopping) {
		for (index = 0; index < 2; index++) {
			if (pipeline->slots[index].ready && !pipeline->slots[index].capturing &&
			    !pipeline->slots[index].processing) {
				if (!slot || pipeline->slots[index].frame.sequence > slot->frame.sequence)
					slot = &pipeline->slots[index];
			}
		}
		if (slot)
			break;
		pthread_cond_wait(&pipeline->changed, &pipeline->lock);
	}
	if (!slot) {
		int error = pipeline->error ? pipeline->error : EINTR;
		pthread_mutex_unlock(&pipeline->lock);
		errno = error;
		return -1;
	}
	slot->ready = false;
	slot->processing = true;
	for (index = 0; index < 2; index++) {
		if (&pipeline->slots[index] != slot)
			pipeline->slots[index].ready = false;
	}
	*slot_out = slot;
	pthread_mutex_unlock(&pipeline->lock);
	return 0;
}

static void capture_pipeline_release(struct capture_pipeline *pipeline,
				     struct capture_slot *slot)
{
	pthread_mutex_lock(&pipeline->lock);
	slot->processing = false;
	pthread_cond_broadcast(&pipeline->changed);
	pthread_mutex_unlock(&pipeline->lock);
}

static void *result_thread_main(void *data)
{
	struct result_pipeline *pipeline = data;
	uint64_t sent_frames = 0;
	uint64_t sent_raw = 0;
	uint64_t sent_detections = 0;
	uint64_t previous_sent_ns = 0;

	while (!stopping) {
		struct result_slot *slot = NULL;
		bool raw = false;
		uint64_t send_started_ns;
		uint64_t sent_ns;
		unsigned int index;

		pthread_mutex_lock(&pipeline->lock);
		while (!pipeline->stopping && !stopping) {
			for (index = 0; index < 2; index++) {
				if (pipeline->slots[index].ready && !pipeline->slots[index].sending) {
					if (!slot || pipeline->slots[index].frame.sequence > slot->frame.sequence)
						slot = &pipeline->slots[index];
				}
			}
			if (!slot) {
				for (index = 0; index < 2; index++) {
					if (pipeline->raw_slots[index].ready && !pipeline->raw_slots[index].sending) {
						if (!slot || pipeline->raw_slots[index].frame.sequence > slot->frame.sequence) {
							slot = &pipeline->raw_slots[index];
							raw = true;
						}
					}
				}
			}
			if (slot)
				break;
			pthread_cond_wait(&pipeline->changed, &pipeline->lock);
		}
		if (!slot || pipeline->stopping || stopping) {
			pthread_mutex_unlock(&pipeline->lock);
			break;
		}
		slot->ready = false;
		slot->sending = true;
		pthread_mutex_unlock(&pipeline->lock);
		send_started_ns = monotonic_ns();
		if (raw ? send_raw_frame(pipeline->client, &slot->frame, slot->pixels) :
			  send_detection_result(pipeline->client, &slot->frame, &slot->timing,
						 slot->detections, slot->count, slot->pixels,
						 slot->completed_ns)) {
			pthread_mutex_lock(&pipeline->lock);
			pipeline->error = errno ? errno : EPIPE;
			pipeline->stopping = true;
			slot->sending = false;
			pthread_cond_broadcast(&pipeline->changed);
			pthread_mutex_unlock(&pipeline->lock);
			break;
		}
		sent_ns = monotonic_ns();
		if (pipeline->matched_preview && (sent_frames < 3 || (sent_frames + 1) % 10 == 0)) {
			printf("RKNN preview timing: mode=matched seq=%" PRIu64
			       " send_ms=%.3f frame_age_ms=%.3f interval_ms=%.3f fps=%.2f\n",
			       slot->frame.sequence, (sent_ns - send_started_ns) / 1e6,
			       (sent_ns - slot->frame.timestamp_ns) / 1e6,
			       previous_sent_ns ? (sent_ns - previous_sent_ns) / 1e6 : 0.0,
			       previous_sent_ns ? 1e9 / (sent_ns - previous_sent_ns) : 0.0);
		}
		previous_sent_ns = sent_ns;
		pthread_mutex_lock(&pipeline->lock);
		slot->sending = false;
		pthread_cond_broadcast(&pipeline->changed);
		pthread_mutex_unlock(&pipeline->lock);
		sent_frames++;
		if (raw)
			sent_raw++;
		else
			sent_detections++;
	}
	printf("RKNN sender finished; sent records=%" PRIu64 " raw=%" PRIu64
	       " detections=%" PRIu64 " raw_replaced=%" PRIu64 " detection_replaced=%" PRIu64 "\n",
	       sent_frames, sent_raw, sent_detections, pipeline->raw_replaced, pipeline->replaced);
	return NULL;
}

static int result_pipeline_start(struct result_pipeline *pipeline, int client, bool matched_preview)
{
	unsigned int index;

	memset(pipeline, 0, sizeof(*pipeline));
	pipeline->client = client;
	pipeline->matched_preview = matched_preview;
	if (pthread_mutex_init(&pipeline->lock, NULL) ||
	    pthread_cond_init(&pipeline->changed, NULL))
		return -1;
	for (index = 0; index < 2; index++) {
		pipeline->slots[index].detections = calloc(YOLO_MAX_DETECTIONS,
							 sizeof(struct yolo_detection));
		if (matched_preview)
			pipeline->slots[index].pixels = malloc(RGB565_BYTES);
		else
			pipeline->raw_slots[index].pixels = malloc(RGB565_BYTES);
		if (!pipeline->slots[index].detections ||
		    (matched_preview ? !pipeline->slots[index].pixels : !pipeline->raw_slots[index].pixels))
			pipeline->stopping = true;
	}
	if (pipeline->stopping || pthread_create(&pipeline->thread, NULL,
						 result_thread_main, pipeline)) {
		for (index = 0; index < 2; index++) {
			free(pipeline->slots[index].detections);
			free(pipeline->slots[index].pixels);
			free(pipeline->raw_slots[index].pixels);
		}
		pthread_cond_destroy(&pipeline->changed);
		pthread_mutex_destroy(&pipeline->lock);
		return -1;
	}
	return 0;
}

static int result_pipeline_publish(struct result_pipeline *pipeline,
				   const struct frame_info *frame,
				   const struct frame_timing *timing, const uint8_t *pixels,
				   const struct yolo_detection *detections, size_t count,
				   uint64_t completed_ns)
{
	struct result_slot *slot = NULL;
	unsigned int index;

	pthread_mutex_lock(&pipeline->lock);
	if (pipeline->stopping) {
		int error = pipeline->error ? pipeline->error : EINTR;
		pthread_mutex_unlock(&pipeline->lock);
		errno = error;
		return -1;
	}
	for (index = 0; index < 2; index++) {
		if (!pipeline->slots[index].sending) {
			slot = &pipeline->slots[index];
			break;
		}
	}
	if (!slot) {
		pthread_mutex_unlock(&pipeline->lock);
		errno = EBUSY;
		return -1;
	}
	for (index = 0; index < 2; index++) {
		if (pipeline->slots[index].ready) {
			pipeline->slots[index].ready = false;
			pipeline->replaced++;
		}
	}
	slot->frame = *frame;
	slot->timing = *timing;
	slot->count = count;
	slot->completed_ns = completed_ns;
	if (pipeline->matched_preview)
		memcpy(slot->pixels, pixels, frame->pixel_bytes);
	memcpy(slot->detections, detections, count * sizeof(*detections));
	slot->ready = true;
	pthread_cond_broadcast(&pipeline->changed);
	pthread_mutex_unlock(&pipeline->lock);
	return 0;
}

static void result_pipeline_stop(struct result_pipeline *pipeline)
{
	unsigned int index;

	pthread_mutex_lock(&pipeline->lock);
	pipeline->stopping = true;
	pthread_cond_broadcast(&pipeline->changed);
	pthread_mutex_unlock(&pipeline->lock);
	shutdown(pipeline->client, SHUT_RDWR);
	pthread_join(pipeline->thread, NULL);
	for (index = 0; index < 2; index++) {
		free(pipeline->slots[index].detections);
		free(pipeline->slots[index].pixels);
		free(pipeline->raw_slots[index].pixels);
	}
	pthread_cond_destroy(&pipeline->changed);
	pthread_mutex_destroy(&pipeline->lock);
}

static int result_pipeline_publish_raw(struct result_pipeline *pipeline,
				       const struct frame_info *frame, const uint8_t *pixels)
{
	struct result_slot *slot = NULL;
	unsigned int index;

	pthread_mutex_lock(&pipeline->lock);
	if (pipeline->stopping) {
		int error = pipeline->error ? pipeline->error : EINTR;
		pthread_mutex_unlock(&pipeline->lock);
		errno = error;
		return -1;
	}
	if (pipeline->matched_preview) {
		pthread_mutex_unlock(&pipeline->lock);
		return 0;
	}
	for (index = 0; index < 2; index++) {
		if (!pipeline->raw_slots[index].sending) {
			slot = &pipeline->raw_slots[index];
			break;
		}
	}
	if (!slot) {
		pthread_mutex_unlock(&pipeline->lock);
		errno = EBUSY;
		return -1;
	}
	for (index = 0; index < 2; index++) {
		if (pipeline->raw_slots[index].ready) {
			pipeline->raw_slots[index].ready = false;
			pipeline->raw_replaced++;
		}
	}
	slot->frame = *frame;
	memcpy(slot->pixels, pixels, frame->pixel_bytes);
	slot->ready = true;
	pthread_cond_broadcast(&pipeline->changed);
	pthread_mutex_unlock(&pipeline->lock);
	return 0;
}

static int serve_client(int client, const char *device, struct inference_engine *engine,
			bool profile, bool matched_preview)
{
	struct timeval timeout = { .tv_sec = 5 };
	struct capture_pipeline pipeline;
	struct result_pipeline sender;
	struct capture_slot *slot;
	uint64_t frames = 0;
	uint64_t skipped = 0;
	uint64_t previous_sequence = 0;
	uint64_t previous_timestamp = 0;
	uint64_t previous_inferred_ns = 0;
	int pipeline_started = 0;
	int result = 0;

	if (setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout))) {
		perror("SO_SNDTIMEO");
		return 0;
	}
	if (result_pipeline_start(&sender, client, matched_preview)) {
		return 0;
	}
	if (capture_pipeline_start(&pipeline, device, &sender)) {
		result_pipeline_stop(&sender);
		return 0;
	}
	pipeline_started = 1;
	printf("RKNN client connected; reading %s preview_mode=%s\n", device,
	       matched_preview ? "matched" : "async");
	while (!stopping) {
		struct frame_info frame;
		struct frame_timing timing = { 0 };
		uint64_t started_ns = monotonic_ns();
		uint64_t inferred_ns;
		size_t count;

		if (capture_pipeline_take(&pipeline, &slot)) {
			if (!stopping)
				perror("Acquire capture frame");
			break;
		}
		frame = slot->frame;
		verbose_frame = false;
		if (frames && (frame.sequence <= previous_sequence ||
			       frame.timestamp_ns <= previous_timestamp)) {
			fprintf(stderr, "Stream sequence or timestamp did not increase\n");
			capture_pipeline_release(&pipeline, slot);
			break;
		}
		if (frames)
			skipped += frame.sequence - previous_sequence - 1;
		timing.read_ns = monotonic_ns() - started_ns;
		verbose_frame = profile && (frames < 3 || frame.sequence % 10 == 0);
		if (!frames)
			printf("RKNN first inference: seq=%" PRIu64 "\n", frame.sequence);
		if (infer_frame(engine, slot->pixels, &slot->frame, &timing, &count, !frames)) {
			capture_pipeline_release(&pipeline, slot);
			result = -1;
			break;
		}
		inferred_ns = monotonic_ns();
		if (result_pipeline_publish(&sender, &frame, &timing, slot->pixels,
					    engine->detections, count, inferred_ns)) {
			capture_pipeline_release(&pipeline, slot);
			if (!stopping)
				perror("Send RKNN result");
			break;
		}
		capture_pipeline_release(&pipeline, slot);
		frames++;
		if (frames <= 3 || frames % 10 == 0) {
			printf("RKNN stream: frames=%" PRIu64 " seq=%" PRIu64 " detections=%zu skipped=%" PRIu64 "\n",
			       frames, frame.sequence, count, skipped);
			printf("RKNN stream timing: seq=%" PRIu64 " read_ms=%.3f preprocess_ms=%.3f inputs_ms=%.3f "
			       "run_ms=%.3f outputs_ms=%.3f postprocess_ms=%.3f total_ms=%.3f interval_ms=%.3f\n",
			       frame.sequence, timing.read_ns / 1e6, timing.preprocess_ns / 1e6,
			       timing.inputs_ns / 1e6, timing.run_ns / 1e6, timing.outputs_ns / 1e6,
			       timing.postprocess_ns / 1e6, (inferred_ns - started_ns) / 1e6,
			       previous_inferred_ns ? (inferred_ns - previous_inferred_ns) / 1e6 : 0.0);
		}
		previous_sequence = frame.sequence;
		previous_timestamp = frame.timestamp_ns;
		previous_inferred_ns = inferred_ns;
	}
	if (pipeline_started)
		capture_pipeline_stop(&pipeline);
	result_pipeline_stop(&sender);
	printf("RKNN client finished; sent frames=%" PRIu64 "\n", frames);
	return result;
}

static int run_server(int listener, const char *device, struct inference_engine *engine,
			bool profile, bool matched_preview)
{
	int result = 0;

	while (!stopping) {
		struct pollfd event = { .fd = listener, .events = POLLIN };
		int ready = poll(&event, 1, 500);
		int client;

		if (ready < 0 && errno == EINTR)
			continue;
		if (ready < 0) {
			perror("poll listener");
			return -1;
		}
		if (!ready)
			continue;
		client = accept(listener, NULL, NULL);
		if (client < 0) {
			if (errno == EINTR || errno == ECONNABORTED)
				continue;
			perror("accept");
			return -1;
		}
		result = serve_client(client, device, engine, profile, matched_preview);
		close(client);
		if (result)
			break;
	}
	return result;
}

static void usage(const char *program)
{
	printf("Usage: %s --model FILE [--runtime LIBRARY] [--device DEVICE_OR_RECORD] "
	       "[--save-frame FILE.pgm_or_ppm] [--inspect] [--serve] [--bind IPv4] [--port PORT] "
	       "[--daemon] [--log FILE] [--profile] [--float-output] [--output-stats] "
	       "[--preview-mode matched|async]\n", program);
	printf("Default runtime: librknnrt.so\nDefault device: /dev/pango_pcie_stream_0002:21:00.0\n");
	printf("Runs one verified PCIe frame through RKNN and reports YOLOv5 detections.\n");
	printf("PCIe input formats: GRAY8, RGB565LE; result_version=%u result_header=%u\n",
	       RESULT_VERSION, RESULT_HEADER_BYTES);
	printf("--serve continuously sends matching frames and detections to preview_stream.py; "
	       "default bind=127.0.0.1 port=9000.\n");
	printf("--float-output requests FP32 output conversion for compatibility testing; "
	       "default keeps quantized output.\n");
	printf("--preview-mode matched (default) sends each inferred image with its own detections; "
	       "async sends raw images and detections separately.\n");
	printf("--output-stats scans full output tensors for diagnostics; disabled during streaming by default.\n");
}

int main(int argc, char **argv)
{
	const char *model_path = NULL;
	const char *runtime_path = "librknnrt.so";
	const char *device = "/dev/pango_pcie_stream_0002:21:00.0";
	const char *image_path = NULL;
	const char *bind_address = "127.0.0.1";
	const char *log_path = "/data/local/tmp/pcie_rknn_server.log";
	unsigned long port = 9000;
	bool serve = false;
	bool daemon = false;
	bool profile = false;
	bool float_output = false;
	bool matched_preview = true;
	struct sockaddr_in address = { .sin_family = AF_INET };
	struct sigaction action = { .sa_handler = stop_server };
	struct inference_engine engine = { 0 };
	struct frame_info frame;
	struct frame_timing timing = { 0 };
	int listener = -1;
	int source = -1;
	bool inspect_only = false;
	bool initialized = false;
	struct rknn_functions functions = { 0 };
	rknn_context context = 0;
	rknn_input_output_num counts = { 0 };
	rknn_sdk_version version = { 0 };
	rknn_tensor_attr input_attribute = { 0 };
	rknn_tensor_attr *output_attributes = NULL;
	rknn_output *outputs = NULL;
	struct yolo_detection *detections = NULL;
	uint8_t *pixels = NULL;
	void *model_data = NULL;
	uint32_t model_bytes = 0;
	uint32_t model_width = 0;
	uint32_t model_height = 0;
	uint32_t index;
	size_t detection_count;
	uint64_t started_ns;
	int argument;
	int result = EXIT_FAILURE;
	int status;

	setvbuf(stdout, NULL, _IOLBF, 0);
	for (argument = 1; argument < argc; argument++) {
		if (!strcmp(argv[argument], "--help")) {
			usage(argv[0]);
			return EXIT_SUCCESS;
		}
		if (!strcmp(argv[argument], "--inspect")) {
			inspect_only = true;
			continue;
		}
		if (!strcmp(argv[argument], "--serve")) {
			serve = true;
			continue;
		}
		if (!strcmp(argv[argument], "--daemon")) {
			daemon = true;
			continue;
		}
		if (!strcmp(argv[argument], "--profile")) {
			profile = true;
			continue;
		}
		if (!strcmp(argv[argument], "--float-output")) {
			float_output = true;
			continue;
		}
		if (!strcmp(argv[argument], "--output-stats")) {
			output_stats = true;
			continue;
		}
		if (argument + 1 >= argc) {
			fprintf(stderr, "Missing value for %s\n", argv[argument]);
			return EXIT_FAILURE;
		}
		if (!strcmp(argv[argument], "--model"))
			model_path = argv[++argument];
		else if (!strcmp(argv[argument], "--runtime"))
			runtime_path = argv[++argument];
		else if (!strcmp(argv[argument], "--device"))
			device = argv[++argument];
		else if (!strcmp(argv[argument], "--save-frame"))
			image_path = argv[++argument];
		else if (!strcmp(argv[argument], "--bind"))
			bind_address = argv[++argument];
		else if (!strcmp(argv[argument], "--log"))
			log_path = argv[++argument];
		else if (!strcmp(argv[argument], "--preview-mode")) {
			const char *mode = argv[++argument];

			if (strcmp(mode, "matched") && strcmp(mode, "async")) {
				fprintf(stderr, "Preview mode must be matched or async\n");
				return EXIT_FAILURE;
			}
			matched_preview = !strcmp(mode, "matched");
		}
		else if (!strcmp(argv[argument], "--port")) {
			char *end;
			errno = 0;
			port = strtoul(argv[++argument], &end, 10);
			if (errno || !*argv[argument] || *end || !port || port > 65535) {
				fprintf(stderr, "Port must be between 1 and 65535\n");
				return EXIT_FAILURE;
			}
		} else {
			fprintf(stderr, "Unknown option: %s\n", argv[argument]);
			return EXIT_FAILURE;
		}
	}
	if (!model_path) {
		usage(argv[0]);
		return EXIT_FAILURE;
	}
	if ((inspect_only && serve) || (daemon && !serve) || (serve && image_path)) {
		fprintf(stderr, "--serve cannot combine with --inspect or --save-frame; --daemon requires --serve\n");
		return EXIT_FAILURE;
	}
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL)) {
		perror("sigaction");
		return EXIT_FAILURE;
	}
	if (prctl(PR_SET_NAME, "pcie_rknn_probe", 0, 0, 0)) {
		perror("set process name");
		return EXIT_FAILURE;
	}
	if (serve) {
		int reuse = 1;
		if (inet_pton(AF_INET, bind_address, &address.sin_addr) != 1) {
			fprintf(stderr, "Invalid IPv4 bind address\n");
			return EXIT_FAILURE;
		}
		address.sin_port = htons((uint16_t)port);
		listener = socket(AF_INET, SOCK_STREAM, 0);
		if (listener < 0 || setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) < 0 ||
		    bind(listener, (struct sockaddr *)&address, sizeof(address)) < 0 || listen(listener, 1) < 0) {
			perror("Listen socket");
			goto cleanup;
		}
		if (daemon) {
			int background_result = start_background(log_path);
			if (background_result != 0) {
				result = background_result < 0 ? EXIT_FAILURE : EXIT_SUCCESS;
				goto cleanup;
			}
		}
	}
	model_data = read_model(model_path, &model_bytes);
	if (!model_data || load_runtime(&functions, runtime_path))
		goto cleanup;
	status = functions.init(&context, model_data, model_bytes, 0, NULL);
	if (status) {
		fprintf(stderr, "rknn_init failed: %d\n", status);
		goto cleanup;
	}
	initialized = true;
	status = functions.query(context, RKNN_QUERY_SDK_VERSION, &version, sizeof(version));
	if (status) {
		fprintf(stderr, "SDK version query failed: %d\n", status);
		goto cleanup;
	}
	printf("RKNN runtime: api=%.*s driver=%.*s\n", (int)sizeof(version.api_version),
	       version.api_version, (int)sizeof(version.drv_version), version.drv_version);
	status = functions.query(context, RKNN_QUERY_IN_OUT_NUM, &counts, sizeof(counts));
	if (status || counts.n_input != 1 || !counts.n_output || counts.n_output > MAX_OUTPUTS) {
		fprintf(stderr, "Unsupported tensor counts: status=%d inputs=%u outputs=%u\n",
			status, counts.n_input, counts.n_output);
		goto cleanup;
	}
	status = functions.query(context, RKNN_QUERY_INPUT_ATTR, &input_attribute, sizeof(input_attribute));
	if (status) {
		fprintf(stderr, "Input attribute query failed: %d; check matching RKNN SDK header/runtime\n", status);
		goto cleanup;
	}
	print_tensor("input", &input_attribute);
	if (input_attribute.n_dims == 4 && input_attribute.dims[0] == 1 &&
	    input_attribute.fmt == RKNN_TENSOR_NHWC) {
		model_height = input_attribute.dims[1];
		model_width = input_attribute.dims[2];
	} else if (input_attribute.n_dims == 4 && input_attribute.dims[0] == 1 &&
		   input_attribute.fmt == RKNN_TENSOR_NCHW) {
		model_height = input_attribute.dims[2];
		model_width = input_attribute.dims[3];
	}
	if (!model_width || !model_height) {
		fprintf(stderr, "Cannot determine image model dimensions\n");
		goto cleanup;
	}
	output_attributes = calloc(counts.n_output, sizeof(*output_attributes));
	outputs = calloc(counts.n_output, sizeof(*outputs));
	detections = calloc(YOLO_MAX_DETECTIONS, sizeof(*detections));
	if (!output_attributes || !outputs || !detections) {
		perror("allocate output descriptors");
		goto cleanup;
	}
	for (index = 0; index < counts.n_output; index++) {
		output_attributes[index].index = index;
		status = functions.query(context, RKNN_QUERY_OUTPUT_ATTR,
					 &output_attributes[index], sizeof(output_attributes[index]));
		if (status || !output_attributes[index].n_elems) {
			fprintf(stderr, "Output attribute query failed: index=%u status=%d\n", index, status);
			goto cleanup;
		}
		print_tensor("output", &output_attributes[index]);
		outputs[index].index = index;
		outputs[index].want_float = float_output || !output_supports_native(&output_attributes[index]);
		printf("output[%u] transfer=%s\n", index, outputs[index].want_float ? "FP32" : "native");
	}
	if (inspect_only) {
		result = EXIT_SUCCESS;
		goto cleanup;
	}
	pixels = malloc(RGB565_BYTES);
	if (!pixels) {
		perror("allocate pixels");
		goto cleanup;
	}
	engine.functions = functions;
	engine.context = context;
	engine.counts = counts;
	engine.input_attribute = input_attribute;
	engine.output_attributes = output_attributes;
	engine.outputs = outputs;
	engine.detections = detections;
	engine.model_width = model_width;
	engine.model_height = model_height;
	if (!serve)
		output_stats = true;
	if (serve) {
		printf("RKNN detection server listening on %s:%lu, result_header=%u pid=%ld preview_mode=%s\n",
		       bind_address, port, RESULT_HEADER_BYTES, (long)getpid(), matched_preview ? "matched" : "async");
		result = run_server(listener, device, &engine, profile, matched_preview) ?
			EXIT_FAILURE : EXIT_SUCCESS;
		goto cleanup;
	}
	started_ns = monotonic_ns();
	source = open(device, O_RDONLY | O_CLOEXEC);
	if (source < 0) {
		perror(device);
		goto cleanup;
	}
	if (read_frame_record(source, pixels, &frame))
		goto cleanup;
	if (close(source)) {
		source = -1;
		perror("close frame source");
		goto cleanup;
	}
	source = -1;
	timing.read_ns = monotonic_ns() - started_ns;
	if (infer_frame(&engine, pixels, &frame, &timing, &detection_count, true))
		goto cleanup;
	printf("RKNN timing: seq=%" PRIu64 " read_ms=%.3f preprocess_ms=%.3f inputs_ms=%.3f "
	       "run_ms=%.3f outputs_ms=%.3f postprocess_ms=%.3f total_ms=%.3f\n",
	       frame.sequence, timing.read_ns / 1e6, timing.preprocess_ns / 1e6,
	       timing.inputs_ns / 1e6, timing.run_ns / 1e6, timing.outputs_ns / 1e6,
	       timing.postprocess_ns / 1e6, (monotonic_ns() - started_ns) / 1e6);
	if (image_path && save_frame(image_path, pixels, &frame))
		goto cleanup;
	printf("PCIe-to-RKNN single-frame inference passed: sequence=%" PRIu64 "\n", frame.sequence);
	result = EXIT_SUCCESS;
cleanup:
	if (source >= 0)
		close(source);
	if (listener >= 0)
		close(listener);
	if (initialized && functions.destroy(context))
		result = EXIT_FAILURE;
	if (functions.library)
		dlclose(functions.library);
	free(outputs);
	free(detections);
	free(output_attributes);
	free(engine.model_input);
	free(pixels);
	free(model_data);
	return result;
}
