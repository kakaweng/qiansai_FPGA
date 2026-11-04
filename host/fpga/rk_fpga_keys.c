/* Read-only PCIe key adapter. No DMA, BAR writes or driving decisions. */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopped;
static void stop_service(int sig) { (void)sig; stopped=1; }
static int bdf_valid(const char *text)
{
    size_t i;
    if(strlen(text)!=12 || text[4]!=':' || text[7]!=':' || text[10]!='.' || text[11]<'0' || text[11]>'7') return 0;
    for(i=0;i<12;i++) if(i!=4 && i!=7 && i!=10 && !isxdigit((unsigned char)text[i])) return 0;
    return 1;
}
static void diagnose_fault(int signum)
{
    static const char message[] = "JUNCTION_PCIE_PROBE_ABORTED: MMIO signal or read deadline; report is incomplete\n";
    (void)write(STDERR_FILENO,message,sizeof(message)-1);
    _exit(128+signum);
}
static void diagnose_bar0(volatile uint32_t *bar,const char *bdf)
{
    static const unsigned offsets[] = {0x000,0x004,0x008,0x00c,0x100,0x104,0x108,0x10c,
        0x110,0x114,0x118,0x11c,0x120,0x124,0x128,0x12c,0x150,0x154,0x158,0x15c,
        0x160,0x164,0x168,0x16c,0x170,0x174,0x178,0x17c,0x180,0x184,0x188,0x18c};
    char path[256];
    unsigned char configuration[64];
    uint32_t values[sizeof(offsets)/sizeof(offsets[0])];
    snprintf(path,sizeof(path),"/sys/bus/pci/devices/%s/config",bdf);
    int fd=open(path,O_RDONLY|O_SYNC);
    ssize_t length=fd>=0?pread(fd,configuration,sizeof(configuration),0):-1;
    if(fd>=0) close(fd);
    if(length>=8) {
        unsigned vendor=configuration[0]|(unsigned)configuration[1]<<8;
        unsigned device=configuration[2]|(unsigned)configuration[3]<<8;
        unsigned command=configuration[4]|(unsigned)configuration[5]<<8;
        unsigned status=configuration[6]|(unsigned)configuration[7]<<8;
        printf("PROBE_CONFIG vendor=%04x device=%04x command=%04x memory_decode=%u bus_master=%u status=%04x bytes=%ld\n",
                vendor,device,command,!!(command&2),!!(command&4),status,(long)length);
        for(unsigned offset=0;offset+4<=(unsigned)length;offset+=4) {
            uint32_t value=configuration[offset]|(uint32_t)configuration[offset+1]<<8|
                (uint32_t)configuration[offset+2]<<16|(uint32_t)configuration[offset+3]<<24;
            printf("PROBE_CONFIG_DWORD offset=%03x value=%08x\n",offset,value);
        }
    } else fprintf(stderr,"PROBE_CONFIG_UNREADABLE bytes=%ld errno=%d\n",(long)length,errno);
    for(unsigned sample=0;sample<3;sample++) {
        int all_ones=1;
        for(unsigned index=0;index<sizeof(offsets)/sizeof(offsets[0]);index++) {
            values[index]=bar[offsets[index]/4];
            all_ones &= values[index]==UINT32_MAX;
            printf("PROBE_BAR0 sample=%u offset=%03x value=%08x\n",sample,offsets[index],values[index]);
        }
        int video=values[1]==4147200U && values[2]==0x52473536U && values[16]==0x4d565331U &&
            (values[17]&3)==3 && values[18]==0x160 && values[19]==1;
        int keys=values[24]==0x4a4b3031U && values[27]==1;
        int feedback=values[28]==0x4c463031U && values[31]==1;
        printf("PROBE_SUMMARY sample=%u video_metadata=%s keys=%s feedback=%s bar0_all_ones=%u\n",
                sample,video?"OK":"UNCONFIRMED",keys?"OK":"UNCONFIRMED",feedback?"OK":"UNCONFIRMED",all_ones);
        fflush(stdout);
        if(sample<2) { struct timespec delay={0,100000000};nanosleep(&delay,NULL); }
    }
    puts("JUNCTION_PCIE_PROBE_COLLECTED read_only=1 hardware_health_not_assumed=1");
}
int main(int argc,char **argv)
{
    char path[256],generation[80],output[512];
    const char *pid_path=NULL,*log_path=NULL;
    int fd,server=-1,index,inspect=0,diagnose=0,daemon_mode=0,port=9014;
    volatile uint32_t *bar;
    struct sockaddr_in address;
    struct timespec started;
    if(argc<2 || !bdf_valid(argv[1])) { fprintf(stderr,"Usage: rk_fpga_keys PCI_BDF [--inspect|--diagnose] [--port N] [--daemon --pid FILE --log FILE]\n");return 1; }
    for(index=2;index<argc;index++) {
        if(!strcmp(argv[index],"--inspect")) inspect=1;
        else if(!strcmp(argv[index],"--diagnose")) diagnose=1;
        else if(!strcmp(argv[index],"--daemon")) daemon_mode=1;
        else if(index+1<argc && !strcmp(argv[index],"--pid")) pid_path=argv[++index];
        else if(index+1<argc && !strcmp(argv[index],"--log")) log_path=argv[++index];
        else if(index+1<argc && !strcmp(argv[index],"--port")) {
            char *end;long parsed=strtol(argv[++index],&end,10);
            if(!*argv[index] || *end || parsed<1024 || parsed>65535) return 1;
            port=(int)parsed;
        } else return 1;
    }
    if(diagnose) {
        if(argc!=3 || inspect || daemon_mode) return 1;
        setvbuf(stdout,NULL,_IOLBF,0);
        signal(SIGBUS,diagnose_fault);signal(SIGALRM,diagnose_fault);alarm(5);
    }
    snprintf(path,sizeof(path),"/sys/bus/pci/devices/%s/resource0",argv[1]);
    fd=open(path,O_RDONLY|O_SYNC);
    if(fd<0) { perror(path);return 1; }
    bar=mmap(NULL,4096,PROT_READ,MAP_SHARED,fd,0);close(fd);
    if(bar==MAP_FAILED) { perror("mmap read-only BAR0");return 1; }
    if(diagnose) {
        diagnose_bar0(bar,argv[1]);munmap((void *)bar,4096);alarm(0);return 0;
    }
    uint32_t capability_magic=bar[0x170/4],capability_abi=bar[0x17c/4];
    if(capability_magic!=0x4a4b3031 || capability_abi!=1) {
        fprintf(stderr,"FPGA_KEY_CAPABILITY_READ bdf=%s magic=%08x expected=4a4b3031 abi=%u expected_abi=1 feedback_magic=%08x\n",
                argv[1],capability_magic,capability_abi,bar[0x180/4]);
        fprintf(stderr,"FPGA_KEY_CAPABILITY_UNCONFIRMED: check PCIe enable/driver and register readback; this alone does not identify the loaded bitstream\n");return 2;
    }
    if(inspect) {
        printf("FPGA_KEY_INSPECT_PASS magic=JK01 abi=1 event=%u pressed=%u\n",bar[0x174/4],bar[0x178/4]&7);return 0;
    }
    server=socket(AF_INET,SOCK_STREAM,0);
    if(server<0) { perror("socket");return 1; }
    { int enabled=1;setsockopt(server,SOL_SOCKET,SO_REUSEADDR,&enabled,sizeof(enabled)); }
    memset(&address,0,sizeof(address));address.sin_family=AF_INET;
    address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons((uint16_t)port);
    if(bind(server,(struct sockaddr *)&address,sizeof(address)) || listen(server,8)) { perror("key port");return 1; }
    if(daemon_mode) {
        pid_t child;
        if(!pid_path || !log_path) { fprintf(stderr,"daemon requires --pid and --log\n");return 1; }
        fd=open(log_path,O_WRONLY|O_CREAT|O_APPEND,0644);
        if(fd<0) { perror("log");return 1; }
        child=fork();if(child<0) return 1;
        if(child>0) { close(fd);close(server);munmap((void *)bar,4096);return 0; }
        if(setsid()<0) _exit(1);
        dup2(fd,STDOUT_FILENO);dup2(fd,STDERR_FILENO);close(fd);
        fd=open("/dev/null",O_RDONLY);if(fd>=0){dup2(fd,STDIN_FILENO);close(fd);}
        { FILE *pid=fopen(pid_path,"w");if(!pid) _exit(1);fprintf(pid,"%ld\n",(long)getpid());fclose(pid); }
    }
    clock_gettime(CLOCK_MONOTONIC,&started);
    snprintf(generation,sizeof(generation),"%ld-%lld-%ld",(long)getpid(),(long long)started.tv_sec,started.tv_nsec);
    { struct sigaction action;memset(&action,0,sizeof(action));action.sa_handler=stop_service;sigaction(SIGINT,&action,NULL);sigaction(SIGTERM,&action,NULL); }
    signal(SIGPIPE,SIG_IGN);
    printf("FPGA_KEY_SERVICE_READY 127.0.0.1:%d read_only=1 KEY5=left KEY6=straight KEY7=right legacy_keys=preserved\n",port);fflush(stdout);
    while(!stopped) {
        int client=accept(server,NULL,NULL),size;size_t sent=0;
        uint32_t event,magic,abi;
        struct timeval timeout={.tv_sec=0,.tv_usec=200000};
        if(client<0) { if(errno==EINTR)continue;break; }
        setsockopt(client,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
        magic=bar[0x170/4];abi=bar[0x17c/4];event=bar[0x174/4];
        size=snprintf(output,sizeof(output),"{\"version\":1,\"generation\":\"%s\",\"valid\":%s,\"event\":%u}\n",
                      generation,magic==0x4a4b3031 && abi==1?"true":"false",event);
        while(sent<(size_t)size) {
            ssize_t amount=send(client,output+sent,(size_t)size-sent,0);
            if(amount<=0) break;
            sent+=(size_t)amount;
        }
        close(client);
    }
    close(server);munmap((void *)bar,4096);
    if(pid_path) unlink(pid_path);
    return 0;
}
