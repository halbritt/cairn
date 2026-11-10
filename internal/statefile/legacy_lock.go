package statefile

import (
	"context"
	"errors"
	"os"
	"syscall"
	"time"
)

// LegacyLock retains Python's append-open permissions and symlink-following
// policy, using the caller's established lock path. It attempts flock before
// testing a contended deadline; existing File locks keep their own policy.
func LegacyLock(ctx context.Context, path string, wait time.Duration, run func() error) error {
	lock, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0666)
	if err != nil {
		return err
	}
	defer lock.Close()
	limit := time.Now().Add(wait)
	if deadline, ok := ctx.Deadline(); ok && deadline.Before(limit) {
		limit = deadline
	}
	for {
		err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
			return run()
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			return err
		}
		remaining := time.Until(limit)
		if remaining <= 0 {
			return err
		}
		if remaining > 10*time.Millisecond {
			remaining = 10 * time.Millisecond
		}
		timer := time.NewTimer(remaining)
		select {
		case <-timer.C:
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		}
	}
}
