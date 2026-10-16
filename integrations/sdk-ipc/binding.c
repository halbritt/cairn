#define _GNU_SOURCE
#define NAPI_VERSION 8
#include "binding_util.h"

static napi_value protect(napi_env env, napi_callback_info info) {
    if (arguments(env, info, 0, NULL)) return error(env, "ipc_argument");
    if (prctl(PR_SET_DUMPABLE, 0) || prctl(PR_GET_DUMPABLE) != 0)
        return error(env, "ipc_protection_failed");
    napi_value value;
    if (napi_create_int32(env, getpid(), &value) != napi_ok) return error(env, "ipc_result");
    return value;
}
static napi_value now(napi_env env, napi_callback_info info) {
    if (arguments(env, info, 0, NULL)) return error(env, "ipc_argument");
    int64_t time = ipc_now(); napi_value value;
    if (time < 0 || napi_create_bigint_int64(env, time, &value) != napi_ok)
        return error(env, "ipc_clock");
    return value;
}
static napi_value open_peer(napi_env env, napi_callback_info info) {
    napi_value a[7], object; uint32_t fd, pidfd, pid, uid, gid; int64_t deadline; ipc_view scratch;
    if (arguments(env, info, 7, a) || integer(env,a[0],INT_MAX,&fd)
        || integer(env,a[1],INT_MAX,&pidfd) || integer(env,a[2],INT_MAX,&pid) || pid <= 1
        || integer(env,a[3],UINT32_MAX,&uid) || integer(env,a[4],UINT32_MAX,&gid)
        || bigint(env,a[5],&deadline) || view(env,a[6],&scratch) || scratch.size != 512)
        return error(env, "ipc_argument");
    ipc_peer *p = calloc(1, sizeof(*p));
    if (!p) return error(env, "ipc_allocation");
    int result = ipc_open(p, (int)fd, (int)pidfd, (pid_t)pid, (uid_t)uid,
        (gid_t)gid, deadline, scratch.data);
    memset(scratch.data, 0, scratch.size);
    if (result || napi_create_object(env, &object) != napi_ok
        || napi_type_tag_object(env, object, &peer_tag) != napi_ok
        || napi_wrap(env, object, p, finalize, NULL, NULL) != napi_ok) {
        const char *code = p->error ? p->error : "ipc_wrap";
        if (ipc_close(p)) code = "ipc_setup_cleanup_unknown";
        free(p); return error(env, code);
    }
    return object;
}
static napi_value close_peer(napi_env env, napi_callback_info info) {
    napi_value a[1], result;
    if (arguments(env, info, 1, a)) return error(env, "ipc_argument");
    ipc_peer *p = peer(env, a[0]);
    if (!p) return error(env, "ipc_peer_argument");
    if (p->fd < 0 && p->pidfd < 0) return error(env, "ipc_peer_closed");
    if (ipc_close(p)) return error(env, p->error);
    if (napi_get_undefined(env, &result) != napi_ok) return error(env, "ipc_result");
    return result;
}
static napi_value record(napi_env env, napi_callback_info info, int receiving) {
    napi_value a[2], result; ipc_view bytes;
    if (arguments(env, info, 2, a)) return error(env, "ipc_argument");
    ipc_peer *p = peer(env, a[0]);
    if (!p) return error(env, "ipc_peer_argument");
    if (view(env, a[1], &bytes) || (receiving ? bytes.size != 544 : bytes.size < 32 || bytes.size > 544))
        return error(env, "ipc_argument");
    int n = receiving ? ipc_receive(p, bytes.data, bytes.size) : ipc_send(p, bytes.data, bytes.size);
    if (n == -2) return error(env, p->error);
    if (napi_create_int32(env, n, &result) != napi_ok) {
        ipc_fail(p, "ipc_result_unknown"); return error(env, p->error);
    }
    return result;
}
static napi_value send_record(napi_env env, napi_callback_info info) {return record(env, info, 0);}
static napi_value receive(napi_env env, napi_callback_info info) {return record(env, info, 1);}
static napi_value exchange(napi_env env, napi_callback_info info) {
    napi_value a[4], result; ipc_view request, reply; int64_t deadline; bool same;
    if (arguments(env, info, 4, a)) return error(env, "ipc_argument");
    ipc_peer *p = peer(env, a[0]);
    if (!p) return error(env, "ipc_peer_argument");
    if (view(env,a[1],&request) || view(env,a[2],&reply)
        || request.size < 32 || request.size > 544 || reply.size != 544
        || bigint(env,a[3],&deadline)
        || napi_strict_equals(env,request.backing,reply.backing,&same) != napi_ok || same)
        return error(env, "ipc_argument");
    int n = ipc_exchange(p, request.data, request.size, reply.data, deadline);
    if (n < 0) return error(env, p->error);
    if (napi_create_int32(env,n,&result) != napi_ok) {
        ipc_fail(p, "ipc_result_unknown"); return error(env, p->error);
    }
    return result; /* Authenticated record length, NOT signal-acceptance Boolean. */
}
static napi_value init(napi_env env, napi_value exports) {
    const napi_property_descriptor methods[] = {
        {"protect",NULL,protect,NULL,NULL,NULL,napi_default,NULL},
        {"now",NULL,now,NULL,NULL,NULL,napi_default,NULL},
        {"openPeer",NULL,open_peer,NULL,NULL,NULL,napi_default,NULL},
        {"closePeer",NULL,close_peer,NULL,NULL,NULL,napi_default,NULL},
        {"sendRecord",NULL,send_record,NULL,NULL,NULL,napi_default,NULL},
        {"receive",NULL,receive,NULL,NULL,NULL,napi_default,NULL},
        {"exchange",NULL,exchange,NULL,NULL,NULL,napi_default,NULL}
    };
    if (napi_define_properties(env,exports,sizeof(methods)/sizeof(methods[0]),methods) != napi_ok)
        return error(env, "ipc_exports");
    return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME, init)
