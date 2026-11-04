#include "front_p1.h"
#include <stdio.h>
#include <string.h>

static bool recent(uint64_t stamp, uint64_t now, uint64_t limit)
{ return stamp && stamp <= now && now - stamp <= limit; }

static bool signal_controlled(const struct front_p1 *state)
{ return state->direction != 3; }

void front_p1_people(struct front_p1 *state, bool valid, unsigned count, uint64_t stamp, uint64_t now)
{
    state->people = count <= 128 ? count : 0;
    state->people_at = stamp;
    state->people_valid = valid && count <= 128 && recent(stamp, now, FRONT_P1_PEOPLE_LIVE_MS);
}

static void clear_signal(struct front_p1 *state)
{
    state->signal = state->pending = state->confirmations = 0;
    state->first_green_at = state->evidence_at = 0;
    state->held = state->current_evidence = false;
}

void front_p1_reset(struct front_p1 *state)
{
    memset(state, 0, sizeof(*state));
    state->reason = "SELECT_DIRECTION";
    state->scene.phase = "READY";
}

static void scene_reset(struct front_p1 *s, uint64_t now)
{
    unsigned run = s->scene.run_id + 1;
    memset(&s->scene, 0, sizeof(s->scene));
    s->scene.enabled = true;
    s->scene.run_id = run;
    s->scene.tick_at = now;
    s->scene.phase = "READY";
    s->armed = s->alarm = false;
    s->force_until = 0;
    s->speed = s->led = 0;
}

static bool scene_action(struct front_p1 *s, const char *action, bool running, uint64_t now)
{
    if (!strcmp(action, "scene_open")) {
        if (!s->scene.enabled) scene_reset(s, now);
        if (!s->direction) s->direction = 2;
    } else if (!strcmp(action, "scene_close")) {
        s->scene.enabled = s->scene.active = false;
        s->scene.speed_mm_s = 0;
        s->armed = s->alarm = false;
        s->force_until = 0;
    } else if (!s->scene.enabled) return false;
    else if (!strcmp(action, "scene_reset")) scene_reset(s, now);
    else if (!strcmp(action, "scene_pause")) {
        s->scene.active = false;
        s->scene.speed_mm_s = 0;
        s->scene.phase = "PAUSED";
    } else if (!strcmp(action, "scene_start")) {
        if (!running || s->scene.distance_mm >= FRONT_SCENE_END_MM) return false;
        /* Starting the scene confirms a single visible candidate, just as the old G button.
         * Multiple targets still require an explicit click. Never bind a later arrival silently. */
        if (!s->scene.crossed && signal_controlled(s) && !s->bound &&
            recent(s->observed_at, now, FRONT_P1_SIGNAL_LIVE_MS)) {
            unsigned count = 0, selected = 0, i;
            for (i = 0; i < s->candidate_count; i++) {
                const unsigned *box = s->candidates[i];
                unsigned cx = (box[0]+box[2])/2, cy = (box[1]+box[3])/2;
                if (cx >= 1500 && cx <= 8500 && cy >= 400 && cy <= 6500) { count++; selected = i; }
            }
            if (count == 1) front_p1_bind(s, s->candidates[selected], now);
        }
        s->scene.active = true;
        s->scene.tick_at = now;
    } else if (!strcmp(action, "scene_force")) {
        if (!running || !signal_controlled(s) || s->scene.crossed || s->scene.distance_mm != FRONT_SCENE_STOP_MM ||
            !s->bound || !s->current_evidence ||
            !recent(s->evidence_at, now, FRONT_P1_SIGNAL_LIVE_MS) ||
            !recent(s->received_at, now, 750) || (s->signal != 1 && s->signal != 2)) return false;
        s->scene.active = s->scene.crossed = s->scene.violation = true;
        s->scene.tick_at = now;
        s->event_id++;
    } else return false;
    return true;
}

