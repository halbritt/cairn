/* Loopback-only network interposer for the OpenCode admission harness.
   Built at runtime by isolate.sh; applied via LD_PRELOAD to dynamically linked
   children. Policy:
   - connect/sendto/sendmsg to non-loopback IPv4/IPv6 -> ENETUNREACH;
   - any DNS transport (destination port 53, UDP or TCP, loopback included)
     -> ENETUNREACH, so a host resolver service cannot forward queries.
   Verified by isolation-negative-tests.sh before fixtures start. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <netinet/in.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <netdb.h>
#include <arpa/inet.h>

static int unix_resolver_path(const struct sockaddr_un *un) {
    const char *p = un->sun_path;
    return p && (strncmp(p, "/run/systemd/resolve", sizeof("/run/systemd/resolve") - 1) == 0
                 || strncmp(p, "/var/run/systemd/resolve", sizeof("/var/run/systemd/resolve") - 1) == 0);
}

static int (*real_connect)(int, const struct sockaddr *, socklen_t);
static int (*real_getaddrinfo)(const char *, const char *, const struct addrinfo *, struct addrinfo **);
static ssize_t (*real_sendto)(int, const void *, size_t, int, const struct sockaddr *, socklen_t);
static ssize_t (*real_sendmsg)(int, const struct msghdr *, int);
static int (*real_sendmmsg)(int, struct mmsghdr *, unsigned int, int);

static int (*find_real(int id))(void) {
    void *out = NULL;
    switch (id) {
        case 0: out = dlsym(RTLD_NEXT, "connect"); break;
        case 1: out = dlsym(RTLD_NEXT, "sendto"); break;
        case 2: out = dlsym(RTLD_NEXT, "sendmsg"); break;
        case 3: out = dlsym(RTLD_NEXT, "sendmmsg"); break;
    }
    return (int (*)(void))out;
}

static int blocked(const struct sockaddr *addr, socklen_t len) {
    if (!addr) return 0;
    if (addr->sa_family == AF_UNIX && len >= sizeof(sa_family_t)) {
        const struct sockaddr_un *un = (const struct sockaddr_un *)addr;
        if (un->sun_path[0] != '\0' && unix_resolver_path(un)) return 1;
        return 0;
    }
    if (addr->sa_family == AF_INET && len >= sizeof(struct sockaddr_in)) {
        const struct sockaddr_in *in = (const struct sockaddr_in *)addr;
        const unsigned char *b = (const unsigned char *)&in->sin_addr.s_addr;
        if (b[0] != 127) return 1;
        if (ntohs(in->sin_port) == 53) return 1;
        return 0;
    }
    if (addr->sa_family == AF_INET6 && len >= sizeof(struct sockaddr_in6)) {
        const struct sockaddr_in6 *in6 = (const struct sockaddr_in6 *)addr;
        const unsigned char *b = in6->sin6_addr.s6_addr;
        int v4mapped = b[0] == 0 && b[1] == 0 && b[2] == 0 && b[3] == 0 && b[4] == 0
            && b[5] == 0 && b[6] == 0 && b[7] == 0 && b[8] == 0 && b[9] == 0
            && b[10] == 0xff && b[11] == 0xff;
        if (v4mapped) {
            if (b[12] != 127) return 1;
            if (ntohs(in6->sin6_port) == 53) return 1;
            return 0;
        }
        if (b[0] == 0 && b[15] == 1) {
            int loopback = 1;
            for (int i = 1; i < 15; i++)
                if (b[i]) { loopback = 0; break; }
            if (loopback && ntohs(in6->sin6_port) == 53) return 1;
            if (loopback) return 0;
        }
        return 1;
    }
    return 0;
}

/* glibc's internal resolver bypasses the PLT, so connect/send* interposition
   cannot see it. Applications resolve names through getaddrinfo, which they DO
   call via the PLT: allow only numeric literals and localhost here. */
int getaddrinfo(const char *node, const char *service,
                const struct addrinfo *hints, struct addrinfo **res) {
    if (!real_getaddrinfo) {
        *(void **)(&real_getaddrinfo) = dlsym(RTLD_NEXT, "getaddrinfo");
        if (!real_getaddrinfo) return EAI_FAIL;
    }
    if (!node) return real_getaddrinfo(node, service, hints, res);
    struct in_addr a4;
    struct in6_addr a6;
    if (inet_pton(AF_INET, node, &a4) == 1 || inet_pton(AF_INET6, node, &a6) == 1)
        return real_getaddrinfo(node, service, hints, res);
    if (strcmp(node, "localhost") == 0)
        return real_getaddrinfo("127.0.0.1", service, hints, res);
    return EAI_NONAME;
}

int connect(int fd, const struct sockaddr *addr, socklen_t len) {
    if (!real_connect) {
        *(void **)(&real_connect) = dlsym(RTLD_NEXT, "connect");
        if (!real_connect) { errno = ENOSYS; return -1; }
    }
    if (blocked(addr, len)) { errno = ENETUNREACH; return -1; }
    return real_connect(fd, addr, len);
}

ssize_t sendto(int fd, const void *buf, size_t n, int flags,
               const struct sockaddr *addr, socklen_t len) {
    if (!real_sendto) {
        *(void **)(&real_sendto) = dlsym(RTLD_NEXT, "sendto");
        if (!real_sendto) { errno = ENOSYS; return -1; }
    }
    if (blocked(addr, len)) { errno = ENETUNREACH; return -1; }
    return real_sendto(fd, buf, n, flags, addr, len);
}

int sendmmsg(int fd, struct mmsghdr *msgvec, unsigned int vlen, int flags) {
    if (!real_sendmmsg) {
        *(void **)(&real_sendmmsg) = dlsym(RTLD_NEXT, "sendmmsg");
        if (!real_sendmmsg) { errno = ENOSYS; return -1; }
    }
    for (unsigned int i = 0; i < vlen; i++) {
        struct msghdr *msg = &msgvec[i].msg_hdr;
        if (msg && msg->msg_name && blocked((const struct sockaddr *)msg->msg_name, msg->msg_namelen)) {
            errno = ENETUNREACH;
            return i > 0 ? (int)i : -1;
        }
    }
    return real_sendmmsg(fd, msgvec, vlen, flags);
}

ssize_t sendmsg(int fd, const struct msghdr *msg, int flags) {
    if (!real_sendmsg) {
        *(void **)(&real_sendmsg) = dlsym(RTLD_NEXT, "sendmsg");
        if (!real_sendmsg) { errno = ENOSYS; return -1; }
    }
    if (msg && msg->msg_name && blocked((const struct sockaddr *)msg->msg_name, msg->msg_namelen)) {
        errno = ENETUNREACH;
        return -1;
    }
    return real_sendmsg(fd, msg, flags);
}
