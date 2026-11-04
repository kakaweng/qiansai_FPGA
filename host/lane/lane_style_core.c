#include "lane_style_core.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LANE_MAX_PATHS 256
#define LANE_MAX_ROWS 96
#define LANE_MAX_OBSERVATIONS 64
#define LANE_REFERENCE 0.84
#define LANE_HOLD_NS ((uint64_t)LANE_CONFIRMED_HOLD_MS * UINT64_C(1000000))
#define LANE_CONFIRM_FRAMES 3U

struct resize_filter { int first, count; int32_t *weights; };
struct lane_preprocessor {
    unsigned width, height;
    uint8_t *horizontal;
    struct resize_filter *columns, *rows;
};
struct lane_path { unsigned count; float rows[LANE_MAX_ROWS], columns[LANE_MAX_ROWS], scores[LANE_MAX_ROWS], solid[LANE_MAX_ROWS]; };
struct lane_observation { float column, score, solid; };
struct lane_match { float distance; unsigned path, observation; };

static int round_even(double value) { return (int)nearbyint(value); }
static double maximum(double first, double second) { return first > second ? first : second; }
static double minimum(double first, double second) { return first < second ? first : second; }

static struct resize_filter *make_filters(unsigned input_size, unsigned output_size)
{
    struct resize_filter *filters = calloc(output_size, sizeof(*filters));
    double scale = (double)input_size / output_size;
    double support = maximum(1.0, scale);
    unsigned index;
    if (!filters) return NULL;
    for (index = 0; index < output_size; index++) {
        double center = (index + 0.5) * scale, total = 0;
        int first = (int)(center - support + 0.5), last = (int)(center + support + 0.5), offset;
        if (first < 0) first = 0;
        if (last > (int)input_size) last = (int)input_size;
        filters[index].first = first;
        filters[index].count = last - first;
        filters[index].weights = calloc((size_t)(last - first), sizeof(int32_t));
        if (!filters[index].weights) {
            unsigned cleanup;
            for (cleanup = 0; cleanup < index; cleanup++) free(filters[cleanup].weights);
            free(filters);
            return NULL;
        }
        for (offset = first; offset < last; offset++) total += maximum(0, 1 - fabs((offset - center + 0.5) / support));
        for (offset = first; offset < last; offset++) {
            double weight = maximum(0, 1 - fabs((offset - center + 0.5) / support)) / total;
            filters[index].weights[offset - first] = (int32_t)(weight * 4194304 + 0.5);
        }
    }
    return filters;
}

static void free_filters(struct resize_filter *filters, unsigned count)
{
    unsigned index;
    if (!filters) return;
    for (index = 0; index < count; index++) free(filters[index].weights);
    free(filters);
}

struct lane_preprocessor *lane_preprocessor_create(void)
{
    return calloc(1, sizeof(struct lane_preprocessor));
}

void lane_preprocessor_destroy(struct lane_preprocessor *processor)
{
    if (!processor) return;
    free(processor->horizontal);
    free_filters(processor->columns, LANE_INPUT_WIDTH);
    free_filters(processor->rows, LANE_INPUT_HEIGHT);
    free(processor);
}

bool lane_preprocessor_prepare(struct lane_preprocessor *processor, const uint8_t *source,
                               unsigned width, unsigned height, uint8_t *rgb)
{
    unsigned crop_top, crop_height, row, column, channel;
    uint8_t *horizontal;
    struct resize_filter *columns, *rows;
    if (!processor || !source || !rgb || width < 4 || height < 4 || width > 4096 || height > 2160) return false;
    crop_top = (unsigned)round_even(height * 0.35);
    crop_height = height - crop_top;
    if (processor->width != width || processor->height != height) {
        horizontal = malloc((size_t)crop_height * LANE_INPUT_WIDTH * 3);
        columns = make_filters(width, LANE_INPUT_WIDTH);
        rows = make_filters(crop_height, LANE_INPUT_HEIGHT);
        if (!horizontal || !columns || !rows) {
            free(horizontal); free_filters(columns, LANE_INPUT_WIDTH); free_filters(rows, LANE_INPUT_HEIGHT);
            return false;
        }
        free(processor->horizontal);
        free_filters(processor->columns, LANE_INPUT_WIDTH);
        free_filters(processor->rows, LANE_INPUT_HEIGHT);
        processor->horizontal = horizontal; processor->columns = columns; processor->rows = rows;
        processor->width = width; processor->height = height;
    }
    horizontal = processor->horizontal; columns = processor->columns; rows = processor->rows;
    for (row = 0; row < crop_height; row++) {
        for (column = 0; column < LANE_INPUT_WIDTH; column++) {
            int64_t sums[3] = {2097152, 2097152, 2097152};
            int offset;
            for (offset = 0; offset < columns[column].count; offset++) {
                size_t address = ((size_t)(row + crop_top) * width + (unsigned)(columns[column].first + offset)) * 2;
                uint16_t packed = (uint16_t)(source[address] | ((uint16_t)source[address + 1] << 8));
                int red = (packed >> 11) & 31, green = (packed >> 5) & 63, blue = packed & 31;
                int32_t weight = columns[column].weights[offset];
                sums[0] += ((red << 3) | (red >> 2)) * (int64_t)weight;
                sums[1] += ((green << 2) | (green >> 4)) * (int64_t)weight;
                sums[2] += ((blue << 3) | (blue >> 2)) * (int64_t)weight;
            }
            for (channel = 0; channel < 3; channel++) horizontal[((size_t)row * LANE_INPUT_WIDTH + column) * 3 + channel] = (uint8_t)(sums[channel] >> 22);
        }
    }
    for (row = 0; row < LANE_INPUT_HEIGHT; row++) {
        for (column = 0; column < LANE_INPUT_WIDTH; column++) {
            for (channel = 0; channel < 3; channel++) {
                int64_t sum = 2097152;
                int offset;
                for (offset = 0; offset < rows[row].count; offset++)
                    sum += horizontal[((size_t)(rows[row].first + offset) * LANE_INPUT_WIDTH + column) * 3 + channel] * (int64_t)rows[row].weights[offset];
                rgb[((size_t)row * LANE_INPUT_WIDTH + column) * 3 + channel] = (uint8_t)(sum >> 22);
            }
        }
    }
    return true;
}

