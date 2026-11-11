// Native acceptance entry. Build in the campaign wrapper; execute in the
// dedicated minimal root prepared by check_semantic_offline_package.py.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const selectionPath = "/fixture/selection.json"

type launcher struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

type selection struct {
	Files     map[string]string          `json:"files"`
	Launchers map[string]launcher        `json:"launchers"`
	Request   json.RawMessage            `json:"request"`
	Expected  map[string]json.RawMessage `json:"expected"`
	HostPIDNS string                     `json:"host_pid_namespace"`
	HostNetNS string                     `json:"host_net_namespace"`
}

type boundary struct {
	Files  map[string]string `json:"files"`
	Probes map[string]string `json:"absence_probes"`
	PIDs   []int             `json:"visible_pids"`
	PIDNS  string            `json:"pid_namespace"`
	NetNS  string            `json:"net_namespace"`
	Passed bool              `json:"passed"`
}

type result struct {
	Schema     string   `json:"schema"`
	Boundary   boundary `json:"boundary"`
	Mode       string   `json:"mode"`
	Route      string   `json:"route"`
	Started    bool     `json:"command_started"`
	Exit       int      `json:"command_exit"`
	Stdout     []byte   `json:"stdout"`
	Stderr     []byte   `json:"stderr"`
	Comparison bool     `json:"exact_retained_reply"`
	Error      string   `json:"error,omitempty"`
}

func digestFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func forbiddenPath(path string) bool {
	name := strings.ToLower(filepath.Base(path))
	return strings.HasPrefix(name, "python") || strings.HasPrefix(name, "libpython") ||
		name == "go.mod" || name == ".git"
}

func inventory(root string) (map[string]string, error) {
	files := map[string]string{}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		name := "/" + filepath.ToSlash(relative)
		if name == "/proc" || name == "/dev" {
			return filepath.SkipDir
		}
		if forbiddenPath(path) {
			return fmt.Errorf("Python or development checkout exposed: %s", name)
		}
		if entry.IsDir() {
			return nil
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("unexpected runtime file type: %s (%s)", name, entry.Type())
		}
		sha, err := digestFile(path)
		if err != nil {
			return err
		}
		files[name] = sha
		return nil
	})
	return files, err
}

func sameFiles(actual, expected map[string]string) error {
	for path, sha := range actual {
		if path == selectionPath {
			continue // The descriptor cannot contain its own whole-file hash.
		}
		if expected[path] != sha {
			return fmt.Errorf("unexpected or changed runtime file: %s", path)
		}
	}
	for path, sha := range expected {
		if actual[path] != sha {
			return fmt.Errorf("missing or changed runtime file: %s", path)
		}
	}
	return nil
}

func noHostProcesses(observed *boundary, selected selection) error {
	var err error
	observed.PIDNS, err = os.Readlink("/proc/self/ns/pid")
	if err != nil {
		return err
	}
	observed.NetNS, err = os.Readlink("/proc/self/ns/net")
	if err != nil {
		return err
	}
	if observed.PIDNS == selected.HostPIDNS || observed.NetNS == selected.HostNetNS {
		return errors.New("host PID or network namespace exposed")
	}
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return err
	}
	root, err := os.Stat("/")
	if err != nil {
		return err
	}
	for _, entry := range entries {
		pid, err := strconv.Atoi(entry.Name())
		if err != nil {
			continue
		}
		observed.PIDs = append(observed.PIDs, pid)
		if pid != 1 && pid != os.Getpid() {
			return fmt.Errorf("unexpected initial process: %d", pid)
		}
		prefix := "/proc/" + entry.Name()
		procRoot, err := os.Stat(prefix + "/root")
		if err != nil || !os.SameFile(root, procRoot) {
			return fmt.Errorf("foreign process root: %d: %v", pid, err)
		}
		image, err := os.Readlink(prefix + "/exe")
		if err != nil || forbiddenPath(image) {
			return fmt.Errorf("unexpected process image: %d: %s: %v", pid, image, err)
		}
		fds, err := os.ReadDir(prefix + "/fd")
		if err != nil {
			return err
		}
		for _, fd := range fds {
			info, err := os.Stat(prefix + "/fd/" + fd.Name())
			if os.IsNotExist(err) {
				continue // The /proc directory reader has already closed its FD.
			}
			if err != nil {
				return err
			}
			if info.IsDir() {
				return fmt.Errorf("directory descriptor exposed by process %d", pid)
			}
		}
	}
	sort.Ints(observed.PIDs)
	return nil
}

func noNetwork() error {
	interfaces, err := net.Interfaces()
	if err != nil {
		return err
	}
	if len(interfaces) != 1 || interfaces[0].Name != "lo" {
		return fmt.Errorf("unexpected network interfaces: %v", interfaces)
	}
	for _, path := range []string{"/proc/net/route", "/proc/net/unix"} {
		text, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if len(bytes.Split(bytes.TrimSpace(text), []byte("\n"))) != 1 {
			return fmt.Errorf("routes or sockets visible: %s", path)
		}
	}
	text, err := os.ReadFile("/proc/net/ipv6_route")
	if err != nil {
		return err
	}
	for _, line := range strings.Split(strings.TrimSpace(string(text)), "\n") {
		fields := strings.Fields(line)
		if len(fields) > 0 && fields[len(fields)-1] != "lo" {
			return errors.New("external IPv6 route visible")
		}
	}
	return nil
}

