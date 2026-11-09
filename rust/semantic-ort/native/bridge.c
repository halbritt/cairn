#define _GNU_SOURCE
#include "bridge.h"
#include "onnxruntime_c_api.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <float.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

_Static_assert(ORT_API_VERSION == 29, "selected API29 header");
_Static_assert(sizeof(void *) == 8 && sizeof(size_t) == 8 && sizeof(int) == 4, "selected x86_64 GNU ABI");
_Static_assert(sizeof(OrtErrorCode) == 4 && sizeof(ONNXTensorElementDataType) == 4 && sizeof(ExecutionMode) == 4, "ORT enum ABI; reject short-enum compiler settings");
_Static_assert(sizeof(CairnOrtError) == 32 && offsetof(CairnOrtError,stage) == 8, "bridge error ABI");
_Static_assert(sizeof(CairnOrtNode) == 32 && sizeof(CairnOrtMapping) == 32 && sizeof(CairnOrtOptions) == 8 && sizeof(CairnOrtOutput) == 48, "bridge record ABI");
_Static_assert(sizeof(CairnOrtBytes)==16 && sizeof(CairnOrtConfigEntry)==32 && sizeof(CairnOrtConfigView)==24, "config observation aggregate ABI");
_Static_assert(sizeof(CairnOrtMemoryObservation)==48 && sizeof(CairnOrtEpObservation)==40 && sizeof(CairnOrtIoObservation)==112, "IO observation aggregate ABI");
_Static_assert(sizeof(OrtAllocatorType)==4 && sizeof(OrtMemType)==4 && sizeof(OrtDeviceMemoryType)==4 && sizeof(OrtMemoryInfoDeviceType)==4, "memory observation enum ABI");
_Static_assert(sizeof(float) == 4 && FLT_MANT_DIG == 24 && FLT_MAX_EXP == 128, "IEEE binary32 required");
struct CairnOrtRuntime { void *library; int fd; const OrtApi *api; OrtEnv *env; };
typedef struct { char *name; int32_t kind; int32_t ort_type; size_t rank; int64_t *dimensions; CairnOrtIoObservation observation; } OwnedNode;
struct CairnOrtSession { CairnOrtRuntime *runtime; OrtSession *session; OwnedNode *inputs; OwnedNode *outputs; size_t input_count; size_t output_count; CairnOrtConfigEntry *config_entries; size_t config_count; int config_observed; };

static int error_add(CairnOrtError *e, int code, const char *stage, const char *message) {
    if (!e->stage) { e->stage = stage; e->code = code; }
    size_t old = e->message ? strlen(e->message) : 0;
    char code_text[24];
    int code_length=snprintf(code_text,sizeof(code_text),"[%d] ",code);
    if (code_length < 0 || (size_t)code_length >= sizeof(code_text)) { e->copy_failed=1; return -1; }
    size_t a = strlen(stage), b = strlen(message), c = (size_t)code_length;
    if (old > SIZE_MAX-a-4 || old+a+4 > SIZE_MAX-b-1 || old+a+b+5 > SIZE_MAX-c) { e->copy_failed = 1; return -1; }
    char *next = malloc(old+a+b+c+5);
    if (!next) { e->copy_failed = 1; return -1; }
    if (old) { memcpy(next,e->message,old); memcpy(next+old,"; ",2); old += 2; }
    memcpy(next+old,stage,a); old += a; memcpy(next+old,": ",2); old += 2;
    memcpy(next+old,code_text,c); old += c;
    memcpy(next+old,message,b+1); free(e->message); e->message = next;
    return -1;
}
static int system_error(CairnOrtError *error, const char *stage) {
    int saved_errno=errno;
    return error_add(error,saved_errno,stage,strerror(saved_errno));
}
void cairn_ort_error_release(CairnOrtError *e) { free(e->message); memset(e,0,sizeof(*e)); }
static int ort_status(const OrtApi *api, const char *stage, OrtStatus *status, CairnOrtError *error) {
    if (!status) return 0;
    int result = error_add(error,(int)api->GetErrorCode(status),stage,api->GetErrorMessage(status));
    api->ReleaseStatus(status);
    return result;
}
#define CALL(api, expression) do { if (ort_status((api),#expression,(expression),error)) goto done; } while (0)

