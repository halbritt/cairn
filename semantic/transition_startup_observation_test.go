package semantic

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// An early observation measures the selected executable and environment before
// model initialization. It does not attest to libraries not yet mapped.
type transitionStartupSelection struct {
	Image       string            `json:"image"`
	SHA256      string            `json:"sha256"`
	Environment map[string]string `json:"environment"`
}

type transitionStartupObservation struct {
	PID          int               `json:"pid"`
	Parent       int               `json:"parent_pid"`
	StartTicks   string            `json:"process_start_ticks"`
	StartedAt    time.Time         `json:"started_at"`
	CompletedAt  time.Time         `json:"completed_at"`
	Image        string            `json:"image"`
	ImageSHA256  string            `json:"image_sha256"`
	ImageInode   uint64            `json:"image_inode"`
	MappedImage  string            `json:"mapped_image"`
	MapFilesPath string            `json:"map_files_target"`
	Maps         string            `json:"maps"`
	Argv         []string          `json:"argv"`
	Environment  map[string]string `json:"environment"`
}

func transitionStartupTicks(pid int) (string, error) {
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return "", err
	}
	end := strings.LastIndexByte(string(data), ')')
	if end < 0 {
		return "", fmt.Errorf("invalid process stat")
	}
	fields := strings.Fields(string(data)[end+1:])
	if len(fields) <= 19 {
		return "", fmt.Errorf("missing process start ticks")
	}
	if ticks, err := strconv.ParseUint(fields[19], 10, 64); err != nil || ticks == 0 {
		return "", fmt.Errorf("invalid process start ticks")
	}
	return fields[19], nil
}

func transitionObserveStartup(pid, owner int, selected transitionStartupSelection) (transitionStartupObservation, error) {
	row := transitionStartupObservation{PID: pid, Parent: owner, StartedAt: time.Now()}
	if pid <= 1 || pid == os.Getpid() || owner != os.Getpid() {
		return row, fmt.Errorf("refusing unowned startup observation")
	}
	parent, state, err := transitionProcessStatus(pid)
	if err != nil || parent != owner || state == "Z" || state == "X" {
		return row, fmt.Errorf("startup process is not a live owned child")
	}
	row.StartTicks, err = transitionStartupTicks(pid)
	if err != nil {
		return row, err
	}
	proc := fmt.Sprintf("/proc/%d/", pid)
	row.Image, err = os.Readlink(proc + "exe")
	if err != nil || row.Image != selected.Image {
		return row, fmt.Errorf("selected startup executable not yet running")
	}
	image, err := os.Open(proc + "exe")
	if err != nil {
		return row, err
	}
	defer image.Close()
	before, err := image.Stat()
	if err != nil {
		return row, err
	}
	hash := sha256.New()
	if _, err = io.Copy(hash, image); err != nil {
		return row, err
	}
	row.ImageSHA256 = hex.EncodeToString(hash.Sum(nil))
	if row.ImageSHA256 != selected.SHA256 {
		return row, fmt.Errorf("startup executable bytes differ")
	}
	row.ImageInode = before.Sys().(*syscall.Stat_t).Ino
	maps, err := os.ReadFile(proc + "maps")
	if err != nil {
		return row, err
	}
	row.Maps = string(maps)
	for _, line := range strings.Split(row.Maps, "\n") {
		fields := strings.Fields(line)
		if len(fields) != 6 || fields[4] != strconv.FormatUint(row.ImageInode, 10) || fields[5] != row.Image || !strings.Contains(fields[1], "x") {
			continue
		}
		first, last, ok := strings.Cut(fields[0], "-")
		start, startErr := strconv.ParseUint(first, 16, 64)
		end, endErr := strconv.ParseUint(last, 16, 64)
		if !ok || startErr != nil || endErr != nil || start >= end {
			return row, fmt.Errorf("invalid startup mapping range")
		}
		// map_files names omit address padding used by /proc/maps.
		target, err := os.Readlink(proc + fmt.Sprintf("map_files/%x-%x", start, end))
		if err != nil || target != row.Image {
			return row, fmt.Errorf("startup kernel map_files target %q differs: %v", target, err)
		}
		row.MappedImage, row.MapFilesPath = line, target
		break
	}
	if row.MappedImage == "" {
		return row, fmt.Errorf("startup executable mapping missing")
	}
	data, err := os.ReadFile(proc + "environ")
	if err != nil {
		return row, err
	}
	row.Environment = map[string]string{}
	for _, item := range strings.Split(strings.TrimRight(string(data), "\x00"), "\x00") {
		key, value, ok := strings.Cut(item, "=")
		if _, duplicate := row.Environment[key]; !ok || duplicate {
			return row, fmt.Errorf("invalid startup environment")
		}
		row.Environment[key] = value
	}
	if !reflect.DeepEqual(row.Environment, selected.Environment) {
		return row, fmt.Errorf("startup environment differs")
	}
	data, err = os.ReadFile(proc + "cmdline")
	if err != nil {
		return row, err
	}
	row.Argv = strings.Split(strings.TrimRight(string(data), "\x00"), "\x00")
	if len(row.Argv) == 0 || row.Argv[0] != selected.Image {
		return row, fmt.Errorf("startup argv executable differs")
	}
	after, err := os.Stat(proc + "exe")
	if err != nil || !os.SameFile(before, after) {
		return row, fmt.Errorf("startup executable changed during observation")
	}
	oldStat, newStat := *before.Sys().(*syscall.Stat_t), *after.Sys().(*syscall.Stat_t)
	oldStat.Atim, newStat.Atim = syscall.Timespec{}, syscall.Timespec{}
	if oldStat != newStat {
		return row, fmt.Errorf("startup executable metadata changed during observation")
	}
	ticks, err := transitionStartupTicks(pid)
	parent, state, statusErr := transitionProcessStatus(pid)
	if err != nil || ticks != row.StartTicks || statusErr != nil || parent != owner || state == "Z" || state == "X" {
		return row, fmt.Errorf("startup process identity changed during observation")
	}
	row.CompletedAt = time.Now()
	return row, nil
}

