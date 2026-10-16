#ifndef CAIRN_READINESS_H
#define CAIRN_READINESS_H
/* Same event-loop thread only. No body storage, record I/O or JS callbacks. */
struct peer_wait {
    uv_poll_t poll;
    uv_timer_t timer;
    binding_peer *owner;
    napi_ref reference;
    napi_deferred deferred;
    int64_t deadline;
    unsigned mask, initialized, closing;
    int done;
};
static void finish_wait(peer_wait *w, const char *code, unsigned ready, int settle);
static void checked(napi_status status) {
    if (status != napi_ok)
        napi_fatal_error("sdk-ipc", NAPI_AUTO_LENGTH, "ipc_napi_cleanup_unknown", NAPI_AUTO_LENGTH);
}
static addon_state *state_for(napi_env env) {
    addon_state *s = NULL;
    return napi_get_instance_data(env, (void **)&s) == napi_ok ? s : NULL;
}
static void drop_state(addon_state *s) {
    if (!--s->refs) free(s);
}
static void cleanup_done(addon_state *s) {
    if (s->closing && !s->wait_count && s->hook) {
        napi_async_cleanup_hook_handle hook = s->hook;
        s->hook = NULL;
        checked(napi_remove_async_cleanup_hook(hook));
        drop_state(s);
    }
}
static void drop_peer(binding_peer *p) {
    if (--p->refs) return;
    addon_state *s = p->state;
    binding_peer **link = &s->peers;
    while (*link != p) link = &(*link)->next;
    *link = p->next;
    --s->peer_count;
    atomic_fetch_sub(&retained_peers, 1);
    free(p);
    drop_state(s);
}
static void destroy_wait(peer_wait *w) {
    binding_peer *p = w->owner;
    addon_state *s = p->state;
    /* The C peer ref survives deleting the last strong JS reference. */
    checked(napi_delete_reference(s->env, w->reference));
    --s->wait_count;
    atomic_fetch_sub(&retained_waits, 1);
    free(w);
    cleanup_done(s);
    drop_peer(p);
}
static void wait_closed(uv_handle_t *handle) {
    peer_wait *w = handle->data;
    if (!--w->closing) destroy_wait(w);
}
static void stop_wait(peer_wait *w) {
    if (w->initialized & 1) {
        /* Stop synchronously before closePeer may close/reuse the watched FD. */
        if (uv_poll_stop(&w->poll)) ipc_fail(&w->owner->core, "ipc_wait_stop_unknown");
        ++w->closing;
        uv_close((uv_handle_t *)&w->poll, wait_closed);
    }
    if (w->initialized & 2) {
        if (uv_timer_stop(&w->timer)) ipc_fail(&w->owner->core, "ipc_wait_stop_unknown");
        ++w->closing;
        uv_close((uv_handle_t *)&w->timer, wait_closed);
    }
}
static void finish_wait(peer_wait *w, const char *code, unsigned ready, int settle) {
    if (w->done) return;
    w->done = 1;
    binding_peer *p = w->owner;
    if (p->armed == w) p->armed = NULL;
    stop_wait(w);
    if (p->core.error && !strcmp(p->core.error, "ipc_wait_stop_unknown"))
        code = p->core.error;
    if (settle) {
        napi_env env = p->state->env;
        napi_handle_scope scope;
        checked(napi_open_handle_scope(env, &scope));
        napi_value value;
        if (code) {
            napi_value text;
            checked(napi_create_string_utf8(env, code, NAPI_AUTO_LENGTH, &text));
            checked(napi_create_error(env, text, text, &value));
            checked(napi_reject_deferred(env, w->deferred, value));
        } else {
            checked(napi_create_uint32(env, ready, &value));
            checked(napi_resolve_deferred(env, w->deferred, value));
        }
        checked(napi_close_handle_scope(env, scope));
    }
    /* Zero-init failure has no libuv close callbacks to retain this context. */
    if (!w->closing) destroy_wait(w);
}
static void wait_timeout(uv_timer_t *timer) {
    peer_wait *w = timer->data;
    int64_t now = ipc_now();
    if (now < 0) {
        ipc_fail(&w->owner->core, "ipc_clock");
        finish_wait(w, "ipc_clock", 0, 1);
    } else if (now >= w->deadline) {
        ipc_fail(&w->owner->core, "ipc_wait_timeout");
        finish_wait(w, "ipc_wait_timeout", 0, 1);
    } else {
        /* libuv's cached millisecond clock may fire early. Same absolute bound. */
        uint64_t delay = (uint64_t)(w->deadline - now + 999999) / 1000000;
        if (uv_timer_start(&w->timer, wait_timeout, delay, 0)) {
            ipc_fail(&w->owner->core, "ipc_wait_timer_unknown");
            finish_wait(w, "ipc_wait_timer_unknown", 0, 1);
        }
    }
}
static void wait_ready(uv_poll_t *poller, int status, int events) {
    peer_wait *w = poller->data;
    if (w->done) return;
    ipc_peer *p = &w->owner->core;
    const char *code = NULL;
    unsigned ready = (unsigned)events & w->mask;
    if (ipc_live(p)) code = p->error;
    else {
        int64_t now = ipc_now();
        if (now < 0 || now >= w->deadline) {
            ipc_fail(p, "ipc_wait_timeout"); code = p->error;
        } else if (status < 0 || !ready || ((unsigned)events & ~(1u|2u))) {
            ipc_fail(p, "ipc_wait_readiness_unknown"); code = p->error;
        }
    }
    finish_wait(w, code, ready, 1);
}
static napi_value wait_peer(napi_env env, napi_callback_info info) {
    napi_value a[3], promise;
    uint32_t mask; int64_t deadline;
    if (arguments(env, info, 3, a) || integer(env, a[1], 3, &mask) || !mask
        || bigint(env, a[2], &deadline)) return error(env, "ipc_argument");
    ipc_peer *core = peer(env, a[0]);
    if (!core) return error(env, "ipc_peer_argument");
    binding_peer *p = (binding_peer *)core;
    addon_state *s = p->state;
    if (s->closing) return error(env, "ipc_environment_closed");
    if (p->armed) return error(env, "ipc_wait_armed");
    if (ipc_live(core)) return error(env, core->error);
    int64_t now = ipc_now();
    if (now < 0 || deadline <= now || deadline > core->deadline) {
        ipc_fail(core, "ipc_wait_deadline"); return error(env, core->error);
    }
    if (!reserve_metadata(&retained_waits, IPC_MAX_WAITS)) return error(env, "ipc_wait_capacity");
    peer_wait *w = calloc(1, sizeof(*w));
    if (!w) {atomic_fetch_sub(&retained_waits, 1); return error(env, "ipc_allocation");}
    if (napi_create_promise(env, &w->deferred, &promise) != napi_ok) {
        free(w); atomic_fetch_sub(&retained_waits, 1); return error(env, "ipc_wait_promise");
    }
    if (napi_create_reference(env, a[0], 1, &w->reference) != napi_ok) {
        free(w); atomic_fetch_sub(&retained_waits, 1); return error(env, "ipc_wait_reference");
    }
    w->owner = p; w->mask = mask; w->deadline = deadline;
    ++p->refs; ++s->wait_count; p->armed = w;
    int result = uv_poll_init(s->loop, &w->poll, core->fd);
    if (!result) {
        w->initialized |= 1; w->poll.data = w;
        result = uv_timer_init(s->loop, &w->timer);
    }
    if (!result) {
        w->initialized |= 2; w->timer.data = w;
        /* Refresh libuv's cached clock; the timer never defines our deadline. */
        uv_update_time(s->loop);
        uint64_t delay = (uint64_t)(deadline - now + 999999) / 1000000;
        result = uv_timer_start(&w->timer, wait_timeout, delay, 0);
        if (!result) result = uv_poll_start(&w->poll, (int)mask, wait_ready);
    }
    if (result) {
        ipc_fail(core, "ipc_wait_setup_unknown");
        finish_wait(w, "ipc_wait_setup_unknown", 0, 1);
    }
    return promise;
}
static napi_value cancel_wait(napi_env env, napi_callback_info info) {
    napi_value a[1], value;
    if (arguments(env, info, 1, a)) return error(env, "ipc_argument");
    ipc_peer *core = peer(env, a[0]);
    if (!core) return error(env, "ipc_peer_argument");
    binding_peer *p = (binding_peer *)core;
    int cancelled = p->armed != NULL;
    if (cancelled) finish_wait(p->armed, "ipc_wait_cancelled", 0, 1);
    if (napi_get_boolean(env, cancelled, &value) != napi_ok) return error(env, "ipc_result");
    return value;
}
static void cleanup_environment(napi_async_cleanup_hook_handle hook, void *data) {
    addon_state *s = data;
    (void)hook;
    s->closing = 1;
    for (binding_peer *p = s->peers; p; p = p->next) {
        if (p->armed) finish_wait(p->armed, NULL, 0, 0);
        (void)ipc_close(&p->core); /* Teardown has no JS outcome; OS process wait is external. */
    }
    cleanup_done(s);
}
static void finalize(napi_env env, void *data, void *hint) {
    (void)env; (void)hint;
    binding_peer *p = data;
    /* A strong reference forbids ordinary GC while a wait owns uv handles. */
    (void)ipc_close(&p->core);
    drop_peer(p);
}
static void finalize_state(napi_env env, void *data, void *hint) {
    (void)env; (void)hint;
    drop_state(data);
}
static int init_readiness(napi_env env) {
    addon_state *s = calloc(1, sizeof(*s));
    if (!s) return -1;
    s->env = env; s->refs = 1;
    if (napi_get_uv_event_loop(env, &s->loop) != napi_ok
        || napi_set_instance_data(env, s, finalize_state, NULL) != napi_ok) {
        free(s); return -1;
    }
    if (napi_add_async_cleanup_hook(env, cleanup_environment, s, &s->hook) != napi_ok)
        return -1; /* Instance finalizer retains the sole reference. */
    ++s->refs; /* Async hook owns state until all uv_close callbacks drain. */
    return 0;
}
#endif
