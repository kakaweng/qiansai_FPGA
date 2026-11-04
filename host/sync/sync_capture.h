#ifndef SYNC_CAPTURE_H
#define SYNC_CAPTURE_H
#include "sync_receiver_core.h"
#include "sync_receiver_core.h"
struct sync_capture;
struct sync_capture *sync_capture_open(const char *state_file);
void sync_capture_close(struct sync_capture *capture);
bool sync_capture_update(struct sync_capture *capture, const uint8_t *front, const uint8_t *right);
bool sync_capture_source(struct sync_capture *capture, unsigned role, struct sync_mark *mark);
void sync_capture_report(struct sync_capture *capture, const char *report);
uint32_t sync_capture_session(const struct sync_capture *capture);
#endif
