package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

type watchFailedWriter struct{}

func (watchFailedWriter) Write([]byte) (int, error) { return 0, errors.New("consumer closed") }

func TestWatchPersistsCursorOnlyAfterOutputAndResumes(t *testing.T) {
	root := t.TempDir()
	socket := filepath.Join(root, "api.sock")
	token := filepath.Join(root, "token")
	cursor := filepath.Join(root, "cursor")
	if err := os.WriteFile(token, []byte("fixture-only-token"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	seen := make(chan core.InboxWatchRequest, 3)
	server := http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req core.InboxWatchRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
			return
		}
		seen <- req
		if err := json.NewEncoder(w).Encode(response{Schema: "cairn.response/1", OK: true, Status: "OK", Data: core.InboxPage{Cursor: "next-cursor", Deliveries: []core.InboxArrival{}}}); err != nil {
			t.Error(err)
		}
	})}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })
	p := watchPlan{socket: socket, token: token, cursorFile: cursor, once: true}
	if err = p.execute(context.Background(), watchFailedWriter{}, io.Discard); err == nil {
		t.Fatal("write failure hidden")
	}
	if _, err = os.Stat(cursor); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("cursor advanced without output")
	}
	var out bytes.Buffer
	if err = p.execute(context.Background(), &out, io.Discard); err != nil {
		t.Fatal(err)
	}
	if err = p.execute(context.Background(), io.Discard, io.Discard); err != nil {
		t.Fatal(err)
	}
	for i := range 3 {
		req := <-seen
		if i < 2 && req.Cursor != "" || i == 2 && req.Cursor != "next-cursor" {
			t.Fatalf("request %d cursor %q", i, req.Cursor)
		}
	}
	info, err := os.Stat(cursor)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("cursor permissions: %v %v", info, err)
	}
}

func TestWatchOutputCancellationUnblocksFullPipe(t *testing.T) {
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	output, cleanup, err := watchOutputFile(ctx, writer)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	done := make(chan error, 1)
	go func() { _, err := output.Write(make([]byte, 1024*1024)); done <- err }()
	select {
	case err := <-done:
		t.Fatalf("pipe did not block: %v", err)
	case <-time.After(30 * time.Millisecond):
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled write succeeded")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("cancellation did not unblock full output pipe")
	}
}

type watchGateWriter struct {
	entered chan struct{}
	release chan struct{}
	once    sync.Once
}

func (w *watchGateWriter) Write(body []byte) (int, error) {
	w.once.Do(func() { close(w.entered); <-w.release })
	return len(body), nil
}

func TestWatchBackpressureLockAndRestart(t *testing.T) {
	root := t.TempDir()
	socket := filepath.Join(root, "api.sock")
	token := filepath.Join(root, "token")
	cursor := filepath.Join(root, "cursor")
	if err := os.WriteFile(token, []byte("fixture-token"), 0600); err != nil {
		t.Fatal(err)
	}
	requests := make(chan string, 10)
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
	defer cancel()
	var resumed atomic.Bool
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req core.InboxWatchRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
			return
		}
		requests <- req.Cursor
		next := "before-restart"
		if resumed.Load() {
			next = "after-restart"
		}
		if err := json.NewEncoder(w).Encode(response{Schema: "cairn.response/1", OK: true, Status: "OK", Data: core.InboxPage{Cursor: next, Deliveries: []core.InboxArrival{}}}); err != nil {
			t.Error(err)
		}
	})
	start := func() *http.Server {
		listener, err := net.Listen("unix", socket)
		if err != nil {
			t.Fatal(err)
		}
		server := &http.Server{Handler: handler}
		go server.Serve(listener)
		t.Cleanup(func() { server.Close() })
		return server
	}
	server := start()
	p := watchPlan{socket: socket, token: token, cursorFile: cursor}
	output := &watchGateWriter{entered: make(chan struct{}), release: make(chan struct{})}
	done := make(chan error, 1)
	go func() { done <- p.execute(ctx, output, io.Discard) }()
	select {
	case <-output.entered:
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	if got := <-requests; got != "" {
		t.Fatalf("initial cursor %q", got)
	}
	// A stalled downstream reader must not start another HTTP request or advance.
	select {
	case got := <-requests:
		t.Fatalf("read ahead while output blocked: %q", got)
	case <-time.After(50 * time.Millisecond):
	}
	if _, err := os.Stat(cursor); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("checkpoint preceded output")
	}
	if err := p.execute(ctx, io.Discard, io.Discard); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("second writer bypassed lock: %v", err)
	}
	if err := server.Close(); err != nil {
		t.Fatal(err)
	}
	close(output.release)
	// Leave the socket unavailable across at least one poll, then restart the API.
	time.Sleep(1200 * time.Millisecond)
	resumed.Store(true)
	start()
	select {
	case got := <-requests:
		if got != "before-restart" {
			t.Fatalf("reconnect cursor %q", got)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("watch exit: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("watch did not stop")
	}
}

func TestWatchEmptySessionDoesNotSelectBaseInbox(t *testing.T) {
	_, err := prepareWatch([]string{"--socket", "/fixture/socket", "--token-file", "/fixture/token", "--agent-id", "", "--execution-id", ""})
	if core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("empty explicit session fell back to base inbox: %v", err)
	}
}
