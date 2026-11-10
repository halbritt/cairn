package install

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

const legacyID = "147fc8dd-b0d9-4b9f-8be1-59a2c77ef0cb"
const nextID = "9661ba6a-14a1-4167-82a4-72a400af3546"

func generationInputs(t *testing.T) (string, []File) {
	t.Helper()
	root, _, _ := inputs(t)
	var files []File
	for name, raw := range map[string][]byte{
		"memory.json":       []byte("{ \"schema\":\"/2\", \"custom_state\":\"retained\", \"token_file\":\"not-read\" }\n"),
		"coordination.json": []byte("{\"unknown_custom_field\":[1,2,3]}\n"),
		"launcher":          []byte("#!/bin/sh\nexit 0\n"),
	} {
		path := filepath.Join(filepath.Dir(root), name)
		if err := os.WriteFile(path, raw, 0600); err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256(raw)
		files = append(files, File{Name: name, Path: path, SHA256: hex.EncodeToString(h[:]), Executable: name == "launcher"})
	}
	return root, files
}

func TestGenerationPublishesExactFilesAndRetainsReverseSelection(t *testing.T) {
	root, files := generationInputs(t)
	legacy, err := StageGeneration(context.Background(), root, legacyID, files)
	if err != nil {
		t.Fatal(err)
	}
	old, err := ReadGeneration(context.Background(), legacy)
	if err != nil {
		t.Fatal(err)
	}
	before := make(map[string][]byte)
	for _, f := range old {
		before[f.Name], err = os.ReadFile(f.Path)
		if err != nil {
			t.Fatal(err)
		}
	}
	// The next generation comes from independent files. Retained paths are not
	// rewritten into /3 under an existing callback or issued command.
	nextFiles := append([]File(nil), files...)
	for i, f := range nextFiles {
		raw, err := os.ReadFile(f.Path)
		if err != nil {
			t.Fatal(err)
		}
		raw = bytes.ReplaceAll(raw, []byte("/2"), []byte("/3"))
		path := f.Path + ".next"
		if err := os.WriteFile(path, raw, 0600); err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256(raw)
		nextFiles[i].Path, nextFiles[i].SHA256 = path, hex.EncodeToString(h[:])
	}
	next, err := StageGeneration(context.Background(), root, nextID, nextFiles)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ReadGeneration(context.Background(), next); err != nil {
		t.Fatal(err)
	}
	reverse, err := ReadGeneration(context.Background(), legacy)
	if err != nil || !reflect.DeepEqual(reverse, old) {
		t.Fatalf("reverse selection changed: %v %v", reverse, err)
	}
	for _, f := range reverse {
		raw, err := os.ReadFile(f.Path)
		if err != nil || !bytes.Equal(raw, before[f.Name]) {
			t.Fatalf("legacy bytes changed: %s: %v", f.Name, err)
		}
		info, err := os.Stat(f.Path)
		if err != nil || info.Mode().Perm() != map[bool]os.FileMode{true: 0500, false: 0400}[f.Executable] {
			t.Fatalf("member mode: %s: %v", f.Name, err)
		}
	}
	info, err := os.Stat(legacy.Path)
	if err != nil {
		t.Fatal(err)
	}
	again, err := StageGeneration(context.Background(), root, legacyID, files)
	if err != nil || again != legacy {
		t.Fatalf("reuse: %+v %v", again, err)
	}
	after, err := os.Stat(legacy.Path)
	if err != nil || !os.SameFile(info, after) {
		t.Fatal("exact reuse replaced the manifest")
	}
}

func TestGenerationRefusesChangedReuseAndMemberTampering(t *testing.T) {
	for _, kind := range []string{"pin", "mode", "symlink", "extra", "manifest-pin"} {
		t.Run(kind, func(t *testing.T) {
			root, files := generationInputs(t)
			selected, err := StageGeneration(context.Background(), root, legacyID, files)
			if err != nil {
				t.Fatal(err)
			}
			dir := filepath.Dir(selected.Path)
			switch kind {
			case "pin":
				files[0].SHA256 = strings.Repeat("0", 64)
			case "mode":
				if err := os.Chmod(filepath.Join(dir, "memory.json"), 0600); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Remove(filepath.Join(dir, "memory.json")); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(files[0].Path, filepath.Join(dir, "memory.json")); err != nil {
					t.Fatal(err)
				}
			case "extra":
				if err := os.WriteFile(filepath.Join(dir, "extra"), nil, 0400); err != nil {
					t.Fatal(err)
				}
			case "manifest-pin":
				selected.SHA256 = strings.Repeat("0", 64)
			}
			if kind != "manifest-pin" {
				if _, err := StageGeneration(context.Background(), root, legacyID, files); err == nil {
					t.Fatal("tampered reuse accepted")
				}
			}
			if kind != "pin" {
				if _, err := ReadGeneration(context.Background(), selected); err == nil {
					t.Fatal("tampered generation accepted")
				}
			}
			names, err := os.ReadDir(root)
			if err != nil || len(names) != 1 {
				t.Fatalf("refusal left a temporary artifact: %v %v", names, err)
			}
		})
	}
}

func TestGenerationFailureAtEveryDurableBoundaryKeepsOldSelection(t *testing.T) {
	points := []string{"member-sync/coordination.json", "member-sync/launcher", "member-sync/memory.json", "manifest-sync", "generation-sync", "publication", "root-sync"}
	for _, point := range points {
		for _, side := range []string{"before:", "after:"} {
			t.Run(side+point, func(t *testing.T) {
				root, files := generationInputs(t)
				old, err := StageGeneration(context.Background(), root, legacyID, files)
				if err != nil {
					t.Fatal(err)
				}
				sentinel := errors.New("injected durable-boundary interruption")
				result, err := stageGeneration(context.Background(), root, nextID, files, func(at string) error {
					if at == side+point {
						return sentinel
					}
					return nil
				})
				if !errors.Is(err, sentinel) {
					t.Fatalf("injection did not propagate: %v", err)
				}
				if _, err := ReadGeneration(context.Background(), old); err != nil {
					t.Fatalf("old selection invalidated: %v", err)
				}
				if result.Path != "" {
					if _, err := ReadGeneration(context.Background(), result); err != nil {
						t.Fatalf("published failure is not recoverable: %v", err)
					}
				}
				if _, err := StageGeneration(context.Background(), root, nextID, files); err != nil {
					t.Fatalf("exact reconciliation failed: %v", err)
				}
			})
		}
	}
}

func TestGenerationLockAndCanceledContextRefuseBeforeWrites(t *testing.T) {
	root, files := generationInputs(t)
	lock, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := unix.Flock(int(lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	if _, err := StageGeneration(context.Background(), root, legacyID, files); err == nil {
		t.Fatal("concurrent installer did not refuse")
	}
	if err := unix.Flock(int(lock.Fd()), unix.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := StageGeneration(ctx, root, legacyID, files); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation: %v", err)
	}
	names, err := os.ReadDir(root)
	if err != nil || len(names) != 0 {
		t.Fatalf("refusal wrote state: %v %v", names, err)
	}
}
