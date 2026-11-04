#include "road_object_core.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const unsigned anchors[3][6] = {{10,13,16,30,33,23},{30,61,62,45,59,119},{116,90,156,198,373,326}};
static float clamp(float value, float low, float high) { return fminf(high, fmaxf(low, value)); }
static void rgb_pixel(const uint8_t *source, unsigned *r, unsigned *g, unsigned *b)
{
    unsigned packed = source[0] | (unsigned)source[1] << 8;
    *r = (packed >> 11) & 31; *g = (packed >> 5) & 63; *b = packed & 31;
    *r = (*r << 3) | (*r >> 2); *g = (*g << 2) | (*g >> 4); *b = (*b << 3) | (*b >> 2);
}

bool road_object_prepare(const uint8_t *source, unsigned width, unsigned height, uint8_t *rgb)
{
    unsigned y, x, resized_h, pad, step;
    if (!source || !rgb || !((width == 1920 && height == 1080) || (width == 640 && height == 480))) return false;
    resized_h = width == 1920 ? 360 : 480; pad = (640 - resized_h) / 2; step = width / 640;
    memset(rgb, 114, 640U * 640U * 3U);
    for (y = 0; y < resized_h; y++) for (x = 0; x < 640; x++) {
        unsigned r, g, b;
        size_t from = ((size_t)(y * step + step / 2) * width + x * step + step / 2) * 2;
        size_t to = ((size_t)(y + pad) * 640 + x) * 3;
        rgb_pixel(source + from, &r, &g, &b); rgb[to] = (uint8_t)r; rgb[to + 1] = (uint8_t)g; rgb[to + 2] = (uint8_t)b;
    }
    return true;
}

