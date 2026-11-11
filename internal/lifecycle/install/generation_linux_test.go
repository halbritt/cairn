package install

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

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

func TestChangedGenerationReuseReturnsNoSelection(t *testing.T) {
	root, files := generationInputs(t)
	old, err := StageGeneration(context.Background(), root, legacyID, files)
	if err != nil {
		t.Fatal(err)
	}
	files[0].Executable = !files[0].Executable
	refused, err := StageGeneration(context.Background(), root, legacyID, files)
	if err == nil || refused != (Generation{}) {
		t.Fatalf("refused replacement returned an unusable selection pin: %+v %v", refused, err)
	}
	if _, err := ReadGeneration(context.Background(), old); err != nil {
		t.Fatalf("refused replacement changed the retained generation: %v", err)
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

type generationCrashInput struct {
	Root      string
	Files     []File
	At        string
	Directory bool
}

func TestGenerationCrashChild(t *testing.T) {
	path := os.Getenv("CAIRN_R02_CRASH_INPUT")
	if path == "" {
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var in generationCrashInput
	if err := json.Unmarshal(raw, &in); err != nil {
		t.Fatal(err)
	}
	observe := func(at string) error {
		if at == in.At {
			if _, err := os.Stdout.WriteString("at-boundary\n"); err != nil {
				return err
			}
			var release [1]byte
			_, err := os.Stdin.Read(release[:])
			return err
		}
		return nil
	}
	if in.Directory {
		_, err = stageDirectory(context.Background(), in.Root, strings.Repeat("b", 64), in.Files, observe)
	} else {
		_, err = stageGeneration(context.Background(), in.Root, nextID, in.Files, observe)
	}
	if err != nil {
		t.Fatal(err)
	}
	t.Fatal("crash child unexpectedly finished staging")
}

func TestGenerationSIGKILLReconciliationRetainsPreviousGeneration(t *testing.T) {
	for _, point := range []string{"member-sync/coordination.json", "member-sync/launcher", "member-sync/memory.json", "manifest-sync", "generation-sync", "publication", "root-sync"} {
		for _, side := range []string{"before:", "after:"} {
			t.Run(side+point, func(t *testing.T) {
				root, files := generationInputs(t)
				old, err := StageGeneration(context.Background(), root, legacyID, files)
				if err != nil {
					t.Fatal(err)
				}
				raw, err := json.Marshal(generationCrashInput{Root: root, Files: files, At: side + point})
				if err != nil {
					t.Fatal(err)
				}
				input := filepath.Join(filepath.Dir(root), "crash-input.json")
				if err := os.WriteFile(input, raw, 0600); err != nil {
					t.Fatal(err)
				}
				killPublisherAtBoundary(t, input)
				if _, err := ReadGeneration(context.Background(), old); err != nil {
					t.Fatalf("previous generation invalid after crash: %v", err)
				}
				selected, err := StageGeneration(context.Background(), root, nextID, files)
				if err != nil {
					t.Fatalf("exact file-only reconciliation after joined child: %v", err)
				}
				if _, err := ReadGeneration(context.Background(), selected); err != nil {
					t.Fatalf("reconciled selection invalid: %v", err)
				}
			})
		}
	}
}

func killPublisherAtBoundary(t *testing.T, input string) {
	t.Helper()
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, self, "-test.run=^TestGenerationCrashChild$")
	cmd.Env = append(os.Environ(), "CAIRN_R02_CRASH_INPUT="+input)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	defer stdin.Close()
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var diagnostic bytes.Buffer
	cmd.Stderr = &diagnostic
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if cmd.ProcessState == nil {
			if err := cmd.Process.Kill(); err != nil && !errors.Is(err, os.ErrProcessDone) {
				t.Error(err)
			}
			if err := cmd.Wait(); err == nil {
				t.Error("expected terminated fixture")
			}
		}
	})
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil || line != "at-boundary\n" {
		t.Fatalf("child did not reach boundary: %q %v %s", line, err, diagnostic.String())
	}
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Wait(); err == nil || cmd.ProcessState.Success() {
		t.Fatalf("child did not terminate under SIGKILL: %v", err)
	}
}
