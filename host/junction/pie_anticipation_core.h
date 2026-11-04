#ifndef PIE_ANTICIPATION_CORE_H
#define PIE_ANTICIPATION_CORE_H

#include "front_world.h"

#define PA_PERSONS 16U
#define PA_HISTORY 6U
#define PA_JSON 8192U
#define PA_MAX_AGE_MS 750U

enum pa_status { PA_WAITING_HISTORY, PA_CADENCE, PA_AMBIGUOUS, PA_SCORED };

struct pa_person {
    unsigned id, box[4], samples;
    uint64_t sampled_ms;
    enum pa_status status;
    float history[PA_HISTORY][4], scores[2];
};

struct pie_anticipation {
    unsigned next_id, count;
    uint32_t producer;
    uint64_t object_seq, object_ms, last_now;
    bool has_source;
    const char *reason;
    struct pa_person persons[PA_PERSONS];
};

bool pa_predict(const float boxes[PA_HISTORY][4], float scores[2]);
void pa_reset(struct pie_anticipation *state);
void pa_update(struct pie_anticipation *state, const struct front_world *world, uint64_t now, bool running);
bool pa_json(const struct pie_anticipation *state, uint64_t now, bool running, char *out, size_t capacity);

#endif
