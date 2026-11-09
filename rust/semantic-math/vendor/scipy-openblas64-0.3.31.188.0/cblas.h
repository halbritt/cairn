/***************************************************************************
 * Copyright (c) 2025, The OpenBLAS Project
 * All rights reserved.
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met:
 * 1. Redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in
 * the documentation and/or other materials provided with the
 * distribution.
 * 3. Neither the name of the OpenBLAS project nor the names of
 * its contributors may be used to endorse or promote products
 * derived from this software without specific prior written permission.
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE OPENBLAS PROJECT OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 * *****************************************************************************/

#ifndef CBLAS_H
#define CBLAS_H

#include <stddef.h>
#include "openblas_config.h"

#ifdef __cplusplus
extern "C" {
	/* Assume C declarations for C++ */
#endif  /* __cplusplus */

/*Set the number of threads on runtime.*/
void scipy_openblas_set_num_threads64_(int num_threads);
void scipy_goto_set_num_threads64_(int num_threads);
int scipy_openblas_set_num_threads_local64_(int num_threads);

/*Get the number of threads on runtime.*/
int scipy_openblas_get_num_threads64_(void);

/*Get the number of physical processors (cores).*/
int scipy_openblas_get_num_procs64_(void);

/*Get the build configure on runtime.*/
char* scipy_openblas_get_config64_(void);

/*Get the CPU corename on runtime.*/
char* scipy_openblas_get_corename64_(void);

/*Set the threading backend to a custom callback.*/
typedef void (*scipy_openblas_dojob_callback64_)(int thread_num, void *jobdata, int dojob_data);
typedef void (*scipy_openblas_threads_callback64_)(int sync, scipy_openblas_dojob_callback64_ dojob, int numjobs, size_t jobdata_elsize, void *jobdata, int dojob_data);
void scipy_openblas_set_threads_callback_function64_(scipy_openblas_threads_callback64_ callback);

#ifdef OPENBLAS_OS_LINUX
/* Sets thread affinity for OpenBLAS threads. `thread_idx` is in [0, scipy_openblas_get_num_threads64_()-1]. */
int scipy_openblas_setaffinity64_(int thread_idx, size_t cpusetsize, cpu_set_t* cpu_set);
/* Queries thread affinity for OpenBLAS threads. `thread_idx` is in [0, scipy_openblas_get_num_threads64_()-1]. */
int scipy_openblas_getaffinity64_(int thread_idx, size_t cpusetsize, cpu_set_t* cpu_set);
#endif

/* Get the parallelization type which is used by OpenBLAS */
int scipy_openblas_get_parallel64_(void);
/* OpenBLAS is compiled for sequential use  */
#define OPENBLAS_SEQUENTIAL  0
/* OpenBLAS is compiled using normal threading model */
#define OPENBLAS_THREAD  1
/* OpenBLAS is compiled using OpenMP threading model */
#define OPENBLAS_OPENMP 2


/*
 * Since all of GotoBlas was written without const,
 * we disable it at build time.
 */
#ifndef OPENBLAS_CONST
# define OPENBLAS_CONST const
#endif


#define CBLAS_INDEX size_t

typedef enum CBLAS_ORDER     {CblasRowMajor=101, CblasColMajor=102} CBLAS_ORDER;
typedef enum CBLAS_TRANSPOSE {CblasNoTrans=111, CblasTrans=112, CblasConjTrans=113, CblasConjNoTrans=114} CBLAS_TRANSPOSE;
typedef enum CBLAS_UPLO      {CblasUpper=121, CblasLower=122} CBLAS_UPLO;
typedef enum CBLAS_DIAG      {CblasNonUnit=131, CblasUnit=132} CBLAS_DIAG;
typedef enum CBLAS_SIDE      {CblasLeft=141, CblasRight=142} CBLAS_SIDE;
typedef CBLAS_ORDER CBLAS_LAYOUT;
	
float  scipy_cblas_sdsdot64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST float *y, OPENBLAS_CONST blasint incy);
double scipy_cblas_dsdot64_ (OPENBLAS_CONST blasint n, OPENBLAS_CONST float *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST float *y, OPENBLAS_CONST blasint incy);
float  scipy_cblas_sdot64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST float  *y, OPENBLAS_CONST blasint incy);
double scipy_cblas_ddot64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST double *y, OPENBLAS_CONST blasint incy);

