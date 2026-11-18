package coordination

import (
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

func fullLinuxStatLine(pid int, state string, flags uint64, start, envStart, envEnd uint64) []byte {
	fields := make([]string, 50)
	for i := range fields {
		fields[i] = "0"
	}
	fields[0] = state
	fields[1] = "1"
	fields[2] = fmt.Sprint(pid)
	fields[3] = fmt.Sprint(pid)
	fields[6] = fmt.Sprint(flags)
	fields[19] = fmt.Sprint(start)
	fields[47] = fmt.Sprint(envStart)
	fields[48] = fmt.Sprint(envEnd)
	return []byte(fmt.Sprintf("%d (proc) %s\n", pid, strings.Join(fields, " ")))
}

func writeFullProcEntry(t *testing.T, root string, pid int, stat []byte, environ []byte, cmdline string) string {
	t.Helper()
	dir := filepath.Join(root, fmt.Sprint(pid))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "stat"), stat, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "environ"), environ, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "cmdline"), []byte(cmdline), 0o600); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestScanMarkersMidExecveKernelAndEmptyEnv(t *testing.T) {
	uid := uint32(os.Getuid())

	t.Run("kernel_thread_and_empty_env_are_not_unknown", func(t *testing.T) {
		root := procFixtureRoot(t)
		// Kernel thread: PF_KTHREAD set, 0-byte environ, env_start=0, env_end=0.
		writeFullProcEntry(t, root, 401, fullLinuxStatLine(401, "I", pfKthread, 500, 0, 0), nil, "")
		// Empty-env user process: env_start != 0 && env_start == env_end, 0-byte environ.
		writeFullProcEntry(t, root, 402, fullLinuxStatLine(402, "S", 0x400000, 500, 0x7fff00001000, 0x7fff00001000), nil, "sleep\x001\x00")
		res, err := ScanMarkersAt(root, uid, "CAIRN_REQUEST_ID", "m1", 100)
		if err != nil || !res.Clear || res.Coverage != "complete" || len(res.Unknown) != 0 || len(res.Processes) != 0 {
			t.Fatalf("expected clear complete scan, got %+v err=%v", res, err)
		}
	})

	t.Run("transient_mid_execve_recovers_marker", func(t *testing.T) {
		root := procFixtureRoot(t)
		dir := writeFullProcEntry(t, root, 403, fullLinuxStatLine(403, "R", 0x400000, 500, 0, 0), nil, "worker\x00")
		go func() {
			time.Sleep(3 * time.Millisecond)
			_ = os.WriteFile(filepath.Join(dir, "environ"), []byte("CAIRN_REQUEST_ID=m2\x00"), 0o600)
			_ = os.WriteFile(filepath.Join(dir, "stat"), fullLinuxStatLine(403, "S", 0x400000, 500, 0x7fff00001000, 0x7fff00001020), 0o600)
		}()
		res, err := ScanMarkersAt(root, uid, "CAIRN_REQUEST_ID", "m2", 100)
		if err != nil || res.Clear || res.Coverage != "complete" || len(res.Processes) != 1 || res.Processes[0].PID != 403 {
			t.Fatalf("expected mid-execve PID 403 caught, got %+v err=%v", res, err)
		}
	})

	t.Run("stalled_mid_execve_is_unknown_not_false_clear", func(t *testing.T) {
		root := procFixtureRoot(t)
		writeFullProcEntry(t, root, 404, fullLinuxStatLine(404, "D", 0x400000, 500, 0, 0), nil, "")
		res, err := ScanMarkersAt(root, uid, "CAIRN_REQUEST_ID", "m3", 100)
		if err != nil || res.Clear || res.Coverage != "unknown" || !slices.Equal(res.Unknown, []int{404}) {
			t.Fatalf("expected stalled mid-execve PID 404 unknown, got %+v err=%v", res, err)
		}
	})
}
