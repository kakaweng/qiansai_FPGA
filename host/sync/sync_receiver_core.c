#include "sync_receiver_core.h"
#include <stdlib.h>
#include <string.h>
static uint32_t be32(const uint8_t *p)
{ return (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3]; }
static uint32_t crc32(const uint8_t *p, size_t bytes)
{
    uint32_t crc=0xffffffffU;
    size_t i; unsigned bit;
    for (i=0;i<bytes;i++) {
        crc^=p[i];
        for (bit=0;bit<8;bit++) crc=(crc>>1)^((0U-(crc&1U))&0xedb88320U);
    }
    return ~crc;
}
bool sync_read_mark(const uint8_t *pixels, unsigned width, unsigned height, struct sync_mark *out)
{
    uint8_t data[32]={0}; unsigned bit,scale=width/640,cell=4*scale,origin=8*scale;
    if (!pixels || !out || !scale || origin+16*cell>width || origin+16*cell>height) return false;
    for (bit=0;bit<256;bit++) {
        unsigned y=origin+(bit/16)*cell+cell/2, x=origin+(bit%16)*cell+cell/2;
        const uint8_t *p=pixels+((size_t)y*width+x)*2;
        unsigned pixel=p[0]|(unsigned)p[1]<<8;
        unsigned luminance=((pixel>>11)&31)*8+((pixel>>5)&63)*4+(pixel&31)*8;
        if (luminance>384) data[bit/8]|=(uint8_t)(128U>>(bit%8));
    }
    if (memcmp(data,"SYN1",4) || data[4]!=1 || data[5]<1 || data[5]>2 || data[6] ||
        data[7]<1 || data[7]>3 || crc32(data,28)!=be32(data+28)) return false;
    out->role=data[5]; out->phase=data[7]; out->session=be32(data+8); out->wire=be32(data+12);
    out->scene_ms=(uint64_t)be32(data+16)<<32 | be32(data+20); out->index=be32(data+24);
    return out->session!=0;
}
void sync_receiver_reset(struct sync_receiver *r, uint32_t session)
{
    unsigned role,i;
    for (role=0;role<2;role++) for(i=0;i<SYNC_SLOTS;i++) r->frames[role][i].valid=false;
    r->next[0]=r->next[1]=0; r->session=session; r->last_wire=0; r->published=false;
}
void sync_receiver_close(struct sync_receiver *r)
{
    unsigned role,i;
    for (role=0;role<2;role++) for(i=0;i<SYNC_SLOTS;i++) free(r->frames[role][i].pixels);
    memset(r,0,sizeof(*r));
}
bool sync_receiver_push(struct sync_receiver *r, const uint8_t *pixels, unsigned role,
                        uint64_t now_ms, struct sync_pair *pair)
{
    struct sync_mark mark; struct sync_slot *slot,*other; unsigned i,index=role-1;
    unsigned width=role==1?1920:640,height=role==1?1080:480;
    uint64_t difference;
    if (role<1 || role>2 || !sync_read_mark(pixels,width,height,&mark) || mark.role!=role || mark.session!=r->session) return false;
    for(i=0;i<SYNC_SLOTS;i++) if (r->frames[index][i].valid &&
        r->frames[index][i].mark.wire==mark.wire) return false; /* A frozen image is not new evidence. */
    slot=&r->frames[index][r->next[index]++%SYNC_SLOTS];
    if (!slot->pixels) slot->pixels=malloc((size_t)width*height*2);
    if (!slot->pixels) return false;
    memcpy(slot->pixels,pixels,(size_t)width*height*2);
    slot->mark=mark; slot->received_ms=now_ms; slot->valid=true;
    for(i=0;i<SYNC_SLOTS;i++) {
        other=&r->frames[1-index][i];
        if (!other->valid || other->mark.wire!=mark.wire || other->mark.phase!=mark.phase ||
            other->received_ms>now_ms || now_ms-other->received_ms>750) continue;
        difference=mark.scene_ms>other->mark.scene_ms?mark.scene_ms-other->mark.scene_ms:other->mark.scene_ms-mark.scene_ms;
        if (difference>150 || (r->published && (int32_t)(mark.wire-r->last_wire)<=0)) continue;
        pair->front=index==0?mark:other->mark; pair->right=index==1?mark:other->mark;
        pair->pixels[index]=slot->pixels; pair->pixels[1-index]=other->pixels;
        pair->age_ms=now_ms-other->received_ms;
        r->last_wire=mark.wire; r->published=true; return true;
    }
    return false;
}