openblas_complex_float   scipy_cblas_cdotu64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST void  *y, OPENBLAS_CONST blasint incy);
openblas_complex_float   scipy_cblas_cdotc64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST void  *y, OPENBLAS_CONST blasint incy);
openblas_complex_double  scipy_cblas_zdotu64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST void *y, OPENBLAS_CONST blasint incy);
openblas_complex_double  scipy_cblas_zdotc64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST void *y, OPENBLAS_CONST blasint incy);

void  scipy_cblas_cdotu_sub64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST void  *y, OPENBLAS_CONST blasint incy, void  *ret);
void  scipy_cblas_cdotc_sub64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST void  *y, OPENBLAS_CONST blasint incy, void  *ret);
void  scipy_cblas_zdotu_sub64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST void *y, OPENBLAS_CONST blasint incy, void *ret);
void  scipy_cblas_zdotc_sub64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST void *y, OPENBLAS_CONST blasint incy, void *ret);

float  scipy_cblas_sasum64_ (OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx);
double scipy_cblas_dasum64_ (OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx);
float  scipy_cblas_scasum64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx);
double scipy_cblas_dzasum64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx);

float  scipy_cblas_ssum64_ (OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx);
double scipy_cblas_dsum64_ (OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx);
float  scipy_cblas_scsum64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx);
double scipy_cblas_dzsum64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx);

float  scipy_cblas_snrm264_ (OPENBLAS_CONST blasint N, OPENBLAS_CONST float  *X, OPENBLAS_CONST blasint incX);
double scipy_cblas_dnrm264_ (OPENBLAS_CONST blasint N, OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX);
float  scipy_cblas_scnrm264_(OPENBLAS_CONST blasint N, OPENBLAS_CONST void  *X, OPENBLAS_CONST blasint incX);
double scipy_cblas_dznrm264_(OPENBLAS_CONST blasint N, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX);

CBLAS_INDEX scipy_cblas_isamax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_idamax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_icamax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_izamax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx);

CBLAS_INDEX scipy_cblas_isamin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_idamin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_icamin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_izamin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx);

float scipy_cblas_samax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx);
double scipy_cblas_damax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx);
float scipy_cblas_scamax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx);
double scipy_cblas_dzamax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx);

float scipy_cblas_samin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx);
double scipy_cblas_damin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx);
float scipy_cblas_scamin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx);
double scipy_cblas_dzamin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx);

CBLAS_INDEX scipy_cblas_ismax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_idmax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_icmax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_izmax64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx);

CBLAS_INDEX scipy_cblas_ismin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_idmin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_icmin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx);
CBLAS_INDEX scipy_cblas_izmin64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx);

