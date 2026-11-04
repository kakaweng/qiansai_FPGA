#include "pie_anticipation_core.h"
#include "pie_anticipation_weights.h"
#include <math.h>

static float pa_sigmoid(float value)
{
    if(value>=0) return 1.0f/(1.0f+expf(-value));
    value=expf(value); return value/(1.0f+value);
}

bool pa_predict(const float boxes[PA_HISTORY][4], float scores[2])
{
    float states[PA_HISTORY][4], normalized[PA_HISTORY][12], features[36], logits[2]={0};
    unsigned sample, component, head;
    if(!boxes || !scores) return false;
    for(sample=0;sample<PA_HISTORY;sample++) {
        for(component=0;component<4;component++)
            if(!isfinite(boxes[sample][component]) || boxes[sample][component]<0 || boxes[sample][component]>1) return false;
        if(boxes[sample][0]>=boxes[sample][2] || boxes[sample][1]>=boxes[sample][3]) return false;
        states[sample][0]=(boxes[sample][0]+boxes[sample][2])*.5f;
        states[sample][1]=(boxes[sample][1]+boxes[sample][3])*.5f;
        states[sample][2]=boxes[sample][2]-boxes[sample][0];
        states[sample][3]=boxes[sample][3]-boxes[sample][1];
    }
    for(sample=0;sample<PA_HISTORY;sample++) {
        for(component=0;component<12;component++) {
            float value;
            unsigned index=component%4;
            if(component<4) value=states[sample][index];
            else if(component<8) value=sample?(states[sample][index]-states[sample-1][index])*5.0f:0.0f;
            else value=states[sample][index]-states[PA_HISTORY-1][index];
            if(!isfinite(pie_geometry_scale[component]) || pie_geometry_scale[component]<=0) return false;
            value=(value-pie_geometry_mean[component])/pie_geometry_scale[component];
            normalized[sample][component]=fmaxf(-5.0f,fminf(5.0f,value));
        }
    }
    for(component=0;component<12;component++) {
        float total=0;
        for(sample=0;sample<PA_HISTORY;sample++) total+=normalized[sample][component];
        features[component]=normalized[PA_HISTORY-1][component];
        features[12+component]=total/PA_HISTORY;
        features[24+component]=normalized[PA_HISTORY-1][component]-normalized[0][component];
    }
    for(head=0;head<2;head++) {
        logits[head]=pie_head_bias[head];
        for(component=0;component<36;component++) logits[head]+=features[component]*pie_head_weight[head][component];
        if(!isfinite(logits[head])) return false;
    }
    scores[0]=pa_sigmoid(logits[0]);
    scores[1]=scores[0]+(1.0f-scores[0])*pa_sigmoid(logits[1]);
    return isfinite(scores[0]) && isfinite(scores[1]) && scores[0]>=0 && scores[1]>=scores[0] && scores[1]<=1;
}

void pa_reset(struct pie_anticipation *state)
{
    memset(state,0,sizeof(*state)); state->next_id=1; state->reason="WAITING_HISTORY";
}

static void pa_clear(struct pie_anticipation *state, uint64_t now, const char *reason)
{
    pa_reset(state); state->last_now=now; state->reason=reason;
}

static float pa_iou(const unsigned first[4],const unsigned second[4])
{
    unsigned left=first[0]>second[0]?first[0]:second[0];
    unsigned top=first[1]>second[1]?first[1]:second[1];
    unsigned right=first[2]<second[2]?first[2]:second[2];
    unsigned bottom=first[3]<second[3]?first[3]:second[3];
    float overlap, area;
    if(left>=right || top>=bottom) return 0;
    overlap=(float)(right-left)*(bottom-top);
    area=(float)(first[2]-first[0])*(first[3]-first[1])+(float)(second[2]-second[0])*(second[3]-second[1]);
    return overlap/(area-overlap);
}

static bool pa_box(const unsigned box[4])
{
    return box[0]<box[2] && box[1]<box[3] && box[2]<=10000 && box[3]<=10000;
}

static void pa_sample(struct pa_person *person, uint64_t at)
{
    unsigned component;
    uint64_t elapsed=person->samples && at>=person->sampled_ms?at-person->sampled_ms:0;
    if(person->samples && elapsed<160) { person->status=PA_CADENCE; return; }
    if(person->samples && elapsed>240) { person->samples=0; person->status=PA_CADENCE; }
    if(person->samples==PA_HISTORY) {
        memmove(person->history,person->history+1,(PA_HISTORY-1)*sizeof(person->history[0]));
        person->samples--;
    }
    for(component=0;component<4;component++) person->history[person->samples][component]=person->box[component]/10000.0f;
    person->samples++; person->sampled_ms=at;
    if(person->samples==PA_HISTORY) {
        person->status=pa_predict(person->history,person->scores)?PA_SCORED:PA_WAITING_HISTORY;
    } else if(person->status!=PA_CADENCE && person->status!=PA_AMBIGUOUS) person->status=PA_WAITING_HISTORY;
}

