package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

type lockedStateFault struct {
	target     string
	temporary  *os.File
	writeErr   error
	syncErr    error
	closeErr   error
	syncCalls  int
	closeCalls int
	renames    int
}

// These serial tests replace only filesystem operations and restore them at cleanup.
func injectLockedStateFault(t *testing.T, target, fault string) *lockedStateFault {
	t.Helper()
	write, sync, close, rename := lockedStateWrite, lockedStateSync, lockedStateClose, lockedStateRename
	t.Cleanup(func() {
		lockedStateWrite, lockedStateSync, lockedStateClose, lockedStateRename = write, sync, close, rename
	})
	f := &lockedStateFault{target: target, writeErr: errors.New("fixture temporary write failed"),
		syncErr: errors.New("fixture temporary sync failed"), closeErr: errors.New("fixture temporary close failed")}
	lockedStateWrite = func(file *os.File, raw []byte) (int, error) {
		if filepath.Dir(file.Name()) != filepath.Dir(target) {
			return write(file, raw)
		}
		f.temporary = file
		switch fault {
		case "write":
			// A real partial write must never replace the prior durable state.
			n, err := write(file, raw[:1])
			if err != nil {
				return n, err
			}
			return n, f.writeErr
		case "write-close":
			return 0, f.writeErr
		default:
			return write(file, raw)
		}
	}
	lockedStateSync = func(file *os.File) error {
		if file == f.temporary {
			f.syncCalls++
			if fault == "sync" {
				return f.syncErr
			}
		}
		return sync(file)
	}
	lockedStateClose = func(file *os.File) error {
		err := close(file)
		if file == f.temporary {
			f.closeCalls++
			if fault == "write-close" {
				return errors.Join(err, f.closeErr)
			}
		}
		return err
	}
	lockedStateRename = func(old, next string) error {
		if next == target {
			f.renames++
		}
		return rename(old, next)
	}
	return f
}

func (f *lockedStateFault) sentinel(fault string) error {
	if fault == "sync" {
		return f.syncErr
	}
	return f.writeErr
}

func assertLockedStateLockReleased(t *testing.T, path string) {
	t.Helper()
	lock, err := os.OpenFile(path+".lock", os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatalf("state-file lock retained after update: %v", err)
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
}

func (f *lockedStateFault) assertNoPublication(t *testing.T, before os.FileInfo, prior []byte) {
	t.Helper()
	if f.renames != 0 {
		t.Errorf("failed temporary I/O attempted %d renames", f.renames)
	}
	if before == nil {
		if _, err := os.Lstat(f.target); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("failed save published a previously absent target: %v", err)
		}
	} else {
		raw, err := os.ReadFile(f.target)
		if err != nil || !bytes.Equal(raw, prior) {
			t.Errorf("failed save changed prior durable bytes: %q (err=%v)", raw, err)
		}
		after, err := os.Stat(f.target)
		if err != nil || !os.SameFile(before, after) {
			t.Errorf("failed save replaced prior durable inode: %v", err)
		}
	}
	if f.temporary == nil {
		t.Fatal("fault did not reach a real temporary file")
	}
	if _, err := f.temporary.Stat(); !errors.Is(err, os.ErrClosed) {
		t.Errorf("temporary descriptor is not closed: %v", err)
	}
	if f.closeCalls != 1 {
		t.Errorf("temporary close count=%d, want 1", f.closeCalls)
	}
	if _, err := os.Lstat(f.temporary.Name()); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("temporary path remains after failed save: %v", err)
	}
	entries, err := os.ReadDir(filepath.Dir(f.target))
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.Name() != filepath.Base(f.target) && entry.Name() != filepath.Base(f.target)+".lock" {
			t.Errorf("unexpected temporary remnant: %s", entry.Name())
		}
	}
	assertLockedStateLockReleased(t, f.target)
}

// CAIRN-331: temporary write/sync failures must stop publication and reach callers.
func TestLockedStateFileTemporaryIOFailurePreservesState(t *testing.T) {
	for _, existing := range []bool{true, false} {
		name := "absent"
		if existing {
			name = "existing"
		}
		for _, fault := range []string{"write", "sync", "write-close"} {
			for _, entry := range []string{"final", "checkpoint"} {
				t.Run(name+"/"+fault+"/"+entry, func(t *testing.T) {
					dir := t.TempDir()
					if err := os.Chmod(dir, 0700); err != nil {
						t.Fatal(err)
					}
					path := filepath.Join(dir, "state.json")
					prior := []byte("{\"schema\":\"cairn.test/1\", \"count\":7}\n")
					var before os.FileInfo
					if existing {
						if err := os.WriteFile(path, prior, 0600); err != nil {
							t.Fatal(err)
						}
						var err error
						before, err = os.Stat(path)
						if err != nil {
							t.Fatal(err)
						}
					}
					f := injectLockedStateFault(t, path, fault)
					store := LockedStateFile[testCounterState]{Path: path, TempPattern: ".cairn-fault-*",
						RequirePrivateDir: true, RequirePrivateFile: true, StrictJSON: true, NonBlocking: true,
						Default: func() testCounterState { return testCounterState{Schema: "cairn.test/1"} }}
					callbackErr := errors.New("fixture callback failure")
					var checkpointErr error
					err := store.Update(func(current testCounterState, save func(testCounterState) error) (testCounterState, bool, error) {
						next := testCounterState{Schema: "cairn.test/1", Count: 99}
						if entry == "checkpoint" {
							checkpointErr = save(next)
							return current, false, checkpointErr
						}
						return next, true, callbackErr
					})
					if !errors.Is(err, f.sentinel(fault)) {
						t.Errorf("Update lost temporary %s failure: %v", fault, err)
					}
					if entry == "checkpoint" && !errors.Is(checkpointErr, f.sentinel(fault)) {
						t.Errorf("intermediate save lost temporary %s failure: %v", fault, checkpointErr)
					}
					if entry == "final" && !errors.Is(err, callbackErr) {
						t.Errorf("final write lost joined callback failure: %v", err)
					}
					wantSync := 0
					if fault == "sync" {
						wantSync = 1
					}
					if f.syncCalls != wantSync {
						t.Errorf("temporary sync count=%d, want %d", f.syncCalls, wantSync)
					}
					f.assertNoPublication(t, before, prior)
				})
			}
		}
	}
}

func TestLockedStateFileDirectorySyncFailureKeepsPublishedState(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")
	if err := os.WriteFile(path, []byte(`{"schema":"cairn.test/1","count":7}`), 0600); err != nil {
		t.Fatal(err)
	}
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	sync := lockedStateSync
	t.Cleanup(func() { lockedStateSync = sync })
	failed := errors.New("fixture directory sync failed")
	lockedStateSync = func(file *os.File) error {
		if file.Name() == dir {
			return failed
		}
		return sync(file)
	}
	store := LockedStateFile[testCounterState]{Path: path}
	err = store.Update(func(current testCounterState, _ func(testCounterState) error) (testCounterState, bool, error) {
		return testCounterState{Schema: "cairn.test/1", Count: 99}, true, nil
	})
	if !errors.Is(err, failed) {
		t.Fatalf("directory sync failure was lost: %v", err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var published testCounterState
	if err := json.Unmarshal(raw, &published); err != nil || published.Count != 99 {
		t.Fatalf("post-rename failure hid published state: %q (err=%v)", raw, err)
	}
	after, err := os.Stat(path)
	if err != nil || os.SameFile(before, after) || after.Mode().Perm() != 0600 {
		t.Fatalf("expected owner-only replacement despite directory sync failure: %v", err)
	}
	assertLockedStateLockReleased(t, path)
}
