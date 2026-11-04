#ifndef FRONT_WORLD_CAPTURE_H
#define FRONT_WORLD_CAPTURE_H
#include <math.h>
#include "front_world.h"
#include "lane_style_core.h"
#include "road_object_core.h"

static inline unsigned fw_norm(float value,unsigned extent)
{ return (unsigned)(fminf(1.f,fmaxf(0.f,value/extent))*10000.f+.5f); }
static inline void fw_capture(struct front_world *v,const struct lane_result *lanes,const struct road_object_result *objects)
{
    unsigned i,j,pass; bool used[LANE_MAX_BOUNDARIES]={0};
    if(lanes) {
        v->flags|=1; v->lane_seq=lanes->sequence; v->lane_ms=lanes->timestamp_ns/1000000;
        /* Keep ego boundaries first, then highest-quality candidates. */
        for(pass=0;pass<FW_LANES;pass++) {
            int best=-1; float rank=-1;
            for(i=0;i<lanes->boundary_count && i<LANE_MAX_BOUNDARIES;i++) {
                const struct lane_boundary *b=&lanes->boundaries[i]; bool valid=true;
                float q=b->quality;
                if(used[i] || !isfinite(q) || q<0 || q>1) continue;
                for(j=0;j<FW_POINTS;j++) {
                    const struct lane_point *p=&b->curve[j*(LANE_CURVE_POINTS-1)/(FW_POINTS-1)];
                    if(!isfinite(p->x)||!isfinite(p->y)||p->x<0||p->x>v->width||p->y<0||p->y>v->height) valid=false;
                }
                if(!valid) continue;
                if((int)i==lanes->sides[0].boundary_index || (int)i==lanes->sides[1].boundary_index) q+=2;
                if(q>rank) { rank=q; best=(int)i; }
            }
            if(best<0) break;
            {
                const struct lane_boundary *b=&lanes->boundaries[best];
                struct fw_lane *l=&v->lanes[v->lane_count++]; used[best]=true;
                l->style=b->style_conflict?0:(unsigned)b->observed;
                l->quality=(unsigned)(b->quality*10000.f);
                l->role=best==lanes->sides[0].boundary_index?1:best==lanes->sides[1].boundary_index?2:0;
                for(j=0;j<FW_POINTS;j++) {
                    const struct lane_point *p=&b->curve[j*(LANE_CURVE_POINTS-1)/(FW_POINTS-1)];
                    l->xy[j][0]=fw_norm(p->x,v->width); l->xy[j][1]=fw_norm(p->y,v->height);
                }
            }
        }
    }
    if(objects) {
        v->flags|=2; v->object_seq=objects->sequence; v->object_ms=objects->timestamp_ns/1000000;
        /* YOLO decoder already returns score-sorted NMS boxes. */
        for(i=0;i<objects->count && i<ROAD_OBJECT_CAPACITY && v->object_count<FW_OBJECTS;i++) {
            const struct road_box *b=&objects->boxes[i]; struct fw_object *o=&v->objects[v->object_count];
            if(!fw_class(b->class_id) || !isfinite(b->score)||b->score<0||b->score>1 ||
               !isfinite(b->left)||!isfinite(b->top)||!isfinite(b->right)||!isfinite(b->bottom)) continue;
            o->cls=b->class_id; o->score=(unsigned)(b->score*10000.f);
            o->box[0]=fw_norm(b->left,v->width); o->box[1]=fw_norm(b->top,v->height);
            o->box[2]=fw_norm(b->right,v->width); o->box[3]=fw_norm(b->bottom,v->height);
            if(o->box[0]<o->box[2] && o->box[1]<o->box[3]) v->object_count++;
        }
    }
}
#endif
