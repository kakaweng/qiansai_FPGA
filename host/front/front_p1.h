#ifndef FRONT_P1_H
#define FRONT_P1_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define FRONT_P1_MAX_LIGHTS 16U
#define FRONT_P1_SIGNAL_LIVE_MS 1000U
#define FRONT_P1_SIGNAL_HOLD_MS 1500U
#define FRONT_P1_LANE_HOLD_MS 1500U
#define FRONT_P1_PEOPLE_LIVE_MS 750U
#define FRONT_SCENE_STOP_MM 15000U
#define FRONT_SCENE_END_MM 60000U
struct front_scene {
    bool enabled, active, crossed, violation;
    unsigned distance_mm, speed_mm_s, run_id;
    uint64_t tick_at;
    const char *phase;
};
struct front_p1 {
    unsigned direction, roi[4], signal, pending, confirmations, matches;
    unsigned people;
    unsigned selected[4], speed, led, event_id;
    uint64_t bound_at, observed_at, received_at, first_green_at, force_until;
    uint64_t evidence_at, target_seen_at;
    uint64_t people_at;
    unsigned candidates[FRONT_P1_MAX_LIGHTS][5], candidate_count;
    bool bound, armed, alarm, has_selected;
    bool held, current_evidence, conflict, people_valid;
    bool people_spatial;
    const char *people_state;
    const char *reason;
    struct front_scene scene;
};
void front_p1_reset(struct front_p1 *state);
bool front_p1_bind(struct front_p1 *state, const unsigned roi[4], uint64_t now);
bool front_p1_pick(struct front_p1 *state, unsigned x, unsigned y, uint64_t now);
bool front_p1_action(struct front_p1 *state, const char *action, bool running, uint64_t now);
bool front_p1_accept(struct front_p1 *state, const char *report, uint64_t now);
void front_p1_people(struct front_p1 *state, bool valid, unsigned count, uint64_t stamp, uint64_t now);
void front_p1_update(struct front_p1 *state, bool running, uint64_t now);
void front_p1_json(const struct front_p1 *state, uint64_t now, char *out, size_t capacity);
#endif
