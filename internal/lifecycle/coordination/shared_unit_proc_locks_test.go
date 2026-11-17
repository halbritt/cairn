package coordination

import (
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

type procLocksFixture struct {
	t      *testing.T
	tables []string
	opened int
	closed int
	eof    int
}

func (f *procLocksFixture) open() (io.ReadCloser, error) {
	f.t.Helper()
	if f.opened == len(f.tables) {
		f.t.Fatal("lock reader exceeded expected complete observations")
	}
	body := f.tables[f.opened]
	f.opened++
	return &procLocksChunk{fixture: f, raw: []byte(body)}, nil
}

type procLocksChunk struct {
	fixture *procLocksFixture
	raw     []byte
}

func (c *procLocksChunk) Read(p []byte) (int, error) {
	if len(p) < 64*1024 {
		c.fixture.t.Fatalf("read buffer = %d, want at least 64KiB", len(p))
	}
	if len(c.raw) == 0 {
		c.fixture.eof++
		return 0, io.EOF
	}
	// Even a large request can return a short chunk without reaching EOF.
	n := copy(p[:min(len(p), 512)], c.raw)
	c.raw = c.raw[n:]
	return n, nil
}

func (c *procLocksChunk) Close() error { c.fixture.closed++; return nil }

func TestProcLockDrainReadsThroughEOF(t *testing.T) {
	unrelated := strings.Repeat("1: FLOCK ADVISORY WRITE 123 00:af:789 0 EOF\n", 220)
	selected := "2: FLOCK ADVISORY WRITE 123 00:c0:456 0 EOF\n"
	f := &procLocksFixture{t: t, tables: []string{unrelated + selected, selected}}
	r := &watchDrainRead{locks: map[string]string{"0:c0:456": "/selected.lock"}}
	if err := r.lockOwnersFrom(f.open); err == nil || !strings.Contains(err.Error(), "owner or waiter") {
		t.Fatalf("selected owner after short chunks was lost: %v", err)
	}
	if f.opened != 2 || f.closed != 2 || f.eof != 2 {
		t.Fatalf("incomplete observations: %+v", f)
	}
}

func TestProcLockDrainWaitsForConsecutiveEmptyObservations(t *testing.T) {
	owner := "1: FLOCK ADVISORY WRITE 123 00:c0:456 0 EOF\n"
	f := &procLocksFixture{t: t, tables: []string{owner, "", ""}}
	r := &watchDrainRead{locks: map[string]string{"0:c0:456": "/selected.lock"}}
	if err := r.lockOwnersFrom(f.open); err != nil {
		t.Fatal("two complete clear observations refused:", err)
	}
	if f.opened != 3 || f.closed != 3 || f.eof != 3 {
		t.Fatalf("clear result without consecutive complete observations: %+v", f)
	}
}

func TestProcLockDrainWiredRefusals(t *testing.T) {
	row := "1: FLOCK ADVISORY WRITE 123 00:c0:456 0 EOF\n"
	for _, tc := range []struct{ name, table, reason string }{
		{"duplicate", row + strings.Replace(row, "1:", "2:", 1), "duplicates"},
		{"missing-field", "1: FLOCK ADVISORY WRITE 123 00:c0:456 0\n", "malformed"},
		{"invalid-inode", strings.Replace(row, ":456", ":bad", 1), "malformed"},
		{"invalid-pid", strings.Replace(row, "123", "bad", 1), "malformed"},
		{"unknown-kind", strings.Replace(row, "FLOCK", "UNKNOWN", 1), "unknown"},
		{"invalid-mode", strings.Replace(row, "WRITE", "UNKNOWN", 1), "malformed"},
		{"partial-row", strings.TrimSuffix(row, "\n"), "incomplete"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			proc := t.TempDir()
			if err := os.WriteFile(filepath.Join(proc, "locks"), []byte(tc.table), 0600); err != nil {
				t.Fatal(err)
			}
			r := &watchDrainRead{cli: &CLI{ProcRoot: proc}, locks: map[string]string{"0:c0:456": "/selected.lock"}}
			if err := r.lockOwners(); err == nil || !strings.Contains(err.Error(), tc.reason) {
				t.Fatalf("wired reader did not refuse %s: %v", tc.reason, err)
			}
		})
	}
}

func TestProcLockDrainInodelessKernelRows(t *testing.T) {
	for _, tc := range []struct {
		row string
		ok  bool
	}{
		{"1: POSIX *NOINODE* WRITE 123:0 0 EOF\n", true},
		{"1: LEASE BREAKER UNLCK 123:0 0 EOF\n", true},
		{"1: -> DELEG BREAKING READ 123:0 0 EOF\n", true},
		{"1: POSIX *NOINODE* WRITE bad:0 0 EOF\n", false},
		{"1: FLOCK ADVISORY WRITE 123:0 0 EOF\n", false},
	} {
		f := &procLocksFixture{t: t, tables: []string{tc.row, tc.row}}
		r := &watchDrainRead{locks: map[string]string{"0:c0:456": "/selected.lock"}}
		if err := r.lockOwnersFrom(f.open); (err == nil) != tc.ok {
			t.Fatalf("inode-less row %q: %v", tc.row, err)
		}
	}
}

