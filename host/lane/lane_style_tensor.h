#ifndef LANE_STYLE_TENSOR_H
#define LANE_STYLE_TENSOR_H

#include <stdbool.h>
#include "rknn_api.h"

static inline bool lane_tensor_floating(const rknn_tensor_attr *attribute)
{
    if (!attribute || (attribute->type != RKNN_TENSOR_FLOAT16 && attribute->type != RKNN_TENSOR_FLOAT32)) return false;
    return attribute->qnt_type == RKNN_TENSOR_QNT_NONE ||
           (attribute->qnt_type == RKNN_TENSOR_QNT_AFFINE_ASYMMETRIC && attribute->scale == 1.0f && attribute->zp == 0);
}

#endif
