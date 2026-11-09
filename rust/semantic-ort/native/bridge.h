#ifndef CAIRN_SEMANTIC_ORT_BRIDGE_H
#define CAIRN_SEMANTIC_ORT_BRIDGE_H
#include <stddef.h>
#include <stdint.h>

typedef struct CairnOrtRuntime CairnOrtRuntime;
typedef struct CairnOrtSession CairnOrtSession;
typedef struct { int32_t code; const char *stage; char *message; int32_t copy_failed; } CairnOrtError;
typedef struct { const char *name; int32_t kind; int32_t ort_type; size_t rank; const int64_t *dimensions; } CairnOrtNode;
typedef struct { int32_t execution_mode; int32_t memory_pattern; } CairnOrtOptions;
typedef struct { uint64_t device; uint64_t inode; uint64_t bytes; int32_t fd; } CairnOrtMapping;
typedef struct { int64_t dimensions[3]; size_t count; uint32_t *bits; } CairnOrtOutput;

void cairn_ort_error_release(CairnOrtError *error);
int cairn_ort_runtime_open(const uint8_t *bytes, size_t length, CairnOrtRuntime **out, CairnOrtMapping *mapping, CairnOrtError *error);
int cairn_ort_runtime_close(CairnOrtRuntime *runtime, CairnOrtError *error);
int cairn_ort_session_open(CairnOrtRuntime *runtime, const uint8_t *model, size_t length, CairnOrtSession **out, CairnOrtOptions *options, CairnOrtError *error);
void cairn_ort_session_close(CairnOrtSession *session);
size_t cairn_ort_node_count(const CairnOrtSession *session, int input);
int cairn_ort_node(const CairnOrtSession *session, int input, size_t index, CairnOrtNode *out, CairnOrtError *error);
int cairn_ort_run(CairnOrtSession *session, const int64_t *ids, const int64_t *mask, const int64_t *types, size_t sequence, CairnOrtOutput *out, CairnOrtError *error);
void cairn_ort_output_release(CairnOrtOutput *out);
#endif
