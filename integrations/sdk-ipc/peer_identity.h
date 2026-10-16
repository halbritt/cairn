#ifndef CAIRN_PEER_IDENTITY_H
#define CAIRN_PEER_IDENTITY_H
/* Linux-only candidate. No SDK/native child, message-body or authority policy. */
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/prctl.h>
#include <unistd.h>
#include <poll.h>
#include <fcntl.h>
#include <time.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <limits.h>

typedef struct {
    int fd, pidfd, failed;
    pid_t pid;
    uid_t uid;
    gid_t gid;
    int64_t deadline;
    const char *error;
} ipc_peer;

static int ipc_fail(ipc_peer *p, const char *error) {
    p->failed = 1; p->error = error; return -2;
}
static int64_t ipc_now(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static int ipc_close(ipc_peer *p) {
    int fd = p->fd, pidfd = p->pidfd, bad = 0;
    p->fd = p->pidfd = -1;
    if (fd >= 0 && close(fd)) bad = 1;
    if (pidfd >= 0 && close(pidfd)) bad = 1;
    return bad ? ipc_fail(p, "ipc_close_unknown") : 0;
}
static int ipc_live(ipc_peer *p) {
    if (p->failed || p->fd < 0 || p->pidfd < 0)
        return ipc_fail(p, "ipc_peer_unusable");
    if (prctl(PR_GET_DUMPABLE) != 0)
        return ipc_fail(p, "ipc_protection_changed");
    int64_t now = ipc_now();
    if (now < 0 || now >= p->deadline) return ipc_fail(p, "ipc_deadline");
    struct pollfd life = {.fd = p->pidfd, .events = POLLIN};
    if (poll(&life, 1, 0) != 0 || life.revents)
        return ipc_fail(p, "ipc_peer_lifetime_unknown");
    return 0;
}
static int ipc_options(int fd) {
    int value; socklen_t size;
    const int options[3] = {SO_TYPE, SO_DOMAIN, SO_PASSCRED};
    const int values[3] = {SOCK_SEQPACKET, AF_UNIX, 1};
    for (int i = 0; i < 3; ++i) {
        size = sizeof(value);
        if (getsockopt(fd, SOL_SOCKET, options[i], &value, &size)
            || size != sizeof(value) || value != values[i]) return -1;
    }
    return 0;
}
/* Fresh socketpair provenance check only: SO_PASSCRED sends can autobind names. */
static int ipc_socket(int fd) {
    if (ipc_options(fd)) return -1;
    struct sockaddr name;
    socklen_t size = sizeof(name);
    if (getsockname(fd, &name, &size) || size != sizeof(sa_family_t)
        || name.sa_family != AF_UNIX) return -1;
    size = sizeof(name);
    if (getpeername(fd, &name, &size) || size != sizeof(sa_family_t)
        || name.sa_family != AF_UNIX) return -1;
    return 0;
}
/* scratch is caller-reserved writable storage of exactly512B. */
static int ipc_open(ipc_peer *p, int fd, int pidfd, pid_t pid, uid_t uid,
                    gid_t gid, int64_t deadline, unsigned char *scratch) {
    p->fd = p->pidfd = -1; p->failed = 0; p->error = NULL;
    p->pid = pid; p->uid = uid; p->gid = gid; p->deadline = deadline;
    int64_t now = ipc_now();
    if (prctl(PR_GET_DUMPABLE) != 0 || now < 0 || deadline <= now
        || deadline - now > INT64_C(140000000000))
        return ipc_fail(p, "ipc_setup_boundary");
    p->fd = fcntl(fd, F_DUPFD_CLOEXEC, 3);
    p->pidfd = fcntl(pidfd, F_DUPFD_CLOEXEC, 3);
    if (p->fd < 0 || p->pidfd < 0 || ipc_socket(p->fd))
        return ipc_fail(p, "ipc_setup_descriptor");
    char path[64];
    int length = snprintf(path, sizeof(path), "/proc/self/fdinfo/%d", p->pidfd);
    if (length <= 0 || (size_t)length >= sizeof(path))
        return ipc_fail(p, "ipc_pidfd_identity");
    int info = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (info < 0) return ipc_fail(p, "ipc_pidfd_identity");
    size_t used = 0; int bad = 0;
    for (;;) {
        if (used == 512 || ipc_now() < 0 || ipc_now() >= deadline) {bad = 1; break;}
        ssize_t n = read(info, scratch + used, 512 - used);
        if (n < 0) {bad = 1; break;}
        if (!n) break;
        used += (size_t)n;
    }
    if (close(info)) bad = 1; /* Never retry an uncertain close. */
    if (bad || !used || used >= 512) return ipc_fail(p, "ipc_pidfd_identity");
    scratch[used] = 0;
    int found = 0; char *save, *line = strtok_r((char *)scratch, "\n", &save);
    while (line) {
        if (!strncmp(line, "Pid:\t", 5)) {
            char *end; errno = 0;
            long value = strtol(line + 5, &end, 10);
            if (++found != 1 || errno || *end || value != pid)
                return ipc_fail(p, "ipc_pidfd_identity");
        }
        line = strtok_r(NULL, "\n", &save);
    }
    memset(scratch, 0, 512);
    if (found != 1) return ipc_fail(p, "ipc_pidfd_identity");
    return ipc_live(p);
}
#endif