/* A dladdr pathname alone can refer to a reused /proc/self/fd/N spelling.
 * Check the actual kernel mapping of the resolved symbol against the sealed fd. */
static int mapped_to_fd(void *symbol, int fd, CairnOrtError *error) {
    struct stat st;
    if (fstat(fd,&st)) return system_error(error,"fstat sealed runtime");
    FILE *maps = fopen("/proc/self/maps","re");
    if (!maps) return system_error(error,"open runtime maps");
    char *line = NULL; size_t capacity = 0; int found = 0; int result = 0;
    uintptr_t address = (uintptr_t)symbol;
    ssize_t line_length;
    errno=0;
    while ((line_length=getline(&line,&capacity,maps)) >= 0) {
        uintptr_t begin,end; uint64_t offset,inode; unsigned dev_major,dev_minor; char permissions[5];
        if (sscanf(line,"%" SCNxPTR "-%" SCNxPTR " %4s %" SCNx64 " %x:%x %" SCNu64,
                   &begin,&end,permissions,&offset,&dev_major,&dev_minor,&inode) == 7 && address >= begin && address < end) {
            found = dev_major == major(st.st_dev) && dev_minor == minor(st.st_dev) && inode == (uint64_t)st.st_ino;
            break;
        }
    }
    if (line_length < 0 && !feof(maps)) result = system_error(error,"read runtime maps");
    free(line);
    if (fclose(maps)) result = system_error(error,"close runtime maps");
    if (!found) result = error_add(error,-1,"runtime mapping","resolved ORT address is not mapped from the sealed selected bytes");
    return result;
}

int cairn_ort_runtime_close(CairnOrtRuntime *r, CairnOrtError *error) {
    if (!r) return 0;
    int result = 0;
    if (r->env) r->api->ReleaseEnv(r->env);
    if (r->library && dlclose(r->library)) { const char *why=dlerror(); result=error_add(error,-1,"dlclose",why ? why : "loader returned failure without detail"); }
    if (r->fd >= 0 && close(r->fd)) result=system_error(error,"close sealed runtime");
    free(r); return result;
}

