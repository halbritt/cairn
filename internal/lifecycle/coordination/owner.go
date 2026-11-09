package coordination

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"unicode"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/procpeer"
)

// ProcView keeps ancillary ancestry outside the shared PID/start/boot identity.
// The assigned procpeer baseline has no ProcessRefOfAt, Parent or PGID fields.
type ProcView struct {
	Ref          procpeer.ProcessRef
	Parent, PGID int
	State        string
	Comm, Exe    string
	Argv         []string
}

type OwnerRecord struct {
	PID    int    `json:"pid"`
	Start  uint64 `json:"start"`
	State  string `json:"state"`
	Parent int    `json:"parent"`
	Boot   string `json:"boot"`
}

func (v ProcView) OwnerRecord() OwnerRecord {
	return OwnerRecord{PID: v.Ref.PID, Start: v.Ref.Start, State: v.State, Parent: v.Parent, Boot: v.Ref.Boot}
}

type OwnerConfig struct {
	Harness           string
	ProcessNames      []string
	HasProcessExeDirs bool
	ProcessExeDirs    []string
	HomeDir           string
}

type processStat struct {
	state             string
	parent, pgid, sid int
	start             uint64
}

type invalidProcessStat struct {
	pid   int
	cause error
}

type invalidProcessText struct{ path string }

func (e *invalidProcessText) Error() string { return e.path + " is not UTF-8" }

func (e *invalidProcessStat) Error() string {
	return fmt.Sprintf("invalid /proc/%d/stat: %v", e.pid, e.cause)
}
func (e *invalidProcessStat) Unwrap() error { return e.cause }

func procRoot(root string) string {
	if root == "" {
		return "/proc"
	}
	return root
}

func readProcessStatAt(root string, pid int) (processStat, error) {
	raw, err := os.ReadFile(filepath.Join(procRoot(root), strconv.Itoa(pid), "stat"))
	if err != nil {
		return processStat{}, err
	}
	end := bytes.LastIndexByte(raw, ')')
	if end < 0 {
		return processStat{}, &invalidProcessStat{pid, errors.New("missing command terminator")}
	}
	fields := strings.Fields(string(raw[end+1:]))
	if len(fields) < 20 {
		return processStat{}, &invalidProcessStat{pid, errors.New("short process stat")}
	}
	parent, err := strconv.Atoi(fields[1])
	if err != nil {
		return processStat{}, &invalidProcessStat{pid, err}
	}
	pgid, err := strconv.Atoi(fields[2])
	if err != nil {
		return processStat{}, &invalidProcessStat{pid, err}
	}
	sid, err := strconv.Atoi(fields[3])
	if err != nil {
		return processStat{}, &invalidProcessStat{pid, err}
	}
	start, err := strconv.ParseUint(fields[19], 10, 64)
	if err != nil {
		return processStat{}, &invalidProcessStat{pid, err}
	}
	return processStat{state: fields[0], parent: parent, pgid: pgid, sid: sid, start: start}, nil
}

func bootIDAt(root string) (string, error) {
	root = procRoot(root)
	raw, err := os.ReadFile(filepath.Join(root, "sys/kernel/random/boot_id"))
	if errors.Is(err, os.ErrNotExist) && filepath.Clean(root) != "/proc" {
		raw, err = os.ReadFile("/proc/sys/kernel/random/boot_id")
	}
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(raw)), nil
}

func ReadProcViewAt(root string, pid int) (ProcView, error) {
	root = procRoot(root)
	stat, err := readProcessStatAt(root, pid)
	if err != nil {
		return ProcView{}, err
	}
	boot, err := bootIDAt(root)
	if err != nil {
		return ProcView{}, err
	}
	directory := filepath.Join(root, strconv.Itoa(pid))
	comm, err := os.ReadFile(filepath.Join(directory, "comm"))
	if err != nil {
		return ProcView{}, err
	}
	if !utf8.Valid(comm) {
		return ProcView{}, &invalidProcessText{path: filepath.Join(directory, "comm")}
	}
	exe, _ := os.Readlink(filepath.Join(directory, "exe")) // Python treats an unreadable executable as absent.
	view := ProcView{Ref: procpeer.ProcessRef{PID: pid, Start: stat.start, Boot: boot}, Parent: stat.parent, PGID: stat.pgid, State: stat.state, Comm: pythonStrip(string(comm)), Exe: exe, Argv: []string{}}
	file, err := os.Open(filepath.Join(directory, "cmdline"))
	if err != nil {
		return view, nil
	} // Unreadable argv does not discard the other observations.
	raw, readErr := io.ReadAll(io.LimitReader(file, 65536))
	closeErr := file.Close()
	if readErr != nil || closeErr != nil {
		return view, nil
	}
	for _, part := range bytes.Split(raw, []byte{0}) {
		if len(part) != 0 {
			view.Argv = append(view.Argv, strings.ToValidUTF8(string(part), "\ufffd"))
		}
	}
	return view, nil
}

func ClaudeSupervisorRole(view ProcView) string {
	if len(view.Argv) == 0 {
		return ""
	}
	if len(view.Argv) > 1 && view.Argv[1] == "daemon" {
		return "daemon"
	}
	words := strings.Split(view.Argv[0], " ")
	for _, role := range []string{"bg-pty-host", "bg-spare"} {
		if words[len(words)-1] == role || len(view.Argv) > 1 && (view.Argv[1] == role || view.Argv[1] == "--"+role) {
			return role
		}
	}
	return ""
}

