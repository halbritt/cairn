package main

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

func listenPrivateUnix(socket string) (net.Listener, error) {
	parent := filepath.Dir(socket)
	if err := os.MkdirAll(parent, 0700); err != nil {
		return nil, err
	}
	info, err := os.Stat(parent)
	if err != nil {
		return nil, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) || info.Mode().Perm()&0077 != 0 {
		return nil, invalid("socket directory must be owned by the current user and owner-only")
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		if errors.Is(err, syscall.EADDRINUSE) {
			return nil, invalid("API socket already exists; stop its owner or explicitly remove a confirmed stale socket")
		}
		return nil, err
	}
	if err = os.Chmod(socket, 0600); err != nil {
		_ = listener.Close()
		return nil, err
	}
	return listener, nil
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