int cairn_ort_runtime_open(const uint8_t *bytes, size_t length, CairnOrtRuntime **out, CairnOrtMapping *mapping, CairnOrtError *error) {
    int result = -1; CairnOrtRuntime *r = calloc(1,sizeof(*r));
    *out = NULL;
    if (!r) return error_add(error,ENOMEM,"runtime allocation","out of memory");
    r->fd = -1;
    const char *telemetry = getenv("ORT_DISABLE_TELEMETRY");
    if (!telemetry || strcmp(telemetry,"1") || getenv("LD_LIBRARY_PATH") || getenv("LD_PRELOAD") || getenv("LD_AUDIT")) {
        error_add(error,-1,"launcher environment","requires ORT_DISABLE_TELEMETRY=1 and absent LD_LIBRARY_PATH/LD_PRELOAD/LD_AUDIT before runtime initialization"); goto done;
    }
    r->fd = memfd_create("cairn-ort-1.29.0",MFD_CLOEXEC|MFD_ALLOW_SEALING);
    if (r->fd < 0) { system_error(error,"memfd_create"); goto done; }
    size_t offset = 0;
    while (offset < length) {
        ssize_t n = write(r->fd,bytes+offset,length-offset);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { system_error(error,"write sealed runtime"); goto done; }
        if (n == 0) { error_add(error,-1,"write sealed runtime","zero-length write"); goto done; }
        offset += (size_t)n;
    }
    if (fcntl(r->fd,F_ADD_SEALS,F_SEAL_WRITE|F_SEAL_GROW|F_SEAL_SHRINK|F_SEAL_SEAL)) { system_error(error,"seal runtime bytes"); goto done; }
    char path[64]; int path_length = snprintf(path,sizeof(path),"/proc/self/fd/%d",r->fd);
    if (path_length < 0 || (size_t)path_length >= sizeof(path)) { error_add(error,-1,"runtime fd path","path overflow"); goto done; }
    dlerror(); r->library = dlopen(path,RTLD_NOW|RTLD_LOCAL);
    if (!r->library) { const char *why=dlerror(); error_add(error,-1,"dlopen selected runtime",why ? why : "no loader detail"); goto done; }
    dlerror(); void *symbol = dlsym(r->library,"OrtGetApiBase"); const char *lookup_error = dlerror();
    if (lookup_error || !symbol) { error_add(error,-1,"dlsym OrtGetApiBase",lookup_error ? lookup_error : "null symbol"); goto done; }
    if (mapped_to_fd(symbol,r->fd,error)) goto done;
    typedef const OrtApiBase *(ORT_API_CALL *GetApiBaseFn)(void);
    GetApiBaseFn get_base;
    _Static_assert(sizeof(get_base)==sizeof(symbol),"POSIX function pointer representation");
    memcpy(&get_base,&symbol,sizeof(get_base));
    const OrtApiBase *base = get_base();
    if (!base || !base->GetVersionString || !base->GetApi) { error_add(error,-1,"OrtApiBase","missing API base functions"); goto done; }
    const char *version = base->GetVersionString();
    if (!version || strcmp(version,"1.29.0")) { error_add(error,-1,"ORT version",version ? version : "null runtime version"); goto done; }
    r->api = base->GetApi(ORT_API_VERSION);
    if (!r->api) { error_add(error,-1,"GetApi(29)","selected runtime refused API29"); goto done; }
    CALL(r->api,r->api->CreateEnv(ORT_LOGGING_LEVEL_WARNING,"cairn-semantic-ort",&r->env));
    CALL(r->api,r->api->DisableTelemetryEvents(r->env));
    struct stat st;
    if (fstat(r->fd,&st)) { system_error(error,"final sealed runtime stat"); goto done; }
    mapping->device=(uint64_t)st.st_dev; mapping->inode=(uint64_t)st.st_ino; mapping->bytes=(uint64_t)st.st_size; mapping->fd=r->fd;
    *out=r; r=NULL; result=0;
done:
    if (r) cairn_ort_runtime_close(r,error);
    return result;
}

static void bytes_free(CairnOrtBytes *bytes) { free((void *)bytes->data); memset(bytes,0,sizeof(*bytes)); }
static void node_free(OwnedNode *node) {
    free(node->name); free(node->dimensions);
    bytes_free(&node->observation.node_name);
    bytes_free(&node->observation.memory.name);
    bytes_free(&node->observation.ep.name); bytes_free(&node->observation.ep.vendor);
}
void cairn_ort_session_close(CairnOrtSession *s) {
    if (!s) return;
    if (s->session) s->runtime->api->ReleaseSession(s->session);
    if (s->inputs) for (size_t i=0;i<s->input_count;i++) node_free(&s->inputs[i]);
    if (s->outputs) for (size_t i=0;i<s->output_count;i++) node_free(&s->outputs[i]);
    if (s->config_entries) for (size_t i=0;i<s->config_count;i++) {
        bytes_free(&s->config_entries[i].key); bytes_free(&s->config_entries[i].value);
    }
    free(s->config_entries); free(s->inputs); free(s->outputs); free(s);
}