void scipy_cblas_saxpy64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *x, OPENBLAS_CONST blasint incx, float *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_daxpy64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx, double *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_caxpy64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_zaxpy64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_caxpyc64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_zaxpyc64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_scopy64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float *x, OPENBLAS_CONST blasint incx, float *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_dcopy64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx, double *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_ccopy64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_zcopy64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_sswap64_(OPENBLAS_CONST blasint n, float *x, OPENBLAS_CONST blasint incx, float *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_dswap64_(OPENBLAS_CONST blasint n, double *x, OPENBLAS_CONST blasint incx, double *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_cswap64_(OPENBLAS_CONST blasint n, void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_zswap64_(OPENBLAS_CONST blasint n, void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_srot64_(OPENBLAS_CONST blasint N, float *X, OPENBLAS_CONST blasint incX, float *Y, OPENBLAS_CONST blasint incY, OPENBLAS_CONST float c, OPENBLAS_CONST float s);
void scipy_cblas_drot64_(OPENBLAS_CONST blasint N, double *X, OPENBLAS_CONST blasint incX, double *Y, OPENBLAS_CONST blasint incY, OPENBLAS_CONST double c, OPENBLAS_CONST double  s);
void scipy_cblas_csrot64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incY, OPENBLAS_CONST float c, OPENBLAS_CONST float s);
void scipy_cblas_zdrot64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx, void *y, OPENBLAS_CONST blasint incY, OPENBLAS_CONST double c, OPENBLAS_CONST double s);

void scipy_cblas_srotg64_(float *a, float *b, float *c, float *s);
void scipy_cblas_drotg64_(double *a, double *b, double *c, double *s);
void scipy_cblas_crotg64_(void *a, void *b, float *c, void *s);
void scipy_cblas_zrotg64_(void *a, void *b, double *c, void *s);


void scipy_cblas_srotm64_(OPENBLAS_CONST blasint N, float *X, OPENBLAS_CONST blasint incX, float *Y, OPENBLAS_CONST blasint incY, OPENBLAS_CONST float *P);
void scipy_cblas_drotm64_(OPENBLAS_CONST blasint N, double *X, OPENBLAS_CONST blasint incX, double *Y, OPENBLAS_CONST blasint incY, OPENBLAS_CONST double *P);

void scipy_cblas_srotmg64_(float *d1, float *d2, float *b1, OPENBLAS_CONST float b2, float *P);
void scipy_cblas_drotmg64_(double *d1, double *d2, double *b1, OPENBLAS_CONST double b2, double *P);

void scipy_cblas_sscal64_(OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, float *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_dscal64_(OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, double *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_cscal64_(OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_zscal64_(OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_csscal64_(OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_zdscal64_(OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, void *X, OPENBLAS_CONST blasint incX);

void scipy_cblas_sgemv64_(OPENBLAS_CONST enum CBLAS_ORDER order,  OPENBLAS_CONST enum CBLAS_TRANSPOSE trans,  OPENBLAS_CONST blasint m, OPENBLAS_CONST blasint n,
		 OPENBLAS_CONST float alpha, OPENBLAS_CONST float  *a, OPENBLAS_CONST blasint lda,  OPENBLAS_CONST float  *x, OPENBLAS_CONST blasint incx,  OPENBLAS_CONST float beta,  float  *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_dgemv64_(OPENBLAS_CONST enum CBLAS_ORDER order,  OPENBLAS_CONST enum CBLAS_TRANSPOSE trans,  OPENBLAS_CONST blasint m, OPENBLAS_CONST blasint n,
		 OPENBLAS_CONST double alpha, OPENBLAS_CONST double  *a, OPENBLAS_CONST blasint lda,  OPENBLAS_CONST double  *x, OPENBLAS_CONST blasint incx,  OPENBLAS_CONST double beta,  double  *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_cgemv64_(OPENBLAS_CONST enum CBLAS_ORDER order,  OPENBLAS_CONST enum CBLAS_TRANSPOSE trans,  OPENBLAS_CONST blasint m, OPENBLAS_CONST blasint n,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void  *a, OPENBLAS_CONST blasint lda,  OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx,  OPENBLAS_CONST void *beta,  void  *y, OPENBLAS_CONST blasint incy);
void scipy_cblas_zgemv64_(OPENBLAS_CONST enum CBLAS_ORDER order,  OPENBLAS_CONST enum CBLAS_TRANSPOSE trans,  OPENBLAS_CONST blasint m, OPENBLAS_CONST blasint n,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void  *a, OPENBLAS_CONST blasint lda,  OPENBLAS_CONST void  *x, OPENBLAS_CONST blasint incx,  OPENBLAS_CONST void *beta,  void  *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_sger64_ (OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST float   alpha, OPENBLAS_CONST float  *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST float  *Y, OPENBLAS_CONST blasint incY, float  *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_dger64_ (OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST double  alpha, OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST double *Y, OPENBLAS_CONST blasint incY, double *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_cgeru64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST void  *alpha, OPENBLAS_CONST void  *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void  *Y, OPENBLAS_CONST blasint incY, void  *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_cgerc64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST void  *alpha, OPENBLAS_CONST void  *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void  *Y, OPENBLAS_CONST blasint incY, void  *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_zgeru64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *Y, OPENBLAS_CONST blasint incY, void *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_zgerc64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *Y, OPENBLAS_CONST blasint incY, void *A, OPENBLAS_CONST blasint lda);

void scipy_cblas_strsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint N, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, float *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_dtrsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint N, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, double *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ctrsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ztrsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *X, OPENBLAS_CONST blasint incX);

void scipy_cblas_strmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint N, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, float *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_dtrmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint N, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, double *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ctrmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ztrmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *X, OPENBLAS_CONST blasint incX);

void scipy_cblas_ssyr64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *X, OPENBLAS_CONST blasint incX, float *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_dsyr64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX, double *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_cher64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, void *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_zher64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, void *A, OPENBLAS_CONST blasint lda);

void scipy_cblas_ssyr264_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo,OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *X,
                OPENBLAS_CONST blasint incX, OPENBLAS_CONST float *Y, OPENBLAS_CONST blasint incY, float *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_dsyr264_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *X,
                OPENBLAS_CONST blasint incX, OPENBLAS_CONST double *Y, OPENBLAS_CONST blasint incY, double *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_cher264_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX,
                OPENBLAS_CONST void *Y, OPENBLAS_CONST blasint incY, void *A, OPENBLAS_CONST blasint lda);
void scipy_cblas_zher264_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX,
                OPENBLAS_CONST void *Y, OPENBLAS_CONST blasint incY, void *A, OPENBLAS_CONST blasint lda);

void scipy_cblas_sgbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST blasint KL, OPENBLAS_CONST blasint KU, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST float *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST float beta, float *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_dgbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST blasint KL, OPENBLAS_CONST blasint KU, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST double beta, double *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_cgbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST blasint KL, OPENBLAS_CONST blasint KU, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *beta, void *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_zgbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST blasint KL, OPENBLAS_CONST blasint KU, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *beta, void *Y, OPENBLAS_CONST blasint incY);

void scipy_cblas_ssbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A,
                 OPENBLAS_CONST blasint lda, OPENBLAS_CONST float *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST float beta, float *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_dsbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A,
                 OPENBLAS_CONST blasint lda, OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST double beta, double *Y, OPENBLAS_CONST blasint incY);


void scipy_cblas_stbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, float *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_dtbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, double *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ctbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ztbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *X, OPENBLAS_CONST blasint incX);

void scipy_cblas_stbsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, float *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_dtbsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, double *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ctbsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ztbsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *X, OPENBLAS_CONST blasint incX);

void scipy_cblas_stpmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST float *Ap, float *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_dtpmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST double *Ap, double *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ctpmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST void *Ap, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ztpmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST void *Ap, void *X, OPENBLAS_CONST blasint incX);

void scipy_cblas_stpsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST float *Ap, float *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_dtpsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST double *Ap, double *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ctpsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST void *Ap, void *X, OPENBLAS_CONST blasint incX);
void scipy_cblas_ztpsv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_DIAG Diag,
                 OPENBLAS_CONST blasint N, OPENBLAS_CONST void *Ap, void *X, OPENBLAS_CONST blasint incX);