bool lane_style_prepare_rgb565(const uint8_t *source, unsigned width, unsigned height, uint8_t *rgb)
{
    struct lane_preprocessor *processor = lane_preprocessor_create();
    bool success = lane_preprocessor_prepare(processor, source, width, height, rgb);
    lane_preprocessor_destroy(processor);
    return success;
}

bool lane_style_decode_logits(struct lane_style_core *core, const float *logits, bool nhwc)
{
    size_t pixel;
    if (!core || !logits) return false;
    for (pixel = 0; pixel < LANE_MAP_PIXELS; pixel++) {
        float values[4], highest = -INFINITY, runner = -INFINITY, sum = 0;
        unsigned channel, winner = 0;
        for (channel = 0; channel < 4; channel++) {
            values[channel] = logits[nhwc ? pixel * 4 + channel : channel * LANE_MAP_PIXELS + pixel];
            if (!isfinite(values[channel])) return false;
            if (values[channel] > highest) { runner = highest; highest = values[channel]; winner = channel; }
            else if (values[channel] > runner) runner = values[channel];
        }
        core->classes[pixel] = (uint8_t)winner;
        if (winner != 1 && winner != 2) { core->confidence[pixel] = core->margin[pixel] = 0; continue; }
        for (channel = 0; channel < 4; channel++) sum += expf(values[channel] - highest);
        core->confidence[pixel] = 1 / sum;
        core->margin[pixel] = (1 - expf(runner - highest)) / sum;
    }
    return true;
}

void lane_style_reset(struct lane_style_core *core)
{
    if (!core) return;
    memset(core->history, 0, sizeof(core->history));
    core->last_timestamp_ns = 0; core->has_timestamp = false; core->corridor_count = 0;
    core->source_width = core->source_height = 0;
}

static bool fit_curve(const struct lane_path *path, const bool *selected, double coefficients[3])
{
    double matrix[3][4] = {{0}};
    unsigned index, power, equation;
    for (index = 0; index < path->count; index++) {
        double basis[5], normalized;
        if (selected && !selected[index]) continue;
        normalized = path->rows[index] / 384.0;
        basis[0] = 1;
        for (power = 1; power < 5; power++) basis[power] = basis[power - 1] * normalized;
        for (equation = 0; equation < 3; equation++) {
            for (power = 0; power < 3; power++) matrix[equation][power] += basis[equation + power];
            matrix[equation][3] += basis[equation] * path->columns[index];
        }
    }
    for (equation = 0; equation < 3; equation++) {
        unsigned pivot = equation, other, column;
        double divisor;
        for (other = equation + 1; other < 3; other++) if (fabs(matrix[other][equation]) > fabs(matrix[pivot][equation])) pivot = other;
        if (fabs(matrix[pivot][equation]) < 1e-10) return false;
        for (column = 0; column < 4; column++) { double swap = matrix[equation][column]; matrix[equation][column] = matrix[pivot][column]; matrix[pivot][column] = swap; }
        divisor = matrix[equation][equation];
        for (column = equation; column < 4; column++) matrix[equation][column] /= divisor;
        for (other = 0; other < 3; other++) {
            double factor;
            if (other == equation) continue;
            factor = matrix[other][equation];
            for (column = equation; column < 4; column++) matrix[other][column] -= factor * matrix[equation][column];
        }
    }
    for (equation = 0; equation < 3; equation++) coefficients[equation] = matrix[equation][3];
    return true;
}

static double evaluate(const double coefficients[3], double row)
{
    double normalized = row / 384;
    return coefficients[0] + normalized * (coefficients[1] + normalized * coefficients[2]);
}