void pa_update(struct pie_anticipation *state, const struct front_world *world, uint64_t now, bool running)
{
    struct pa_person previous[PA_PERSONS];
    const struct fw_object *detections[PA_PERSONS];
    unsigned previous_degree[PA_PERSONS]={0}, detection_degree[PA_PERSONS]={0}, choice[PA_PERSONS]={0};
    unsigned previous_count, count=0, detection, track, component;
    bool reset_source;
    if(!state->next_id) pa_reset(state);
    if(!running) { pa_clear(state,now,"STOPPED"); return; }
    if(state->last_now && now<state->last_now) { pa_clear(state,now,"TIME_RESET"); return; }
    state->last_now=now;
    if(!world || !(world->flags&2) || !world->producer || world->width!=1920 || world->height!=1080 ||
       world->object_count>FW_OBJECTS || !world->object_ms || world->object_ms>now || now-world->object_ms>PA_MAX_AGE_MS ||
       world->object_ms>world->frame_ms || world->object_seq>world->frame_seq) {
        pa_clear(state,now,"STALE"); return;
    }
    reset_source=state->has_source && (state->producer!=world->producer || world->object_seq<state->object_seq || world->object_ms<state->object_ms);
    if(reset_source) pa_clear(state,now,"TIME_RESET");
    if(state->has_source && world->object_seq==state->object_seq) {
        if(world->object_ms!=state->object_ms) pa_clear(state,now,"INVALID_SEQUENCE");
        return;
    }
    if(state->has_source && world->object_ms<=state->object_ms) { pa_clear(state,now,"INVALID_SEQUENCE"); return; }
    for(detection=0;detection<world->object_count;detection++) {
        const struct fw_object *object=&world->objects[detection];
        if(object->cls!=0 || object->score<2500) continue;
        if(!pa_box(object->box) || object->score>10000) { pa_clear(state,now,"INVALID_INPUT"); return; }
        if(count<PA_PERSONS) detections[count++]=object;
    }
    previous_count=state->count; memcpy(previous,state->persons,sizeof(previous));
    state->producer=world->producer; state->object_seq=world->object_seq; state->object_ms=world->object_ms;
    state->has_source=true; state->count=count;
    for(detection=0;detection<count;detection++) {
        for(track=0;track<previous_count;track++) {
            if(pa_iou(previous[track].box,detections[detection]->box)>=.15f) {
                previous_degree[track]++; detection_degree[detection]++; choice[detection]=track;
            }
        }
    }
    state->reason=count?"WAITING_HISTORY":"NO_PERSON";
    for(detection=0;detection<count;detection++) {
        struct pa_person *person=&state->persons[detection];
        bool unique=detection_degree[detection]==1 && previous_degree[choice[detection]]==1;
        if(unique) *person=previous[choice[detection]];
        else {
            memset(person,0,sizeof(*person)); person->id=state->next_id++;
            if(!state->next_id) state->next_id=1;
            person->status=detection_degree[detection]?PA_AMBIGUOUS:PA_WAITING_HISTORY;
        }
        for(component=0;component<4;component++) person->box[component]=detections[detection]->box[component];
        if(unique) person->status=PA_WAITING_HISTORY;
        pa_sample(person,world->object_ms);
        if(person->status==PA_SCORED) state->reason="SCORED";
    }
}

bool pa_json(const struct pie_anticipation *state, uint64_t now, bool running, char *out, size_t capacity)
{
    static const char *statuses[]={"WAITING_HISTORY","CADENCE","AMBIGUOUS","SCORED"};
    size_t used=0; unsigned index;
    bool live=running && state->has_source && state->object_ms<=now && now-state->object_ms<=PA_MAX_AGE_MS;
    const char *reason=!running?"STOPPED":live?(state->reason?state->reason:"WAITING_HISTORY"):"STALE";
#define PA_WRITE(...) do { if(!fw_append(out,capacity,&used,__VA_ARGS__)) return false; } while(0)
    PA_WRITE("{\"model_id\":\"%s\",\"device\":\"RK3568_CPU\",\"no_control\":true,\"eligibility\":\"unverified\",\"age_ms\":%lld,\"reason\":\"%s\",\"persons\":[",
        PIE_MODEL_ID,live?(long long)(now-state->object_ms):-1LL,reason);
    for(index=0;live && index<state->count;index++) {
        const struct pa_person *person=&state->persons[index];
        if(person->status>PA_SCORED || person->samples>PA_HISTORY) return false;
        PA_WRITE("%s{\"id\":%u,\"box\":[%u,%u,%u,%u],\"samples\":%u,\"status\":\"%s\"",
            index?",":"",person->id,person->box[0],person->box[1],person->box[2],person->box[3],person->samples,statuses[person->status]);
        if(person->status==PA_SCORED) PA_WRITE(",\"score06\":%.9g,\"score1\":%.9g",person->scores[0],person->scores[1]);
        PA_WRITE("}");
    }
    PA_WRITE("]}");
#undef PA_WRITE
    return true;
}