static int read_node(CairnOrtSession *s, int input, size_t index, OrtAllocator *allocator, OwnedNode *out, CairnOrtError *error) {
    const OrtApi *api=s->runtime->api; OrtTypeInfo *type=NULL;
    const OrtTensorTypeAndShapeInfo *shape=NULL; char *name=NULL;
    ONNXTensorElementDataType element_type; int result=-1;
    if (input) {
        CALL(api,api->SessionGetInputName(s->session,index,allocator,&name));
        CALL(api,api->SessionGetInputTypeInfo(s->session,index,&type));
    } else {
        CALL(api,api->SessionGetOutputName(s->session,index,allocator,&name));
        CALL(api,api->SessionGetOutputTypeInfo(s->session,index,&type));
    }
    if (!name) { error_add(error,-1,"session node name","ORT returned no name"); goto done; }
    out->name=strdup(name);
    if (!out->name) { error_add(error,ENOMEM,"copy session node name","out of memory"); goto done; }
    enum ONNXType onnx_type;
    CALL(api,api->GetOnnxTypeFromTypeInfo(type,&onnx_type));
    if (onnx_type!=ONNX_TYPE_TENSOR) {
        if (input || index==0) { error_add(error,-1,"session metadata","first output/inputs must be tensors"); goto done; }
        out->kind=0; out->ort_type=-(int32_t)onnx_type; out->rank=0;
        result=0; goto done;
    }
    CALL(api,api->CastTypeInfoToTensorInfo(type,&shape));
    if (!shape) { error_add(error,-1,"session metadata","tensor type has no shape"); goto done; }
    CALL(api,api->GetTensorElementType(shape,&element_type));
    out->ort_type=(int32_t)element_type;
    out->kind=element_type==ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ? 1 : element_type==ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64 ? 2 : 0;
    CALL(api,api->GetDimensionsCount(shape,&out->rank));
    if (out->rank > 16) { error_add(error,-1,"session metadata","node rank exceeds supported model boundary16"); goto done; }
    if (out->rank) {
        out->dimensions=calloc(out->rank,sizeof(int64_t));
        if (!out->dimensions) { error_add(error,ENOMEM,"session dimensions","out of memory"); goto done; }
        CALL(api,api->GetDimensions(shape,out->dimensions,out->rank));
    }
    result=0;
done:
    if (type) api->ReleaseTypeInfo(type);
    if (name && ort_status(api,"AllocatorFree node name",api->AllocatorFree(allocator,name),error)) result=-1;
    return result;
}

/* Public string getters return NUL-terminated bytes. PTRDIFF_MAX bounds
 * addressable C objects and Rust slices; it is not an invented ORT text limit. */
static int copy_observed_text(const char *text, CairnOrtBytes *out, CairnOrtError *error) {
    if (!text) return error_add(error,-1,"observation string","required string pointer is null");
    size_t length=strnlen(text,(size_t)PTRDIFF_MAX);
    if (length==(size_t)PTRDIFF_MAX) return error_add(error,-1,"observation string","string exceeds addressable object bound");
    uint8_t *owned=malloc(length+1);
    if (!owned) return error_add(error,ENOMEM,"observation string allocation","out of memory");
    memcpy(owned,text,length+1); out->data=owned; out->length=length; return 0;
}

static int collect_config_entries(CairnOrtSession *s, const OrtSessionOptions *options, CairnOrtError *error) {
    const OrtApi *api=s->runtime->api; OrtKeyValuePairs *pairs=NULL; int result=-1;
    const char *const *keys=NULL, *const *values=NULL; size_t count=0;
    CALL(api,api->GetSessionOptionsConfigEntries(options,&pairs));
    if (!pairs) { error_add(error,-1,"options config entries","getter returned no owned object"); goto done; }
    api->GetKeyValuePairs(pairs,&keys,&values,&count);
    if (count>(size_t)PTRDIFF_MAX/sizeof(CairnOrtConfigEntry) || (count && (!keys || !values))) {
        error_add(error,-1,"options config entries","invalid array pointer/count or allocation bound"); goto done;
    }
    if (count) {
        s->config_entries=calloc(count,sizeof(CairnOrtConfigEntry));
        if (!s->config_entries) { error_add(error,ENOMEM,"config observation allocation","out of memory"); goto done; }
    }
    s->config_count=count;
    for (size_t i=0;i<count;i++) {
        if (copy_observed_text(keys[i],&s->config_entries[i].key,error) ||
            copy_observed_text(values[i],&s->config_entries[i].value,error)) goto done;
    }
    s->config_observed=1; result=0;
done:
    if (pairs) api->ReleaseKeyValuePairs(pairs);
    return result;
}