static int compare_float(const void *first, const void *second)
{
    float left = *(const float *)first, right = *(const float *)second;
    return (left > right) - (left < right);
}

static int compare_match(const void *first, const void *second)
{
    const struct lane_match *left = first, *right = second;
    if (left->distance != right->distance) return left->distance > right->distance ? 1 : -1;
    if (left->path != right->path) return left->path > right->path ? 1 : -1;
    return (left->observation > right->observation) - (left->observation < right->observation);
}

static int compare_quality(const void *first, const void *second)
{
    const struct lane_boundary *left = first, *right = second;
    return (right->quality > left->quality) - (right->quality < left->quality);
}

static int compare_bottom(const void *first, const void *second)
{
    const struct lane_boundary *left = first, *right = second;
    return (left->bottom > right->bottom) - (left->bottom < right->bottom);
}

static float source_x(double column, unsigned width) { return (float)((column + 0.5) * width / 640 - 0.5); }
static float source_y(double row, unsigned height) { unsigned top = (unsigned)round_even(height * 0.35); return (float)(top + (row + 0.5) * (height - top) / 384 - 0.5); }

static bool make_boundary(const struct lane_path *path, unsigned width, unsigned height, struct lane_boundary *boundary)
{
    double coefficients[3], reference_row, reference_y = height * LANE_REFERENCE;
    float residuals[LANE_MAX_ROWS], sorted[LANE_MAX_ROWS], quality = 0, solid = 0, error = 0;
    bool selected[LANE_MAX_ROWS];
    unsigned index, count = 0;
    float smallest = 384, largest = 0, median, limit;
    unsigned crop_top = (unsigned)round_even(height * 0.35);
    reference_row = (reference_y - crop_top + 0.5) * 384 / (height - crop_top) - 0.5;
    if (path->count < 8 || path->rows[0] - path->rows[path->count - 1] < 64 || reference_row > path->rows[0] + 24) return false;
    if (!fit_curve(path, NULL, coefficients)) return false;
    for (index = 0; index < path->count; index++) sorted[index] = residuals[index] = (float)fabs(evaluate(coefficients, path->rows[index]) - path->columns[index]);
    qsort(sorted, path->count, sizeof(float), compare_float);
    median = path->count & 1 ? sorted[path->count / 2] : (sorted[path->count / 2 - 1] + sorted[path->count / 2]) * 0.5f;
    limit = (float)maximum(4, median * 2.5);
    for (index = 0; index < path->count; index++) {
        selected[index] = residuals[index] <= limit;
        if (!selected[index]) continue;
        count++; smallest = (float)minimum(smallest, path->rows[index]); largest = (float)maximum(largest, path->rows[index]);
        quality += path->scores[index]; solid += path->solid[index];
    }
    if (count < 8 || largest - smallest < 64 || reference_row > largest + 24 || !fit_curve(path, selected, coefficients)) return false;
    for (index = 0; index < path->count; index++) if (selected[index]) error += (float)fabs(evaluate(coefficients, path->rows[index]) - path->columns[index]);
    if (error / count > 4.5) return false;
    memset(boundary, 0, sizeof(*boundary));
    memcpy(boundary->coefficients, coefficients, sizeof(coefficients));
    boundary->bottom = source_x(evaluate(coefficients, reference_row), width);
    if (boundary->bottom < 0 || boundary->bottom >= width) return false;
    for (index = 0; index < LANE_CURVE_POINTS; index++) {
        double row = smallest + (maximum(largest, reference_row) - smallest) * index / (LANE_CURVE_POINTS - 1);
        double column = evaluate(coefficients, row);
        if (column < -32 || column > 672) return false;
        boundary->curve[index].x = (float)round_even(source_x(column, width));
        boundary->curve[index].y = (float)round_even(source_y(row, height));
    }
    boundary->top_x = source_x(evaluate(coefficients, smallest), width);
    boundary->top_y = source_y(smallest, height);
    boundary->quality = quality / count;
    solid /= count;
    boundary->style_fraction = (float)maximum(solid, 1 - solid);
    boundary->observed = boundary->style_fraction >= 0.70 ? (solid >= 0.5 ? LANE_SOLID : LANE_DASHED) : LANE_UNKNOWN;
    return true;
}

static bool supported(const struct lane_style_core *core, int row, int column)
{
    size_t index = (size_t)row * LANE_INPUT_WIDTH + (unsigned)column;
    return (core->classes[index] == 1 || core->classes[index] == 2) && core->confidence[index] >= 0.45f && core->margin[index] >= 0.10f;
}