void scipy_cblas_ssymv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A,
                 OPENBLAS_CONST blasint lda, OPENBLAS_CONST float *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST float beta, float *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_dsymv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A,
                 OPENBLAS_CONST blasint lda, OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST double beta, double *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_chemv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A,
                 OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *beta, void *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_zhemv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A,
                 OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *beta, void *Y, OPENBLAS_CONST blasint incY);


void scipy_cblas_sspmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *Ap,
                 OPENBLAS_CONST float *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST float beta, float *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_dspmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *Ap,
                 OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST double beta, double *Y, OPENBLAS_CONST blasint incY);

void scipy_cblas_sspr64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *X, OPENBLAS_CONST blasint incX, float *Ap);
void scipy_cblas_dspr64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX, double *Ap);

void scipy_cblas_chpr64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, void *A);
void scipy_cblas_zhpr64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST void *X,OPENBLAS_CONST blasint incX, void *A);

void scipy_cblas_sspr264_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST float *Y, OPENBLAS_CONST blasint incY, float *A);
void scipy_cblas_dspr264_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST double *Y, OPENBLAS_CONST blasint incY, double *A);
void scipy_cblas_chpr264_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *Y, OPENBLAS_CONST blasint incY, void *Ap);
void scipy_cblas_zhpr264_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *Y, OPENBLAS_CONST blasint incY, void *Ap);

