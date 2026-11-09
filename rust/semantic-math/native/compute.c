#define _GNU_SOURCE
#include "internal.h"
#include <float.h>
#include <limits.h>
_Static_assert(sizeof(blasint)==8,"selected scipy ILP64 header");
_Static_assert(sizeof(float)==4 && FLT_MANT_DIG==24 && FLT_MAX_EXP==128,"binary32 required");
int cairn_math_compute(CairnMathRuntime *r,const float *matrix,size_t rows,size_t elements,int32_t operation,float *output,size_t output_length,CairnMathError *error) {
    if (!rows || rows>SIZE_MAX/384 || elements!=rows*384 || output_length!=rows-1 || rows-1>(uint64_t)INT64_MAX-1)
        return cairn_math_error(error,-1,"matrix layout","invalid full-occurrence float32 storage/ILP64 size");
    size_t passages=rows-1;
    if (!passages && operation==0) return 0;
    if (passages==1 && operation==1) {
        float dot=r->dot((blasint)384,matrix+384,(blasint)1,matrix,(blasint)1);
        /* NumPy FLOAT_dot accumulates BLAS chunks in double. Width384 is ONE
         * chunk: no element/block f64 accumulation is needed. Preserve its
         * addition of positive zero, including signed-zero behavior. Native
         * NaN-payload/FENV correspondence remains in the executable P3 gate. */
        volatile float positive_zero=0.0f;
        output[0]=positive_zero+dot;
        return 0;
    }
    if (passages>1 && operation==2) {
        /* Exact NumPy gemv convention for a C-contiguous [passages,384] view:
         * column-major transposed [384,passages], lda384, increments1. */
        r->gemv(CblasColMajor,CblasTrans,(blasint)384,(blasint)passages,1.0f,matrix+384,(blasint)384,matrix,(blasint)1,0.0f,output,(blasint)1);
        return 0;
    }
    return cairn_math_error(error,-1,"dispatch","operation differs from NumPy zero/one/many-row selection");
}
