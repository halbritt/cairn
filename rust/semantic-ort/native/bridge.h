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
typedef struct { size_t rank; int64_t dimensions[3]; size_t count; uint32_t *bits; } CairnOrtOutput;

_Static_assert(sizeof(CairnOrtOutput)==48, "x86_64 Source output ABI size");

/* Borrowed views into bridge-owned copies. No ORT object crosses this ABI. */
typedef struct { const uint8_t *data; size_t length; } CairnOrtBytes;
typedef struct { CairnOrtBytes key; CairnOrtBytes value; } CairnOrtConfigEntry;
typedef struct { int32_t observed; int32_t reserved; size_t count; const CairnOrtConfigEntry *entries; } CairnOrtConfigView;
typedef struct {
    int32_t present, device_type, device_id, allocator_type;
    int32_t memory_type, device_memory_type; uint32_t vendor_id, reserved;
    CairnOrtBytes name;
} CairnOrtMemoryObservation;
typedef struct { int32_t present, reserved; CairnOrtBytes name, vendor; } CairnOrtEpObservation;
typedef struct { size_t ordinal; CairnOrtBytes node_name; CairnOrtMemoryObservation memory; CairnOrtEpObservation ep; } CairnOrtIoObservation;
typedef struct { CairnOrtBytes name, domain, operator_type; } CairnOrtAssignedNode;
typedef struct { CairnOrtBytes provider; size_t node_count; const CairnOrtAssignedNode *nodes; } CairnOrtAssignedSubgraph;
typedef struct { int32_t observed, reserved; size_t count; const CairnOrtAssignedSubgraph *subgraphs; } CairnOrtAssignmentView;
_Static_assert(sizeof(CairnOrtAssignedNode)==48 && sizeof(CairnOrtAssignedSubgraph)==32 && sizeof(CairnOrtAssignmentView)==24, "assignment observation ABI");
_Static_assert(offsetof(CairnOrtAssignedNode,name)==0 && offsetof(CairnOrtAssignedNode,domain)==16 && offsetof(CairnOrtAssignedNode,operator_type)==32, "assignment node offsets");
_Static_assert(offsetof(CairnOrtAssignedSubgraph,provider)==0 && offsetof(CairnOrtAssignedSubgraph,node_count)==16 && offsetof(CairnOrtAssignedSubgraph,nodes)==24, "assignment subgraph offsets");
_Static_assert(offsetof(CairnOrtAssignmentView,observed)==0 && offsetof(CairnOrtAssignmentView,reserved)==4 && offsetof(CairnOrtAssignmentView,count)==8 && offsetof(CairnOrtAssignmentView,subgraphs)==16, "assignment view offsets");
_Static_assert(sizeof(CairnOrtBytes)==16 && sizeof(CairnOrtConfigEntry)==32 && sizeof(CairnOrtConfigView)==24, "x86_64 config observation ABI");
_Static_assert(sizeof(CairnOrtMemoryObservation)==48 && sizeof(CairnOrtEpObservation)==40 && sizeof(CairnOrtIoObservation)==112, "x86_64 IO observation ABI");
_Static_assert(offsetof(CairnOrtConfigView,count)==8 && offsetof(CairnOrtConfigView,entries)==16, "config observation field offsets");
_Static_assert(offsetof(CairnOrtMemoryObservation,name)==32 && offsetof(CairnOrtEpObservation,name)==8 && offsetof(CairnOrtEpObservation,vendor)==24, "memory/EP byte field offsets");
_Static_assert(offsetof(CairnOrtIoObservation,node_name)==8 && offsetof(CairnOrtIoObservation,memory)==24 && offsetof(CairnOrtIoObservation,ep)==72, "IO association field offsets");
_Static_assert(offsetof(CairnOrtConfigView,observed)==0 && offsetof(CairnOrtConfigView,reserved)==4, "config presence offsets");
_Static_assert(offsetof(CairnOrtMemoryObservation,present)==0 && offsetof(CairnOrtMemoryObservation,device_type)==4 && offsetof(CairnOrtMemoryObservation,device_id)==8 && offsetof(CairnOrtMemoryObservation,allocator_type)==12, "memory device offsets");
_Static_assert(offsetof(CairnOrtMemoryObservation,memory_type)==16 && offsetof(CairnOrtMemoryObservation,device_memory_type)==20 && offsetof(CairnOrtMemoryObservation,vendor_id)==24 && offsetof(CairnOrtMemoryObservation,reserved)==28, "memory type offsets");
_Static_assert(offsetof(CairnOrtEpObservation,present)==0 && offsetof(CairnOrtEpObservation,reserved)==4, "EP presence offsets");


void cairn_ort_error_release(CairnOrtError *error);
int cairn_ort_runtime_open(const uint8_t *bytes, size_t length, CairnOrtRuntime **out, CairnOrtMapping *mapping, CairnOrtError *error);
int cairn_ort_runtime_close(CairnOrtRuntime *runtime, CairnOrtError *error);
int cairn_ort_session_open(CairnOrtRuntime *runtime, const uint8_t *model, size_t length, CairnOrtSession **out, CairnOrtOptions *options, CairnOrtError *error);
void cairn_ort_session_close(CairnOrtSession *session);
size_t cairn_ort_node_count(const CairnOrtSession *session, int input);
int cairn_ort_node(const CairnOrtSession *session, int input, size_t index, CairnOrtNode *out, CairnOrtError *error);
int cairn_ort_run(CairnOrtSession *session, const int64_t *ids, const int64_t *mask, const int64_t *types, size_t sequence, CairnOrtOutput *out, CairnOrtError *error);
int cairn_ort_config_observation(const CairnOrtSession *session, CairnOrtConfigView *out, CairnOrtError *error);
int cairn_ort_io_observation(const CairnOrtSession *session, int input, size_t index, CairnOrtIoObservation *out, CairnOrtError *error);
int cairn_ort_assignment_observation(const CairnOrtSession *session, CairnOrtAssignmentView *out, CairnOrtError *error);
void cairn_ort_output_release(CairnOrtOutput *out);
#endif