void scipy_cblas_chbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *beta, void *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_zhbmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *beta, void *Y, OPENBLAS_CONST blasint incY);

void scipy_cblas_chpmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *Ap, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *beta, void *Y, OPENBLAS_CONST blasint incY);
void scipy_cblas_zhpmv64_(OPENBLAS_CONST enum CBLAS_ORDER order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint N,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *Ap, OPENBLAS_CONST void *X, OPENBLAS_CONST blasint incX, OPENBLAS_CONST void *beta, void *Y, OPENBLAS_CONST blasint incY);

void scipy_cblas_sgemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST float *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST float beta, float *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_dgemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST double *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST double beta, double *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_cgemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_cgemm3m64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zgemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zgemm3m64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);

void scipy_cblas_sgemmt64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST float *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST float beta, float *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_dgemmt64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST double *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST double beta, double *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_cgemmt64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zgemmt64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint K,
		 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);

void scipy_cblas_ssymm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST float *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST float beta, float *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_dsymm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST double *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST double beta, double *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_csymm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zsymm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);

void scipy_cblas_ssyrk64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans,
		 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST float beta, float *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_dsyrk64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans,
		 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST double beta, double *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_csyrk64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans,
		 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zsyrk64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans,
		 OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);

void scipy_cblas_ssyr2k64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans,
		  OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST float *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST float beta, float *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_dsyr2k64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans,
		  OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST double *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST double beta, double *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_csyr2k64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans,
		  OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zsyr2k64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans,
		  OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);

void scipy_cblas_strmm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA,
                 OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, float *B, OPENBLAS_CONST blasint ldb);
void scipy_cblas_dtrmm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA,
                 OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, double *B, OPENBLAS_CONST blasint ldb);
void scipy_cblas_ctrmm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA,
                 OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *B, OPENBLAS_CONST blasint ldb);
void scipy_cblas_ztrmm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA,
                 OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *B, OPENBLAS_CONST blasint ldb);

void scipy_cblas_strsm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA,
                 OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *A, OPENBLAS_CONST blasint lda, float *B, OPENBLAS_CONST blasint ldb);
void scipy_cblas_dtrsm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA,
                 OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *A, OPENBLAS_CONST blasint lda, double *B, OPENBLAS_CONST blasint ldb);
void scipy_cblas_ctrsm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA,
                 OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *B, OPENBLAS_CONST blasint ldb);
void scipy_cblas_ztrsm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA,
                 OPENBLAS_CONST enum CBLAS_DIAG Diag, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, void *B, OPENBLAS_CONST blasint ldb);

void scipy_cblas_chemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zhemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_SIDE Side, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N,
                 OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST void *beta, void *C, OPENBLAS_CONST blasint ldc);

void scipy_cblas_cherk64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
                 OPENBLAS_CONST float alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST float beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zherk64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
                 OPENBLAS_CONST double alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST double beta, void *C, OPENBLAS_CONST blasint ldc);

void scipy_cblas_cher2k64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
                  OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST float beta, void *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_zher2k64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_UPLO Uplo, OPENBLAS_CONST enum CBLAS_TRANSPOSE Trans, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
                  OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST void *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST double beta, void *C, OPENBLAS_CONST blasint ldc);

void scipy_cblas_xerbla64_(blasint p, OPENBLAS_CONST char *rout, OPENBLAS_CONST char *form, ...);

/*** BLAS extensions ***/

void scipy_cblas_saxpby64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float alpha, OPENBLAS_CONST float *x, OPENBLAS_CONST blasint incx,OPENBLAS_CONST float beta, float *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_daxpby64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double alpha, OPENBLAS_CONST double *x, OPENBLAS_CONST blasint incx,OPENBLAS_CONST double beta, double *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_caxpby64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx,OPENBLAS_CONST void *beta, void *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_zaxpby64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST void *alpha, OPENBLAS_CONST void *x, OPENBLAS_CONST blasint incx,OPENBLAS_CONST void *beta, void *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_somatcopy64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER, OPENBLAS_CONST enum CBLAS_TRANSPOSE CTRANS, OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST float calpha, OPENBLAS_CONST float *a, 
		     OPENBLAS_CONST blasint clda, float *b, OPENBLAS_CONST blasint cldb); 
