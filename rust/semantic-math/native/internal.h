#ifndef CAIRN_MATH_INTERNAL_H
#define CAIRN_MATH_INTERNAL_H
#define _GNU_SOURCE
#include "bridge.h"
#include "cblas.h"
#include <link.h>
typedef __typeof__(&scipy_cblas_sgemv64_) CairnSgemv;
typedef __typeof__(&scipy_cblas_sdot64_) CairnSdot;
typedef __typeof__(&scipy_openblas_get_config64_) CairnConfig;
typedef __typeof__(&scipy_openblas_get_corename64_) CairnCore;
typedef __typeof__(&scipy_openblas_get_num_threads64_) CairnThreads;
typedef __typeof__(&scipy_openblas_get_parallel64_) CairnParallel;
struct CairnMathRuntime {void *handles[3];int fds[3];CairnSgemv gemv;CairnSdot dot;CairnMathModule *modules;size_t module_count;char *config;char *core;};
int cairn_math_error(CairnMathError *error,int code,const char *stage,const char *message);
#endif
