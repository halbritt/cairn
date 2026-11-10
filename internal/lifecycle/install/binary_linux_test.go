package install

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

func inputs(t *testing.T) (string, string, string) {
	t.Helper()
	base, err := os.MkdirTemp("/dev/shm", "cairn-stage-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(base); err != nil {
			t.Error(err)
		}
	})
	root := filepath.Join(base, "releases")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(base, "candidate")
	content := []byte("selected executable fixture\x00\xff\n")
	if err := os.WriteFile(source, content, 0700); err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(content)
	return root, source, hex.EncodeToString(hash[:])
}

func TestStageBinaryPreservesLegacyAndReusesExactArtifact(t *testing.T) {
	root, source, digest := inputs(t)
	legacy := filepath.Join(root, "legacy-config.json")
	if err := os.WriteFile(legacy, []byte("retained /2 selection"), 0600); err != nil {
		t.Fatal(err)
	}
	first, err := StageBinary(context.Background(), root, source, digest)
	if err != nil {
		t.Fatal(err)
	}
	if first.Path != filepath.Join(root, digest, "cairn") || first.SHA256 != digest {
		t.Fatalf("descriptor = %+v", first)
	}
	content, err := os.ReadFile(first.Path)
	if err != nil {
		t.Fatal(err)
	}
	want, err := os.ReadFile(source)
	if err != nil || string(content) != string(want) {
		t.Fatalf("content differs: %v", err)
	}
	before, err := os.Stat(first.Path)
	if err != nil || before.Mode().Perm() != 0500 {
		t.Fatalf("executable mode: %v, %v", before, err)
	}
	directory, err := os.Stat(filepath.Dir(first.Path))
	if err != nil || directory.Mode().Perm() != 0700 {
		t.Fatalf("directory mode: %v, %v", directory, err)
	}
	second, err := StageBinary(context.Background(), root, source, digest)
	if err != nil || second != first {
		t.Fatalf("reuse = %+v, %v", second, err)
	}
	after, err := os.Stat(first.Path)
	if err != nil || !os.SameFile(before, after) {
		t.Fatalf("existing artifact was replaced: %v", err)
	}
	retained, err := os.ReadFile(legacy)
	if err != nil || string(retained) != "retained /2 selection" {
		t.Fatalf("legacy selection changed: %v", err)
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 2 {
		t.Fatalf("temporary stage retained: %v, %v", entries, err)
	}
}

func TestStageBinaryRefusesUnsafeInputsWithoutPublication(t *testing.T) {
	cases := map[string]func(*testing.T, *string, *string, *string){
		"wrong digest":     func(t *testing.T, root, source, digest *string) { *digest = strings.Repeat("0", 64) },
		"uppercase digest": func(t *testing.T, root, source, digest *string) { *digest = strings.ToUpper(*digest) },
		"relative source":  func(t *testing.T, root, source, digest *string) { *source = "candidate" },
		"relative root":    func(t *testing.T, root, source, digest *string) { *root = "releases" },
		"public root": func(t *testing.T, root, source, digest *string) {
			if err := os.Chmod(*root, 0755); err != nil {
				t.Fatal(err)
			}
		},
		"writable ancestor": func(t *testing.T, root, source, digest *string) {
			if err := os.Chmod(filepath.Dir(*root), 0777); err != nil {
				t.Fatal(err)
			}
		},
		"writable source": func(t *testing.T, root, source, digest *string) {
			if err := os.Chmod(*source, 0777); err != nil {
				t.Fatal(err)
			}
		},
		"source hardlink": func(t *testing.T, root, source, digest *string) {
			if err := os.Link(*source, *source+".alias"); err != nil {
				t.Fatal(err)
			}
		},
		"source symlink": func(t *testing.T, root, source, digest *string) {
			alias := *source + ".alias"
			if err := os.Symlink(*source, alias); err != nil {
				t.Fatal(err)
			}
			*source = alias
		},
		"root symlink": func(t *testing.T, root, source, digest *string) {
			alias := *root + ".alias"
			if err := os.Symlink(*root, alias); err != nil {
				t.Fatal(err)
			}
			*root = alias
		},
		"fifo source": func(t *testing.T, root, source, digest *string) {
			fifo := *source + ".fifo"
			if err := unix.Mkfifo(fifo, 0700); err != nil {
				t.Fatal(err)
			}
			*source = fifo
		},
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			root, source, digest := inputs(t)
			actualRoot := root
			change(t, &root, &source, &digest)
			if _, err := StageBinary(context.Background(), root, source, digest); err == nil {
				t.Fatal("unsafe input accepted")
			}
			entries, err := os.ReadDir(actualRoot)
			if err != nil || len(entries) != 0 {
				t.Fatalf("failed stage published or leaked files: %v, %v", entries, err)
			}
		})
	}
}

func TestStageBinaryRefusesTamperedPublishedArtifact(t *testing.T) {
	for _, kind := range []string{"contents", "mode", "extra member", "hardlink", "directory symlink"} {
		t.Run(kind, func(t *testing.T) {
			root, source, digest := inputs(t)
			staged, err := StageBinary(context.Background(), root, source, digest)
			if err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "contents":
				if err := os.Chmod(staged.Path, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(staged.Path, []byte("tampered"), 0500); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(staged.Path, 0500); err != nil {
					t.Fatal(err)
				}
			case "mode":
				if err := os.Chmod(staged.Path, 0700); err != nil {
					t.Fatal(err)
				}
			case "extra member":
				if err := os.WriteFile(filepath.Join(filepath.Dir(staged.Path), "extra"), []byte("unexpected"), 0600); err != nil {
					t.Fatal(err)
				}
			case "hardlink":
				if err := os.Link(staged.Path, filepath.Join(root, "alias")); err != nil {
					t.Fatal(err)
				}
			case "directory symlink":
				original := filepath.Dir(staged.Path)
				retained := original + ".retained"
				if err := os.Rename(original, retained); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(retained, original); err != nil {
					t.Fatal(err)
				}
			}
			before, err := os.ReadFile(staged.Path)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := StageBinary(context.Background(), root, source, digest); err == nil {
				t.Fatal("tampered artifact accepted")
			}
			after, err := os.ReadFile(staged.Path)
			if err != nil || string(before) != string(after) {
				t.Fatalf("tamper was silently repaired: %v", err)
			}
		})
	}
}

func TestStageBinaryCancellationAndLockRefuseBeforeWrites(t *testing.T) {
	root, source, digest := inputs(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := StageBinary(ctx, root, source, digest); err == nil {
		t.Fatal("cancelled stage accepted")
	}
	dir, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer dir.Close()
	if err := unix.Flock(int(dir.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	if _, err := StageBinary(context.Background(), root, source, digest); err == nil {
		t.Fatal("concurrent staging accepted")
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 0 {
		t.Fatalf("refusal wrote artifacts: %v, %v", entries, err)
	}
}

func TestStageBinaryRefusesUnrepresentableDescriptorPath(t *testing.T) {
	root, source, digest := inputs(t)
	invalid := filepath.Join(filepath.Dir(root), "invalid-\xff")
	if err := os.Mkdir(invalid, 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := StageBinary(context.Background(), invalid, source, digest); err == nil {
		t.Fatal("published a path that cannot round-trip through the JSON descriptor")
	}
	entries, err := os.ReadDir(invalid)
	if err != nil || len(entries) != 0 {
		t.Fatalf("invalid descriptor path caused writes: %v, %v", entries, err)
	}
}
