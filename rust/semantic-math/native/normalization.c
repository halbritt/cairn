/* SPDX-License-Identifier: BSD-3-Clause
 * Adapted from NumPy, Copyright (c) 2005-2025, NumPy Developers.
 * See vendor/numpy-2.4.6/LICENSE.txt.
 * Source-derived NumPy 2.4.6 float32 normalization. See normalization provenance.
 * x86-64 SSE lane operations keep explicit binary32 rounding without FMA.
 * No runtime-dispatch/CPU/FENV or NaN-bit parity is asserted by this source unit.
 */
#include <stddef.h>
#include <float.h>
#include <emmintrin.h>
_Static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24
               && FLT_MAX_EXP == 128, "requires IEEE binary32");
#define WIDTH 384
static float add(float a, float b) {
    return _mm_cvtss_f32(_mm_add_ss(_mm_set_ss(a), _mm_set_ss(b)));
}
/* NumPy loops_utils.h.src FLOAT_pairwise_sum, PW_BLOCKSIZE=128. */
static float pairwise(const float *a, size_t n) {
    if (n < 8) {
        float result = -0.0f;
        for (size_t i = 0; i < n; ++i) result = add(result, a[i]);
        return result;
    }
    if (n <= 128) {
        float r[8];
        for (size_t j = 0; j < 8; ++j) {
            r[j] = a[j];
        }
        size_t i = 8;
        for (; i < n - n % 8; i += 8) {
            for (size_t j = 0; j < 8; ++j) {
                r[j] = add(r[j], a[i + j]);
            }
        }
        float result = add(add(add(r[0], r[1]), add(r[2], r[3])),
                           add(add(r[4], r[5]), add(r[6], r[7])));
        for (; i < n; ++i) {
            result = add(result, a[i]);
        }
        return result;
    }
    size_t half = n / 2;
    half -= half % 8;
    return add(pairwise(a, half), pairwise(a + half, n - half));
}
static float norm384(const float *row) {
    float squares[WIDTH];
    for (size_t i = 0; i < WIDTH; i += 4) {
        __m128 x = _mm_loadu_ps(row + i);
        _mm_storeu_ps(squares + i, _mm_mul_ps(x, x));
    }
    /* FLOAT_add's outer reduction updates its positive-zero identity. */
    float sum = add(0.0f, pairwise(squares, WIDTH));
    return _mm_cvtss_f32(_mm_sqrt_ps(_mm_set_ss(sum)));
}
static void divide384(float *row, float norm) {
    __m128 denominator = _mm_set1_ps(norm);
    for (size_t i = 0; i < WIDTH; i += 4) {
        _mm_storeu_ps(row + i, _mm_div_ps(_mm_loadu_ps(row + i), denominator));
    }
}
void cairn_norm_first(float *row, float *norm, float *denominator) {
    *norm = norm384(row);
    /* Python's binary64 1e-12 is weakly cast to FLOAT by NumPy. Keep this
     * conversion at runtime (including the current rounding mode), rather
     * than substituting a compiler-rounded binary32 constant. */
    volatile double epsilon = 0x1.19799812dea11p-40;
    __m128 b = _mm_cvtsd_ss(_mm_setzero_ps(), _mm_set_sd(epsilon));
    __m128 a = _mm_set_ss(*norm);
    __m128 maximum = _mm_max_ss(a, b);
    __m128 ordered = _mm_cmpord_ss(a, a);
    /* NumPy scalar_max_f: propagate FIRST NaN; x86 max handles second operand. */
    maximum = _mm_xor_ps(a, _mm_and_ps(_mm_xor_ps(a, maximum), ordered));
    *denominator = _mm_cvtss_f32(maximum);
    divide384(row, *denominator);
}
void cairn_norm_rows(const float *values, size_t rows, float *norms) {
    for (size_t row = 0; row < rows; ++row) {
        norms[row] = norm384(values + row * WIDTH);
    }
}
void cairn_norm_divide_rows(float *values, size_t rows, const float *norms) {
    for (size_t row = 0; row < rows; ++row) {
        divide384(values + row * WIDTH, norms[row]);
    }
}