static void scene_update(struct front_p1 *s, bool running, bool green_ready, uint64_t now)
{
    struct front_scene *v = &s->scene;
    uint64_t dt = v->tick_at && now >= v->tick_at ? now - v->tick_at : 0;
    bool clock_gap = v->tick_at && (now < v->tick_at || dt > 750);
    unsigned step;
    v->tick_at = now;
    s->speed = s->led = v->speed_mm_s = 0;
    s->armed = v->active;
    s->alarm = v->violation;
    if (!running || clock_gap) {
        v->active = s->armed = false;
        v->phase = "DISCONNECTED";
        return;
    }
    if (v->distance_mm >= FRONT_SCENE_END_MM) {
        v->active = s->armed = false;
        v->phase = "COMPLETE";
        return;
    }
    if (!v->active) {
        if (strcmp(v->phase, "READY") && strcmp(v->phase, "DISCONNECTED")) v->phase = "PAUSED";
        return;
    }
    if (s->direction == 3 && (!s->people_valid || s->people)) {
        v->phase = s->people_valid ? "WAIT_PEDESTRIAN" : "WAIT_PERCEPTION";
        return;
    }
    if (v->distance_mm < FRONT_SCENE_STOP_MM) {
        v->phase = "APPROACH";
        v->speed_mm_s = s->direction == 3 ? 2000 : 3000;
        step = (unsigned)(dt * v->speed_mm_s / 1000);
        if (step > FRONT_SCENE_STOP_MM - v->distance_mm) step = FRONT_SCENE_STOP_MM - v->distance_mm;
        v->distance_mm += step;
    }
    if (v->distance_mm == FRONT_SCENE_STOP_MM && !v->crossed) {
        v->speed_mm_s = 0;
        if (s->direction == 3) { v->crossed = true; v->phase = "RIGHT_GO"; }
        else if (!s->bound) v->phase = "SELECT_LIGHT";
        else if (!green_ready) v->phase = s->signal == 1 ? "WAIT_RED" : s->signal == 2 ? "WAIT_YELLOW" : "WAIT_SIGNAL";
        else { v->crossed = true; v->phase = "CROSSING"; }
        /* Consume no crossing distance on the same tick that reaches the line. */
    } else if (v->crossed) {
        /* Entry permission is latched at the line. Later lamp changes never strand
         * a vehicle in the intersection. Explicit pause / stream loss still stop it. */
        v->phase = v->violation ? "VIOLATION" : s->direction == 3 ? "RIGHT_GO" : "CROSSING";
        v->speed_mm_s = s->direction == 3 ? 2000 : 4000;
        step = (unsigned)(dt * v->speed_mm_s / 1000);
        if (step > FRONT_SCENE_END_MM - v->distance_mm) step = FRONT_SCENE_END_MM - v->distance_mm;
        v->distance_mm += step;
        if (v->distance_mm == FRONT_SCENE_END_MM) {
            v->phase = "COMPLETE";
            v->active = s->armed = false;
            v->speed_mm_s = 0;
        }
    }
    s->speed = (v->speed_mm_s * 36 + 5000) / 10000;
    if (v->crossed && v->speed_mm_s) s->led = s->direction;
}

bool front_p1_bind(struct front_p1 *state, const unsigned roi[4], uint64_t now)
{
    unsigned copy[4];
    if (!now || roi[0] >= roi[2] || roi[1] >= roi[3] || roi[2] > 10000 || roi[3] > 10000 ||
        roi[2] - roi[0] < 10 || roi[3] - roi[1] < 10) return false;
    memcpy(copy, roi, sizeof(copy));
    memcpy(state->roi, copy, sizeof(copy));
    state->bound = true;
    state->bound_at = state->target_seen_at = now;
    state->observed_at = state->received_at = state->force_until = 0;
    state->armed = state->alarm = state->has_selected = state->conflict = false;
    state->speed = state->led = state->matches = 0;
    clear_signal(state);
    state->reason = "WAIT_SIGNAL";
    return true;
}

bool front_p1_pick(struct front_p1 *state, unsigned x, unsigned y, uint64_t now)
{
    unsigned index, matches = 0, selected = 0;
    if (x > 10000 || y > 10000 || !recent(state->observed_at, now, FRONT_P1_SIGNAL_LIVE_MS)) return false;
    for (index = 0; index < state->candidate_count; index++) {
        const unsigned *box = state->candidates[index];
        /* Allow a small display/detector lag when clicking a moving light. */
        if (x + 150 >= box[0] && x <= box[2] + 150 && y + 150 >= box[1] && y <= box[3] + 150) {
            matches++; selected = index;
        }
    }
    if (matches != 1) return false;
    return front_p1_bind(state, state->candidates[selected], now);
}

