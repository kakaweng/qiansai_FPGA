#define _POSIX_C_SOURCE 200809L
#include "road_object_rknn.h"
#include "rknn_scheduler.h"
#include "rknn_api.h"
#include <dlfcn.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

struct road_object_engine {
    void *library;
    rknn_context context;
    int (*init)(rknn_context *, void *, uint32_t, uint32_t, rknn_init_extend *);
    int (*destroy)(rknn_context);
    int (*query)(rknn_context, rknn_query_cmd, void *, uint32_t);
    int (*inputs_set)(rknn_context, uint32_t, rknn_input *);
    int (*run)(rknn_context, rknn_run_extend *);
    int (*outputs_get)(rknn_context, uint32_t, rknn_output *, rknn_output_extend *);
    int (*outputs_release)(rknn_context, uint32_t, rknn_output *);
    rknn_tensor_attr attributes[3];
    struct road_box workspace[ROAD_YOLO_CANDIDATES];
    uint8_t rgb[640 * 640 * 3];
    struct road_signal_history signal;
    unsigned long frames;
};
static uint64_t object_now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec * UINT64_C(1000000000) + t.tv_nsec;
}
void road_object_reset(struct road_object_engine *engine)
{ if (engine) memset(&engine->signal, 0, sizeof(engine->signal)); }
static bool api_symbol(void *library, const char *name, void *destination, size_t size)
{
    void *symbol=dlsym(library,name);
    if (!symbol || size != sizeof(symbol)) { fprintf(stderr,"Object RKNN missing symbol: %s\n",name); return false; }
    memcpy(destination,&symbol,size); return true;
}
static uint8_t *object_model(const char *path, const char *contract, uint32_t *bytes)
{
    FILE *stream=fopen(contract,"rb");
    uint64_t expected_size=0, expected_hash=0, hash=UINT64_C(14695981039346656037);
    uint8_t *data=NULL;
    size_t index;
    char magic[80], tail[80], extra;
    if (!stream) return NULL;
    if (!fgets(magic,sizeof(magic),stream) || strcmp(magic,"road_object_contract_v1\n") ||
        fscanf(stream,"bytes %" SCNu64 "\nfnv1a64 %" SCNx64 "\n",&expected_size,&expected_hash)!=2 ||
        !fgets(tail,sizeof(tail),stream) || strcmp(tail,"decoder " ROAD_OBJECT_DECODER "\n") ||
        fscanf(stream,"%c",&extra)==1 || !expected_size || expected_size>100U*1024U*1024U) goto done;
    fclose(stream); stream=fopen(path,"rb");
    if (!stream) return NULL;
    data=malloc((size_t)expected_size);
    if (!data || fread(data,1,(size_t)expected_size,stream)!=expected_size || fgetc(stream)!=EOF) { free(data); data=NULL; goto done; }
    for (index=0; index<(size_t)expected_size; index++) hash=(hash ^ data[index])*UINT64_C(1099511628211);
    if (hash!=expected_hash) { free(data); data=NULL; goto done; }
    *bytes=(uint32_t)expected_size;
done:
    fclose(stream); return data;
}
struct road_object_engine *road_object_open(const char *runtime, const char *model, const char *contract)
{
    struct road_object_engine *e=calloc(1,sizeof(*e));
    uint8_t *data;
    uint32_t bytes=0;
    rknn_input_output_num count={0};
    rknn_tensor_attr input={0};
    unsigned index, seen=0;
    if (!e) return NULL;
    data=object_model(model,contract,&bytes);
    if (!data) { fprintf(stderr,"Object model identity/contract check failed\n"); goto failed; }
    e->library=dlopen(runtime,RTLD_NOW|RTLD_LOCAL);
    if (!e->library) { fprintf(stderr,"Object runtime: %s\n",dlerror()); goto failed; }
#define LOAD(field) if (!api_symbol(e->library,"rknn_" #field,&e->field,sizeof(e->field))) goto failed
    LOAD(init); LOAD(destroy); LOAD(query); LOAD(inputs_set); LOAD(run); LOAD(outputs_get); LOAD(outputs_release);
#undef LOAD
    if (e->init(&e->context,data,bytes,0,NULL)!=RKNN_SUCC) goto failed;
    free(data); data=NULL;
    if (e->query(e->context,RKNN_QUERY_IN_OUT_NUM,&count,sizeof(count))!=RKNN_SUCC || count.n_input!=1 || count.n_output!=3 ||
        e->query(e->context,RKNN_QUERY_INPUT_ATTR,&input,sizeof(input))!=RKNN_SUCC) goto failed;
    if (input.n_dims!=4 || input.dims[0]!=1 || input.n_elems!=640U*640U*3U ||
        !((input.fmt==RKNN_TENSOR_NHWC && input.dims[1]==640 && input.dims[2]==640 && input.dims[3]==3) ||
          (input.fmt==RKNN_TENSOR_NCHW && input.dims[1]==3 && input.dims[2]==640 && input.dims[3]==640))) goto failed;
    for (index=0; index<3; index++) {
        rknn_tensor_attr *a=&e->attributes[index];
        unsigned grid, level;
        a->index=index;
        if (e->query(e->context,RKNN_QUERY_OUTPUT_ATTR,a,sizeof(*a))!=RKNN_SUCC) goto failed;
        printf("Object tensor index=%u fmt=%s type=%s qnt=%s scale=%g zp=%d shape=[%u,%u,%u,%u]\n",index,get_format_string(a->fmt),get_type_string(a->type),get_qnt_type_string(a->qnt_type),a->scale,a->zp,a->dims[0],a->dims[1],a->dims[2],a->dims[3]);
        grid=a->dims[2]; level=grid==80 ? 0 : grid==40 ? 1 : 2;
        if (a->n_dims!=4 || a->dims[0]!=1 || a->dims[1]!=255 || a->dims[3]!=grid || a->fmt!=RKNN_TENSOR_NCHW ||
            (grid!=80 && grid!=40 && grid!=20) || a->n_elems!=255U*grid*grid || (seen & (1U<<level))) goto failed;
        if ((a->type==RKNN_TENSOR_INT8 || a->type==RKNN_TENSOR_UINT8) &&
            (a->qnt_type!=RKNN_TENSOR_QNT_AFFINE_ASYMMETRIC || !isfinite(a->scale) || a->scale<=0)) goto failed;
        if (a->type!=RKNN_TENSOR_INT8 && a->type!=RKNN_TENSOR_UINT8 && a->type!=RKNN_TENSOR_FLOAT16 && a->type!=RKNN_TENSOR_FLOAT32) goto failed;
        seen |= 1U<<level;
    }
    printf("OBJECT_INSPECT_PASS decoder=%s signal=%s classes=80 input=RGB-U8-NHWC-640x640 letterbox=640x360-pad140 sigmoid=embedded\n",ROAD_OBJECT_DECODER,ROAD_SIGNAL_VERSION);
    return e;
failed:
    fprintf(stderr,"Object RKNN initialization failed; model/tensors must match existing YOLOv5s COCO80 contract\n");
    free(data); road_object_close(e); return NULL;
}
void road_object_close(struct road_object_engine *e)
{
    if (!e) return;
    if (e->context && e->destroy) e->destroy(e->context);
    if (e->library) dlclose(e->library);
    free(e);
}
static bool object_process(struct road_object_engine *e, const uint8_t *source, unsigned width, unsigned height,
                           uint64_t sequence, uint64_t timestamp, struct road_object_result *result, bool reserved)
{
    rknn_input input={0}; rknn_output outputs[3]={{0}};
    struct road_head heads[3];
    uint64_t start=object_now(), prepared, inferred, done;
    unsigned index;
    bool success;
    if (!e || !result) { if (reserved) road_npu_unlock(); return false; }
    memset(result,0,sizeof(*result));
    if (!road_object_prepare(source,width,height,e->rgb)) { if (reserved) road_npu_unlock(); return false; }
    prepared=object_now();
    input.buf=e->rgb; input.size=sizeof(e->rgb); input.type=RKNN_TENSOR_UINT8; input.fmt=RKNN_TENSOR_NHWC;
    for (index=0; index<3; index++) {
        outputs[index].index=index;
        outputs[index].want_float=e->attributes[index].type!=RKNN_TENSOR_INT8 && e->attributes[index].type!=RKNN_TENSOR_UINT8;
    }
    if (!reserved) road_npu_lock();
    success=e->inputs_set(e->context,1,&input)==RKNN_SUCC && e->run(e->context,NULL)==RKNN_SUCC && e->outputs_get(e->context,3,outputs,NULL)==RKNN_SUCC;
    road_npu_unlock(); inferred=object_now();
    if (!success) { if(width==1920) memset(&e->signal,0,sizeof(e->signal)); return false; }
    for (index=0; index<3; index++) heads[index]=(struct road_head){outputs[index].buf,outputs[index].size,e->attributes[index].dims[2],
        outputs[index].want_float ? ROAD_F32 : e->attributes[index].type==RKNN_TENSOR_INT8 ? ROAD_I8 : ROAD_U8,e->attributes[index].scale,e->attributes[index].zp};
    success=road_object_decode_sized(heads,e->workspace,result,width,height);
    if (e->outputs_release(e->context,3,outputs)!=RKNN_SUCC) success=false;
    if (!success) { if(width==1920) memset(&e->signal,0,sizeof(e->signal)); memset(result,0,sizeof(*result)); return false; }
    result->sequence=sequence; result->timestamp_ns=timestamp;
    road_signal_update(&e->signal,source,width,height,timestamp,result);
#ifndef HAVE_JUNCTION_DEMO
    if (width==640 && height==480) road_object_vehicles_only(result);
#endif
    done=object_now(); e->frames++;
    if (e->frames<=3 || e->frames%10==0) printf("objects source_size=%ux%u source_seq=%" PRIu64 " prepare_ms=%.2f inference_wait_io_ms=%.2f decode_color_ms=%.2f total_ms=%.2f boxes=%u vehicles=%u people=%u lights=%u signal=%s raw=%s confirm=%u conflict=%u\n",width,height,sequence,
        (prepared-start)/1e6,(inferred-prepared)/1e6,(done-inferred)/1e6,(done-start)/1e6,result->count,result->vehicles,result->people,result->lamps,road_signal_name(result->signal),road_signal_name(result->raw_signal),result->signal_confirmations,result->signal_conflict);
    return true;
}
bool road_object_process(struct road_object_engine *e, const uint8_t *source, unsigned width, unsigned height,
                          uint64_t sequence, uint64_t timestamp, struct road_object_result *result)
{ return object_process(e,source,width,height,sequence,timestamp,result,false); }
bool road_object_process_reserved(struct road_object_engine *e, const uint8_t *source, unsigned width, unsigned height,
                                   uint64_t sequence, uint64_t timestamp, struct road_object_result *result)
{ return object_process(e,source,width,height,sequence,timestamp,result,true); }
