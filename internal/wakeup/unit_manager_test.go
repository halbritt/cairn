package wakeup

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestStopRechecksCleanupAfterSystemdStopError(t *testing.T) {
	for _, state := range []string{"collected", "running", "unreadable"} {
		t.Run(state, func(t *testing.T) {
			dir := t.TempDir()
			// systemd may collect a deadline-expired unit between show and stop.
			// Exercise the command boundary, including an unsuccessful stop that
			// does not establish cleanup and a failed subsequent observation.
			script := `#!/bin/sh
if [ "$2" = stop ]; then
  touch "$CAIRN_STOP_TEST_DIR/stopped"
  exit 5
fi
if [ ! -e "$CAIRN_STOP_TEST_DIR/stopped" ]; then
  printf 'LoadState=loaded\nActiveState=active\nControlGroup=\n'
elif [ "$CAIRN_STOP_TEST_STATE" = collected ]; then
  printf 'LoadState=not-found\nActiveState=inactive\nControlGroup=\n'
elif [ "$CAIRN_STOP_TEST_STATE" = running ]; then
  printf 'LoadState=loaded\nActiveState=active\nControlGroup=\n'
else
  exit 1
fi
`
			if err := os.WriteFile(filepath.Join(dir, "systemctl"), []byte(script), 0700); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
			t.Setenv("CAIRN_STOP_TEST_DIR", dir)
			t.Setenv("CAIRN_STOP_TEST_STATE", state)
			err := (UnitManager{}).stop(context.Background(), "test-attempt")
			if state == "collected" {
				if err != nil {
					t.Fatalf("confirmed collected unit stranded cleanup: %v", err)
				}
			} else if err == nil || !strings.Contains(err.Error(), "stop worker unit") {
				t.Fatalf("unconfirmed cleanup lost stop failure: %v", err)
			}
		})
	}
}

func TestActiveHandlesNotFoundOnNonzeroSystemctlExit(t *testing.T) {
	dir := t.TempDir()
	script := `#!/bin/sh
printf 'LoadState=not-found\nActiveState=inactive\nControlGroup=\n'
exit 1
`
	if err := os.WriteFile(filepath.Join(dir, "systemctl"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	active, err := (UnitManager{}).active(context.Background(), "test-attempt")
	if err != nil {
		t.Fatalf("expected nil error on LoadState=not-found with nonzero exit, got: %v", err)
	}
	if active {
		t.Fatal("expected inactive (false)")
	}
}

func TestActivePreservesContextTimeoutEvenIfOutputHasNotFound(t *testing.T) {
	dir := t.TempDir()
	script := `#!/bin/sh
printf 'LoadState=not-found\nActiveState=inactive\nControlGroup=\n'
exec sleep 1
`
	if err := os.WriteFile(filepath.Join(dir, "systemctl"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	_, err := (UnitManager{}).active(ctx, "test-attempt")
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected context deadline exceeded error, got: %v", err)
	}
}

func TestLeaseRenewalShutdownDoesNotBecomeFailure(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	entered := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		done <- renewLease(ctx, time.Millisecond, func(renewal context.Context) error {
			close(entered)
			<-renewal.Done()
			return renewal.Err()
		})
	}()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("renewal did not start")
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("normal shutdown became lease failure: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("renewal did not stop")
	}
}

func TestLeaseRenewalPreservesIndependentFailures(t *testing.T) {
	transport := errors.New("transport failure")
	for _, failure := range []error{transport, context.DeadlineExceeded} {
		for _, shutdown := range []bool{false, true} {
			ctx, cancel := context.WithCancel(context.Background())
			err := renewLease(ctx, time.Millisecond, func(context.Context) error {
				if shutdown {
					cancel()
				}
				return failure
			})
			cancel()
			if !errors.Is(err, failure) {
				t.Fatalf("renewal failure lost during shutdown=%v: %v", shutdown, err)
			}
		}
	}
}

func TestActivePreservesCommandErrors(t *testing.T) {
	for _, output := range []string{"", "LoadState=loaded\\nActiveState=inactive\\n"} {
		t.Run(output, func(t *testing.T) {
			dir := t.TempDir()
			if err := os.WriteFile(filepath.Join(dir, "systemctl"), []byte("#!/bin/sh\nprintf '"+output+"'\nexit 1\n"), 0700); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", dir)
			_, err := (UnitManager{}).active(context.Background(), "test-attempt")
			var exitErr *exec.ExitError
			if !errors.As(err, &exitErr) {
				t.Fatalf("expected command exit error, got %v", err)
			}
		})
	}
	t.Run("missing executable", func(t *testing.T) {
		t.Setenv("PATH", t.TempDir())
		_, err := (UnitManager{}).active(context.Background(), "test-attempt")
		if !errors.Is(err, exec.ErrNotFound) {
			t.Fatalf("expected missing executable error, got %v", err)
		}
	})
}