bool front_p1_action(struct front_p1 *state, const char *action, bool running, uint64_t now)
{
    unsigned direction = !strcmp(action, "turn_left") ? 1 : !strcmp(action, "straight") ? 2 :
                         !strcmp(action, "turn_right") ? 3 : 0;
    if (!strncmp(action, "scene_", 6)) {
        if (!scene_action(state, action, running, now)) return false;
        front_p1_update(state, running, now);
        return true;
    }
    if (direction) {
        if (state->scene.enabled && (state->scene.active || state->scene.crossed)) return false;
        if (state->direction && state->direction != direction) {
            state->bound = state->has_selected = false;
            state->bound_at = 0;
            clear_signal(state);
        }
        state->direction = direction;
        state->armed = state->alarm = false;
        state->force_until = 0;
        if (state->scene.enabled) scene_reset(state, now);
    } else if (!strcmp(action, "unbind")) {
        state->bound = state->has_selected = state->armed = state->alarm = false;
        state->bound_at = state->force_until = 0;
        clear_signal(state);
    } else if (!strcmp(action, "brake")) {
        if (state->scene.enabled) return front_p1_action(state, "scene_pause", running, now);
        state->armed = state->alarm = false;
        state->force_until = 0;
    } else if (!strcmp(action, "go")) {
        if (state->scene.enabled) return front_p1_action(state, "scene_start", running, now);
        if (!running || !state->direction) return false;
        if (signal_controlled(state) && !state->bound) {
            /* This button explicitly confirms the visible single target.
             * Multiple lamps require a click; colour never assigns direction. */
            if (state->matches != 1 || !state->has_selected ||
                !recent(state->observed_at, now, FRONT_P1_SIGNAL_LIVE_MS) ||
                !front_p1_bind(state, state->selected, now)) return false;
        }
        state->armed = true;
        state->force_until = 0;
        state->alarm = false;
    } else if (!strcmp(action, "force_go")) {
        if (state->scene.enabled) return front_p1_action(state, "scene_force", running, now);
        if (!running || !state->direction || !signal_controlled(state)) return false;
        state->armed = false;
        state->force_until = now + 2000;
        state->event_id++;
    } else return false;
    front_p1_update(state, running, now);
    return true;
}

static bool overlaps(const unsigned first[4], const unsigned second[4])
{
    unsigned left = first[0] > second[0] ? first[0] : second[0];
    unsigned top = first[1] > second[1] ? first[1] : second[1];
    unsigned right = first[2] < second[2] ? first[2] : second[2];
    unsigned bottom = first[3] < second[3] ? first[3] : second[3];
    uint64_t area_a = (uint64_t)(first[2]-first[0])*(first[3]-first[1]);
    uint64_t area_b = (uint64_t)(second[2]-second[0])*(second[3]-second[1]);
    uint64_t area = area_a < area_b ? area_a : area_b;
    return left < right && top < bottom && (uint64_t)(right-left)*(bottom-top)*2 >= area;
}

static bool follows(const unsigned previous[4], const unsigned next[4])
{
    unsigned expanded[4];
    unsigned dx = (previous[2]-previous[0])/2 + 120;
    unsigned dy = (previous[3]-previous[1])/2 + 120;
    unsigned width = next[2]-next[0], height = next[3]-next[1];
    unsigned cx_a = previous[0]+previous[2], cx_b = next[0]+next[2];
    unsigned cy_a = previous[1]+previous[3], cy_b = next[1]+next[3];
    if ((cx_a > cx_b ? cx_a-cx_b : cx_b-cx_a) > 2*dx ||
        (cy_a > cy_b ? cy_a-cy_b : cy_b-cy_a) > 2*dy) return false;
    if (width*3 < previous[2]-previous[0] || width > 3*(previous[2]-previous[0]) ||
        height*3 < previous[3]-previous[1] || height > 3*(previous[3]-previous[1])) return false;
    expanded[0] = previous[0] > dx ? previous[0]-dx : 0;
    expanded[1] = previous[1] > dy ? previous[1]-dy : 0;
    expanded[2] = previous[2]+dx < 10000 ? previous[2]+dx : 10000;
    expanded[3] = previous[3]+dy < 10000 ? previous[3]+dy : 10000;
    return overlaps(expanded, next);
}