static float value(const struct road_head *head, size_t index)
{
    if (head->type == ROAD_F32) return ((const float *)head->data)[index];
    return ((head->type == ROAD_I8 ? ((const int8_t *)head->data)[index] : ((const uint8_t *)head->data)[index]) - head->zero_point) * head->scale;
}
static bool wanted(unsigned cls) { return cls == 0 || cls == 1 || cls == 2 || cls == 3 || cls == 5 || cls == 7 || cls == 9; }
static int compare_boxes(const void *first, const void *second)
{
    float a = ((const struct road_box *)first)->score, b = ((const struct road_box *)second)->score;
    return (a < b) - (a > b);
}
static float iou(const struct road_box *a, const struct road_box *b)
{
    float intersect = fmaxf(0, fminf(a->right, b->right) - fmaxf(a->left, b->left)) * fmaxf(0, fminf(a->bottom, b->bottom) - fmaxf(a->top, b->top));
    float total = (a->right - a->left) * (a->bottom - a->top) + (b->right - b->left) * (b->bottom - b->top) - intersect;
    return total > 0 ? intersect / total : 0;
}
bool road_object_decode_sized(const struct road_head heads[3], struct road_box *workspace, struct road_object_result *result,
                               unsigned source_width, unsigned source_height)
{
    unsigned head_index, seen = 0, candidates = 0, index;
    uint8_t suppressed[ROAD_YOLO_CANDIDATES] = {0};
    float scale, pad;
    if (!heads || !workspace || !result || !((source_width == 1920 && source_height == 1080) ||
        (source_width == 640 && source_height == 480))) return false;
    scale = source_width / 640.0f; pad = (640 - source_height / scale) / 2;
    result->count = result->vehicles = result->people = result->lamps = 0;
    for (head_index = 0; head_index < 3; head_index++) {
        const struct road_head *head = &heads[head_index];
        unsigned level = head->grid == 80 ? 0 : head->grid == 40 ? 1 : 2;
        unsigned grid = head->grid, area = grid * grid, anchor, cell;
        size_t bytes = head->type == ROAD_F32 ? sizeof(float) : 1;
        if ((grid != 80 && grid != 40 && grid != 20) || (seen & (1U << level)) || !head->data ||
            head->type > ROAD_U8 || head->bytes != (size_t)area * 255 * bytes ||
            (head->type != ROAD_F32 && (!isfinite(head->scale) || head->scale <= 0))) return false;
        seen |= 1U << level;
        for (anchor = 0; anchor < 3; anchor++) for (cell = 0; cell < area; cell++) {
            size_t base = (size_t)anchor * 85 * area + cell;
            float objectness = value(head, base + 4 * area), probability, score;
            unsigned cls = 0, candidate;
            struct road_box box;
            if (!isfinite(objectness)) return false;
            if (objectness < .25f) continue;
            probability = value(head, base + 5 * area);
            if (!isfinite(probability)) return false;
            for (candidate = 1; candidate < 80; candidate++) {
                float next = value(head, base + (5 + candidate) * area);
                if (!isfinite(next)) return false;
                if (next > probability) { probability = next; cls = candidate; }
            }
            score = objectness * probability;
            if (score < .25f || !wanted(cls)) continue;
            {
                float x = value(head, base), y = value(head, base + area);
                float w = value(head, base + 2 * area), h = value(head, base + 3 * area);
                if (!isfinite(x) || !isfinite(y) || !isfinite(w) || !isfinite(h)) return false;
                x = (x * 2 - .5f + cell % grid) * (640U / grid);
                y = (y * 2 - .5f + cell / grid) * (640U / grid);
                w = w * w * 4 * anchors[level][anchor * 2]; h = h * h * 4 * anchors[level][anchor * 2 + 1];
                box = (struct road_box){x-w/2, y-h/2, x+w/2, y+h/2, score, cls};
            }
            if (candidates == ROAD_YOLO_CANDIDATES) return false; /* Never silently publish a truncated candidate set. */
            workspace[candidates++] = box;
        }
    }
    qsort(workspace, candidates, sizeof(*workspace), compare_boxes);
    for (index = 0; index < candidates; index++) {
        unsigned next;
        struct road_box box = workspace[index];
        if (suppressed[index]) continue;
        for (next = index + 1; next < candidates; next++) if (!suppressed[next] && box.class_id == workspace[next].class_id && iou(&box, &workspace[next]) > .45f) suppressed[next] = 1;
        box.left = clamp(box.left * scale, 0, source_width); box.right = clamp(box.right * scale, 0, source_width);
        box.top = clamp((box.top - pad) * scale, 0, source_height); box.bottom = clamp((box.bottom - pad) * scale, 0, source_height);
        if (box.right <= box.left || box.bottom <= box.top) continue;
        if (result->count == ROAD_OBJECT_CAPACITY) break;
        result->boxes[result->count++] = box;
        if (box.class_id == 0) result->people++;
        else if (box.class_id == 9) result->lamps++;
        else result->vehicles++;
    }
    return seen == 7;
}
bool road_object_decode(const struct road_head heads[3], struct road_box *workspace, struct road_object_result *result)
{ return road_object_decode_sized(heads, workspace, result, 1920, 1080); }
void road_object_vehicles_only(struct road_object_result *result)
{
    unsigned i, count = 0;
    for (i = 0; i < result->count; i++) {
        unsigned cls = result->boxes[i].class_id;
        if (cls == 1 || cls == 2 || cls == 3 || cls == 5 || cls == 7) result->boxes[count++] = result->boxes[i];
    }
    result->count = result->vehicles = count; result->people = result->lamps = 0;
    result->signal = result->raw_signal = ROAD_SIGNAL_UNKNOWN;
    result->signal_evidence_ns = 0; result->signal_held = result->signal_conflict = false;
}

/* YOLO-box HSV evidence, with a lower-fill green-symbol profile for arrows.
 * Sparse green symbols require stronger dark housing than compact lamps.
 * No scene ROI fallback, arrow direction or inferred turn permission. */
static unsigned pixel_color(unsigned r, unsigned g, unsigned b)
{
    unsigned hi = r > g ? r : g, lo = r < g ? r : g;
    float hue;
    hi = hi > b ? hi : b; lo = lo < b ? lo : b;
    if (hi < 160 || !hi || (hi - lo) * 255U < 110U * hi) return 0;
    if (hi == r) hue = 30.0f * ((float)g - b) / (hi - lo);
    else if (hi == g) hue = 60 + 30.0f * ((float)b - r) / (hi - lo);
    else hue = 120 + 30.0f * ((float)r - g) / (hi - lo);
    if (hue < 0) hue += 180;
    if (hue <= 10 || hue >= 170) return ROAD_SIGNAL_RED;
    if (hue >= 15 && hue <= 38) return ROAD_SIGNAL_YELLOW;
    if (hue >= 45 && hue <= 100) return ROAD_SIGNAL_GREEN;
    return 0;
}

