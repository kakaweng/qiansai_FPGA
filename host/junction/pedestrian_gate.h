#ifndef PEDESTRIAN_GATE_H
#define PEDESTRIAN_GATE_H
#include "front_world.h"
#include "crosswalk_observation.h"

#define PG_TRACKS 16U
#define PG_JSON 6000U
#define PG_SOURCE_AGE_MS 750U
#define PG_PAIR_SKEW_MS 350U
#define PG_ASSOCIATION_AGE_MS 850U

struct pg_track {
    unsigned id, box[4], score, samples, contacts;
    uint64_t first_ms, seen_ms, contact_ms, crossing_seq;
    bool observed, contact, confirmed;
};
struct pedestrian_gate {
    uint32_t producer;
    unsigned next_id, total, relevant, eligible;
    uint64_t object_seq, object_ms, crosswalk_seq, crosswalk_ms, clear_since, last_now;
    unsigned clear_samples;
    bool valid;
    const char *status;
    struct pg_track tracks[PG_TRACKS];
};

static inline void pg_reset(struct pedestrian_gate *gate)
{
    memset(gate, 0, sizeof(*gate));
    gate->next_id = 1;
    gate->status = "WAITING";
}

static inline unsigned pg_difference(unsigned first, unsigned second)
{ return first > second ? first - second : second - first; }

static inline bool pg_matches(const struct pg_track *track, const unsigned box[4])
{
    unsigned width = track->box[2] - track->box[0], height = track->box[3] - track->box[1];
    unsigned next_width = box[2] - box[0], next_height = box[3] - box[1];
    unsigned limit = width + next_width + 180;
    return next_width * 2 >= width && width * 2 >= next_width &&
        next_height * 2 >= height && height * 2 >= next_height &&
        pg_difference(track->box[0] + track->box[2], box[0] + box[2]) < limit &&
        pg_difference(track->box[3], box[3]) < height / 2 + 150;
}

static inline bool pg_pixel(const struct crosswalk_observation *crossing, unsigned column, unsigned row)
{
    unsigned bit = row * CW_PLANE_WIDTH + column;
    return (crossing->mask[bit / 8] >> (bit % 8)) & 1U;
}

static inline bool pg_contact(const struct crosswalk_observation *crossing, const unsigned box[4])
{
    unsigned column = ((box[0] + box[2]) * 639U + 10000U) / 20000U / 2U;
    unsigned row = (box[3] * 359U + 5000U) / 10000U / 2U;
    unsigned horizontal, vertical, support = 0, nearby = 0;
    unsigned left = column > 16 ? column - 16 : 0;
    unsigned right = column + 16 < CW_PLANE_WIDTH ? column + 16 : CW_PLANE_WIDTH - 1;
    unsigned top = row > 2 ? row - 2 : 0;
    unsigned bottom = row + 2 < CW_PLANE_HEIGHT ? row + 2 : CW_PLANE_HEIGHT - 1;
    for (vertical = top; vertical <= bottom; vertical++) {
        for (horizontal = left; horizontal <= right; horizontal++) {
            if (pg_pixel(crossing, horizontal, vertical)) {
                support++;
                if (pg_difference(horizontal, column) <= 2) nearby++;
            }
        }
    }
    if (support < 12 || nearby < 3) return false;
    if (pg_pixel(crossing, column, row)) return true;
    /* Close only a small, two-sided segmentation hole. A single nearby stripe
       or broad convex hull must not turn roadside people into crossing people. */
    if (nearby >= 6) {
        unsigned step;
        bool before = false, after = false;
        for (step = 1; step <= 3; step++) {
            if (column >= step && pg_pixel(crossing, column - step, row)) before = true;
            if (column + step < CW_PLANE_WIDTH && pg_pixel(crossing, column + step, row)) after = true;
        }
        if (before && after) return true;
        before = after = false;
        for (step = 1; step <= 2; step++) {
            if (row >= step && pg_pixel(crossing, column, row - step)) before = true;
            if (row + step < CW_PLANE_HEIGHT && pg_pixel(crossing, column, row + step)) after = true;
        }
        if (before && after) return true;
    }
    return false;
}

