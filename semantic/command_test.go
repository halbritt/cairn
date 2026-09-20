package semantic

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

func script(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "worker")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body), 0700); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestCommandExcludesServerCredentialsAndRejectsInvalidResponses(t *testing.T) {
	t.Setenv("CAIRN_DATABASE_URL", "must-not-reach-worker")
	path := script(t, "test -z \"${CAIRN_DATABASE_URL:-}\" || exit 9\nprintf '%s' '{\"model_sha256\":\"test\",\"algorithm\":\"test/1\",\"scores\":[]}'\n")
	rank, err := Command(path)
	if err != nil {
		t.Fatal(err)
	}
	result, err := rank(context.Background(), core.SemanticRankRequest{Query: "test"})
	if err != nil || result.Algorithm != "test/1" {
		t.Fatalf("worker transport: %+v %v", result, err)
	}
	for _, test := range []struct{ body, reason string }{{"printf '{}{}'\n", "one JSON"}, {"exit 5\n", "worker failed"}} {
		rank, err = Command(script(t, test.body))
		if err != nil {
			t.Fatal(err)
		}
		if _, err = rank(context.Background(), core.SemanticRankRequest{}); err == nil || !strings.Contains(err.Error(), test.reason) {
			t.Fatalf("expected %s, got %v", test.reason, err)
		}
	}
}

func TestCommandEnforcesOutputLimit(t *testing.T) {
	for _, size := range []int{65536, 65537, 1 << 20} {
		t.Run(strconv.Itoa(size), func(t *testing.T) {
			const prefix = `{"model_sha256":"test","algorithm":"`
			const suffix = `","scores":[]}`
			algorithm := strings.Repeat("x", size-len(prefix)-len(suffix))
			response := prefix + algorithm + suffix
			rank, err := Command(script(t, "printf '%s' '"+response+"'\n"))
			if err != nil {
				t.Fatal(err)
			}
			result, err := rank(context.Background(), core.SemanticRankRequest{})
			if size == 65536 {
				if err != nil || result.Algorithm != algorithm {
					t.Fatalf("response at limit was not preserved: %v", err)
				}
			} else if err == nil || !strings.Contains(err.Error(), "worker failed") {
				// Pipe closure can surface either the writer error or producer SIGPIPE.
				t.Fatalf("oversized valid response was not refused by transport: %v", err)
			}
		})
	}
}

func TestCommandReapsWorkerGroupAfterWait(t *testing.T) {
	for _, test := range []struct {
		name        string
		tail        string
		wantFailure bool
	}{
		{"successful worker leaves no descendant", "printf '%s' '{\"model_sha256\":\"test\",\"algorithm\":\"test/1\",\"scores\":[]}'\n", false},
		{"failed worker still reaps its group", "exit 5\n", true},
	} {
		t.Run(test.name, func(t *testing.T) {
			pidfile := filepath.Join(t.TempDir(), "descendant")
			path := script(t, "</dev/null >/dev/null 2>&1 sleep 300 &\necho $! > '"+pidfile+"'\n"+test.tail)
			rank, err := Command(path)
			if err != nil {
				t.Fatal(err)
			}
			_, err = rank(context.Background(), core.SemanticRankRequest{})
			if test.wantFailure != (err != nil) {
				t.Fatalf("worker outcome: %v", err)
			}
			raw, err := os.ReadFile(pidfile)
			if err != nil {
				t.Fatal(err)
			}
			deadline := time.Now().Add(3 * time.Second)
			for {
				stat, readErr := os.ReadFile("/proc/" + strings.TrimSpace(string(raw)) + "/stat")
				if readErr != nil {
					if os.IsNotExist(readErr) {
						break
					}
					t.Fatal(readErr)
				}
				start, end := bytes.IndexByte(stat, '('), bytes.LastIndexByte(stat, ')')
				if start < 0 || end < 0 || string(stat[start+1:end]) != "sleep" {
					break
				}
				if time.Now().After(deadline) {
					t.Fatal("worker descendant survived the reaped process group")
				}
				time.Sleep(20 * time.Millisecond)
			}
		})
	}
	original := groupKill
	t.Cleanup(func() { groupKill = original })
	groupKill = func(pid int, signal syscall.Signal) error {
		return syscall.EPERM
	}
	rank, err := Command(script(t, "printf '%s' '{\"model_sha256\":\"test\",\"algorithm\":\"test/1\",\"scores\":[]}'\n"))
	if err != nil {
		t.Fatal(err)
	}
	_, err = rank(context.Background(), core.SemanticRankRequest{})
	if err == nil || !strings.Contains(err.Error(), "group cleanup failed") {
		t.Fatalf("unreapable group must surface: %v", err)
	}
}

func TestCommandCancellationReapsWorkerAndBusyRequestsDoNotQueue(t *testing.T) {
	ready := filepath.Join(t.TempDir(), "ready")
	quoted := "'" + strings.ReplaceAll(ready, "'", "'\\''") + "'"
	rank, err := Command(script(t, "printf ready > "+quoted+"\nexec sleep 30\n"))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := rank(ctx, core.SemanticRankRequest{}); done <- err }()
	deadline := time.Now().Add(3 * time.Second)
	for {
		if _, err := os.Stat(ready); err == nil {
			break
		} else if !os.IsNotExist(err) {
			t.Fatal(err)
		}
		if time.Now().After(deadline) {
			t.Fatal("worker did not signal startup")
		}
		time.Sleep(5 * time.Millisecond)
	}
	if _, err := rank(context.Background(), core.SemanticRankRequest{}); err == nil || !strings.Contains(err.Error(), "busy") {
		t.Fatalf("second worker did not refuse: %v", err)
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled worker succeeded")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("worker was not reaped after cancellation")
	}
}