func procLocksOwnedFixture(t *testing.T) (*watchDrainRead, *LockLease, string) {
	t.Helper()
	dir := t.TempDir()
	lease, err := TryAcquireIntentLock(dir, "reader")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { mustRelease(t, lease) })
	r := &watchDrainRead{cli: &CLI{ProcRoot: "/proc"}, locks: map[string]string{}, facts: map[string]string{}}
	if err := r.lockFile(IntentLockPath(dir, "reader")); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(fmt.Sprintf("/proc/self/fdinfo/%d", lease.file.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.HasPrefix(line, "lock:") {
			return r, lease, strings.TrimSpace(strings.TrimPrefix(line, "lock:")) + "\n"
		}
	}
	t.Fatal("private owned fixture flock not visible on its descriptor")
	return nil, nil, ""
}

func TestProcLockBootstrapRequiresUniqueStableOwnedRow(t *testing.T) {
	for _, name := range []string{"unique", "missing", "duplicate", "waiter", "device-alias", "other-owner", "unstable", "unrelated-change", "other-selected-owner"} {
		t.Run(name, func(t *testing.T) {
			r, lease, own := procLocksOwnedFixture(t)
			inode, err := strconv.ParseUint(strings.Split(strings.Fields(own)[5], ":")[2], 10, 64)
			if err != nil {
				t.Fatal(err)
			}
			otherKey := fmt.Sprintf("0:c0:%d", inode^1)
			otherRow := fmt.Sprintf("8: FLOCK ADVISORY WRITE 123 00:c0:%d 0 EOF\n", inode^1)
			shifted := strings.Fields(own)
			shifted[0] = "999:"
			renumbered := strings.Join(shifted, " ") + "\n"
			before := maps.Clone(r.locks)
			tables, reason := []string{own, own}, ""
			switch name {
			case "missing":
				tables, reason = []string{"", ""}, "not uniquely observed"
			case "duplicate":
				tables, reason = []string{own + renumbered}, "duplicates"
			case "waiter":
				waiter := strings.Replace(own, " FLOCK", " -> FLOCK", 1)
				tables, reason = []string{own + waiter, own + waiter}, "another owner/waiter"
			case "device-alias", "other-owner":
				fields := strings.Fields(own)
				if name == "device-alias" {
					inode := strings.Split(fields[5], ":")[2]
					fields[5] = "ffff:ffff:" + inode
				} else {
					fields[4] = "-1"
				}
				body := strings.Join(fields, " ") + "\n"
				tables, reason = []string{body, body}, "another owner/waiter"
			case "unstable":
				tables, reason = []string{own, "", own, ""}, "unstable"
			case "unrelated-change":
				tables = []string{own, otherRow + renumbered}
			case "other-selected-owner":
				r.locks[otherKey] = "/other.lock"
				before = maps.Clone(r.locks)
				body := own + otherRow
				tables, reason = []string{body, body}, "owner or waiter"
			}
			f := &procLocksFixture{t: t, tables: tables}
			err = bootstrapLockOwnersFrom(r, lease, f.open)
			if (reason == "" && err != nil) || (reason != "" && (err == nil || !strings.Contains(err.Error(), reason))) {
				t.Fatalf("bootstrap %s: %v", name, err)
			}
			if f.opened != len(tables) || f.closed != f.opened || f.eof != f.opened || !maps.Equal(before, r.locks) {
				t.Fatalf("incomplete observations or mutated selection: opens=%d closes=%d EOF=%d locks=%v", f.opened, f.closed, f.eof, r.locks)
			}
		})
	}
}

type procLockReadError struct{ err, closeErr error }

func (r procLockReadError) Read([]byte) (int, error) { return 0, r.err }
func (r procLockReadError) Close() error             { return r.closeErr }

func TestProcLockDrainRefusesIncompleteObservations(t *testing.T) {
	for _, tc := range []struct {
		name string
		open procLocksOpen
	}{
		{"read-error", func() (io.ReadCloser, error) { return procLockReadError{err: errors.New("read failed")}, nil }},
		{"no-progress", func() (io.ReadCloser, error) { return procLockReadError{}, nil }},
		{"close-error", func() (io.ReadCloser, error) {
			return procLockReadError{err: io.EOF, closeErr: errors.New("close failed")}, nil
		}},
		{"oversize", func() (io.ReadCloser, error) {
			return io.NopCloser(strings.NewReader(strings.Repeat(" ", procLocksMaxBytes+1))), nil
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := &watchDrainRead{locks: map[string]string{}}
			if err := r.lockOwnersFrom(tc.open); err == nil {
				t.Fatal("incomplete observation became a clear lock table")
			}
		})
	}
}

func TestProcLockDrainAcceptsExactBoundOnlyAfterEOF(t *testing.T) {
	open := func() (io.ReadCloser, error) {
		return io.NopCloser(strings.NewReader(strings.Repeat(" ", procLocksMaxBytes-1) + "\n")), nil
	}
	if err := (&watchDrainRead{}).lockOwnersFrom(open); err != nil {
		t.Fatal("complete exact-bound empty table refused:", err)
	}
}