func checkBoundary(selected selection) (boundary, error) {
	observed := boundary{Probes: map[string]string{}}
	var err error
	observed.Files, err = inventory("/")
	if err != nil {
		return observed, err
	}
	if err = sameFiles(observed.Files, selected.Files); err != nil {
		return observed, err
	}
	for _, path := range []string{"/usr/bin/python3", "/usr/local/bin/python3", "/lib/libpython3.so",
		"/home/halbritt/git/cairn", "/work/go.mod", "/work/.git", "/root", "/home"} {
		_, err := os.Stat(path)
		if !os.IsNotExist(err) {
			return observed, fmt.Errorf("absence probe did not return ENOENT: %s: %v", path, err)
		}
		observed.Probes[path] = "ENOENT"
	}
	if err = noHostProcesses(&observed, selected); err != nil {
		return observed, err
	}
	if err = noNetwork(); err != nil {
		return observed, err
	}
	entries, err := os.ReadDir("/nonexistent")
	if err != nil || len(entries) != 0 {
		return observed, fmt.Errorf("worker HOME/cache not initially empty: %v", err)
	}
	observed.Passed = true
	return observed, nil
}

func decoded(raw []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var document any
	if err := decoder.Decode(&document); err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, errors.New("expected exactly one JSON reply")
	}
	return document, nil
}

func workerCommand(selected selection, mode, route string) (*exec.Cmd, error) {
	launch, ok := selected.Launchers[mode]
	if !ok || (route != "direct" && route != "go") {
		return nil, errors.New("expected rank/stream/embed and direct/go")
	}
	sha, err := digestFile(launch.Path)
	if err != nil || sha != launch.SHA256 {
		return nil, fmt.Errorf("selected launcher binding differs: %s: %v", mode, err)
	}
	command := exec.Command(launch.Path)
	input := selected.Request
	if route == "go" {
		command = exec.Command("/fixture/go-driver", "--mode", mode, "--launcher", launch.Path)
		input, err = json.Marshal(map[string]json.RawMessage{"request": selected.Request})
	} else if mode == "stream" {
		input, err = json.Marshal(struct {
			ID      string          `json:"id"`
			Request json.RawMessage `json:"request"`
		}{"package", selected.Request})
	} else if mode == "embed" {
		input = []byte(`{"id":"package","operation":"identity","text":""}`)
	}
	if err != nil {
		return nil, err
	}
	command.Stdin = bytes.NewReader(append(input, '\n'))
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return command, nil
}

func execute(command *exec.Cmd, report *result) error {
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	if err := command.Start(); err != nil {
		return err
	}
	report.Started = true
	if command.Process.Pid <= 1 {
		return errors.New("refusing process-group control for PID <= 1")
	}
	finished := make(chan error, 1)
	go func() { finished <- command.Wait() }()
	var err error
	select {
	case err = <-finished:
	case <-time.After(120 * time.Second):
		if err := syscall.Kill(-command.Process.Pid, syscall.SIGKILL); err != nil {
			return err
		}
		<-finished
		return errors.New("native fixture harness timed out")
	}
	report.Exit = command.ProcessState.ExitCode()
	report.Stdout, report.Stderr = stdout.Bytes(), stderr.Bytes()
	return err
}

func run(report *result, selected selection, mode, route string, negative, control bool) error {
	var err error
	report.Boundary, err = checkBoundary(selected)
	if err != nil {
		return err
	}
	command, err := workerCommand(selected, mode, route)
	if err != nil {
		return err
	}
	if control {
		command = exec.Command("/fixture/go-driver", "--observer-control")
		command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	}
	err = execute(command, report)
	if negative {
		if report.Exit != 1 || len(report.Stdout) != 0 {
			return fmt.Errorf("selected-byte refusal expected exit 1 and empty stdout: exit %d: %v", report.Exit, err)
		}
		return nil
	}
	if err != nil || control {
		return err
	}
	actual, err := decoded(report.Stdout)
	if err != nil {
		return err
	}
	expected, err := decoded(selected.Expected[route+"-"+mode])
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(actual, expected) {
		return errors.New("reply differs from exact retained package fixture output")
	}
	report.Comparison = true
	after, err := inventory("/")
	if err != nil {
		return err
	}
	return sameFiles(after, selected.Files)
}

func main() {
	mode := flag.String("mode", "", "rank, stream or embed")
	route := flag.String("route", "", "direct or go")
	negative := flag.Bool("negative", false, "expect selected-byte launcher refusal")
	control := flag.Bool("observer-control", false, "exercise observer in disposable runtime")
	flag.Parse()
	report := result{Schema: "cairn.semantic-native-fixture/1", Mode: *mode, Route: *route, Exit: -1}
	raw, err := os.ReadFile(selectionPath)
	var selected selection
	if err == nil {
		err = json.Unmarshal(raw, &selected)
	}
	if err == nil {
		err = run(&report, selected, *mode, *route, *negative, *control)
	}
	if err != nil {
		report.Error = err.Error()
	}
	if encodeErr := json.NewEncoder(os.Stdout).Encode(report); encodeErr != nil {
		fmt.Fprintln(os.Stderr, encodeErr)
		os.Exit(1)
	}
	if err != nil {
		os.Exit(1)
	}
}
