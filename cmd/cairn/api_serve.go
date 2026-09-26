package main

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

func listenPrivateUnix(socket string) (net.Listener, error) {
	parent := filepath.Dir(socket)
	if err := os.MkdirAll(parent, 0700); err != nil {
		return nil, err
	}
	info, err := os.Lstat(parent)
	if err != nil {
		return nil, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.IsDir() || stat.Uid != uint32(os.Geteuid()) || info.Mode().Perm()&0077 != 0 {
		return nil, invalid("socket directory must be owned by the current user and owner-only")
	}

	lock, err := os.OpenFile(socket+".lock", os.O_RDWR|os.O_CREATE|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0600)
	if err != nil {
		return nil, err
	}
	keepLock := false
	defer func() {
		if !keepLock {
			_ = lock.Close()
		}
	}()
	lockInfo, err := lock.Stat()
	if err != nil {
		return nil, err
	}
	lockStat, ok := lockInfo.Sys().(*syscall.Stat_t)
	if !ok || !lockInfo.Mode().IsRegular() || lockInfo.Mode().Perm()&0077 != 0 || lockStat.Uid != uint32(os.Geteuid()) {
		return nil, invalid("socket lock must be an owner-only regular file owned by the current user")
	}
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, invalid("API socket is already in use")
		}
		return nil, err
	}
	if err = removeStaleSocket(socket); err != nil {
		return nil, err
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		return nil, err
	}
	if err = os.Chmod(socket, 0600); err != nil {
		_ = listener.Close()
		return nil, err
	}
	keepLock = true
	return &privateUnixListener{Listener: listener, lock: lock}, nil
}

// Retain the lock file: unlinking it would let concurrent starts lock distinct
// inodes. A killed process releases the kernel lock while leaving the socket.
type privateUnixListener struct {
	net.Listener
	lock     *os.File
	once     sync.Once
	closeErr error
}

func (l *privateUnixListener) Close() error {
	l.once.Do(func() { l.closeErr = errors.Join(l.Listener.Close(), l.lock.Close()) })
	return l.closeErr
}

func removeStaleSocket(socket string) error {
	info, err := os.Lstat(socket)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || info.Mode()&os.ModeSocket == 0 || info.Mode().Perm()&0077 != 0 || stat.Uid != uint32(os.Geteuid()) {
		return invalid("existing socket path is not an owner-only socket owned by the current user")
	}
	conn, err := net.DialTimeout("unix", socket, 250*time.Millisecond)
	if err == nil {
		_ = conn.Close()
		return invalid("API socket is already in use")
	}
	if !errors.Is(err, syscall.ECONNREFUSED) {
		return invalid("cannot establish that the existing socket is stale")
	}
	current, err := os.Lstat(socket)
	if err != nil {
		return err
	}
	if !os.SameFile(info, current) {
		return invalid("socket changed during stale listener inspection")
	}
	return os.Remove(socket)
}

func listenRemote(address, certFile, keyFile string, proxy bool) (net.Listener, error) {
	if address == "" {
		if certFile != "" || keyFile != "" || proxy {
			return nil, invalid("TLS listener options require --listen")
		}
		return nil, nil
	}
	var tlsConfig *tls.Config
	if proxy {
		host, _, err := net.SplitHostPort(address)
		ip := net.ParseIP(host)
		if err != nil || ip == nil || !ip.IsLoopback() || certFile != "" || keyFile != "" {
			return nil, invalid("TLS-terminated proxy requires a literal loopback listener and no TLS certificate options")
		}
	} else {
		if certFile == "" || keyFile == "" {
			return nil, invalid("remote listener requires --tls-cert and --tls-key, or an explicit loopback --tls-terminated-proxy")
		}
		certificate, err := tls.LoadX509KeyPair(certFile, keyFile)
		if err != nil {
			return nil, err
		}
		tlsConfig = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return nil, err
	}
	if tlsConfig != nil {
		listener = tls.NewListener(listener, tlsConfig)
	}
	return listener, nil
}

type apiEndpoint struct {
	listener net.Listener
	handler  http.Handler
}

// All endpoints share one lifetime: a bind/serve failure or cancellation closes
// every listener before the database handler is released.
func serveAPIs(ctx context.Context, endpoints []apiEndpoint) error {
	results := make(chan error, len(endpoints))
	servers := make([]*http.Server, 0, len(endpoints))
	for _, endpoint := range endpoints {
		server := &http.Server{Handler: endpoint.handler, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 35 * time.Second, WriteTimeout: 35 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 * 1024}
		servers = append(servers, server)
		go func() { results <- server.Serve(endpoint.listener) }()
	}
	var first error
	remaining := len(servers)
	select {
	case <-ctx.Done():
	case first = <-results:
		remaining--
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	for _, server := range servers {
		if err := server.Shutdown(shutdownCtx); err != nil {
			_ = server.Close()
		}
	}
	for ; remaining > 0; remaining-- {
		err := <-results
		if first == nil && !errors.Is(err, http.ErrServerClosed) {
			first = err
		}
	}
	if errors.Is(first, http.ErrServerClosed) {
		return nil
	}
	return first
}
