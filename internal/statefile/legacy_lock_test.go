package statefile

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestLegacyLockAttemptsUncontendedInodeBeforeExpiredWait(t *testing.T) {
	path := filepath.Join(t.TempDir(), "session.lock")
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	called := false
	err := LegacyLock(ctx, path, 1500*time.Millisecond, func() error { called = true; return nil })
	if err != nil || !called {
		t.Fatalf("initial source flock attempt was lost: called=%v err=%v", called, err)
	}
}

func TestLegacyLockHostExpiryInterruptsOnlyItsContendedWait(t *testing.T) {
	path := filepath.Join(t.TempDir(), "session.lock")
	owner, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, 0664)
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	if err = syscall.Flock(int(owner.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	defer syscall.Flock(int(owner.Fd()), syscall.LOCK_UN)
	before, err := owner.Stat()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Millisecond)
	defer cancel()
	err = LegacyLock(ctx, path, 1500*time.Millisecond, func() error {
		t.Fatal("contended writer entered the critical section")
		return nil
	})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("lost host deadline: %v", err)
	}
	after, err := os.Stat(path)
	if err != nil || !os.SameFile(before, after) || before.Mode() != after.Mode() {
		t.Fatalf("changed the retained lock inode/mode: %v", err)
	}
	if err = syscall.Flock(int(owner.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	if err = LegacyLock(context.Background(), path, 1500*time.Millisecond, func() error { return nil }); err != nil {
		t.Fatalf("subsequent writer could not use the released inode: %v", err)
	}
}
