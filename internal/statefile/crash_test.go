package statefile

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// The helper pauses only at the four I/O boundaries retained from the CLI.
// These are process-death observations, not a power-loss durability test.
func TestCrashWriterHelper(t *testing.T) {
	if os.Getenv("CAIRN_STATEFILE_CRASH_HELPER") != "1" {
		return
	}
	path, stage := os.Getenv("CAIRN_STATEFILE_CRASH_PATH"), os.Getenv("CAIRN_STATEFILE_CRASH_STAGE")
	pause := func() {
		fmt.Fprintln(os.Stdout, "ready")
		time.Sleep(30 * time.Second)
		t.Fatal("parent did not kill paused writer")
	}
	f := File[int]{Path: path, TempPattern: ".crash-state-*"}.WithIO(IO{
		Write: func(file *os.File, raw []byte) (int, error) {
			if stage == "partial-write" {
				n, err := file.Write(raw[:1])
				if err == nil {
					pause()
				}
				return n, err
			}
			return file.Write(raw)
		},
		Sync: func(file *os.File) error {
			info, err := file.Stat()
			if err != nil {
				return err
			}
			if info.IsDir() && stage == "published-before-directory-sync" {
				pause()
			}
			if err := file.Sync(); err != nil {
				return err
			}
			if !info.IsDir() && stage == "temporary-synced" {
				pause()
			}
			return nil
		},
	})
	if err := f.Update(func(int, func(int) error) (int, bool, error) { return 12345, true, nil }); err != nil {
		t.Fatal(err)
	}
	t.Fatal("helper did not reach requested boundary")
}

func TestKilledWriterRetainsCompleteTargetAndReleasesLock(t *testing.T) {
	for _, stage := range []string{"partial-write", "temporary-synced", "published-before-directory-sync"} {
		t.Run(stage, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "state.json")
			prior := []byte("900")
			if err := os.WriteFile(path, prior, 0600); err != nil {
				t.Fatal(err)
			}
			before, err := os.Stat(path)
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestCrashWriterHelper$")
			cmd.Env = append(os.Environ(), "CAIRN_STATEFILE_CRASH_HELPER=1", "CAIRN_STATEFILE_CRASH_PATH="+path, "CAIRN_STATEFILE_CRASH_STAGE="+stage)
			cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			stdout, err := cmd.StdoutPipe()
			if err != nil {
				t.Fatal(err)
			}
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			// Only this exec.Cmd's owned child is ever signalled; no group kill.
			if cmd.Process.Pid <= 1 {
				t.Fatal("invalid owned child PID")
			}
			reaped := false
			defer func() {
				if !reaped {
					_ = cmd.Process.Kill()
					_ = cmd.Wait()
				}
			}()
			pgid, err := syscall.Getpgid(cmd.Process.Pid)
			if err != nil || pgid <= 1 || pgid != cmd.Process.Pid {
				t.Fatalf("owned child group = %d: %v", pgid, err)
			}
			ready := make(chan bool, 1)
			go func() { scanner := bufio.NewScanner(stdout); ready <- scanner.Scan() && scanner.Text() == "ready" }()
			select {
			case ok := <-ready:
				if !ok {
					t.Fatalf("no boundary observation: %s", stderr.String())
				}
			case <-ctx.Done():
				t.Fatal("writer boundary timed out")
			}
			lock, err := os.OpenFile(path+".lock", os.O_RDWR, 0600)
			if err != nil {
				t.Fatal(err)
			}
			defer lock.Close()
			if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); !errors.Is(err, syscall.EWOULDBLOCK) {
				t.Fatalf("writer did not hold actual lock: %v", err)
			}
			if err := cmd.Process.Kill(); err != nil {
				t.Fatal(err)
			}
			err = cmd.Wait()
			reaped = true
			var exited *exec.ExitError
			if !errors.As(err, &exited) || !exited.Sys().(syscall.WaitStatus).Signaled() || exited.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
				t.Fatalf("writer was not killed at boundary: %v (%s)", err, stderr.String())
			}
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			after, err := os.Stat(path)
			if err != nil {
				t.Fatal(err)
			}
			published := stage == "published-before-directory-sync"
			want := prior
			if published {
				want = []byte("12345")
			}
			if !bytes.Equal(raw, want) || os.SameFile(before, after) == published {
				t.Fatalf("target custody at %s: %q, same inode=%v", stage, raw, os.SameFile(before, after))
			}
			temps, err := filepath.Glob(filepath.Join(filepath.Dir(path), ".crash-state-*"))
			if err != nil || (published && len(temps) != 0) || (!published && len(temps) != 1) {
				t.Fatalf("temporary custody: %v, %v", temps, err)
			}
			if len(temps) == 1 {
				temp, err := os.ReadFile(temps[0])
				info, statErr := os.Stat(temps[0])
				wantTemp := "12345"
				if stage == "partial-write" {
					wantTemp = "1"
				}
				if err != nil || statErr != nil || string(temp) != wantTemp || info.Mode().Perm() != 0600 {
					t.Fatalf("orphan temporary: %q, %v, %v", temp, err, statErr)
				}
			}
			if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
				t.Fatalf("killed writer retained lock: %v", err)
			}
			if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); err != nil {
				t.Fatal(err)
			}
			if err := (File[int]{Path: path}).Update(func(got int, _ func(int) error) (int, bool, error) {
				if (published && got != 12345) || (!published && got != 900) {
					t.Fatalf("next writer loaded orphan/partial state: %d", got)
				}
				return got + 1, true, nil
			}); err != nil {
				t.Fatal(err)
			}
		})
	}
}