void scipy_cblas_domatcopy64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER, OPENBLAS_CONST enum CBLAS_TRANSPOSE CTRANS, OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST double calpha, OPENBLAS_CONST double *a,
		     OPENBLAS_CONST blasint clda, double *b, OPENBLAS_CONST blasint cldb); 
void scipy_cblas_comatcopy64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER, OPENBLAS_CONST enum CBLAS_TRANSPOSE CTRANS, OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST float* calpha, OPENBLAS_CONST float* a, 
		     OPENBLAS_CONST blasint clda, float*b, OPENBLAS_CONST blasint cldb); 
void scipy_cblas_zomatcopy64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER, OPENBLAS_CONST enum CBLAS_TRANSPOSE CTRANS, OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST double* calpha, OPENBLAS_CONST double* a, 
		     OPENBLAS_CONST blasint clda,  double *b, OPENBLAS_CONST blasint cldb); 

void scipy_cblas_simatcopy64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER, OPENBLAS_CONST enum CBLAS_TRANSPOSE CTRANS, OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST float calpha, float *a, 
		     OPENBLAS_CONST blasint clda, OPENBLAS_CONST blasint cldb); 
void scipy_cblas_dimatcopy64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER, OPENBLAS_CONST enum CBLAS_TRANSPOSE CTRANS, OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST double calpha, double *a,
		     OPENBLAS_CONST blasint clda, OPENBLAS_CONST blasint cldb); 
void scipy_cblas_cimatcopy64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER, OPENBLAS_CONST enum CBLAS_TRANSPOSE CTRANS, OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST float* calpha, float* a, 
		     OPENBLAS_CONST blasint clda, OPENBLAS_CONST blasint cldb); 
void scipy_cblas_zimatcopy64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER, OPENBLAS_CONST enum CBLAS_TRANSPOSE CTRANS, OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST double* calpha, double* a, 
		     OPENBLAS_CONST blasint clda, OPENBLAS_CONST blasint cldb); 

void scipy_cblas_sgeadd64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER,OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST float calpha, OPENBLAS_CONST float *a, OPENBLAS_CONST blasint clda, OPENBLAS_CONST float cbeta, 
		  float *c, OPENBLAS_CONST blasint cldc); 
void scipy_cblas_dgeadd64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER,OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST double calpha, OPENBLAS_CONST double *a, OPENBLAS_CONST blasint clda, OPENBLAS_CONST double cbeta, 
		  double *c, OPENBLAS_CONST blasint cldc); 
void scipy_cblas_cgeadd64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER,OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST float *calpha, OPENBLAS_CONST float *a, OPENBLAS_CONST blasint clda, OPENBLAS_CONST float *cbeta, 
		  float *c, OPENBLAS_CONST blasint cldc); 
void scipy_cblas_zgeadd64_(OPENBLAS_CONST enum CBLAS_ORDER CORDER,OPENBLAS_CONST blasint crows, OPENBLAS_CONST blasint ccols, OPENBLAS_CONST double *calpha, OPENBLAS_CONST double *a, OPENBLAS_CONST blasint clda, OPENBLAS_CONST double *cbeta, 
		  double *c, OPENBLAS_CONST blasint cldc); 

void scipy_cblas_sgemm_batch64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransA_array, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransB_array, OPENBLAS_CONST blasint * M_array, OPENBLAS_CONST blasint * N_array, OPENBLAS_CONST blasint * K_array,
		       OPENBLAS_CONST float * alpha_array, OPENBLAS_CONST float ** A_array, OPENBLAS_CONST blasint * lda_array, OPENBLAS_CONST float ** B_array, OPENBLAS_CONST blasint * ldb_array, OPENBLAS_CONST float * beta_array, float ** C_array, OPENBLAS_CONST blasint * ldc_array, OPENBLAS_CONST blasint group_count, OPENBLAS_CONST blasint * group_size);

