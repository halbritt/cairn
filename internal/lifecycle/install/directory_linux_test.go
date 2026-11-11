package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

func directoryFiles(selected Directory, source []File) []File {
	files := append([]File(nil), source...)
	for i := range files {
		files[i].Path = filepath.Join(selected.Path, files[i].Name)
	}
	return files
}

func TestDirectoryPublishesExactAliasesAndLauncherWithoutManifestCollision(t *testing.T) {
	root, files := generationInputs(t)
	files[0].Name = "manifest.json"
	identity := strings.Repeat("a", 64)
	selected, err := StageDirectory(context.Background(), root, identity, files)
	if err != nil {
		t.Fatal(err)
	}
	pinned := directoryFiles(selected, files)
	verified, err := ReadDirectory(context.Background(), selected, pinned)
	if err != nil {
		t.Fatal(err)
	}
	if len(verified) != len(files) {
		t.Fatal("installed closure changed membership")
	}
	stat, err := os.Stat(filepath.Join(selected.Path, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	again, err := StageDirectory(context.Background(), root, identity, files)
	if err != nil || again != selected {
		t.Fatalf("unchanged publication: %+v %v", again, err)
	}
	after, err := os.Stat(filepath.Join(selected.Path, "manifest.json"))
	if err != nil || !os.SameFile(stat, after) {
		t.Fatal("reuse replaced retained bytes")
	}
	for _, file := range verified {
		info, err := os.Stat(file.Path)
		if err != nil || info.Mode().Perm() != map[bool]os.FileMode{false: 0400, true: 0500}[file.Executable] {
			t.Fatalf("wrong sealed mode: %v %v", info, err)
		}
	}
}

func TestDirectoryRefusesTamperedReadbackAndReplacement(t *testing.T) {
	for _, kind := range []string{"bytes", "mode", "symlink", "extra", "hardlink", "different-plan"} {
		t.Run(kind, func(t *testing.T) {
			root, files := generationInputs(t)
			selected, err := StageDirectory(context.Background(), root, strings.Repeat("a", 64), files)
			if err != nil {
				t.Fatal(err)
			}
			pinned := directoryFiles(selected, files)
			target := filepath.Join(selected.Path, files[0].Name)
			switch kind {
			case "bytes":
				if err := os.Chmod(target, 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(target, []byte("changed"), 0400); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(target, 0400); err != nil {
					t.Fatal(err)
				}
			case "mode":
				if err := os.Chmod(target, 0600); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Remove(target); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(files[0].Path, target); err != nil {
					t.Fatal(err)
				}
			case "extra":
				if err := os.WriteFile(filepath.Join(selected.Path, "unexpected"), nil, 0400); err != nil {
					t.Fatal(err)
				}
			case "hardlink":
				if err := os.Link(target, filepath.Join(root, "outside-link")); err != nil {
					t.Fatal(err)
				}
			case "different-plan":
				files[0].Executable = !files[0].Executable
				pinned = directoryFiles(selected, files)
			}
			if _, err := ReadDirectory(context.Background(), selected, pinned); err == nil {
				t.Fatal("invalid directory selected")
			}
			refused, err := StageDirectory(context.Background(), root, selected.Identity, files)
			if err == nil || refused != (Directory{}) {
				t.Fatalf("changed reuse produced a selection: %+v %v", refused, err)
			}
		})
	}
}

func TestDirectoryEveryDurableBoundaryRetainsPreviousSelection(t *testing.T) {
	points := []string{"member-sync/coordination.json", "member-sync/launcher", "member-sync/memory.json", "directory-sync", "publication", "root-sync"}
	for _, point := range points {
		for _, side := range []string{"before:", "after:"} {
			t.Run(side+point, func(t *testing.T) {
				root, files := generationInputs(t)
				old, err := StageDirectory(context.Background(), root, strings.Repeat("a", 64), files)
				if err != nil {
					t.Fatal(err)
				}
				before, err := ReadDirectory(context.Background(), old, directoryFiles(old, files))
				if err != nil {
					t.Fatal(err)
				}
				sentinel := errors.New("injected directory interruption")
				selected, err := stageDirectory(context.Background(), root, strings.Repeat("b", 64), files, func(at string) error {
					if at == side+point {
						return sentinel
					}
					return nil
				})
				if !errors.Is(err, sentinel) {
					t.Fatalf("lost failure: %v", err)
				}
				after, err := ReadDirectory(context.Background(), old, directoryFiles(old, files))
				if err != nil || !reflect.DeepEqual(before, after) {
					t.Fatalf("previous selection changed: %v", err)
				}
				if selected.Path != "" {
					if _, err := ReadDirectory(context.Background(), selected, directoryFiles(selected, files)); err != nil {
						t.Fatalf("published failure lost recoverable identity: %v", err)
					}
				}
				if _, err := StageDirectory(context.Background(), root, strings.Repeat("b", 64), files); err != nil {
					t.Fatalf("exact reconciliation: %v", err)
				}
			})
		}
	}
}

func TestDirectoryConflictingInstallerAndInvalidPlansRefuse(t *testing.T) {
	root, files := generationInputs(t)
	lock, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := unix.Flock(int(lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	if _, err := StageDirectory(context.Background(), root, strings.Repeat("c", 64), files); !errors.Is(err, unix.EWOULDBLOCK) {
		t.Fatalf("installer lock not respected: %v", err)
	}
	if err := unix.Flock(int(lock.Fd()), unix.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"", ".", "..", "../escape", "a/b", "bad\\name", "zero\x00name"} {
		bad := append([]File(nil), files...)
		bad[0].Name = name
		if _, err := StageDirectory(context.Background(), root, strings.Repeat("c", 64), bad); err == nil {
			t.Fatalf("accepted name %q", name)
		}
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := StageDirectory(cancelled, root, strings.Repeat("c", 64), files); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation lost: %v", err)
	}
	names, err := os.ReadDir(root)
	if err != nil || len(names) != 0 {
		t.Fatalf("refusals published files: %v %v", names, err)
	}
}