static bool find_boundaries(const struct lane_style_core *core, unsigned width, unsigned height, struct lane_result *result)
{
    struct lane_path *paths = calloc(LANE_MAX_PATHS, sizeof(*paths));
    struct lane_match *matches = malloc(LANE_MAX_PATHS * LANE_MAX_OBSERVATIONS * sizeof(*matches));
    struct lane_boundary *candidates = calloc(LANE_MAX_PATHS, sizeof(*candidates));
    unsigned path_count = 0, candidate_count = 0, path_index;
    unsigned crop_top = (unsigned)round_even(height * 0.35);
    int first_row = round_even((height * 0.52 - crop_top) * 384 / (height - crop_top));
    int last_row = round_even((height * 0.94 - crop_top) * 384 / (height - crop_top)), row;
    bool success = false;
    if (!paths || !matches || !candidates) goto cleanup;
    if (first_row < 0) first_row = 0;
    if (last_row >= LANE_INPUT_HEIGHT) last_row = LANE_INPUT_HEIGHT - 1;
    for (row = last_row; row >= first_row; row -= 4) {
        struct lane_observation observations[LANE_MAX_OBSERVATIONS];
        bool used_paths[LANE_MAX_PATHS] = {false}, used_observations[LANE_MAX_OBSERVATIONS] = {false};
        unsigned observation_count = 0, match_count = 0, observation_index, match_index;
        int column = 0;
        for (column = 0; column < LANE_INPUT_WIDTH; column++) {
            size_t pixel = (size_t)row * LANE_INPUT_WIDTH + (unsigned)column;
            unsigned side = column < LANE_INPUT_WIDTH / 2 ? 0 : 1;
            if (core->classes[pixel] == 1 || core->classes[pixel] == 2) result->diagnostics.raw_pixels[side]++;
            if (supported(core, row, column)) result->diagnostics.supported_pixels[side]++;
        }
        column = 0;
        while (column < LANE_INPUT_WIDTH) {
            int start, end;
            float score = 0, solid = 0;
            while (column < LANE_INPUT_WIDTH && !supported(core, row, column)) column++;
            start = column;
            while (column < LANE_INPUT_WIDTH && supported(core, row, column)) {
                size_t address = (size_t)row * LANE_INPUT_WIDTH + (unsigned)column;
                score += core->confidence[address]; solid += core->classes[address] == 1;
                column++;
            }
            end = column;
            if (end - start < 2 || end - start > 48) continue;
            if (observation_count == LANE_MAX_OBSERVATIONS) goto cleanup;
            result->diagnostics.runs[(start + end - 1) < LANE_INPUT_WIDTH ? 0 : 1]++;
            observations[observation_count++] = (struct lane_observation){(start + end - 1) * 0.5f, score / (end - start), solid / (end - start)};
        }
        for (path_index = 0; path_index < path_count; path_index++) {
            struct lane_path *path = &paths[path_index];
            unsigned last = path->count - 1;
            float gap = path->rows[last] - row, slope = 0, predicted;
            if (gap > 28) continue;
            if (path->count >= 3) slope = (path->columns[last] - path->columns[last - 2]) / (path->rows[last] - path->rows[last - 2]);
            predicted = path->columns[last] - gap * slope;
            for (observation_index = 0; observation_index < observation_count; observation_index++) {
                float distance = fabsf(observations[observation_index].column - predicted);
                if (distance <= 9 + gap * 0.30f) matches[match_count++] = (struct lane_match){distance, path_index, observation_index};
            }
        }
        qsort(matches, match_count, sizeof(*matches), compare_match);
        for (match_index = 0; match_index < match_count; match_index++) {
            struct lane_match match = matches[match_index];
            struct lane_path *path;
            struct lane_observation observation;
            unsigned next;
            if (used_paths[match.path] || used_observations[match.observation]) continue;
            used_paths[match.path] = used_observations[match.observation] = true;
            path = &paths[match.path]; observation = observations[match.observation]; next = path->count;
            if (next == LANE_MAX_ROWS) goto cleanup;
            path->rows[next] = (float)row; path->columns[next] = observation.column; path->scores[next] = observation.score; path->solid[next] = observation.solid;
            path->count++;
        }
        for (observation_index = 0; observation_index < observation_count; observation_index++) {
            struct lane_path *path;
            if (used_observations[observation_index]) continue;
            if (path_count == LANE_MAX_PATHS) goto cleanup;
            path = &paths[path_count++]; path->count = 1;
            path->rows[0] = (float)row; path->columns[0] = observations[observation_index].column;
            path->scores[0] = observations[observation_index].score; path->solid[0] = observations[observation_index].solid;
        }
    }
    result->diagnostics.paths = path_count;
    for (path_index = 0; path_index < path_count; path_index++) {
        if (make_boundary(&paths[path_index], width, height, &candidates[candidate_count])) {
            result->diagnostics.candidates[candidates[candidate_count].bottom < width * 0.5f ? 0 : 1]++;
            candidate_count++;
        } else result->diagnostics.rejected_geometry++;
    }
    qsort(candidates, candidate_count, sizeof(*candidates), compare_quality);
    for (path_index = 0; path_index < candidate_count; path_index++) {
        unsigned other;
        for (other = 0; other < result->boundary_count; other++) if (fabsf(candidates[path_index].bottom - result->boundaries[other].bottom) <= width * 0.018f) break;
        if (other < result->boundary_count) {
            if (candidates[path_index].observed != result->boundaries[other].observed) {
                result->boundaries[other].observed = LANE_UNKNOWN;
                result->boundaries[other].style_conflict = true;
                result->diagnostics.conflicts++;
            }
        } else {
            if (result->boundary_count == LANE_MAX_BOUNDARIES) goto cleanup;
            result->boundaries[result->boundary_count++] = candidates[path_index];
        }
    }
    qsort(result->boundaries, result->boundary_count, sizeof(*result->boundaries), compare_bottom);
    success = true;
cleanup:
    free(paths); free(matches); free(candidates);
    if (!success) result->boundary_count = 0;
    return success;
}

