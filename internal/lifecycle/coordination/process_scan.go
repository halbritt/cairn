package coordination

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	pfKthread       = 0x00200000
	maxMarkerPasses = 5
)

var execveRetryDelays = []time.Duration{
	0,
	1 * time.Millisecond,
	2 * time.Millisecond,
	4 * time.Millisecond,
	8 * time.Millisecond,
	10 * time.Millisecond,
}

// Tests replace the pause between mid-execve rereads to change the fixture at
// an exact retry, rather than racing a goroutine against wall-clock delays.
var execveRetrySleep = time.Sleep

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

func checkZeroEnvironAt(root string, pid int, since int64) (midExec, gone, unknown bool, err error) {
	raw, readErr := os.ReadFile(filepath.Join(procRoot(root), strconv.Itoa(pid), "stat"))
	if readErr != nil {
		if processGone(readErr) {
			return false, true, false, nil
		}
		unk, fail := scanFailure(root, pid, since, readErr)
		return false, false, unk, fail
	}
	end := bytes.LastIndexByte(raw, ')')
	if end < 0 {
		return false, false, true, nil
	}
	fields := strings.Fields(string(raw[end+1:]))
	if len(fields) < 20 {
		return false, false, true, nil
	}
	start, parseErr := strconv.ParseUint(fields[19], 10, 64)
	if parseErr != nil {
		return false, false, true, nil
	}
	if fields[0] == "Z" || (since >= 0 && start < uint64(since)) || len(fields) < 49 {
		return false, false, false, nil
	}
	flags, err1 := strconv.ParseUint(fields[6], 10, 64)
	envStart, err2 := strconv.ParseUint(fields[47], 10, 64)
	envEnd, err3 := strconv.ParseUint(fields[48], 10, 64)
	if err1 != nil || err2 != nil || err3 != nil {
		return false, false, true, nil
	}
	if flags&pfKthread != 0 || (envStart != 0 && envStart == envEnd) {
		return false, false, false, nil
	}
	return true, false, false, nil
}

func readProcessEnvironAt(root, directory string, pid int, since int64) (environ []byte, gone, unknown bool, err error) {
	path := filepath.Join(directory, "environ")
	environ, err = os.ReadFile(path)
	if err != nil {
		if processGone(err) {
			return nil, true, false, nil
		}
		unk, fail := scanFailure(root, pid, since, err)
		return nil, false, unk, fail
	}
	if len(environ) > 0 {
		return environ, false, false, nil
	}
	midExec, gone, unknown, err := checkZeroEnvironAt(root, pid, since)
	if err != nil || gone || unknown || !midExec {
		return nil, gone, unknown, err
	}
	for _, delay := range execveRetryDelays {
		if delay > 0 {
			execveRetrySleep(delay)
		}
		environ, err = os.ReadFile(path)
		if err != nil {
			if processGone(err) {
				return nil, true, false, nil
			}
			unk, fail := scanFailure(root, pid, since, err)
			return nil, false, unk, fail
		}
		if len(environ) > 0 {
			return environ, false, false, nil
		}
		midExec, gone, unknown, err = checkZeroEnvironAt(root, pid, since)
		if err != nil || gone || unknown || !midExec {
			return nil, gone, unknown, err
		}
	}
	return nil, false, true, nil
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
	result := MarkerScanResult{Coverage: "complete", Processes: []MarkerProcess{}, Unknown: []int{}}
	needle := []byte(name + "=" + value)
	seenClear := map[int]bool{}
	seenMatched := map[int]bool{}
	seenUnknown := map[int]bool{}
	markUnknown := func(pid int) {
		if !seenUnknown[pid] {
			seenUnknown[pid] = true
			result.Unknown = append(result.Unknown, pid)
		}
	}
	failed := func(pid int, err error) error {
		unknown, failure := scanFailure(root, pid, since, err)
		if unknown {
			markUnknown(pid)
		}
		return failure
	}
	for pass := 0; pass < maxMarkerPasses; pass++ {
		entries, err := os.ReadDir(root)
		if err != nil {
			return MarkerScanResult{}, err
		}
		newUnseen := false
		for _, entry := range entries {
			if !asciiDigits(entry.Name()) {
				continue
			}
			pid, err := strconv.Atoi(entry.Name())
			if err != nil {
				return MarkerScanResult{}, err
			}
			if seenClear[pid] || seenMatched[pid] || seenUnknown[pid] {
				continue
			}
			info, err := os.Lstat(filepath.Join(root, entry.Name()))
			if err != nil {
				if processGone(err) {
					continue
				}
				if err := failed(pid, err); err != nil {
					return MarkerScanResult{}, err
				}
				continue
			}
			if info.Sys().(*syscall.Stat_t).Uid != uid {
				seenClear[pid] = true
				continue
			}
			if pass > 0 {
				newUnseen = true
			}
			directory := filepath.Join(root, strconv.Itoa(pid))
			environ, gone, unk, err := readProcessEnvironAt(root, directory, pid, since)
			if err != nil {
				return MarkerScanResult{}, err
			}
			if unk {
				markUnknown(pid)
				continue
			}
			if gone {
				if pass == maxMarkerPasses-1 {
					markUnknown(pid)
				} else {
					newUnseen = true
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
				seenClear[pid] = true
				continue
			}
			stat, err := readProcessStatAt(root, pid)
			if err != nil {
				if processGone(err) {
					if pass == maxMarkerPasses-1 {
						markUnknown(pid)
					} else {
						newUnseen = true
					}
					continue
				}
				if err := failed(pid, err); err != nil {
					return MarkerScanResult{}, err
				}
				continue
			}
			if stat.state == "Z" {
				seenClear[pid] = true
				continue
			}
			cmd, err := os.ReadFile(filepath.Join(directory, "cmdline"))
			if err != nil {
				if processGone(err) {
					if pass == maxMarkerPasses-1 {
						markUnknown(pid)
					} else {
						newUnseen = true
					}
					continue
				}
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
			seenMatched[pid] = true
			result.Processes = append(result.Processes, MarkerProcess{PID: pid, Start: stat.start, Boot: boot, PGID: stat.pgid, SID: stat.sid, Cmd: string(text)})
		}
		if pass > 0 && !newUnseen {
			break
		}
	}
	slices.Sort(result.Unknown)
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
