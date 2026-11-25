package coordination

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Readback rechecks each directory under the strictest mode set it was first
// admitted with: an owned 0755 retained source directory (CAIRN-745) stays
// admissible, and nothing becomes looser than its first observation.
func TestCensusReadbackKeepsAdmittedDirectoryModes(t *testing.T) {
	newReader := func(root string) *watchCensusRead {
		return &watchCensusRead{catalog: WatchCensusCatalog{Root: root}, local: &watchDrainRead{},
			report: WatchUnitCensus{Facts: map[string]string{}}, files: map[string]string{},
			directories: map[string][]string{}, inodes: map[string]string{}, images: map[string]bool{}}
	}
	setup := func(t *testing.T, mode os.FileMode) (string, string) {
		root := t.TempDir()
		if err := os.Chmod(root, 0700); err != nil {
			t.Fatal(err)
		}
		dir := filepath.Join(root, "base")
		if err := os.Mkdir(dir, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(dir, mode); err != nil {
			t.Fatal(err)
		}
		return root, dir
	}
	refuses := func(t *testing.T, err error) {
		t.Helper()
		if err == nil || !strings.Contains(err.Error(), "census requires a private owned directory") {
			t.Fatalf("readback accepted a directory outside its admitted modes: %v", err)
		}
	}

	t.Run("retained 0755 source directory survives readback", func(t *testing.T) {
		root, dir := setup(t, 0755)
		r := newReader(root)
		if _, err := r.retainedSourceDirectory(dir); err != nil {
			t.Fatal(err)
		}
		if err := r.readback(context.Background()); err != nil {
			t.Fatalf("readback refused an admitted retained source directory: %v", err)
		}
	})
	t.Run("retained directory widened to 0775 is refused", func(t *testing.T) {
		root, dir := setup(t, 0755)
		r := newReader(root)
		if _, err := r.retainedSourceDirectory(dir); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(dir, 0775); err != nil {
			t.Fatal(err)
		}
		err := r.readback(context.Background())
		if err == nil || !strings.Contains(err.Error(), "census unsafe path/alias") {
			t.Fatalf("readback accepted a group-writable retained directory: %v", err)
		}
	})
	t.Run("private-only admission stays private at readback", func(t *testing.T) {
		root, dir := setup(t, 0700)
		r := newReader(root)
		if _, err := r.directory(dir); err != nil {
			t.Fatal(err)
		}
		if _, err := r.retainedSourceDirectory(dir); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(dir, 0755); err != nil {
			t.Fatal(err)
		}
		refuses(t, r.readback(context.Background()))
	})
	t.Run("plain census directory at 0755 is refused at readback", func(t *testing.T) {
		root, dir := setup(t, 0700)
		r := newReader(root)
		if _, err := r.directory(dir); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(dir, 0755); err != nil {
			t.Fatal(err)
		}
		refuses(t, r.readback(context.Background()))
	})
}