bool front_p1_accept(struct front_p1 *state, const char *report, uint64_t now)
{
    unsigned count, index, matches = 0, selected = 0, colors = 0, color = 0;
    unsigned candidates[FRONT_P1_MAX_LIGHTS][5] = {{0}};
    unsigned long long sent, stamp;
    int consumed = 0, length = 0;
    const char *cursor;
    if (sscanf(report, "LIGHTS %llu %llu %u %n", &sent, &stamp, &count, &consumed) != 3 ||
        !consumed || count > FRONT_P1_MAX_LIGHTS || !recent(sent, now, 750) ||
        stamp > sent || !recent(stamp, now, 750) || stamp <= state->observed_at ||
        (state->bound && stamp <= state->bound_at)) return false;
    cursor = report + consumed;
    for (index = 0; index < count; index++) {
        unsigned *box = candidates[index];
        length = 0;
        if (sscanf(cursor, "%u %u %u %u %u %n", &box[0], &box[1], &box[2], &box[3], &box[4], &length) != 5 ||
            !length || box[0] >= box[2] || box[1] >= box[3] || box[2] > 10000 || box[3] > 10000 || box[4] > 3)
            return false;
        cursor += length;
    }
    if (*cursor) return false;
    if (state->observed_at && !recent(state->observed_at, stamp, FRONT_P1_SIGNAL_HOLD_MS)) clear_signal(state);
    state->candidate_count = count;
    memcpy(state->candidates, candidates, sizeof(candidates));
    state->observed_at = stamp;
    state->received_at = sent;
    state->current_evidence = state->conflict = false;
    if (state->bound && !recent(state->target_seen_at, now, FRONT_P1_SIGNAL_HOLD_MS)) {
        state->bound = state->has_selected = false;
        if (signal_controlled(state)) state->armed = false;
        state->bound_at = 0;
        clear_signal(state);
    }
    for (index = 0; index < count; index++) {
        const unsigned *box = candidates[index];
        unsigned cx = (box[0]+box[2])/2, cy = (box[1]+box[3])/2;
        bool matched = state->bound ? (state->has_selected ? follows(state->selected, box) : overlaps(state->roi, box)) :
                                       (cx >= 1500 && cx <= 8500 && cy >= 400 && cy <= 6500);
        if (!matched) continue;
        matches++; selected = index;
        if (box[4]) colors |= 1U << box[4];
    }
    state->matches = matches;
    if (matches == 1) {
        memcpy(state->selected, candidates[selected], sizeof(state->selected));
        state->has_selected = true;
        state->target_seen_at = stamp;
        if (state->bound) memcpy(state->roi, state->selected, sizeof(state->roi));
    } else if (!state->bound) state->has_selected = false;
    for (index = 1; index <= 3; index++) if (colors == (1U << index)) color = index;
    state->conflict = colors && !color;
    if (state->conflict || (state->bound && matches > 1)) {
        clear_signal(state);
        return true;
    }
    if (!color) {
        state->held = state->signal != 0;
        return true;
    }
    if (color == 3) {
        if (state->pending != 3) {
            state->confirmations = 0;
            state->first_green_at = stamp;
        }
        if (state->confirmations < 3) state->confirmations++;
        if (state->confirmations >= 3 && stamp-state->first_green_at >= 200) state->signal = 3;
    } else {
        state->confirmations = 1;
        state->first_green_at = 0;
        state->signal = color;
    }
    state->pending = color;
    state->current_evidence = state->signal == color;
    state->held = state->signal && !state->current_evidence;
    if (state->current_evidence) state->evidence_at = stamp;
    return true;
}