static unsigned lamp_colors(const uint8_t *source, unsigned width, unsigned height, const struct road_box *box)
{
    unsigned colors = 0, x, y, r, g, b;
    int margin = (int)fmaxf(6, nearbyintf(fminf(box->right - box->left, box->bottom - box->top) * .3f));
    unsigned left = (unsigned)clamp(floorf(box->left) - margin, 0, width), top = (unsigned)clamp(floorf(box->top) - margin, 0, height);
    unsigned right = (unsigned)clamp(ceilf(box->right) + margin, 0, width), bottom = (unsigned)clamp(ceilf(box->bottom) + margin, 0, height);
    unsigned w = right - left, h = bottom - top;
    unsigned area_limit = 1400;
    size_t pixels = (size_t)w * h;
    uint8_t *mask;
    uint32_t *queue;
#ifdef HAVE_FRONT_P1
    /* Night-time bloom grows with the detected lamp. Keep a bounded,
     * box-relative limit instead of rejecting every component above 1400px. */
    area_limit = (unsigned)fmaxf(1400, fminf(8000,
        (box->right-box->left)*(box->bottom-box->top)*.9f));
#endif
    if (!w || !h || pixels > 400000) return 0;
    mask = calloc(pixels, 1); queue = malloc(pixels * sizeof(*queue));
    if (!mask || !queue) { free(mask); free(queue); return 0; }
    for (y = 0; y < h; y++) for (x = 0; x < w; x++) {
        rgb_pixel(source + ((size_t)(top + y) * width + left + x) * 2, &r, &g, &b);
        mask[(size_t)y * w + x] = (uint8_t)pixel_color(r, g, b);
    }
    for (y = 0; y < h; y++) for (x = 0; x < w; x++) {
        unsigned color = mask[(size_t)y * w + x], count = 1, read = 0, min_x=x, max_x=x, min_y=y, max_y=y;
        unsigned px, py, box_w, box_h, dark = 0, total = 0, patch_margin;
        bool compact;
        if (!color) continue;
        queue[0] = y * w + x; mask[(size_t)y * w + x] = 0;
        while (read < count) {
            unsigned current = queue[read++], cx = current % w, cy = current / w;
            int dx, dy;
            if (cx < min_x) min_x = cx;
            if (cx > max_x) max_x = cx;
            if (cy < min_y) min_y = cy;
            if (cy > max_y) max_y = cy;
            for (dy = -1; dy <= 1; dy++) for (dx = -1; dx <= 1; dx++) {
                int nx = (int)cx + dx, ny = (int)cy + dy;
                size_t next;
                if (nx < 0 || ny < 0 || nx >= (int)w || ny >= (int)h) continue;
                next = (size_t)ny * w + (unsigned)nx;
                if (mask[next] == color) { mask[next] = 0; queue[count++] = (uint32_t)next; }
            }
        }
        box_w = max_x - min_x + 1; box_h = max_y - min_y + 1;
        compact = (float)count / (box_w * box_h) >= .45f;
#ifdef HAVE_FRONT_P1
        if (!compact && color == ROAD_SIGNAL_GREEN) {
            unsigned neutral_core = 0;
            /* The saturated green ring of a night lamp surrounds a white
             * overexposed centre. Count central white pixels for shape only;
             * they never create colour evidence on their own. */
            for (py = min_y + box_h/4; py <= max_y - box_h/4; py++)
                for (px = min_x + box_w/4; px <= max_x - box_w/4; px++) {
                    unsigned low, high;
                    rgb_pixel(source + ((size_t)(top+py)*width + left+px)*2, &r, &g, &b);
                    low = r < g ? r : g; low = low < b ? low : b;
                    high = r > g ? r : g; high = high > b ? high : b;
                    if (low >= 200 && high-low <= 60) neutral_core++;
                }
            if (neutral_core >= 6 && (float)(count+neutral_core)/(box_w*box_h) >= .45f) compact = true;
        }
#endif
        if (count < 6 || count > area_limit || (float)box_w / box_h < .5f || (float)box_w / box_h > 1.8f ||
            (!compact && (color != ROAD_SIGNAL_GREEN || (float)count / (box_w * box_h) < .25f))) continue;
        patch_margin = (unsigned)fmaxf(3, nearbyintf(fmaxf(box_w, box_h) * .3f));
        for (py = min_y > patch_margin ? min_y - patch_margin : 0; py < h && py < max_y + 1 + patch_margin; py++)
            for (px = min_x > patch_margin ? min_x - patch_margin : 0; px < w && px < max_x + 1 + patch_margin; px++) {
                rgb_pixel(source + ((size_t)(top + py) * width + left + px) * 2, &r, &g, &b);
                total++; if (r < 100 && g < 100 && b < 100) dark++;
            }
        if (total && (float)dark / total >= (compact ? .25f : .35f)) colors |= 1U << color;
    }
    free(mask); free(queue); return colors;
}

