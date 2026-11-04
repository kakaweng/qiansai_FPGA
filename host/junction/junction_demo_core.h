#ifndef JUNCTION_DEMO_CORE_H
#define JUNCTION_DEMO_CORE_H
#include "road_object_core.h"
#define JD_MAX_BOXES 48
struct jd_observation {
    struct road_object_result objects;
    unsigned contacts[ROAD_OBJECT_CAPACITY];
    uint64_t received_ms, sequence;
    bool valid;
    unsigned risk_count, streak, clear_streak;
    uint64_t risk_until, clear_since;
};
struct jd_state {
    struct jd_observation sources[2];
    unsigned direction, signal, signal_candidate, signal_confirmations;
    uint64_t signal_ms, signal_candidate_ms, last_tick, alarm_until, force_until;
    uint64_t revision;
    bool armed, entered;
    double distance, speed;
    unsigned led_mode;
    const char *phase;
};
void jd_init(struct jd_state *state);
unsigned jd_lamp(const uint8_t *pixels, unsigned width, unsigned height);
bool jd_crosswalk_contact(const uint8_t *pixels, unsigned width, unsigned height, const struct road_box *box);
void jd_observe(struct jd_state *state, unsigned source, const struct road_object_result *result,
                const uint8_t *pixels, unsigned width, unsigned height, uint64_t now);
bool jd_action(struct jd_state *state, const char *action, uint64_t now);
void jd_tick(struct jd_state *state, uint64_t now);
#endif
