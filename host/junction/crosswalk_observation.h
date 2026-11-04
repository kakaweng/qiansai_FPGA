#ifndef CROSSWALK_OBSERVATION_H
#define CROSSWALK_OBSERVATION_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CW_PLANE_WIDTH 320U
#define CW_PLANE_HEIGHT 180U
#define CW_MASK_BYTES (CW_PLANE_WIDTH * CW_PLANE_HEIGHT / 8U)
#define CW_WIRE_HEADER 48U
#define CW_WIRE_BYTES (CW_WIRE_HEADER + CW_MASK_BYTES)
#define CW_JSON_BYTES (CW_MASK_BYTES * 2U + 640U)
#define CW_MAX_AGE_MS 1000U
#define CW_MODEL_VERSION "mapillary_lraspp_v1_20261101"

struct crosswalk_observation {
    uint32_t producer;
    uint64_t sent_ms, source_seq, source_ms;
    unsigned width, height;
    bool valid;
    unsigned char mask[CW_MASK_BYTES];
};

static inline uint64_t cw_get(const unsigned char *data, unsigned bytes)
{
    uint64_t value = 0;
    while (bytes--) value = (value << 8) | *data++;
    return value;
}

static inline void cw_put(unsigned char *data, uint64_t value, unsigned bytes)
{
    while (bytes) { data[--bytes] = (unsigned char)value; value >>= 8; }
}

static inline size_t cw_pack(const struct crosswalk_observation *value, unsigned char *data, size_t capacity)
{
    if (!value || !data || capacity < CW_WIRE_BYTES || !value->producer ||
        value->width != 1920 || value->height != 1080 || !value->source_ms ||
        value->source_ms > value->sent_ms) return 0;
    memset(data, 0, CW_WIRE_BYTES);
    memcpy(data, "FCW1", 4); cw_put(data + 4, value->producer, 4);
    cw_put(data + 8, value->sent_ms, 8); cw_put(data + 16, value->source_seq, 8);
    cw_put(data + 24, value->source_ms, 8);
    cw_put(data + 32, value->width, 2); cw_put(data + 34, value->height, 2);
    cw_put(data + 36, CW_PLANE_WIDTH, 2); cw_put(data + 38, CW_PLANE_HEIGHT, 2);
    data[40] = value->valid ? 1 : 0;
    cw_put(data + 44, CW_MASK_BYTES, 4);
    if (value->valid) memcpy(data + CW_WIRE_HEADER, value->mask, CW_MASK_BYTES);
    return CW_WIRE_BYTES;
}

static inline bool cw_unpack(struct crosswalk_observation *out, const unsigned char *data,
                             size_t size, uint64_t now, uint64_t since, uint32_t producer)
{
    struct crosswalk_observation value = {0};
    size_t index;
    if (!out || !data || size != CW_WIRE_BYTES || memcmp(data, "FCW1", 4) ||
        data[40] > 1 || data[41] || data[42] || data[43] ||
        cw_get(data + 36, 2) != CW_PLANE_WIDTH || cw_get(data + 38, 2) != CW_PLANE_HEIGHT ||
        cw_get(data + 44, 4) != CW_MASK_BYTES) return false;
    value.producer = (uint32_t)cw_get(data + 4, 4);
    value.sent_ms = cw_get(data + 8, 8); value.source_seq = cw_get(data + 16, 8);
    value.source_ms = cw_get(data + 24, 8);
    value.width = (unsigned)cw_get(data + 32, 2); value.height = (unsigned)cw_get(data + 34, 2);
    value.valid = data[40] != 0;
    if (!value.producer || (producer && value.producer != producer) ||
        value.width != 1920 || value.height != 1080 || !value.source_ms ||
        value.sent_ms > now || now - value.sent_ms > CW_MAX_AGE_MS ||
        value.source_ms > value.sent_ms || now - value.source_ms > CW_MAX_AGE_MS ||
        value.source_ms <= since) return false;
    if (out->producer == value.producer && (value.sent_ms <= out->sent_ms ||
        value.source_seq <= out->source_seq || value.source_ms <= out->source_ms)) return false;
    if (value.valid) memcpy(value.mask, data + CW_WIRE_HEADER, CW_MASK_BYTES);
    else for (index = CW_WIRE_HEADER; index < size; index++) if (data[index]) return false;
    *out = value;
    return true;
}

static inline bool cw_json(const struct crosswalk_observation *value, uint64_t now, bool running,
                           uint32_t producer, char *out, size_t capacity)
{
    static const char hex[] = "0123456789abcdef";
    bool valid = running && value->valid && value->producer &&
        (!producer || value->producer == producer) && value->sent_ms <= now && value->source_ms <= now &&
        now - value->sent_ms <= CW_MAX_AGE_MS && now - value->source_ms <= CW_MAX_AGE_MS;
    int prefix;
    size_t index, used;
    if (!out || capacity < CW_JSON_BYTES) return false;
    prefix = snprintf(out, capacity,
        "{\"version\":1,\"producer\":%u,\"source_seq\":%llu,\"source_ms\":%llu,\"sent_ms\":%llu,"
        "\"age_ms\":%lld,\"valid\":%s,\"width\":1920,\"height\":1080,\"plane_width\":320,"
        "\"plane_height\":180,\"model_version\":\"" CW_MODEL_VERSION "\",\"held\":false,\"mask_lsb_hex\":\"",
        value->producer, (unsigned long long)value->source_seq, (unsigned long long)value->source_ms,
        (unsigned long long)value->sent_ms, valid ? (long long)(now - value->source_ms) : -1LL,
        valid ? "true" : "false");
    if (prefix < 0 || (size_t)prefix + (valid ? CW_MASK_BYTES * 2U : 0U) + 3U > capacity) return false;
    used = (size_t)prefix;
    if (valid) for (index = 0; index < CW_MASK_BYTES; index++) {
        out[used++] = hex[value->mask[index] >> 4]; out[used++] = hex[value->mask[index] & 15];
    }
    out[used++] = '"'; out[used++] = '}'; out[used] = 0;
    return true;
}

#endif