void front_p1_update(struct front_p1 *state, bool running, uint64_t now)
{
    bool ready;
    state->speed = state->led = 0;
    if (state->bound && !recent(state->target_seen_at, now, FRONT_P1_SIGNAL_HOLD_MS)) {
        state->bound = state->has_selected = false;
        if (signal_controlled(state)) state->armed = false;
        state->bound_at = state->force_until = 0;
    }
    if ((state->evidence_at && !recent(state->evidence_at, now, FRONT_P1_SIGNAL_HOLD_MS)) ||
        (state->observed_at && !recent(state->observed_at, now, FRONT_P1_SIGNAL_HOLD_MS))) clear_signal(state);
    ready = state->current_evidence && recent(state->evidence_at, now, FRONT_P1_SIGNAL_LIVE_MS) &&
            recent(state->received_at, now, 750);
    state->held = state->signal && !ready;
    state->people_valid = running && state->people_valid && recent(state->people_at, now, FRONT_P1_PEOPLE_LIVE_MS);
    if (state->scene.enabled) {
        scene_update(state, running, ready && state->signal == 3, now);
        if (state->direction == 3) {
            if (!running) state->reason = "NOT_RUNNING";
            else if (!state->people_valid) state->reason = "WAIT_PERCEPTION";
            else if (state->people) state->reason = "WAIT_PEDESTRIAN";
            else state->reason = state->speed ? "RIGHT_GO" : "RIGHT_READY";
        }
        else state->reason = state->conflict ? "COLOR_CONFLICT" : "SCENE_ACTIVE";
        return;
    }
    if (!running) {
        state->armed = false;
        state->force_until = 0;
        state->reason = "NOT_RUNNING";
        return;
    }
    if (state->direction == 3) {
        state->alarm = false;
        state->force_until = 0;
        if (!state->people_valid) state->reason = "WAIT_PERCEPTION";
        else if (state->people) state->reason = "WAIT_PEDESTRIAN";
        else {
            state->reason = state->armed ? "RIGHT_GO" : "RIGHT_READY";
            if (state->armed) { state->speed = 7; state->led = 3; }
        }
        return;
    }
    if (state->conflict) state->reason = "COLOR_CONFLICT";
    else if (!state->direction) state->reason = "SELECT_DIRECTION";
    else if (state->signal == 1) state->reason = "RED_STOP";
    else if (state->signal == 2) state->reason = "YELLOW_WAIT";
    else if (!state->signal) state->reason = state->bound && state->matches > 1 ? "MULTIPLE_LIGHTS" : "WAIT_SIGNAL";
    else if (!state->bound) state->reason = state->matches == 1 ? "AUTO_GREEN_READY" : "CHOOSE_LIGHT";
    else if (!ready) state->reason = "SIGNAL_HOLD";
    else if (!state->armed) state->reason = "GREEN_READY";
    else {
        state->reason = "GREEN_GO";
        state->speed = 20;
        state->led = state->direction;
    }
    if (state->force_until && now < state->force_until && state->force_until-now <= 2000) {
        state->speed = 10;
        state->reason = "FORCED_MOVE";
        if (!state->bound || !ready || state->signal != 3) state->alarm = true;
        if (state->bound && ready && state->signal == 3) state->led = state->direction;
    } else state->force_until = 0;
}

void front_p1_json(const struct front_p1 *state, uint64_t now, char *out, size_t capacity)
{
    long long age = state->evidence_at && state->evidence_at <= now ? (long long)(now-state->evidence_at) : -1;
    long long people_age = state->people_at && state->people_at <= now ? (long long)(now-state->people_at) : -1;
    bool people_valid = state->people_valid && recent(state->people_at, now, FRONT_P1_PEOPLE_LIVE_MS);
    snprintf(out, capacity,
        "{\"version\":2,\"direction\":%u,\"people\":%u,\"people_valid\":%s,\"people_age_ms\":%lld,"
        "\"people_basis\":\"%s\",\"people_state\":\"%s\",\"bound\":%s,\"roi\":[%u,%u,%u,%u],"
        "\"signal\":%u,\"signal_age_ms\":%lld,\"signal_held\":%s,\"signal_live_ms\":%u,\"signal_hold_ms\":%u,"
        "\"matches\":%u,\"confirmations\":%u,\"armed\":%s,\"reason\":\"%s\","
        "\"speed_kmh\":%u,\"led\":%u,\"alarm\":%s,\"event_id\":%u,"
        "\"scene\":{\"version\":1,\"enabled\":%s,\"active\":%s,\"crossed\":%s,\"violation\":%s,"
        "\"run_id\":%u,\"distance_mm\":%u,\"stop_mm\":%u,\"end_mm\":%u,\"speed_mm_s\":%u,\"phase\":\"%s\"}}",
        state->direction, state->people, people_valid ? "true" : "false", people_age,
        state->people_spatial ? "crosswalk_contact" : "frame_count", state->people_state ? state->people_state : "LEGACY",
        state->bound ? "true" : "false", state->roi[0], state->roi[1], state->roi[2], state->roi[3],
        state->signal, age, state->held ? "true" : "false", FRONT_P1_SIGNAL_LIVE_MS, FRONT_P1_SIGNAL_HOLD_MS,
        state->matches, state->confirmations, state->armed ? "true" : "false",
        state->reason, state->speed, state->led, state->alarm ? "true" : "false", state->event_id,
        state->scene.enabled ? "true" : "false", state->scene.active ? "true" : "false",
        state->scene.crossed ? "true" : "false", state->scene.violation ? "true" : "false",
        state->scene.run_id, state->scene.distance_mm, FRONT_SCENE_STOP_MM, FRONT_SCENE_END_MM,
        state->scene.speed_mm_s, state->scene.phase ? state->scene.phase : "READY");
}
