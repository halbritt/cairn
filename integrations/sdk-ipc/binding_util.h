#ifndef CAIRN_BINDING_UTIL_H
#define CAIRN_BINDING_UTIL_H
#include <node_api.h>
#include <math.h>
#include "peer_record.h"

static const napi_type_tag peer_tag = {UINT64_C(0x3c06d4a7e99c42b1), UINT64_C(0xb8a8d3fd662590af)};
static napi_value error(napi_env env, const char *code) {
    bool pending = false;
    if (napi_is_exception_pending(env, &pending) == napi_ok && pending) {
        napi_value discarded;
        (void)napi_get_and_clear_last_exception(env, &discarded);
    }
    (void)napi_throw_error(env, code, code);
    return NULL;
}
static int arguments(napi_env env, napi_callback_info info, size_t count, napi_value *values) {
    size_t n = 0;
    if (napi_get_cb_info(env, info, &n, NULL, NULL, NULL) != napi_ok || n != count) return -1;
    return napi_get_cb_info(env, info, &n, values, NULL, NULL) == napi_ok && n == count ? 0 : -1;
}
static int integer(napi_env env, napi_value value, double maximum, uint32_t *result) {
    double n;
    if (napi_get_value_double(env, value, &n) != napi_ok || !isfinite(n)
        || n < 0 || n > maximum || floor(n) != n) return -1;
    *result = (uint32_t)n;
    return 0;
}
static int bigint(napi_env env, napi_value value, int64_t *result) {
    bool exact = false;
    return napi_get_value_bigint_int64(env, value, result, &exact) == napi_ok && exact ? 0 : -1;
}
typedef struct {unsigned char *data; size_t size; napi_value backing;} ipc_view;
static int view(napi_env env, napi_value value, ipc_view *result) {
    bool typed = false, ordinary = false, detached = false;
    napi_typedarray_type type; size_t offset;
    if (napi_is_typedarray(env, value, &typed) != napi_ok || !typed
        || napi_get_typedarray_info(env, value, &type, &result->size,
            (void **)&result->data, &result->backing, &offset) != napi_ok
        || type != napi_uint8_array
        || napi_is_arraybuffer(env, result->backing, &ordinary) != napi_ok || !ordinary
        || napi_is_detached_arraybuffer(env, result->backing, &detached) != napi_ok || detached)
        return -1;
    return 0; /* No JS callbacks or retained raw pointer across this call. */
}
static ipc_peer *peer(napi_env env, napi_value value) {
    bool tagged = false; void *p = NULL;
    if (napi_check_object_type_tag(env, value, &peer_tag, &tagged) != napi_ok || !tagged
        || napi_unwrap(env, value, &p) != napi_ok || !p) return NULL;
    return p;
}
static void finalize(napi_env env, void *data, void *hint) {
    (void)env; (void)hint;
    ipc_peer *p = data;
    (void)ipc_close(p); /* Best effort only; explicit close is the observable path. */
    free(p);
}
#endif