static int copy_memory_observation(const OrtApi *api, const OrtMemoryInfo *memory, CairnOrtMemoryObservation *out, CairnOrtError *error) {
    if (!memory) return 0; /* Preserve missing evidence, never substitute CPU. */
    const char *name=NULL; int device_id; OrtAllocatorType allocator; OrtMemType memory_type;
    OrtMemoryInfoDeviceType device; int result=-1;
    CALL(api,api->MemoryInfoGetName(memory,&name));
    CALL(api,api->MemoryInfoGetId(memory,&device_id));
    CALL(api,api->MemoryInfoGetType(memory,&allocator));
    CALL(api,api->MemoryInfoGetMemType(memory,&memory_type));
    api->MemoryInfoGetDeviceType(memory,&device);
    if (copy_observed_text(name,&out->name,error)) goto done;
    out->device_type=(int32_t)device; out->device_id=device_id;
    out->allocator_type=(int32_t)allocator; out->memory_type=(int32_t)memory_type;
    out->device_memory_type=(int32_t)api->MemoryInfoGetDeviceMemType(memory);
    out->vendor_id=api->MemoryInfoGetVendorId(memory); out->present=1; result=0;
done:
    return result;
}

static int copy_ep_observation(const OrtApi *api, const OrtEpDevice *device, CairnOrtEpObservation *out, CairnOrtError *error) {
    if (!device) return 0; /* API29 legacy/non-V2 sessions may have NULL entries. */
    const char *name=api->EpDevice_EpName(device), *vendor=api->EpDevice_EpVendor(device);
    /* Keep NULL text distinct from observed empty text. No EP object is retained. */
    if (name && copy_observed_text(name,&out->name,error)) return -1;
    if (vendor && copy_observed_text(vendor,&out->vendor,error)) return -1;
    out->present=1; return 0;
}

static int collect_io_observations(CairnOrtSession *s, int input, CairnOrtError *error) {
    const OrtApi *api=s->runtime->api; int result=-1;
    size_t count=input ? s->input_count : s->output_count;
    OwnedNode *nodes=input ? s->inputs : s->outputs;
    const OrtMemoryInfo *memory[64]={NULL}; const OrtEpDevice *devices[64]={NULL};
    if (!count || count>64) return error_add(error,-1,"IO observation count","outside selected session node bound");
    if (input) {
        CALL(api,api->SessionGetMemoryInfoForInputs(s->session,memory,count));
        CALL(api,api->SessionGetEpDeviceForInputs(s->session,devices,count));
    } else {
        CALL(api,api->SessionGetMemoryInfoForOutputs(s->session,memory,count));
        CALL(api,api->SessionGetEpDeviceForOutputs(s->session,devices,count));
    }
    for (size_t i=0;i<count;i++) {
        CairnOrtIoObservation *out=&nodes[i].observation; out->ordinal=i;
        if (copy_observed_text(nodes[i].name,&out->node_name,error) ||
            copy_memory_observation(api,memory[i],&out->memory,error) ||
            copy_ep_observation(api,devices[i],&out->ep,error)) goto done;
    }
    result=0;
done:
    /* MemoryInfo/EpDevice pointers borrow the live session; only our copies free. */
    return result;
}

int cairn_ort_config_observation(const CairnOrtSession *s, CairnOrtConfigView *out, CairnOrtError *error) {
    if (!s->config_observed) return error_add(error,-1,"config observation","not collected");
    memset(out,0,sizeof(*out)); out->observed=s->config_observed;
    out->count=s->config_count; out->entries=s->config_entries; return 0;
}
int cairn_ort_io_observation(const CairnOrtSession *s, int input, size_t index, CairnOrtIoObservation *out, CairnOrtError *error) {
    size_t count=input ? s->input_count : s->output_count;
    if (index>=count) return error_add(error,-1,"IO observation index","outside owning metadata count");
    *out=(input ? s->inputs[index] : s->outputs[index]).observation; return 0;
}