static bool same_boundary(const struct lane_boundary *first, const struct lane_boundary *second, unsigned width, unsigned height)
{
    float reference_y = (float)(height * LANE_REFERENCE), anchor_y = height * 0.8f;
    float first_anchor = first->top_x + (first->bottom - first->top_x) * (anchor_y - first->top_y) / (reference_y - first->top_y);
    float second_anchor = second->top_x + (second->bottom - second->top_x) * (anchor_y - second->top_y) / (reference_y - second->top_y);
    return fabsf(first->bottom - second->bottom) < width * 0.03f && fabsf(first_anchor - second_anchor) < width * 0.03f;
}

static uint64_t analysis_gap_ns(const struct lane_style_core *core)
{
    unsigned gap=core->analysis_gap_ms;
    if(gap<500 || gap>1200)gap=500;
    return (uint64_t)gap*UINT64_C(1000000);
}

static void confirm_side(struct lane_style_core *core, struct lane_result *result, unsigned side, unsigned width, unsigned height)
{
    struct lane_history *history = &core->history[side];
    struct lane_side *output = &result->sides[side];
    struct lane_boundary *boundary = output->boundary_index < 0 ? NULL : &result->boundaries[output->boundary_index];
    uint64_t now = result->timestamp_ns;
    bool continuous;
    bool was_active = history->active, expired = history->has_confirmation && now - history->confirmed_ns > LANE_HOLD_NS;
    enum lane_line_type previous_pending = history->pending;
    if (history->active && now - history->observed_ns > analysis_gap_ns(core)) { history->active = false; history->count = 0; }
    continuous = boundary && history->active && same_boundary(&history->boundary, boundary, width, height);
    if (history->has_confirmation && now - history->confirmed_ns > LANE_HOLD_NS) history->has_confirmation = false;
    if (boundary && history->has_confirmation && !same_boundary(&history->confirmed_boundary, boundary, width, height)) history->has_confirmation = false;
    output->observed = boundary ? boundary->observed : LANE_UNKNOWN;
    output->confidence = boundary ? boundary->quality : 0;
    if (boundary && boundary->observed != LANE_UNKNOWN) {
        history->unknown_count = 0;
        history->count = continuous && history->pending == boundary->observed ? history->count + 1 : 1;
        history->pending = boundary->observed; history->boundary = *boundary; history->observed_ns = now; history->active = true;
        if (history->has_confirmation && history->confirmed != boundary->observed) history->has_confirmation = false;
        if (history->count >= LANE_CONFIRM_FRAMES) {
            history->confirmed = boundary->observed; history->confirmed_ns = now;
            history->confirmed_boundary = *boundary; history->has_confirmation = true;
            output->type = history->confirmed;
        }
    } else if (boundary && history->has_confirmation && boundary->observed == LANE_UNKNOWN &&
               same_boundary(&history->confirmed_boundary, boundary, width, height) &&
               history->unknown_count < LANE_UNKNOWN_TOLERANCE) {
        history->unknown_count++;
        history->count = 0;
    } else {
        history->count = 0;
        if (boundary) {
            history->unknown_count = 0;
            history->active = false;
            history->has_confirmation = false;
        }
    }
    if (output->observed == LANE_UNKNOWN && history->has_confirmation) {
        output->type = history->confirmed; output->held = true;
        output->confidence = history->confirmed_boundary.quality;
        output->age_ms = (float)((now - history->confirmed_ns) / 1e6);
    }
    output->confirmations = history->count;
    if (output->held) output->reason = LANE_REASON_HOLD;
    else if (output->type != LANE_UNKNOWN) output->reason = LANE_REASON_OK;
    else if (boundary && boundary->observed == LANE_UNKNOWN)
        output->reason = boundary->style_conflict ? LANE_REASON_CONFLICT : LANE_REASON_MIXED;
    else if (boundary) {
        output->reason = !continuous && was_active ? LANE_REASON_MOVED :
                         continuous && previous_pending != boundary->observed ? LANE_REASON_TYPE : LANE_REASON_CONFIRM;
    } else if (expired) output->reason = LANE_REASON_EXPIRED;
    else if (!result->diagnostics.raw_pixels[side]) output->reason = LANE_REASON_NO_PIXELS;
    else if (!result->diagnostics.supported_pixels[side]) output->reason = LANE_REASON_LOW_SCORE;
    else if (!result->diagnostics.runs[side]) output->reason = LANE_REASON_NO_RUNS;
    else if (!result->diagnostics.candidates[side]) output->reason = LANE_REASON_NO_CURVE;
    else output->reason = LANE_REASON_NO_SIDE;
    output->decision_type = output->held ? LANE_UNKNOWN : output->type;
}

