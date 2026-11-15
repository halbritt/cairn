package coordination

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

func censusVerifiedRead(t *testing.T, root string) *watchCensusRead {
	t.Helper()
	pass := newUnitVerifiedFiles(context.Background())
	t.Cleanup(func() {
		if err := pass.Close(); err != nil {
			t.Error(err)
		}
	})
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	facts := map[string]string{}
	return &watchCensusRead{cli: cli, catalog: WatchCensusCatalog{Root: root},
		local: &watchDrainRead{cli: cli, verified: pass, facts: facts, tokenPaths: map[string]bool{}},
		files: map[string]string{}, images: map[string]bool{}, inodes: map[string]string{},
		directories: map[string][]string{}, report: WatchUnitCensus{Facts: facts}}
}

func TestUnitCensusPassReusesJSONAndImageBuildReads(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	root := unitVerifiedDirectory(t, filepath.Join(home, ".cache"))
	r := censusVerifiedRead(t, root)
	pin := censusTestJSON(t, filepath.Join(root, "selected.json"), map[string]any{"selected": "original"})
	image := WatchCensusPin{Path: filepath.Join(root, "image")}
	image.SHA256 = writePrivateFile(t, image.Path, []byte("actual image bytes"), 0500)
	for range 8 {
		if _, err := r.pin(pin, false); err != nil {
			t.Fatal(err)
		}
		if v, err := r.jsonFile(pin.Path); err != nil || !value.SameText(v.Get("selected"), "original") {
			t.Fatal("JSON view of the same verified file changed", err)
		}
		if _, err := r.pin(image, true); err != nil {
			t.Fatal(err)
		}
	}
	if r.local.verified.hashReads != 2 {
		t.Fatalf("two distinct files need two actual build hashes, got %d", r.local.verified.hashReads)
	}
	if err := r.readback(context.Background()); err != nil || r.local.verified.hashReads != 4 {
		t.Fatalf("fresh readback must hash both files again: %d %v", r.local.verified.hashReads, err)
	}
}

func TestUnitCensusPassReadbackRefusesUnchangedStatBytes(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	for _, base := range []string{home, filepath.Join(home, ".cache")} {
		t.Run(filepath.Base(base), func(t *testing.T) {
			root := unitVerifiedDirectory(t, base)
			r := censusVerifiedRead(t, root)
			pin := censusTestJSON(t, filepath.Join(root, "selected.json"), map[string]any{"selected": "original"})
			writer, err := os.OpenFile(pin.Path, os.O_RDWR, 0)
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			info, err := writer.Stat()
			if err != nil {
				t.Fatal(err)
			}
			mapped, err := unix.Mmap(int(writer.Fd()), 0, int(info.Size()), unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
			if err != nil {
				t.Fatal(err)
			}
			defer unix.Munmap(mapped)
			mapped[0] = '{'
			if _, err := r.pin(pin, false); err != nil {
				t.Fatal(err)
			}
			before, err := unitVerifiedPathStat(pin.Path)
			if err != nil {
				t.Fatal(err)
			}
			mapped[0] = '!'
			after, err := unitVerifiedPathStat(pin.Path)
			if err != nil || !unitVerifiedSame(before, after) {
				t.Fatal("unchanged-stat fixture was not established", err)
			}
			if err := r.readback(context.Background()); err == nil || !strings.Contains(err.Error(), "census pin changed") || errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("census readback accepted actual changed bytes: %v", err)
			}
		})
	}
}
