#include "junction_demo_core.h"
#include <math.h>
#include <string.h>

static void jd_rgb(const uint8_t *pixels, unsigned width, unsigned column, unsigned row,
                   unsigned *red, unsigned *green, unsigned *blue)
{
    size_t offset = ((size_t)row * width + column) * 2;
    unsigned packed = pixels[offset] | (unsigned)pixels[offset + 1] << 8;
    *red = ((packed >> 11) & 31) * 255 / 31;
    *green = ((packed >> 5) & 63) * 255 / 63;
    *blue = (packed & 31) * 255 / 31;
}

void jd_init(struct jd_state *state)
{
    memset(state, 0, sizeof(*state));
    state->direction = 2;
    state->armed = true;
    state->phase = "WAIT_PERCEPTION";
    state->led_mode = 5;
}

unsigned jd_lamp(const uint8_t *pixels, unsigned width, unsigned height)
{
    unsigned counts[4] = {0};
    unsigned row, column, red, green, blue;
    if (!pixels || width != 1920 || height != 1080) return 0;
    for (row = height * 28 / 100; row < height * 43 / 100; row++)
        for (column = width * 23 / 100; column < width * 37 / 100; column++) {
            jd_rgb(pixels, width, column, row, &red, &green, &blue);
            if (red > 170 && red > green * 17 / 10 && red > blue * 16 / 10) counts[1]++;
            if (green > 170 && green > red * 12 / 10 && green > blue * 9 / 10) counts[3]++;
        }
    if (counts[1] >= 6 && counts[3] >= 6) return 0;
    if (counts[1] >= 6) return 1;
    if (counts[3] >= 6) return 3;
    return 0;
}

bool jd_crosswalk_contact(const uint8_t *pixels, unsigned width, unsigned height, const struct road_box *box)
{
    int row, column, first_row, last_row, first_column, last_column;
    unsigned yellow = 0, rows = 0;
    float box_height, foot_x, foot_y, reach;
    if (!pixels || !box || box->class_id != 0 || box->score < .35f) return false;
    box_height = box->bottom - box->top;
    foot_x = (box->left + box->right) * .5f;
    foot_y = box->bottom;
    if (box_height < height * .035f || foot_y < height * .48f || foot_y > height * .95f) return false;
    reach = fmaxf(width * .018f, box_height * .55f);
    first_column = (int)fmaxf(0, foot_x - reach);
    last_column = (int)fminf(width - 1, foot_x + reach);
    first_row = (int)fmaxf(0, foot_y - box_height * .08f);
    last_row = (int)fminf(height - 1, foot_y + box_height * .18f);
    for (row = first_row; row <= last_row; row += 2) {
        unsigned count = 0;
        for (column = first_column; column <= last_column; column += 2) {
            unsigned red, green, blue;
            jd_rgb(pixels, width, (unsigned)column, (unsigned)row, &red, &green, &blue);
            if (red > 100 && green > 65 && red > blue * 15 / 10 && green > blue * 12 / 10 && red > green && red < green * 22 / 10)
                count++;
        }
        yellow += count;
        if (count >= (unsigned)fmaxf(3, reach * .15f)) rows++;
    }
    return rows >= 2 && yellow >= (unsigned)fmaxf(8, reach * .7f);
}

