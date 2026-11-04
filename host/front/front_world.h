#ifndef FRONT_WORLD_H
#define FRONT_WORLD_H

/* Display-only HDMI observations. Explicit big-endian wire format, no ABI/padding.
 * Neither this transport nor the desktop tracker grants driving permission. */
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>

#define FW_LANES 12U
#define FW_POINTS 12U
#define FW_OBJECTS 40U
#define FW_HEADER 72U
#define FW_BYTES (FW_HEADER + FW_LANES * 52U + FW_OBJECTS * 12U)
#define FW_JSON 10000U

struct fw_lane { unsigned style, quality, role; unsigned xy[FW_POINTS][2]; };
struct fw_object { unsigned cls, score, box[4]; };
struct front_world {
    uint32_t producer;
    uint64_t sent, frame_seq, frame_ms, lane_seq, lane_ms, object_seq, object_ms;
    unsigned width, height, lane_count, object_count, flags;
    struct fw_lane lanes[FW_LANES];
    struct fw_object objects[FW_OBJECTS];
};
static inline uint64_t fw_get(const unsigned char *p, unsigned n)
{ uint64_t v=0; while(n--) v=(v<<8)|*p++; return v; }
static inline void fw_put(unsigned char *p, uint64_t v, unsigned n)
{ while(n) { p[--n]=(unsigned char)v; v>>=8; } }
static inline bool fw_class(unsigned cls)
{ return cls==0 || cls==1 || cls==2 || cls==3 || cls==5 || cls==7; }

static inline size_t fw_pack(const struct front_world *v, unsigned char *p, size_t cap)
{
    unsigned i,j; size_t at=FW_HEADER;
    size_t size=at+v->lane_count*52U+v->object_count*12U;
    if(v->lane_count>FW_LANES || v->object_count>FW_OBJECTS || cap<size) return 0;
    memset(p,0,size); memcpy(p,"FWM1",4); fw_put(p+4,v->producer,4);
    fw_put(p+8,v->sent,8); fw_put(p+16,v->frame_seq,8); fw_put(p+24,v->frame_ms,8);
    fw_put(p+32,v->lane_seq,8); fw_put(p+40,v->lane_ms,8);
    fw_put(p+48,v->object_seq,8); fw_put(p+56,v->object_ms,8);
    fw_put(p+64,v->width,2); fw_put(p+66,v->height,2);
    p[68]=(unsigned char)v->lane_count; p[69]=(unsigned char)v->object_count; p[70]=(unsigned char)v->flags;
    for(i=0;i<v->lane_count;i++,at+=52) {
        const struct fw_lane *l=&v->lanes[i];
        p[at]=(unsigned char)l->style; p[at+1]=(unsigned char)l->role; fw_put(p+at+2,l->quality,2);
        for(j=0;j<FW_POINTS;j++) { fw_put(p+at+4+j*4,l->xy[j][0],2); fw_put(p+at+6+j*4,l->xy[j][1],2); }
    }
    for(i=0;i<v->object_count;i++,at+=12) {
        const struct fw_object *o=&v->objects[i]; p[at]=(unsigned char)o->cls; fw_put(p+at+2,o->score,2);
        for(j=0;j<4;j++) fw_put(p+at+4+j*2,o->box[j],2);
    }
    return size;
}