int cairn_ort_session_open(CairnOrtRuntime *runtime, const uint8_t *model, size_t length, CairnOrtSession **out, CairnOrtOptions *observed, CairnOrtError *error) {
    const OrtApi *api=runtime->api; int result=-1; *out=NULL;
    OrtSessionOptions *options=NULL; OrtAllocator *allocator=NULL;
    ExecutionMode execution; int pattern;
    CairnOrtSession *s=calloc(1,sizeof(*s));
    if (!s) return error_add(error,ENOMEM,"session allocation","out of memory");
    s->runtime=runtime;
    CALL(api,api->CreateSessionOptions(&options));
    /* FastEmbed common/onnx_model.py104-112 explicitly supplies these three values. */
    CALL(api,api->SetSessionGraphOptimizationLevel(options,ORT_ENABLE_ALL));
    CALL(api,api->SetIntraOpNumThreads(options,2));
    CALL(api,api->SetInterOpNumThreads(options,2));
    /* API29 header5195 documents false as the native deterministic-compute default. */
    CALL(api,api->SetDeterministicCompute(options,false));
    CALL(api,api->DisableProfiling(options));
    /* Preserve the native defaults Python leaves untouched, and read the available getters. */
    CALL(api,api->GetSessionExecutionMode(options,&execution));
    CALL(api,api->GetMemPatternEnabled(options,&pattern));
    observed->execution_mode=(int32_t)execution; observed->memory_pattern=pattern;
    if (collect_config_entries(s,options,error)) goto done;
    /* No EP is appended: API29 header1440-1449 selects the internal CPU EP. */
    CALL(api,api->CreateSessionFromArray(runtime->env,model,length,options,&s->session));
    CALL(api,api->GetAllocatorWithDefaultOptions(&allocator));
    CALL(api,api->SessionGetInputCount(s->session,&s->input_count));
    CALL(api,api->SessionGetOutputCount(s->session,&s->output_count));
    if (s->input_count==0 || s->input_count>3 || s->output_count==0 || s->output_count>64) {
        error_add(error,-1,"model node counts","selected batch-one BGE model supports1..3 inputs and1..64 outputs"); goto done;
    }
    s->inputs=calloc(s->input_count,sizeof(OwnedNode)); s->outputs=calloc(s->output_count,sizeof(OwnedNode));
    if (!s->inputs || !s->outputs) { error_add(error,ENOMEM,"session node arrays","out of memory"); goto done; }
    for (size_t i=0;i<s->input_count;i++) if (read_node(s,1,i,allocator,&s->inputs[i],error)) goto done;
    for (size_t i=0;i<s->output_count;i++) if (read_node(s,0,i,allocator,&s->outputs[i],error)) goto done;
    if (collect_io_observations(s,1,error) || collect_io_observations(s,0,error)) goto done;
    *out=s; s=NULL; result=0;
done:
    if (options) api->ReleaseSessionOptions(options);
    if (s) cairn_ort_session_close(s);
    return result;
}

size_t cairn_ort_node_count(const CairnOrtSession *s, int input) { return input ? s->input_count : s->output_count; }
int cairn_ort_node(const CairnOrtSession *s, int input, size_t index, CairnOrtNode *out, CairnOrtError *error) {
    size_t count=input ? s->input_count : s->output_count;
    if (index>=count) return error_add(error,-1,"session metadata index","out of bounds");
    const OwnedNode *node=input ? &s->inputs[index] : &s->outputs[index];
    out->name=node->name; out->kind=node->kind; out->ort_type=node->ort_type;
    out->rank=node->rank; out->dimensions=node->dimensions;
    return 0;
}

