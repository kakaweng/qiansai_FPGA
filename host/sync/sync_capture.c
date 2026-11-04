#define _POSIX_C_SOURCE 200809L
#include "sync_capture.h"
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
struct sync_capture {
    struct sync_receiver receiver; const char *file; int socket; struct sockaddr_in destination;
    uint32_t front_wire; uint64_t front_seen_ms; bool front_seen;
    struct sync_mark marks[2]; bool new_source[2], source_seen[2];
};
struct sync_capture *sync_capture_open(const char *file)
{
    struct sync_capture *c=calloc(1,sizeof(*c));
    if (!c) return NULL;
    c->file=file; c->socket=socket(AF_INET,SOCK_DGRAM,0);
    if(c->socket<0) { free(c); return NULL; }
    c->destination.sin_family=AF_INET; c->destination.sin_port=htons(9013);
    c->destination.sin_addr.s_addr=htonl(INADDR_LOOPBACK); return c;
}
void sync_capture_close(struct sync_capture *c)
{ if(c) { close(c->socket); sync_receiver_close(&c->receiver); free(c); } }
uint32_t sync_capture_session(const struct sync_capture *c) { return c->receiver.session; }
void sync_capture_report(struct sync_capture *c,const char *report)
{ if(c && report) sendto(c->socket,report,strlen(report),MSG_DONTWAIT,(struct sockaddr *)&c->destination,sizeof(c->destination)); }
bool sync_capture_source(struct sync_capture *c,unsigned role,struct sync_mark *mark)
{
    if(!c || role<1 || role>2 || !c->new_source[role-1]) return false;
    *mark=c->marks[role-1]; return true;
}
bool sync_capture_update(struct sync_capture *c,const uint8_t *front,const uint8_t *right)
{
    FILE *file=fopen(c->file,"r"); unsigned session=0,enabled=0,role;
    struct timespec time; uint64_t now; struct sync_mark mark;
    if(file) { if(fscanf(file,"%u %u",&session,&enabled)!=2) session=enabled=0; fclose(file); }
    if(session!=c->receiver.session) { sync_receiver_reset(&c->receiver,session); c->front_seen=false; memset(c->source_seen,0,sizeof(c->source_seen)); }
    memset(c->new_source,0,sizeof(c->new_source));
    clock_gettime(CLOCK_MONOTONIC,&time); now=(uint64_t)time.tv_sec*1000+(uint64_t)time.tv_nsec/1000000;
    for(role=1;role<=2;role++) {
        struct sync_pair pair; char report[256]; int bytes;
        if(enabled && sync_read_mark(role==1?front:right,role==1?1920:640,role==1?1080:480,&mark) &&
            mark.session==session && mark.role==role && mark.phase==2 &&
            (!c->source_seen[role-1] || (int32_t)(mark.wire-c->marks[role-1].wire)>0)) {
            c->new_source[role-1]=c->source_seen[role-1]=true; c->marks[role-1]=mark;
        }
        if(!sync_receiver_push(&c->receiver,role==1?front:right,role,now,&pair)) continue;
        bytes=snprintf(report,sizeof(report),"PAIR %u %u %u %llu %llu %llu",session,pair.front.wire,pair.front.phase,
            (unsigned long long)pair.front.scene_ms,(unsigned long long)pair.right.scene_ms,(unsigned long long)pair.age_ms);
        sendto(c->socket,report,(size_t)bytes,MSG_DONTWAIT,(struct sockaddr *)&c->destination,sizeof(c->destination));
    }
    if(!enabled || !sync_read_mark(front,1920,1080,&mark) || mark.session!=session || mark.phase!=2) return false;
    if(!c->front_seen || mark.wire!=c->front_wire) {
        c->front_seen=true; c->front_wire=mark.wire; c->front_seen_ms=now;
    }
    return now>=c->front_seen_ms && now-c->front_seen_ms<=750;
}
