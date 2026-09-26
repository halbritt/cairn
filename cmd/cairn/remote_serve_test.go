package main

import (
	"context"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestRemoteListenerRequiresTLSOrExplicitLoopbackProxy(t *testing.T) {
	cases := []struct {
		address, cert, key string
		proxy              bool
	}{
		{"127.0.0.1:0", "", "", false}, {"0.0.0.0:0", "", "", true},
		{"localhost:0", "", "", true}, {":0", "", "", true},
		{"", "cert", "key", false}, {"", "", "", true},
		{"127.0.0.1:0", "cert", "", false}, {"127.0.0.1:0", "cert", "key", true},
	}
	for _, test := range cases {
		l, err := listenRemote(test.address, test.cert, test.key, test.proxy)
		if err == nil {
			if l != nil {
				_ = l.Close()
			}
			t.Errorf("unsafe listener accepted %+v", test)
		}
	}
	l, err := listenRemote("127.0.0.1:0", "", "", true)
	if err != nil {
		t.Fatal(err)
	}
	_ = l.Close()
	l, err = listenRemote("", "", "", false)
	if err != nil || l != nil {
		t.Fatal("local-only configuration changed")
	}
}

func TestAPIServersShutdownAllListeners(t *testing.T) {
	parent := t.TempDir()
	if err := os.Chmod(parent, 0700); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(parent, "api.sock")
	local, err := listenPrivateUnix(socket)
	if err != nil {
		t.Fatal(err)
	}
	remote, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(200) })
	go func() {
		done <- serveAPIs(ctx, []apiEndpoint{{listener: local, handler: handler}, {listener: remote, handler: handler}})
	}()
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("shutdown did not finish")
	}
	for _, endpoint := range []struct{ network, address string }{{"unix", socket}, {"tcp", remote.Addr().String()}} {
		conn, err := net.DialTimeout(endpoint.network, endpoint.address, time.Second)
		if err == nil {
			_ = conn.Close()
			t.Fatalf("listener remains active: %+v", endpoint)
		}
	}
}

func TestPrivateSocketRecoversStalePathWithoutReplacingLiveOwner(t *testing.T) {
	parent := t.TempDir()
	if err := os.Chmod(parent, 0700); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(parent, "api.sock")
	stale, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	stale.SetUnlinkOnClose(false)
	if err = os.Chmod(socket, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = listenPrivateUnix(socket); err == nil {
		t.Fatal("replaced a live legacy listener")
	}
	if err = stale.Close(); err != nil {
		t.Fatal(err)
	}
	listener, err := listenPrivateUnix(socket)
	if err != nil {
		t.Fatalf("did not reclaim stale socket: %v", err)
	}
	if _, err = listenPrivateUnix(socket); err == nil {
		t.Fatal("second listener bypassed lock")
	}
	conn, err := net.DialTimeout("unix", socket, time.Second)
	if err != nil {
		t.Fatalf("contender removed live socket: %v", err)
	}
	_ = conn.Close()
	if err = listener.Close(); err != nil {
		t.Fatal(err)
	}
	restarted, err := listenPrivateUnix(socket)
	if err != nil {
		t.Fatalf("lock not released: %v", err)
	}
	_ = restarted.Close()
	if err = os.WriteFile(socket, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = listenPrivateUnix(socket); err == nil {
		t.Fatal("removed ordinary file")
	}
	body, err := os.ReadFile(socket)
	if err != nil || string(body) != "keep" {
		t.Fatal("ordinary file changed")
	}
	if err = os.Remove(socket); err != nil {
		t.Fatal(err)
	}
	if err = os.Symlink(filepath.Join(parent, "missing"), socket); err != nil {
		t.Fatal(err)
	}
	if _, err = listenPrivateUnix(socket); err == nil {
		t.Fatal("followed socket symlink")
	}
}