void road_signal_update(struct road_signal_history *history, const uint8_t *source, unsigned width, unsigned height,
                         uint64_t timestamp, struct road_object_result *result)
{
    unsigned colors = 0, index;
    enum road_signal raw = ROAD_SIGNAL_UNKNOWN;
    if (!history || !source || !result || width != 1920 || height != 1080) return;
    if (history->has_timestamp && (timestamp <= history->timestamp_ns || timestamp - history->timestamp_ns > UINT64_C(500000000))) memset(history, 0, sizeof(*history));
    history->has_timestamp = true; history->timestamp_ns = timestamp;
    memset(result->lamp_color, 0, sizeof(result->lamp_color));
    for (index = 0; index < result->count; index++) if (result->boxes[index].class_id == 9) {
        const struct road_box *box = &result->boxes[index];
        float x = (box->left + box->right) * .5f, y = (box->top + box->bottom) * .5f;
        bool central = x >= width * .15f && x <= width * .85f && y >= height * .04f && y <= height * .60f;
        unsigned individual = 0, color;
#ifdef HAVE_FRONT_P1
        individual = lamp_colors(source, width, height, box);
#else
        if (central) individual = lamp_colors(source, width, height, box);
#endif
        if (central) colors |= individual;
        for (color = ROAD_SIGNAL_RED; color <= ROAD_SIGNAL_GREEN; color++)
            if (individual == (1U << color)) result->lamp_color[index] = color;
    }
    for (index = ROAD_SIGNAL_RED; index <= ROAD_SIGNAL_GREEN; index++) if (colors == (1U << index)) raw = (enum road_signal)index;
    result->signal_conflict = colors && raw == ROAD_SIGNAL_UNKNOWN;
    if (raw == ROAD_SIGNAL_UNKNOWN) {
        history->pending = ROAD_SIGNAL_UNKNOWN; history->confirmations = 0;
        if (colors || !history->has_evidence || timestamp - history->evidence_ns > UINT64_C(500000000)) history->state = ROAD_SIGNAL_UNKNOWN;
    } else {
        history->evidence_ns = timestamp; history->has_evidence = true;
        history->confirmations = history->pending == raw ? history->confirmations + 1 : 1;
        history->pending = raw;
        if (history->state != raw) history->state = history->confirmations >= 3 ? raw : ROAD_SIGNAL_UNKNOWN;
    }
    result->signal = history->state; result->raw_signal = raw; result->signal_confirmations = history->confirmations;
    result->signal_held = result->signal != ROAD_SIGNAL_UNKNOWN && raw == ROAD_SIGNAL_UNKNOWN;
    result->signal_evidence_ns = history->evidence_ns;
}
const char *road_object_label(unsigned cls)
{
    switch (cls) {
    case 0: return "PERSON"; case 1: return "BICYCLE"; case 2: return "CAR"; case 3: return "MOTORCYCLE";
    case 5: return "BUS"; case 7: return "TRUCK"; case 9: return "LIGHT"; default: return "UNKNOWN";
    }
}
const char *road_signal_name(enum road_signal signal)
{
    switch (signal) { case ROAD_SIGNAL_RED: return "RED"; case ROAD_SIGNAL_YELLOW: return "YELLOW"; case ROAD_SIGNAL_GREEN: return "GREEN"; default: return "UNKNOWN"; }
}

