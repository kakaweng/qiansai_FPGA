#ifndef SYNC_RECEIVER_CORE_H
#define SYNC_RECEIVER_CORE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define SYNC_SLOTS 6
struct sync_mark { unsigned role, phase; uint32_t session, wire; uint64_t scene_ms; uint32_t index; };
struct sync_slot { struct sync_mark mark; uint64_t received_ms; uint8_t *pixels; bool valid; };
struct sync_receiver {
    struct sync_slot frames[2][SYNC_SLOTS];
    unsigned next[2];
    uint32_t session, last_wire;
    bool published;
};
struct sync_pair { struct sync_mark front, right; uint64_t age_ms; const uint8_t *pixels[2]; };
bool sync_read_mark(const uint8_t *pixels, unsigned width, unsigned height, struct sync_mark *out);
void sync_receiver_reset(struct sync_receiver *receiver, uint32_t session);
void sync_receiver_close(struct sync_receiver *receiver);
bool sync_receiver_push(struct sync_receiver *receiver, const uint8_t *pixels, unsigned role,
                        uint64_t now_ms, struct sync_pair *pair);
#endif
