package localapi_test

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/halbritt/cairn/localapi"
)

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
