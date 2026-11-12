package coordination

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSharedWatchDrainExistingLockIsObservedWithoutAcquisition(t *testing.T) {
	stateDir := t.TempDir()
	lease, err := TryAcquireIntentLock(stateDir, "existing")
	if err != nil {
		t.Fatal(err)
	}
	defer mustRelease(t, lease)
	scan := &watchDrainRead{cli: &CLI{ProcRoot: "/proc"}, locks: map[string]string{}, facts: map[string]string{}}
	path := IntentLockPath(stateDir, "existing")
	if err := scan.lockFile(path); err != nil {
		t.Fatal(err)
	}
	if err := scan.lockOwners(); err == nil || !strings.Contains(err.Error(), "D3") {
		t.Fatalf("held lock not observed: inodes=%v err=%v", scan.locks, err)
	}
	mustRelease(t, lease)
	if err := scan.lockOwners(); err != nil {
		t.Fatalf("released lock still held: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("inspection changed lock: %v %v", info, err)
	}
	scan.cli.ProcRoot = t.TempDir()
	if err := scan.lockOwners(); err == nil {
		t.Fatal("missing kernel lock visibility became clear")
	}
}

func TestSharedWatchDrainDeviceAliasRefusesAsUnknown(t *testing.T) {
	proc := t.TempDir()
	if err := os.WriteFile(filepath.Join(proc, "locks"), []byte("1: FLOCK ADVISORY WRITE 123 00:af:456 0 EOF\n"), 0600); err != nil {
		t.Fatal(err)
	}
	scan := &watchDrainRead{cli: &CLI{ProcRoot: proc}, locks: map[string]string{"0:c0:456": "/selected.lock"}}
	if err := scan.lockOwners(); err == nil || !strings.Contains(err.Error(), "ambiguous") {
		t.Fatalf("device alias was treated as free: %v", err)
	}
	for _, body := range []string{"1: -> FLOCK ADVISORY WRITE 123 00:c0:456 0 EOF\n", "1: FLOCK ADVISORY WRITE 123 00:c0:456 0 EOF\n"} {
		if err := os.WriteFile(filepath.Join(proc, "locks"), []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
		if err := scan.lockOwners(); err == nil || !strings.Contains(err.Error(), "owner or waiter") {
			t.Fatalf("owner/waiter became clear: %v", err)
		}
	}
	if err := os.WriteFile(filepath.Join(proc, "locks"), []byte(fmt.Sprintln("1: FLOCK ADVISORY WRITE 123 00:af:789 0 EOF")), 0600); err != nil {
		t.Fatal(err)
	}
	if err := scan.lockOwners(); err != nil {
		t.Fatalf("unrelated lock refused: %v", err)
	}
}
