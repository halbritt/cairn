package binding

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestBinaryImageHelper(t *testing.T) {
	if os.Getenv("CAIRN_F01_IMAGE_HELPER") != "1" {
		return
	}
	fmt.Println("ready")
	if _, err := bufio.NewReader(os.Stdin).ReadString('\n'); err != nil {
		t.Fatal(err)
	}
	err := ValidateBinary(os.Getenv("CAIRN_F01_IMAGE_PIN"), os.Getenv("CAIRN_F01_IMAGE_SHA"))
	message := ""
	if err != nil {
		message = err.Error()
	}
	if err := json.NewEncoder(os.Stdout).Encode(map[string]string{"error": message}); err != nil {
		t.Fatal(err)
	}
}

func TestDigestRefusesRealDescriptorMetadataChangeDuringRead(t *testing.T) {
	// A pipe lets the actual read block while its descriptor metadata changes;
	// no hash, stat, or filesystem operation is replaced by a test callback.
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	done := make(chan error, 1)
	go func() { _, err := stableDigest(reader); done <- err }()
	// Larger than the kernel pipe buffer: returning proves the reader passed
	// its initial fstat and consumed bytes before this chmod.
	written := make(chan error, 1)
	go func() { _, err := writer.Write(make([]byte, 1024*1024)); written <- err }()
	select {
	case err := <-written:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("hash reader did not consume the first block")
	}
	if err := reader.Chmod(0400); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "metadata changed while hashing") {
			t.Fatalf("changed descriptor was accepted: %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("blocked descriptor hash did not finish")
	}
	if _, err := reader.Stat(); errors.Is(err, os.ErrClosed) {
		t.Fatal("digest helper closed caller descriptor")
	}
}

func stageImage(t *testing.T, directory string) (string, string) {
	t.Helper()
	if err := os.MkdirAll(directory, 0700); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(os.Args[0])
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "cairn-fixture")
	if err := os.WriteFile(path, raw, 0700); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(raw)
	return path, hex.EncodeToString(digest[:])
}

func runImage(t *testing.T, executable, pin, digest string, before func()) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, executable, "-test.run=^TestBinaryImageHelper$")
	cmd.Env = append(os.Environ(), "CAIRN_F01_IMAGE_HELPER=1", "CAIRN_F01_IMAGE_PIN="+pin, "CAIRN_F01_IMAGE_SHA="+digest)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	defer stdin.Close()
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	if cmd.Process.Pid <= 1 {
		t.Fatal("invalid owned image fixture PID")
	}
	reaped := false
	defer func() {
		if !reaped {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		}
	}()
	scanner := bufio.NewScanner(stdout)
	if !scanner.Scan() || scanner.Text() != "ready" {
		t.Fatalf("image fixture not ready: %v, %s", scanner.Err(), stderr.String())
	}
	if before != nil {
		before()
	}
	if _, err := fmt.Fprintln(stdin, "validate"); err != nil {
		t.Fatal(err)
	}
	if !scanner.Scan() {
		t.Fatalf("image fixture has no result: %v, %s", scanner.Err(), stderr.String())
	}
	var result map[string]string
	if err := json.Unmarshal(scanner.Bytes(), &result); err != nil {
		t.Fatalf("bad image result %q: %v", scanner.Text(), err)
	}
	// Drain the Go test harness PASS line before Wait closes stdout.
	for scanner.Scan() {
	}
	err = cmd.Wait()
	reaped = true
	if err != nil {
		t.Fatalf("image fixture failed: %v, %s", err, stderr.String())
	}
	return result["error"]
}

