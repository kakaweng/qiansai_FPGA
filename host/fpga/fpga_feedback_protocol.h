#ifndef FPGA_FEEDBACK_PROTOCOL_H
#define FPGA_FEEDBACK_PROTOCOL_H
#include <stdint.h>
#include <string.h>
static int parse_command(const char *text, uint32_t *word)
{
    unsigned mode,direction,simulated,active_low;
    if(strlen(text)!=13 || strncmp(text,"LED1 ",5) || text[6]!=' ' || text[8]!=' ' ||
       text[10]!=' ' || text[12]!='\n' || text[5]<'0' || text[5]>'6' ||
       text[7]<'0' || text[7]>'3' || text[9]<'0' || text[9]>'1' || text[11]<'0' || text[11]>'1')return 0;
    mode=(unsigned)(text[5]-'0');direction=(unsigned)(text[7]-'0');
    simulated=(unsigned)(text[9]-'0');active_low=(unsigned)(text[11]-'0');
    if((mode==1 || mode==2) && !direction)return 0;
    *word=0x4c310000U | (active_low<<9) | (simulated<<8) | (direction<<4) | mode;
    return 1;
}
static int feedback_protocol_self_test(void)
{
    uint32_t word;
    return !parse_command("LED1 2 3 1 0\n",&word) || word!=0x4c310132U ||
        parse_command("LED1 2 0 0 0\n",&word) || parse_command("LED1 7 1 0 0\n",&word) ||
        parse_command("LED1 2 1 0 0\nextra",&word) || parse_command("LED1 2 1 0 0",&word) ||
        parse_command("LED1 2 1 0 2\n",&word) || parse_command("LED1 -1 1 0 0\n",&word) ||
        parse_command("LED1 4294967296 1 0 0\n",&word) || parse_command("LED1 2 1 0 0\r\n",&word);
}
#endif