static float curve_at(const struct lane_boundary *boundary, float row)
{
    unsigned index;
    for (index = 1; index < LANE_CURVE_POINTS; index++) {
        if (row <= boundary->curve[index].y) {
            float span = boundary->curve[index].y - boundary->curve[index - 1].y;
            return boundary->curve[index - 1].x + (boundary->curve[index].x - boundary->curve[index - 1].x) * (row - boundary->curve[index - 1].y) / (span > 0 ? span : 1);
        }
    }
    return boundary->curve[LANE_CURVE_POINTS - 1].x;
}

bool lane_style_locate(struct lane_style_core *core, unsigned width, unsigned height, uint64_t sequence, uint64_t timestamp_ns, struct lane_result *result)
{
    unsigned index, side;
    bool located;
    if (!core || !result) return false;
    memset(result, 0, sizeof(*result));
    result->sequence = sequence; result->timestamp_ns = timestamp_ns;
    result->sides[0].boundary_index = result->sides[1].boundary_index = -1;
    if (width < 4 || height < 4 || width > 4096 || height > 2160) { lane_style_reset(core); return false; }
    if (core->has_timestamp && timestamp_ns > core->last_timestamp_ns)
        result->diagnostics.input_gap_ms = (timestamp_ns - core->last_timestamp_ns) / 1e6;
    if (core->source_width != width || core->source_height != height) {
        result->diagnostics.reset = LANE_RESET_SIZE;
        lane_style_reset(core);
    }
    if (core->has_timestamp && (timestamp_ns <= core->last_timestamp_ns || timestamp_ns - core->last_timestamp_ns > analysis_gap_ns(core))) {
        result->diagnostics.reset = timestamp_ns <= core->last_timestamp_ns ? LANE_RESET_CLOCK : LANE_RESET_GAP;
        lane_style_reset(core);
    }
    core->last_timestamp_ns = timestamp_ns; core->has_timestamp = true;
    core->source_width = width; core->source_height = height;
    located = find_boundaries(core, width, height, result);
    if (!located) { lane_style_reset(core); return false; }
    for (index = 0; index < result->boundary_count; index++) {
        if (result->boundaries[index].bottom < width * 0.5f) result->sides[0].boundary_index = (int)index;
        else if (result->sides[1].boundary_index < 0) result->sides[1].boundary_index = (int)index;
    }
    for (side = 0; side < 2; side++) confirm_side(core, result, side, width, height);
    if (result->sides[0].boundary_index >= 0 && result->sides[1].boundary_index >= 0) {
        const struct lane_boundary *left = &result->boundaries[result->sides[0].boundary_index], *right = &result->boundaries[result->sides[1].boundary_index];
        float top = (float)maximum(height * 0.60, maximum(left->top_y, right->top_y));
        float bottom = (float)minimum(height * 0.94, minimum(left->curve[31].y, right->curve[31].y));
        float span = right->bottom - left->bottom;
        bool valid = top < height * LANE_REFERENCE - height * 0.10 && span >= width * 0.09f && span <= width * 0.75f;
        for (index = 0; index < 24 && valid; index++) {
            float row = top + (bottom - top) * index / 23, observed_span = curve_at(right, row) - curve_at(left, row);
            if (observed_span <= width * 0.025f || (!index && observed_span > span * 1.05f)) valid = false;
        }
        result->diagnostics.corridor_geometry_valid = valid;
        if (valid) {
            bool continuous = core->corridor_count && same_boundary(left, &core->corridor_pair[0], width, height) && same_boundary(right, &core->corridor_pair[1], width, height);
            core->corridor_count = continuous ? core->corridor_count + 1 : 1;
            core->corridor_pair[0] = *left; core->corridor_pair[1] = *right;
            result->corridor_confirmed = core->corridor_count >= 2;
            result->offset = (width * 0.5f - (left->bottom + right->bottom) * 0.5f) / span;
        } else core->corridor_count = 0;
    } else core->corridor_count = 0;
    result->diagnostics.corridor_confirmations = core->corridor_count;
    return located;
}

const char *lane_style_type_name(enum lane_line_type type)
{
    return type == LANE_SOLID ? "SOLID" : type == LANE_DASHED ? "DASHED" : "UNKNOWN";
}

const char *lane_style_reason_name(enum lane_reason reason)
{
    static const char *const names[] = {"OK", "NO-PIX", "LOW-SCORE", "NO-RUNS", "NO-CURVE", "NO-SIDE",
        "MIXED", "CONFLICT", "CONFIRM", "MOVED", "TYPE", "HOLD", "EXPIRED", "WAIT", "STALE", "ERROR"};
    return (unsigned)reason < sizeof(names) / sizeof(names[0]) ? names[reason] : "ERROR";
}

