package memoryhook

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestCaptureExpiredCallerCannotStartSelector(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "started")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	observed := value.Mapping()
	_, _, err := captureCommand(ctx, "/bin/sh", []string{"-c", `printf started > "$MARKER"`}, nil, []string{"MARKER=" + marker}, dir, observed)
	if !captureTimedOut(err) {
		t.Fatalf("expired caller: %v", err)
	}
	if _, err := os.Stat(marker); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("selector ran after caller cancellation: %v", err)
	}
	if code := observed.Get("returncode"); code != nil && code.Kind != value.Null {
		t.Fatalf("a child was started: %#v", observed)
	}
}

func TestCaptureDeadlineCoversPipeRetainedAfterDirectChildExit(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "pipe-holder")
	// The direct selector exits, but a descendant retains its output pipes.
	// The fixture owns and cleans that descendant; production mirrors Source's
	// direct-child cleanup, without inventing process-group ownership.
	t.Cleanup(func() {
		raw, err := os.ReadFile(marker)
		if err != nil {
			return
		}
		pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
		if err != nil {
			t.Error(err)
			return
		}
		if err := syscall.Kill(pid, syscall.SIGKILL); err != nil && err != syscall.ESRCH {
			t.Error(err)
		}
	})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	observed := value.Mapping()
	started := time.Now()
	_, _, err := captureCommand(ctx, "/bin/sh", []string{"-c", `/bin/sleep 60 & printf '%s' "$!" > "$MARKER"; exit 0`}, nil, []string{"MARKER=" + marker}, dir, observed)
	if !captureTimedOut(err) {
		t.Fatalf("open descendant pipe bypassed deadline: %v", err)
	}
	if time.Since(started) > 5*time.Second {
		t.Fatal("capture waited for the descendant after its deadline")
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("fixture never reached pipe retention: %v", err)
	}
	if !value.SameText(observed.Get("outcome"), "timeout") || captureOffset(observed.Get("returncode")) != 0 {
		t.Fatalf("direct child exit hid the timeout: %#v", observed)
	}
}

func TestCaptureTimeoutRetainsPartialOutputAccounting(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	observed := value.Mapping()
	prefix := `{"partial":true}`
	_, _, err := captureCommand(ctx, "/bin/sh", []string{"-c", `printf '%s' "$PREFIX"; exec /bin/sleep 60`}, nil, []string{"PREFIX=" + prefix}, t.TempDir(), observed)
	if !captureTimedOut(err) {
		t.Fatalf("partial reply was accepted or deadline lost: %v", err)
	}
	if captureOffset(observed.Get("stdout_bytes")) != int64(len(prefix)) || observed.Get("first_stdout_ms") == nil {
		t.Fatalf("timeout discarded the pipe observation: %#v", observed)
	}
	if captureOffset(observed.Get("returncode")) != -int64(syscall.SIGKILL) {
		t.Fatalf("timed-out direct child was not reaped: %#v", observed)
	}
}
