package localapi_test

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func TestClientIdentifiesUnavailableAPIWithoutDisclosingPath(t *testing.T) {
	home := t.TempDir()
	token := filepath.Join(home, "token")
	if err := os.WriteFile(token, []byte("private-token-content-canary"), 0600); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(home, "private-socket-path-canary")
	client, err := localapi.NewClient(socket, token)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	err = client.Call(context.Background(), "version", struct{}{}, &struct{}{})
	if core.Code(err) != "API_CONNECTION_FAILED" || !errors.Is(err, syscall.ENOENT) {
		t.Fatalf("missing socket diagnosis: %v", err)
	}
	if strings.Contains(err.Error(), home) || strings.Contains(err.Error(), "private-token-content-canary") || !strings.Contains(err.Error(), "socket and service") {
		t.Fatalf("unsafe or unhelpful diagnosis: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := client.Call(ctx, "version", struct{}{}, &struct{}{}); !errors.Is(err, context.Canceled) {
		t.Fatalf("lost cancellation identity: %v", err)
	}
}

func TestClientIdentifiesMissingTokenWithoutDisclosingPath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "private-token-path-canary")
	client, err := localapi.NewClient("unused.sock", path)
	if client != nil || core.Code(err) != "CLIENT_SETUP_FAILED" || !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("missing token diagnosis: client=%v error=%v", client, err)
	}
	if strings.Contains(err.Error(), path) || !strings.Contains(err.Error(), "token file") {
		t.Fatalf("unsafe or unhelpful diagnosis: %v", err)
	}
}

func TestClientRejectsUnsafeTokenSources(t *testing.T) {
	for _, kind := range []string{"empty", "oversized", "public", "symlink", "fifo"} {
		t.Run(kind, func(t *testing.T) {
			home := t.TempDir()
			path := filepath.Join(home, "token")
			body := "synthetic-client-token"
			if kind == "empty" {
				body = " \n"
			}
			if kind == "oversized" {
				body = strings.Repeat("x", 513)
			}
			if kind == "fifo" {
				if err := syscall.Mkfifo(path, 0600); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := os.WriteFile(path, []byte(body), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if kind == "public" {
				if err := os.Chmod(path, 0644); err != nil {
					t.Fatal(err)
				}
			}
			if kind == "symlink" {
				link := filepath.Join(home, "link")
				if err := os.Symlink(path, link); err != nil {
					t.Fatal(err)
				}
				path = link
			}
			client, err := localapi.NewClient(filepath.Join(home, "absent.sock"), path)
			if err == nil {
				client.Close()
				t.Fatal("unsafe token accepted before contacting server")
			}
		})
	}
}