static inline void pg_update(struct pedestrian_gate *gate, const struct front_world *world,
                             const struct crosswalk_observation *crossing, uint64_t now, bool running)
{
    unsigned index, detection, eligible = 0, uncertain = 0, active = 0, current_contact = 0;
    bool assigned[PG_TRACKS] = {false};
    bool fresh_objects, paired, new_object;
    uint64_t skew;
    if (!gate->status) pg_reset(gate);
    if (!running || (gate->last_now && now < gate->last_now) ||
        (gate->producer && world->producer != gate->producer) ||
        (gate->object_ms && (world->object_ms < gate->object_ms || world->object_seq < gate->object_seq)))
        pg_reset(gate);
    gate->last_now = now;
    fresh_objects = running && world->producer && (world->flags & 2) &&
        world->sent <= now && now - world->sent <= PG_SOURCE_AGE_MS &&
        world->object_ms && world->object_ms <= now && now - world->object_ms <= PG_SOURCE_AGE_MS;
    if (!fresh_objects) {
        gate->valid = false; gate->relevant = 0; gate->status = "STALE";
        gate->clear_since = gate->clear_samples = 0;
        for (index = 0; index < PG_TRACKS; index++) gate->tracks[index].observed = false;
        return;
    }
    gate->producer = world->producer;
    skew = world->object_ms > crossing->source_ms ? world->object_ms - crossing->source_ms : crossing->source_ms - world->object_ms;
    paired = crossing->valid && crossing->producer == world->producer && crossing->source_ms <= now &&
        now - crossing->source_ms <= PG_ASSOCIATION_AGE_MS && skew <= PG_PAIR_SKEW_MS;
    new_object = world->object_seq != gate->object_seq || world->object_ms != gate->object_ms;
    if (!new_object && (!paired || crossing->source_seq == gate->crosswalk_seq)) {
        if (!paired && gate->eligible) {
            gate->valid = false; gate->relevant = 0; gate->status = "UNALIGNED";
        }
        return;
    }
    if (new_object) {
        for (index = 0; index < PG_TRACKS; index++) {
            struct pg_track *track = &gate->tracks[index];
            track->observed = false;
            if (track->seen_ms > world->object_ms || world->object_ms - track->seen_ms > 900)
                memset(track, 0, sizeof(*track));
        }
    }
    gate->total = 0;
    for (detection = 0; detection < world->object_count; detection++) {
        const struct fw_object *person = &world->objects[detection];
        struct pg_track *chosen = NULL;
        unsigned center;
        bool contact;
        if (person->cls != 0) continue;
        gate->total++;
        center = (person->box[0] + person->box[2]) / 2;
        if (person->score < 4500 || person->box[3] - person->box[1] < 300 ||
            center < 1800 || center > 9500 || person->box[3] < 4800 || person->box[3] >= 9700) continue;
        eligible++;
        for (index = 0; index < PG_TRACKS; index++) {
            struct pg_track *track = &gate->tracks[index];
            if (track->id && !assigned[index] && (new_object ? pg_matches(track, person->box) :
                !memcmp(track->box, person->box, sizeof(track->box)))) {
                chosen = track; break;
            }
        }
        if (!chosen && new_object) {
            for (index = 0; index < PG_TRACKS; index++) {
                if (!gate->tracks[index].id) { chosen = &gate->tracks[index]; break; }
            }
            if (chosen) { chosen->id = gate->next_id++; chosen->first_ms = world->object_ms; }
        }
        if (!chosen) { uncertain++; continue; }
        assigned[chosen - gate->tracks] = true;
        contact = paired && pg_contact(crossing, person->box);
        if (new_object) {
            if (chosen->samples < 1000) chosen->samples++;
            chosen->seen_ms = world->object_ms;
            memcpy(chosen->box, person->box, sizeof(chosen->box));
            chosen->score = person->score;
        }
        chosen->observed = true;
        chosen->contact = contact;
        if (contact) {
            if (chosen->contact_ms && world->object_ms - chosen->contact_ms > 1100) chosen->contacts = 0;
            if (crossing->source_seq != chosen->crossing_seq) {
                chosen->contacts++;
                chosen->crossing_seq = crossing->source_seq;
            }
            chosen->contact_ms = world->object_ms;
            chosen->confirmed = chosen->samples >= 2 && chosen->contacts >= 2 &&
                world->object_ms - chosen->first_ms >= 150;
        } else if (world->object_ms - chosen->contact_ms > 450) {
            chosen->confirmed = false;
            chosen->contacts = 0;
        }
        if (paired && chosen->confirmed && world->object_ms - chosen->contact_ms <= 450) {
            active++; if (contact) current_contact++;
        }
        else if (contact || !paired || chosen->samples < 2) uncertain++;
    }
    gate->object_seq = world->object_seq; gate->object_ms = world->object_ms;
    gate->crosswalk_seq = paired ? crossing->source_seq : 0;
    gate->crosswalk_ms = paired ? crossing->source_ms : 0;
    gate->relevant = active; gate->eligible = eligible; gate->valid = false;
    if (active) {
        gate->status = current_contact ? "YIELD" : "YIELD_HOLD"; gate->valid = true;
        gate->clear_since = gate->clear_samples = 0;
    } else if (uncertain) {
        gate->status = paired ? "CONFIRMING" : "UNALIGNED";
        gate->clear_since = gate->clear_samples = 0;
    } else {
        if (new_object) {
            if (!gate->clear_since) gate->clear_since = world->object_ms;
            gate->clear_samples++;
        }
        gate->valid = gate->clear_samples >= 2 && world->object_ms - gate->clear_since >= 200;
        gate->status = gate->valid ? (gate->total || eligible ? "OBSERVE" : "CLEAR") : "CONFIRMING";
    }
}