static inline bool fw_unpack_age(struct front_world *out,const unsigned char *p,size_t size,uint64_t now,uint64_t since,unsigned object_max_ms)
{
    struct front_world v={0}; unsigned i,j; size_t at=FW_HEADER;
    if(size<FW_HEADER || memcmp(p,"FWM1",4) || p[71]) return false;
    v.producer=(uint32_t)fw_get(p+4,4); v.sent=fw_get(p+8,8);
    v.frame_seq=fw_get(p+16,8); v.frame_ms=fw_get(p+24,8);
    v.lane_seq=fw_get(p+32,8); v.lane_ms=fw_get(p+40,8);
    v.object_seq=fw_get(p+48,8); v.object_ms=fw_get(p+56,8);
    v.width=(unsigned)fw_get(p+64,2); v.height=(unsigned)fw_get(p+66,2);
    v.lane_count=p[68]; v.object_count=p[69]; v.flags=p[70];
    if(!v.producer || v.width!=1920 || v.height!=1080 || v.flags>3 ||
       v.lane_count>FW_LANES || v.object_count>FW_OBJECTS ||
       size!=at+v.lane_count*52U+v.object_count*12U || !v.frame_ms ||
       v.sent>now || now-v.sent>750 || v.frame_ms>v.sent || now-v.frame_ms>1000 || v.frame_ms<since) return false;
    if(v.flags&1) {
        if(!v.lane_ms || v.lane_ms<since || v.lane_ms>v.frame_ms || now-v.lane_ms>1000 || v.lane_seq>v.frame_seq) return false;
    } else if(v.lane_count || v.lane_ms || v.lane_seq) return false;
    if(v.flags&2) {
        if(!v.object_ms || v.object_ms<since || v.object_ms>v.frame_ms || now-v.object_ms>object_max_ms || v.object_seq>v.frame_seq) return false;
    } else if(v.object_count || v.object_ms || v.object_seq) return false;
    for(i=0;i<v.lane_count;i++,at+=52) {
        struct fw_lane *l=&v.lanes[i];
        l->style=p[at]; l->role=p[at+1]; l->quality=(unsigned)fw_get(p+at+2,2);
        if(l->style>2 || l->role>2 || l->quality>10000) return false;
        for(j=0;j<FW_POINTS;j++) {
            l->xy[j][0]=(unsigned)fw_get(p+at+4+j*4,2); l->xy[j][1]=(unsigned)fw_get(p+at+6+j*4,2);
            if(l->xy[j][0]>10000 || l->xy[j][1]>10000) return false;
        }
    }
    for(i=0;i<v.object_count;i++,at+=12) {
        struct fw_object *o=&v.objects[i]; o->cls=p[at]; o->score=(unsigned)fw_get(p+at+2,2);
        if(!fw_class(o->cls) || p[at+1] || o->score>10000) return false;
        for(j=0;j<4;j++) { o->box[j]=(unsigned)fw_get(p+at+4+j*2,2); if(o->box[j]>10000) return false; }
        if(o->box[0]>=o->box[2] || o->box[1]>=o->box[3]) return false;
    }
    /* Reject reordered datagrams; producer changes are explicit discontinuities. */
    if(out->producer==v.producer && (v.sent<=out->sent || v.frame_seq<out->frame_seq || v.frame_ms<out->frame_ms ||
       ((v.flags&1) && out->lane_ms && (v.lane_ms<out->lane_ms || v.lane_seq<out->lane_seq)) ||
       ((v.flags&2) && out->object_ms && (v.object_ms<out->object_ms || v.object_seq<out->object_seq)))) return false;
    *out=v; return true;
}

static inline bool fw_unpack(struct front_world *out,const unsigned char *p,size_t size,uint64_t now,uint64_t since)
{ return fw_unpack_age(out,p,size,now,since,1000); }
static inline bool fw_append(char *out,size_t cap,size_t *used,const char *format,...)
{
    int n; va_list args; if(*used>=cap) return false;
    va_start(args,format); n=vsnprintf(out+*used,cap-*used,format,args); va_end(args);
    if(n<0 || (size_t)n>=cap-*used) return false;
    *used+=(size_t)n; return true;
}
static inline bool fw_json_age(const struct front_world *v,uint64_t now,bool running,char *out,size_t cap,unsigned object_max_ms)
{
    size_t used=0; unsigned i,j; bool live=running && v->sent && now>=v->sent && now-v->sent<=750;
    bool lane=live && (v->flags&1) && now>=v->lane_ms && now-v->lane_ms<=1000;
    bool objects=live && (v->flags&2) && now>=v->object_ms && now-v->object_ms<=object_max_ms;
#define FW_WRITE(...) do { if(!fw_append(out,cap,&used,__VA_ARGS__)) return false; } while(0)
    FW_WRITE("{\"version\":1,\"producer\":%u,\"live\":%s,\"frame_seq\":%llu,\"frame_ms\":%llu,\"width\":%u,\"height\":%u,\"report_age_ms\":%lld,",
        v->producer,live?"true":"false",(unsigned long long)v->frame_seq,(unsigned long long)v->frame_ms,v->width,v->height,
        live?(long long)(now-v->sent):-1LL);
    FW_WRITE("\"lane_seq\":%llu,\"lane_ms\":%llu,\"lane_age_ms\":%lld,\"lane_valid\":%s,\"object_seq\":%llu,\"object_ms\":%llu,\"object_age_ms\":%lld,\"object_valid\":%s,\"lanes\":[",
        (unsigned long long)v->lane_seq,(unsigned long long)v->lane_ms,lane?(long long)(now-v->lane_ms):-1LL,lane?"true":"false",
        (unsigned long long)v->object_seq,(unsigned long long)v->object_ms,objects?(long long)(now-v->object_ms):-1LL,objects?"true":"false");
    for(i=0;lane && i<v->lane_count;i++) {
        const struct fw_lane *l=&v->lanes[i]; FW_WRITE("%s[%u,%u,%u,[",i?",":"",l->style,l->quality,l->role);
        for(j=0;j<FW_POINTS;j++) FW_WRITE("%s[%u,%u]",j?",":"",l->xy[j][0],l->xy[j][1]);
        FW_WRITE("]]");
    }
    FW_WRITE("],\"objects\":[");
    for(i=0;objects && i<v->object_count;i++) {
        const struct fw_object *o=&v->objects[i]; FW_WRITE("%s[%u,%u,%u,%u,%u,%u]",i?",":"",o->cls,o->score,o->box[0],o->box[1],o->box[2],o->box[3]);
    }
    FW_WRITE("]}");
#undef FW_WRITE
    return true;
}
static inline bool fw_json(const struct front_world *v,uint64_t now,bool running,char *out,size_t cap)
{ return fw_json_age(v,now,running,out,cap,1000); }
#endif