func ClaudeExeDirs(harness string, hasProcessExeDirs bool, processExeDirs []string, homeDir string) []string {
	if hasProcessExeDirs {
		result := make([]string, len(processExeDirs))
		for i, path := range processExeDirs {
			result[i] = purePosixPath(path)
		}
		return result
	}
	if harness == "claude" && homeDir != "" {
		home := purePosixPath(homeDir)
		if home == "/" || home == "//" {
			return []string{home + ".local/share/claude/versions"}
		}
		return []string{purePosixPath(home + "/.local/share/claude/versions")}
	}
	return nil
}

func pythonStrip(value string) string {
	return strings.TrimFunc(value, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
}

// PurePosixPath normalizes separators and dot segments without resolving '..'.
func purePosixPath(value string) string {
	root := ""
	if strings.HasPrefix(value, "//") && !strings.HasPrefix(value, "///") {
		root = "//"
	} else if strings.HasPrefix(value, "/") {
		root = "/"
	}
	parts := strings.Split(value, "/")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		if part != "" && part != "." {
			result = append(result, part)
		}
	}
	if root == "" && len(result) == 0 {
		return "."
	}
	return root + strings.Join(result, "/")
}

func purePosixParent(value string) string {
	value = purePosixPath(value)
	if value == "/" || value == "//" || value == "." {
		return value
	}
	last := strings.LastIndexByte(value, '/')
	if last < 0 {
		return "."
	}
	if last == 0 {
		return "/"
	}
	if last == 1 && strings.HasPrefix(value, "//") {
		return "//"
	}
	return value[:last]
}

func OwnerProcess(cfg OwnerConfig, declaredHostPID any) (procpeer.ProcessRef, error) {
	view, err := OwnerProcessView(cfg, declaredHostPID)
	return view.Ref, err
}

func OwnerProcessAt(root string, startPID int, cfg OwnerConfig, declaredHostPID any) (procpeer.ProcessRef, error) {
	view, err := OwnerProcessViewAt(root, startPID, cfg, declaredHostPID)
	return view.Ref, err
}

func OwnerProcessView(cfg OwnerConfig, declaredHostPID any) (ProcView, error) {
	return OwnerProcessViewAt("/proc", os.Getppid(), cfg, declaredHostPID)
}

func OwnerProcessViewAt(root string, startPID int, cfg OwnerConfig, declaredHostPID any) (ProcView, error) {
	var declared int64
	if declaredHostPID != nil {
		value, ok := exactInteger(declaredHostPID)
		if !ok || value < 2 {
			return ProcView{}, &CoordinationError{Code: "INVALID_HOST", Message: "invalid native process ID"}
		}
		declared = value
	}
	directories := ClaudeExeDirs(cfg.Harness, cfg.HasProcessExeDirs, cfg.ProcessExeDirs, cfg.HomeDir)
	pid := startPID
	for depth := 0; depth < 128 && pid >= 2; depth++ {
		view, err := ReadProcViewAt(root, pid)
		if err != nil {
			if processGone(err) {
				return ProcView{}, &CoordinationError{Code: "INVALID_HOST", Message: "native process exited during owner lookup", Cause: err}
			}
			return ProcView{}, err
		}
		if declaredHostPID != nil {
			if int64(pid) == declared {
				return view, nil
			}
		} else if ClaudeSupervisorRole(view) == "" {
			for _, name := range cfg.ProcessNames {
				if view.Comm == name || view.Exe != "" && filepath.Base(view.Exe) == name {
					return view, nil
				}
			}
			if view.Exe != "" {
				for _, directory := range directories {
					if purePosixParent(view.Exe) == directory {
						return view, nil
					}
				}
			}
		}
		pid = view.Parent
	}
	return ProcView{}, &CoordinationError{Code: "INVALID_HOST", Message: "native process is not an observed ancestor"}
}

func processGone(err error) bool {
	return errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ESRCH)
}

func ClaudeDaemonManagedAt(root string, process procpeer.ProcessRef) bool {
	view, err := ReadProcViewAt(root, process.PID)
	if err != nil || view.Ref != process {
		return false
	}
	parent, err := ReadProcViewAt(root, view.Parent)
	return err == nil && ClaudeSupervisorRole(parent) == "bg-pty-host"
}

func DaemonWorkerOfAt(root string, worker, daemon procpeer.ProcessRef) bool {
	view, err := ReadProcViewAt(root, worker.PID)
	if err != nil || view.Ref != worker {
		return false
	}
	host, err := ReadProcViewAt(root, view.Parent)
	if err != nil || ClaudeSupervisorRole(host) != "bg-pty-host" {
		return false
	}
	supervisor, err := ReadProcViewAt(root, host.Parent)
	return err == nil && ClaudeSupervisorRole(supervisor) == "daemon" && supervisor.Ref == daemon
}

func CallerDescendsFrom(target procpeer.ProcessRef) bool {
	return CallerDescendsFromAt("/proc", os.Getpid(), target)
}

func CallerDescendsFromAt(root string, startPID int, target procpeer.ProcessRef) bool {
	pid := startPID
	for depth := 0; depth < 64 && pid >= 2; depth++ {
		view, err := ReadProcViewAt(root, pid)
		if err != nil {
			return false
		}
		if view.Ref == target {
			return true
		}
		pid = view.Parent
	}
	return false
}