void jd_observe(struct jd_state *state, unsigned source, const struct road_object_result *result,
                const uint8_t *pixels, unsigned width, unsigned height, uint64_t now)
{
    struct jd_observation *observation;
    unsigned index, contacts = 0;
    if (!state || source > 1) return;
    observation = &state->sources[source];
    if (!result || result->count > ROAD_OBJECT_CAPACITY || !pixels ||
        width != (source ? 640U : 1920U) || height != (source ? 480U : 1080U) ||
        !result->timestamp_ns || result->timestamp_ns / 1000000 > now ||
        now - result->timestamp_ns / 1000000 > 1800) {
        observation->valid = false;
        observation->streak = 0;
        observation->risk_until = observation->risk_count = 0;
        if (!source) state->signal = 0;
        return;
    }
    if (observation->valid && result->sequence == observation->sequence) return;
    if (observation->valid && now - observation->received_ms > 1800) {
        observation->streak = observation->clear_streak = 0;
        observation->risk_until = observation->risk_count = 0;
        if (!source) state->signal_candidate = state->signal_confirmations = 0;
    }
    if (observation->valid && (result->timestamp_ns <= observation->objects.timestamp_ns || result->sequence < observation->sequence)) {
        unsigned direction = state->direction;
        jd_init(state);
        state->direction = direction;
        observation = &state->sources[source];
    }
    observation->objects = *result;
    observation->sequence = result->sequence;
    observation->received_ms = now;
    observation->valid = true;
    memset(observation->contacts, 0, sizeof(observation->contacts));
    for (index = 0; index < result->count; index++) {
        observation->contacts[index] = jd_crosswalk_contact(pixels, width, height, &result->boxes[index]);
        contacts += observation->contacts[index];
    }
    if (contacts) {
        observation->streak++;
        observation->clear_streak = 0;
        observation->clear_since = 0;
        if (observation->streak >= 2) {
            observation->risk_until = now + 1600;
            observation->risk_count = contacts;
        }
    } else {
        observation->streak = 0;
        observation->clear_streak++;
        if (!observation->clear_since) observation->clear_since = now;
        if (observation->clear_streak >= 3 && now - observation->clear_since >= 900)
            observation->risk_until = 0;
    }
    if (!source) {
        unsigned signal = jd_lamp(pixels, width, height);
        if (signal) {
            if (signal != state->signal_candidate) {
                state->signal_candidate = signal;
                state->signal_confirmations = 0;
                state->signal_candidate_ms = now;
            }
            state->signal_confirmations++;
            if (signal == 1 || (state->signal_confirmations >= 2 && now - state->signal_candidate_ms >= 100)) {
                state->signal = signal;
                state->signal_ms = result->timestamp_ns / 1000000;
            }
        } else {
            state->signal_candidate = state->signal_confirmations = 0;
        }
    }
    state->revision++;
    jd_tick(state, now);
}

bool jd_action(struct jd_state *state, const char *action, uint64_t now)
{
    jd_tick(state, now);
    if (!strcmp(action, "left")) state->direction = 1;
    else if (!strcmp(action, "straight")) state->direction = 2;
    else if (!strcmp(action, "right")) state->direction = 3;
    else if (!strcmp(action, "go")) state->armed = true;
    else if (!strcmp(action, "brake")) {
        state->armed = false;
        state->force_until = state->alarm_until = 0;
    } else if (!strcmp(action, "reset")) jd_init(state);
    else if (!strcmp(action, "force")) {
        bool pedestrian = false;
        for (unsigned source = 0; source < 2; source++) {
            uint64_t captured = state->sources[source].objects.timestamp_ns / 1000000;
            if (!state->sources[source].valid || now < captured || now - captured > 1800) return false;
            pedestrian |= state->sources[source].risk_until > now;
        }
        if (!state->signal && !state->entered && !pedestrian) return false;
        state->armed = true;
        state->force_until = now + 1500;
        if (state->signal == 1 || state->signal == 2 || pedestrian) state->alarm_until = now + 3000;
    } else return false;
    state->revision++;
    jd_tick(state, now);
    return true;
}

void jd_tick(struct jd_state *state, uint64_t now)
{
    bool fresh[2], danger = false;
    unsigned source;
    double elapsed = state->last_tick && now >= state->last_tick ? fmin(.2, (now - state->last_tick) / 1000.) : 0;
    if (state->last_tick && now < state->last_tick) { jd_init(state); elapsed = 0; }
    state->last_tick = now;
    for (source = 0; source < 2; source++) {
        uint64_t captured = state->sources[source].objects.timestamp_ns / 1000000;
        fresh[source] = state->sources[source].valid && now >= captured && now - captured <= 1800;
        if (fresh[source] && state->sources[source].risk_until > now) danger = true;
    }
    if (now < state->signal_ms || now - state->signal_ms > 1400) state->signal = 0;
    state->speed = 0;
    if (!fresh[0] || !fresh[1]) { state->phase = "WAIT_PERCEPTION"; state->led_mode = 5; state->force_until = 0; }
    else if (!state->armed) { state->phase = "PAUSED"; state->led_mode = 3; }
    else if (state->force_until > now) { state->phase = "FORCED"; state->speed = 8; state->led_mode = state->alarm_until > now ? 6 : 2; }
    else if (state->signal == 1 || state->signal == 2) { state->phase = "STOP_RED"; state->led_mode = 3; }
    else if (danger) { state->phase = "YIELD"; state->led_mode = 4; }
    else if (state->signal == 3 || state->entered) { state->phase = state->entered ? "CRUISE" : "GO"; state->speed = 12; state->led_mode = 2; }
    else { state->phase = "WAIT_SIGNAL"; state->led_mode = 5; }
    if (state->alarm_until > now && state->led_mode != 5) state->led_mode = 6;
    state->distance += state->speed / 3.6 * elapsed;
    if (state->distance >= 10 && state->signal == 3) state->entered = true;
}
