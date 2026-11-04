#ifndef JUNCTION_DEMO_H
#define JUNCTION_DEMO_H
#include <stdbool.h>
#include <stdint.h>
struct junction_demo;
struct junction_demo *junction_demo_open(const char *runtime, const char *model, const char *contract);
void junction_demo_close(struct junction_demo *demo);
bool junction_demo_submit(struct junction_demo *demo, unsigned source, const uint8_t *pixels,
                          uint64_t sequence, uint64_t timestamp);
#endif
