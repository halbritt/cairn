#ifndef CAIRN_SEMANTIC_MATH_H
#define CAIRN_SEMANTIC_MATH_H
#include <stdint.h>
#include <stddef.h>
typedef struct CairnMathRuntime CairnMathRuntime;
typedef struct {int32_t code;const char *stage;char *message;int32_t copy_failed;} CairnMathError;
typedef struct {const uint8_t *bytes;size_t length;} CairnMathBytes;
typedef struct {const char *path;uint64_t device;uint64_t inode;int32_t pinned_index;} CairnMathModule;
typedef struct {const char *config;const char *core;int32_t threads;int32_t parallel;} CairnMathSettings;
void cairn_math_error_release(CairnMathError *error);
int cairn_math_open(const CairnMathBytes inputs[3],CairnMathRuntime **out,CairnMathSettings *settings,CairnMathError *error);
int cairn_math_close(CairnMathRuntime *runtime,CairnMathError *error);
size_t cairn_math_module_count(const CairnMathRuntime *runtime);
int cairn_math_module(const CairnMathRuntime *runtime,size_t index,CairnMathModule *out,CairnMathError *error);
int cairn_math_compute(CairnMathRuntime *runtime,const float *matrix,size_t full_rows,size_t elements,int32_t operation,float *output,size_t output_length,CairnMathError *error);
#endif
