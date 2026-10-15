package experimentaldebit

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

// Test-only ownership helper shared by ordinary injected tests and future PG tests.
// Limits bound this test's work/join, not campaign admission or provider runtime.
type fenceTestLease interface {
	exercise(context.Context) error
	release(context.Context) error
}
type fenceTestResult struct {
	Primary, Dispatch, Release error
	Joined                     bool
}

func runFenceCheck(parent context.Context, workLimit, cleanupLimit time.Duration,
	dispatch func(context.Context, func() error) error,
	acquire func(context.Context) (fenceTestLease, error)) (result fenceTestResult) {
	work, cancel := context.WithTimeout(parent, workLimit)
	defer cancel()
	entered, leave := make(chan struct{}), make(chan struct{})
	done := make(chan error, 1)
	var enterOnce, leaveOnce sync.Once
	var lease fenceTestLease
	// Install cleanup before starting work. No test fatal can bypass this owner.
	defer func() {
		leaveOnce.Do(func() { close(leave) })
		if result.Primary != nil {
			cancel()
		}
		cleanup, stop := context.WithTimeout(context.WithoutCancel(parent), cleanupLimit)
		defer stop()
		if !result.Joined {
			select {
			case result.Dispatch = <-done:
				result.Joined = true
			case <-cleanup.Done():
				if result.Primary == nil {
					result.Primary = errors.New("dispatch_join_unknown")
				}
			}
		}
		// release must run even after failed join or expired cleanup. The PG adapter
		// defers the actual pool release before attempting RESET with this context.
		if lease != nil {
			result.Release = lease.release(cleanup)
		}
		if result.Primary == nil {
			result.Primary = result.Dispatch
		}
		if result.Primary == nil {
			result.Primary = result.Release
		}
	}()
	go func() {
		var err error
		defer func() {
			if recover() != nil {
				err = errors.New("dispatch_panic_unknown")
			}
			done <- err
		}()
		err = dispatch(work, func() error {
			enterOnce.Do(func() { close(entered) })
			select {
			case <-leave:
				return nil
			case <-work.Done():
				return work.Err()
			}
		})
	}()
	select {
	case <-entered:
	case result.Dispatch = <-done:
		result.Joined = true
		result.Primary = result.Dispatch
		if result.Primary == nil {
			result.Primary = errors.New("dispatch_without_entry")
		}
		return
	case <-work.Done():
		result.Primary = work.Err()
		return
	}
	// A returned owned lease is retained even if acquisition also returns error.
	var err error
	lease, err = acquire(work)
	if err != nil {
		result.Primary = err
		return
	}
	if lease == nil {
		result.Primary = errors.New("fence_lease_unavailable")
		return
	}
	result.Primary = lease.exercise(work)
	return
}

type fakeFenceLease struct {
	exerciseErr, releaseErr error
	exercises, releases     int
}

func (f *fakeFenceLease) exercise(context.Context) error { f.exercises++; return f.exerciseErr }
func (f *fakeFenceLease) release(context.Context) error  { f.releases++; return f.releaseErr }

func TestFenceHandshakeJoinsEarlyDispatchWithoutAcquiring(t *testing.T) {
	primary := errors.New("injected early dispatch")
	acquired := false
	r := runFenceCheck(context.Background(), time.Second, time.Second,
		func(context.Context, func() error) error { return primary },
		func(context.Context) (fenceTestLease, error) { acquired = true; return nil, nil })
	if r.Primary != primary || r.Dispatch != primary || !r.Joined || acquired {
		t.Fatal("early completion/primary lost")
	}
}
func TestFenceOwnedCleanupPreservesPrimaryOnEverySetupExit(t *testing.T) {
	for _, stage := range []string{"success", "acquire", "acquire_with_lease", "statement_setup", "dispatch_completion", "release", "compound"} {
		t.Run(stage, func(t *testing.T) {
			primary := errors.New("injected primary")
			cleanup := errors.New("injected cleanup")
			lease := &fakeFenceLease{}
			var want error
			acquired := false
			completed := make(chan struct{})
			r := runFenceCheck(context.Background(), time.Second, time.Second,
				func(ctx context.Context, call func() error) error {
					defer close(completed)
					e := call()
					if stage == "dispatch_completion" {
						return primary
					}
					return e
				},
				func(context.Context) (fenceTestLease, error) {
					acquired = true
					switch stage {
					case "acquire":
						want = primary
						return nil, primary
					case "acquire_with_lease":
						want = primary
						return lease, primary
					case "statement_setup":
						want = primary
						lease.exerciseErr = primary
					case "compound":
						want = primary
						lease.exerciseErr = primary
						lease.releaseErr = cleanup
					case "release":
						want = cleanup
						lease.releaseErr = cleanup
					case "dispatch_completion":
						want = primary
					}
					return lease, nil
				})
			if !acquired || !r.Joined || r.Primary != want {
				t.Fatalf("lost primary/join: %#v", r)
			}
			select {
			case <-completed:
			default:
				t.Fatal("dispatch not completed")
			}
			expected := 1
			if stage == "acquire" {
				expected = 0
			}
			if lease.releases != expected {
				t.Fatal("owned lease release not exactly once")
			}
			if stage == "compound" && r.Release != cleanup {
				t.Fatal("cleanup hid primary")
			}
			if stage == "acquire_with_lease" && lease.exercises != 0 {
				t.Fatal("continued failed acquisition")
			}
		})
	}
}
func TestFenceHandshakeCancellationAndMissingCallbackAreBounded(t *testing.T) {
	t.Run("cancelled_entry", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		r := runFenceCheck(ctx, time.Second, time.Second,
			func(ctx context.Context, _ func() error) error { <-ctx.Done(); return ctx.Err() },
			func(context.Context) (fenceTestLease, error) { t.Fatal("acquire after no entry"); return nil, nil })
		if !r.Joined || !errors.Is(r.Primary, context.Canceled) {
			t.Fatal("cancellation not joined")
		}
	})
	t.Run("missing_entry", func(t *testing.T) {
		r := runFenceCheck(context.Background(), time.Second, time.Second,
			func(context.Context, func() error) error { return nil },
			func(context.Context) (fenceTestLease, error) { t.Fatal("acquire after no entry"); return nil, nil })
		if !r.Joined || r.Primary == nil || r.Primary.Error() != "dispatch_without_entry" {
			t.Fatal("missing entry lost")
		}
	})
}