const char *lane_style_reset_name(enum lane_reset_reason reason)
{
    static const char *const names[] = {"NONE", "SIZE", "CLOCK", "GAP"};
    return (unsigned)reason < sizeof(names) / sizeof(names[0]) ? names[reason] : "ERROR";
}

static uint16_t packed_color(unsigned red, unsigned green, unsigned blue)
{
    return (uint16_t)(((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3));
}

static void blend_pixel(uint8_t *pixels, unsigned width, unsigned column, unsigned row, unsigned red, unsigned green, unsigned blue, unsigned alpha)
{
    size_t address = ((size_t)row * width + column) * 2;
    uint16_t before = (uint16_t)(pixels[address] | ((uint16_t)pixels[address + 1] << 8));
    unsigned old_red = (before >> 11) & 31, old_green = (before >> 5) & 63, old_blue = before & 31;
    uint16_t after = packed_color((((old_red << 3) | (old_red >> 2)) * (100 - alpha) + red * alpha) / 100,
                                  (((old_green << 2) | (old_green >> 4)) * (100 - alpha) + green * alpha) / 100,
                                  (((old_blue << 3) | (old_blue >> 2)) * (100 - alpha) + blue * alpha) / 100);
    pixels[address] = (uint8_t)after; pixels[address + 1] = (uint8_t)(after >> 8);
}

static uint16_t glyph(char character)
{
    switch (character) {
    case 'A': return 0x5f6f; case 'D': return 0x6b6e; case 'E': return 0x79a7; case 'F': return 0x79a4;
    case 'H': return 0x5bed; case 'I': return 0x7497; case 'K': return 0x5bad; case 'L': return 0x4927;
    case 'N': return 0x5fed; case 'O': return 0x2b6a; case 'P': return 0x6ba4; case 'R': return 0x6bad;
    case 'S': return 0x388e; case 'T': return 0x7492; case 'U': return 0x5b6f; case 'W': return 0x5bfd;
    case 'G': return 0x3b6b; case '0': return 0x7b6f; case '1': return 0x2492; case '2': return 0x73e7;
    case '3': return 0x73cf; case '4': return 0x5bc9; case ':': return 0x0410; case '-': return 0x01c0;
    case '5': return 0x79cf; case '6': return 0x79ef; case '7': return 0x7249;
    case '8': return 0x7bef; case '9': return 0x7bcf; case 'C': return 0x3923;
    case 'Y': return 0x5a92; case 'M': return 0x5fed;
    case 'V': return 0x5b6a; case 'X': return 0x5aad;
    case '/': return 0x12a4;
    default: return 0;
    }
}

static void draw_label(uint8_t *output, unsigned width, unsigned left, unsigned top, unsigned tile_width, const char *text)
{
    unsigned character, row, column, block_y, block_x;
    for (character = 0; text[character] && (character + 1) * 8 < tile_width; character++) {
        uint16_t bits = glyph(text[character]);
        for (row = 0; row < 5; row++) for (column = 0; column < 3; column++) if (bits & (UINT16_C(1) << (14 - row * 3 - column)))
            for (block_y = 0; block_y < 2; block_y++) for (block_x = 0; block_x < 2; block_x++)
                blend_pixel(output, width, left + character * 8 + column * 2 + block_x, top + row * 2 + block_y, 255, 255, 255, 100);
    }
}

static void draw_view(const uint8_t *classes, const struct lane_result *result,
                            uint8_t *output, unsigned output_width, unsigned output_height,
                            unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height,
                            unsigned source_width, unsigned source_height)
{
    unsigned row, column, side;
    unsigned crop_top;
    const struct lane_boundary *left, *right;
    char label[96];
    if (!classes || !result || !output || source_width < 4 || source_height < 4 || tile_width < 16 || tile_height < 20 ||
        tile_width > output_width || tile_height > output_height || tile_left > output_width - tile_width || tile_top > output_height - tile_height) return;
    crop_top = (unsigned)round_even(source_height * 0.35);
    left = result->sides[0].boundary_index < 0 ? NULL : &result->boundaries[result->sides[0].boundary_index];
    right = result->sides[1].boundary_index < 0 ? NULL : &result->boundaries[result->sides[1].boundary_index];
    for (row = 0; row < tile_height; row++) {
        float source_row = row * (float)source_height / tile_height;
        int model_row = (int)floorf((source_row - crop_top) * 384 / (source_height - crop_top));
        float corridor_left = left ? curve_at(left, source_row) : 0, corridor_right = right ? curve_at(right, source_row) : 0;
        bool corridor = result->corridor_confirmed && left && right && source_row >= maximum(left->top_y, right->top_y) && source_row <= minimum(left->curve[31].y, right->curve[31].y);
        for (column = 0; column < tile_width; column++) {
            float source_column = column * (float)source_width / tile_width;
            unsigned model_column = (unsigned)(source_column * 640 / source_width);
            unsigned identifier = model_row >= 0 && model_row < 384 && model_column < 640 ? classes[(unsigned)model_row * 640 + model_column] : 0;
            if (corridor && source_column > corridor_left && source_column < corridor_right)
                blend_pixel(output, output_width, tile_left + column, tile_top + row, 80, 220, 80, 18);
            if (identifier == 1) blend_pixel(output, output_width, tile_left + column, tile_top + row, 255, 170, 0, 45);
            else if (identifier == 2) blend_pixel(output, output_width, tile_left + column, tile_top + row, 0, 210, 255, 45);
            else if (identifier == 3) blend_pixel(output, output_width, tile_left + column, tile_top + row, 210, 90, 210, 45);
        }
        for (side = 0; side < 2; side++) {
            const struct lane_boundary *boundary = side ? right : left;
            int line_column, thickness;
            if (!boundary || source_row < boundary->curve[0].y || source_row > boundary->curve[31].y) continue;
            line_column = round_even(curve_at(boundary, source_row) * tile_width / source_width);
            for (thickness = -1; thickness <= 1; thickness++) if (line_column + thickness >= 0 && line_column + thickness < (int)tile_width) {
                unsigned red = boundary->observed == LANE_SOLID ? 255 : boundary->observed == LANE_DASHED ? 0 : 180;
                unsigned green = boundary->observed == LANE_SOLID ? 170 : boundary->observed == LANE_DASHED ? 210 : 180;
                unsigned blue = boundary->observed == LANE_SOLID ? 0 : boundary->observed == LANE_DASHED ? 255 : 180;
                blend_pixel(output, output_width, tile_left + (unsigned)(line_column + thickness), tile_top + row, red, green, blue, 100);
            }
        }
    }
#ifdef HAVE_FRONT_P1
    /* P1 has its own Chinese panel. Keep the sky visible for lamp selection. */
    if (tile_left == 213 && tile_top == 0 && tile_width == 853 && tile_height == 480) return;
#endif
    for (row = 0; row < 16; row++) for (column = 0; column < tile_width; column++)
        blend_pixel(output, output_width, tile_left + column, tile_top + row, 0, 0, 0, 80);
    snprintf(label, sizeof(label), "L:%s%s R:%s%s", lane_style_type_name(result->sides[0].type), result->sides[0].held ? " HOLD" : "",
             lane_style_type_name(result->sides[1].type), result->sides[1].held ? " HOLD" : "");
    draw_label(output, output_width, tile_left + 5, tile_top + 3, tile_width - 5, label);
}

void lane_style_draw_rgb565(const struct lane_style_core *core, const struct lane_result *result,
                            uint8_t *output, unsigned output_width, unsigned output_height,
                            unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height,
                            unsigned source_width, unsigned source_height)
{
    if (core) draw_view(core->classes, result, output, output_width, output_height,
                       tile_left, tile_top, tile_width, tile_height, source_width, source_height);
}

void lane_style_draw_view_rgb565(const struct lane_style_view *view, uint8_t *output,
                                 unsigned output_width, unsigned output_height,
                                 unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height)
{
    if (view) draw_view(view->classes, &view->result, output, output_width, output_height,
                       tile_left, tile_top, tile_width, tile_height, view->width, view->height);
}

void lane_style_draw_status_rgb565(uint8_t *output, unsigned output_width, unsigned output_height,
                                   unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height,
                                   const char *text)
{
    unsigned row, column;
    if (!output || !text || tile_width < 16 || tile_height < 32 || tile_width > output_width || tile_height > output_height ||
        tile_left > output_width - tile_width || tile_top > output_height - tile_height) return;
    for (row = 16; row < 32; row++) for (column = 0; column < tile_width; column++)
        blend_pixel(output, output_width, tile_left + column, tile_top + row, 0, 0, 0, 80);
    draw_label(output, output_width, tile_left + 5, tile_top + 19, tile_width - 5, text);
}

void lane_style_draw_reasons_rgb565(const struct lane_result *result, uint8_t *output,
                                    unsigned output_width, unsigned output_height,
                                    unsigned tile_left, unsigned tile_top, unsigned tile_width, unsigned tile_height)
{
    unsigned row, column;
    char text[112];
    if (!result || !output || tile_width < 16 || tile_height < 48 || tile_width > output_width || tile_height > output_height ||
        tile_left > output_width - tile_width || tile_top > output_height - tile_height) return;
    snprintf(text, sizeof(text), "WHY L:%s %u/3 R:%s %u/3", lane_style_reason_name(result->sides[0].reason),
             result->sides[0].confirmations < 3 ? result->sides[0].confirmations : 3,
             lane_style_reason_name(result->sides[1].reason), result->sides[1].confirmations < 3 ? result->sides[1].confirmations : 3);
    for (row = 32; row < 48; row++) for (column = 0; column < tile_width; column++)
        blend_pixel(output, output_width, tile_left + column, tile_top + row, 0, 0, 0, 80);
    draw_label(output, output_width, tile_left + 5, tile_top + 35, tile_width - 5, text);
}