void cairn_ort_output_release(CairnOrtOutput *out) { free(out->bits); memset(out,0,sizeof(*out)); }
int cairn_ort_run(CairnOrtSession *s, const int64_t *ids, const int64_t *mask, const int64_t *types, size_t sequence, CairnOrtOutput *out, CairnOrtError *error) {
    const OrtApi *api=s->runtime->api; int result=-1;
    OrtMemoryInfo *memory=NULL; OrtValue *inputs[3]={NULL,NULL,NULL}; OrtValue *outputs[64]={NULL}; OrtValue *output=NULL;
    OrtTensorTypeAndShapeInfo *shape=NULL; const OrtMemoryInfo *output_memory=NULL;
    const char *names[3]; const char *output_names[64];
    const int64_t dimensions[2]={1,(int64_t)sequence};
    ONNXTensorElementDataType element_type; size_t rank,count; int is_tensor;
    OrtMemoryInfoDeviceType device_type; void *data=NULL;
    memset(out,0,sizeof(*out));
    if (!sequence || sequence>512 || s->input_count>3 || !s->output_count || s->output_count>64) { error_add(error,-1,"batch shape","expected batch1 and1..512 tokens"); goto done; }
    CALL(api,api->CreateCpuMemoryInfo(OrtArenaAllocator,OrtMemTypeDefault,&memory));
    for (size_t i=0;i<s->input_count;i++) {
        const int64_t *values=NULL; names[i]=s->inputs[i].name;
        if (!strcmp(names[i],"input_ids")) values=ids;
        else if (!strcmp(names[i],"attention_mask")) values=mask;
        else if (!strcmp(names[i],"token_type_ids")) values=types;
        else { error_add(error,-1,"input name","unexpected input in owning session"); goto done; }
        CALL(api,api->CreateTensorWithDataAsOrtValue(memory,(void *)values,sequence*sizeof(int64_t),dimensions,2,ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64,&inputs[i]));
    }
    /* All Rust-owned input storage stays alive through this synchronous Run. */
    /* Source: run(None) requests ALL outputs in SessionGetOutputName order,
     * then FastEmbed takes returned[0]. Keep auxiliaries alive through Run. */
    for (size_t i=0;i<s->output_count;i++) output_names[i]=s->outputs[i].name;
    CALL(api,api->Run(s->session,NULL,names,(const OrtValue *const *)inputs,s->input_count,output_names,s->output_count,outputs));
    output=outputs[0];
    CALL(api,api->IsTensor(output,&is_tensor));
    if (!is_tensor) { error_add(error,-1,"first model output","output is not a tensor"); goto done; }
    CALL(api,api->GetTensorTypeAndShape(output,&shape));
    CALL(api,api->GetTensorElementType(shape,&element_type));
    CALL(api,api->GetDimensionsCount(shape,&rank));
    if (element_type!=ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT || (rank!=2 && rank!=3)) { error_add(error,-1,"first model output","expected rank2/3 float32"); goto done; }
    out->rank=rank;
    CALL(api,api->GetDimensions(shape,out->dimensions,rank));
    CALL(api,api->GetTensorShapeElementCount(shape,&count));
    if (out->dimensions[0]!=1 ||
        (rank==3 && (out->dimensions[1]!=(int64_t)sequence || out->dimensions[2]!=384 || count!=sequence*384)) ||
        (rank==2 && (out->dimensions[1]!=384 || count!=384))) {
        error_add(error,-1,"first model output","shape/count must equal[1,sequence,384] or[1,384]"); goto done;
    }
    CALL(api,api->GetTensorMemoryInfo(output,&output_memory));
    api->MemoryInfoGetDeviceType(output_memory,&device_type);
    if (device_type!=OrtMemoryInfoDeviceType_CPU) { error_add(error,-1,"first model output","output is not CPU-addressable"); goto done; }
    CALL(api,api->GetTensorMutableData(output,&data));
    if (!data) { error_add(error,-1,"first model output","null tensor storage"); goto done; }
    out->bits=malloc(count*sizeof(uint32_t));
    if (!out->bits) { error_add(error,ENOMEM,"copy output bits","out of memory"); goto done; }
    memcpy(out->bits,data,count*sizeof(uint32_t)); out->count=count; result=0;
done:
    if (shape) api->ReleaseTensorTypeAndShapeInfo(shape);
    for (size_t i=0;i<64;i++) if (outputs[i]) api->ReleaseValue(outputs[i]);
    for (size_t i=0;i<3;i++) if (inputs[i]) api->ReleaseValue(inputs[i]);
    if (memory) api->ReleaseMemoryInfo(memory);
    if (result) cairn_ort_output_release(out);
    return result;
}
