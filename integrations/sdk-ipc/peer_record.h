#ifndef CAIRN_PEER_RECORD_H
#define CAIRN_PEER_RECORD_H
#include "peer_identity.h"
/* -1 means known would-block; -2 is terminal, never replay. */
static int ipc_send(ipc_peer *p, const unsigned char *bytes, size_t size) {
    if (ipc_live(p)) return -2;
    ssize_t n = send(p->fd, bytes, size, MSG_DONTWAIT | MSG_NOSIGNAL);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return -1;
    if (n < 0 && errno == EINTR) return -1; /* send reports no transmission. */
    if (n != (ssize_t)size) return ipc_fail(p, "ipc_send_unknown");
    return (int)n; /* Socket receipt only; never native write acceptance. */
}
static int ipc_receive(ipc_peer *p, unsigned char *bytes, size_t size) {
    if (ipc_live(p) || ipc_options(p->fd)) return ipc_fail(p, "ipc_receive_boundary");
    union {
        struct cmsghdr aligned;
        unsigned char bytes[CMSG_SPACE(sizeof(struct ucred)) + CMSG_SPACE(16 * sizeof(int))];
    } control;
    memset(&control, 0, sizeof(control));
    struct iovec io = {.iov_base = bytes, .iov_len = size};
    struct msghdr msg = {.msg_iov = &io, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes)};
    ssize_t n = recvmsg(p->fd, &msg, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return -1;
    if (n < 0) return ipc_fail(p, "ipc_receive_unknown");
    int bad = (n < 32 || n > (ssize_t)size
        || (msg.msg_flags & ~(MSG_EOR | MSG_CMSG_CLOEXEC)) != 0);
    int credentials = 0, close_unknown = 0;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
        size_t left = (size_t)((unsigned char *)control.bytes + msg.msg_controllen - (unsigned char *)c);
        if (c->cmsg_len < CMSG_LEN(0) || c->cmsg_len > left) {bad = 1; break;}
        size_t payload = c->cmsg_len - CMSG_LEN(0);
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS) {
            bad = 1;
            if (payload % sizeof(int)) close_unknown = 1;
            for (size_t i = 0; i + sizeof(int) <= payload; i += sizeof(int)) {
                int received; memcpy(&received, CMSG_DATA(c) + i, sizeof(received));
                if (close(received)) close_unknown = 1;
            }
        } else if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_CREDENTIALS
                   && payload == sizeof(struct ucred)) {
            struct ucred who; memcpy(&who, CMSG_DATA(c), sizeof(who));
            if (++credentials != 1 || who.pid != p->pid || who.uid != p->uid || who.gid != p->gid)
                bad = 1;
        } else bad = 1;
    }
    if (credentials != 1 || ipc_live(p)) bad = 1;
    if (bad || close_unknown) {
        memset(bytes, 0, size);
        return ipc_fail(p, close_unknown ? "ipc_rights_close_unknown" : "ipc_record_refused");
    }
    return (int)n;
}
static int ipc_exchange_live(ipc_peer *p, int64_t deadline) {
    if (ipc_live(p)) return -2;
    int64_t now = ipc_now();
    if (now < 0 || now >= deadline) return ipc_fail(p, "ipc_exchange_unknown");
    return 0;
}
static int ipc_ready(ipc_peer *p, short event, int64_t deadline) {
    for (;;) {
        if (ipc_exchange_live(p, deadline)) return -2;
        int64_t now = ipc_now();
        if (now < 0 || now >= deadline) return ipc_fail(p, "ipc_exchange_unknown");
        int timeout = (int)((deadline - now + 999999) / 1000000);
        struct pollfd fds[2] = {{.fd=p->fd, .events=event}, {.fd=p->pidfd, .events=POLLIN}};
        int n = poll(fds, 2, timeout);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 || fds[1].revents || (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)))
            return ipc_fail(p, "ipc_exchange_unknown");
        if (n && (fds[0].revents & event)) return ipc_exchange_live(p, deadline);
    }
}
static int ipc_exchange(ipc_peer *p, const unsigned char *request, size_t size,
                        unsigned char *reply, int64_t deadline) {
    if (deadline <= 0 || deadline > p->deadline) return ipc_fail(p, "ipc_exchange_deadline");
    int sent;
    do {
        if (ipc_ready(p, POLLOUT, deadline) || ipc_exchange_live(p, deadline)) return -2;
        sent = ipc_send(p, request, size);
        if (sent == -2) return -2; /* Preserve an existing operation/cleanup fault. */
        if (ipc_exchange_live(p, deadline)) return -2;
    } while (sent == -1);
    if (sent < 0) return -2;
    int received;
    do {
        if (ipc_ready(p, POLLIN, deadline) || ipc_exchange_live(p, deadline)) return -2;
        received = ipc_receive(p, reply, 544);
        if (received == -2) return -2;
        if (ipc_exchange_live(p, deadline)) {
            memset(reply, 0, 544);
            return -2;
        }
    } while (received == -1);
    return received;
}
#endif
