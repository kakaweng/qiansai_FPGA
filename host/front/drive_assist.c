#include "drive_assist.h"
#include <stdio.h>
#include <string.h>
static bool fresh(uint64_t stamp,uint64_t now)
{ return stamp && stamp<=now && now-stamp<=750; }
#ifdef HAVE_FRONT_P1
static void stabilize_front_lane(struct drive_assist *d, uint64_t now)
{
    unsigned side;
    struct drive_evidence *e = &d->evidence;
    if (!d->running || !fresh(e->received, now)) {
        memset(d->stable_line, 0, sizeof(d->stable_line));
        memset(d->stable_lane_stamp, 0, sizeof(d->stable_lane_stamp));
        memset(d->lane_held, 0, sizeof(d->lane_held));
        d->lane_seen_stamp = 0;
        return;
    }
    for (side = 0; side < 2; side++) {
        if (fresh(e->lane_stamp, now) && e->lane_stamp > d->lane_seen_stamp && e->line[side]) {
            d->stable_line[side] = e->line[side];
            d->stable_lane_stamp[side] = e->lane_stamp;
        }
        if (!d->stable_lane_stamp[side] || d->stable_lane_stamp[side] > now ||
            now-d->stable_lane_stamp[side] > FRONT_P1_LANE_HOLD_MS) d->stable_line[side] = 0;
        d->lane_held[side] = d->stable_line[side] &&
            (!fresh(e->lane_stamp, now) || e->line[side] != d->stable_line[side]);
    }
    if (e->lane_stamp > d->lane_seen_stamp) d->lane_seen_stamp = e->lane_stamp;
}
#endif
void drive_reset(struct drive_assist *d)
{
    memset(d,0,sizeof(*d)); d->gear='P'; d->feedback="IDLE";
#ifdef HAVE_FRONT_P1
    front_p1_reset(&d->p1);
#endif
}
bool drive_action(struct drive_assist *d,const char *action,uint64_t now)
{
#ifdef HAVE_FRONT_P1
    if (front_p1_action(&d->p1, action, d->running && fresh(d->evidence.received, now), now)) {
        drive_update(d, d->running, now);
        return true;
    }
    if (strcmp(action, "left") && strcmp(action, "right") && strcmp(action, "cancel")) return false;
#endif
    if(!strcmp(action,"cancel")) { d->request=0; d->solid_alarm=false; d->feedback="CANCELLED"; return true; }
    if(!strcmp(action,"left") || !strcmp(action,"right")) {
        d->request=!strcmp(action,"left") ? 1 : 2; d->request_id++; d->request_at=now;
        drive_update(d,d->running,now); return true;
    }
    if(!strcmp(action,"brake")) { d->speed=0; return true; }
    if(!strcmp(action,"P") || !strcmp(action,"N") || !strcmp(action,"D") || !strcmp(action,"R")) {
        if(d->speed) return false;
        d->gear=action[0]; return true;
    }
    if(!strcmp(action,"slower")) { d->speed=d->speed>=5 ? d->speed-5 : 0; return true; }
    if(!strcmp(action,"faster")) {
        if(!d->running || (d->gear!='D' && d->gear!='R')) return false;
        if(d->speed<(d->gear=='R' ? 10U : 120U)) d->speed+=5;
        return true;
    }
    return false;
}
void drive_update(struct drive_assist *d,bool running,uint64_t now)
{
    struct drive_evidence *e=&d->evidence;
    unsigned side;
    d->running=running; d->solid_alarm=false;
#ifdef HAVE_FRONT_P1
    stabilize_front_lane(d, now);
#ifndef HAVE_CROSSWALK
    front_p1_people(&d->p1, running && fresh(e->received, now) && fresh(e->object_stamp[0], now),
                    e->people, e->object_stamp[0], now);
#endif
    front_p1_update(&d->p1, running && fresh(e->received, now), now);
    d->speed = d->p1.speed;
    d->gear = d->speed ? 'D' : 'P';
#endif
    if(!running) d->speed=0;
    if(!d->request) return;
    if(now<d->request_at || now-d->request_at>10000) {
        d->request=0; d->feedback="EXPIRED"; return;
    }
    if(!running) { d->feedback="NOT_RUNNING"; return; }
    side=d->request-1;
#ifdef HAVE_FRONT_P1
    if (!fresh(e->received,now)) { d->feedback="LANE_STALE"; return; }
    if (d->stable_line[side] == 1) {
        d->feedback = d->lane_held[side] ? "SOLID_HOLD" : "SOLID_BLOCK";
        d->solid_alarm = true; return;
    }
    if (d->stable_line[side] == 2) {
        d->feedback = d->lane_held[side] ? "DASHED_HOLD" : "DASHED_REVIEW";
        return;
    }
    d->feedback = "LINE_UNKNOWN"; return;
#endif
    if(!fresh(e->received,now) || !fresh(e->lane_stamp,now)) { d->feedback="LANE_STALE"; return; }
    if(e->line[side]==1) { d->feedback="SOLID_BLOCK"; d->solid_alarm=true; return; }
    if(e->line[side]!=2) { d->feedback="LINE_UNKNOWN"; return; }
#ifdef FRONT_ONLY
    d->feedback=e->vehicles[0] ? "FRONT_VEHICLE_REVIEW" : "DASHED_REVIEW";
    return;
#else
    if(!fresh(e->object_stamp[d->request],now)) { d->feedback="SIDE_STALE"; return; }
    /* Camera has no source timecode; a live DMA result cannot establish sync. */
    if(d->request==1) { d->feedback=e->vehicles[1] ? "LEFT_VEHICLE_MANUAL" : "LEFT_MANUAL"; return; }
    if((e->front_scene>e->right_scene ? e->front_scene-e->right_scene : e->right_scene-e->front_scene)>250) {
        d->feedback="SIDE_TIME_MISMATCH"; return;
    }
    d->feedback=e->vehicles[2] ? "SIDE_VEHICLE_REVIEW" : "DASHED_REVIEW";
#endif
}
bool drive_accept(struct drive_assist *d,const char *report,uint32_t expected,uint64_t now)
{
    struct drive_evidence e={0};
    unsigned session;
    unsigned long long sent,lane,front,left,right,fs,rs;
    char tail;
    if(sscanf(report,"DRIVE %u %llu %u %u %llu %u %u %u %u %u %llu %llu %llu %llu %llu %c",
        &session,&sent,&e.line[0],&e.line[1],&lane,&e.signal,&e.vehicles[0],&e.vehicles[1],&e.vehicles[2],&e.people,
        &front,&left,&right,&fs,&rs,&tail)!=15 || session!=expected || !sent || sent>now || now-sent>750 ||
        (d->evidence.received && sent<=d->evidence.received) || e.line[0]>2 || e.line[1]>2 || e.signal>3 ||
        e.vehicles[0]>128 || e.vehicles[1]>128 || e.vehicles[2]>128 || e.people>128 ||
        lane>sent || front>sent || left>sent || right>sent) return false;
    e.received=sent; e.lane_stamp=lane; e.object_stamp[0]=front; e.object_stamp[1]=left; e.object_stamp[2]=right;
    e.front_scene=fs; e.right_scene=rs; d->evidence=e;
    drive_update(d,d->running,now); return true;
}
#ifdef FRONT_ONLY
bool drive_accept_front(struct drive_assist *d,const char *report,uint64_t now)
{
    struct drive_evidence e={0};
    unsigned long long sent,lane,object;
    char tail;
    if(sscanf(report,"FRONT %llu %u %u %llu %u %u %u %llu %c",
        &sent,&e.line[0],&e.line[1],&lane,&e.signal,&e.vehicles[0],&e.people,&object,&tail)!=8 ||
        !sent || sent>now || now-sent>750 ||
        (d->evidence.received && sent<=d->evidence.received) ||
        e.line[0]>2 || e.line[1]>2 || e.signal>3 || e.vehicles[0]>128 || e.people>128 ||
        lane>sent || object>sent) return false;
    e.received=sent; e.lane_stamp=lane; e.object_stamp[0]=object;
    d->evidence=e;
    drive_update(d,d->running,now);
    return true;
}
#endif
static long long age(uint64_t stamp,uint64_t now)
{ return stamp && stamp<=now ? (long long)(now-stamp) : -1; }
void drive_json(struct drive_assist *d,uint64_t now,char *out,size_t capacity)
{
    struct drive_evidence *e=&d->evidence;
    bool live=d->running && fresh(e->received,now);
#ifdef FRONT_ONLY
    const char *front_only="true";
#else
    const char *front_only="false";
#endif
    drive_update(d,d->running,now);
    snprintf(out,capacity,"{\"request\":%u,\"request_id\":%u,\"feedback\":\"%s\",\"solid_alarm\":%s,"
        "\"simulated\":true,\"speed_kmh\":%u,\"gear\":\"%c\",\"calibrated\":false,\"left_sync\":false,"
        "\"front_only\":%s,"
        "\"line\":[%u,%u],\"signal\":%u,\"vehicles\":[%u,%u,%u],\"people\":%u,"
        "\"lane_age_ms\":%lld,\"object_age_ms\":[%lld,%lld,%lld],\"object_valid\":[%s,%s,%s],\"scene_delta_ms\":%llu}",
        d->request,d->request_id,d->feedback,d->solid_alarm ? "true" : "false",d->speed,d->gear,front_only,
        live && fresh(e->lane_stamp,now) ? e->line[0] : 0,live && fresh(e->lane_stamp,now) ? e->line[1] : 0,
        live && fresh(e->object_stamp[0],now) ? e->signal : 0,e->vehicles[0],e->vehicles[1],e->vehicles[2],e->people,
        age(e->lane_stamp,now),age(e->object_stamp[0],now),age(e->object_stamp[1],now),age(e->object_stamp[2],now),
        live && fresh(e->object_stamp[0],now) ? "true" : "false",live && fresh(e->object_stamp[1],now) ? "true" : "false",
        live && fresh(e->object_stamp[2],now) ? "true" : "false",
        (unsigned long long)(e->front_scene>e->right_scene ? e->front_scene-e->right_scene : e->right_scene-e->front_scene));
#ifdef HAVE_FRONT_P1
    {
        char p1[1536];
        size_t length = strlen(out);
        front_p1_json(&d->p1, now, p1, sizeof(p1));
        if (length && out[length-1] == '}' && capacity > length)
            snprintf(out+length-1, capacity-length+1,
                ",\"stable_line\":[%u,%u],\"line_held\":[%s,%s],\"line_age_ms\":[%lld,%lld],\"line_hold_ms\":%u,\"p1\":%s}",
                d->stable_line[0], d->stable_line[1], d->lane_held[0] ? "true" : "false", d->lane_held[1] ? "true" : "false",
                age(d->stable_lane_stamp[0], now), age(d->stable_lane_stamp[1], now), FRONT_P1_LANE_HOLD_MS, p1);
    }
#endif
}
