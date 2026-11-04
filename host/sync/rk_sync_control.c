/* RK-owned replay controller. Local HTTP panel + local UDP receiver reports.
 * Windows build is supported solely for socket/state-machine integration tests.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <signal.h>
#ifdef HAVE_DRIVE_ASSIST
#include "drive_assist.h"
#endif
#ifdef HAVE_FRONT_P1
#include "front_world.h"
#endif
#ifdef HAVE_CROSSWALK
#include "crosswalk_observation.h"
#include "pedestrian_gate.h"
#endif
#ifdef HAVE_PIE_ANTICIPATION
#include "pie_anticipation_core.h"
#endif
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
typedef SOCKET sync_socket;
#define close_socket closesocket
#else
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <unistd.h>
typedef int sync_socket;
#define INVALID_SOCKET (-1)
#define SOCKET_ERROR (-1)
#define close_socket close
#endif
static volatile sig_atomic_t quit;
static void stop_control(int unused) { (void)unused; quit=1; }
static uint64_t now_ms(void)
{
#ifdef _WIN32
    return GetTickCount64();
#else
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000+(uint64_t)t.tv_nsec/1000000;
#endif
}
static uint32_t session_seed(void)
{
    uint32_t seed=0;
#ifndef _WIN32
    FILE *random=fopen("/dev/urandom","rb");
    if(random) { if(fread(&seed,sizeof(seed),1,random)!=1) seed=0; fclose(random); }
#else
    LARGE_INTEGER counter; QueryPerformanceCounter(&counter);
    seed=(uint32_t)counter.QuadPart ^ GetCurrentProcessId();
#endif
    if(!seed) seed=(uint32_t)time(NULL)^(uint32_t)now_ms();
    return seed?seed:1;
}
struct control {
    const char *state,*action,*reason,*file;
    uint32_t session,id;
    unsigned phase,wire;
    uint64_t changed,pair_seen,front_ms,right_ms,source_seen;
    int acknowledged;
#ifdef HAVE_DRIVE_ASSIST
    struct drive_assist drive;
#ifdef HAVE_FRONT_P1
    char p1_last_event[256];
    struct front_world world;
    struct front_world display_world;
    uint64_t world_since;
#ifdef HAVE_CROSSWALK
    struct crosswalk_observation crosswalk;
    struct pedestrian_gate pedestrians;
#endif
#ifdef HAVE_PIE_ANTICIPATION
    struct pie_anticipation anticipation;
#endif
#endif
#endif
};
#ifdef HAVE_DRIVE_ASSIST
static void control_drive_update(struct control *control, bool running, uint64_t now)
{
#ifdef HAVE_CROSSWALK
    pg_update(&control->pedestrians, &control->world, &control->crosswalk, now, running);
    control->drive.p1.people_spatial = true;
    control->drive.p1.people_state = control->pedestrians.status;
    front_p1_people(&control->drive.p1, control->pedestrians.valid, control->pedestrians.relevant,
                    control->pedestrians.object_ms, now);
#endif
    drive_update(&control->drive, running, now);
}
#endif
static void save_state(struct control *c)
{
    char path[1024]; FILE *f;
    if (!c->file) return;
    snprintf(path,sizeof(path),"%s.tmp",c->file);
    f=fopen(path,"w");
    if (!f) { perror("sync state file"); quit=1; return; }
    fprintf(f,"%u %u\n",c->session,!strcmp(c->state,"RUNNING") || !strcmp(c->state,"WAIT_FRAMES") || !strcmp(c->state,"SYNC_LOST"));
    if (fclose(f)) { perror("sync state flush"); quit=1; return; }
#ifdef _WIN32
    if (!MoveFileExA(path,c->file,MOVEFILE_REPLACE_EXISTING)) quit=1;
#else
    if (rename(path,c->file)) { perror("sync state rename"); quit=1; }
#endif
}
static void transition(struct control *c,const char *state,const char *reason)
{
    c->state=state; c->reason=reason; c->changed=now_ms(); save_state(c);
#ifdef HAVE_FRONT_P1
    if(strcmp(state,"RUNNING")) memset(&c->world,0,sizeof(c->world));
#endif
#ifdef HAVE_CROSSWALK
    if(strcmp(state,"RUNNING")) {
        memset(&c->crosswalk,0,sizeof(c->crosswalk));
        pg_reset(&c->pedestrians);
    }
#endif
#ifdef HAVE_PIE_ANTICIPATION
    if(strcmp(state,"RUNNING")) pa_reset(&c->anticipation);
#endif
    printf("SYNC state=%s session=%u reason=%s\n",state,c->session,reason); fflush(stdout);
}
#ifndef FRONT_ONLY
static void command(struct control *c,const char *action)
{ c->id++; if (!c->id) c->id++; c->action=action; c->acknowledged=0; }
#endif
static sync_socket listener(unsigned port,int udp)
{
    sync_socket s=socket(AF_INET,udp?SOCK_DGRAM:SOCK_STREAM,0);
    struct sockaddr_in a; int enabled=1;
    if (s==INVALID_SOCKET) return s;
    memset(&a,0,sizeof(a)); a.sin_family=AF_INET; a.sin_port=htons((uint16_t)port); a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    setsockopt(s,SOL_SOCKET,SO_REUSEADDR,(const char *)&enabled,sizeof(enabled));
    if (bind(s,(struct sockaddr *)&a,sizeof(a))==SOCKET_ERROR || (!udp && listen(s,8)==SOCKET_ERROR)) {
        close_socket(s); return INVALID_SOCKET;
    }
    return s;
}
static void respond(sync_socket s,int status,const char *type,const char *body)
{
    char header[384]; size_t size=strlen(body),sent=0; int n;
    n=snprintf(header,sizeof(header),"HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %lu\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
        status,status==200?"OK":"Error",type,(unsigned long)size);
    if (send(s,header,n,0)!=n) return;
    while(sent<size) { n=send(s,body+sent,(int)(size-sent),0); if(n<=0) break; sent+=(size_t)n; }
}
#ifdef HAVE_DRIVE_ASSIST
#ifdef HAVE_FRONT_P1
#include "front_p1_panel.h"
#elif defined(FRONT_ONLY)
#include "front_panel.h"
#else
#include "drive_panel.h"
#endif
#else
static const char panel[]= "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
"<title>RK3568 多路检测控制</title><style>body{font:18px system-ui;background:#101a2b;color:#eef;padding:32px;max-width:900px;margin:auto}"
"button{font:inherit;padding:14px 24px;margin:12px 12px 12px 0}pre{white-space:pre-wrap;line-height:1.7}</style>"
"<h1>多路检测控制 · RK3568</h1><p>先打开三路预览和 Windows 受控视频源，再开始。OV5640：手动对齐，未确认同步。</p>"
"<button onclick=\"act('start')\">开始检测 / 从头重播</button><button onclick=\"act('stop')\">停止</button><pre id='status'>连接中…</pre>"
"<p>同步确认依据：收到同一播放编号、同一配对编号的两路画面，源时间差≤150ms，缓存接收年龄≤750ms。此面板尚不执行变道放行。</p>"
"<script>const names={IDLE:'等待开始',PREPARING:'视频源回到起点',WAIT_READY:'等待两路首帧到达板端',"
"STARTING:'下发共同播放指令',WAIT_FRAMES:'等待播放画面配对',RUNNING:'检测中：已确认画面配对',"
"SYNC_LOST:'同步丢失：暂停联合判断',ENDED:'录像结束',STOPPING:'正在停止',STOPPED:'已停止',ERROR:'故障'};"
"async function act(a){try{await fetch('/api/'+a,{method:'POST',headers:{'X-Sync-Control':'1'}})}catch(e){document.getElementById('status').textContent=String(e)}}"
"async function tick(){try{let s=await(await fetch('/api/status')).json();document.getElementById('status').textContent="
"(names[s.state]||s.state)+'\\n播放编号：'+s.session+'\\n配对编号：'+s.wire+'\\n前方源时间：'+s.front_ms+'ms　右侧：'+s.right_ms+"
"'ms\\n最近配对年龄：'+s.pair_age_ms+'ms\\n状态原因：'+s.reason+'\\nOV5640：手动，未确认'}catch(e){document.getElementById('status').textContent='控制服务连接中断：'+e}setTimeout(tick,300)}tick()</script></html>";
#endif
static void http_request(struct control *c,sync_socket client)
{
    char request[4096],method[12],path[256],body[65536]; int length=0,n;
    unsigned id=0,session=0,ok=0; char tail;
#ifdef _WIN32
    DWORD timeout=500;
#else
    struct timeval timeout={0,500000};
#endif
    setsockopt(client,SOL_SOCKET,SO_RCVTIMEO,(const char *)&timeout,sizeof(timeout));
    setsockopt(client,SOL_SOCKET,SO_SNDTIMEO,(const char *)&timeout,sizeof(timeout));
    while(length<(int)sizeof(request)-1) {
        n=recv(client,request+length,(int)sizeof(request)-1-length,0);
        if(n<=0) break;
        length+=n; request[length]=0;
        if(strstr(request,"\r\n\r\n")) break;
    }
    if(length<=0 || !strstr(request,"\r\n\r\n") || sscanf(request,"%11s %255s",method,path)!=2) return;
    {
        char *end=strstr(request,"\r\n\r\n"), *field=strstr(request,"\r\nContent-Length:");
        unsigned long bytes=0;
        size_t header=(size_t)(end-request)+4;
        if(!field) field=strstr(request,"\r\ncontent-length:");
        if(field && sscanf(field+17,"%lu",&bytes)!=1) return;
        if(bytes>1024 || header+bytes>=sizeof(request)) return;
        /* Drain a POST body before closing: otherwise a late body causes TCP RST
         * and the relay loses an otherwise successful acknowledgement. */
        while((size_t)length<header+bytes) {
            n=recv(client,request+length,(int)(header+bytes-(size_t)length),0);
            if(n<=0) return;
            length+=n;
        }
        request[length]=0;
    }
    if (!strcmp(method,"GET") && !strcmp(path,"/")) { respond(client,200,"text/html; charset=utf-8",panel); return; }
    if (!strcmp(method,"GET") && !strcmp(path,"/api/status")) {
#ifdef HAVE_DRIVE_ASSIST
        char drive[2048];
        control_drive_update(c,!strcmp(c->state,"RUNNING"),now_ms());
        drive_json(&c->drive,now_ms(),drive,sizeof(drive));
#ifdef FRONT_ONLY
#ifdef HAVE_FRONT_P1
        char world[FW_JSON];
        if(!fw_json(&c->world,now_ms(),!strcmp(c->state,"RUNNING"),world,sizeof(world))) strcpy(world,"null");
#ifdef HAVE_CROSSWALK
        char crosswalk[CW_JSON_BYTES];
        if(!cw_json(&c->crosswalk,now_ms(),!strcmp(c->state,"RUNNING") && c->world.producer,c->world.producer,crosswalk,sizeof(crosswalk))) strcpy(crosswalk,"null");
#endif
#ifdef HAVE_PIE_ANTICIPATION
        char anticipation[PA_JSON];
        pa_update(&c->anticipation,&c->world,now_ms(),!strcmp(c->state,"RUNNING"));
        if(!pa_json(&c->anticipation,now_ms(),!strcmp(c->state,"RUNNING"),anticipation,sizeof(anticipation))) strcpy(anticipation,"null");
        snprintf(body,sizeof(body),"{\"state\":\"%s\",\"reason\":\"%s\",\"session\":%u,\"source\":\"HDMI\",\"drive\":%s,\"world\":%s,\"anticipation\":%s"
#ifdef HAVE_CROSSWALK
            ",\"crosswalk\":%s"
#endif
            "}",c->state,c->reason,c->session,drive,world,anticipation
#ifdef HAVE_CROSSWALK
            ,crosswalk
#endif
            );
#else
        snprintf(body,sizeof(body),"{\"state\":\"%s\",\"reason\":\"%s\",\"session\":%u,\"source\":\"HDMI\",\"drive\":%s,\"world\":%s"
#ifdef HAVE_CROSSWALK
            ",\"crosswalk\":%s"
#endif
            "}",c->state,c->reason,c->session,drive,world
#ifdef HAVE_CROSSWALK
            ,crosswalk
#endif
            );
#endif
#else
        snprintf(body,sizeof(body),"{\"state\":\"%s\",\"reason\":\"%s\",\"session\":%u,\"source\":\"HDMI\",\"drive\":%s}",
            c->state,c->reason,c->session,drive);
#endif
#else
        snprintf(body,sizeof(body),"{\"state\":\"%s\",\"reason\":\"%s\",\"session\":%u,\"wire\":%u,\"front_ms\":%llu,\"right_ms\":%llu,\"pair_age_ms\":%lld,\"ov5640\":\"manual_unverified\",\"drive\":%s}",
            c->state,c->reason,c->session,c->wire,(unsigned long long)c->front_ms,(unsigned long long)c->right_ms,
            c->pair_seen?(long long)(now_ms()-c->pair_seen):-1LL,drive);
#endif
#else
        snprintf(body,sizeof(body),"{\"state\":\"%s\",\"reason\":\"%s\",\"session\":%u,\"wire\":%u,\"front_ms\":%llu,\"right_ms\":%llu,\"pair_age_ms\":%lld,\"ov5640\":\"manual_unverified\"}",
            c->state,c->reason,c->session,c->wire,(unsigned long long)c->front_ms,(unsigned long long)c->right_ms,
            c->pair_seen?(long long)(now_ms()-c->pair_seen):-1LL);
#endif
#ifdef HAVE_CROSSWALK
        {
            char pedestrians[PG_JSON];
            size_t used = strlen(body);
            if (!pg_json(&c->pedestrians, now_ms(), pedestrians, sizeof(pedestrians))) strcpy(pedestrians, "null");
            if (used && body[used - 1] == '}')
                snprintf(body + used - 1, sizeof(body) - used + 1, ",\"pedestrian\":%s}", pedestrians);
        }
#endif
#if defined(FRONT_ONLY) && defined(HAVE_FRONT_P1)
        {
            char display[FW_JSON];size_t used=strlen(body);
            if(!fw_json_age(&c->display_world,now_ms(),!strcmp(c->state,"RUNNING"),display,sizeof(display),2500)) strcpy(display,"null");
            if(used && body[used-1]=='}')
                snprintf(body+used-1,sizeof(body)-used+1,",\"display_only\":true,\"display_world\":%s}",display);
        }
#endif
    } else if (!strcmp(method,"GET") && !strcmp(path,"/api/request")) {
        snprintf(body,sizeof(body),"{\"id\":%u,\"session\":%u,\"action\":\"%s\",\"pending\":%s}",
            c->id,c->session,c->action,c->acknowledged?"false":"true");
    } else if (strcmp(method,"POST") || !strstr(request,"\r\nX-Sync-Control: 1\r\n")) {
        respond(client,403,"application/json","{\"error\":\"control header required\"}"); return;
    } else if (!strcmp(path,"/api/start")) {
#ifdef HAVE_PIE_ANTICIPATION
        pa_reset(&c->anticipation);
#endif
#ifdef HAVE_FRONT_P1
        memset(&c->world,0,sizeof(c->world));memset(&c->display_world,0,sizeof(c->display_world)); c->world_since=now_ms();
#endif
#ifdef HAVE_CROSSWALK
        memset(&c->crosswalk,0,sizeof(c->crosswalk));
        pg_reset(&c->pedestrians);
#endif
#ifdef HAVE_DRIVE_ASSIST
        drive_reset(&c->drive);
#endif
        c->session++; if(!c->session) c->session++;
        c->wire=c->phase=0; c->pair_seen=0; c->front_ms=c->right_ms=0;
#ifdef FRONT_ONLY
        c->action="start"; c->acknowledged=1;
        transition(c,"RUNNING","front_hdmi_started"); strcpy(body,"{\"ok\":true}");
#else
        command(c,"prepare"); transition(c,"PREPARING","waiting_source_prepare"); strcpy(body,"{\"ok\":true}");
#endif
    } else if (!strcmp(path,"/api/stop")) {
#ifdef HAVE_DRIVE_ASSIST
        drive_reset(&c->drive);
#endif
#ifdef FRONT_ONLY
        c->acknowledged=1; transition(c,"STOPPED","user_stopped");
#else
        if(!strcmp(c->state,"IDLE") || !strcmp(c->state,"STOPPED")) {
            c->acknowledged=1; transition(c,"STOPPED","user_stopped");
        } else {
            if (!c->session) c->session=1;
            command(c,"stop"); transition(c,"STOPPING","waiting_source_stop");
        }
#endif
        strcpy(body,"{\"ok\":true}");
#ifdef HAVE_DRIVE_ASSIST
 #ifdef HAVE_FRONT_P1
    } else if (!strncmp(path,"/api/pick?",10)) {
        unsigned x, y;
        if (sscanf(path,"/api/pick?x=%u&y=%u%c", &x, &y, &tail) != 2 ||
            !front_p1_pick(&c->drive.p1, x, y, now_ms())) {
            respond(client,409,"application/json; charset=utf-8","{\"error\":\"点击位置没有唯一的新鲜LIGHT目标，请直接点击灯框中心\"}"); return;
        }
        drive_json(&c->drive,now_ms(),body,sizeof(body));
    } else if (!strncmp(path,"/api/bind?",10)) {
        unsigned roi[4];
        if (sscanf(path,"/api/bind?x0=%u&y0=%u&x1=%u&y1=%u%c", &roi[0], &roi[1], &roi[2], &roi[3], &tail) != 4 ||
            !front_p1_bind(&c->drive.p1, roi, now_ms())) {
            respond(client,409,"application/json; charset=utf-8","{\"error\":\"请先选择行驶方向，再框选一盏对应交通灯\"}"); return;
        }
        drive_json(&c->drive,now_ms(),body,sizeof(body));
 #endif
    } else if (!strncmp(path,"/api/drive?action=",18)) {
        drive_update(&c->drive,!strcmp(c->state,"RUNNING"),now_ms());
        if(!drive_action(&c->drive,path+18,now_ms())) {
#ifdef HAVE_FRONT_P1
            if (!strncmp(path+18, "scene_", 6) || c->drive.p1.scene.enabled) {
                if (strcmp(c->state,"RUNNING"))
                    respond(client,409,"application/json; charset=utf-8","{\"error\":\"请先开始检测并确认HDMI输入正常\"}");
                else if (!c->drive.p1.scene.enabled)
                    respond(client,409,"application/json; charset=utf-8","{\"error\":\"请先按M进入路口演示\"}");
                else if (!strcmp(path+18,"scene_force") || !strcmp(path+18,"force_go"))
                    respond(client,409,"application/json; charset=utf-8","{\"error\":\"需在停止线前确认红/黄灯，再违规演示\"}");
                else if (c->drive.p1.scene.distance_mm >= FRONT_SCENE_END_MM)
                    respond(client,409,"application/json; charset=utf-8","{\"error\":\"本次已完成，按R重置场景\"}");
                else respond(client,409,"application/json; charset=utf-8","{\"error\":\"请暂停车辆；进入路口后须重置再换方向\"}");
            }
            else respond(client,409,"application/json; charset=utf-8","{\"error\":\"请开始检测并选择方向；有多盏灯时，直接点击对应LIGHT框再起步\"}");
            return;
#else
            respond(client,409,"application/json; charset=utf-8","{\"error\":\"停车后换挡；模拟加速需检测运行且处于D或R挡\"}"); return;
#endif
        }
        drive_json(&c->drive,now_ms(),body,sizeof(body));
#endif
    } else if (sscanf(path,"/api/ack?id=%u&session=%u&ok=%u%c",&id,&session,&ok,&tail)==3 && ok<=1) {
        if (id==c->id && session==c->session && !c->acknowledged) {
            c->acknowledged=1;
            if (!ok) transition(c,"ERROR","source_command_failed_check_relay");
            else if (!strcmp(c->action,"prepare")) transition(c,"WAIT_READY","waiting_received_ready_pair_open_preview");
            else if (!strcmp(c->action,"play")) transition(c,"WAIT_FRAMES","waiting_received_playing_pair");
            else transition(c,"STOPPED","user_stopped");
        }
        strcpy(body,"{\"ok\":true}");
    } else { respond(client,404,"application/json","{\"error\":\"unknown_route\"}"); return; }
    respond(client,200,"application/json; charset=utf-8",body);
}
static void observation(struct control *c,sync_socket udp)
{
    char data[8192],tail; unsigned session,wire,phase; unsigned long long front,right,age;
    int n=recv(udp,data,sizeof(data)-1,0);
    if(n<=0) return;
    data[n]=0;
#ifdef HAVE_DRIVE_ASSIST
#ifdef FRONT_ONLY
#ifdef HAVE_FRONT_P1
#ifdef HAVE_CROSSWALK
    if(n>=4 && !memcmp(data,"FCW1",4)) {
        if(!strcmp(c->state,"RUNNING") && c->world.producer)
            cw_unpack(&c->crosswalk,(const unsigned char *)data,(size_t)n,now_ms(),c->world_since,c->world.producer);
        return;
    }
#endif
    if(n>=4 && !memcmp(data,"FWD1",4)) {
        if(!strcmp(c->state,"RUNNING") && n>=(int)FW_HEADER) {
            memcpy(data,"FWM1",4);
            if(c->world.producer && fw_get((const unsigned char *)data+4,4)==c->world.producer)
                fw_unpack_age(&c->display_world,(const unsigned char *)data,(size_t)n,now_ms(),c->world_since,2500);
        }
        return;
    }
    if(n>=4 && !memcmp(data,"FWM1",4)) {
        if(!strcmp(c->state,"RUNNING")) {
            bool accepted=fw_unpack(&c->world,(const unsigned char *)data,(size_t)n,now_ms(),c->world_since);
            if(accepted && c->display_world.producer && c->display_world.producer!=c->world.producer)
                memset(&c->display_world,0,sizeof(c->display_world));
#ifdef HAVE_CROSSWALK
            if(accepted && c->crosswalk.producer && c->crosswalk.producer!=c->world.producer)
                memset(&c->crosswalk,0,sizeof(c->crosswalk));
#endif
#ifdef HAVE_PIE_ANTICIPATION
            if(accepted) pa_update(&c->anticipation,&c->world,now_ms(),true);
#endif
#if defined(HAVE_PIE_ANTICIPATION) || defined(HAVE_CROSSWALK)
            if(!accepted && (n<(int)FW_HEADER || fw_get((const unsigned char *)data+4,4)!=c->world.producer ||
                    fw_get((const unsigned char *)data+48,8)!=c->world.object_seq ||
                    fw_get((const unsigned char *)data+56,8)!=c->world.object_ms)) {
#ifdef HAVE_PIE_ANTICIPATION
                pa_reset(&c->anticipation);
#endif
                memset(&c->world,0,sizeof(c->world));
#ifdef HAVE_CROSSWALK
                memset(&c->crosswalk,0,sizeof(c->crosswalk));
#endif
            }
#else
            (void)accepted;
#endif
        }
        return;
    }
    if (!strncmp(data, "LIGHTS ", 7)) {
        if (!strcmp(c->state, "RUNNING")) front_p1_accept(&c->drive.p1, data, now_ms());
        return;
    }
#endif
    if(!strncmp(data,"FRONT ",6)) {
        uint64_t received_now = now_ms();
        bool accepted;
        control_drive_update(c,!strcmp(c->state,"RUNNING") || !strcmp(c->state,"HDMI_STALE"),received_now);
        accepted = drive_accept_front(&c->drive,data,received_now);
        if(accepted && !strcmp(c->state,"HDMI_STALE"))
            transition(c,"RUNNING","front_hdmi_resumed");
        return;
    }
#else
    if(!strncmp(data,"DRIVE ",6)) {
        if(!strcmp(c->state,"RUNNING")) drive_accept(&c->drive,data,c->session,now_ms());
        return;
    }
#endif
#endif
    if(sscanf(data,"PAIR %u %u %u %llu %llu %llu %c",&session,&wire,&phase,&front,&right,&age,&tail)!=6 ||
        session!=c->session || phase<1 || phase>3 || age>750 ||
        (front>right?front-right:right-front)>150) return;
    if(c->wire && (int32_t)(wire-c->wire)<=0) return;
    c->wire=wire; c->phase=phase; c->front_ms=front; c->right_ms=right; c->pair_seen=now_ms();
}
int main(int argc,char **argv)
{
    unsigned port=9010,report_port=9013; sync_socket http,udp; int i;
    struct control c={.state="IDLE",.action="stop",.reason="waiting_start",.acknowledged=1};
#ifdef HAVE_DRIVE_ASSIST
    drive_reset(&c.drive);
#endif
#ifdef HAVE_PIE_ANTICIPATION
    pa_reset(&c.anticipation);
#endif
#ifdef _WIN32
    WSADATA data; if(WSAStartup(MAKEWORD(2,2),&data)) return 1;
#else
    signal(SIGPIPE,SIG_IGN);
    signal(SIGHUP,SIG_IGN);
#endif
    for(i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--port") && i+1<argc) port=(unsigned)strtoul(argv[++i],NULL,10);
        else if(!strcmp(argv[i],"--report-port") && i+1<argc) report_port=(unsigned)strtoul(argv[++i],NULL,10);
        else if(!strcmp(argv[i],"--state-file") && i+1<argc) c.file=argv[++i];
        else { fprintf(stderr,"Usage: rk_sync_control [--port 9010] [--report-port 9013] --state-file PATH\n"); return 1; }
    }
    if(!port || port>65535 || !report_port || report_port>65535) return 1;
    http=listener(port,0); udp=listener(report_port,1);
    if(http==INVALID_SOCKET || udp==INVALID_SOCKET) { fprintf(stderr,"Sync control port occupied\n"); return 1; }
    c.session=session_seed(); c.changed=now_ms(); save_state(&c);
    signal(SIGINT,stop_control); signal(SIGTERM,stop_control);
    printf("RK_SYNC_CONTROL_READY http=127.0.0.1:%u report=%u\n",port,report_port); fflush(stdout);
#ifdef HAVE_CROSSWALK
    puts("PEDESTRIAN_POLICY_READY policy=crosswalk-contact-v1"); fflush(stdout);
#endif
    while(!quit) {
        fd_set reads; struct timeval timeout={0,100000}; int ready;
        FD_ZERO(&reads); FD_SET(http,&reads); FD_SET(udp,&reads);
        ready=select((int)(http>udp?http:udp)+1,&reads,NULL,NULL,&timeout);
        if(ready>0 && FD_ISSET(http,&reads)) {
            sync_socket client=accept(http,NULL,NULL);
            if(client!=INVALID_SOCKET) { http_request(&c,client); close_socket(client); }
        }
        if(ready>0 && FD_ISSET(udp,&reads)) observation(&c,udp);
#ifdef HAVE_PIE_ANTICIPATION
        pa_update(&c.anticipation,&c.world,now_ms(),!strcmp(c.state,"RUNNING"));
#endif
#ifdef HAVE_DRIVE_ASSIST
        control_drive_update(&c,!strcmp(c.state,"RUNNING"),now_ms());
#ifdef HAVE_FRONT_P1
        {
            const struct front_p1 *p1 = &c.drive.p1;
            char event[256];
            snprintf(event, sizeof(event), "P1 direction=%u bound=%u signal=%u reason=%s speed=%u led=%u alarm=%u lane=%s solid_alarm=%u scene=%u phase=%s run=%u violation=%u",
                p1->direction, (unsigned)p1->bound, p1->signal, p1->reason, p1->speed, p1->led,
                (unsigned)p1->alarm, c.drive.feedback, (unsigned)c.drive.solid_alarm,
                (unsigned)p1->scene.enabled, p1->scene.phase, p1->scene.run_id, (unsigned)p1->scene.violation);
            if (strcmp(event, c.p1_last_event)) {
                strcpy(c.p1_last_event, event);
                printf("%s\n", event); fflush(stdout);
            }
        }
#endif
#endif
#ifdef FRONT_ONLY
        if(!strcmp(c.state,"RUNNING") &&
           (!c.drive.evidence.received || now_ms() < c.drive.evidence.received ||
            now_ms()-c.drive.evidence.received>1000))
            transition(&c,"HDMI_STALE","no_fresh_front_hdmi_report");
#else
        if(!strcmp(c.state,"WAIT_READY") && c.phase==1 && c.pair_seen && now_ms()-c.pair_seen<750) {
            command(&c,"play"); transition(&c,"STARTING","waiting_source_play");
        } else if ((!strcmp(c.state,"WAIT_FRAMES") || !strcmp(c.state,"SYNC_LOST")) && c.phase==2 && c.pair_seen && now_ms()-c.pair_seen<750) {
            transition(&c,"RUNNING","received_matching_source_pair");
        } else if ((!strcmp(c.state,"RUNNING") || !strcmp(c.state,"SYNC_LOST") || !strcmp(c.state,"WAIT_FRAMES")) && c.phase==3 && c.pair_seen && now_ms()-c.pair_seen<750) {
            transition(&c,"ENDED","source_playback_held_or_ended");
        } else if(!strcmp(c.state,"RUNNING") && now_ms()-c.pair_seen>1000) {
            transition(&c,"SYNC_LOST","no_fresh_matching_pair");
        } else if((!strcmp(c.state,"PREPARING") || !strcmp(c.state,"WAIT_READY") || !strcmp(c.state,"STARTING") || !strcmp(c.state,"WAIT_FRAMES") || !strcmp(c.state,"STOPPING")) && now_ms()-c.changed>20000) {
            c.acknowledged=1; transition(&c,"ERROR","timeout_check_source_relay_and_preview");
        }
#endif
    }
    transition(&c,"STOPPED","controller_shutdown"); close_socket(http); close_socket(udp); return 0;
}
