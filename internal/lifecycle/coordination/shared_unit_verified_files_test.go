package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

func unitVerifiedDirectory(t *testing.T, base string) string {
	t.Helper()
	dir, err := os.MkdirTemp(base, ".cairn-verified-files-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(dir); err != nil {
			t.Error(err)
		}
	})
	return dir
}

func TestUnitVerifiedFilesRecheckSharedMapping(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	for _, base := range []string{home, filepath.Join(home, ".cache")} {
		for _, size := range []int{128, 2<<20 + 17} {
			for _, image := range []bool{false, true} {
				name := filepath.Base(base) + "/data"
				if image {
					name = filepath.Base(base) + "/image"
				}
				if size > 1<<20 {
					name += "-large"
				}
				t.Run(name, func(t *testing.T) {
					dir := unitVerifiedDirectory(t, base)
					path := filepath.Join(dir, "selected")
					writer, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0600)
					if err != nil {
						t.Fatal(err)
					}
					t.Cleanup(func() { _ = writer.Close() })
					original := bytes.Repeat([]byte{'a'}, size)
					if _, err := writer.Write(original); err != nil {
						t.Fatal(err)
					}
					mapped, err := unix.Mmap(int(writer.Fd()), 0, size, unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
					if err != nil {
						t.Fatal(err)
					}
					t.Cleanup(func() {
						if err := unix.Munmap(mapped); err != nil {
							t.Error(err)
						}
					})
					// Fault the writable page before capturing stat identity. A
					// later store needs neither write(2) nor a new page fault.
					mapped[0] = 'a'
					mode := os.FileMode(0400)
					if image {
						mode = 0500
					}
					if err := writer.Chmod(mode); err != nil {
						t.Fatal(err)
					}
					pass := newUnitVerifiedFiles(context.Background())
					defer pass.Close()
					if _, _, err := pass.Read(path, int64(size), image); err != nil {
						t.Fatal(err)
					}
					before, err := unitVerifiedPathStat(path)
					if err != nil {
						t.Fatal(err)
					}
					mapped[0] = 'b'
					after, err := unitVerifiedPathStat(path)
					if err != nil || !unitVerifiedSame(before, after) {
						t.Fatalf("filesystem cannot reproduce unchanged-stat shared store: %v", err)
					}
					if err := pass.Recheck(); err == nil || !strings.Contains(err.Error(), "verified file content changed") || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
						t.Fatalf("shared mapping needs actual byte-mutation refusal: %v", err)
					}
					var fs unix.Statfs_t
					if err := unix.Statfs(dir, &fs); err != nil {
						t.Fatal(err)
					}
					t.Logf("actual_fs_type=%x mode=%o size=%d unchanged_stat=true byte_mutation_refused=true", fs.Type, mode, size)
				})
			}
		}
	}
}

func TestUnitVerifiedFilesReuseBuildBytes(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	for _, base := range []string{home, filepath.Join(home, ".cache")} {
		t.Run(filepath.Base(base), func(t *testing.T) {
			dir := unitVerifiedDirectory(t, base)
			path := filepath.Join(dir, "selected.json")
			original := []byte(`{"selected":"original"}`)
			if err := os.WriteFile(path, original, 0600); err != nil {
				t.Fatal(err)
			}
			pass := newUnitVerifiedFiles(context.Background())
			defer pass.Close()
			want := sha256.Sum256(original)
			first, digest, err := pass.Read(path, 1024, false)
			if err != nil || string(first) != string(original) || digest != hex.EncodeToString(want[:]) {
				t.Fatalf("first verified read: %q %s %v", first, digest, err)
			}
			first[0] = '!'
			second, again, err := pass.Read(path, 1024, false)
			if err != nil || string(second) != string(original) || again != digest {
				t.Fatal("caller bytes altered the private verified observation")
			}
			if pass.hashReads != 1 {
				t.Fatalf("one build observation hashed the same file %d times", pass.hashReads)
			}
			entry := pass.files[path]
			if err := pass.Recheck(); err != nil || pass.hashReads != 2 {
				t.Fatalf("fresh byte readback did not run exactly once: %d %v", pass.hashReads, err)
			}
			if err := pass.Close(); err != nil {
				t.Fatal(err)
			}
			if _, err := entry.file.Stat(); !errors.Is(err, os.ErrClosed) {
				t.Fatalf("observation retained its descriptor: %v", err)
			}
			if err := pass.Recheck(); err == nil {
				t.Fatal("closed observation became an empty successful recheck")
			}
		})
	}
}

func TestUnitVerifiedFilesRestoredMtimeAndFreshPass(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	for _, base := range []string{home, filepath.Join(home, ".cache")} {
		t.Run(filepath.Base(base), func(t *testing.T) {
			path := filepath.Join(unitVerifiedDirectory(t, base), "selected")
			if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
				t.Fatal(err)
			}
			old := newUnitVerifiedFiles(context.Background())
			defer old.Close()
			_, first, err := old.Read(path, 1024, false)
			if err != nil {
				t.Fatal(err)
			}
			info, err := os.Stat(path)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, []byte("modified"), 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
				t.Fatal(err)
			}
			if err := old.Recheck(); err == nil || errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("same-size restored-mtime mutation survived: %v", err)
			}
			fresh := newUnitVerifiedFiles(context.Background())
			defer fresh.Close()
			raw, digest, err := fresh.Read(path, 1024, false)
			if err != nil || string(raw) != "modified" || digest == first || fresh.hashReads != 1 {
				t.Fatal("fresh pass reused another observation's data or digest")
			}
		})
	}
}

func TestUnitVerifiedFilesRefusalClosesDescriptors(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	for _, actor := range []string{"replace", "symlink", "hardlink", "parent-mode", "file-mode", "limit", "binary-policy", "missing-read", "cancel"} {
		t.Run(actor, func(t *testing.T) {
			dir := unitVerifiedDirectory(t, filepath.Join(home, ".cache"))
			path := filepath.Join(dir, "selected")
			if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			pass := newUnitVerifiedFiles(ctx)
			defer pass.Close()
			if _, _, err := pass.Read(path, 1024, false); err != nil {
				t.Fatal(err)
			}
			entry := pass.files[path]
			var err error
			switch actor {
			case "replace", "symlink":
				if err := os.Rename(path, path+"-original"); err != nil {
					t.Fatal(err)
				}
				if actor == "replace" {
					err = os.WriteFile(path, []byte("original"), 0600)
				} else {
					err = os.Symlink(path+"-original", path)
				}
			case "parent-mode":
				err = os.Chmod(dir, 0750)
			case "file-mode":
				err = os.Chmod(path, 0660)
			case "hardlink":
				err = os.Link(path, path+"-alias")
			case "cancel":
				cancel()
			}
			if err != nil {
				t.Fatal(err)
			}
			switch actor {
			case "limit":
				_, _, err = pass.Read(path, 1, false)
			case "binary-policy":
				_, _, err = pass.Read(path, -1, true)
			case "missing-read":
				_, _, err = pass.Read(path+"-missing", 1024, false)
			case "hardlink":
				_, _, err = pass.Read(path+"-alias", 1024, false)
			default:
				err = pass.Recheck()
			}
			if err == nil {
				t.Fatal("unsafe or canceled observation remained usable")
			}
			if _, err := entry.file.Stat(); !errors.Is(err, os.ErrClosed) {
				t.Fatalf("failed observation retained descriptor: %v", err)
			}
			if _, _, err := pass.Read(path, 1024, false); err == nil {
				t.Fatal("invalidated observation revived")
			}
		})
	}
}
