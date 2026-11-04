/* Restricted LED feedback adapter. Only BAR1 0x180 can be written. */
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
#include <unistd.h>
#include "fpga_feedback_protocol.h"

static volatile sig_atomic_t stopped;
static void stop_service(int signal_number) { (void)signal_number;stopped=1; }
static int valid_bdf(const char *text)
{
    size_t i;
    if(strlen(text)!=12 || text[4]!=':' || text[7]!=':' || text[10]!='.' || text[11]<'0' || text[11]>'7')return 0;
    for(i=0;i<12;i++)if(i!=4 && i!=7 && i!=10 && !isxdigit((unsigned char)text[i]))return 0;
    return 1;
}
int main(int argc,char **argv)
{
    char path[256],input[128],output[160];int fd,server,client,inspect=0;
    volatile uint32_t *read_bar,*write_bar;
    uint32_t word,capability_magic,capability_abi;
    struct sockaddr_in address;
    if(argc==2 && !strcmp(argv[1],"--self-test")) {
        if(feedback_protocol_self_test())return 1;
        puts("FPGA_FEEDBACK_PROTOCOL_PASS");return 0;
    }
    if(argc<2 || argc>3 || !valid_bdf(argv[1]) || (argc==3 && strcmp(argv[2],"--inspect"))) {
        fprintf(stderr,"Usage: rk_fpga_feedback PCI_BDF [--inspect] | --self-test\n");return 1;
    }
    inspect=argc==3;
    snprintf(path,sizeof(path),"/sys/bus/pci/devices/%s/resource0",argv[1]);
    fd=open(path,O_RDONLY|O_SYNC);if(fd<0){perror(path);return 1;}
    read_bar=mmap(NULL,4096,PROT_READ,MAP_SHARED,fd,0);close(fd);
    if(read_bar==MAP_FAILED){perror("BAR0 read map");return 1;}
    capability_magic=read_bar[0x180/4];capability_abi=read_bar[0x18c/4];
    if(capability_magic!=0x4c463031U || capability_abi!=1) {
        fprintf(stderr,"FPGA_FEEDBACK_CAPABILITY_READ bdf=%s magic=%08x expected=4c463031 abi=%u expected_abi=1 key_magic=%08x\n",
                argv[1],capability_magic,capability_abi,read_bar[0x170/4]);
        fprintf(stderr,"FPGA_FEEDBACK_CAPABILITY_ABSENT: load drive_feedback_v1 bitstream and re-enumerate PCIe\n");return 2;
    }
    if(inspect){printf("FPGA_FEEDBACK_INSPECT_PASS magic=LF01 abi=1 command=%08x flags=%08x\n",read_bar[0x184/4],read_bar[0x188/4]);return 0;}
    snprintf(path,sizeof(path),"/sys/bus/pci/devices/%s/resource1",argv[1]);
    fd=open(path,O_RDWR|O_SYNC);if(fd<0){perror(path);return 1;}
    write_bar=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);close(fd);
    if(write_bar==MAP_FAILED){perror("BAR1 feedback map");return 1;}
    server=socket(AF_INET,SOCK_STREAM,0);if(server<0)return 1;
    {int enabled=1;setsockopt(server,SOL_SOCKET,SO_REUSEADDR,&enabled,sizeof(enabled));}
    memset(&address,0,sizeof(address));address.sin_family=AF_INET;
    address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(9015);
    if(bind(server,(struct sockaddr *)&address,sizeof(address)) || listen(server,4)){perror("feedback port");return 1;}
    {struct sigaction action;memset(&action,0,sizeof(action));action.sa_handler=stop_service;
     sigaction(SIGINT,&action,NULL);sigaction(SIGTERM,&action,NULL);}
    signal(SIGPIPE,SIG_IGN);
    setvbuf(stdout,NULL,_IOLBF,0);
    puts("FPGA_FEEDBACK_READY port=9015 register=BAR1:0x180 lease_ms=1500");
    while(!stopped) {
        size_t used=0;int length;struct timeval timeout={.tv_sec=0,.tv_usec=350000};
        client=accept(server,NULL,NULL);if(client<0){if(errno==EINTR)continue;break;}
        setsockopt(client,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
        setsockopt(client,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
        while(used<sizeof(input)-1) {
            ssize_t amount=recv(client,input+used,sizeof(input)-1-used,0);
            if(amount<=0)break;
            used+=(size_t)amount;input[used]=0;
            if(memchr(input,'\n',used))break;
        }
        input[used]=0;
        if(!memchr(input,0,used) && parse_command(input,&word) && read_bar[0x180/4]==0x4c463031U && read_bar[0x18c/4]==1) {
            unsigned flags;uint32_t accepted;
            write_bar[0x180/4]=word;
            __sync_synchronize();
            accepted=read_bar[0x184/4];flags=read_bar[0x188/4]; // read flushes PCIe posted write
            if(accepted==word && (flags&0x100))length=snprintf(output,sizeof(output),"LED1 OK %08x %08x\n",accepted,flags);
            else length=snprintf(output,sizeof(output),"LED1 ERROR ACK %08x %08x\n",accepted,flags);
        } else length=snprintf(output,sizeof(output),"LED1 ERROR INVALID\n");
        (void)send(client,output,(size_t)length,0);close(client);
    }
    /* Clear logical LEDs on normal exit; preserve polarity. FPGA lease covers crashes. */
    write_bar[0x180/4]=0x4c310000U | (read_bar[0x184/4]&0x200U);
    __sync_synchronize();
    close(server);munmap((void *)read_bar,4096);munmap((void *)write_bar,4096);
    return 0;
}