static void put(uint8_t *output, unsigned width, unsigned x, unsigned y, uint16_t color)
{
    size_t at = ((size_t)y * width + x) * 2; output[at] = (uint8_t)color; output[at + 1] = (uint8_t)(color >> 8);
}
static unsigned glyph(char ch)
{
    static const unsigned letters[26] = {0x5f6f,0x6bae,0x3923,0x6b6e,0x79a7,0x79a4,0x3b6b,0x5bed,0x7497,0x124a,0x5bad,0x4927,0x5fed,0x5fed,0x2b6a,0x6ba4,0x2b7b,0x6bad,0x388e,0x7492,0x5b6f,0x5b6a,0x5bfd,0x5aad,0x5a92,0x72a7};
    static const unsigned digits[10] = {0x7b6f,0x2492,0x73e7,0x73cf,0x5bc9,0x79cf,0x79ef,0x7249,0x7bef,0x7bcf};
    if (ch >= 'A' && ch <= 'Z') return letters[ch-'A'];
    if (ch >= '0' && ch <= '9') return digits[ch-'0'];
    return ch == ':' ? 0x0410 : ch == '-' ? 0x01c0 : 0;
}
static void label(uint8_t *out, unsigned width, unsigned x, unsigned y, unsigned max_width, const char *text, uint16_t color)
{
    unsigned character, row, column;
    for (character=0; text[character] && (character+1)*8 < max_width; character++) {
        unsigned bits=glyph(text[character]);
        for (row=0; row<10; row++) for (column=0; column<6; column++) if (bits & (1U << (14-row/2*3-column/2))) put(out,width,x+character*8+column,y+row,color);
    }
}
void road_object_draw(const struct road_object_result *result, bool usable, const char *status,
                       uint8_t *out, unsigned width, unsigned height,
                       unsigned left, unsigned top, unsigned tile_width, unsigned tile_height)
{
    road_object_draw_sized(result,usable,status,out,width,height,left,top,tile_width,tile_height,1920,1080,false);
}
void road_object_draw_sized(const struct road_object_result *result, bool usable, const char *status,
                             uint8_t *out, unsigned width, unsigned height,
                             unsigned left, unsigned top, unsigned tile_width, unsigned tile_height,
                             unsigned source_width, unsigned source_height, bool side_view)
{
    unsigned index, x, y, header;
    char text[112];
    if (!result || !status || !out || !source_width || !source_height || tile_width < 32 || tile_height < 96 || tile_width > width || tile_height > height || left > width-tile_width || top > height-tile_height) return;
    if (usable) for (index=0; index<result->count; index++) {
        const struct road_box *box=&result->boxes[index];
        unsigned x0=(unsigned)clamp(box->left*tile_width/source_width,0,tile_width-1), x1=(unsigned)clamp(box->right*tile_width/source_width,0,tile_width-1);
        unsigned y0=(unsigned)clamp(box->top*tile_height/source_height,0,tile_height-1), y1=(unsigned)clamp(box->bottom*tile_height/source_height,0,tile_height-1);
        bool held=!strncmp(status,"OBJ:HOLD",8);
        uint16_t color=held ? 0x8410 : box->class_id == 0 ? 0xf81f : box->class_id == 9 ? 0xffe0 : 0x07e0;
        if (x1<=x0 || y1<=y0) continue;
        for (x=x0; x<=x1; x++) { put(out,width,left+x,top+y0,color); put(out,width,left+x,top+y1,color); }
        for (y=y0; y<=y1; y++) { put(out,width,left+x0,top+y,color); put(out,width,left+x1,top+y,color); }
        snprintf(text,sizeof(text),"%s %u%s",road_object_label(box->class_id),(unsigned)(box->score*100),held?" HOLD":"");
        if (y0+12<tile_height) label(out,width,left+x0,top+y0+2,tile_width-x0,text,color);
    }
#ifdef HAVE_FRONT_P1
    if (!side_view && left == 213 && top == 0 && tile_width == 853 && tile_height == 480) return;
#endif
    /* Top three lines belong to lane diagnostics; keep these two lines below. */
    for (header=side_view ? 0 : 48; header<(side_view ? 32U : 80U); header++) for (x=0; x<tile_width; x++) put(out,width,left+x,top+header,0);
    label(out,width,left+5,top+(side_view ? 3 : 51),tile_width-5,status,0xffff);
    if (usable) snprintf(text,sizeof(text),"SIGNAL:%s%s CAR:%u PERSON:%u LIGHT:%u",road_signal_name(result->signal),result->signal_held ? " HOLD" : "",result->vehicles,result->people,result->lamps);
    else snprintf(text,sizeof(text),"SIGNAL:UNKNOWN");
#ifdef HAVE_FRONT_P1
    snprintf(text,sizeof(text),"SIGNAL:SELECT IN PANEL CAR:%u PERSON:%u LIGHT:%u",
             usable ? result->vehicles : 0, usable ? result->people : 0, usable ? result->lamps : 0);
#endif
    if (side_view) snprintf(text,sizeof(text),"CAR:%u UNCALIBRATED",usable ? result->vehicles : 0);
    label(out,width,left+5,top+(side_view ? 19 : 67),tile_width-5,text,0xffff);
}