void scipy_cblas_dgemm_batch64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransA_array, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransB_array, OPENBLAS_CONST blasint * M_array, OPENBLAS_CONST blasint * N_array, OPENBLAS_CONST blasint * K_array,
		       OPENBLAS_CONST double * alpha_array, OPENBLAS_CONST double ** A_array, OPENBLAS_CONST blasint * lda_array, OPENBLAS_CONST double ** B_array, OPENBLAS_CONST blasint * ldb_array, OPENBLAS_CONST double * beta_array, double ** C_array, OPENBLAS_CONST blasint * ldc_array, OPENBLAS_CONST blasint group_count, OPENBLAS_CONST blasint * group_size);

void scipy_cblas_cgemm_batch64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransA_array, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransB_array, OPENBLAS_CONST blasint * M_array, OPENBLAS_CONST blasint * N_array, OPENBLAS_CONST blasint * K_array,
		       OPENBLAS_CONST void * alpha_array, OPENBLAS_CONST void ** A_array, OPENBLAS_CONST blasint * lda_array, OPENBLAS_CONST void ** B_array, OPENBLAS_CONST blasint * ldb_array, OPENBLAS_CONST void * beta_array, void ** C_array, OPENBLAS_CONST blasint * ldc_array, OPENBLAS_CONST blasint group_count, OPENBLAS_CONST blasint * group_size);

void scipy_cblas_zgemm_batch64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransA_array, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransB_array, OPENBLAS_CONST blasint * M_array, OPENBLAS_CONST blasint * N_array, OPENBLAS_CONST blasint * K_array,
		       OPENBLAS_CONST void * alpha_array, OPENBLAS_CONST void ** A_array, OPENBLAS_CONST blasint * lda_array, OPENBLAS_CONST void ** B_array, OPENBLAS_CONST blasint * ldb_array, OPENBLAS_CONST void * beta_array, void ** C_array, OPENBLAS_CONST blasint * ldc_array, OPENBLAS_CONST blasint group_count, OPENBLAS_CONST blasint * group_size);

void scipy_cblas_sgemm_batch_strided64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST float alpha, OPENBLAS_CONST float * A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST blasint stridea, OPENBLAS_CONST float * B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST blasint strideb, OPENBLAS_CONST float beta, float * C, OPENBLAS_CONST blasint ldc, OPENBLAS_CONST blasint stridec, OPENBLAS_CONST blasint group_size);

void scipy_cblas_dgemm_batch_strided64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST double alpha, OPENBLAS_CONST double * A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST blasint stridea, OPENBLAS_CONST double * B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST blasint strideb, OPENBLAS_CONST double beta, double * C, OPENBLAS_CONST blasint ldc, OPENBLAS_CONST blasint stridec, OPENBLAS_CONST blasint group_size);

void scipy_cblas_cgemm_batch_strided64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void * alpha, OPENBLAS_CONST void * A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST blasint stridea, OPENBLAS_CONST void * B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST blasint strideb, OPENBLAS_CONST void * beta, void * C, OPENBLAS_CONST blasint ldc, OPENBLAS_CONST blasint stridec, OPENBLAS_CONST blasint group_size);

void scipy_cblas_zgemm_batch_strided64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST void * alpha, OPENBLAS_CONST void * A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST blasint stridea, OPENBLAS_CONST void * B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST blasint strideb, OPENBLAS_CONST void * beta, void * C, OPENBLAS_CONST blasint ldc, OPENBLAS_CONST blasint stridec, OPENBLAS_CONST blasint group_size);

