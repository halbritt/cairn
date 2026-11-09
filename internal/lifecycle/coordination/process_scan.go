package coordination

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

type MarkerProcess struct {
	PID   int    `json:"pid"`
	Start uint64 `json:"start"`
	Boot  string `json:"boot"`
	PGID  int    `json:"pgid"`
	SID   int    `json:"sid"`
	Cmd   string `json:"cmd"`
}

type MarkerScanResult struct {
	Clear     bool            `json:"clear"`
	Coverage  string          `json:"coverage"`
	Processes []MarkerProcess `json:"processes"`
	Unknown   []int           `json:"unknown"`
}

type ToolCallCapture struct {
	AttemptID string `json:"attempt_id"`
	Since     int64  `json:"since"`
}

func StartTicksAt(root string, pid int) (int64, error) {
	stat, err := readProcessStatAt(root, pid)
	if err != nil {
		return 0, err
	}
	if stat.start > math.MaxInt64 {
		return 0, fmt.Errorf("process %d start ticks exceed int64", pid)
	}
	return int64(stat.start), nil
}

// scanFailure preserves the distinction between an exited process, an
// unreadable possible descendant, and a scan that failed for another reason.
func scanFailure(root string, pid int, since int64, err error) (bool, error) {
	if processGone(err) {
		return false, nil
	}
	if !os.IsPermission(err) {
		return false, err
	}
	stat, statErr := readProcessStatAt(root, pid)
	if processGone(statErr) {
		return false, nil
	}
	if statErr != nil {
		var invalid *invalidProcessStat
		if os.IsPermission(statErr) || errors.As(statErr, &invalid) {
			return true, nil
		}
		return false, statErr
	}
	return stat.state != "Z" && (since < 0 || stat.start >= uint64(since)), nil
}

func asciiDigits(name string) bool {
	if name == "" {
		return false
	}
	for i := range name {
		if name[i] < '0' || name[i] > '9' {
			return false
		}
	}
	return true
}

func ScanMarkers(name, value string, since int64) (MarkerScanResult, error) {
	return ScanMarkersAt("/proc", uint32(os.Getuid()), name, value, since)
}

func ScanMarkersAt(root string, uid uint32, name, value string, since int64) (MarkerScanResult, error) {
	if name == "" || strings.Contains(name, "=") || value == "" {
		return MarkerScanResult{}, errors.New("marker name and value must be nonempty and name must not contain =")
	}
	root = procRoot(root)
	boot, err := bootIDAt(root)
	if err != nil {
		return MarkerScanResult{}, err
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return MarkerScanResult{}, err
	}
	result := MarkerScanResult{Coverage: "complete", Processes: []MarkerProcess{}, Unknown: []int{}}
	needle := []byte(name + "=" + value)
	failed := func(pid int, err error) error {
		unknown, failure := scanFailure(root, pid, since, err)
		if unknown {
			result.Unknown = append(result.Unknown, pid)
		}
		return failure
	}
	for _, entry := range entries {
		if !asciiDigits(entry.Name()) {
			continue
		}
		pid, err := strconv.Atoi(entry.Name())
		if err != nil {
			return MarkerScanResult{}, err
		}
		info, err := os.Lstat(filepath.Join(root, entry.Name()))
		if err != nil {
			if err := failed(pid, err); err != nil {
				return MarkerScanResult{}, err
			}
			continue
		}
		if info.Sys().(*syscall.Stat_t).Uid != uid {
			continue
		}
		directory := filepath.Join(root, strconv.Itoa(pid))
		environ, err := os.ReadFile(filepath.Join(directory, "environ"))
		if err != nil {
			if err := failed(pid, err); err != nil {
				return MarkerScanResult{}, err
			}
			continue
		}
		matched := false
		for _, item := range bytes.Split(environ, []byte{0}) {
			if bytes.Equal(item, needle) {
				matched = true
				break
			}
		}
		if !matched {
			continue
		}
		stat, err := readProcessStatAt(root, pid)
		if err != nil {
			if err := failed(pid, err); err != nil {
				return MarkerScanResult{}, err
			}
			continue
		}
		if stat.state == "Z" {
			continue
		}
		cmd, err := os.ReadFile(filepath.Join(directory, "cmdline"))
		if err != nil {
			if err := failed(pid, err); err != nil {
				return MarkerScanResult{}, err
			}
			continue
		}
		cmd = bytes.Trim(bytes.ReplaceAll(cmd, []byte{0}, []byte{' '}), " \t\r\n\v\f")
		text := []rune(strings.ToValidUTF8(string(cmd), "\ufffd"))
		if len(text) > 200 {
			text = text[:200]
		}
		result.Processes = append(result.Processes, MarkerProcess{PID: pid, Start: stat.start, Boot: boot, PGID: stat.pgid, SID: stat.sid, Cmd: string(text)})
	}
	if len(result.Unknown) > 0 {
		result.Coverage = "unknown"
	}
	result.Clear = result.Coverage == "complete" && len(result.Processes) == 0
	return result, nil
}

func OpenCodeCancelMinSince(processStart int64, opencodeTurnSince *int64, toolCalls map[string]ToolCallCapture, attemptID string) (int64, error) {
	since := processStart
	if opencodeTurnSince != nil {
		since = *opencodeTurnSince
	}
	for _, item := range toolCalls {
		if item.AttemptID != attemptID {
			return 0, &CoordinationError{Code: "INBOX_ATTEMPT_MISMATCH", Message: "tool capture belongs to another attempt"}
		}
		since = min(since, item.Since)
	}
	return since, nil
}

func OpenCodeCancelReleaseReason(turnExclusive bool) string {
	if turnExclusive {
		return "cancel_confirmed"
	}
	return "exclusivity_revoked"
}