static inline bool pg_json(const struct pedestrian_gate *gate, uint64_t now, char *out, size_t capacity)
{
    size_t used = 0;
    unsigned index, count = 0;
    bool fresh = gate->object_ms && gate->object_ms <= now && now - gate->object_ms <= PG_SOURCE_AGE_MS;
    if (!fw_append(out, capacity, &used,
        "{\"version\":1,\"policy\":\"crosswalk-contact-v1\",\"status\":\"%s\",\"producer\":%u,"
        "\"object_seq\":%llu,\"object_ms\":%llu,\"crosswalk_seq\":%llu,\"crosswalk_ms\":%llu,"
        "\"age_ms\":%lld,\"total\":%u,\"relevant\":%u,\"persons\":[",
        fresh ? gate->status : "STALE", gate->producer, (unsigned long long)gate->object_seq,
        (unsigned long long)gate->object_ms, (unsigned long long)gate->crosswalk_seq,
        (unsigned long long)gate->crosswalk_ms, fresh ? (long long)(now - gate->object_ms) : -1LL,
        gate->total, fresh ? gate->relevant : 0)) return false;
    for (index = 0; fresh && index < PG_TRACKS; index++) {
        const struct pg_track *track = &gate->tracks[index];
        if (!track->observed) continue;
        if (!fw_append(out, capacity, &used,
            "%s{\"id\":%u,\"box\":[%u,%u,%u,%u],\"samples\":%u,\"contact\":%s,\"confirmed\":%s}",
            count++ ? "," : "", track->id, track->box[0], track->box[1], track->box[2], track->box[3],
            track->samples, track->contact ? "true" : "false", track->confirmed ? "true" : "false")) return false;
    }
    return fw_append(out, capacity, &used, "]}");
}
#endif