/*** BFLOAT16 and INT8 extensions ***/
/* convert float array to BFLOAT16 array by rounding */
void   scipy_cblas_sbstobf1664_(OPENBLAS_CONST blasint n, OPENBLAS_CONST float  *in, OPENBLAS_CONST blasint incin, bfloat16 *out, OPENBLAS_CONST blasint incout);
/* convert double array to BFLOAT16 array by rounding */
void   scipy_cblas_sbdtobf1664_(OPENBLAS_CONST blasint n, OPENBLAS_CONST double *in, OPENBLAS_CONST blasint incin, bfloat16 *out, OPENBLAS_CONST blasint incout);
/* convert BFLOAT16 array to float array */
void   scipy_cblas_sbf16tos64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST bfloat16 *in, OPENBLAS_CONST blasint incin, float  *out, OPENBLAS_CONST blasint incout);
/* convert BFLOAT16 array to double array */
void   scipy_cblas_dbf16tod64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST bfloat16 *in, OPENBLAS_CONST blasint incin, double *out, OPENBLAS_CONST blasint incout);
void   scipy_cblas_bgemv64_(OPENBLAS_CONST enum CBLAS_ORDER order,  OPENBLAS_CONST enum CBLAS_TRANSPOSE trans,  OPENBLAS_CONST blasint m, OPENBLAS_CONST blasint n, OPENBLAS_CONST bfloat16 alpha, OPENBLAS_CONST bfloat16 *a, OPENBLAS_CONST blasint lda, OPENBLAS_CONST bfloat16 *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST bfloat16 beta, bfloat16 *y, OPENBLAS_CONST blasint incy);
/* dot production of BFLOAT16 input arrays, and output as float */
float  scipy_cblas_sbdot64_(OPENBLAS_CONST blasint n, OPENBLAS_CONST bfloat16 *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST bfloat16 *y, OPENBLAS_CONST blasint incy);
void   scipy_cblas_sbgemv64_(OPENBLAS_CONST enum CBLAS_ORDER order,  OPENBLAS_CONST enum CBLAS_TRANSPOSE trans,  OPENBLAS_CONST blasint m, OPENBLAS_CONST blasint n, OPENBLAS_CONST float alpha, OPENBLAS_CONST bfloat16 *a, OPENBLAS_CONST blasint lda, OPENBLAS_CONST bfloat16 *x, OPENBLAS_CONST blasint incx, OPENBLAS_CONST float beta, float *y, OPENBLAS_CONST blasint incy);

void scipy_cblas_bgemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		    OPENBLAS_CONST bfloat16 alpha, OPENBLAS_CONST bfloat16 *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST bfloat16 *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST bfloat16 beta, bfloat16 *C, OPENBLAS_CONST blasint ldc);
void   scipy_cblas_sbgemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		    OPENBLAS_CONST float alpha, OPENBLAS_CONST bfloat16 *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST bfloat16 *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST float beta, float *C, OPENBLAS_CONST blasint ldc);
void scipy_cblas_sbgemm_batch64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransA_array, OPENBLAS_CONST enum CBLAS_TRANSPOSE * TransB_array, OPENBLAS_CONST blasint * M_array, OPENBLAS_CONST blasint * N_array, OPENBLAS_CONST blasint * K_array,
		       OPENBLAS_CONST float * alpha_array, OPENBLAS_CONST bfloat16 ** A_array, OPENBLAS_CONST blasint * lda_array, OPENBLAS_CONST bfloat16 ** B_array, OPENBLAS_CONST blasint * ldb_array, OPENBLAS_CONST float * beta_array, float ** C_array, OPENBLAS_CONST blasint * ldc_array, OPENBLAS_CONST blasint group_count, OPENBLAS_CONST blasint * group_size);

void scipy_cblas_sbgemm_batch_strided64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K, OPENBLAS_CONST float alpha, OPENBLAS_CONST bfloat16 * A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST blasint stridea, OPENBLAS_CONST bfloat16 * B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST blasint strideb, OPENBLAS_CONST float beta, float * C, OPENBLAS_CONST blasint ldc, OPENBLAS_CONST blasint stridec, OPENBLAS_CONST blasint group_size);
/*** FLOAT16 extensions ***/
void scipy_cblas_shgemm64_(OPENBLAS_CONST enum CBLAS_ORDER Order, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransA, OPENBLAS_CONST enum CBLAS_TRANSPOSE TransB, OPENBLAS_CONST blasint M, OPENBLAS_CONST blasint N, OPENBLAS_CONST blasint K,
		    OPENBLAS_CONST float alpha, OPENBLAS_CONST hfloat16 *A, OPENBLAS_CONST blasint lda, OPENBLAS_CONST hfloat16 *B, OPENBLAS_CONST blasint ldb, OPENBLAS_CONST float beta, float *C, OPENBLAS_CONST blasint ldc);

#ifdef __cplusplus
}
#endif  /* __cplusplus */

#endif