func TestRunningAndPinnedImageCustody(t *testing.T) {
	for _, name := range []string{"staged", "hard-link", "another-copy", "replaced", "missing", "deleted-running-hard-link", "modified-image", "symlink-file", "symlink-parent", "unsafe-parent", "unsafe-parent-other-write", "nonprivate-snapshot", "wrong-digest", "no-owner-execute", "group-writable", "set-id", "sticky-file"} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			if err := os.Chmod(root, 0700); err != nil {
				t.Fatal(err)
			}
			path, digest := stageImage(t, filepath.Join(root, "snapshot"))
			executable, pin := path, path
			var before func()
			switch name {
			case "hard-link":
				pin = filepath.Join(filepath.Dir(path), "alias")
				if err := os.Link(path, pin); err != nil {
					t.Fatal(err)
				}
			case "another-copy":
				executable, _ = stageImage(t, filepath.Join(root, "other"))
			case "replaced":
				before = func() {
					if err := os.Rename(path, path+".old"); err != nil {
						t.Fatal(err)
					}
					stageImage(t, filepath.Dir(path))
				}
			case "missing":
				before = func() {
					if err := os.Remove(path); err != nil {
						t.Fatal(err)
					}
				}
			case "deleted-running-hard-link":
				pin = path + ".alias"
				if err := os.Link(path, pin); err != nil {
					t.Fatal(err)
				}
				before = func() {
					if err := os.Remove(path); err != nil {
						t.Fatal(err)
					}
				}
			case "modified-image":
				file, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
				if err != nil {
					t.Fatal(err)
				}
				_, writeErr := file.Write([]byte("changed before execution"))
				closeErr := file.Close()
				if writeErr != nil || closeErr != nil {
					t.Fatalf("change staged image: %v, %v", writeErr, closeErr)
				}
			case "symlink-file":
				pin = path + ".link"
				if err := os.Symlink(path, pin); err != nil {
					t.Fatal(err)
				}
			case "symlink-parent":
				if err := os.Symlink(filepath.Dir(path), filepath.Join(root, "link")); err != nil {
					t.Fatal(err)
				}
				pin = filepath.Join(root, "link", filepath.Base(path))
			case "unsafe-parent":
				before = func() {
					if err := os.Chmod(root, 0770); err != nil {
						t.Fatal(err)
					}
				}
			case "unsafe-parent-other-write":
				before = func() {
					if err := os.Chmod(root, 0707); err != nil {
						t.Fatal(err)
					}
				}
			case "nonprivate-snapshot":
				before = func() {
					if err := os.Chmod(filepath.Dir(path), 0750); err != nil {
						t.Fatal(err)
					}
				}
			case "wrong-digest":
				digest = strings.Repeat("0", 64)
			case "no-owner-execute", "set-id", "group-writable", "sticky-file":
				before = func() {
					mode := os.FileMode(0600)
					if name == "set-id" {
						mode = 0700 | os.ModeSetuid
					}
					if name == "group-writable" {
						mode = 0720
					}
					if name == "sticky-file" {
						mode = 0700 | os.ModeSticky
					}
					if err := os.Chmod(path, mode); err != nil {
						t.Fatal(err)
					}
				}
			}
			err := runImage(t, executable, pin, digest, before)
			wantOK := name == "staged" || name == "hard-link"
			if (err == "") != wantOK {
				t.Fatalf("image custody %s: %q", name, err)
			}
			refusal := map[string]string{
				"another-copy": "same device/inode", "replaced": "same device/inode",
				"deleted-running-hard-link": "running executable is deleted", "modified-image": "digest changed",
				"missing": "binary unavailable", "symlink-file": "symlink", "symlink-parent": "symlink",
				"unsafe-parent": "ancestor", "unsafe-parent-other-write": "ancestor", "nonprivate-snapshot": "snapshot directory",
				"wrong-digest": "digest changed", "no-owner-execute": "owner execute", "set-id": "owner execute", "group-writable": "owner execute", "sticky-file": "owner execute",
			}[name]
			if refusal != "" && !strings.Contains(err, refusal) {
				t.Fatalf("wrong refusal for %s: %q", name, err)
			}
		})
	}
}

func TestActualRootOwnedSourcePinRefusedForNonroot(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("requires non-root real UID")
	}
	path, err := filepath.EvalSymlinks("/usr/bin/true")
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Sys().(*syscall.Stat_t).Uid != 0 {
		t.Fatalf("root-owned fixture source unavailable: %v", err)
	}
	if err := ValidateBinary(path, strings.Repeat("0", 64)); err == nil || !strings.Contains(err.Error(), "real UID") {
		t.Fatalf("root-owned source pin was not refused for its owner: %v", err)
	}
}

func TestNonEnumeratedRootStickyAncestorRefused(t *testing.T) {
	// This exercises the real descriptor walk in-process. /dev/shm can be
	// noexec; no ELF is executed there and no mount or host mode is changed.
	info, err := os.Stat("/dev/shm")
	if err != nil {
		t.Skipf("non-enumerated sticky fixture unavailable: %v", err)
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.IsDir() || stat.Uid != 0 || stat.Mode&syscall.S_ISVTX == 0 || stat.Mode&0022 == 0 {
		t.Skip("/dev/shm is not a writable root-owned sticky directory on this host")
	}
	root, err := os.MkdirTemp("/dev/shm", "f01-ancestor-")
	if err != nil {
		t.Skipf("cannot create owned non-enumerated ancestor fixture: %v", err)
	}
	defer os.RemoveAll(root)
	path, digest := stageImage(t, filepath.Join(root, "snapshot"))
	if err := ValidateBinary(path, digest); err == nil || !strings.Contains(err.Error(), `binary ancestor "/dev/shm"`) {
		t.Fatalf("non-enumerated root sticky ancestor was not refused at its path policy: %v", err)
	}
}

func TestEnumeratedTemporaryAncestorsAllowPrivateStaging(t *testing.T) {
	for _, directory := range []string{"/tmp", "/var/tmp"} {
		t.Run(directory, func(t *testing.T) {
			info, err := os.Stat(directory)
			if err != nil {
				t.Skipf("enumerated temporary fixture unavailable: %v", err)
			}
			stat, ok := info.Sys().(*syscall.Stat_t)
			if !ok || !info.IsDir() || stat.Uid != 0 || stat.Mode&syscall.S_ISVTX == 0 || stat.Mode&0022 == 0 {
				t.Skip("enumerated temporary fixture is not writable, root-owned and sticky")
			}
			root, err := os.MkdirTemp(directory, "f01-private-stage-")
			if err != nil {
				t.Skipf("cannot create owned private staging fixture: %v", err)
			}
			defer os.RemoveAll(root)
			path, digest := stageImage(t, filepath.Join(root, "snapshot"))
			if err := runImage(t, path, path, digest, nil); err != "" {
				t.Fatalf("enumerated ancestor refused private staging: %q", err)
			}
		})
	}
}