func TestSemanticPartialStartupObservation(t *testing.T) {
	if os.Getenv("CAIRN_PARTIAL_STARTUP_HELPER") == "1" {
		fmt.Println("ready")
		_, _ = io.Copy(io.Discard, os.Stdin)
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	image, err := filepath.EvalSymlinks(os.Args[0])
	if err != nil {
		t.Fatal(err)
	}
	bytes, err := os.ReadFile(image)
	if err != nil {
		t.Fatal(err)
	}
	want := transitionStartupSelection{Image: image, SHA256: transitionHash(string(bytes)), Environment: map[string]string{"CAIRN_PARTIAL_STARTUP_HELPER": "1"}}
	cmd := exec.CommandContext(ctx, image, "-test.run=^TestSemanticPartialStartupObservation$")
	cmd.Env = []string{"CAIRN_PARTIAL_STARTUP_HELPER=1"}
	input, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	output, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = input.Close(); _ = cmd.Wait() })
	if line, err := bufio.NewReader(output).ReadString('\n'); err != nil || line != "ready\n" {
		t.Fatalf("owned startup fixture not ready: %q %v", line, err)
	}
	pid := cmd.Process.Pid
	row, err := transitionObserveStartup(pid, os.Getpid(), want)
	if err != nil || row.PID != pid || row.Image != image || row.MappedImage == "" || row.CompletedAt.Before(row.StartedAt) {
		t.Fatalf("actual owned observation pid=%d image=%s mapping=%q: %v", row.PID, row.Image, row.MappedImage, err)
	}
	wrongHash := want
	wrongHash.SHA256 = strings.Repeat("0", 64)
	wrongEnv := want
	wrongEnv.Environment = map[string]string{"CAIRN_PARTIAL_STARTUP_HELPER": "wrong"}
	for _, bad := range []struct {
		pid, owner int
		selection  transitionStartupSelection
	}{{0, os.Getpid(), want}, {1, os.Getpid(), want}, {os.Getpid(), os.Getpid(), want}, {pid, os.Getpid() + 1, want}, {pid, os.Getpid(), wrongHash}, {pid, os.Getpid(), wrongEnv}} {
		if _, err := transitionObserveStartup(bad.pid, bad.owner, bad.selection); err == nil {
			t.Fatalf("invalid startup evidence accepted: %+v", bad)
		}
	}
}
