#ifndef DRIVE_ASSIST_H
#define DRIVE_ASSIST_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef HAVE_FRONT_P1
#include "front_p1.h"
#endif
/* Local monotonic milliseconds only. No range/TTC estimates without calibration. */
struct drive_evidence {
    uint64_t received, lane_stamp, object_stamp[3], front_scene, right_scene;
    unsigned line[2], signal, vehicles[3], people;
};
struct drive_assist {
    struct drive_evidence evidence;
    unsigned request, request_id, speed;
    char gear;
    uint64_t request_at;
    bool running, solid_alarm;
    const char *feedback;
#ifdef HAVE_FRONT_P1
    struct front_p1 p1;
    unsigned stable_line[2];
    uint64_t stable_lane_stamp[2], lane_seen_stamp;
    bool lane_held[2];
#endif
};
void drive_reset(struct drive_assist *d);
bool drive_action(struct drive_assist *d, const char *action, uint64_t now);
void drive_update(struct drive_assist *d, bool running, uint64_t now);
bool drive_accept(struct drive_assist *d, const char *report, uint32_t session, uint64_t now);
#ifdef FRONT_ONLY
bool drive_accept_front(struct drive_assist *d, const char *report, uint64_t now);
#endif
void drive_json(struct drive_assist *d, uint64_t now, char *out, size_t capacity);
#endif
